# Strumode Instagram Engagement Agent

A small cloud agent that runs Strumode's weekly Instagram engine on GitHub Actions:
it **writes** the two weekly posts with the Claude API, **illustrates** them with
OpenAI's GPT Image, opens a **pull request for Jack to approve**, then **publishes**
the merged posts via Instagram and **learns** from the metrics each week.

It automates Stages 4–5 of `STRUMODE/Marketing/Instagram_Gameplan.md`. It does **not**
touch DMs (ManyChat owns that) and never does anything involving fake engagement.

> **Language: Python.** Chosen over Node because this is API-glue + a little image
> processing (Pillow), the Anthropic/OpenAI Python SDKs are first-class, and it keeps
> the whole thing to a handful of dependency-light scripts that are easy for a
> non-engineer to read and edit.

---

## How the loop works

| When (Central) | Workflow | What happens |
|---|---|---|
| **Fri 9:00 am** | `generate.yml` | Claude writes next week's Story (Tue) + Value (Thu) posts → **review.py proofreads & auto-fixes** (spelling, grammar, complete sentences — unresolved errors FAIL the run, so a flawed PR never emails out) → the art is drawn and framed in the brand template → a **PR opens**. |
| **you, anytime** | *(the gate)* | You review the PR, edit anything, and **merge** — merging is the approval to publish. |
| **Tue 11:00 am** | `publish.yml` | Publishes the merged Story Day carousel. |
| **Thu 11:00 am** | `publish.yml` | Publishes the merged Value Day post. |
| **Mon 8:00 am** | `insights.yml` | Pulls metrics → `data/metrics.json` + `data/weekly_report.md`. Friday's drafts read this. |
| **1st of month** | `refresh-token.yml` | Refreshes the Instagram access token so it never expires. |

**The approval gate is permanent (≥ 2 months) and cannot be bypassed.** `publish.py`
only ever reads files that are on `main` — i.e. files you merged. Nothing publishes
otherwise. Publishing is *also* gated by `enable_publish` (see below), which stays
**false** (dry-run) until the seed grid is up.

---

## One-time setup (Jack)

### 1. Push this repo to GitHub
This folder is a ready-to-push git repo with an initial commit. From here:
```bash
cd strumode-ig-agent
git remote add origin https://github.com/<you>/strumode-ig-agent.git   # your PRIVATE repo
git push -u origin main
```
Also create the **public** repo `strumode-ig-assets` (empty is fine) — Instagram's API
only accepts images from a public URL, and post images go public on IG anyway.

### 2. Meta app + Instagram token
In the Meta app you created (developers.facebook.com → your app):
1. **Instagram → API setup with Instagram login** → connect **@strumode**.
2. Generate a token with exactly these three scopes (no others):
   `instagram_business_basic`, `instagram_business_content_publish`,
   `instagram_business_manage_insights`.
3. Exchange it for a **long-lived** token (60-day, auto-refreshed monthly by this agent):
   ```
   GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token
       &client_secret=<APP_SECRET>&access_token=<SHORT_LIVED_TOKEN>
   ```
4. Copy the **long-lived token** and the **Instagram user ID** shown in API setup.

The app can stay in **dev mode** — that's sufficient for posting to our own account.
(No Facebook Page, no App Review, no Meta Verified needed for this agent.)

### 3. GitHub secrets — create these by name
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (console.anthropic.com). |
| `OPENAI_API_KEY` | OpenAI key (platform.openai.com) — powers GPT Image. |
| `IG_ACCESS_TOKEN` | The long-lived Instagram token from step 2. |
| `IG_USER_ID` | The Instagram user ID from step 2. |
| `GH_PAT` | A GitHub token used only to (a) write the refreshed `IG_ACCESS_TOKEN` back into secrets and (b) push approved images to `strumode-ig-assets`. Fine-grained: on **strumode-ig-agent** grant *Secrets: Read/Write* + *Contents: Read/Write*, and on **strumode-ig-assets** grant *Contents: Read/Write*. (Classic fallback: a token with the `repo` scope.) |
| `MANYCHAT_API_KEY` | Only needed if `publishing.manychat_sync: true`. From ManyChat → **Settings → API → Generate your API Key**. Used for exactly one call a week (`setBotFieldByName`) — never touches flows, subscribers, or sends a message. |

Never paste any of these into chat, code, or config — secrets live only here.

### 4. Point config at your assets repo
In `config.yaml`, set `publishing.assets_repo` to `"<you>/strumode-ig-assets"`.

### 5. (Optional) Auto-sync the weekly prompt into ManyChat
By default, getting each week's DM prompt into ManyChat is still the manual step in the
Instagram Gameplan's Monday ritual (Step 4.2) — you copy it from the merged PR into the
ManyChat flow yourself. To automate that instead:

1. In ManyChat: **Settings → Fields → Bot Fields** → create a field named `latest_prompt`
   (or whatever you set `publishing.manychat_bot_field` to).
2. In your DM flow's message (the one that currently has the prompt text hardcoded), replace
   that text with the merge tag for that field instead — ManyChat inserts the merge tag from the
   same field picker used for custom fields.
