"""Community-maintained internship feeds (SimplifyJobs / vanshb03 GitHub repos).

These repos publish a listings.json that is refreshed many times a day. It is the
fastest way to learn about hardware postings on career sites we do not poll
directly, and it is the seed for automatic board discovery.
"""
from __future__ import annotations

import json
import logging

from ..textutil import parse_datetime
from .base import Source

log = logging.getLogger(__name__)


class ListingsJsonSource(Source):
    kind = "listings-json"
    is_aggregator = True

    @property
    def ident(self) -> str:
        return str(self.entry.get("name") or self.entry.get("url"))

    def fetch(self):
        url = self.entry["url"]
        headers = {"Accept": "application/json"}
        etag_key = f"etag:{url}"
        cache_key = f"cache:{url}"
        etag = self.store.get(etag_key) if self.store else None
        if etag:
            headers["If-None-Match"] = etag
        resp = self.http.get(url, headers=headers)
        if resp.status_code == 304 and self.store:
            cached = self.store.get(cache_key)
            if cached:
                data = json.loads(cached)
            else:
                resp = self.http.get(url, headers={"Accept": "application/json"})
                resp.raise_for_status()
                data = resp.json()
        else:
            resp.raise_for_status()
            data = resp.json()
            if self.store and resp.headers.get("ETag"):
                self.store.set(etag_key, resp.headers["ETag"])
                self.store.set(cache_key, resp.text)
        jobs = []
        for x in data if isinstance(data, list) else []:
            if x.get("active") is False or x.get("is_visible") is False:
                continue
            terms = x.get("terms") or ([x["season"]] if x.get("season") else [])
            cat = x.get("category")
            if isinstance(cat, list):
                cat = ", ".join(cat)
            jobs.append(self._job(
                company=x.get("company_name") or "",
                title=x.get("title") or "",
                url=x.get("url") or "",
                external_id=str(x.get("id") or x.get("url")),
                location="; ".join(x.get("locations") or []),
                posted_at=parse_datetime(x.get("date_posted") or x.get("date_updated")),
                terms=[str(t) for t in terms],
                category=str(cat or ""),
                sponsorship=str(x.get("sponsorship") or ""),
                extra={"degrees": x.get("degrees"), "feed_source": x.get("source")},
            ))
        return jobs
