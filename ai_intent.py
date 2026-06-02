from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI


INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "count_active_campaigns",
                "status",
                "activate_campaign",
                "pause_campaign",
                "compare_performance",
                "compare_budget",
                "create_full_funnel",
                "update_budget",
                "help",
                "unknown",
            ],
        },
        "ad_account_id": {"type": "string"},
        "campaign_query": {"type": "string"},
        "goal": {"type": "string", "enum": ["messages", "conversions", "leads", "traffic"]},
        "level": {"type": "string", "enum": ["campaign", "adset", "ad"]},
        "date_preset": {"type": "string", "enum": ["today", "yesterday", "last_7_days", "last_30_days", "custom"]},
        "since": {"type": "string"},
        "until": {"type": "string"},
        "campaign_name": {"type": "string"},
        "page_url": {"type": "string"},
        "post_url": {"type": "string"},
        "daily_budget": {"type": "integer"},
        "targeting_note": {"type": "string"},
        "wants_live": {"type": "boolean"},
        "confidence": {"type": "number"},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "reply_hint": {"type": "string"},
        "target_age_min": {"type": "integer"},
        "target_age_max": {"type": "integer"},
        "target_genders": {"type": "array", "items": {"type": "integer"}},
        "target_locations": {"type": "array", "items": {"type": "string"}},
        "budget_value": {"type": "integer"},
        "budget_object_type": {"type": "string", "enum": ["campaign", "adset"]},
    },
    "required": [
        "intent",
        "ad_account_id",
        "campaign_query",
        "goal",
        "level",
        "date_preset",
        "since",
        "until",
        "campaign_name",
        "page_url",
        "post_url",
        "daily_budget",
        "targeting_note",
        "wants_live",
        "confidence",
        "missing_fields",
        "reply_hint",
        "target_age_min",
        "target_age_max",
        "target_genders",
        "target_locations",
        "budget_value",
        "budget_object_type",
    ],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """You parse Vietnamese Telegram messages from a Meta/Facebook Ads admin.
Return JSON only through the provided schema.
Do not execute actions. Extract intent and parameters.

Rules:
- If user asks how many campaigns are running, intent=count_active_campaigns.
- If user asks to turn on/bat/active/chay campaign, intent=activate_campaign.
- If user asks to turn off/tat/dung/pause campaign, intent=pause_campaign.
- If user asks which campaign is good/bad/winner/loser/compare result, intent=compare_performance.
- If user asks budget/ngan sach comparison, intent=compare_budget.
- If user asks to create/launch a campaign with page/post links, intent=create_full_funnel.
- If user asks to update, change, set, increase, or decrease budget (ngân sách) for a campaign or adset, intent=update_budget.
- campaign_query is a campaign id or name if present (or the target of budget changes).
- goal messages for tin nhan/inbox/messenger; conversions for chuyen doi/sale/don hang/purchase; leads for lead; traffic for traffic/truy cap.
- level defaults to campaign unless the user asks ad set/nhom quang cao or ad/quang cao.
- date_preset defaults to today, except comparative performance defaults to last_7_days if user asks best/winner without dates.
- wants_live is true only if the user explicitly says live/chay that/bat that/CONFIRM_LIVE.
- For create_full_funnel, missing_fields should include campaign_name, page_url, post_url, daily_budget when missing.
- Extract target age (min/max), genders (1=male, 2=female, default empty array for both), and locations (e.g. ['Ha Noi', 'Ho Chi Minh'] or ['VN']) from the query or message context. If not mentioned, default target_age_min=18, target_age_max=55, target_genders=[], target_locations=['VN'].
- Extract budget_value (as an integer in the primary currency unit, e.g. 200000 for 200k/200.000) and budget_object_type ('campaign' or 'adset'). If not mentioned, default budget_value=0, budget_object_type='campaign'.
"""


def openai_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def parse_intent(message: str, session_context: dict[str, Any] | None = None) -> dict[str, Any]:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    context = json.dumps(session_context or {}, ensure_ascii=False)[:2000]
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Session context JSON: {context}\n\nUser message: {message}"},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "telegram_ads_intent",
                "strict": True,
                "schema": INTENT_SCHEMA,
            },
        },
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)
