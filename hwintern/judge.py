"""LLM judge: reads the actual posting (title + description) and decides whether it truly fits.

The keyword classifier is a cheap pre-filter; every posting it accepts is then judged by Claude
with a structured-output schema. The judge can reject junk the keywords let through, and its fit
score drives the tier. Judgments are cached in the state database so a posting is never paid for twice.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import Job
from .textutil import truncate

log = logging.getLogger(__name__)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_internship": {"type": "boolean",
                          "description": "True for internships, co-ops, student worker / working-student roles."},
        "role_family": {"type": "string", "enum": [
            "embedded_firmware", "electrical_pcb", "robotics_controls", "silicon_asic_fpga",
            "rf_analog_photonics", "mechanical", "manufacturing_test", "software_only", "data_ml_only",
            "non_engineering", "other"]},
        "hardware_relevance": {"type": "integer", "minimum": 0, "maximum": 100,
                               "description": "How much of the day-to-day work is hardware / ECE / robotics engineering."},
        "undergrad_eligible": {"type": "boolean",
                               "description": "A bachelor's student (2nd/3rd year) could realistically be hired."},
        "eligibility": {"type": "string",
                        "enum": ["ok", "citizenship_or_clearance_required", "no_sponsorship_stated", "unclear"]},
        "term": {"type": "string", "description": "Season and year the posting targets, or 'unstated'."},
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100,
                      "description": "Overall fit for this candidate: 85+ dream role, 65-84 good, 45-64 weak, <45 junk."},
        "verdict": {"type": "string", "enum": ["strong", "good", "weak", "reject"]},
        "summary": {"type": "string", "description": "One line (max 140 chars): what the intern would actually do."},
        "reasons": {"type": "string", "description": "Max 300 chars: why this verdict, incl. any eligibility concern."},
    },
    "required": ["is_internship", "role_family", "hardware_relevance", "undergrad_eligible", "eligibility",
                 "term", "fit_score", "verdict", "summary", "reasons"],
    "additionalProperties": False,
}

SYSTEM_TEMPLATE = """You screen internship postings for one specific candidate. You will receive a posting and must
judge whether it is genuinely worth their time. Be strict: keyword matches are not enough, the actual
work must fit. Only the posting content counts; do not assume things it does not say.

CANDIDATE PROFILE
{profile}

RULES
- Reject anything that is not an internship / co-op / student role (full-time, new grad, contractor, fellowship for PhDs).
- Reject roles whose day-to-day work is software-only, web, data/ML platform, IT, business, sales, HR, finance,
  writing, consulting or analyst work, even when the company or team is a hardware company.
- Roles that are embedded/firmware, electrical/PCB, robotics, controls, avionics, drones/UAV, maritime/ship
  systems, silicon/ASIC/FPGA, RF/analog/photonics, sensors, perception for robots, or hands-on mechanical /
  manufacturing hardware are relevant. Embedded-software or robotics-software roles count as relevant.
- eligibility = citizenship_or_clearance_required when the posting requires US citizenship, permanent residency /
  green card, a security clearance, or ITAR "US person" status. eligibility = no_sponsorship_stated when it only
  says it will not sponsor a visa (still acceptable for a CPT internship). Otherwise ok / unclear.
- undergrad_eligible = false only when the posting clearly requires a Master's/PhD or graduate enrollment.
- fit_score weighs: relevance of the work to the candidate's interests and skills (most important), the term,
  location, and eligibility. verdict: strong (85+), good (65-84), weak (45-64), reject (<45 or ineligible).
