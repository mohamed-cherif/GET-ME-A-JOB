from __future__ import annotations

from ..textutil import html_to_text, parse_datetime
from .base import Source


class LeverSource(Source):
    kind = "lever"

    def fetch(self):
        site = self.ident
        data = self.http.get_json(f"https://api.lever.co/v0/postings/{site}?mode=json")
        jobs = []
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            loc = cats.get("location") or ""
            if j.get("workplaceType") == "remote" and "remote" not in loc.lower():
                loc = (loc + " (Remote)").strip()
            desc_parts = [j.get("descriptionPlain") or ""]
            for lst in j.get("lists") or []:
                desc_parts.append(lst.get("text") or "")
                desc_parts.append(lst.get("content") or "")
            desc_parts.append(j.get("additionalPlain") or "")
            desc = html_to_text("\n".join(p for p in desc_parts if p))
            jobs.append(self._job(
                title=j.get("text") or "",
                url=j.get("hostedUrl") or f"https://jobs.lever.co/{site}/{j.get('id')}",
                external_id=str(j.get("id")),
                location=loc,
                description=desc,
                has_full_description=bool(desc),
                posted_at=parse_datetime(j.get("createdAt")),
                extra={"team": cats.get("team"), "commitment": cats.get("commitment")},
            ))
        return jobs
