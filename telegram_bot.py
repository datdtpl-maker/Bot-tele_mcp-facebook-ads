import json
import os
import re
import shlex
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

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


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def today_range() -> tuple[str, str]:
    today = datetime.now(ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Bangkok"))).date()
    return today.isoformat(), today.isoformat()


def last_days_range(days: int) -> tuple[str, str]:
    today = datetime.now(ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Bangkok"))).date()
    since = today - timedelta(days=days - 1)
    return since.isoformat(), today.isoformat()


def money_minor_to_text(value: int | float | str | None) -> str:
    amount = int(value or 0)
    if amount == 0:
        return "0"
    return f"{amount / 100:,.2f}"


def campaign_lines(campaigns: list[dict[str, Any]], max_items: int = 10) -> str:
    if not campaigns:
        return "Khong co campaign nao."
    lines = []
    for item in campaigns[:max_items]:
        lines.append(
            f"- {item.get('name')} | ID: {item.get('id')} | status: {item.get('status')} | effective: {item.get('effective_status')}"
        )
    return "\n".join(lines)


def format_active_campaigns(result: dict[str, Any]) -> str:
    return (
        f"Hom nay co {result.get('count', 0)} chien dich dang chay.\n"
        f"{campaign_lines(result.get('campaigns', []))}"
    )


def format_budget_report(result: dict[str, Any]) -> str:
    rows = result.get("data", [])[:10]
    if not rows:
        return "Chua co du lieu ngan sach de so sanh."
    lines = ["Top campaign theo ngan sach:"]
    for row in rows:
        daily = row["campaign_daily_budget"] + row["adset_daily_budget"]
        lifetime = row["campaign_lifetime_budget"] + row["adset_lifetime_budget"]
        lines.append(
            f"- {row['campaign_name']} | ID: {row['campaign_id']} | daily: {money_minor_to_text(daily)} | lifetime: {money_minor_to_text(lifetime)} | adsets: {row['adset_count']}"
        )
    return "\n".join(lines)


def format_analysis(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    best = result.get("best")
    lines = [
        f"Tong quan {result.get('goal')}: spend={summary.get('spend')}, revenue={summary.get('revenue')}, ROAS={summary.get('roas')}, messages={summary.get('messages')}, leads={summary.get('leads')}, purchases={summary.get('purchases')}"
    ]
    if best:
        lines.append(
            f"Tot nhat: {best.get('campaign_name') or best.get('adset_name') or best.get('ad_name')} | ID: {best.get('campaign_id') or best.get('adset_id') or best.get('ad_id')} | spend={best.get('spend')} | ROAS={best.get('roas')} | messages={best.get('messages')} | leads={best.get('leads')} | purchases={best.get('purchases')}"
        )
    winners = result.get("winners", [])[:3]
    losers = result.get("losers", [])[:3]
    if winners:
        lines.append("Winner:")
        for item in winners:
            lines.append(f"- {item.get('campaign_name') or item.get('adset_name') or item.get('ad_name')} | spend={item.get('spend')} | ROAS={item.get('roas')}")
    if losers:
        lines.append("Loser can xem lai:")
        for item in losers:
            lines.append(f"- {item.get('campaign_name') or item.get('adset_name') or item.get('ad_name')} | spend={item.get('spend')} | ROAS={item.get('roas')}")
    return "\n".join(lines)


def extract_account_id(text: str) -> str | None:
    match = re.search(r"\bact_\d+\b", text)
    return match.group(0) if match else default_account()


def extract_goal(normalized: str) -> str:
    if "tin nhan" in normalized or "message" in normalized:
        return "messages"
    if "lead" in normalized:
        return "leads"
    if "traffic" in normalized or "truy cap" in normalized:
        return "traffic"
    return "conversions"


def extract_level(normalized: str) -> str:
    if "quang cao" in normalized or " ad " in f" {normalized} ":
        return "ad"
    if "nhom" in normalized or "adset" in normalized or "ad set" in normalized:
        return "adset"
    return "campaign"


def extract_campaign_query(text: str, normalized: str) -> str | None:
    id_match = re.search(r"\b\d{8,}\b", text)
    if id_match:
        return id_match.group(0)
    quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', text)
    if quoted:
        return next(part for pair in quoted for part in pair if part)
    for marker in ["chien dich", "campaign", "camp"]:
        idx = normalized.find(marker)
        if idx >= 0:
            tail = text[idx + len(marker):].strip(" :,-")
            tail = re.sub(r"\b(confirm|xac nhan|di|nay|do)\b", "", tail, flags=re.IGNORECASE).strip(" :,-")
            return tail or None
    return None


def extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://\S+", text)


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


async def text_reply_and_log(update: Update, command: str, payload: dict[str, Any], result: Any, text: str) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    log_action(user_id, chat_id, command, payload, result)
    session = load_session(user_id)
    session.update({"last_command": command, "last_payload": payload, "last_result": result})
    save_session(user_id, chat_id, session)
    await update.message.reply_text(text[:3900])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    mode = "TEST/DRY-RUN" if safe_mode() else "LIVE"
    await update.message.reply_text(
        f"Mode: {mode}\n"
        "Ban co the nhan nhu dang noi voi tro ly that, vi du:\n"
        "- Hom nay co bao nhieu chien dich dang chay?\n"
        "- Bat chien dich \"Ten camp\"\n"
        "- Tat campaign 120000000000000\n"
        "- So sanh camp nao tot nhat 7 ngay qua\n"
        "- So sanh ngan sach chien dich\n"
        "- Tao chien dich tin nhan ten \"Camp A\" page <link_page> bai viet <link_post>\n\n"
        "Lenh cu van dung duoc: /accounts, /campaigns [act_id], /adsets campaign_id, /ads parent_id,\n"
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


async def natural_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    text = update.message.text or ""
    normalized = normalize_text(text)
    ad_account_id = extract_account_id(text)

    if not ad_account_id:
        await update.message.reply_text("Ban chua cau hinh DEFAULT_AD_ACCOUNT_ID hoac chua nhap act_... trong tin nhan.")
        return

    try:
        if ("bao nhieu" in normalized or "may" in normalized) and "dang chay" in normalized:
            payload = {"ad_account_id": ad_account_id, "limit": 100}
            result = await call_mcp_tool("count_active_campaigns", payload)
            await text_reply_and_log(update, "natural_active_campaigns", payload, result, format_active_campaigns(result))
            return

        if "ngan sach" in normalized and ("so sanh" in normalized or "campaign" in normalized or "chien dich" in normalized):
            payload = {"ad_account_id": ad_account_id, "limit": 50}
            result = await call_mcp_tool("campaign_budget_report", payload)
            await text_reply_and_log(update, "natural_budget_report", payload, result, format_budget_report(result))
            return

        if "so sanh" in normalized or "camp nao tot" in normalized or "tot nhat" in normalized or "winner" in normalized:
            since, until = last_days_range(7 if "7 ngay" in normalized or "tuan" in normalized else 1)
            payload = {
                "ad_account_id": ad_account_id,
                "since": since,
                "until": until,
                "level": extract_level(normalized),
                "goal": extract_goal(normalized),
                "limit": 100,
            }
            result = await call_mcp_tool("analyze_performance", payload)
            await text_reply_and_log(update, "natural_analyze", payload, result, format_analysis(result))
            return

        if normalized.startswith("bat ") or " bat chien dich" in normalized or " active " in f" {normalized} ":
            query = extract_campaign_query(text, normalized)
            if not query:
                await update.message.reply_text('Ban muon bat campaign nao? Gui ten trong ngoac kep, vi du: bat chien dich "Camp A"')
                return
            found_payload = {"ad_account_id": ad_account_id, "query": query, "limit": 100}
            found = await call_mcp_tool("find_campaign", found_payload)
            match = found.get("match")
            if not match:
                await text_reply_and_log(
                    update,
                    "natural_activate_find",
                    found_payload,
                    found,
                    "Minh chua tim duoc dung 1 campaign. Cac campaign gan dung:\n" + campaign_lines(found.get("candidates", [])),
                )
                return
            payload = {"campaign_id": match["id"], "status": "ACTIVE"}
            if safe_mode():
                result = {"dry_run": True, "campaign": match, "blocked_live_reason": "SAFE_MODE=true"}
                await text_reply_and_log(
                    update,
                    "natural_activate_dry_run",
                    payload,
                    result,
                    f"SAFE_MODE dang bat nen minh chua bat that.\nCampaign: {match.get('name')} | ID: {match.get('id')}\nMuon chay that: dat SAFE_MODE=false roi gui /activate {match.get('id')} CONFIRM",
                )
                return
            result = await call_mcp_tool("set_campaign_status", payload)
            await text_reply_and_log(update, "natural_activate", payload, result, f"Da bat campaign: {match.get('name')} | ID: {match.get('id')}")
            return

        if normalized.startswith("tat ") or normalized.startswith("dung ") or " pause " in f" {normalized} ":
            query = extract_campaign_query(text, normalized)
            if not query:
                await update.message.reply_text('Ban muon tat campaign nao? Gui ten trong ngoac kep, vi du: tat chien dich "Camp A"')
                return
            found_payload = {"ad_account_id": ad_account_id, "query": query, "limit": 100}
            found = await call_mcp_tool("find_campaign", found_payload)
            match = found.get("match")
            if not match:
                await text_reply_and_log(
                    update,
                    "natural_pause_find",
                    found_payload,
                    found,
                    "Minh chua tim duoc dung 1 campaign. Cac campaign gan dung:\n" + campaign_lines(found.get("candidates", [])),
                )
                return
            payload = {"campaign_id": match["id"], "status": "PAUSED"}
            result = await call_mcp_tool("set_campaign_status", payload)
            await text_reply_and_log(update, "natural_pause", payload, result, f"Da tat campaign: {match.get('name')} | ID: {match.get('id')}")
            return

        if "tao" in normalized and ("chien dich" in normalized or "campaign" in normalized or "camp" in normalized):
            urls = extract_urls(text)
            name_match = re.search(r'ten\s+["\']([^"\']+)["\']', text, flags=re.IGNORECASE)
            if len(urls) < 2 or not name_match:
                await update.message.reply_text(
                    'Cu phap de tao bang tin nhan:\n'
                    'Tao chien dich tin nhan ten "Camp A" act_123 page https://facebook.com/page bai viet https://facebook.com/page/posts/123\n\n'
                    "Bat buoc: tai khoan/token phai co quyen truy cap Page. Bot se kiem tra Page truoc va mac dinh chi dry-run."
                )
                return
            payload = {
                "ad_account_id": ad_account_id,
                "goal": extract_goal(normalized),
                "name": name_match.group(1),
                "page_url": urls[0],
                "post_url": urls[1],
            }
            result = await call_mcp_tool("preview_post_campaign", payload)
            await text_reply_and_log(
                update,
                "natural_preview_post_campaign",
                payload,
                result,
                "Minh da kiem tra duoc Page va tao ban nhap an toan:\n"
                f"- Ten: {result.get('name')}\n"
                f"- Goal: {result.get('goal')}\n"
                f"- Objective: {result.get('objective')}\n"
                f"- Page: {result.get('page_access', {}).get('page_name')} | ID: {result.get('page_access', {}).get('page_id')}\n"
                f"- Bai viet: {result.get('post_url')}\n"
                "- Trang thai: dry-run, chua tao/chua tieu tien.",
            )
            return

        if "trang thai" in normalized:
            query = extract_campaign_query(text, normalized)
            if not query:
                await update.message.reply_text('Ban muon xem trang thai campaign nao? Vi du: trang thai chien dich "Camp A"')
                return
            found_payload = {"ad_account_id": ad_account_id, "query": query, "limit": 100}
            found = await call_mcp_tool("find_campaign", found_payload)
            match = found.get("match")
            if not match:
                await text_reply_and_log(update, "natural_status_find", found_payload, found, "Cac campaign gan dung:\n" + campaign_lines(found.get("candidates", [])))
                return
            await text_reply_and_log(
                update,
                "natural_status",
                found_payload,
                match,
                f"Campaign: {match.get('name')}\nID: {match.get('id')}\nStatus: {match.get('status')}\nEffective: {match.get('effective_status')}\nObjective: {match.get('objective')}",
            )
            return

        await update.message.reply_text(
            "Minh chua hieu y nay. Ban co the hoi:\n"
            "- Hom nay co bao nhieu chien dich dang chay?\n"
            "- So sanh camp nao tot nhat 7 ngay qua\n"
            "- So sanh ngan sach chien dich\n"
            '- Bat/Tat chien dich "Ten camp"\n'
            '- Tao chien dich tin nhan ten "Camp A" act_123 page <link_page> bai viet <link_post>'
        )
    except Exception as exc:
        await update.message.reply_text(f"Khong thuc hien duoc: {exc}")


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, natural_message))
    app.run_polling()


if __name__ == "__main__":
    main()
