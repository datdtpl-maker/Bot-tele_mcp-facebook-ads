import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from google_sheets_logger import log_action
from supabase_store import load_session, save_session


load_dotenv()
ROOT = Path(__file__).resolve().parent


def allowed_user_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


def default_account() -> str | None:
    return os.getenv("DEFAULT_AD_ACCOUNT_ID")


def safe_mode() -> bool:
    return os.getenv("SAFE_MODE", "true").lower() == "true"


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)[:3500]


async def call_mcp_tool(name: str, arguments: dict[str, Any]) -> Any:
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "mcp_server.py")],
        env=dict(os.environ),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if getattr(result, "isError", False):
                raise RuntimeError(str(result.content))
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                return structured
            return [getattr(item, "text", str(item)) for item in result.content]


async def guard(update: Update) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    allowed = allowed_user_ids()
    if not user_id or (allowed and user_id not in allowed):
        if update.message:
            await update.message.reply_text("Khong co quyen dung bot nay.")
        return False
    return True


async def reply_and_log(update: Update, command: str, payload: dict[str, Any], result: Any) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    log_action(user_id, chat_id, command, payload, result)
    context = load_session(user_id)
    context.update({"last_command": command, "last_payload": payload, "last_result": result})
    save_session(user_id, chat_id, context)
    await update.message.reply_text(compact_json(result))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    mode = "TEST/DRY-RUN" if safe_mode() else "LIVE"
    await update.message.reply_text(
        f"Mode: {mode}\n"
        "Lenh: /accounts, /campaigns [act_id], /adsets campaign_id, /ads parent_id,\n"
        "/status campaign|adset|ad object_id,\n"
        "/insights [act_id] YYYY-MM-DD YYYY-MM-DD [campaign|adset|ad],\n"
        "/analyze [act_id] YYYY-MM-DD YYYY-MM-DD [messages|conversions|leads] [campaign|adset|ad],\n"
        '/draft_campaign act_id messages|conversions|leads|traffic "Ten camp",\n'
        '/create_campaign act_id messages|conversions|leads|traffic "Ten camp" CONFIRM_LIVE,\n'
        "/pause campaign_id CONFIRM, /activate campaign_id CONFIRM"
    )


async def accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    payload = {"limit": 25}
    result = await call_mcp_tool("list_ad_accounts", payload)
    await reply_and_log(update, "accounts", payload, result)


async def campaigns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    ad_account_id = context.args[0] if context.args else default_account()
    if not ad_account_id:
        await update.message.reply_text("Thieu ad account id, vi du: /campaigns act_123")
        return
    payload = {"ad_account_id": ad_account_id, "limit": 25}
    result = await call_mcp_tool("list_campaigns", payload)
    await reply_and_log(update, "campaigns", payload, result)


async def adsets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if not context.args:
        await update.message.reply_text("Vi du: /adsets campaign_id")
        return
    payload = {"campaign_id": context.args[0], "limit": 25}
    result = await call_mcp_tool("list_adsets", payload)
    await reply_and_log(update, "adsets", payload, result)


async def ads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if not context.args:
        await update.message.reply_text("Vi du: /ads campaign_or_adset_id")
        return
    payload = {"parent_id": context.args[0], "limit": 25}
    result = await call_mcp_tool("list_ads", payload)
    await reply_and_log(update, "ads", payload, result)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Vi du: /status campaign 120000000000000")
        return
    payload = {"object_type": context.args[0], "object_id": context.args[1]}
    result = await call_mcp_tool("get_status", payload)
    await reply_and_log(update, "status", payload, result)


def parse_account_dates(args: list[str]) -> tuple[str | None, str | None, str | None, list[str]]:
    if len(args) >= 2 and default_account() and not args[0].startswith("act_"):
        return default_account(), args[0], args[1], args[2:]
    if len(args) >= 3:
        return args[0], args[1], args[2], args[3:]
    return None, None, None, []


async def insights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    ad_account_id, since, until, rest = parse_account_dates(context.args)
    if not ad_account_id or not since or not until:
        await update.message.reply_text("Vi du: /insights act_123 2026-05-01 2026-05-28 campaign")
        return
    level = rest[0] if rest else "campaign"
    payload = {"ad_account_id": ad_account_id, "since": since, "until": until, "level": level, "limit": 50}
    result = await call_mcp_tool("get_insights", payload)
    await reply_and_log(update, "insights", payload, result)


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    ad_account_id, since, until, rest = parse_account_dates(context.args)
    if not ad_account_id or not since or not until:
        await update.message.reply_text("Vi du: /analyze act_123 2026-05-01 2026-05-28 conversions campaign")
        return
    goal = rest[0] if rest else "conversions"
    level = rest[1] if len(rest) > 1 else "campaign"
    payload = {
        "ad_account_id": ad_account_id,
        "since": since,
        "until": until,
        "level": level,
        "goal": goal,
        "limit": 100,
    }
    result = await call_mcp_tool("analyze_performance", payload)
    await reply_and_log(update, "analyze", payload, result)


async def draft_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    try:
        parts = shlex.split(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"Sai cu phap: {exc}")
        return
    if len(parts) < 4:
        await update.message.reply_text('/draft_campaign act_123 messages|conversions|leads|traffic "Ten camp"')
        return
    payload = {"ad_account_id": parts[1], "goal": parts[2], "name": parts[3]}
    result = await call_mcp_tool("preview_campaign", payload)
    await reply_and_log(update, "draft_campaign", payload, result)


async def create_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    try:
        parts = shlex.split(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"Sai cu phap: {exc}")
        return
    if len(parts) < 5:
        await update.message.reply_text('/create_campaign act_123 messages|conversions|leads|traffic "Ten camp" CONFIRM_LIVE')
        return
    payload = {"ad_account_id": parts[1], "goal": parts[2], "name": parts[3]}
    if safe_mode() or parts[4] != "CONFIRM_LIVE":
        result = await call_mcp_tool("preview_campaign", payload)
        result["blocked_live_reason"] = "SAFE_MODE=true or missing CONFIRM_LIVE. No campaign was created."
        await reply_and_log(update, "create_campaign_dry_run", payload, result)
        return
    result = await call_mcp_tool("create_campaign_for_goal", payload)
    await reply_and_log(update, "create_campaign_live", payload, result)


async def set_status(update: Update, context: ContextTypes.DEFAULT_TYPE, new_status: str) -> None:
    if not await guard(update):
        return
    if len(context.args) < 2 or context.args[1] != "CONFIRM":
        await update.message.reply_text(f"Xac nhan bang: /{update.message.text.split()[0][1:]} campaign_id CONFIRM")
        return
    payload = {"campaign_id": context.args[0], "status": new_status}
    if safe_mode() and new_status == "ACTIVE":
        result = {"dry_run": True, "blocked_live_reason": "SAFE_MODE=true", **payload}
        await reply_and_log(update, "activate_dry_run", payload, result)
        return
    result = await call_mcp_tool("set_campaign_status", payload)
    await reply_and_log(update, new_status.lower(), payload, result)


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_status(update, context, "PAUSED")


async def activate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_status(update, context, "ACTIVE")


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("accounts", accounts))
    app.add_handler(CommandHandler("campaigns", campaigns))
    app.add_handler(CommandHandler("adsets", adsets))
    app.add_handler(CommandHandler("ads", ads))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("insights", insights))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("compare", analyze))
    app.add_handler(CommandHandler("draft_campaign", draft_campaign))
    app.add_handler(CommandHandler("create_campaign", create_campaign))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("activate", activate))
    app.run_polling()


if __name__ == "__main__":
    main()
