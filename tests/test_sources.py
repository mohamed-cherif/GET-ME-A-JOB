import unittest

from hwintern.sources import build_source
from hwintern.sources.discovery import board_from_url
from tests.fakes import FakeHttp, FakeResponse

GREENHOUSE = {"jobs": [{"id": 123, "title": "Electrical Engineer Intern - Summer 2027",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                        "location": {"name": "Austin, TX"}, "updated_at": "2026-09-01T10:00:00-04:00",
                        "content": "&lt;p&gt;Design &amp;amp; test PCBs.&lt;/p&gt;", "departments": [{"name": "HW"}]}]}
LEVER = [{"id": "abc", "text": "Firmware Intern", "categories": {"location": "San Mateo, CA", "team": "FW"},
          "hostedUrl": "https://jobs.lever.co/acme/abc", "createdAt": 1788220800000,
          "descriptionPlain": "Write firmware.", "lists": [{"text": "Reqs", "content": "<li>C</li>"}]}]
ASHBY = {"jobs": [{"id": "u1", "title": "RF Engineer Intern", "location": "LA", "isListed": True,
                   "secondaryLocations": [{"location": "Torrance, CA"}], "publishedAt": "2026-08-30T00:00:00Z",
                   "jobUrl": "https://jobs.ashbyhq.com/acme/u1", "descriptionHtml": "<p>Antennas</p>"}]}
SR_LIST = {"totalFound": 1, "content": [{"id": "744000", "name": "Hardware Engineering Intern",
                                          "releasedDate": "2026-09-02T00:00:00.000Z",
                                          "location": {"city": "Fremont", "region": "CA", "country": "us"}}]}
SR_DETAIL = {"postingUrl": "https://jobs.smartrecruiters.com/Acme/744000",
             "jobAd": {"sections": {"jobDescription": {"text": "<p>Summer 2027. Board bring-up.</p>"}}}}
WD_LIST = {"total": 2, "jobPostings": [
    {"title": "Hardware ASIC Design Intern", "externalPath": "/job/US-CA-Santa-Clara/HW-ASIC_JR2023486",
     "locationsText": "Santa Clara, CA", "postedOn": "Posted Today"},
    {"title": "Engineering Intern", "externalPath": "/job/US-TX-Austin/Eng-Intern_JR1", "locationsText": "Austin, TX"}]}
WD_DETAIL = {"jobPostingInfo": {"jobDescription": "<p>Summer 2027 internship. Must be a U.S. citizen.</p>",
                                "externalUrl": "https://acme.wd5.myworkdayjobs.com/External/job/x/HW-ASIC_JR2023486",
                                "startDate": "2026-09-01", "jobReqId": "JR2023486"}}
ORACLE_LIST = {"items": [{"TotalJobsCount": 1, "requisitionList": [
    {"Id": "555", "Title": "Analog Design Intern Summer 2027", "PostedDate": "2026-09-01",
     "PrimaryLocation": "Dallas, TX", "ShortDescriptionStr": "ADC design"}]}]}


