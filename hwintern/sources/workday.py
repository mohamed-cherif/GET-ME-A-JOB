"""Workday (myworkdayjobs.com / myworkdaysite.com) via the public CXS JSON endpoints."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..textutil import html_to_text, parse_datetime
from .base import Source

log = logging.getLogger(__name__)

_AGO_RE = re.compile(r"posted\s+(?:(today)|(yesterday)|(\d+)\+?\s+(day|week|month)s?\s+ago)", re.I)


def parse_posted_on(text: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' -> approximate datetime."""
    if not text:
        return None
    m = _AGO_RE.search(text)
    if not m:
        return parse_datetime(text)
    now = now or datetime.now(timezone.utc)
    if m.group(1):
        return now
    if m.group(2):
        return now - timedelta(days=1)
    n, unit = int(m.group(3)), m.group(4).lower()
    return now - timedelta(days=n * {"day": 1, "week": 7, "month": 30}[unit])


def csrf_headers(session) -> dict:
    """Workday's CXS endpoints reject POSTs (422) on some tenants unless the CSRF cookie is echoed as a header."""
    try:
        token = session.cookies.get("CALYPSO_CSRF_TOKEN")
    except Exception:  # noqa: BLE001
        token = None
    return {"X-CALYPSO-CSRF-TOKEN": token} if token else {}


def workday_parts(entry: dict) -> tuple[str, str, str]:
    """Return (host, tenant, site) from an entry with host/tenant/site or an id like 'host|tenant|site'."""
    if entry.get("host") and entry.get("site"):
        host = entry["host"]
        tenant = entry.get("tenant") or host.split(".")[0]
        return host, tenant, entry["site"]
    ident = str(entry.get("id") or "")
    parts = ident.split("|")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[0].split(".")[0], parts[1]
    raise ValueError(f"workday entry needs host/tenant/site: {entry}")


class WorkdaySource(Source):
    kind = "workday"
    supports_details = True

    def __init__(self, http, entry, run_cfg=None):
        super().__init__(http, entry, run_cfg)
        self.host, self.tenant, self.site = workday_parts(entry)
        self.search_text = entry.get("search_text", "intern")
        self.max_results = int(entry.get("max_results") or 400)
        if "myworkdaysite.com" in self.host:
            self.public_base = f"https://{self.host}/recruiting/{self.tenant}/{self.site}"
        else:
            self.public_base = f"https://{self.host}/{self.site}"
        self.cxs_base = f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    @property
    def ident(self) -> str:
        return f"{self.host}|{self.tenant}|{self.site}"

    def _session(self):
        s = self.http.new_session()
        try:  # prime cookies; some tenants 4xx the CXS endpoint without them
            self.http.get(self.public_base, session=s, timeout=20)
        except Exception as exc:  # noqa: BLE001
            log.debug("workday %s cookie priming failed: %s", self.ident, exc)
        s.headers.update({"Accept": "application/json", "Content-Type": "application/json",
                          "Origin": f"https://{self.host}", "Referer": self.public_base + "/",
                          "X-Requested-With": "XMLHttpRequest", **csrf_headers(s)})
        return s

    def _discover_site(self) -> bool:
        """The tenant root redirects to its default career site (e.g. /en-US/Qualcomm_Careers). Adopt it."""
        try:
            resp = self.http.get(f"https://{self.host}/", timeout=20, allow_redirects=True)
        except Exception:  # noqa: BLE001
            return False
        path = [seg for seg in resp.url.split("/")[3:] if seg]
        site = next((seg for seg in path if not re.fullmatch(r"[a-z]{2}-[A-Za-z]{2}", seg)), None)
        if not site or site == self.site or site in ("job", "details", "wday"):
            return False
        log.info("workday %s: site %r not found, switching to %r (from the tenant's redirect)", self.host, self.site, site)
        self.site = site
        self.public_base = f"https://{self.host}/{self.site}"
        self.cxs_base = f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"
        if self.store is not None:
            self.store.set(f"workday-site:{self.host}|{self.tenant}", site)
        return True

    def fetch(self):
        if self.store is not None:
            fixed = self.store.get(f"workday-site:{self.host}|{self.tenant}")
            if fixed and fixed != self.site:
                self.site = fixed
                self.public_base = f"https://{self.host}/{self.site}"
                self.cxs_base = f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"
        s = self._session()
        jobs, offset, limit = [], 0, 20
        repaired = False
        while offset < self.max_results:
            payload = {"appliedFacets": self.entry.get("facets") or {}, "limit": limit, "offset": offset,
                       "searchText": self.search_text}
            resp = self.http.post(f"{self.cxs_base}/jobs", session=s, json=payload)
            if resp.status_code in (404, 422) and not repaired and offset == 0:
                repaired = True
                if self._discover_site():
                    s = self._session()
                    continue
            if resp.status_code >= 400:
                body = (resp.text or "")[:160].replace("\n", " ")
                raise RuntimeError(f"HTTP {resp.status_code} from {self.cxs_base}/jobs: {body}")
            data = resp.json()
            postings = data.get("jobPostings") or []
            for p in postings:
                path = p.get("externalPath") or ""
                if not path:
                    continue
                m = re.search(r"_([A-Za-z0-9-]+)$", path)
                ext_id = m.group(1) if m else path.rsplit("/", 1)[-1]
                jobs.append(self._job(
                    title=p.get("title") or "",
                    url=f"{self.public_base}{path}",
                    external_id=ext_id,
                    location=p.get("locationsText") or "",
                    posted_at=parse_posted_on(p.get("postedOn")),
                    extra={"posted_on": p.get("postedOn"), "bullets": p.get("bulletFields"), "path": path},
                ))
            offset += limit
            total = int(data.get("total") or 0)
            if not postings or offset >= total:
                break
        return jobs

    def fetch_details(self, job):
        s = self._session()
        path = job.extra.get("path") or ""
        resp = self.http.get(f"{self.cxs_base}{path}", session=s)
        resp.raise_for_status()
        info = (resp.json() or {}).get("jobPostingInfo") or {}
        job.description = html_to_text(info.get("jobDescription") or "")
        job.has_full_description = True
        if info.get("externalUrl"):
            job.url = info["externalUrl"]
        job.posted_at = parse_datetime(info.get("startDate")) or job.posted_at
        if info.get("location") and not job.location:
            job.location = info["location"]
        if info.get("jobReqId"):
            job.extra["req_id"] = info["jobReqId"]
