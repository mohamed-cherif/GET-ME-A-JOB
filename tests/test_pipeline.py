import json
import tempfile
import unittest
from pathlib import Path

from hwintern.config import Config, RunConfig
from hwintern.filters import FilterConfig
from hwintern.judge import LLMConfig, LLMJudge
from hwintern.notify import Notifier
from hwintern.pipeline import Pipeline
from hwintern.store import Store
from tests.fakes import FakeHttp, FakeResponse


class CaptureNotifier(Notifier):
    name = "capture"

    def __init__(self):
        self.batches = []

    def send(self, jobs, heading=""):
        self.batches.append((heading, list(jobs)))


def make_cfg(tmp, companies, aggregators=None, llm=None, **run):
    return Config(run=RunConfig(state_dir=str(tmp), workers=2, initial_max_age_days=None, **run),
                  filters=FilterConfig(), llm=llm or LLMConfig(enabled=False), notifiers=[], companies=companies,
                  aggregators=aggregators or [], base_dir=Path(tmp))


class _Block:
    def __init__(self, text): self.type, self.text = "text", text


class _Resp:
    def __init__(self, text, model="claude-opus-5", stop="end_turn"):
        self.content, self.model, self.stop_reason = [_Block(text)], model, stop


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: returns canned judgments keyed on the posting title."""
    def __init__(self, verdicts):
        self.verdicts, self.calls = verdicts, []
        outer = self

        class _Msgs:
            def create(self, **kw):
                outer.calls.append(kw)
                text = kw["messages"][0]["content"]
                for needle, data in outer.verdicts.items():
                    if needle in text:
                        return _Resp(json.dumps(data))
                return _Resp(json.dumps(outer.verdicts["__default__"]))

        class _Beta:
            messages = _Msgs()
        self.beta, self.messages = _Beta(), _Msgs()


def judgment(**over):
    d = {"is_internship": True, "role_family": "embedded_firmware", "hardware_relevance": 90, "undergrad_eligible": True,
         "eligibility": "ok", "term": "Summer 2027", "fit_score": 88, "verdict": "strong",
         "summary": "Bring up firmware on a flight controller.", "reasons": "embedded work"}
    d.update(over)
    return d


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

    def test_first_run_refuses_without_real_channel(self):
        from hwintern.notify import StdoutNotifier
        with tempfile.TemporaryDirectory() as tmp:
            http = FakeHttp({"boards-api.greenhouse.io/v1/boards/acme/jobs": GH})
            cfg = make_cfg(tmp, [{"kind": "greenhouse", "id": "acme", "company": "Acme"}])
            p = Pipeline(cfg, http=http, notifiers=[StdoutNotifier({}, http)])
            rep = p.run_once()
            self.assertIn("config", rep.errors)
            self.assertTrue(p.store.is_first_run())          # baseline NOT built
            self.assertEqual(p.store.stats()["jobs_seen"], 0)
            # once a real channel exists the same run proceeds normally
            cap = CaptureNotifier()
            rep = Pipeline(cfg, http=http, notifiers=[cap]).run_once()
            self.assertEqual(len(rep.notified), 1)

    def test_notifier_failure_does_not_crash(self):
        class Boom(Notifier):
            name = "boom"
            def __init__(self): pass
            def send(self, jobs, heading=""): raise RuntimeError("down")
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


class DotenvTests(unittest.TestCase):
    def test_env_file_next_to_config_is_loaded(self):
        import os
        from hwintern.config import load_config
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "config.yaml").write_text(
                "notifiers:\n  - type: telegram\n    bot_token: ${HWTEST_TOKEN}\n"
                "  - type: discord\n    webhook_url: ${HWTEST_HOOK}\n", encoding="utf-8")
            (d / ".env").write_text(
                "# comment\r\nHWTEST_TOKEN=123:abc\r\nexport HWTEST_HOOK=\"https://x/y\"  \r\n\r\nbad line\r\n",
                encoding="utf-8")
            os.environ.pop("HWTEST_TOKEN", None); os.environ.pop("HWTEST_HOOK", None)
            try:
                cfg = load_config(d / "config.yaml")
                self.assertEqual(cfg.notifiers[0]["bot_token"], "123:abc")
                self.assertEqual(cfg.notifiers[1]["webhook_url"], "https://x/y")
            finally:
                os.environ.pop("HWTEST_TOKEN", None); os.environ.pop("HWTEST_HOOK", None)


class TierAndDigestTests(unittest.TestCase):
    def test_safety_tier_is_queued_and_flushed_as_digest(self):
        gh = {"jobs": [
            {"id": 1, "title": "Robotics Engineer Intern - Summer 2027", "absolute_url": "https://boards.greenhouse.io/a/jobs/1",
             "location": {"name": "Austin, TX"}, "content": "x", "updated_at": "2026-09-01T00:00:00Z"},
            {"id": 2, "title": "Mechanical Engineering Intern - Fall 2027", "absolute_url": "https://boards.greenhouse.io/a/jobs/2",
             "location": {"name": "Remote"}, "content": "We cannot sponsor visas.", "updated_at": "2026-09-01T00:00:00Z"},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            http = FakeHttp({"boards-api.greenhouse.io/v1/boards/a/jobs": gh})
            cfg = make_cfg(tmp, [{"kind": "greenhouse", "id": "a", "company": "A"}])
            cfg.filters = FilterConfig(accept_other_terms=True, priority_keywords=["robot"], countries_allow=["US", "DE"])
            cfg.run.digest_time = "23:59"   # not due yet
            cap = CaptureNotifier()
            p = Pipeline(cfg, http=http, notifiers=[cap])
            rep = p.run_once()
            self.assertEqual([j.tier for j in rep.notified], ["target"])
            self.assertEqual(len(cap.batches), 1)
            queued = p.store.digest_queue()
            self.assertEqual([q["tier"] for q in queued], ["safety"])
            self.assertIn("location-unknown", queued[0]["flags"])
            self.assertEqual(p.flush_digest(force=True), 1)
            self.assertEqual(cap.batches[1][0][:6], "Digest")
            self.assertEqual(cap.batches[1][1][0].title, "Mechanical Engineering Intern - Fall 2027")
            self.assertEqual(p.store.digest_queue(), [])


class LLMJudgeTests(unittest.TestCase):
    def test_judge_rejects_junk_and_rescores_good_ones(self):
        gh = {"jobs": [
            {"id": 1, "title": "Embedded Firmware Intern - Summer 2027", "absolute_url": "https://boards.greenhouse.io/a/jobs/1",
             "location": {"name": "Austin, TX"}, "content": "Write firmware for drones.", "updated_at": "2026-09-01T00:00:00Z"},
            {"id": 2, "title": "Hardware Engineering Intern - Summer 2027", "absolute_url": "https://boards.greenhouse.io/a/jobs/2",
             "location": {"name": "Austin, TX"}, "content": "You will build dashboards in React for the hardware team.",
             "updated_at": "2026-09-01T00:00:00Z"},
            {"id": 3, "title": "Electrical Engineering Intern - Summer 2027", "absolute_url": "https://boards.greenhouse.io/a/jobs/3",
             "location": {"name": "Austin, TX"}, "content": "Must be a US citizen.", "updated_at": "2026-09-01T00:00:00Z"},
        ]}
        fake = FakeAnthropic({
            "Embedded Firmware Intern": judgment(),
            "Hardware Engineering Intern": judgment(role_family="software_only", hardware_relevance=10, fit_score=20,
                                                    verdict="reject", summary="React dashboards"),
            "Electrical Engineering Intern": judgment(eligibility="citizenship_or_clearance_required", verdict="reject"),
            "__default__": judgment(),
        })
        with tempfile.TemporaryDirectory() as tmp:
            http = FakeHttp({"boards-api.greenhouse.io/v1/boards/a/jobs": gh})
            cfg = make_cfg(tmp, [{"kind": "greenhouse", "id": "a", "company": "A"}], llm=LLMConfig(enabled=True))
            cfg.filters = FilterConfig(priority_keywords=["embedded"], exclude_flags=[])  # let the judge catch the citizenship one
            store = Store(Path(tmp) / "db.sqlite3")
            judge = LLMJudge(cfg.llm, store, client=fake)
            cap = CaptureNotifier()
            p = Pipeline(cfg, store=store, http=http, notifiers=[cap], judge=judge)
            rep = p.run_once()
            self.assertEqual(rep.llm_judged, 3)
            self.assertEqual(rep.llm_rejected, 2)
            self.assertEqual([j.title for j in rep.notified], ["Embedded Firmware Intern - Summer 2027"])
            j = rep.notified[0]
            self.assertEqual(j.summary, "Bring up firmware on a flight controller.")
            self.assertIn("llm:strong", j.flags)
            self.assertEqual(j.tier, "target")
            # structured output + effort were requested, judgment cached
            kw = fake.calls[0]
            self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
            self.assertEqual(kw["output_config"]["effort"], "low")
            self.assertIn("Embedded", kw["messages"][0]["content"])
            self.assertIsNotNone(store.get("judge:greenhouse:a:1"))

    def test_judge_unavailable_falls_back_to_keywords(self):
        gh = {"jobs": [{"id": 1, "title": "Embedded Firmware Intern - Summer 2027", "absolute_url": "https://boards.greenhouse.io/a/jobs/1",
                        "location": {"name": "Austin, TX"}, "content": "x", "updated_at": "2026-09-01T00:00:00Z"}]}
        with tempfile.TemporaryDirectory() as tmp:
            http = FakeHttp({"boards-api.greenhouse.io/v1/boards/a/jobs": gh})
            cfg = make_cfg(tmp, [{"kind": "greenhouse", "id": "a", "company": "A"}], llm=LLMConfig(enabled=True))
            judge = LLMJudge(cfg.llm, None, client=None)
            judge.disabled_reason = "no key"
            cap = CaptureNotifier()
            rep = Pipeline(cfg, http=http, notifiers=[cap], judge=judge).run_once()
            self.assertEqual(len(rep.notified), 1)
            self.assertIn("llm-unjudged", rep.notified[0].flags)

    def test_feed_hit_is_enriched_before_judging(self):
        from hwintern.sources.enrich import fetch_description
        http = FakeHttp({"boards-api.greenhouse.io/v1/boards/k2spacecorporation/jobs/5411918008":
                         {"content": "&lt;p&gt;Design power boards for satellites.&lt;/p&gt;"},
                         "api.lever.co/v0/postings/zoox/abc": {"descriptionPlain": "Firmware for sensors.", "lists": []}})
        self.assertEqual(fetch_description(http, "https://job-boards.greenhouse.io/k2spacecorporation/jobs/5411918008"),
                         "Design power boards for satellites.")
        self.assertEqual(fetch_description(http, "https://jobs.lever.co/zoox/abc/apply"), "Firmware for sensors.")
        self.assertEqual(fetch_description(http, "https://careers.example.com/jobs/1"), "")


class _HttpResp:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._data, self.text, self.headers = status, data, text or json.dumps(data or {}), {}

    def json(self):
        return self._data


class FreeProviderTests(unittest.TestCase):
    def test_openai_compatible_backend_with_json_fences_and_drift(self):
        import os
        from hwintern.judge import LLMJudge, LLMConfig
        calls = []

        class Sess:
            def post(self, url, json=None, headers=None, timeout=None):
                calls.append((url, json, headers))
                reply = "```json\n" + __import__("json").dumps({
                    "is_internship": "yes", "role_family": "robotics_controls", "hardware_relevance": "88",
                    "undergrad_eligible": True, "eligibility": "ok", "term": "Summer 2027", "fit_score": 81.0,
                    "verdict": "GOOD", "summary": "Build drone flight controllers.", "reasons": "robotics"}) + "\n```"
                return _HttpResp(200, {"choices": [{"message": {"content": reply}}], "model": "llama-3.3-70b-versatile"})
        os.environ["GROQ_API_KEY"] = "gsk_test"
        try:
            judge = LLMJudge(LLMConfig(enabled=True, provider="auto", concurrency=1), http=Sess())
            self.assertTrue(judge.available)
            self.assertEqual(judge.describe(), "groq / llama-3.3-70b-versatile")
            from hwintern.models import Job
            job = Job(source="t", company="Skydio", title="Robotics Intern", url="https://x/1", external_id="1",
                      description="Flight controller firmware for drones.")
            v = judge.judge(job)
            self.assertTrue(v.ok, v.error)
            self.assertEqual(v.data["verdict"], "good")
            self.assertEqual(v.data["hardware_relevance"], 88)
            self.assertIs(v.data["is_internship"], True)
            url, payload, headers = calls[0]
            self.assertEqual(url, "https://api.groq.com/openai/v1/chat/completions")
            self.assertEqual(headers["Authorization"], "Bearer gsk_test")
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            self.assertIn("Skydio", payload["messages"][1]["content"])
        finally:
            os.environ.pop("GROQ_API_KEY", None)

    def test_json_mode_fallback_and_auth_error(self):
        import os
        from hwintern.judge import LLMJudge, LLMConfig, JudgeError
        from hwintern.models import Job
        state = {"n": 0}

        class Sess:
            def post(self, url, json=None, headers=None, timeout=None):
                state["n"] += 1
                if "response_format" in (json or {}):
                    return _HttpResp(400, {"error": "response_format not supported"}, text="response_format not supported")
                return _HttpResp(200, {"choices": [{"message": {"content": '{"fit_score": 30, "verdict": "reject", "is_internship": true, "hardware_relevance": 10, "undergrad_eligible": true, "eligibility": "ok", "term": "?", "summary": "s", "reasons": "r", "role_family": "software_only"}'}}]})
        os.environ["OLLAMA_HOST"] = "http://localhost:11434"
        try:
            judge = LLMJudge(LLMConfig(enabled=True, provider="ollama", model="qwen2.5:7b"), http=Sess())
            job = Job(source="t", company="A", title="Web Intern", url="https://x/2", external_id="2", description="React.")
            v = judge.judge(job)
            self.assertTrue(v.ok)
            self.assertEqual(v.data["verdict"], "reject")
            self.assertEqual(state["n"], 2)   # retried once without JSON mode
        finally:
            os.environ.pop("OLLAMA_HOST", None)

        class Bad:
            def post(self, url, json=None, headers=None, timeout=None):
                return _HttpResp(401, {"error": "invalid api key"}, text="invalid api key")
        judge = LLMJudge(LLMConfig(enabled=True, provider="groq", api_key="bad"), http=Bad())
        v = judge.judge(Job(source="t", company="A", title="x", url="https://x/3", external_id="3"))
        self.assertFalse(v.ok)
        self.assertFalse(judge.available)
        self.assertIn("rejected", judge.disabled_reason)

    def test_no_credentials_means_unavailable(self):
        import os
        from hwintern.judge import LLMJudge, LLMConfig
        for k in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY",
                  "MISTRAL_API_KEY", "OLLAMA_HOST"):
            os.environ.pop(k, None)
        judge = LLMJudge(LLMConfig(enabled=True, provider="auto"))
        self.assertFalse(judge.available)
        self.assertIn("no LLM credentials", judge.disabled_reason)


class ModelRetirementTests(unittest.TestCase):
    def test_follows_provider_model_hint(self):
        from hwintern.judge import LLMJudge, LLMConfig, _suggested_model
        from hwintern.models import Job
        self.assertEqual(_suggested_model('This model models/gemini-2.5-flash is no longer available to new users. '
                                          'Please update your code to use models/gemini-3.6-flash for the latest features'),
                         "gemini-3.6-flash")
        seen = []

        class Sess:
            def post(self, url, json=None, headers=None, timeout=None):
                seen.append(json["model"])
                if json["model"] == "gemini-2.5-flash":
                    return _HttpResp(404, {"error": {"code": 404, "message": "This model models/gemini-2.5-flash is no longer "
                                           "available to new users. Please update your code to use models/gemini-3.6-flash for x"}},
                                     text="This model models/gemini-2.5-flash is no longer available to new users. Please update "
                                          "your code to use models/gemini-3.6-flash for the latest features")
                return _HttpResp(200, {"choices": [{"message": {"content": json_dumps_judgment()}}], "model": json["model"]})
        judge = LLMJudge(LLMConfig(enabled=True, provider="gemini", model="gemini-2.5-flash", api_key="k"), http=Sess())
        v = judge.judge(Job(source="t", company="A", title="Embedded Intern", url="https://x/9", external_id="9", description="fw"))
        self.assertTrue(v.ok, v.error)
        self.assertEqual(seen, ["gemini-2.5-flash", "gemini-3.6-flash"])
        self.assertEqual(judge.describe(), "gemini / gemini-3.6-flash")


def json_dumps_judgment():
    return json.dumps(judgment())


class TelegramCommandTests(unittest.TestCase):
    def test_status_command_is_answered(self):
        from hwintern.notify import build_notifiers
        sent = []

        def route(method, url, kw):
            if "getUpdates" in url:
                return {"ok": True, "result": [
                    {"update_id": 7, "message": {"chat": {"id": 42}, "text": "hello"}},
                    {"update_id": 8, "message": {"chat": {"id": 42}, "text": "/status@mybot"}}]}
            if "sendMessage" in url:
                sent.append(kw.get("json"))
                return {"ok": True}
            return FakeResponse({}, status=404)
        http = FakeHttp({"api.telegram.org": route, "boards-api.greenhouse.io/v1/boards/acme/jobs": GH})
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(tmp, [{"kind": "greenhouse", "id": "acme", "company": "Acme"}])
            store = Store(Path(tmp) / "db.sqlite3")
            (tg,) = build_notifiers([{"type": "telegram", "bot_token": "t"}], http, store)
            p = Pipeline(cfg, store=store, http=http, notifiers=[tg])
            p.run_once()
            p.poll_commands()
            replies = [m for m in sent if "watcher alive" in m["text"]]
            self.assertEqual(len(replies), 1)
            self.assertTrue(replies[0]["disable_notification"])
            self.assertIn("1 cycle", replies[0]["text"])
            self.assertEqual(store.get("telegram:update_offset"), "9")
            self.assertIn("/status", p.handle_command("help"))
            self.assertIn("Acme", p.handle_command("last"))