class SourceTests(unittest.TestCase):
    def test_greenhouse(self):
        http = FakeHttp({"boards-api.greenhouse.io/v1/boards/acme/jobs": GREENHOUSE})
        jobs = build_source(http, {"kind": "greenhouse", "id": "acme", "company": "Acme"}).fetch()
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j.external_id, "123")
        self.assertEqual(j.location, "Austin, TX")
        self.assertIn("Design & test PCBs.", j.description)
        self.assertTrue(j.has_full_description)
        self.assertEqual(j.key, "greenhouse:acme:123")

    def test_lever(self):
        http = FakeHttp({"api.lever.co/v0/postings/acme": LEVER})
        jobs = build_source(http, {"kind": "lever", "id": "acme", "company": "Acme"}).fetch()
        self.assertEqual(jobs[0].title, "Firmware Intern")
        self.assertIn("Write firmware.", jobs[0].description)
        self.assertEqual(jobs[0].posted_at.year, 2026)

    def test_ashby(self):
        http = FakeHttp({"api.ashbyhq.com/posting-api/job-board/acme": ASHBY})
        jobs = build_source(http, {"kind": "ashby", "id": "acme", "company": "Acme"}).fetch()
        self.assertEqual(jobs[0].location, "LA; Torrance, CA")
        self.assertEqual(jobs[0].description, "Antennas")

    def test_smartrecruiters_with_details(self):
        http = FakeHttp({"/postings/744000": SR_DETAIL, "/postings?limit": SR_LIST})
        src = build_source(http, {"kind": "smartrecruiters", "id": "Acme", "company": "Acme"})
        jobs = src.fetch()
        self.assertEqual(jobs[0].location, "Fremont, CA, us")
        self.assertFalse(jobs[0].has_full_description)
        src.fetch_details(jobs[0])
        self.assertIn("Board bring-up", jobs[0].description)
        self.assertTrue(jobs[0].has_full_description)

    def test_workday_with_details(self):
        http = FakeHttp({"/wday/cxs/acme/External/jobs": WD_LIST,
                         "/wday/cxs/acme/External/job/": WD_DETAIL,
                         "acme.wd5.myworkdayjobs.com/External": FakeResponse({}, text="<html>")})
        src = build_source(http, {"kind": "workday", "id": "acme.wd5.myworkdayjobs.com|acme|External", "company": "Acme"})
        jobs = src.fetch()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].external_id, "JR2023486")
        self.assertEqual(jobs[0].url, "https://acme.wd5.myworkdayjobs.com/External/job/US-CA-Santa-Clara/HW-ASIC_JR2023486")
        src.fetch_details(jobs[0])
        self.assertIn("Summer 2027", jobs[0].description)
        self.assertEqual(jobs[0].extra["req_id"], "JR2023486")

    def test_workday_myworkdaysite_urls(self):
        http = FakeHttp({"/wday/cxs/microchiphr/External/jobs": {"total": 0, "jobPostings": []}})
        src = build_source(http, {"kind": "workday", "id": "wd5.myworkdaysite.com|microchiphr|External"})
        self.assertEqual(src.public_base, "https://wd5.myworkdaysite.com/recruiting/microchiphr/External")
        self.assertEqual(src.fetch(), [])

    def test_oracle(self):
        http = FakeHttp({"recruitingCEJobRequisitions?": ORACLE_LIST})
        src = build_source(http, {"kind": "oracle", "id": "edbz.fa.us2.oraclecloud.com|CX", "company": "TI"})
        jobs = src.fetch()
        self.assertEqual(jobs[0].url, "https://edbz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/555")
        self.assertEqual(jobs[0].location, "Dallas, TX")

    def test_listings_json_with_etag_cache(self):
        class Store:
            def __init__(self): self.d = {}
            def get(self, k, default=None): return self.d.get(k, default)
            def set(self, k, v): self.d[k] = v
        payload = [{"id": "1", "company_name": "K2 Space", "title": "Electrical Engineer Intern - Summer 2027",
                    "url": "https://job-boards.greenhouse.io/k2spacecorporation/jobs/5411918008", "active": True,
                    "terms": ["Summer 2027"], "category": "Hardware", "locations": ["LA"], "date_posted": 1756800000,
                    "sponsorship": "Other"},
                   {"id": "2", "company_name": "Old", "title": "x", "url": "https://x", "active": False}]
        calls = {"n": 0}

        def route(method, url, kw):
            calls["n"] += 1
            if kw.get("headers", {}).get("If-None-Match") == "W/abc":
                return FakeResponse(None, status=304, text="")
            return FakeResponse(payload, headers={"ETag": "W/abc"})
        http = FakeHttp({"listings.json": route})
        src = build_source(http, {"kind": "listings-json", "name": "t", "url": "https://h/listings.json"})
        src.store = Store()
        jobs = src.fetch()
        self.assertEqual([j.company for j in jobs], ["K2 Space"])
        self.assertEqual(jobs[0].terms, ["Summer 2027"])
        jobs2 = src.fetch()  # served from the ETag cache
        self.assertEqual(len(jobs2), 1)
        self.assertEqual(calls["n"], 2)

    def test_discovery(self):
        self.assertEqual(board_from_url("https://job-boards.greenhouse.io/k2spacecorporation/jobs/5411918008", "K2")["id"],
                         "k2spacecorporation")
        self.assertEqual(board_from_url("https://boards.greenhouse.io/embed/job_app?for=spacex&token=1")["id"], "spacex")
        self.assertEqual(board_from_url("https://jobs.lever.co/zoox/76845566/apply")["kind"], "lever")
        self.assertEqual(board_from_url("https://jobs.ashbyhq.com/Flock%20Safety/abc")["id"], "Flock Safety")
        self.assertEqual(board_from_url("https://jobs.smartrecruiters.com/WesternDigital/744000140949875")["id"], "WesternDigital")
        wd = board_from_url("https://globalhr.wd5.myworkdayjobs.com/fr-CA/rec_rtx_ext_gateway/job/US-IA/x_01871019")
        self.assertEqual((wd["tenant"], wd["site"]), ("globalhr", "rec_rtx_ext_gateway"))
        wd2 = board_from_url("https://wd3.myworkdaysite.com/recruiting/magna/Magna/job/Troy/x_R00259672")
        self.assertEqual(wd2["id"], "wd3.myworkdaysite.com|magna|Magna")
        orc = board_from_url("https://egup.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/20278957")
        self.assertEqual(orc["id"], "egup.fa.us2.oraclecloud.com|CX")
        self.assertIsNone(board_from_url("https://www.tesla.com/careers/search/job/281634"))
        self.assertIsNone(board_from_url("https://careers.jhuapl.edu/jobs/59901?icims=1"))


