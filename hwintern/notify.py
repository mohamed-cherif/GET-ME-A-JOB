"""Notification channels. Every notifier takes a list of Jobs and pushes them."""
from __future__ import annotations

import json
import logging
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from .http import Http
from .models import Job
from .textutil import truncate

log = logging.getLogger(__name__)

TIER_LABELS = {"target": "🎯 TARGET", "match": "✅ MATCH", "safety": "🟡 SAFETY"}
TIER_ORDER = {"target": 0, "match": 1, "safety": 2}

FLAG_LABELS = {
    "priority": "⭐ priority match",
    "location-unknown": "location not stated (check country)",
    "remote": "remote",
    "other-term": "not Summer term (see Term)",
    "phd-title": "PhD role",
    "grad-only": "graduate students only",
    "citizenship-required": "US citizenship / green card / clearance / ITAR language",
    "no-sponsorship": "no visa sponsorship",
    "sponsorship-offered": "sponsorship offered",
    "phd": "mentions PhD",
    "masters": "mentions Master's",
    "returning-students": "returning students",
    "term-unknown": "term not stated (verify it is Summer 2027)",
}


def _fmt_posted(job: Job) -> str:
    if not job.posted_at:
        return "posted: unknown"
    age = job.age_days()
    if age is None:
        return "posted: unknown"
    if age < 1 / 24:
        return "posted: just now"
    if age < 1:
        return f"posted: {int(age * 24)}h ago"
    return f"posted: {int(age)}d ago"


_QUIET_FLAGS = {"priority", "remote"}


def _flags_line(job: Job) -> str:
    fl = [FLAG_LABELS.get(f, f) for f in job.flags if f not in _QUIET_FLAGS]
    return (" · ".join(fl)) if fl else ""


def tier_label(job: Job) -> str:
    return f"{TIER_LABELS.get(job.tier, job.tier)} {job.score}"


def group_by_tier(jobs: list[Job]) -> list[tuple[str, list[Job]]]:
    out: list[tuple[str, list[Job]]] = []
    for tier in ("target", "match", "safety"):
        chunk = [j for j in jobs if j.tier == tier]
        if chunk:
            out.append((tier, chunk))
    return out


def _star(job: Job) -> str:
    return "⭐ " if "priority" in job.flags else ""


def sort_for_notification(jobs: list[Job]) -> list[Job]:
    """Best tier first, then highest fit score, then company."""
    return sorted(jobs, key=lambda j: (TIER_ORDER.get(j.tier, 9), -j.score, j.company.lower(), j.title.lower()))


def job_text_line(job: Job, markdown: bool = True) -> str:
    where = f" — {job.location}" if job.location else ""
    terms = f" [{', '.join(job.detected_terms)}]" if job.detected_terms else ""
    line = (f"{_star(job)}**{job.company}** — {job.title}{where}{terms}\n{job.url}" if markdown
            else f"{_star(job)}{job.company} — {job.title}{where}{terms}\n{job.url}")
    fl = _flags_line(job)
    if fl:
        line += f"\n⚠️ {fl}"
    return line


class Notifier:
    name = "base"

    def __init__(self, cfg: dict, http: Http):
        self.cfg = cfg
        self.http = http
        self.store = None  # set by the pipeline / CLI; lets a channel persist small bits of state

    def send(self, jobs: list[Job], heading: str = "") -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def send_text(self, text: str) -> None:
        """Plain message (used by `test-notify`)."""
        raise NotImplementedError


class StdoutNotifier(Notifier):
    name = "stdout"

    def send(self, jobs: list[Job], heading: str = "") -> None:
        if heading:
            print(f"== {heading} ==")
        for j in jobs:
            print(f"[{tier_label(j)}] {job_text_line(j, markdown=False)}  ({_fmt_posted(j)}; via {j.source})")

    def send_text(self, text: str) -> None:
        print(text)


class FileNotifier(Notifier):
    """Appends to a JSONL log and a human-readable markdown file."""
    name = "file"

    def send(self, jobs: list[Job], heading: str = "") -> None:
        base = Path(self.cfg.get("path") or "state")
        base.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with (base / "new_jobs.jsonl").open("a", encoding="utf-8") as fh:
            for j in jobs:
                d = j.to_dict()
                d["notified_at"] = now
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
        md = base / "NEW_JOBS.md"
        new_file = not md.exists()
        with md.open("a", encoding="utf-8") as fh:
            if new_file:
                fh.write("# New hardware internship postings\n\n| Found | Tier | Company | Title | Location | Terms | Flags | Link |\n|---|---|---|---|---|---|---|---|\n")
            for j in jobs:
                fh.write(f"| {now[:16]} | {tier_label(j)} | {j.company} | {j.title} | {j.location} | {', '.join(j.detected_terms)} | "
                         f"{', '.join(j.flags)} | [apply]({j.url}) |\n")

    def send_text(self, text: str) -> None:
        log.info("file notifier test: %s", text)


