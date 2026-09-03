"""A fake Http that serves canned JSON by URL prefix/substring."""
from __future__ import annotations

import json


class FakeResponse:
    def __init__(self, data, status=200, headers=None, text=None):
        self._data = data
        self.status_code = status
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    def __init__(self, routes: dict):
        self.routes = routes          # substring -> data | callable(method, url, kw) -> data
        self.calls: list[tuple[str, str]] = []

    def _match(self, method, url, kw):
        self.calls.append((method, url))
        for key, val in self.routes.items():
            if key in url:
                data = val(method, url, kw) if callable(val) else val
                if isinstance(data, FakeResponse):
                    return data
                return FakeResponse(data)
        return FakeResponse({"error": "not found"}, status=404, text="not found")

    def get(self, url, **kw):
        return self._match("GET", url, kw)

    def post(self, url, **kw):
        return self._match("POST", url, kw)

    def request(self, method, url, session=None, **kw):
        return self._match(method, url, kw)

    def get_json(self, url, **kw):
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()

    def post_json(self, url, payload, **kw):
        r = self.post(url, json=payload, **kw)
        r.raise_for_status()
        return r.json()

    def new_session(self):
        class _S:
            headers = {}
        return _S()
