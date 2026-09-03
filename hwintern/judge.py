"""LLM judge: reads the actual posting (title + description) and decides whether it truly fits.

The keyword classifier is a cheap pre-filter; every posting it accepts is then judged by Claude
with a structured-output schema. The judge can reject junk the keywords let through, and its fit
score drives the tier. Judgments are cached in the state database so a posting is never paid for twice.
"""
from __future__ import annotations

import json
import logging
import re
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


# Presets for OpenAI-compatible providers. base_url, default model, environment variable holding the key.
# Model names change; override with LLM_MODEL / llm.model if a preset's default is retired.
PROVIDERS: dict[str, dict] = {
    "anthropic":  {"key_env": "ANTHROPIC_API_KEY", "model": "claude-opus-5"},
    "groq":       {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
                   "model": "llama-3.3-70b-versatile"},
    "gemini":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GEMINI_API_KEY",
                   "model": "gemini-3.6-flash"},
    "cerebras":   {"base_url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY", "model": "llama-3.3-70b"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
                   "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "mistral":    {"base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY", "model": "mistral-small-latest"},
    "ollama":     {"base_url": "http://localhost:11434/v1", "key_env": "", "model": "qwen2.5:7b"},
    "openai_compatible": {"base_url": "", "key_env": "LLM_API_KEY", "model": ""},
}
AUTO_ORDER = ["anthropic", "groq", "gemini", "cerebras", "openrouter", "mistral", "ollama"]


@dataclass
class LLMConfig:
    enabled: bool = True
    provider: str = "auto"           # auto | anthropic | groq | gemini | cerebras | openrouter | mistral | ollama | openai_compatible
    providers: list[str] = field(default_factory=list)   # explicit failover order, e.g. [groq, gemini]; empty = every provider with a key
    model: str = ""                  # empty = provider default
    base_url: str = ""               # only for openai_compatible / to override a preset
    api_key: str = ""                # only for openai_compatible (or to override the env var)
    effort: str = "low"
    temperature: float = 0.0
    concurrency: int = 4             # parallel judge calls (free tiers: keep low)
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


class JudgeError(Exception):
    """kind: 'auth' (disable the judge), 'ratelimit' (retry later), 'transient', 'bad_request', 'refused'."""

    def __init__(self, kind: str, msg: str = ""):
        super().__init__(msg or kind)
        self.kind = kind


def _has_credentials(name: str, cfg: LLMConfig) -> bool:
    import os
    preset = PROVIDERS.get(name)
    if not preset:
        return False
    if name == "ollama":
        return bool(os.environ.get("OLLAMA_HOST") or cfg.base_url or _ollama_alive(""))
    if name == "openai_compatible":
        return bool(cfg.base_url and (cfg.api_key or os.environ.get("LLM_API_KEY")))
    return bool(cfg.api_key or (preset["key_env"] and os.environ.get(preset["key_env"])))


def candidate_providers(cfg: LLMConfig) -> list[str]:
    """Providers to try, in failover order."""
    if cfg.providers:
        return [p.lower() for p in cfg.providers if _has_credentials(p.lower(), cfg)]
    name = (cfg.provider or "auto").lower()
    if name != "auto":
        return [name]
    return [c for c in AUTO_ORDER if _has_credentials(c, cfg)]


