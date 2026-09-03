from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone
from typing import Optional

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    text = _html.unescape(raw)
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _NL_RE.sub("\n\n", text).strip()


def parse_datetime(value) -> Optional[datetime]:
    """Best-effort parse of the many date shapes job boards use."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return parse_datetime(int(s))
    s = s.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(0, n - 1)].rstrip() + "…"
