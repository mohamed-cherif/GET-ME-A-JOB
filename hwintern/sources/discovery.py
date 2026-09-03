"""Turn a job URL into a pollable board entry (for automatic board discovery)."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

_LANG = re.compile(r"^[a-z]{2}-[A-Za-z]{2}$")


def board_from_url(url: str, company: str = "") -> Optional[dict]:
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    host = p.netloc.lower()
    seg = [s for s in p.path.split("/") if s]
    if "greenhouse.io" in host:
        token = None
        if seg and seg[0] == "embed":
            token = parse_qs(p.query).get("for", [None])[0]
        elif seg:
            token = seg[0]
        if token and re.fullmatch(r"[A-Za-z0-9_-]+", token) and token not in ("jobs", "job", "embed"):
            return {"kind": "greenhouse", "id": token.lower(), "company": company}
    elif "lever.co" in host:
        if seg and re.fullmatch(r"[A-Za-z0-9_-]+", seg[0]):
            return {"kind": "lever", "id": seg[0], "company": company}
    elif "ashbyhq.com" in host:
        if seg and seg[0] not in ("api",):
            return {"kind": "ashby", "id": unquote(seg[0]), "company": company}
    elif "smartrecruiters.com" in host and host.startswith("jobs."):
        if seg and re.fullmatch(r"[A-Za-z0-9_-]+", seg[0]):
            return {"kind": "smartrecruiters", "id": seg[0], "company": company}
    elif "myworkdayjobs.com" in host:
        tenant = host.split(".")[0]
        site = next((s for s in seg if not _LANG.match(s)), None)
        if site and site not in ("job", "details", "wday"):
            return {"kind": "workday", "id": f"{host}|{tenant}|{site}", "host": host, "tenant": tenant,
                    "site": site, "company": company}
    elif "myworkdaysite.com" in host:
        if "recruiting" in seg:
            i = seg.index("recruiting")
            if len(seg) >= i + 3:
                return {"kind": "workday", "id": f"{host}|{seg[i+1]}|{seg[i+2]}", "host": host,
                        "tenant": seg[i + 1], "site": seg[i + 2], "company": company}
    elif "oraclecloud.com" in host:
        m = re.search(r"/sites/([A-Za-z0-9_]+)", p.path)
        if m:
            return {"kind": "oracle", "id": f"{host}|{m.group(1)}", "host": host, "site": m.group(1),
                    "company": company}
    return None
