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
from .models import Job
from .notify import Notifier, build_notifiers, dispatch, sort_for_notification
from .sources import build_source
from .sources.base import Source
from .sources.discovery import board_from_url
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class CycleReport:
    started: datetime
    sources_total: int = 0
    sources_failed: int = 0
    jobs_fetched: int = 0
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
                f"details {self.details_fetched} · discovered boards {self.boards_discovered} · "
                f"{self.duration_s:.1f}s")


class Pipeline:
    def __init__(self, cfg: Config, store: Optional[Store] = None, http: Optional[Http] = None,
                 notifiers: Optional[list[Notifier]] = None, dry_run: bool = False):
        self.cfg = cfg
        self.http = http or Http(timeout=cfg.run.request_timeout)
        self.store = store or Store(cfg.db_path)
        self.classifier = Classifier(cfg.filters)
        self.notifiers = notifiers if notifiers is not None else build_notifiers(cfg.notifiers, self.http, self.store)
        self.dry_run = dry_run
        self._stop = False

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

    def _run_source(self, src: Source) -> tuple[Source, list[Job], Optional[str]]:
        try:
            jobs = src.fetch()
            self.store.board_result(src.kind, src.ident, None)
            return src, jobs, None
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            self.store.board_result(src.kind, src.ident, msg)
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
        log.info("cycle done: %s", rep.summary())
        if rep.errors:
            log.info("failing sources (%d): %s", len(rep.errors), ", ".join(sorted(rep.errors)))
        return rep

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
    def run_forever(self) -> None:
        def _stop(*_):
            log.info("stop requested; finishing current cycle")
            self._stop = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass
        interval = max(60.0, float(self.cfg.run.interval_minutes) * 60.0)
        while not self._stop:
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                log.exception("cycle crashed: %s", exc)
            sleep_for = interval + random.uniform(0, self.cfg.run.jitter_seconds)
            log.info("next cycle in %.0fs", sleep_for)
            end = time.time() + sleep_for
            while not self._stop and time.time() < end:
                time.sleep(min(5.0, end - time.time()))
