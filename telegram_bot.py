import asyncio
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

from ai_intent import openai_enabled, parse_intent
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


def default_daily_budget() -> int:
    return int(os.getenv("DEFAULT_DAILY_BUDGET", "100000"))


def default_targeting() -> dict[str, Any]:
    return json.loads(os.getenv("DEFAULT_TARGETING", '{"geo_locations":{"countries":["VN"]},"age_min":18,"age_max":55}'))


CITY_KEYS = {
    "ha noi": "2580556",
    "hanoi": "2580556",
    "hn": "2580556",
    "ho chi minh": "2583802",
    "hcm": "2583802",
    "sai gon": "2583802",
    "saigon": "2583802",
    "da nang": "2575168",
    "danang": "2575168",
    "dn": "2575168"
}


def build_targeting(intent: dict[str, Any]) -> dict[str, Any]:
    targeting = {}
    locations = intent.get("target_locations") or []
    cities_to_add = []
    countries_to_add = []
    
    for loc in locations:
        loc_clean = normalize_text(loc).strip()
        if loc_clean in CITY_KEYS:
            cities_to_add.append({
                "key": CITY_KEYS[loc_clean],
                "radius": 40,
                "distance_unit": "kilometer"
            })
        elif loc_clean in {"vn", "viet nam", "vietnam"}:
            countries_to_add.append("VN")
            
    geo_locations = {}
    if cities_to_add:
        geo_locations["cities"] = cities_to_add
    if countries_to_add or not geo_locations:
        geo_locations["countries"] = ["VN"]
        
    targeting["geo_locations"] = geo_locations
    
    age_min = intent.get("target_age_min")
    targeting["age_min"] = age_min if (age_min is not None and age_min > 0) else 18
    
    age_max = intent.get("target_age_max")
    targeting["age_max"] = age_max if (age_max is not None and age_max > 0) else 55
    
    genders = intent.get("target_genders") or []
    if genders:
        targeting["genders"] = genders
        
    return targeting


def default_pixel_id() -> str | None:
    return os.getenv("DEFAULT_PIXEL_ID") or None


def default_conversion_event() -> str | None:
    return os.getenv("DEFAULT_CONVERSION_EVENT") or None


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


