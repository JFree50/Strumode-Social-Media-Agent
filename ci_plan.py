"""
ci_plan.py — emits KEY=value lines the publish workflow reads into $GITHUB_OUTPUT.
Centralises the "should we act, and on what?" decision (DST-safe time guard,
which post type/date, does the merged draft exist, is publishing enabled) in one
testable place so the YAML stays dumb.

Env in:  INPUT_POST_TYPE, INPUT_POST_DATE (from workflow_dispatch), GITHUB_EVENT_NAME
Stdout:  post_type / post_date / proceed / have_draft / enable_publish / assets_repo / assets_branch
"""
from __future__ import annotations

import os

import common


def _default_type() -> str:
    day = common.now_central().strftime("%A")
    return {"Tuesday": "story", "Thursday": "value"}.get(day, "")


def plan() -> dict:
    ptype = os.environ.get("INPUT_POST_TYPE") or _default_type()
    post_date = os.environ.get("INPUT_POST_DATE") or common.now_central().date().isoformat()

    if ptype == "story":
        proceed = common.should_run("Tuesday", 11)
    elif ptype == "value":
        proceed = common.should_run("Thursday", 11)
    else:
        proceed = False

    cfg = common.load_config()["publishing"]
    have_draft = bool(ptype) and (common.QUEUE_DIR / common.draft_filename(post_date, ptype)).exists()

    return {
        "post_type": ptype,
        "post_date": post_date,
        "proceed": "true" if proceed else "false",
        "have_draft": "true" if have_draft else "false",
        "enable_publish": "true" if cfg.get("enable_publish") else "false",
        "assets_repo": cfg["assets_repo"],
        "assets_branch": cfg["assets_branch"],
    }


if __name__ == "__main__":
    for key, value in plan().items():
        print(f"{key}={value}")
