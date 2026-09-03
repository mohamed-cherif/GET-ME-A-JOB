from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36 hardware-internships-watcher/1.0")


class Http:
    """Thin requests wrapper with retries, a shared UA and per-thread sessions."""

    def __init__(self, timeout: float = 30.0, retries: int = 2, user_agent: str = DEFAULT_UA):
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": self.user_agent,
                              "Accept": "application/json, text/plain, */*",
                              "Accept-Language": "en-US,en;q=0.9"})
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            self._local.session = s
        return s

    def new_session(self) -> requests.Session:
        """A fresh cookie jar (Workday needs one per board)."""
        s = requests.Session()
        s.headers.update(self.session.headers)
        return s

    def request(self, method: str, url: str, session: Optional[requests.Session] = None,
                **kw: Any) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        sess = session or self.session
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = sess.request(method, url, **kw)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    delay = float(resp.headers.get("Retry-After") or (1.5 * (attempt + 1)))
                    time.sleep(min(delay, 20))
                    continue
                return resp
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def get(self, url: str, **kw: Any) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> requests.Response:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw: Any) -> Any:
        resp = self.get(url, **kw)
        resp.raise_for_status()
        return resp.json()

    def post_json(self, url: str, payload: Any, **kw: Any) -> Any:
        headers = kw.pop("headers", {}) or {}
        headers.setdefault("Content-Type", "application/json")
        resp = self.post(url, json=payload, headers=headers, **kw)
        resp.raise_for_status()
        return resp.json()