def range_from_intent(intent: dict[str, Any]) -> tuple[str, str]:
    preset = intent.get("date_preset") or "today"
    if preset == "last_30_days":
        return last_days_range(30)
    if preset == "last_7_days":
        return last_days_range(7)
    if preset == "yesterday":
        day = datetime.now(ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Bangkok"))).date() - timedelta(days=1)
        return day.isoformat(), day.isoformat()
    if preset == "custom" and intent.get("since") and intent.get("until"):
        return intent["since"], intent["until"]
    return today_range()


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


def format_accounts(result: dict[str, Any]) -> str:
    data = result.get("data", [])
    if not data:
        return "Không tìm thấy tài khoản quảng cáo nào."
    lines = ["📋 Danh sách tài khoản quảng cáo:"]
    for idx, acc in enumerate(data, 1):
        status_emoji = "🟢" if acc.get("account_status") == 1 else "🔴"
        lines.append(
            f"{idx}. {status_emoji} {acc.get('name')}\n"
            f"   - ID: {acc.get('id')}\n"
            f"   - Tiền tệ: {acc.get('currency')} | Múi giờ: {acc.get('timezone_name')}"
        )
    return "\n\n".join(lines)


def format_campaigns_list(result: dict[str, Any]) -> str:
    data = result.get("data", [])
    if not data:
        return "Không tìm thấy chiến dịch nào."
    lines = ["📂 Danh sách chiến dịch:"]
    for idx, camp in enumerate(data[:15], 1):
        status = camp.get("status")
        eff_status = camp.get("effective_status")
        status_emoji = "🟢" if eff_status == "ACTIVE" or status == "ACTIVE" else "🔴"
        
        budget_str = "Không đặt"
        if camp.get("daily_budget"):
            budget_str = f"{money_minor_to_text(camp.get('daily_budget'))} daily"
        elif camp.get("lifetime_budget"):
            budget_str = f"{money_minor_to_text(camp.get('lifetime_budget'))} lifetime"
            
        lines.append(
            f"{idx}. {status_emoji} {camp.get('name')}\n"
            f"   - ID: {camp.get('id')}\n"
            f"   - Mục tiêu: {camp.get('objective')}\n"
            f"   - Ngân sách: {budget_str}\n"
            f"   - Trạng thái: {eff_status}"
        )
    return "\n\n".join(lines)


def format_adsets_list(result: dict[str, Any]) -> str:
    data = result.get("data", [])
    if not data:
        return "Không tìm thấy nhóm quảng cáo nào."
    lines = ["📦 Danh sách nhóm quảng cáo:"]
    for idx, adset in enumerate(data[:15], 1):
        status = adset.get("status")
        eff_status = adset.get("effective_status")
        status_emoji = "🟢" if eff_status == "ACTIVE" or status == "ACTIVE" else "🔴"
        
        budget_str = "Không đặt"
        if adset.get("daily_budget"):
            budget_str = f"{money_minor_to_text(adset.get('daily_budget'))} daily"
        elif adset.get("lifetime_budget"):
            budget_str = f"{money_minor_to_text(adset.get('lifetime_budget'))} lifetime"
            
        lines.append(
            f"{idx}. {status_emoji} {adset.get('name')}\n"
            f"   - ID: {adset.get('id')}\n"
            f"   - Tối ưu: {adset.get('optimization_goal')} | Trả phí theo: {adset.get('billing_event')}\n"
            f"   - Ngân sách: {budget_str}\n"
            f"   - Trạng thái: {eff_status}"
        )
    return "\n\n".join(lines)


def format_ads_list(result: dict[str, Any]) -> str:
    data = result.get("data", [])
    if not data:
        return "Không tìm thấy quảng cáo nào."
    lines = ["🖼️ Danh sách quảng cáo:"]
    for idx, ad in enumerate(data[:15], 1):
        status = ad.get("status")
        eff_status = ad.get("effective_status")
        status_emoji = "🟢" if eff_status == "ACTIVE" or status == "ACTIVE" else "🔴"
        
        lines.append(
            f"{idx}. {status_emoji} {ad.get('name')}\n"
            f"   - ID: {ad.get('id')}\n"
            f"   - Trạng thái: {eff_status}"
        )
    return "\n\n".join(lines)


def format_status(result: dict[str, Any]) -> str:
    if not result:
        return "Không tìm thấy thông tin đối tượng."
    status = result.get("status")
    eff_status = result.get("effective_status")
    status_emoji = "🟢" if eff_status == "ACTIVE" or status == "ACTIVE" else "🔴"
    
    lines = [
        f"🔍 Thông tin chi tiết ({result.get('name')}):",
        f"- ID: {result.get('id')}",
        f"- Trạng thái: {status_emoji} {status}",
        f"- Trạng thái hiệu lực: {eff_status}",
    ]
    if "objective" in result:
        lines.append(f"- Mục tiêu chiến dịch: {result.get('objective')}")
    if "daily_budget" in result:
        daily = result.get("daily_budget")
        lifetime = result.get("lifetime_budget")
        budget_str = f"{money_minor_to_text(daily)} daily" if daily else (f"{money_minor_to_text(lifetime)} lifetime" if lifetime else "Không đặt")
        lines.append(f"- Ngân sách nhóm: {budget_str}")
        lines.append(f"- Mục tiêu tối ưu: {result.get('optimization_goal')}")
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
    await asyncio.to_thread(log_action, user_id, chat_id, command, payload, result)
    context = await asyncio.to_thread(load_session, user_id)
    context.update({"last_command": command, "last_payload": payload, "last_result": result})
    await asyncio.to_thread(save_session, user_id, chat_id, context)
    await update.message.reply_text(compact_json(result))


async def text_reply_and_log(
    update: Update,
    command: str,
    payload: dict[str, Any],
    result: Any,
    text: str,
    reply_markup: Any = None,
) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    await asyncio.to_thread(log_action, user_id, chat_id, command, payload, result)
    session = await asyncio.to_thread(load_session, user_id)
    session.update({"last_command": command, "last_payload": payload, "last_result": result})
    await asyncio.to_thread(save_session, user_id, chat_id, session)
    await update.message.reply_text(text[:3900], reply_markup=reply_markup)


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
    formatted = format_accounts(result)
    await text_reply_and_log(update, "accounts", payload, result, formatted)


async def campaigns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    ad_account_id = context.args[0] if context.args else default_account()
    if not ad_account_id:
        await update.message.reply_text("Thieu ad account id, vi du: /campaigns act_123")
        return
    payload = {"ad_account_id": ad_account_id, "limit": 25}
    result = await call_mcp_tool("list_campaigns", payload)
    formatted = format_campaigns_list(result)
    await text_reply_and_log(update, "campaigns", payload, result, formatted)


async def adsets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if not context.args:
        await update.message.reply_text("Vi du: /adsets campaign_id")
        return
    payload = {"campaign_id": context.args[0], "limit": 25}
    result = await call_mcp_tool("list_adsets", payload)
    formatted = format_adsets_list(result)
    await text_reply_and_log(update, "adsets", payload, result, formatted)


async def ads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if not context.args:
        await update.message.reply_text("Vi du: /ads campaign_or_adset_id")
        return
    payload = {"parent_id": context.args[0], "limit": 25}
    result = await call_mcp_tool("list_ads", payload)
    formatted = format_ads_list(result)
    await text_reply_and_log(update, "ads", payload, result, formatted)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Vi du: /status campaign 120000000000000")
        return
    object_type = context.args[0]
    object_id = context.args[1]
    payload = {"object_type": object_type, "object_id": object_id}
    result = await call_mcp_tool("get_status", payload)
    formatted = format_status(result)
    
    reply_markup = None
    if object_type == "campaign" and "id" in result:
        campaign_id = result["id"]
        current_status = result.get("status")
        keyboard = []
        if current_status == "ACTIVE":
            keyboard.append([InlineKeyboardButton("🔴 Tạm dừng (PAUSE)", callback_data=f"pause_{campaign_id}")])
        else:
            keyboard.append([InlineKeyboardButton("🟢 Kích hoạt (ACTIVE)", callback_data=f"activate_{campaign_id}")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
    await text_reply_and_log(update, "status", payload, result, formatted, reply_markup=reply_markup)


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


async def handle_ai_intent(update: Update, intent: dict[str, Any]) -> bool:
    ad_account_id = intent.get("ad_account_id") or default_account()
    if not ad_account_id:
        await update.message.reply_text("Ban chua cau hinh DEFAULT_AD_ACCOUNT_ID hoac chua nhap act_... trong tin nhan.")
        return True

    name = intent.get("intent")
    if name == "count_active_campaigns":
        payload = {"ad_account_id": ad_account_id, "limit": 100}
        result = await call_mcp_tool("count_active_campaigns", payload)
        await text_reply_and_log(update, "ai_active_campaigns", payload, result, format_active_campaigns(result))
        return True

    if name == "compare_budget":
        payload = {"ad_account_id": ad_account_id, "limit": 50}
        result = await call_mcp_tool("campaign_budget_report", payload)
        await text_reply_and_log(update, "ai_budget_report", payload, result, format_budget_report(result))
        return True

    if name == "compare_performance":
        since, until = range_from_intent(intent)
        payload = {
            "ad_account_id": ad_account_id,
            "since": since,
            "until": until,
            "level": intent.get("level") or "campaign",
            "goal": intent.get("goal") or "conversions",
            "limit": 100,
        }
        result = await call_mcp_tool("analyze_performance", payload)
        await text_reply_and_log(update, "ai_analyze", payload, result, format_analysis(result))
        return True

    if name in {"activate_campaign", "pause_campaign", "status"}:
        query = intent.get("campaign_query")
        if not query:
            await update.message.reply_text("Ban muon thao tac campaign nao? Hay gui ten hoac ID campaign.")
            return True
        found_payload = {"ad_account_id": ad_account_id, "query": query, "limit": 100}
        found = await call_mcp_tool("find_campaign", found_payload)
        match = found.get("match")
        if not match:
            await text_reply_and_log(
                update,
                f"ai_{name}_find",
                found_payload,
                found,
                "Minh chua tim duoc dung 1 campaign. Cac campaign gan dung:\n" + campaign_lines(found.get("candidates", [])),
            )
            return True
        if name == "status":
            campaign_id = match.get("id")
            current_status = match.get("status")
            keyboard = []
            if current_status == "ACTIVE":
                keyboard.append([InlineKeyboardButton("🔴 Tạm dừng (PAUSE)", callback_data=f"pause_{campaign_id}")])
            else:
                keyboard.append([InlineKeyboardButton("🟢 Kích hoạt (ACTIVE)", callback_data=f"activate_{campaign_id}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await text_reply_and_log(
                update,
                "ai_status",
                found_payload,
                match,
                f"Campaign: {match.get('name')}\nID: {match.get('id')}\nStatus: {match.get('status')}\nEffective: {match.get('effective_status')}\nObjective: {match.get('objective')}",
                reply_markup=reply_markup,
            )
            return True
        new_status = "ACTIVE" if name == "activate_campaign" else "PAUSED"
        payload = {"campaign_id": match["id"], "status": new_status}
        if safe_mode() and new_status == "ACTIVE":
            result = {"dry_run": True, "campaign": match, "blocked_live_reason": "SAFE_MODE=true"}
            await text_reply_and_log(
                update,
                "ai_activate_dry_run",
                payload,
                result,
                f"SAFE_MODE dang bat nen minh chua bat that.\nCampaign: {match.get('name')} | ID: {match.get('id')}\nMuon chay that: dat SAFE_MODE=false roi gui /activate {match.get('id')} CONFIRM",
            )
            return True
        result = await call_mcp_tool("set_campaign_status", payload)
        verb = "bat" if new_status == "ACTIVE" else "tat"
        await text_reply_and_log(update, f"ai_{verb}", payload, result, f"Da {verb} campaign: {match.get('name')} | ID: {match.get('id')}")
        return True

    if name == "create_full_funnel":
        missing = list(intent.get("missing_fields") or [])
        required_missing = [
            field
            for field in ["campaign_name", "page_url", "post_url"]
            if not intent.get(field)
        ]
        missing = sorted(set(missing + required_missing))
        if missing:
            await update.message.reply_text(
                "De tao full funnel, ban bo sung: "
                + ", ".join(missing)
                + '\nVi du: Tao chien dich tin nhan ten "Camp A" act_123 page https://facebook.com/page bai viet https://facebook.com/page/posts/123 ngan sach 100000'
            )
            return True
        goal = intent.get("goal") or "messages"
        pixel_id = default_pixel_id()
        if goal == "conversions" and not pixel_id:
            await update.message.reply_text("Campaign chuyen doi can DEFAULT_PIXEL_ID trong .env hoac bo sung pixel_id trong code parser truoc khi tao live.")
            return True

        payload = {
            "ad_account_id": ad_account_id,
            "name": intent["campaign_name"],
            "goal": goal,
            "page_url": intent["page_url"],
            "post_url": intent["post_url"],
            "daily_budget": int(intent.get("daily_budget") or default_daily_budget()),
            "targeting": build_targeting(intent),
            "pixel_id": pixel_id,
            "conversion_event": default_conversion_event(),
        }

        if safe_mode() or not intent.get("wants_live"):
            result = await call_mcp_tool("preview_full_funnel", payload)
            
            keyboard = []
            if safe_mode():
                keyboard.append([InlineKeyboardButton("ℹ️ Safe Mode đang bật", callback_data="safe_mode_info")])
            else:
                keyboard.append([InlineKeyboardButton("🚀 Tạo thật (LIVE)", callback_data="create_live")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await text_reply_and_log(
                update,
                "ai_full_funnel_preview",
                payload,
                result,
                "Minh da lap ban nhap full funnel an toan, chua tao live:\n"
                f"- Campaign: {result['campaign']['name']} | objective: {result['campaign']['objective']}\n"
                f"- Ad set: daily_budget={result['adset']['daily_budget']} | optimization={result['adset']['optimization_goal']}\n"
                f"- Creative: object_story_id={result['creative']['object_story_id']}\n"
                f"- Ad: {result['ad']['name']}\n"
                f"- Page: {result['page_access']['page_name']} | ID: {result['page_access']['page_id']}\n"
                "Neu muon tao that: bam nut duoi day hoac nhan lai co chu live/CONFIRM_LIVE.",
                reply_markup=reply_markup,
            )
            return True
        result = await call_mcp_tool("create_full_funnel_paused", payload)
        await text_reply_and_log(
            update,
            "ai_full_funnel_live",
            payload,
            result,
            "Da tao full funnel o trang thai PAUSED:\n"
            f"- Campaign ID: {result['campaign'].get('id')}\n"
            f"- Ad set ID: {result['adset'].get('id')}\n"
            f"- Creative ID: {result['creative'].get('id')}\n"
            f"- Ad ID: {result['ad'].get('id')}\n"
            "Hay kiem tra trong Ads Manager truoc khi /activate campaign.",
        )
        return True

    if name == "update_budget":
        query = intent.get("campaign_query")
        budget_val = intent.get("budget_value") or 0
        obj_type = intent.get("budget_object_type") or "campaign"
        
        if not query:
            await update.message.reply_text("Ban muon cap nhat ngan sach cho campaign/adset nao? Hay gui ten hoac ID.")
            return True
        if budget_val <= 0:
            await update.message.reply_text("Vui long cung cap so tien ngan sach hop le.")
            return True
            
        found_payload = {"ad_account_id": ad_account_id, "query": query, "limit": 100}
        found = await call_mcp_tool("find_campaign", found_payload)
        match = found.get("match")
        if not match:
            await text_reply_and_log(
                update,
                "ai_update_budget_find",
                found_payload,
                found,
                "Minh chua tim duoc dung chien dich can doi ngan sach. Cac ung vien:\n" + campaign_lines(found.get("candidates", [])),
            )
            return True
            
        target_id = match["id"]
        target_name = match["name"]
        minor_budget = int(budget_val * 100)
        
        keyboard = [
            [
                InlineKeyboardButton("🚀 Xác nhận đổi ngân sách", callback_data=f"upbudget_{obj_type}_{target_id}_{minor_budget}"),
                InlineKeyboardButton("❌ Hủy", callback_data="cancel_action")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"Bạn có muốn cập nhật ngân sách cho chiến dịch:\n"
            f"- Tên: {target_name}\n"
            f"- ID: `{target_id}`\n"
            f"- Ngân sách mới: {money_minor_to_text(minor_budget)} VND ({budget_val:,} VND)\n\n"
            f"Nhấn nút dưới đây để xác nhận:",
            reply_markup=reply_markup,
        )
        return True

    if name == "help":
        await update.message.reply_text(
            "Ban co the hoi: hom nay co bao nhieu campaign dang chay, so sanh camp nao tot, so sanh ngan sach, bat/tat campaign, tao campaign tu link page + bai viet."
        )
        return True

    return False


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
        if openai_enabled():
            user_id = update.effective_user.id if update.effective_user else None
            session = await asyncio.to_thread(load_session, user_id)
            intent = parse_intent(text, session)
            handled = await handle_ai_intent(update, intent)
            if handled:
                return

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
                "daily_budget": default_daily_budget(),
                "targeting": default_targeting(),
                "pixel_id": default_pixel_id(),
                "conversion_event": default_conversion_event(),
            }
            if payload["goal"] == "conversions" and not payload["pixel_id"]:
                await update.message.reply_text("Campaign chuyen doi can DEFAULT_PIXEL_ID trong .env truoc khi tao full funnel.")
                return
            result = await call_mcp_tool("preview_full_funnel", payload)
            
            keyboard = []
            if safe_mode():
                keyboard.append([InlineKeyboardButton("ℹ️ Safe Mode đang bật", callback_data="safe_mode_info")])
            else:
                keyboard.append([InlineKeyboardButton("🚀 Tạo thật (LIVE)", callback_data="create_live")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await text_reply_and_log(
                update,
                "natural_preview_full_funnel",
                payload,
                result,
                "Minh da tao ban nhap full funnel an toan:\n"
                f"- Campaign: {result['campaign']['name']} | objective: {result['campaign']['objective']}\n"
                f"- Ad set: daily_budget={result['adset']['daily_budget']} | optimization={result['adset']['optimization_goal']}\n"
                f"- Creative: object_story_id={result['creative']['object_story_id']}\n"
                f"- Page: {result['page_access']['page_name']} | ID: {result['page_access']['page_id']}\n"
                "- Trang thai: dry-run, chua tao/chua tieu tien.",
                reply_markup=reply_markup,
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


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "safe_mode_info":
        await query.answer(
            text="Hãy chỉnh sửa SAFE_MODE=false trong file .env để cho phép kích hoạt/tạo thật.",
            show_alert=True
        )
        return

    if data.startswith("activate_") or data.startswith("pause_"):
        action, object_id = data.split("_", 1)
        new_status = "ACTIVE" if action == "activate" else "PAUSED"
        payload = {"campaign_id": object_id, "status": new_status}
        
        if safe_mode() and new_status == "ACTIVE":
            await query.answer(
                text="Không thể kích hoạt vì SAFE_MODE đang bật.",
                show_alert=True
            )
            return
            
        try:
            result = await call_mcp_tool("set_campaign_status", payload)
            verb = "Đã bật" if new_status == "ACTIVE" else "Đã tắt"
            await query.edit_message_text(
                text=f"{query.message.text}\n\n🔄 {verb} thành công chiến dịch (ID: {object_id})!"
            )
            await asyncio.to_thread(log_action, user_id, query.message.chat_id, new_status.lower(), payload, result)
        except Exception as exc:
            await query.edit_message_text(
                text=f"{query.message.text}\n\n❌ Lỗi khi thay đổi trạng thái: {exc}"
            )

    elif data == "create_live":
        session = await asyncio.to_thread(load_session, user_id)
        last_command = session.get("last_command")
        last_payload = session.get("last_payload")
        
        if not last_payload or last_command not in {"ai_full_funnel_preview", "natural_preview_full_funnel"}:
            await query.answer(
                text="Không tìm thấy phiên bản nháp phù hợp để tạo. Vui lòng gửi lại yêu cầu tạo.",
                show_alert=True
            )
            return
            
        try:
            await query.edit_message_text(
                text=f"{query.message.text}\n\n⏳ Đang tiến hành tạo chiến dịch live..."
            )
            result = await call_mcp_tool("create_full_funnel_paused", last_payload)
            success_text = (
                f"✅ Đã tạo full funnel ở trạng thái PAUSED thành công:\n"
                f"- Campaign ID: `{result['campaign'].get('id')}`\n"
                f"- Ad set ID: `{result['adset'].get('id')}`\n"
                f"- Creative ID: `{result['creative'].get('id')}`\n"
                f"- Ad ID: `{result['ad'].get('id')}`\n\n"
                f"Hãy kiểm tra trong Ads Manager trước khi kích hoạt chiến dịch."
            )
            await query.edit_message_text(text=success_text)
            await asyncio.to_thread(log_action, user_id, query.message.chat_id, "create_full_funnel_live", last_payload, result)
        except Exception as exc:
            await query.edit_message_text(
                text=f"{query.message.text}\n\n❌ Lỗi khi tạo chiến dịch live: {exc}"
            )

    elif data.startswith("upbudget_"):
        parts = data.split("_")
        if len(parts) >= 4:
            obj_type = parts[1]
            target_id = parts[2]
            minor_budget = int(parts[3])
            
            payload = {
                "object_type": obj_type,
                "object_id": target_id,
                "budget": minor_budget,
                "budget_type": "daily"
            }
            
            try:
                await query.edit_message_text(
                    text=f"{query.message.text}\n\n⏳ Đang tiến hành cập nhật ngân sách lên Meta Ads..."
                )
                result = await call_mcp_tool("update_budget", payload)
                await query.edit_message_text(
                    text=f"✅ Cập nhật ngân sách thành công cho {obj_type} (ID: {target_id})!\n"
                         f"• Ngân sách mới: {money_minor_to_text(minor_budget)} VND."
                )
                await asyncio.to_thread(log_action, user_id, query.message.chat_id, "update_budget", payload, result)
            except Exception as exc:
                await query.edit_message_text(
                    text=f"{query.message.text}\n\n❌ Lỗi khi cập nhật ngân sách: {exc}"
                )
                
    elif data == "cancel_action":
        await query.edit_message_text(text="🚫 Đã hủy thao tác.")


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
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling()


if __name__ == "__main__":
    main()
