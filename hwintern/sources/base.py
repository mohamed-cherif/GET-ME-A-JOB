from __future__ import annotations

import logging
from typing import Optional

from ..http import Http
from ..models import Job

log = logging.getLogger(__name__)


class Source:
    """One job board. Subclasses implement fetch() and optionally fetch_details()."""

    kind = "base"
    supports_details = False
    is_aggregator = False

    def __init__(self, http: Http, entry: dict, run_cfg=None):
        self.http = http
        self.entry = entry
        self.run_cfg = run_cfg
        self.store = None  # set by the pipeline (used for ETag caches etc.)

    @property
    def ident(self) -> str:
        return str(self.entry.get("id") or self.entry.get("name") or "")

    @property
    def company(self) -> str:
        return str(self.entry.get("company") or self.ident)

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.ident}"

    def fetch(self) -> list[Job]:  # pragma: no cover - interface
        raise NotImplementedError

    def fetch_details(self, job: Job) -> None:
        """Populate job.description (and has_full_description) for a candidate."""
        return None

    # helpers -------------------------------------------------------------
    def _job(self, **kw) -> Job:
        kw.setdefault("source", self.kind)
        kw.setdefault("company", self.company)
        kw.setdefault("board", self.ident)
        return Job(**kw)


def ident_from_entry(entry: dict) -> Optional[str]:
    return entry.get("id") or entry.get("name")
