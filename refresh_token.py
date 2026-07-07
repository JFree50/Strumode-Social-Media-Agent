"""
refresh_token.py — monthly. Long-lived Instagram Login tokens expire after 60
days; refreshing monthly keeps ours alive. Calls the refresh_access_token
endpoint and writes the NEW token to a gitignored file (default new_token.txt)
for the workflow to store back into the IG_ACCESS_TOKEN repo secret via gh.

The token value is written to a file, never printed (logs would leak it). On any
failure it exits non-zero with the exact manual recovery steps, so the workflow
fails and emails Jack instead of silently letting the token die.
"""
from __future__ import annotations

import sys

import requests

import common

log = common.get_logger("refresh-token")

REFRESH_URL = "https://graph.instagram.com/refresh_access_token"

MANUAL_STEPS = """\
COULD NOT REFRESH THE INSTAGRAM TOKEN. Do this by hand:
  1. Meta app dashboard → Instagram → API setup with Instagram login.
  2. Generate a fresh token for @strumode with scopes:
     instagram_business_basic, instagram_business_content_publish,
     instagram_business_manage_insights.
  3. Exchange it for a long-lived token:
     GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token
         &client_secret=<APP_SECRET>&access_token=<SHORT_LIVED_TOKEN>
  4. Update the IG_ACCESS_TOKEN GitHub Actions secret with the new long-lived token.
A token not refreshed within 60 days expires permanently and must be re-issued this way."""


def main() -> int:
    if common.is_paused():
        log.info("Agent is paused — skipping token refresh. "
                 "NOTE: a pause longer than ~60 days will expire the token (manual re-auth).")
        return 0

    token = common.env("IG_ACCESS_TOKEN", required=True)
    try:
        resp = requests.get(REFRESH_URL,
                            params={"grant_type": "ig_refresh_token", "access_token": token},
                            timeout=60)
        if not resp.ok:
            log.error("Refresh HTTP %s: %s", resp.status_code, resp.text)
            log.error(MANUAL_STEPS)
            raise SystemExit(1)
        payload = resp.json()
        new_token = payload.get("access_token")
        if not new_token:
            log.error("Refresh response had no access_token: %s", payload)
            log.error(MANUAL_STEPS)
            raise SystemExit(1)
    except requests.RequestException as exc:
        log.error("Refresh request failed: %s", exc)
        log.error(MANUAL_STEPS)
        raise SystemExit(1)

    out_path = common.REPO_ROOT / (common.env("TOKEN_OUT") or "new_token.txt")
    out_path.write_text(new_token, encoding="utf-8")  # value never logged
    days = int(payload.get("expires_in", 0)) // 86400
    log.info("Token refreshed. New token valid ~%d days. Written to %s for the "
             "workflow to store as a secret.", days, out_path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