class DiscordNotifier(Notifier):
    name = "discord"

    def _post(self, payload: dict) -> None:
        url = self.cfg["webhook_url"]
        for attempt in range(5):
            resp = self.http.post(url, json=payload, headers={"Content-Type": "application/json"})
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After") or 2)
                time.sleep(min(wait, 30))
                continue
            resp.raise_for_status()
            return
        raise RuntimeError("discord: rate limited repeatedly")

    def send(self, jobs: list[Job], heading: str = "") -> None:
        mention = self.cfg.get("mention") or ""
        for i in range(0, len(jobs), 10):
            chunk = jobs[i:i + 10]
            embeds = []
            for j in chunk:
                fields = []
                if j.location:
                    fields.append({"name": "Location", "value": truncate(j.location, 200), "inline": True})
                if j.detected_terms:
                    fields.append({"name": "Term", "value": ", ".join(j.detected_terms)[:200], "inline": True})
                if j.sponsorship and j.sponsorship != "Other":
                    fields.append({"name": "Sponsorship", "value": j.sponsorship[:200], "inline": True})
                fl = _flags_line(j)
                if fl:
                    fields.append({"name": "Heads-up", "value": truncate(fl, 300), "inline": False})
                embeds.append({
                    "title": truncate(f"{TIER_LABELS.get(j.tier, '')} {j.score} · {_star(j)}{j.company} — {j.title}", 250),
                    "url": j.url,
                    "description": truncate(j.description.replace("\n", " "), 220) if j.description else "",
                    "color": {"target": 0x2ECC71, "match": 0x3498DB}.get(j.tier, 0xF1C40F),
                    "footer": {"text": f"{_fmt_posted(j)} · via {j.source}"},
                    "fields": fields,
                })
            content = f"{mention} {heading or f'{len(jobs)} new hardware internship posting(s)'}".strip() if i == 0 else ""
            self._post({"content": content, "embeds": embeds, "allowed_mentions": {"parse": ["users", "roles", "everyone"]}})
            time.sleep(0.5)

    def send_text(self, text: str) -> None:
        self._post({"content": text})


