from __future__ import annotations

import logging

from .aggregators import ListingsJsonSource
from .ashby import AshbySource
from .base import Source
from .custom import AmazonSource, MicrosoftSource, TeslaSource
from .greenhouse import GreenhouseSource
from .lever import LeverSource
from .oracle import OracleHcmSource
from .smartrecruiters import SmartRecruitersSource
from .workday import WorkdaySource

log = logging.getLogger(__name__)

SOURCES: dict[str, type[Source]] = {
    c.kind: c for c in (GreenhouseSource, LeverSource, AshbySource, SmartRecruitersSource, WorkdaySource,
                        OracleHcmSource, TeslaSource, AmazonSource, MicrosoftSource, ListingsJsonSource)
}


def build_source(http, entry: dict, run_cfg=None) -> Source | None:
    kind = (entry.get("kind") or entry.get("type") or "").lower()
    cls = SOURCES.get(kind)
    if not cls:
        log.warning("unknown source kind %r for %s (skipped)", kind, entry)
        return None
    try:
        return cls(http, entry, run_cfg)
    except Exception as exc:  # noqa: BLE001
        log.warning("bad source entry %s: %s", entry, exc)
        return None