def resolve_provider(cfg: LLMConfig, name: str = "") -> tuple[str, str, str, str]:
    """Return (provider, base_url, model, api_key) or raise JudgeError('auth')."""
    import os
    name = (name or cfg.provider or "auto").lower()
    if name == "auto":
        cands = candidate_providers(cfg)
        if not cands:
            raise JudgeError("auth", "no LLM credentials found: set GROQ_API_KEY, GEMINI_API_KEY, CEREBRAS_API_KEY, "
                                     "OPENROUTER_API_KEY, MISTRAL_API_KEY, ANTHROPIC_API_KEY, or run Ollama locally")
        name = cands[0]
    preset = PROVIDERS.get(name)
    if preset is None:
        raise JudgeError("auth", f"unknown llm.provider {name!r}")
    base_url = cfg.base_url or preset.get("base_url", "")
    if name == "ollama":
        import os as _os
        host = _os.environ.get("OLLAMA_HOST")
        if host and not cfg.base_url:
            base_url = host.rstrip("/") + ("" if host.rstrip("/").endswith("/v1") else "/v1")
    # llm.model applies only to a single, explicitly chosen provider; in failover mode each preset keeps its own
    model = (cfg.model if (cfg.provider or "auto").lower() == name else "") or preset["model"]
    api_key = cfg.api_key or (os.environ.get(preset["key_env"]) if preset["key_env"] else "") or ""
    if name == "ollama" and not api_key:
        api_key = "ollama"
    if not api_key:
        raise JudgeError("auth", f"{name}: no API key (set {preset['key_env'] or 'llm.api_key'})")
    if name == "openai_compatible" and not (base_url and model):
        raise JudgeError("auth", "openai_compatible needs llm.base_url and llm.model")
    return name, base_url, model, api_key


_MODEL_HINT_RE = re.compile(r"(?:use|try|switch to)\s+(?:models/)?([A-Za-z0-9][A-Za-z0-9._:/-]{2,})", re.I)


def _suggested_model(text: str) -> str:
    m = _MODEL_HINT_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).rstrip(".,;:")


