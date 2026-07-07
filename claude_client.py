"""
claude_client.py — content generation via the Claude API (Anthropic SDK).

Uses a single forced tool call to get structured, schema-shaped output reliably
across SDK versions (no dependency on the newer messages.parse helper). Thinking
is disabled: this is straightforward marketing copy, so we keep runs cheap and
deterministic (cost stays at cents/week, per the brief's "Sonnet is fine").
"""
from __future__ import annotations

import anthropic

# --- tool schemas (structured output targets) --------------------------------

_STORY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hook": {"type": "string", "description": "One-line scroll-stopping hook."},
        "slides": {
            "type": "array",
            "description": "6–8 carousel slides in order.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "on_image_text": {"type": "string",
                                      "description": "Short text shown on the slide (≤ ~14 words)."},
                    "image_prompt": {"type": "string",
                                     "description": "Subject/scene only, one line, no art-style words."},
                },
                "required": ["on_image_text", "image_prompt"],
            },
        },
        "caption": {"type": "string",
                    "description": "Full caption. Ends with the Story Day CTA."},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "alt_texts": {"type": "array", "items": {"type": "string"},
                      "description": "One alt-text sentence per slide, in order."},
    },
    "required": ["hook", "slides", "caption", "hashtags", "alt_texts"],
}

_VALUE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "image_prompt": {"type": "string",
                         "description": "Subject/scene only, one line, no art-style words."},
        "teaser_caption": {"type": "string",
                           "description": "Teases the value; ends with the comment-to-DM CTA."},
        "prompt_payload": {"type": "string",
                           "description": "The full ready-to-paste prompt/mini-workflow DM'd to commenters."},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "alt_text": {"type": "string"},
    },
    "required": ["image_prompt", "teaser_caption", "prompt_payload", "hashtags", "alt_text"],
}

_TOOLS = {
    "story": ("emit_story_post", "Return the finished Story Day carousel post.", _STORY_SCHEMA),
    "value": ("emit_value_post", "Return the finished Value Day post.", _VALUE_SCHEMA),
}


def _client() -> anthropic.Anthropic:
    # Reads ANTHROPIC_API_KEY from the environment (a GitHub Actions secret).
    return anthropic.Anthropic()


def generate(kind: str, system_prompt: str, user_prompt: str,
             model: str, max_tokens: int) -> dict:
    """Generate one post of `kind` ('story'|'value'). Returns the tool input
    dict matching the schema. Raises if the model won't return the tool."""
    tool_name, tool_desc, schema = _TOOLS[kind]
    tool = {"name": tool_name, "description": tool_desc, "input_schema": schema}
    client = _client()

    messages = [{"role": "user", "content": user_prompt}]
    for attempt in range(2):
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            thinking={"type": "disabled"},
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=messages,
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                return dict(block.input)
        # No tool call — nudge once, then give up loudly.
        messages += [
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": f"You must call the {tool_name} tool with the full post."},
        ]
    raise RuntimeError(f"Claude did not return a {tool_name} tool call after 2 attempts.")