class TelegramNotifier(Notifier):
    """Telegram bot. Setup: create a bot with @BotFather, open it in Telegram and press Start.
    The chat id is discovered automatically from that first message (or set TELEGRAM_CHAT_ID)."""
    name = "telegram"

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.cfg['bot_token']}/{method}"

    def chat_id(self) -> str:
        chat = str(self.cfg.get("chat_id") or "").strip()
        if chat:
            return chat
        if self.store is not None:
            cached = self.store.get("telegram:chat_id")
            if cached:
                return cached
        resp = self.http.get(self._api("getUpdates"), params={"allowed_updates": '["message"]'})
        if resp.status_code == 409:   # a webhook is set; getUpdates is blocked until it is removed
            self.http.get(self._api("deleteWebhook"))
            resp = self.http.get(self._api("getUpdates"), params={"allowed_updates": '["message"]'})
        if resp.status_code >= 400:
            raise RuntimeError(f"telegram getUpdates failed: {self._describe(resp)}")
        updates = resp.json().get("result") or []
        for u in reversed(updates):
            msg = u.get("message") or u.get("edited_message") or {}
            cid = (msg.get("chat") or {}).get("id")
            if cid is not None:
                if self.store is not None:
                    self.store.set("telegram:chat_id", str(cid))
                log.info("telegram: using chat id %s (from %s)", cid, (msg.get("chat") or {}).get("username") or "your chat")
                return str(cid)
        raise RuntimeError("telegram: no chat id yet - open your bot in Telegram, press Start (send any message), "
                           "then retry; or set TELEGRAM_CHAT_ID")

    @staticmethod
    def _describe(resp) -> str:
        try:
            d = resp.json()
            return f"HTTP {resp.status_code}: {d.get('description') or d}"
        except Exception:  # noqa: BLE001
            return f"HTTP {resp.status_code}"

    def diagnose(self) -> str:
        """Human-readable check used by `test-notify`: token valid? chat id known?"""
        resp = self.http.get(self._api("getMe"))
        if resp.status_code == 401:
            return "token rejected by Telegram (401). Re-check TELEGRAM_BOT_TOKEN: it must be the full " \
                   "'123456789:AAxxxxxxxx' string from @BotFather, with no spaces or quotes."
        resp.raise_for_status()
        me = resp.json().get("result") or {}
        info = f"bot @{me.get('username')} (id {me.get('id')}) ok"
        hook = self.http.get(self._api("getWebhookInfo")).json().get("result") or {}
        if hook.get("url"):
            self.http.get(self._api("deleteWebhook"))
            info += "; removed a stale webhook that was blocking getUpdates"
        try:
            chat = self.chat_id()
            return f"{info}; chat id {chat}"
        except RuntimeError as exc:
            return f"{info}; {exc}"

    def _send(self, text: str, silent: bool = False) -> None:
        chat = self.chat_id()
        url = self._api("sendMessage")
        payload = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True,
                   "disable_notification": silent}
        for attempt in range(5):
            resp = self.http.post(url, json=payload)
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 3)
                time.sleep(min(float(wait), 30))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"telegram sendMessage failed: {self._describe(resp)}")
            return

    @staticmethod
    def _esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def send(self, jobs: list[Job], heading: str = "") -> None:
        silent_tiers = set(self.cfg.get("silent_tiers") or ["safety"])
        for tier, chunk in group_by_tier(jobs):
            header = f"<b>{TIER_LABELS[tier]}</b> · {len(chunk)} posting(s)" + (f" · {self._esc(heading)}" if heading else "")
            lines, size = [header, ""], len(header)
            for j in chunk:
                fl = _flags_line(j)
                block = (f"{_star(j) or '🔧 '}<b>{self._esc(j.company)}</b> — <a href=\"{j.url}\">{self._esc(j.title)}</a> "
                         f"<i>({j.score})</i>\n"
                         f"📍 {self._esc(j.location) or 'n/a'}"
                         + (f" · 🗓 {self._esc(', '.join(j.detected_terms))}" if j.detected_terms else "")
                         + f" · {_fmt_posted(j)}"
                         + (f"\n⚠️ {self._esc(fl)}" if fl else "") + "\n")
                if size + len(block) > 3800:
                    self._send("\n".join(lines), silent=tier in silent_tiers)
                    lines, size = [f"<b>{TIER_LABELS[tier]}</b> (cont.)", ""], 40
                lines.append(block)
                size += len(block)
            if len(lines) > 2:
                self._send("\n".join(lines), silent=tier in silent_tiers)

    def send_text(self, text: str) -> None:
        self._send(self._esc(text))


class SlackNotifier(Notifier):
    name = "slack"

    def send(self, jobs: list[Job], heading: str = "") -> None:
        url = self.cfg["webhook_url"]
        for i in range(0, len(jobs), 20):
            chunk = jobs[i:i + 20]
            blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{len(chunk)} new hardware internship posting(s)"}}]
            for j in chunk:
                fl = _flags_line(j)
                text = (f"{tier_label(j)} · *<{j.url}|{j.company} — {j.title}>*\n{j.location or 'n/a'}"
                        + (f" · {', '.join(j.detected_terms)}" if j.detected_terms else "")
                        + f" · {_fmt_posted(j)}" + (f"\n:warning: {fl}" if fl else ""))
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": truncate(text, 2900)}})
            resp = self.http.post(url, json={"blocks": blocks, "text": f"{len(chunk)} new hardware internship posting(s)"})
            resp.raise_for_status()

    def send_text(self, text: str) -> None:
        self.http.post(self.cfg["webhook_url"], json={"text": text}).raise_for_status()


class NtfyNotifier(Notifier):
    """https://ntfy.sh — zero-signup push notifications to your phone."""
    name = "ntfy"

    def send(self, jobs: list[Job], heading: str = "") -> None:
        base = (self.cfg.get("server") or "https://ntfy.sh").rstrip("/")
        topic = self.cfg["topic"]
        headers_base = {}
        if self.cfg.get("token"):
            headers_base["Authorization"] = f"Bearer {self.cfg['token']}"
        for j in jobs:
            fl = _flags_line(j)
            body = f"{j.location or 'n/a'}" + (f" · {', '.join(j.detected_terms)}" if j.detected_terms else "") \
                   + f" · {_fmt_posted(j)}" + (f"\n⚠️ {fl}" if fl else "")
            headers = dict(headers_base)
            headers.update({
                "Title": truncate(f"[{j.tier.upper()} {j.score}] {j.company}: {j.title}", 200).encode("ascii", "ignore").decode(),
                "Click": j.url,
                "Tags": "wrench",
                "Priority": {"target": "urgent", "match": "high"}.get(j.tier, "low"),
                "Actions": f"view, Apply, {j.url}",
            })
            resp = self.http.post(f"{base}/{topic}", data=body.encode("utf-8"), headers=headers)
            resp.raise_for_status()
            time.sleep(0.2)

    def send_text(self, text: str) -> None:
        base = (self.cfg.get("server") or "https://ntfy.sh").rstrip("/")
        headers = {"Title": "hardware internships watcher"}
        if self.cfg.get("token"):
            headers["Authorization"] = f"Bearer {self.cfg['token']}"
        self.http.post(f"{base}/{self.cfg['topic']}", data=text.encode("utf-8"), headers=headers).raise_for_status()


