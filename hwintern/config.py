from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .filters import FilterConfig

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _interpolate(obj: Any) -> Any:
    if isinstance(obj, str):
        def repl(m: re.Match) -> str:
            return os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else "")
        return _ENV_RE.sub(repl, obj)
    if isinstance(obj, list):
        return [_interpolate(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _interpolate(v) for k, v in obj.items()}
    return obj


def load_dotenv(path: Path, override: bool = False) -> int:
    """Load KEY=value lines from a .env file into os.environ (no dependency on python-dotenv).

    Existing environment variables win unless override=True. Supports comments, blank lines,
    an optional `export ` prefix, single/double quotes and Windows line endings.
    """
    if not path.exists():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, 1)[0].rstrip()   # strip trailing " # comment"
        if override or not os.environ.get(key):
            os.environ[key] = value
            loaded += 1
    return loaded


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return _interpolate(data)


@dataclass
class RunConfig:
    interval_minutes: float = 5.0
    jitter_seconds: float = 20.0
    workers: int = 12
    request_timeout: float = 30.0
    detail_fetch_limit: int = 150         # max per-job detail requests per cycle (Workday/SmartRecruiters/Oracle)
    initial_max_age_days: Optional[float] = 45.0  # on first run, skip notifying jobs older than this
    auto_discover: bool = True            # add boards found via aggregator feeds automatically
    log_level: str = "INFO"
    state_dir: str = "state"


@dataclass
class Config:
    run: RunConfig
    filters: FilterConfig
    notifiers: list[dict]
    companies: list[dict]
    aggregators: list[dict]
    base_dir: Path
    raw: dict = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        p = Path(self.run.state_dir)
        return p if p.is_absolute() else self.base_dir / p

    @property
    def db_path(self) -> Path:
        return self.state_dir / "hwintern.sqlite3"


def load_config(config_path: str | Path = "config.yaml", companies_path: Optional[str | Path] = None) -> Config:
    config_path = Path(config_path).resolve()
    base_dir = config_path.parent
    # .env next to config.yaml (and in the current directory) is loaded automatically
    for env_file in {base_dir / ".env", Path.cwd() / ".env"}:
        load_dotenv(env_file)
    raw = load_yaml(config_path)
    run_raw = raw.get("run") or {}
    run = RunConfig(**{k: v for k, v in run_raw.items() if k in RunConfig.__dataclass_fields__})
    filters = FilterConfig.from_dict(raw.get("filters"))
    notifiers = [n for n in (raw.get("notifiers") or []) if n and n.get("enabled", True)]
    companies_path = Path(companies_path) if companies_path else base_dir / (raw.get("companies_file") or "companies.yaml")
    comp_raw = load_yaml(companies_path)
    companies = list(comp_raw.get("companies") or [])
    companies += list(raw.get("companies") or [])
    aggregators = list(raw.get("aggregators") or [])
    # env-var override for the log level is handy in containers
    if os.environ.get("HWINTERN_LOG_LEVEL"):
        run.log_level = os.environ["HWINTERN_LOG_LEVEL"]
    return Config(run=run, filters=filters, notifiers=notifiers, companies=companies,
                  aggregators=aggregators, base_dir=base_dir, raw=raw)
