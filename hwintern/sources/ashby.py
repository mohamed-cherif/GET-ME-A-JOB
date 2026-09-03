from __future__ import annotations

from ..textutil import html_to_text, parse_datetime
from .base import Source


class AshbySource(Source):
    kind = "ashby"

    def fetch(self):
        name = self.ident
        data = self.http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true")
        jobs = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            locs = [j.get("location") or ""] + [s.get("location") for s in j.get("secondaryLocations") or [] if s.get("location")]
            loc = "; ".join(l for l in locs if l)
            if j.get("isRemote") and "remote" not in loc.lower():
                loc = (loc + " (Remote)").strip()
            desc = j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or "")
            jobs.append(self._job(
                title=j.get("title") or "",
                url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{name}/{j.get('id')}",
                external_id=str(j.get("id")),
                location=loc,
                description=desc,
                has_full_description=bool(desc),
                posted_at=parse_datetime(j.get("publishedAt")),
                extra={"department": j.get("department"), "team": j.get("team"),
                       "employmentType": j.get("employmentType")},
            ))
        return jobs
