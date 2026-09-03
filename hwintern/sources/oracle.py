"""Oracle HCM / Oracle Recruiting Cloud career sites (…oraclecloud.com/hcmUI/CandidateExperience)."""
from __future__ import annotations

from ..textutil import html_to_text, parse_datetime
from .base import Source


class OracleHcmSource(Source):
    kind = "oracle"
    supports_details = True

    def __init__(self, http, entry, run_cfg=None):
        super().__init__(http, entry, run_cfg)
        if entry.get("host") and entry.get("site"):
            self.host, self.site = entry["host"], entry["site"]
        else:
            self.host, self.site = str(entry.get("id")).split("|", 1)
        self.keyword = entry.get("keyword", "intern")
        self.max_results = int(entry.get("max_results") or 300)
        self.api = f"https://{self.host}/hcmRestApi/resources/latest"

    @property
    def ident(self) -> str:
        return f"{self.host}|{self.site}"

    def fetch(self):
        jobs, offset, limit = [], 0, 100
        while offset < self.max_results:
            finder = (f"findReqs;siteNumber={self.site},keyword={self.keyword},limit={limit},offset={offset},"
                      f"sortBy=POSTING_DATES_DESC")
            url = (f"{self.api}/recruitingCEJobRequisitions?onlyData=true"
                   f"&expand=requisitionList.secondaryLocations&finder={finder}")
            data = self.http.get_json(url, headers={"Accept": "application/json"})
            items = data.get("items") or []
            reqs = items[0].get("requisitionList") or [] if items else []
            for r in reqs:
                rid = str(r.get("Id"))
                locs = [r.get("PrimaryLocation") or ""] + [s.get("Name") for s in r.get("secondaryLocations") or [] if s.get("Name")]
                jobs.append(self._job(
                    title=r.get("Title") or "",
                    url=f"https://{self.host}/hcmUI/CandidateExperience/en/sites/{self.site}/job/{rid}",
                    external_id=rid,
                    location="; ".join(l for l in locs if l),
                    description=html_to_text(r.get("ShortDescriptionStr") or ""),
                    posted_at=parse_datetime(r.get("PostedDate")),
                ))
            total = int(items[0].get("TotalJobsCount") or 0) if items else 0
            offset += limit
            if not reqs or offset >= total:
                break
        return jobs

    def fetch_details(self, job):
        finder = f"ByJobRequisitionId;Id={job.external_id},siteNumber={self.site}"
        url = f"{self.api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder={finder}"
        data = self.http.get_json(url, headers={"Accept": "application/json"})
        items = data.get("items") or []
        if items:
            d = items[0]
            job.description = html_to_text((d.get("ExternalDescriptionStr") or "") + "\n" + (d.get("ExternalQualificationsStr") or ""))
            job.has_full_description = True
