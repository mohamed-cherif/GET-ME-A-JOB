from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "lever-source", "source", "src", "ref", "referrer", "utm_id",
}


def normalize_url(url: str) -> str:
    """Canonical form used for cross-source de-duplication."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _TRACKING_PARAMS]
    path = re.sub(r"/+$", "", parts.path) or "/"
    # Workday / Greenhouse / Lever URLs are case-insensitive on host only.
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path,
                       urlencode(query, doseq=True), ""))


@dataclass
class Job:
    source: str                 # adapter name, e.g. "greenhouse"
    company: str
    title: str
    url: str
    external_id: str
    location: str = ""
    description: str = ""       # plain text when available (may be empty)
    posted_at: Optional[datetime] = None
    terms: list[str] = field(default_factory=list)      # e.g. ["Summer 2027"] (aggregators)
    category: str = ""          # aggregator-provided category, if any
    sponsorship: str = ""       # aggregator-provided sponsorship info, if any
    board: str = ""             # board identifier used by the adapter (for discovery / debugging)
    has_full_description: bool = False
    extra: dict = field(default_factory=dict)

    # populated by the classifier
    matched_categories: list[str] = field(default_factory=list)
    detected_terms: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    score: int = 0              # fit score 0-100
    tier: str = "safety"        # target | match | safety
    summary: str = ""           # one-line description of the work (from the LLM judge)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.board or '-'}:{self.external_id}"

    @property
    def url_norm(self) -> str:
        return normalize_url(self.url)

    def age_days(self, now: Optional[datetime] = None) -> Optional[float]:
        if not self.posted_at:
            return None
        now = now or datetime.now(timezone.utc)
        posted = self.posted_at if self.posted_at.tzinfo else self.posted_at.replace(tzinfo=timezone.utc)
        return (now - posted).total_seconds() / 86400.0

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        from .textutil import parse_datetime  # local import to avoid a cycle
        j = cls(source=d.get("source", ""), company=d.get("company", ""), title=d.get("title", ""),
                url=d.get("url", ""), external_id=str(d.get("key", "").rsplit(":", 1)[-1]),
                location=d.get("location", ""), board=d.get("board", ""), terms=d.get("terms") or [],
                category=d.get("category", ""), sponsorship=d.get("sponsorship", ""),
                posted_at=parse_datetime(d.get("posted_at")))
        j.detected_terms = d.get("detected_terms") or []
        j.matched_categories = d.get("matched_categories") or []
        j.flags = d.get("flags") or []
        j.score = int(d.get("score") or 0)
        j.tier = d.get("tier") or "safety"
        j.summary = d.get("summary") or ""
        if d.get("llm"):
            j.extra["llm"] = d["llm"]
        return j

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "source": self.source,
            "board": self.board,
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "location": self.location,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "terms": self.terms,
            "detected_terms": self.detected_terms,
            "category": self.category,
            "matched_categories": self.matched_categories,
            "sponsorship": self.sponsorship,
            "flags": self.flags,
            "score": self.score,
            "tier": self.tier,
            "summary": self.summary,
            "llm": self.extra.get("llm"),
        }
