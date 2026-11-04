"""Etsy OAuth 2.0 with PKCE: sign in once in the browser, then refresh tokens automatically."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from podagent.http import form_body, request, urllib_transport

AUTH_URL = "https://www.etsy.com/oauth/connect"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
SCOPES = ["listings_r", "listings_w", "shops_r", "shops_w"]


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    q = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


class TokenStore:
    """Tokens in .secrets/etsy_token.json (gitignored), refreshed a minute before they expire."""

    def __init__(self, path: Path, client_id: str, transport=urllib_transport, clock=time.time):
        self.path, self.client_id, self.transport, self.clock = Path(path), client_id, transport, clock

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, tok: dict) -> None:
        """Write the token file readable by its owner only (on Windows, rely on the user folder's ACLs)."""
        tok = {**tok, "obtained_at": int(self.clock())}
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(tok))
        os.chmod(self.path, 0o600)

    def access_token(self) -> str:
        if not self.path.exists():
            raise FileNotFoundError("No Etsy sign-in yet. Run `podagent etsy-login`.")
        tok = json.loads(self.path.read_text(encoding="utf-8"))
        if self.clock() > tok["obtained_at"] + tok["expires_in"] - 60:
            tok = self.refresh(tok["refresh_token"])
        return tok["access_token"]

    def refresh(self, refresh_value: str) -> dict:
        body = form_body({"grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": refresh_value})
        tok = request(
            self.transport, "POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body
        ).json()
        self.save(tok)
        return tok

    def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        body = form_body(
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "code": code,
                "code_verifier": verifier,
            }
        )
        tok = request(
            self.transport,
            "POST",
            TOKEN_URL,
            {"Content-Type": "application/x-www-form-urlencoded"},
            body,
            idempotent=False,
        ).json()
        self.save(tok)
        return tok


def login(store: TokenStore, redirect_uri: str, open_browser=webbrowser.open, timeout: int = 300) -> None:
    """Open Etsy's consent page and catch the redirect on a local server."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    target = urllib.parse.urlparse(redirect_uri)
    if target.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("ETSY_REDIRECT_URI must point at localhost for the sign-in helper")
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<p>Signed in. You can close this tab and return to the terminal.</p>")

        def log_message(self, *args):
            pass

    server = HTTPServer((target.hostname, target.port or 80), Handler)
    server.timeout = timeout
    url = auth_url(store.client_id, redirect_uri, state, challenge)
    print("Opening Etsy sign-in. If no browser opens, visit:\n" + url)
    open_browser(url)
    deadline = time.time() + timeout
    while "code" not in got and "error" not in got and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if "error" in got:
        raise RuntimeError(f"Etsy sign-in refused: {got.get('error_description', got['error'])}")
    if got.get("state") != state:
        raise RuntimeError("Etsy sign-in failed: state mismatch or timed out")
    store.exchange(got["code"], verifier, redirect_uri)
