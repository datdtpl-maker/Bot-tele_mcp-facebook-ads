from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests


def supabase_enabled() -> bool:
    return os.getenv("SUPABASE_ENABLED", "false").lower() == "true"


def _headers() -> dict[str, str]:
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }


def save_session(user_id: int | None, chat_id: int | None, context: dict[str, Any]) -> None:
    if not supabase_enabled() or user_id is None:
        return

    url = os.environ["SUPABASE_URL"].rstrip("/")
    table = os.getenv("SUPABASE_SESSION_TABLE", "bot_sessions")
    payload = {
        "user_id": str(user_id),
        "chat_id": str(chat_id or ""),
        "context": context,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    response = requests.post(
        f"{url}/rest/v1/{table}",
        headers=_headers(),
        params={"on_conflict": "user_id"},
        json=payload,
        timeout=20,
    )
    response.raise_for_status()


def load_session(user_id: int | None) -> dict[str, Any]:
    if not supabase_enabled() or user_id is None:
        return {}

    url = os.environ["SUPABASE_URL"].rstrip("/")
    table = os.getenv("SUPABASE_SESSION_TABLE", "bot_sessions")
    response = requests.get(
        f"{url}/rest/v1/{table}",
        headers=_headers(),
        params={"user_id": f"eq.{user_id}", "select": "context", "limit": "1"},
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0].get("context", {}) if rows else {}