3. Generate a ManyChat API key (**Settings → API**) and add it as the `MANYCHAT_API_KEY` GitHub
   secret above.
4. In `config.yaml`, set `publishing.manychat_sync: true`.

From then on, every Thursday's live publish also pushes that week's prompt payload into the
`latest_prompt` bot field — the ManyChat flow always shows the current week's content with
nothing to copy-paste. This never touches ManyChat flows/automations themselves, only the one
field's value. It only runs on a real (non-dry-run) publish, and a failure here never un-publishes
or blocks the Instagram post — it just fails the workflow run loudly so you get a GitHub email
and know to paste that week's prompt in by hand instead.

That's it. The first Friday run (or a manual run) will open a draft PR.

---

## Common controls

### Pause everything — one switch
- **Instant, no commit:** repo → **Settings → Secrets and variables → Actions →
  Variables →** add `AGENT_PAUSED = 1`. Every workflow then exits immediately.
  Set it back to `0` (or delete it) to resume.
- Or set `paused: true` in `config.yaml` and commit.

### Turn live publishing on (do this WITH Jack, after the seed grid)
`config.yaml → publishing.enable_publish: true`, commit to `main`. While it's `false`,
publish runs do everything except the final Instagram call and log the exact payload
as `[DRY RUN]`.

### Change posting times
Times live in two places, kept in sync:
- `config.yaml → schedule` (human reference).
- `.github/workflows/*.yml` cron lines. GitHub cron is **UTC**, so each job fires at
  both possible UTC hours for the target Central time and a Python guard
  (`common.should_run`) lets only the correct one proceed. To move a time, update the
  cron hours (remember: Central = UTC−5 in summer / −6 in winter) **and** the
  `should_run(...)` hour in the matching script.

### Change the writing or the look
- Voice, formats, rules: `prompts/content_system_prompt.md`.
- Image style (applies to every post): `prompts/image_style.md`.
- A one-off different look for a single post: set `style_override:` in that post's
  draft front-matter (in the PR). The agent never deviates from the sketch style on
  its own.

### Run something on demand (manual)
Actions tab → pick a workflow → **Run workflow**. Manual runs bypass the time guard.
`publish.yml` takes optional `post_type` / `post_date` inputs (used for the supervised
first test publish).

---

## Files

```
generate.py        Fri: Claude writes the 2 posts -> content/queue/
images.py          Fri: GPT Image draws them -> content/queue/*.jpg (1080x1350 JPEG)
publish.py         Tue/Thu: publish the merged post (dry-run unless enable_publish)
insights.py        Mon: pull metrics -> data/metrics.json + data/weekly_report.md
refresh_token.py   Monthly: refresh the IG token
ci_plan.py         Publish-workflow helper (which post today, is it time, go live?)
common.py          Shared: config, pause switch, DST time guard, draft render/parse
ig_client.py       Instagram API wrapper (graph.instagram.com)
manychat_client.py ManyChat API wrapper (api.manychat.com) — optional weekly bot-field sync only
claude_client.py   Claude API wrapper (structured content generation)
config.yaml        All non-secret settings + the master switches
prompts/           content_system_prompt.md + image_style.md (edit these)
content/queue/     drafts + images awaiting review (the PR)
content/published/ posts after they publish (with their media_id)
data/              metrics.json (learning log) + weekly_report.md
docs/API_NOTES.md  Verified Meta + OpenAI endpoints (with sources)
tests/selftest.py  Offline smoke test (no keys/network)
.github/workflows/ the four cron jobs
```

---

## Testing

Offline (no keys, no network) — proves the wiring:
```bash
pip install -r requirements.txt
python tests/selftest.py
```
Dry-run a real generation locally (needs `ANTHROPIC_API_KEY` + `OPENAI_API_KEY` in a
local `.env` or your shell):
```bash
FORCE_RUN=1 python generate.py && FORCE_RUN=1 python images.py
```
This writes real drafts + images into `content/queue/` for you to eyeball. Publishing
stays a dry-run until `enable_publish` is flipped.

---

## Cost (rough, weekly)
- **Claude (text):** ~cents/week (2 short Sonnet generations).
- **GPT Image (visuals):** the real cost — ~9 images/week. At `quality: medium` ≈ **$1/week**;
  `high` is richer and more. Change `image.quality` in `config.yaml`.
- **GitHub Actions:** free tier easily covers this.

## Failure handling
Every workflow fails **loudly** (non-zero exit) on an expired token, API error, or a
missing merged post, so GitHub emails you the failure. Publishing never blind-retries
(double-posting is worse than skipping): once a post is published it moves out of the
queue, so a re-run finds nothing to do. Ensure GitHub Actions failure notifications are
on for your account (github.com → Settings → Notifications).

## Security
Secrets live only in GitHub Actions secrets and are referenced by name — never in code,
config, or logs. `ig_client.py` never logs the token. If you ever find a key committed
anywhere in here, rotate it and tell Jack.
