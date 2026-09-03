import tempfile
import unittest
from pathlib import Path

from hwintern.config import Config, RunConfig
from hwintern.filters import FilterConfig
from hwintern.notify import Notifier
from hwintern.pipeline import Pipeline
from hwintern.store import Store
from tests.fakes import FakeHttp, FakeResponse


class CaptureNotifier(Notifier):
    name = "capture"

    def __init__(self):
        self.batches = []

    def send(self, jobs):
        self.batches.append(list(jobs))


def make_cfg(tmp, companies, aggregators=None, **run):
    return Config(run=RunConfig(state_dir=str(tmp), workers=2, initial_max_age_days=None, **run),
                  filters=FilterConfig(), notifiers=[], companies=companies, aggregators=aggregators or [],
                  base_dir=Path(tmp))


GH = {"jobs": [
    {"id": 1, "title": "Hardware Engineering Intern - Summer 2027", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
     "location": {"name": "Austin, TX"}, "content": "PCB work", "updated_at": "2026-09-01T00:00:00Z"},
    {"id": 2, "title": "Software Engineer Intern - Summer 2027", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
     "location": {"name": "Austin, TX"}, "content": "Web apps", "updated_at": "2026-09-01T00:00:00Z"},
]}
WD_LIST = {"total": 1, "jobPostings": [{"title": "Engineering Intern", "externalPath": "/job/Austin/Eng-Intern_JR1",
                                         "locationsText": "Austin, TX"}]}
WD_DETAIL = {"jobPostingInfo": {"jobDescription": "Summer 2027. You will do PCB layout, firmware bring-up and analog debug.",
                                "startDate": "2026-09-01"}}
FEED = [{"id": "f1", "company_name": "Zoox", "title": "Firmware Engineer Intern", "active": True, "terms": ["Summer 2027"],
         "category": "Hardware", "locations": ["San Mateo, CA"], "date_posted": 1756800000,
         "url": "https://jobs.lever.co/zoox/abc/apply"},
        {"id": "f2", "company_name": "Acme", "title": "Hardware Engineering Intern - Summer 2027", "active": True,
         "terms": ["Summer 2027"], "category": "Hardware", "locations": ["Austin, TX"],
         "url": "https://boards.greenhouse.io/acme/jobs/1?gh_src=feed"}]  # same job as GH id 1


class PipelineTests(unittest.TestCase):
    def test_end_to_end_dedup_details_discovery_and_incremental(self):
        with tempfile.TemporaryDirectory() as tmp:
            routes = {"boards-api.greenhouse.io/v1/boards/acme/jobs": GH,
                      "/wday/cxs/acme/External/jobs": WD_LIST,
                      "/wday/cxs/acme/External/job/": WD_DETAIL,
                      "acme.wd5.myworkdayjobs.com/External": FakeResponse({}, text="<html>"),
                      "listings.json": FEED}
            http = FakeHttp(routes)
            cfg = make_cfg(tmp, [{"kind": "greenhouse", "id": "acme", "company": "Acme"},
                                 {"kind": "workday", "id": "acme.wd5.myworkdayjobs.com|acme|External", "company": "Acme"}],
                           aggregators=[{"kind": "listings-json", "name": "feed", "url": "https://h/listings.json"}])
            cap = CaptureNotifier()
            p = Pipeline(cfg, http=http, notifiers=[cap])
            rep = p.run_once()
            self.assertEqual(rep.sources_failed, 0, rep.errors)
            titles = sorted((j.company, j.title) for j in rep.notified)
            self.assertEqual(titles, [("Acme", "Engineering Intern"),
                                      ("Acme", "Hardware Engineering Intern - Summer 2027"),
                                      ("Zoox", "Firmware Engineer Intern")])
            self.assertEqual(rep.details_fetched, 1)          # the ambiguous Workday title
            self.assertEqual(len(cap.batches), 1)
            # the Zoox lever board was discovered from the feed
            self.assertIn(("lever", "zoox"), {(b["kind"], b["ident"]) for b in p.store.boards()})
            # second run: nothing new, the discovered board is now polled (and fails harmlessly with 404)
            rep2 = p.run_once()
            self.assertEqual(rep2.jobs_new, 0)
            self.assertEqual(len(cap.batches), 1)
            self.assertEqual(rep2.sources_total, 4)
            self.assertEqual(rep2.sources_failed, 1)
            self.assertEqual(p.store.stats()["jobs_matched"], 3)

    def test_first_run_age_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = {"jobs": [{"id": 9, "title": "FPGA Intern Summer 2027", "absolute_url": "https://boards.greenhouse.io/a/jobs/9",
                             "location": {"name": "X"}, "content": "x", "first_published": "2024-01-01T00:00:00Z"}]}
            http = FakeHttp({"boards-api.greenhouse.io/v1/boards/a/jobs": old})
            cfg = make_cfg(tmp, [{"kind": "greenhouse", "id": "a", "company": "A"}])
            cfg.run.initial_max_age_days = 30
            cap = CaptureNotifier()
            rep = Pipeline(cfg, http=http, notifiers=[cap]).run_once()
            self.assertEqual(rep.jobs_matched, 1)
            self.assertEqual(cap.batches, [])

    def test_notifier_failure_does_not_crash(self):
        class Boom(Notifier):
            name = "boom"
            def __init__(self): pass
            def send(self, jobs): raise RuntimeError("down")
        with tempfile.TemporaryDirectory() as tmp:
            http = FakeHttp({"boards-api.greenhouse.io/v1/boards/acme/jobs": GH})
            cfg = make_cfg(tmp, [{"kind": "greenhouse", "id": "acme", "company": "Acme"}])
            rep = Pipeline(cfg, http=http, notifiers=[Boom()]).run_once()
            self.assertEqual(len(rep.notified), 1)


if __name__ == "__main__":
    unittest.main()


class TelegramTests(unittest.TestCase):
    def test_chat_id_autodetected_and_cached(self):
        from hwintern.notify import build_notifiers
        from hwintern.store import Store
        sent = []

        def route(method, url, kw):
            if "getUpdates" in url:
                return {"ok": True, "result": [{"update_id": 1, "message": {"chat": {"id": 42, "username": "mo"}, "text": "/start"}}]}
            if "sendMessage" in url:
                sent.append(kw.get("json"))
                return {"ok": True}
            return FakeResponse({}, status=404)
        http = FakeHttp({"api.telegram.org": route})
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "db.sqlite3")
            (n,) = build_notifiers([{"type": "telegram", "bot_token": "t", "chat_id": ""}], http, store)
            n.send_text("hello")
            self.assertEqual(sent[0]["chat_id"], "42")
            self.assertEqual(store.get("telegram:chat_id"), "42")
            n.send_text("again")
            self.assertEqual(sum(1 for m, u in http.calls if "getUpdates" in u), 1)  # cached

    def test_missing_chat_id_gives_clear_error(self):
        from hwintern.notify import build_notifiers
        http = FakeHttp({"getUpdates": {"ok": True, "result": []}})
        (n,) = build_notifiers([{"type": "telegram", "bot_token": "t"}], http)
        with self.assertRaises(RuntimeError) as ctx:
            n.send_text("x")
        self.assertIn("press Start", str(ctx.exception))