if __name__ == "__main__":
    unittest.main()


class WorkdayRepairTests(unittest.TestCase):
    def test_site_repaired_from_tenant_redirect(self):
        class Resp:
            def __init__(self, status, data=None, url=""):
                self.status_code, self._data, self.url, self.text = status, data, url, ""
            def json(self): return self._data
            def raise_for_status(self):
                if self.status_code >= 400: raise RuntimeError(f"HTTP {self.status_code}")

        class Http(FakeHttp):
            def get(self, url, **kw):
                if url == "https://qualcomm.wd5.myworkdayjobs.com/":
                    return Resp(200, url="https://qualcomm.wd5.myworkdayjobs.com/en-US/Qualcomm_Careers")
                return Resp(200, {}, url=url)
            def post(self, url, **kw):
                if "/wday/cxs/qualcomm/External/jobs" in url:
                    return Resp(422, {})
                if "/wday/cxs/qualcomm/Qualcomm_Careers/jobs" in url:
                    return Resp(200, {"total": 1, "jobPostings": [{"title": "RF Intern", "externalPath": "/job/SD/RF-Intern_JR1",
                                                                    "locationsText": "San Diego, CA"}]})
                return Resp(404, {})

        class Store:
            def __init__(self): self.d = {}
            def get(self, k, default=None): return self.d.get(k, default)
            def set(self, k, v): self.d[k] = v
        src = build_source(Http({}), {"kind": "workday", "id": "qualcomm.wd5.myworkdayjobs.com|qualcomm|External", "company": "Qualcomm"})
        src.store = Store()
        jobs = src.fetch()
        self.assertEqual([j.title for j in jobs], ["RF Intern"])
        self.assertEqual(src.site, "Qualcomm_Careers")
        self.assertEqual(jobs[0].url, "https://qualcomm.wd5.myworkdayjobs.com/Qualcomm_Careers/job/SD/RF-Intern_JR1")
        self.assertEqual(src.store.get("workday-site:qualcomm.wd5.myworkdayjobs.com|qualcomm"), "Qualcomm_Careers")


class WorkdayHelpersTests(unittest.TestCase):
    def test_posted_on_parsing(self):
        from datetime import datetime, timezone
        from hwintern.sources.workday import parse_posted_on
        now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_posted_on("Posted Today", now), now)
        self.assertEqual(parse_posted_on("Posted Yesterday", now).day, 2)
        self.assertEqual(parse_posted_on("Posted 3 Days Ago", now).day, 31)
        self.assertEqual(parse_posted_on("Posted 30+ Days Ago", now).month, 8)
        self.assertIsNone(parse_posted_on(""))

    def test_csrf_header_from_cookie(self):
        from hwintern.sources.workday import csrf_headers

        class Jar(dict):
            pass

        class S:
            cookies = Jar(CALYPSO_CSRF_TOKEN="abc")
        self.assertEqual(csrf_headers(S()), {"X-CALYPSO-CSRF-TOKEN": "abc"})

        class S2:
            cookies = Jar()
        self.assertEqual(csrf_headers(S2()), {})
