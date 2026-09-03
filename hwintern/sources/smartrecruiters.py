from __future__ import annotations

from ..textutil import html_to_text, parse_datetime
from .base import Source


class SmartRecruitersSource(Source):
    kind = "smartrecruiters"
    supports_details = True

    def fetch(self):
        company = self.ident
        max_pages = int(self.entry.get("max_pages") or 30)
        jobs, offset, limit = [], 0, 100
        for _ in range(max_pages):
            data = self.http.get_json(
                f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit={limit}&offset={offset}")
            content = data.get("content") or []
            for j in content:
                loc = j.get("location") or {}
                loc_s = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
                if loc.get("remote"):
                    loc_s = (loc_s + " (Remote)").strip()
                jobs.append(self._job(
                    title=j.get("name") or "",
                    url=f"https://jobs.smartrecruiters.com/{company}/{j.get('id')}",
                    external_id=str(j.get("id")),
                    location=loc_s,
                    posted_at=parse_datetime(j.get("releasedDate")),
                    extra={"employment": (j.get("typeOfEmployment") or {}).get("label"),
                           "level": (j.get("experienceLevel") or {}).get("label"),
                           "department": (j.get("department") or {}).get("label")},
                ))
            offset += limit
            if len(content) < limit or offset >= int(data.get("totalFound") or 0):
                break
        return jobs

    def fetch_details(self, job):
        data = self.http.get_json(
            f"https://api.smartrecruiters.com/v1/companies/{self.ident}/postings/{job.external_id}")
        sections = ((data.get("jobAd") or {}).get("sections") or {})
        text = "\n".join(html_to_text((sections.get(k) or {}).get("text") or "")
                         for k in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"))
        job.description = text.strip()
        job.has_full_description = True
        if data.get("postingUrl"):
            job.url = data["postingUrl"]