class EmailNotifier(Notifier):
    name = "email"

    def _send(self, subject: str, text: str, html: Optional[str] = None) -> None:
        c = self.cfg
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = c.get("from_addr") or c["username"]
        to = c["to_addr"] if isinstance(c["to_addr"], list) else [c["to_addr"]]
        msg["To"] = ", ".join(to)
        msg.attach(MIMEText(text, "plain", "utf-8"))
        if html:
            msg.attach(MIMEText(html, "html", "utf-8"))
        host, port = c.get("smtp_host", "smtp.gmail.com"), int(c.get("smtp_port", 587))
        if c.get("ssl"):
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        with server:
            if c.get("username"):
                server.login(c["username"], c["password"])
            server.sendmail(msg["From"], to, msg.as_string())

    def send(self, jobs: list[Job], heading: str = "") -> None:
        subject = f"[HW internships] {heading or f'{len(jobs)} new posting(s)'}: " + truncate(", ".join(sorted({j.company for j in jobs})), 80)
        text = "\n\n".join(f"{job_text_line(j, markdown=False)}\n{_fmt_posted(j)}" for j in jobs)
        rows = "".join(
            f"<tr><td>{tier_label(j)}</td><td><b>{j.company}</b></td><td><a href='{j.url}'>{j.title}</a></td><td>{j.location}</td>"
            f"<td>{', '.join(j.detected_terms)}</td><td>{_flags_line(j)}</td><td>{_fmt_posted(j)}</td></tr>" for j in jobs)
        html = ("<p>New hardware internship postings:</p><table border='1' cellpadding='4' cellspacing='0'>"
                "<tr><th>Tier</th><th>Company</th><th>Title</th><th>Location</th><th>Term</th><th>Heads-up</th><th>Posted</th></tr>"
                f"{rows}</table>")
        self._send(subject, text, html)

    def send_text(self, text: str) -> None:
        self._send("[HW internships] test notification", text)


class WebhookNotifier(Notifier):
    """Generic JSON POST (Zapier, Make, n8n, your own server...)."""
    name = "webhook"

    def send(self, jobs: list[Job], heading: str = "") -> None:
        headers = {"Content-Type": "application/json"}
        headers.update(self.cfg.get("headers") or {})
        payload = {"event": "new_jobs", "heading": heading, "count": len(jobs), "jobs": [j.to_dict() for j in jobs]}
        self.http.post(self.cfg["url"], json=payload, headers=headers).raise_for_status()

    def send_text(self, text: str) -> None:
        self.http.post(self.cfg["url"], json={"event": "test", "text": text},
                       headers={"Content-Type": "application/json"}).raise_for_status()


NOTIFIERS = {c.name: c for c in (StdoutNotifier, FileNotifier, DiscordNotifier, TelegramNotifier,
                                 SlackNotifier, NtfyNotifier, EmailNotifier, WebhookNotifier)}

_REQUIRED = {
    "discord": ["webhook_url"], "telegram": ["bot_token"], "slack": ["webhook_url"],
    "ntfy": ["topic"], "email": ["to_addr", "username", "password"], "webhook": ["url"],
}


def build_notifiers(entries: list[dict], http: Http, store=None) -> list[Notifier]:
    out: list[Notifier] = []
    for e in entries:
        kind = (e.get("type") or "").lower()
        cls = NOTIFIERS.get(kind)
        if not cls:
            log.warning("unknown notifier type %r (skipped)", kind)
            continue
        missing = [k for k in _REQUIRED.get(kind, []) if not e.get(k)]
        if missing:
            log.warning("notifier %s skipped: missing %s (set the env var or config key)", kind, ", ".join(missing))
            continue
        n = cls(e, http)
        n.store = store
        out.append(n)
    return out


def dispatch(notifiers: list[Notifier], jobs: list[Job], heading: str = "") -> list[str]:
    """Send to every channel; returns names of channels that failed."""
    failed = []
    for n in notifiers:
        try:
            n.send(jobs, heading=heading)
        except Exception as exc:  # noqa: BLE001 - never let one channel kill the run
            log.error("notifier %s failed: %s", n.name, exc)
            failed.append(n.name)
    return failed
