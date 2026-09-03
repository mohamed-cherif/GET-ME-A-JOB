from __future__ import annotations

from ..textutil import html_to_text, parse_datetime
from .base import Source


class GreenhouseSource(Source):
    kind = "greenhouse"
    supports_details = False  # content comes with the listing

    def fetch(self):
        token = self.ident
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        data = self.http.get_json(url)
        jobs = []
        for j in data.get("jobs", []):
            loc = (j.get("location") or {}).get("name") or ""
            offices = [o.get("name") for o in j.get("offices") or [] if o.get("name")]
            if not loc and offices:
                loc = "; ".join(offices)
            desc = html_to_text(j.get("content") or "")
            jobs.append(self._job(
                title=j.get("title") or "",
                url=j.get("absolute_url") or f"https://boards.greenhouse.io/{token}/jobs/{j.get('id')}",
                external_id=str(j.get("id")),
                location=loc,
                description=desc,
                has_full_description=bool(desc),
                posted_at=parse_datetime(j.get("first_published") or j.get("updated_at")),
                extra={"departments": [d.get("name") for d in j.get("departments") or []]},
            ))
        return jobs
