# API Notes — verified against live docs (2026-07-07)

> The build brief says: **"Do NOT guess API behavior."** Everything below was verified
> against the official Meta / OpenAI documentation on 2026-07-07. If a call starts failing,
> re-verify here first — Meta changes these often. Sources listed at the bottom.

## Instagram — "Instagram API with Instagram Login"

- **Host:** `https://graph.instagram.com` (NOT `graph.facebook.com`). No Facebook Page required.
- **Account:** our own professional (Business) account @strumode. App stays in dev mode — that's
  sufficient for posting to our own account. App Review only matters for other people's accounts.
- **Scopes:** `instagram_business_basic`, `instagram_business_content_publish`,
  `instagram_business_manage_insights`.
- **Token:** long-lived Instagram User Access Token (60 days, refreshable). See refresh below.

### Content Publishing  (scope: `instagram_business_content_publish`)

Single image:
1. Create container: `POST /{ig-user-id}/media`
   params: `image_url` (public JPEG URL, required), `caption` (optional),
   `alt_text` (optional, since 2025-03-24), `access_token`.
2. (Recommended) Poll status: `GET /{container-id}?fields=status_code`
   → one of `IN_PROGRESS | FINISHED | ERROR | EXPIRED | PUBLISHED`. Publish only when `FINISHED`.
3. Publish: `POST /{ig-user-id}/media_publish` params: `creation_id={container-id}`, `access_token`.

Carousel (2–10 images):
1. For each image, create an item container: `POST /{ig-user-id}/media`
   params: `image_url`, `is_carousel_item=true`, `access_token`.
2. Create the carousel container: `POST /{ig-user-id}/media`
   params: `media_type=CAROUSEL`, `children={id1,id2,...}` (comma-separated, max 10),
   `caption`, `access_token`.
3. Poll status, then `media_publish` with the carousel container id.

Rules:
- **JPEG only.** Aspect ratio must be between **4:5** and **1.91:1**. Carousel crops all
  images to the **first** image's ratio (default 1:1) — so we generate every image at 4:5 (1080x1350).
- **Rate limit:** 100 API-published posts per rolling 24h (a carousel counts as 1).
  Check: `GET /{ig-user-id}/content_publishing_limit?fields=config,quota_usage`.
- Containers expire ~24h after creation; publish promptly.
- `image_url` must be **publicly reachable** by Meta's servers — served from the public
  `strumode-ig-assets` repo (raw.githubusercontent.com). R2/object-store is the documented
  fallback if raw GitHub URLs ever get rejected.

### Media (post) Insights  (scope: `instagram_business_manage_insights`)

`GET /{ig-media-id}/insights?metric=<comma list>`

Valid metrics for **IMAGE** and **CAROUSEL_ALBUM** (FEED) posts:
`reach`, `likes`, `comments`, `saved`, `shares`, `total_interactions`, `views`.

- **The save metric is `saved` (singular), not "saves".**
- `impressions` is **deprecated** for media created after 2024-07-02 — do not request it; use `views`.
- Some metrics 404/omit for accounts < 100 followers — request tolerantly, skip what errors.

### Account Insights  (scope: `instagram_business_manage_insights`)

`GET /{ig-user-id}/insights?metric=<m>&period=day&metric_type=total_value`

- `reach` — period `day`; `metric_type` `total_value` (or `time_series`).
- `follows_and_unfollows` — period `day`; `metric_type=total_value` (requires 100+ followers).
- `profile_links_taps` — period `day`; `metric_type=total_value` (contact-button taps = our "link taps").

**DEPRECATED (Meta, 2025-01-08, Graph API v21+):** `profile_views`, `website_clicks`,
`email_contacts`, `phone_call_clicks`, `text_message_clicks`, and non-Reels `video_views`.
There is **no direct replacement for `profile_views`.** So for the Gameplan's north-star
funnel (profile visits → link taps → calls) we track the current, live signals instead:
**`reach` + `profile_links_taps` + follower growth**. This is the one forced deviation from the
brief's literal "profile views" wording — flagged to Jack. (See insights.py.)

### Profile fields (scope: `instagram_business_basic`)

`GET /{ig-user-id}?fields=followers_count,follows_count,media_count,username`
— total follower count is a **field**, not an insight metric. Use this for follower totals.

### Token refresh  (scope: `instagram_business_basic`)

`GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token={long-lived-token}`
- Token must be ≥24h old and not yet expired. Refreshed token is valid 60 days from refresh.
- Response JSON: `access_token`, `token_type` ("bearer"), `expires_in` (seconds).
- A token not refreshed within 60 days expires permanently → re-auth by hand (README).

### Initial token acquisition (Jack does this by hand — for README)

Short-lived → long-lived exchange (one time, in the Meta app or via curl):
`GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret={app-secret}&access_token={short-lived-token}`

## OpenAI — Images API (gpt-image-1)

`POST https://api.openai.com/v1/images/generations`
- `model`: `"gpt-image-1"`; `prompt` (≤ 32000 chars).
- `size`: `"1024x1024" | "1024x1536" | "1536x1024" | "auto"` — **no arbitrary sizes**.
  We generate at **`1024x1536`** (portrait) then center-crop + resize to **1080x1350 (4:5)** with Pillow.
- `quality`: `"low" | "medium" | "high" | "auto"`.
- `output_format`: `"png" | "jpeg" | "webp"`; `output_compression`: 0–100 (jpeg/webp).
- Response: `data[].b64_json` — **gpt-image-1 always returns base64; no `url` is provided.**

## Sources
- https://developers.facebook.com/docs/instagram-platform/content-publishing
- https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights/
- https://developers.facebook.com/docs/instagram-platform/api-reference/instagram-user/insights/
- https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token/
- https://developers.facebook.com/docs/instagram-platform/insights/
- https://developers.openai.com/api/reference/resources/images/methods/generate
- https://developers.openai.com/api/docs/guides/image-generation