def _ollama_alive(base_url: str = "") -> bool:
    try:
        import requests
        url = (base_url or "http://localhost:11434/v1").replace("/v1", "") + "/api/tags"
        return requests.get(url, timeout=1.5).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model reply, tolerating code fences and chatter."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def normalize_judgment(d: dict) -> dict:
    """Coerce a loosely-typed reply (small open models drift) into the schema."""
    out = {str(k).strip().lower(): v for k, v in d.items()}
    words = {"very high": 92, "high": 85, "strong": 85, "medium": 55, "moderate": 55, "good": 70, "low": 25,
             "weak": 40, "none": 0, "very low": 10}
    for k in ("hardware_relevance", "fit_score"):
        v = out.get(k, 0)
        sv = str(v).strip().rstrip("%").strip()
        try:
            out[k] = max(0, min(100, int(float(sv))))
        except (TypeError, ValueError):
            out[k] = words.get(sv.lower(), 50)
    out["role_family"] = str(out.get("role_family") or "other").strip().lower().replace(" ", "_").replace("/", "_")
    out["eligibility"] = str(out.get("eligibility") or "unclear").strip().lower()
    for k in ("is_internship", "undergrad_eligible"):
        v = out.get(k, True)
        out[k] = v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1")
    verdict = str(out.get("verdict", "")).lower()
    if verdict not in ("strong", "good", "weak", "reject"):
        f = out["fit_score"]
        verdict = "strong" if f >= 85 else "good" if f >= 65 else "weak" if f >= 45 else "reject"
    out["verdict"] = verdict
    if out.get("eligibility") not in ("ok", "citizenship_or_clearance_required", "no_sponsorship_stated", "unclear"):
        out["eligibility"] = "unclear"
    out["summary"] = str(out.get("summary") or "")[:200]
    out["reasons"] = str(out.get("reasons") or "")[:400]
    out["term"] = str(out.get("term") or "unstated")
    return out


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, cfg: LLMConfig, system_text: str, client: Any = None):
        self.cfg = cfg
        self._client = client
        self._use_fallbacks = cfg.fallbacks
        self.model = cfg.model or PROVIDERS["anthropic"]["model"]
        self.system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise JudgeError("auth", "the 'anthropic' package is not installed (pip install anthropic)") from exc
            self._client = anthropic.Anthropic(max_retries=2, timeout=60.0)
        return self._client

    def complete(self, user_text: str) -> tuple[str, str]:
        import anthropic
        kwargs: dict[str, Any] = dict(
            model=self.model, max_tokens=800, system=self.system,
            messages=[{"role": "user", "content": user_text}],
            output_config={"effort": self.cfg.effort, "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        )
        for attempt in range(2):
            try:
                if self._use_fallbacks:
                    resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
                else:
                    resp = self.client.messages.create(**kwargs)
                break
            except anthropic.AuthenticationError as exc:
                raise JudgeError("auth", f"Anthropic API key rejected: {exc.message}") from exc
            except anthropic.BadRequestError as exc:
                msg = str(getattr(exc, "message", exc))
                if self._use_fallbacks and ("fallback" in msg.lower() or "beta" in msg.lower()):
                    self._use_fallbacks = False
                    continue
                raise JudgeError("bad_request", msg[:200]) from exc
            except anthropic.RateLimitError as exc:
                wait = float((exc.response.headers.get("retry-after") if getattr(exc, "response", None) else None) or 20)
                time.sleep(min(wait, 60))
                if attempt:
                    raise JudgeError("ratelimit") from exc
            except anthropic.APIStatusError as exc:
                raise JudgeError("transient", f"api error {exc.status_code}") from exc
            except anthropic.APIConnectionError as exc:
                raise JudgeError("transient", "connection error") from exc
        else:
            raise JudgeError("ratelimit")
        if getattr(resp, "stop_reason", None) == "refusal":
            raise JudgeError("refused")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        return text, getattr(resp, "model", self.model)


class OpenAICompatBackend:
    """Any /v1/chat/completions endpoint: Groq, Gemini, Cerebras, OpenRouter, Mistral, Ollama, vLLM, LM Studio..."""

    def __init__(self, provider: str, base_url: str, model: str, api_key: str, system_text: str,
                 temperature: float = 0.0, http: Any = None):
        self.name, self.base_url, self.model, self.api_key = provider, base_url.rstrip("/"), model, api_key
        self.system_text = system_text
        self.temperature = temperature
        self._json_mode = "schema"      # schema -> object -> none, downgraded when a provider rejects it
        self._switched = False
        self.timeout = 90
        if http is None:
            import requests
            http = requests.Session()
        self.http = http

    def _post(self, payload: dict):
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.name == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/mohamed-cherif/GET-ME-A-JOB"
            headers["X-Title"] = "hardware internships watcher"
        return self.http.post(f"{self.base_url}/chat/completions", json=payload, headers=headers, timeout=self.timeout)

    _MODEL_PREFS = [r"llama-3\.3-70b", r"llama-4.*(scout|maverick)", r"gpt-oss-120b", r"qwen3-?(32|235)b", r"gemini.*flash(?!.*lite)",
                    r"gemini.*flash", r"llama-3\.1-70b", r"mistral-small", r"llama.*70b", r"qwen.*(7|8|14|32)b",
                    r"llama.*8b", r"gpt-oss-20b"]

    def _pick_live_model(self) -> str:
        """Ask the provider which models exist and choose a capable, cheap one."""
        try:
            if not hasattr(self.http, "get"):
                return ""
            resp = self.http.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}, timeout=30)
            if resp.status_code >= 400:
                return ""
            data = resp.json()
            ids = [m.get("id") or m.get("name") or "" for m in (data.get("data") or data.get("models") or [])]
            ids = [i.split("/", 1)[-1] if i.startswith("models/") else i for i in ids if i]
        except Exception:  # noqa: BLE001
            return ""
        for pat in self._MODEL_PREFS:
            rx = re.compile(pat, re.I)
            hits = [i for i in ids if rx.search(i) and not re.search(r"vision|audio|tts|embed|guard|whisper|image|preview", i, re.I)]
            if hits:
                return sorted(hits, key=len)[0]
        return ""

    def _apply_json_mode(self, payload: dict) -> None:
        payload.pop("response_format", None)
        if self._json_mode == "schema":
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "judgment", "strict": True, "schema": JUDGE_SCHEMA}}
        elif self._json_mode == "object":
            payload["response_format"] = {"type": "json_object"}

    def complete(self, user_text: str) -> tuple[str, str]:
        payload: dict[str, Any] = {
            # generous: "thinking" models (Gemini, o-series, qwen3) spend output tokens reasoning before the JSON
            "model": self.model, "temperature": self.temperature, "max_tokens": 4096,
            "messages": [{"role": "system", "content": self.system_text + "\n\nRespond with a single JSON object with exactly these keys: "
                          + ", ".join(JUDGE_SCHEMA["properties"].keys()) + "."},
                         {"role": "user", "content": user_text}],
        }
        self._apply_json_mode(payload)
        for attempt in range(3):
            try:
                resp = self._post(payload)
            except Exception as exc:  # noqa: BLE001  (timeouts, connection resets)
                if attempt < 2 and ("timed out" in str(exc).lower() or "timeout" in type(exc).__name__.lower()
                                    or "connection" in str(exc).lower()):
                    time.sleep(2)
                    continue
                raise JudgeError("transient", f"{self.name}: {str(exc)[:160]}") from exc
            if resp.status_code in (401, 403):
                raise JudgeError("auth", f"{self.name}: API key rejected ({resp.status_code}) {resp.text[:160]}")
            if resp.status_code == 429:
                body = resp.text.lower()
                if any(w in body for w in ("per day", "daily", "quota exceeded", "requests per day", "rpd", "exhausted", "billing")):
                    raise JudgeError("ratelimit", f"{self.name}: daily quota exhausted ({resp.text[:120]})")
                wait = float(resp.headers.get("retry-after") or 15)
                time.sleep(min(wait, 30))
                continue
            if resp.status_code == 400 and self._json_mode != "none" and (
                    "response_format" in resp.text or "json_schema" in resp.text or "schema" in resp.text.lower()):
                self._json_mode = "object" if self._json_mode == "schema" else "none"   # downgrade and retry
                self._apply_json_mode(payload)
                continue
            if resp.status_code == 404 and self.name == "ollama":
                raise JudgeError("auth", f"ollama: model {self.model!r} not found - run: ollama pull {self.model}")
            if resp.status_code in (400, 404) and "model" in resp.text.lower():
                # Providers retire model names and often name the replacement in the error text
                # ("... please use models/gemini-x-flash ..."). Follow the hint once, otherwise give up clearly.
                hinted = _suggested_model(resp.text) or self._pick_live_model()
                if hinted and hinted != self.model and not self._switched:
                    log.warning("%s: model %r unavailable; switching to %r as suggested by the provider", self.name, self.model, hinted)
                    self.model = payload["model"] = hinted
                    self._switched = True
                    continue
                raise JudgeError("auth", f"{self.name}: model {self.model!r} rejected ({resp.status_code}). "
                                         f"Set LLM_MODEL to a current model id. Provider said: {resp.text[:160]}")
            if resp.status_code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise JudgeError("bad_request", f"{self.name} {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content")) or ""
            if isinstance(text, list):  # some providers return content parts
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            if choice.get("finish_reason") == "length" and payload["max_tokens"] < 16000:
                payload["max_tokens"] = 16000      # reply was cut off: once more with a much larger budget
                continue
            return text, data.get("model") or self.model
        raise JudgeError("ratelimit", f"{self.name}: rate limited / unavailable")


