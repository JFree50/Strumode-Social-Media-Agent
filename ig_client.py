"""
ig_client.py — thin wrapper over the *Instagram API with Instagram Login*
(host: graph.instagram.com). Endpoints/params verified against Meta's docs on
2026-07-07 — see docs/API_NOTES.md. If a call starts failing, re-verify there
first; Meta changes these often.

Never logs the access token. On any API error it raises IGError with the HTTP
status and Meta's error body so failed workflow runs are loud and debuggable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

BASE = "https://graph.instagram.com"

# Media (post) insight metrics valid for IMAGE + CAROUSEL_ALBUM (FEED) posts.
# NOTE: the save metric is `saved` (singular), not "saves". `impressions` is
# deprecated for media created after 2024-07-02 — we use `views`.
MEDIA_METRICS = ["reach", "likes", "comments", "saved", "shares",
                 "total_interactions", "views"]

# Account insights. profile_views was DEPRECATED by Meta (2025-01-08, v21+) with
# no direct replacement, so we track the live funnel signals instead.
ACCOUNT_METRICS = ["reach", "profile_links_taps", "follows_and_unfollows"]

# Total follower/following counts are FIELDS on the user node, not insights.
ACCOUNT_FIELDS = ["username", "followers_count", "follows_count", "media_count"]


class IGError(RuntimeError):
    pass


@dataclass
class IGClient:
    access_token: str
    user_id: str
    session: requests.Session | None = None

    def __post_init__(self):
        self.session = self.session or requests.Session()

    # ── low-level ────────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, params: dict) -> dict:
        params = {**params, "access_token": self.access_token}
        url = f"{BASE}/{path.lstrip('/')}"
        resp = self.session.request(method, url, params=params, timeout=60)
        if not resp.ok:
            # Deliberately quote path (no token) and Meta's body, never the URL.
            raise IGError(f"{method} /{path.lstrip('/')} -> HTTP {resp.status_code}: {resp.text}")
        try:
            return resp.json()
        except ValueError:
            raise IGError(f"{method} /{path.lstrip('/')} returned non-JSON: {resp.text[:200]}")

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params or {})

    def _post(self, path: str, params: dict) -> dict:
        return self._request("POST", path, params)

    # ── publishing ───────────────────────────────────────────────────────────
    def create_image_container(self, image_url: str, caption: str | None = None,
                               alt_text: str | None = None,
                               is_carousel_item: bool = False) -> str:
        params: dict = {"image_url": image_url}
        if caption is not None:
            params["caption"] = caption
        if alt_text:
            params["alt_text"] = alt_text
        if is_carousel_item:
            params["is_carousel_item"] = "true"
        return str(self._post(f"{self.user_id}/media", params)["id"])

    def create_carousel_container(self, children_ids: list[str], caption: str) -> str:
        params = {"media_type": "CAROUSEL",
                  "children": ",".join(children_ids),
                  "caption": caption}
        return str(self._post(f"{self.user_id}/media", params)["id"])

    def container_status(self, container_id: str) -> str:
        data = self._get(container_id, {"fields": "status_code"})
        return data.get("status_code", "UNKNOWN")

    def wait_until_finished(self, container_id: str,
                            timeout_seconds: int, interval_seconds: int) -> None:
        """Poll until FINISHED. Raise loudly on ERROR/EXPIRED or timeout — we
        would rather skip a post than publish a broken/half-processed one."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            status = self.container_status(container_id)
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                raise IGError(f"container {container_id} status={status} — will not publish")
            if time.monotonic() >= deadline:
                raise IGError(f"container {container_id} not FINISHED within "
                              f"{timeout_seconds}s (last status={status})")
            time.sleep(interval_seconds)

    def publish(self, container_id: str) -> str:
        return str(self._post(f"{self.user_id}/media_publish",
                              {"creation_id": container_id})["id"])

    def publishing_limit(self) -> dict:
        return self._get(f"{self.user_id}/content_publishing_limit",
                         {"fields": "config,quota_usage"})

    # ── insights ─────────────────────────────────────────────────────────────
    def media_insights(self, media_id: str, metrics: list[str] | None = None) -> dict:
        metrics = metrics or MEDIA_METRICS
        try:
            data = self._get(f"{media_id}/insights", {"metric": ",".join(metrics)})
        except IGError:
            # Some metrics 404 for young/low-follower accounts — retry one-by-one
            # so one unsupported metric doesn't blank the whole post.
            out: dict = {}
            for m in metrics:
                try:
                    d = self._get(f"{media_id}/insights", {"metric": m})
                    out.update(_flatten_insights(d))
                except IGError:
                    continue
            return out
        return _flatten_insights(data)

    def account_insights(self, metrics: list[str] | None = None) -> dict:
        metrics = metrics or ACCOUNT_METRICS
        out: dict = {}
        for m in metrics:  # request individually; requirements differ per metric
            try:
                d = self._get(f"{self.user_id}/insights",
                              {"metric": m, "period": "day", "metric_type": "total_value"})
                out.update(_flatten_insights(d))
            except IGError:
                continue  # e.g. follows_and_unfollows needs 100+ followers
        return out

    def account_fields(self, fields: list[str] | None = None) -> dict:
        fields = fields or ACCOUNT_FIELDS
        return self._get(self.user_id, {"fields": ",".join(fields)})


def _flatten_insights(payload: dict) -> dict:
    """Normalise Meta's insight shapes into {metric_name: value}. Handles both
    total_value metrics and the older values[] time-series shape."""
    out: dict = {}
    for item in payload.get("data", []):
        name = item.get("name")
        if not name:
            continue
        if "total_value" in item and item["total_value"] is not None:
            out[name] = item["total_value"].get("value")
        else:
            values = item.get("values") or []
            out[name] = values[-1].get("value") if values else None
    return out