Return only the JSON object."""

DEFAULT_PROFILE = """Undergraduate in Electrical & Computer Engineering + Computer Science (bachelor's, graduating May 2029;
will be a rising junior in Summer 2027), certificate in Robotics & Automation. Hands-on with embedded systems
(ESP32, Arduino, Raspberry Pi, C/C++), PCB design (KiCad), sensor integration, forward/inverse kinematics and
robotic-arm control, computer vision (YOLO, PyTorch), CAD and fabrication (SolidWorks, 3D printing, CNC),
exoskeleton research, an autonomous UUV, a phone-controlled robot platform.
Wants: hardware / ECE internships, especially robotics, avionics, drones, ships/maritime systems,
embedded/firmware, electrical/PCB, controls, perception. Mechanical or manufacturing hardware roles are fine.
Not interested in pure software, web, data, ML-platform, IT or business roles.
Constraints: international student (F-1; CPT covers internships, needs sponsorship for full-time later);
cannot take roles requiring US citizenship, a green card or a clearance; undergraduate (no PhD/Master's-only);
locations US, Canada, UK, Italy, France, Switzerland, Germany, Spain; Summer 2027 preferred, other terms ok."""


@dataclass
class LLMConfig:
    enabled: bool = True
    model: str = "claude-opus-5"
    effort: str = "low"
    max_calls_per_cycle: int = 120
    max_description_chars: int = 6000
    min_relevance: int = 40          # hardware_relevance below this -> reject
    reject_verdicts: list[str] = field(default_factory=lambda: ["reject"])
    keyword_weight: float = 0.25     # final score = (1-w)*llm_fit + w*keyword_score
    fallbacks: bool = True           # server-side refusal fallbacks (opt-in beta)
    profile: str = DEFAULT_PROFILE

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "LLMConfig":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Judgment:
    ok: bool                      # a judgment was obtained
    data: dict = field(default_factory=dict)
    error: str = ""

    @property
    def rejected(self) -> bool:
        return bool(self.data) and self.data.get("verdict") == "reject"


def build_user_message(job: Job, max_chars: int) -> str:
    desc = job.description.strip() if job.description else ""
    if not desc:
        desc = "(no description available - judge from the title, company and location only, and lean towards 'weak' rather than 'strong')"
    meta = []
    if job.terms:
        meta.append(f"Feed-reported term(s): {', '.join(job.terms)}")
    if job.sponsorship and job.sponsorship != "Other":
        meta.append(f"Feed-reported sponsorship: {job.sponsorship}")
    if job.extra.get("department"):
        meta.append(f"Department: {job.extra['department']}")
    return (f"POSTING\nCompany: {job.company}\nTitle: {job.title}\nLocation: {job.location or 'not stated'}\n"
            + ("\n".join(meta) + "\n" if meta else "")
            + f"URL: {job.url}\n\nDESCRIPTION\n{truncate(desc, max_chars)}")


class LLMJudge:
    def __init__(self, cfg: LLMConfig, store=None, client: Any = None):
        self.cfg = cfg
        self.store = store
        self._client = client
        self.calls = 0
        self.disabled_reason = ""
        self._use_fallbacks = cfg.fallbacks
        self.system = [{"type": "text", "text": SYSTEM_TEMPLATE.format(profile=cfg.profile.strip()),
                        "cache_control": {"type": "ephemeral"}}]

    # -- client -------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                self.disabled_reason = "the 'anthropic' package is not installed (pip install anthropic)"
                return None
            try:
                self._client = anthropic.Anthropic(max_retries=2, timeout=60.0)
            except Exception as exc:  # noqa: BLE001  (no API key)
                self.disabled_reason = f"no Anthropic credentials: {exc}"
                return None
        return self._client

    @property
    def available(self) -> bool:
        return self.cfg.enabled and not self.disabled_reason and self.client is not None

    # -- cache --------------------------------------------------------------
    def cached(self, job: Job) -> Optional[dict]:
        if self.store is None:
            return None
        raw = self.store.get(f"judge:{job.key}")
        return json.loads(raw) if raw else None

    def remember(self, job: Job, data: dict) -> None:
        if self.store is not None:
            self.store.set(f"judge:{job.key}", json.dumps(data))

    # -- API call -----------------------------------------------------------
    def _request(self, user_text: str):
        kwargs: dict[str, Any] = dict(
            model=self.cfg.model, max_tokens=800, system=self.system,
            messages=[{"role": "user", "content": user_text}],
            output_config={"effort": self.cfg.effort, "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        )
        if self._use_fallbacks:
            return self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        return self.client.messages.create(**kwargs)

    def judge(self, job: Job, keyword_score: int = 0) -> Judgment:
        cached = self.cached(job)
        if cached:
            return Judgment(True, cached)
        if not self.available:
            return Judgment(False, error=self.disabled_reason or "judge disabled")
        if self.calls >= self.cfg.max_calls_per_cycle:
            return Judgment(False, error="per-cycle budget exhausted")
        import anthropic
        user_text = build_user_message(job, self.cfg.max_description_chars)
        for attempt in range(2):
            try:
                self.calls += 1
                resp = self._request(user_text)
                break
            except anthropic.AuthenticationError as exc:
                self.disabled_reason = f"Anthropic API key rejected: {exc.message}"
                log.error("LLM judge disabled: %s", self.disabled_reason)
                return Judgment(False, error=self.disabled_reason)
            except anthropic.BadRequestError as exc:
                msg = str(getattr(exc, "message", exc))
                if self._use_fallbacks and ("fallback" in msg.lower() or "beta" in msg.lower()):
                    self._use_fallbacks = False   # this account/model does not accept the fallback beta; retry plain
                    continue
                log.warning("LLM judge bad request for %s: %s", job.url, msg)
                return Judgment(False, error=f"bad request: {msg[:200]}")
            except anthropic.RateLimitError as exc:
                wait = float((exc.response.headers.get("retry-after") if getattr(exc, "response", None) else None) or 20)
                time.sleep(min(wait, 60))
                continue
            except anthropic.APIStatusError as exc:
                log.warning("LLM judge API error %s for %s", exc.status_code, job.url)
                return Judgment(False, error=f"api error {exc.status_code}")
            except anthropic.APIConnectionError as exc:
                log.warning("LLM judge connection error: %s", exc)
                return Judgment(False, error="connection error")
        else:
            return Judgment(False, error="rate limited")

        if getattr(resp, "stop_reason", None) == "refusal":
            return Judgment(False, error="refused")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("LLM judge returned non-JSON for %s: %r", job.url, text[:120])
            return Judgment(False, error="unparseable response")
        data["model"] = getattr(resp, "model", self.cfg.model)
        self.remember(job, data)
        return Judgment(True, data)

    # -- apply to a job -------------------------------------------------------
    def apply(self, job: Job, judgment: Judgment, tier_for) -> tuple[bool, str]:
        """Merge a judgment into the job. Returns (accepted, reason)."""
        if not judgment.ok:
            if "llm-unjudged" not in job.flags:
                job.flags.append("llm-unjudged")
            return True, "ok"
        d = judgment.data
        job.extra["llm"] = d
        job.summary = d.get("summary") or ""
        if not d.get("is_internship", True):
            return False, "llm:not-internship"
        if d.get("verdict") in self.cfg.reject_verdicts:
            return False, f"llm:reject:{d.get('role_family')}"
        if int(d.get("hardware_relevance", 100)) < self.cfg.min_relevance:
            return False, f"llm:low-relevance:{d.get('role_family')}"
        if d.get("eligibility") == "citizenship_or_clearance_required":
            if "citizenship-required" not in job.flags:
                job.flags.append("citizenship-required")
            return False, "llm:citizenship-required"
        if d.get("eligibility") == "no_sponsorship_stated" and "no-sponsorship" not in job.flags:
            job.flags.append("no-sponsorship")
        if not d.get("undergrad_eligible", True) and "grad-only" not in job.flags:
            job.flags.append("grad-only")
        llm_fit = int(d.get("fit_score", 0))
        w = self.cfg.keyword_weight
        job.score = int(round((1 - w) * llm_fit + w * job.score))
        if "grad-only" in job.flags:
            job.score = min(job.score, 50)
        job.tier = tier_for(job.score)
        job.flags = [f for f in job.flags if f != "llm-unjudged"]
        job.flags.append(f"llm:{d.get('verdict')}")
        return True, "ok"
