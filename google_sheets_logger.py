from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def sheets_enabled() -> bool:
    return os.getenv("GOOGLE_SHEETS_ENABLED", "false").lower() == "true"


def log_action(user_id: int | None, chat_id: int | None, command: str, payload: dict[str, Any], result: Any) -> None:
    if not sheets_enabled():
        return

    service_file = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    tab = os.getenv("GOOGLE_SHEET_TAB", "bot_logs")
    credentials = Credentials.from_service_account_file(service_file, scopes=SCOPES)
    service = build("sheets", "v4", credentials=credentials)

    row = [
        datetime.now(timezone.utc).isoformat(),
        str(user_id or ""),
        str(chat_id or ""),
        command,
        json.dumps(payload, ensure_ascii=False),
        json.dumps(result, ensure_ascii=False)[:4000],
    ]
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"{tab}!A:F",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()