def build_backend(cfg: LLMConfig, system_text: str, client: Any = None, http: Any = None, name: str = ""):
    if client is not None:  # an injected Anthropic client (tests) needs no credential lookup
        return AnthropicBackend(cfg, system_text, client=client)
    provider, base_url, model, api_key = resolve_provider(cfg, name)
    if provider == "anthropic":
        return AnthropicBackend(cfg, system_text, client=client)
    return OpenAICompatBackend(provider, base_url, model, api_key, system_text, cfg.temperature, http=http)


def build_backends(cfg: LLMConfig, system_text: str, client: Any = None, http: Any = None) -> list:
    """Every usable provider, in failover order. Raises JudgeError('auth') when there is none."""
    if client is not None:
        return [AnthropicBackend(cfg, system_text, client=client)]
    names = candidate_providers(cfg)
    if not names:
        raise JudgeError("auth", "no LLM credentials found: set GROQ_API_KEY, GEMINI_API_KEY, CEREBRAS_API_KEY, "
                                 "OPENROUTER_API_KEY, MISTRAL_API_KEY, ANTHROPIC_API_KEY, or run Ollama locally")
    out = []
    for n in names:
        try:
            out.append(build_backend(cfg, system_text, http=http, name=n))
        except JudgeError as exc:
            log.warning("LLM provider %s skipped: %s", n, exc)
    if not out:
        raise JudgeError("auth", "no usable LLM provider")
    return out


