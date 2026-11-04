"""Etsy Open API v3 client: the calls this tool needs, and nothing that can publish.

Setting a listing's `state` to `active` publishes it. `call()` therefore sends only the
requests on ALLOWED, exact method and path, and refuses `state` anywhere in a request.
The one exception is the read-only `state=draft` filter used to list drafts. Publishing is
always the shop owner's click in Etsy.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

from podagent.etsy.oauth import TokenStore
from podagent.http import form_body, multipart, request, urllib_transport

API = "https://openapi.etsy.com/v3/application"
ALLOWED = [
    ("GET", r"/users/me"),
    ("GET", r"/shops/\d+/(shipping-profiles|policies/return|readiness-state-definitions|production-partners|sections)"),
    ("GET", r"/shops/\d+/listings\?state=draft&limit=\d+&offset=\d+"),
    ("POST", r"/shops/\d+/sections"),
    ("POST", r"/shops/\d+/listings"),
    ("POST", r"/shops/\d+/listings/\d+/images"),
    ("PUT", r"/listings/\d+/inventory"),
]
READ_DRAFTS = ALLOWED[2]


class PublishBlocked(RuntimeError):
    pass


def _has_state(obj) -> bool:
    if isinstance(obj, dict):
        return any(k == "state" or _has_state(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(_has_state(v) for v in obj)
    return False


def check_request(method: str, path: str, *payloads) -> None:
    """Raise PublishBlocked unless this is one of the requests on ALLOWED, with no `state` in it."""
    rule = next((r for r in ALLOWED if r[0] == method and re.fullmatch(r[1], path)), None)
    if rule is None:
        raise PublishBlocked(f"{method} {path} is not a request this tool makes")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    if "state" in query and rule != READ_DRAFTS:
        raise PublishBlocked("this tool never sends a listing state; publish in Etsy yourself")
    if any(_has_state(p) for p in payloads if p):
        raise PublishBlocked("this tool never changes a listing's state; publish in Etsy yourself")


class EtsyClient:
    def __init__(
        self,
        keystring: str,
        shared_secret: str,
        tokens: TokenStore,
        transport=urllib_transport,
        sleep=time.sleep,
        min_interval: float = 0.2,
    ):
        self.api_key = f"{keystring}:{shared_secret}"
        self.tokens, self.transport, self.sleep, self.min_interval = tokens, transport, sleep, min_interval

    def call(
        self,
        method: str,
        path: str,
        form: dict | None = None,
        json_body: dict | None = None,
        files: tuple[dict, dict] | None = None,
        idempotent: bool = True,
    ) -> dict:
        check_request(method, path, form, json_body, files[0] if files else None)
        headers = {"x-api-key": self.api_key, "Authorization": f"Bearer {self.tokens.access_token()}"}
        body = None
        if form is not None:
            body, headers["Content-Type"] = form_body(form), "application/x-www-form-urlencoded"
        elif json_body is not None:
            body, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif files is not None:
            body, headers["Content-Type"] = multipart(*files)
        self.sleep(self.min_interval)  # Etsy allows about 10 requests a second
        return request(
            self.transport, method, API + path, headers, body, idempotent=idempotent, sleep=self.sleep
        ).json()

    # -- shop setup --------------------------------------------------------
    def me(self) -> dict:
        return self.call("GET", "/users/me")

    def shipping_profiles(self, shop_id: int) -> list[dict]:
        return self.call("GET", f"/shops/{shop_id}/shipping-profiles")["results"]

    def return_policies(self, shop_id: int) -> list[dict]:
        return self.call("GET", f"/shops/{shop_id}/policies/return")["results"]

    def readiness_states(self, shop_id: int) -> list[dict]:
        return self.call("GET", f"/shops/{shop_id}/readiness-state-definitions")["results"]

    def production_partners(self, shop_id: int) -> list[dict]:
        return self.call("GET", f"/shops/{shop_id}/production-partners")["results"]

    def sections(self, shop_id: int) -> list[dict]:
        return self.call("GET", f"/shops/{shop_id}/sections")["results"]

    def create_section(self, shop_id: int, title: str) -> dict:
        return self.call("POST", f"/shops/{shop_id}/sections", form={"title": title}, idempotent=False)

    # -- drafts ------------------------------------------------------------
    def create_draft(self, shop_id: int, fields: dict) -> dict:
        return self.call("POST", f"/shops/{shop_id}/listings", form=fields, idempotent=False)

    def upload_image(self, shop_id: int, listing_id: int, path: Path, rank: int, alt_text: str) -> dict:
        return self.call(
            "POST",
            f"/shops/{shop_id}/listings/{listing_id}/images",
            files=({"rank": rank, "alt_text": alt_text}, {"image": path}),
            idempotent=False,
        )

    def update_inventory(self, listing_id: int, body: dict) -> dict:
        return self.call("PUT", f"/listings/{listing_id}/inventory", json_body=body)

    def draft_listings(self, shop_id: int) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self.call("GET", f"/shops/{shop_id}/listings?state=draft&limit=100&offset={offset}")
            out += page.get("results", [])
            offset += 100
            if offset >= page.get("count", 0) or not page.get("results"):
                return out
