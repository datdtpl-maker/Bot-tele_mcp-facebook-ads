from __future__ import annotations

from typing import Any


MESSAGE_ACTIONS = {
    "onsite_conversion.messaging_conversation_started_7d",
    "messaging_conversation_started",
    "onsite_conversion.messaging_first_reply",
}
PURCHASE_ACTIONS = {"purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"}
LEAD_ACTIONS = {"lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"}


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _sum_actions(row: dict[str, Any], action_types: set[str], value_key: str = "value") -> float:
    total = 0.0
    for action in row.get("actions", []) or []:
        if action.get("action_type") in action_types:
            total += _float(action.get(value_key))
    return total


def _sum_action_values(row: dict[str, Any], action_types: set[str]) -> float:
    total = 0.0
    for action in row.get("action_values", []) or []:
        if action.get("action_type") in action_types:
            total += _float(action.get("value"))
    return total


def normalize_insight(row: dict[str, Any]) -> dict[str, Any]:
    spend = _float(row.get("spend"))
    purchases = _sum_actions(row, PURCHASE_ACTIONS)
    leads = _sum_actions(row, LEAD_ACTIONS)
    messages = _sum_actions(row, MESSAGE_ACTIONS)
    revenue = _sum_action_values(row, PURCHASE_ACTIONS)

    return {
        "campaign_id": row.get("campaign_id"),
        "campaign_name": row.get("campaign_name"),
        "adset_id": row.get("adset_id"),
        "adset_name": row.get("adset_name"),
        "ad_id": row.get("ad_id"),
        "ad_name": row.get("ad_name"),
        "spend": spend,
        "impressions": _float(row.get("impressions")),
        "clicks": _float(row.get("clicks")),
        "ctr": _float(row.get("ctr")),
        "cpc": _float(row.get("cpc")),
        "cpm": _float(row.get("cpm")),
        "purchases": purchases,
        "leads": leads,
        "messages": messages,
        "revenue": revenue,
        "roas": revenue / spend if spend else 0,
        "cost_per_purchase": spend / purchases if purchases else None,
        "cost_per_lead": spend / leads if leads else None,
        "cost_per_message": spend / messages if messages else None,
    }


def score_row(row: dict[str, Any], goal: str) -> float:
    spend = row["spend"]
    if spend <= 0:
        return -1
    if goal == "messages":
        return (row["messages"] * 1000) / spend
    if goal == "leads":
        return (row["leads"] * 1000) / spend
    return row["roas"] * 100 + (row["purchases"] * 10) - row["cpc"]


def analyze_insights(rows: list[dict[str, Any]], goal: str = "conversions") -> dict[str, Any]:
    normalized = [normalize_insight(row) for row in rows]
    ranked = sorted(normalized, key=lambda item: score_row(item, goal), reverse=True)
    winners = [row for row in ranked if score_row(row, goal) > 0][:5]
    losers = [row for row in reversed(ranked) if row["spend"] > 0][:5]
    total_spend = sum(row["spend"] for row in normalized)
    total_revenue = sum(row["revenue"] for row in normalized)

    return {
        "goal": goal,
        "summary": {
            "items": len(normalized),
            "spend": round(total_spend, 2),
            "revenue": round(total_revenue, 2),
            "roas": round(total_revenue / total_spend, 4) if total_spend else 0,
            "messages": sum(row["messages"] for row in normalized),
            "leads": sum(row["leads"] for row in normalized),
            "purchases": sum(row["purchases"] for row in normalized),
        },
        "best": winners[0] if winners else None,
        "winners": winners,
        "losers": losers,
    }