class LLMJudge:
    def __init__(self, cfg: LLMConfig, store=None, client: Any = None, http: Any = None):
        self.cfg = cfg
        self.store = store
        self._client = client
        self._http = http
        self._backend = None
        self.calls = 0
        self.disabled_reason = ""
        self.system_text = SYSTEM_TEMPLATE.format(profile=cfg.profile.strip())

    # -- backends (failover chain) -------------------------------------------
    @property
    def backends(self) -> list:
        if self._backend is None and not self.disabled_reason:
            try:
                self._backend = build_backends(self.cfg, self.system_text, client=self._client, http=self._http)
                self._dead: dict = {}       # backend name -> reason (exhausted / bad key) for this process
                log.info("LLM judge: %s", " -> ".join(f"{b.name}/{b.model}" for b in self._backend))
            except JudgeError as exc:
                self.disabled_reason = str(exc)
                self._backend = []
        return self._backend or []

    @property
    def backend(self):
        live = [b for b in self.backends if b.name not in getattr(self, "_dead", {})]
        return live[0] if live else None

    @property
    def available(self) -> bool:
        return self.cfg.enabled and not self.disabled_reason and self.backend is not None

    def describe(self) -> str:
        bs = self.backends
        if not bs:
            return f"unavailable: {self.disabled_reason}"
        dead = getattr(self, "_dead", {})
        return " -> ".join(f"{b.name} / {b.model}" + (f" (skipped: {dead[b.name]})" if b.name in dead else "") for b in bs)

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
    def judge(self, job: Job, keyword_score: int = 0) -> Judgment:
        cached = self.cached(job)
        if cached:
            return Judgment(True, cached)
        if not self.available:
            return Judgment(False, error=self.disabled_reason or "judge disabled")
        if self.calls >= self.cfg.max_calls_per_cycle:
            return Judgment(False, error="per-cycle budget exhausted")
        user_text = build_user_message(job, self.cfg.max_description_chars)
        self.calls += 1
        text = model = None
        last_error = "no provider"
        for backend in self.backends:
            if backend.name in self._dead:
                continue
            try:
                text, model = backend.complete(user_text)
                break
            except JudgeError as exc:
                last_error = f"{backend.name}: {exc}"
                if exc.kind in ("auth", "ratelimit"):
                    # bad key or quota exhausted: stop using this provider for the rest of the run, try the next one
                    self._dead[backend.name] = "quota exhausted" if exc.kind == "ratelimit" else "key rejected"
                    log.warning("LLM provider %s unavailable (%s); %s", backend.name, exc,
                                "failing over" if self.backend else "no other provider configured")
                else:
                    log.warning("LLM judge %s on %s for %s: %s", exc.kind, backend.name, job.url, exc)
                    if exc.kind == "bad_request":
                        break      # not the provider's fault; another provider would fail the same way
            except Exception as exc:  # noqa: BLE001
                last_error = f"{backend.name}: {str(exc)[:160]}"
                log.warning("LLM judge failed on %s for %s: %s", backend.name, job.url, exc)
        if text is None:
            if not self.backend:
                self.disabled_reason = f"all LLM providers unavailable ({last_error})"
                log.error("LLM judge disabled for this run: %s", self.disabled_reason)
            return Judgment(False, error=last_error)
        try:
            data = normalize_judgment(extract_json(text))
        except (json.JSONDecodeError, ValueError, TypeError):
            log.warning("LLM judge returned non-JSON for %s (%d chars): %r", job.url, len(text or ""), (text or "")[-300:])
            return Judgment(False, error="unparseable response")
        data["model"] = model
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
