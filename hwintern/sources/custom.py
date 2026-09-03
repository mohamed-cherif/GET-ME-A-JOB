"""Custom career sites with public JSON search endpoints (no ATS)."""
from __future__ import annotations

import logging
import re

from ..textutil import html_to_text, parse_datetime
from .base import Source

log = logging.getLogger(__name__)


class TeslaSource(Source):
    """Tesla publishes every open req as one JSON blob."""
    kind = "tesla"

    @property
    def ident(self) -> str:
        return "tesla"

    @property
    def company(self) -> str:
        return self.entry.get("company") or "Tesla"

    def fetch(self):
        data = self.http.get_json("https://www.tesla.com/cua-api/apps/careers/state",
                                  headers={"Referer": "https://www.tesla.com/careers/search/"})
        listings = data.get("listings") or []
        lookup = data.get("lookup") or {}
        locations = lookup.get("locations") or {}
        departments = lookup.get("departments") or {}

        def _name(table, key):
            v = table.get(str(key)) if isinstance(table, dict) else None
            if isinstance(v, dict):
                return v.get("name") or v.get("label") or v.get("title") or ""
            return v or ""

        jobs = []
        for l in listings:
            jid = str(l.get("id") or "")
            title = l.get("t") or l.get("title") or ""
            if not jid or not title:
                continue
            loc_keys = l.get("l") if isinstance(l.get("l"), list) else [l.get("l")]
            loc = "; ".join(_name(locations, k) for k in loc_keys if k is not None) or str(l.get("location") or "")
            dept = _name(departments, l.get("dp")) or str(l.get("department") or "")
            jobs.append(self._job(
                title=title, url=f"https://www.tesla.com/careers/search/job/{jid}", external_id=jid,
                location=loc, posted_at=parse_datetime(l.get("y") or l.get("posted")),
                extra={"department": dept},
            ))
        return jobs


class AmazonSource(Source):
    """amazon.jobs search JSON."""
    kind = "amazon"

    @property
    def ident(self) -> str:
        return "amazon"

    @property
    def company(self) -> str:
        return self.entry.get("company") or "Amazon"

    def fetch(self):
        jobs, offset, limit = [], 0, 100
        max_results = int(self.entry.get("max_results") or 500)
        query = self.entry.get("query", "intern")
        while offset < max_results:
            url = (f"https://www.amazon.jobs/en/search.json?base_query={query}&sort=recent&result_limit={limit}"
                   f"&offset={offset}&loc_query=&latitude=&longitude=&country=&city=&region=&county=")
            data = self.http.get_json(url, headers={"Referer": "https://www.amazon.jobs/en/search"})
            batch = data.get("jobs") or []
            for j in batch:
                jid = str(j.get("id_icims") or j.get("id") or "")
                jobs.append(self._job(
                    title=j.get("title") or "", url="https://www.amazon.jobs" + (j.get("job_path") or f"/en/jobs/{jid}"),
                    external_id=jid, location=j.get("normalized_location") or j.get("location") or "",
                    description=html_to_text((j.get("description_short") or "") + "\n" + (j.get("basic_qualifications") or "")),
                    has_full_description=bool(j.get("description")),
                    posted_at=parse_datetime(j.get("posted_date")),
                    extra={"category": j.get("job_category"), "family": j.get("job_family")},
                ))
            offset += limit
            if not batch or offset >= int(data.get("hits") or 0):
                break
        return jobs


class MicrosoftSource(Source):
    """Microsoft careers search API."""
    kind = "microsoft"

    @property
    def ident(self) -> str:
        return "microsoft"

    @property
    def company(self) -> str:
        return self.entry.get("company") or "Microsoft"

    def fetch(self):
        jobs, page, size = [], 1, 20
        max_pages = int(self.entry.get("max_pages") or 15)
        query = self.entry.get("query", "intern")
        while page <= max_pages:
            url = (f"https://gcsservices.careers.microsoft.com/search/api/v1/search?q={query}&l=en_us"
                   f"&pg={page}&pgSz={size}&o=Recent&flt=true")
            data = self.http.get_json(url, headers={"Referer": "https://jobs.careers.microsoft.com/"})
            result = ((data.get("operationResult") or {}).get("result") or {})
            batch = result.get("jobs") or []
            for j in batch:
                jid = str(j.get("jobId") or "")
                props = j.get("properties") or {}
                locs = props.get("locations") or []
                jobs.append(self._job(
                    title=j.get("title") or "", url=f"https://jobs.careers.microsoft.com/global/en/job/{jid}",
                    external_id=jid, location="; ".join(locs) if isinstance(locs, list) else str(locs),
                    description=html_to_text(props.get("description") or ""),
                    posted_at=parse_datetime(j.get("postingDate")),
                    extra={"profession": props.get("profession"), "discipline": props.get("discipline")},
                ))
            total = int(result.get("totalJobs") or 0)
            if not batch or page * size >= total:
                break
            page += 1
        return jobs
