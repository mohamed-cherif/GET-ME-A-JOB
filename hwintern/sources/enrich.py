"""Fetch a single posting's description from its ATS when we only have a URL (community feed hits)."""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlsplit

from ..textutil import html_to_text
from .discovery import board_from_url

log = logging.getLogger(__name__)


def fetch_description(http, url: str) -> str:
    """Return plain-text description or '' if the ATS is unknown / the fetch fails."""
    try:
        return _fetch(http, url) or ""
    except Exception as exc:  # noqa: BLE001
        log.debug("enrich failed for %s: %s", url, exc)
        return ""


def _fetch(http, url: str) -> str:
    p = urlsplit(url)
    host = p.netloc.lower()
    seg = [s for s in p.path.split("/") if s]
    q = parse_qs(p.query)
    board = board_from_url(url)
    # Greenhouse: job id is the last path segment, or gh_jid on embedded career pages
    if "greenhouse.io" in host and board:
        jid = q.get("gh_jid", [None])[0] or next((s for s in reversed(seg) if s.isdigit()), None)
        if jid:
            data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{board['id']}/jobs/{jid}?content=true")
            return html_to_text(data.get("content") or "")
    if q.get("gh_jid"):  # any career site embedding Greenhouse
        jid = q["gh_jid"][0]
        # board token unknown: Greenhouse's job endpoint needs it, so try the embed API
        data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/embed/jobs/{jid}?content=true")
        return html_to_text(data.get("content") or "")
    if "lever.co" in host and board and len(seg) >= 2:
        data = http.get_json(f"https://api.lever.co/v0/postings/{board['id']}/{seg[1]}")
        parts = [data.get("descriptionPlain") or ""] + [(l.get("text") or "") + "\n" + (l.get("content") or "") for l in data.get("lists") or []]
        return html_to_text("\n".join(parts))
    if "smartrecruiters.com" in host and board and len(seg) >= 2:
        data = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{board['id']}/postings/{seg[1]}")
        sections = ((data.get("jobAd") or {}).get("sections") or {})
        return "\n".join(html_to_text((sections.get(k) or {}).get("text") or "")
                         for k in ("jobDescription", "qualifications", "additionalInformation"))
    if board and board.get("kind") == "workday":
        # externalPath = everything from "/job/" onwards
        m = re.search(r"(/job/.*)$", p.path)
        if m:
            tenant, site = board["tenant"], board["site"]
            s = http.new_session()
            base = (f"https://{host}/recruiting/{tenant}/{site}" if "myworkdaysite" in host else f"https://{host}/{site}")
            try:
                http.get(base, session=s, timeout=20)
            except Exception:  # noqa: BLE001
                pass
            s.headers.update({"Accept": "application/json"})
            resp = http.get(f"https://{host}/wday/cxs/{tenant}/{site}{m.group(1)}", session=s)
            resp.raise_for_status()
            info = (resp.json() or {}).get("jobPostingInfo") or {}
            return html_to_text(info.get("jobDescription") or "")
    if "ashbyhq.com" in host and board and len(seg) >= 2:
        data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board['id']}")
        for j in data.get("jobs", []):
            if str(j.get("id")) == seg[1]:
                return j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or "")
    return ""
