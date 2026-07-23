"""
manychat_client.py — thin wrapper over the ManyChat Public API (Account API),
used ONLY to push each week's Value Day prompt payload into a ManyChat Bot
Field so the DM flows (built in ManyChat, not this repo) can reference it as
a merge tag ({{latest_prompt}}) instead of Jack hand-pasting it every week.

Scope, deliberately narrow:
  • This repo does NOT create, edit, or trigger ManyChat automations/flows —
    that stays 100% owned in the ManyChat UI, per the Instagram Gameplan.
  • The only call made is POST /fb/page/setBotFieldByName — one bot-wide
    field, one value, once a week. Nothing subscriber-specific, no messaging.

Endpoint verified against ManyChat's docs (api.manychat.com/swagger) and
help center on 2026-07-14 — re-verify there if this starts failing; ManyChat,
like Meta, changes endpoints without much notice.

Never logs the API key. Raises ManyChatError loudly on any non-2xx response
so a broken sync fails the workflow run instead of silently going stale.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

BASE = "https://api.manychat.com"


class ManyChatError(RuntimeError):
    pass


@dataclass
class ManyChatClient:
    api_key: str
    session: requests.Session | None = None

    def __post_init__(self):
        self.session = self.session or requests.Session()

    def _post(self, path: str, body: dict) -> dict:
        url = f"{BASE}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = self.session.post(url, json=body, headers=headers, timeout=30)
        if not resp.ok:
            # Quote path + response body only — never the Authorization header.
            raise ManyChatError(f"POST /{path.lstrip('/')} -> HTTP {resp.status_code}: {resp.text}")
        try:
            data = resp.json()
        except ValueError:
            raise ManyChatError(f"POST /{path.lstrip('/')} returned non-JSON: {resp.text[:200]}")
        if data.get("status") != "success":
            raise ManyChatError(f"POST /{path.lstrip('/')} returned status={data.get('status')!r}: {data}")
        return data

    def set_bot_field_by_name(self, field_name: str, field_value: str) -> dict:
        """Set an account-wide Bot Field. The field must already exist in
        ManyChat (Settings -> Fields -> Bot Fields) — this call sets its
        value, it does not create the field."""
        return self._post("fb/page/setBotFieldByName",
                          {"field_name": field_name, "field_value": field_value})
