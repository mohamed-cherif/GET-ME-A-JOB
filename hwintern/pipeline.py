from __future__ import annotations

import logging
import random
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .config import Config
from .filters import Classifier
from .http import Http
from .judge import LLMJudge
from .models import Job
from .notify import Notifier, build_notifiers, dispatch, sort_for_notification
from .sources import build_source
from .sources.base import Source
from .sources.discovery import board_from_url
from .sources.enrich import fetch_description
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class CycleReport:
    started: datetime
    sources_total: int = 0
    sources_failed: int = 0
    jobs_fetched: int = 0
    llm_judged: int = 0
    llm_rejected: int = 0
    jobs_new: int = 0
    jobs_matched: int = 0
    details_fetched: int = 0
    boards_discovered: int = 0
    notified: list[Job] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    duration_s: float = 0.0

    def summary(self) -> str:
        return (f"sources {self.sources_total - self.sources_failed}/{self.sources_total} ok · "
                f"fetched {self.jobs_fetched} · new {self.jobs_new} · matched {self.jobs_matched} · "
                f"details {self.details_fetched} · llm judged {self.llm_judged} (rejected {self.llm_rejected}) · "
                f"discovered boards {self.boards_discovered} · "
                f"{self.duration_s:.1f}s")


class Pipeline:
    def __init__(self, cfg: Config, store: Optional[Store] = None, http: Optional[Http] = None,
                 notifiers: Optional[list[Notifier]] = None, dry_run: bool = False, judge: Optional[LLMJudge] = None):
        self.cfg = cfg
        self.http = http or Http(timeout=cfg.run.request_timeout)
        self.store = store or Store(cfg.db_path)
        self.classifier = Classifier(cfg.filters)
        self.judge = judge if judge is not None else LLMJudge(cfg.llm, self.store)
        self.notifiers = notifiers if notifiers is not None else build_notifiers(cfg.notifiers, self.http, self.store)
        self.dry_run = dry_run
        self._stop = False
        self.last_report: Optional[CycleReport] = None
        self.started_at = datetime.now(timezone.utc)
        self.cycles = 0

    # -- sources ------------------------------------------------------------
    def sources(self) -> list[Source]:
        out: list[Source] = []
        seen: set[tuple[str, str]] = set()
        for e in list(self.cfg.aggregators) + list(self.cfg.companies):
            if e.get("enabled") is False:
                continue
            src = build_source(self.http, e, self.cfg.run)
            if not src:
                continue
            k = (src.kind, src.ident)
            if k in seen:
                continue
            seen.add(k)
            out.append(src)
        for b in self.store.boards():
            k = (b["kind"], b["ident"])
            if k in seen:
                continue
            entry = {"kind": b["kind"], "id": b["ident"], "company": b.get("company") or b["ident"]}
            entry.update(b.get("params") or {})
            src = build_source(self.http, entry, self.cfg.run)
            if src:
                seen.add(k)
                out.append(src)
        srcs = [s for s in out if s]
        for s in srcs:
            s.store = self.store
        return srcs

    def _failures(self, src: Source) -> int:
        return int(self.store.get(f"fail:{src.kind}:{src.ident}") or 0)

    def _run_source(self, src: Source) -> tuple[Source, list[Job], Optional[str]]:
        key = f"fail:{src.kind}:{src.ident}"
        n = self._failures(src)
        limit = int(self.cfg.run.max_board_failures)
        if limit and n >= limit and (n - limit + 1) % 50 != 0:
            # dead board: skip it, but retry once every 50 cycles in case it came back
            self.store.set(key, str(n + 1))
            return src, [], None
        try:
            jobs = src.fetch()
            self.store.board_result(src.kind, src.ident, None)
            if n:
                self.store.set(key, "0")
            return src, jobs, None
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            self.store.board_result(src.kind, src.ident, msg)
            self.store.set(key, str(n + 1))
            if n + 1 == limit:
                log.warning("board %s failed %d times in a row; parking it (python -m hwintern boards shows it)", src.label, n + 1)
            return src, [], msg

    # -- one cycle ----------------------------------------------------------
    def run_once(self) -> CycleReport:
        t0 = time.time()
        rep = CycleReport(started=datetime.now(timezone.utc))
        first_run = self.store.is_first_run()
        real_channels = [n for n in self.notifiers if n.name not in ("stdout", "file")]
        if first_run and not self.dry_run and not real_channels:
            # Building the baseline now would mark every open posting as "seen" and the user would
            # never be told about it once a channel is configured. Refuse until one exists.
            log.error("no notification channel configured (Telegram/Discord/ntfy/...); refusing to build the "
                      "baseline on the first run. Set a channel or use --dry-run.")
            rep.errors["config"] = "no notification channel configured"
            rep.duration_s = time.time() - t0
            return rep
        sources = self.sources()
        rep.sources_total = len(sources)
        log.info("cycle start: %d sources (%s)", len(sources), "first run - building baseline" if first_run else "incremental")

        seen_keys = self.store.seen_keys()
        seen_urls = self.store.seen_urls()
        by_source: dict[str, Source] = {}
        new_jobs: list[Job] = []
        cycle_keys: set[str] = set()
        cycle_urls: set[str] = set()

        with ThreadPoolExecutor(max_workers=self.cfg.run.workers) as ex:
            futures = [ex.submit(self._run_source, s) for s in sources]
            for fut in as_completed(futures):
                src, jobs, err = fut.result()
                by_source[src.label] = src
                if err:
                    rep.sources_failed += 1
                    rep.errors[src.label] = err
                    log.warning("source %s failed: %s", src.label, err)
                    continue
                rep.jobs_fetched += len(jobs)
                for j in jobs:
                    if not j.url or not j.title:
                        continue
                    j.extra["_src_label"] = src.label
                    if j.key in seen_keys or j.key in cycle_keys:
                        continue
                    un = j.url_norm
                    if un and (un in seen_urls or un in cycle_urls):
                        continue
                    cycle_keys.add(j.key)
                    if un:
                        cycle_urls.add(un)
                    new_jobs.append(j)
        rep.jobs_new = len(new_jobs)

        # classify; fetch details where the title alone is ambiguous
        matched: list[Job] = []
        detail_budget = self.cfg.run.detail_fetch_limit
        detail_candidates: list[tuple[Job, Source]] = []
        decided: list[tuple[Job, bool, str]] = []
        for j in new_jobs:
            v = self.classifier.classify(j)
            src = by_source.get(j.extra.get("_src_label", ""))
            if v.needs_description and src is not None and src.supports_details and not j.has_full_description:
                detail_candidates.append((j, src))
                continue
            j.matched_categories, j.detected_terms, j.flags = v.categories, v.terms, v.flags
            j.score, j.tier = v.score, v.tier
            decided.append((j, v.accepted, v.reason))

        if detail_candidates:
            # only spend requests on postings that at least look like internships
            from .filters import INTERN_RE
            detail_candidates.sort(key=lambda t: 0 if INTERN_RE.search(t[0].title) else 1)
            todo = detail_candidates[:detail_budget]
            for j, _ in detail_candidates[detail_budget:]:
                decided.append((j, False, "detail-budget-exhausted"))

            def _detail(pair):
                job, src = pair
                try:
                    src.fetch_details(job)
                    return job, None
                except Exception as exc:  # noqa: BLE001
                    return job, f"{type(exc).__name__}: {exc}"

            with ThreadPoolExecutor(max_workers=min(8, self.cfg.run.workers)) as ex:
                for job, err in ex.map(_detail, todo):
                    rep.details_fetched += 1
                    if err:
                        log.debug("detail fetch failed for %s: %s", job.url, err)
                    v = self.classifier.classify(job)
                    job.matched_categories, job.detected_terms, job.flags = v.categories, v.terms, v.flags
                    job.score, job.tier = v.score, v.tier
                    decided.append((job, v.accepted, v.reason))

        # LLM judge: read the real posting for everything the keywords accepted
        decided = self._judge_all(decided, rep)

        # decide what to notify
        max_age = self.cfg.run.initial_max_age_days
        to_notify: list[Job] = []
        for j, ok, reason in decided:
            if ok:
                matched.append(j)
                if first_run and max_age is not None:
                    age = j.age_days()
                    if age is not None and age > max_age:
                        reason = "ok-but-stale-on-first-run"
                        self.store.record(j, True, reason, notified=False)
                        continue
                to_notify.append(j)
            else:
                self.store.record(j, False, reason, notified=False)
        rep.jobs_matched = len(matched)

        # auto-discovery of boards from aggregator hits
        if self.cfg.run.auto_discover:
            known = {(s.kind, s.ident) for s in sources}
            for j in matched:
                src = by_source.get(j.extra.get("_src_label", ""))
                if src is None or not src.is_aggregator:
                    continue
                entry = board_from_url(j.url, j.company)
                if not entry or (entry["kind"], entry["id"]) in known:
                    continue
                params = {k: v for k, v in entry.items() if k not in ("kind", "id", "company")}
                if self.store.add_board(entry["kind"], entry["id"], j.company, params, origin=f"aggregator:{src.ident}"):
                    known.add((entry["kind"], entry["id"]))
                    rep.boards_discovered += 1
                    log.info("discovered new board %s:%s (%s)", entry["kind"], entry["id"], j.company)

        # notify: instant tiers now, digest tiers queued
        to_notify = sort_for_notification(to_notify)
        digest_tiers = set(self.cfg.run.digest_tiers or [])
        instant = [j for j in to_notify if j.tier not in digest_tiers]
        queued = [j for j in to_notify if j.tier in digest_tiers]
        if to_notify:
            if self.dry_run:
                log.info("dry-run: would notify %d job(s) now, queue %d for the digest", len(instant), len(queued))
                for j in to_notify:
                    log.info("  [%s %d] %s — %s (%s) %s", j.tier, j.score, j.company, j.title, j.location, j.url)
                    self.store.record(j, True, "ok", notified=False)
            else:
                if instant:
                    failed = dispatch(self.notifiers, instant)
                    notified = len(failed) < len(self.notifiers) or not self.notifiers
                    for j in instant:
                        self.store.record(j, True, "ok", notified=notified)
                    rep.notified = instant
                if queued:
                    self.store.queue_for_digest(queued)
                    for j in queued:
                        self.store.record(j, True, "ok-queued", notified=False)
                    log.info("queued %d lower-tier posting(s) for the digest", len(queued))
        if not self.dry_run:
            self.flush_digest()
        if first_run:
            self.store.mark_first_run_done()
        rep.duration_s = time.time() - t0
        self.last_report = rep
        self.cycles += 1
        log.info("cycle done: %s", rep.summary())
        if rep.errors:
            log.info("failing sources (%d): %s", len(rep.errors), ", ".join(sorted(rep.errors)))
        parked = [s.label for s in sources if self._failures(s) >= int(self.cfg.run.max_board_failures or 10**9)]
        if parked:
            log.info("parked boards (%d, failed repeatedly): %s", len(parked), ", ".join(sorted(parked)))
        return rep

    # -- LLM judge ----------------------------------------------------------
    def _judge_all(self, decided: list, rep: CycleReport) -> list:
        judge = self.judge
        if not self.cfg.llm.enabled:
            return decided
        accepted = [(j, ok, r) for (j, ok, r) in decided if ok]
        if accepted and not judge.available:
            log.warning("LLM judge unavailable (%s); falling back to keyword tiers", judge.disabled_reason or "no client")
            for j, _, _ in accepted:
                if "llm-unjudged" not in j.flags:
                    j.flags.append("llm-unjudged")
            return decided
        # enrich descriptions first, so the judge sees the real posting (budgeted)
        budget = max(0, self.cfg.run.detail_fetch_limit - rep.details_fetched)
        need = [j for (j, ok, r) in accepted if not j.description]
        if need and budget:
            def _enrich(job):
                job.description = fetch_description(self.http, job.url)
                return job
            with ThreadPoolExecutor(max_workers=min(8, self.cfg.run.workers)) as ex:
                for job in ex.map(_enrich, need[:budget]):
                    rep.details_fetched += 1
                    if job.description:
                        job.has_full_description = True
        # judge (sequential-ish: a few threads keep rate limits comfortable)
        by_key = {j.key: (j, ok, r) for (j, ok, r) in decided}

        def _judge(job):
            return job, judge.judge(job, job.score)

        with ThreadPoolExecutor(max_workers=max(1, int(self.cfg.llm.concurrency))) as ex:
            for job, verdict in ex.map(_judge, [j for (j, _, _) in accepted]):
                if verdict.ok:
                    rep.llm_judged += 1
                ok, reason = judge.apply(job, verdict, self.classifier.tier_for)
                if not ok:
                    rep.llm_rejected += 1
                    log.info("LLM rejected: %s — %s (%s)", job.company, job.title, reason)
                by_key[job.key] = (job, ok, reason)
        return list(by_key.values())

    # -- chat commands ------------------------------------------------------
    def status_text(self) -> str:
        from html import escape
        rep = self.last_report
        st = self.store.stats()
        up = datetime.now(timezone.utc) - self.started_at
        lines = [f"🟢 <b>watcher alive</b> · up {int(up.total_seconds() // 3600)}h{int(up.total_seconds() % 3600 // 60):02d}m · "
                 f"{self.cycles} cycle(s) · every {self.cfg.run.interval_minutes:g} min"]
        if rep:
            age = int((datetime.now(timezone.utc) - rep.started).total_seconds() // 60)
            lines.append(f"last cycle {age} min ago: {escape(rep.summary())}")
            if rep.notified:
                lines.append("sent: " + ", ".join(escape(f"{j.company} — {j.title}") for j in rep.notified[:5]))
        lines.append(f"memory: {st['jobs_seen']} postings seen, {st['jobs_matched']} matched, "
                     f"{len(self.store.digest_queue())} waiting for the digest")
        lines.append(f"judge: {escape(self.judge.describe())}")
        return "\n".join(lines)

    def handle_command(self, cmd: str, arg: str = "") -> str:
        from html import escape
        if cmd in ("status", "start", "ping"):
            return self.status_text()
        if cmd == "last":
            n = int(arg) if arg.isdigit() else 8
            rows = self.store.matched_jobs(limit=n)
            if not rows:
                return "nothing matched yet"
            return "\n".join(f"[{r.get('tier', '?')} {r.get('score', '')}] <b>{escape(r['company'])}</b> — "
                              f"<a href=\"{r['url']}\">{escape(r['title'])}</a> ({escape(r['first_seen'][:16])})" for r in rows)
        if cmd == "digest":
            n = self.flush_digest(force=True)
            return f"digest sent with {n} posting(s)" if n else "nothing queued for the digest"
        if cmd == "help":
            return "/status · /last [n] · /digest · /help"
        return ""

    def poll_commands(self) -> None:
        for n in self.notifiers:
            if hasattr(n, "poll_commands"):
                try:
                    n.poll_commands(self.handle_command)
                except Exception as exc:  # noqa: BLE001
                    log.debug("command polling failed on %s: %s", n.name, exc)

    def announce(self, text: str) -> None:
        for n in self.notifiers:
            if hasattr(n, "send_status"):
                try:
                    n.send_status(text)
                except Exception as exc:  # noqa: BLE001
                    log.debug("status message failed on %s: %s", n.name, exc)

    # -- digest -------------------------------------------------------------
    def flush_digest(self, force: bool = False) -> int:
        """Send queued lower-tier postings once a day (at digest_time UTC) or when the queue is large."""
        rows = self.store.digest_queue()
        if not rows:
            return 0
        now = datetime.now(timezone.utc)
        hh, mm = (self.cfg.run.digest_time or "13:00").split(":")
        due_today = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        last = self.store.get("digest:last_date") or ""
        due = now >= due_today and last != now.date().isoformat()
        if not (force or due or len(rows) >= self.cfg.run.digest_max_queue):
            return 0
        jobs = sort_for_notification([Job.from_dict(r) for r in rows])
        heading = f"Digest: {len(jobs)} lower-priority posting(s) since last time"
        failed = dispatch(self.notifiers, jobs, heading=heading)
        if len(failed) < len(self.notifiers) or not self.notifiers:
            self.store.mark_notified([j.key for j in jobs])
            self.store.clear_digest_queue()
            self.store.set("digest:last_date", now.date().isoformat())
            log.info("digest sent: %d posting(s)", len(jobs))
            return len(jobs)
        log.warning("digest not sent (all channels failed); will retry next cycle")
        return 0

    # -- forever ------------------------------------------------------------
    def run_forever(self, max_minutes: Optional[float] = None) -> None:
        deadline = time.time() + max_minutes * 60 if max_minutes else None

        def _stop(*_):
            log.info("stop requested; finishing current cycle")
            self._stop = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass
        interval = max(60.0, float(self.cfg.run.interval_minutes) * 60.0)
        if not self.dry_run:
            self.announce(f"🟢 watcher started · polling {len(self.sources())} boards every "
                          f"{self.cfg.run.interval_minutes:g} min · judge: {self.judge.describe()} · send /status any time")
        while not self._stop:
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                log.exception("cycle crashed: %s", exc)
            if not self.dry_run:
                self.poll_commands()
            sleep_for = interval + random.uniform(0, self.cfg.run.jitter_seconds)
            if deadline and time.time() + sleep_for > deadline:
                log.info("max run time reached; exiting cleanly")
                break
            log.info("next cycle in %.0fs", sleep_for)
            end = time.time() + sleep_for
            next_poll = time.time() + 30
            while not self._stop and time.time() < end:
                time.sleep(min(5.0, max(0.0, end - time.time())))
                if not self.dry_run and time.time() >= next_poll:
                    self.poll_commands()
                    next_poll = time.time() + 30
