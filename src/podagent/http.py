"""Small HTTP layer shared by the Etsy, print-provider and OpenAI clients.

Each client takes a `transport` callable, so tests replace the network with
recorded responses and can assert on every request that would have been sent.
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)

    def json(self):
        return json.loads(self.body) if self.body else {}


Transport = Callable[[str, str, dict, "bytes | None"], Response]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: int = 120) -> Response:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Response(r.status, r.read(), dict(r.headers))
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read(), dict(e.headers or {}))


class HttpError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str):
        super().__init__(f"{method} {url} -> HTTP {status}: {body[:500]}")
        self.method, self.url, self.status, self.body = method, url, status, body

    def short(self) -> str:
        """One line: the host, the status and the API's own message if it sent one."""
        host = urllib.parse.urlsplit(self.url).hostname or self.url
        try:
            data = json.loads(self.body)
            err = data.get("error") if isinstance(data, dict) else None
            text = (
                (err.get("message") if isinstance(err, dict) else err)
                or data.get("error_description")
                or data.get("message")
            )
        except (ValueError, AttributeError):
            text = None
        return f"{host} returned HTTP {self.status}" + (f": {str(text)[:200]}" if text else "")


def request(
    transport: Transport,
    method: str,
    url: str,
    headers: dict | None = None,
    body: bytes | None = None,
    idempotent: bool = True,
    tries: int = 4,
    sleep=time.sleep,
) -> Response:
    """Send with retries. A request that creates something (idempotent=False) is only retried
    on 429, which means it was refused, never on 5xx, where it may already have succeeded."""
    retry_on = RETRY_STATUS if idempotent else {429}
    for attempt in range(tries):
        try:
            r = transport(method, url, dict(headers or {}), body)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if not idempotent or attempt == tries - 1:
                raise
            sleep(2**attempt)
            continue
        if r.status in retry_on and attempt < tries - 1:
            sleep(2**attempt)
            continue
        if r.status >= 400:
            raise HttpError(method, url, r.status, r.body.decode(errors="replace"))
        return r
    raise AssertionError("unreachable")


def form_body(data: dict) -> bytes:
    flat = {
        k: ",".join(str(x) for x in v) if isinstance(v, (list, tuple)) else v for k, v in data.items() if v is not None
    }
    return urllib.parse.urlencode(flat).encode()


def multipart(fields: dict, files: dict[str, Path] | None = None) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = b""
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, p in (files or {}).items():
        p = Path(p)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        out += (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{p.name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode()
            + p.read_bytes()
            + b"\r\n"
        )
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


class Recorder:
    """Test transport: replays queued responses in order and records every request."""

    def __init__(self, routes: list[tuple[str, str, Response]] | None = None):
        self.routes = list(routes or [])
        self.calls: list[dict] = []

    def add(self, method: str, url_part: str, status: int = 200, body=None) -> Recorder:
        raw = body if isinstance(body, bytes) else json.dumps(body if body is not None else {}).encode()
        self.routes.append((method, url_part, Response(status, raw)))
        return self

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        for i, (m, part, resp) in enumerate(self.routes):
            if m == method and part in url:
                del self.routes[i]
                return resp
        raise AssertionError(f"unexpected request {method} {url}")
