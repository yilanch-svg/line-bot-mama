"""
LINE Bot 主程式
媽媽的智慧助手 v1.1（天氣 + 生活問答 + 公車即時到站）
"""

import os
import pathlib
# 最優先載入 .env，必須在所有模組 import 之前
from dotenv import load_dotenv
env_path = pathlib.Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

import logging
from flask import Flask, request, abort, render_template, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from modules.intent import detect_intent
from modules.weather import get_weather_forecast
from modules.qa import get_qa_response
from modules.bus import get_bus_arrival, parse_bus_query, get_stop_options, get_route_variants
from modules.transit import get_directions, parse_transit_query, check_location_precision, search_places, LOCATION_ALIAS
from modules.notes import add_note, search_notes, delete_note, delete_last_note, parse_note_query
from modules.reminder import add_reminder, list_reminders, cancel_reminder, cancel_multi_reminders, cancel_all_reminders, parse_reminder, scheduler, load_reminders_from_db

# 設定 logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 啟動提醒排程器並從 DB 還原
scheduler.start()
load_reminders_from_db()

# LINE API 設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 對話記憶：key = LINE userId，value = 對話歷史 list
conversation_memory: dict[str, list] = {}

# 公車查詢暫存
bus_query_state: dict[str, dict] = {}
# 路線查詢暫存
transit_query_state: dict[str, dict] = {}
# QA 模式中的用戶
qa_mode_users: set[str] = set()


def _get_sb():
    from supabase import create_client
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SECRET_KEY"))


def _rss_get(user_id: str) -> dict | None:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        TZ = ZoneInfo("Asia/Taipei")
        row = _get_sb().table("reminder_setup_state").select("state").eq("user_id", user_id).execute().data
        if not row:
            return None
        state = row[0]["state"]
        for key in ("trigger_time", "prev_day_time", "same_day_time"):
            if state.get(key):
                state[key] = datetime.fromisoformat(state[key]).astimezone(TZ)
        return state
    except Exception as e:
        logger.error(f"rss_get failed: {e}")
        return None


def _rss_set(user_id: str, state: dict):
    try:
        from datetime import datetime, timezone
        serialized = {}
        for k, v in state.items():
            if hasattr(v, "isoformat"):
                serialized[k] = v.isoformat()
            else:
                serialized[k] = v
        _get_sb().table("reminder_setup_state").upsert(
            {"user_id": user_id, "state": serialized,
             "updated_at": datetime.now(timezone.utc).isoformat()}
        ).execute()
        logger.info(f"rss_set ok: user={user_id} step={state.get('step')}")
    except Exception as e:
        logger.error(f"rss_set failed: {e}")


def _rss_del(user_id: str):
    try:
        _get_sb().table("reminder_setup_state").delete().eq("user_id", user_id).execute()
    except Exception as e:
        logger.error(f"rss_del failed: {e}")


def _rss_has(user_id: str) -> bool:
    try:
        row = _get_sb().table("reminder_setup_state").select("user_id").eq("user_id", user_id).execute().data
        result = bool(row)
        logger.info(f"rss_has: user={user_id} found={result}")
        return result
    except Exception as e:
        logger.error(f"rss_has failed: {e}")
        return False

MAX_HISTORY_TURNS = 10


def get_history(user_id: str) -> list:
    return conversation_memory.get(user_id, [])


def update_history(user_id: str, user_msg: str, bot_reply: str):
    history = conversation_memory.setdefault(user_id, [])
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": bot_reply})
    max_entries = MAX_HISTORY_TURNS * 2
    if len(history) > max_entries:
        conversation_memory[user_id] = history[-max_entries:]


def handle_bus(user_id: str, user_text: str) -> str:
    """處理公車查詢，支援多輪對話補充資訊"""
    import re

    state = bus_query_state.get(user_id, {})

    # ── 使用者正在選路線變體（212直/212夜 等）────────────────────
    if state.get("route_options"):
        options = state["route_options"]
        choice = user_text.strip()
        cancel_num = len(options) + 1
        if choice == str(cancel_num) or "皆非" in choice or "取消" in choice:
            bus_query_state.pop(user_id, None)
            return "好的，已取消查詢。"
        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(options)):
                raise ValueError
            confirmed_route = options[idx]
            stop = state.get("stop")
            city = state.get("city", "台北")
            bus_query_state[user_id] = {"route": confirmed_route, "stop": stop, "city": city}
            # 直接進站牌流程並 return，避免再次觸發 get_route_variants
            if not stop:
                route_label = f"{confirmed_route}號公車" if confirmed_route.isdigit() else confirmed_route
                return (f"請問您要查 {route_label} 在哪個站的到站時間呢？🚏\n\n"
                        f"站名照著站牌上寫的說就可以，例如：\n・「台北車站」\n・「捷運忠孝復興站」")
            if "(" in stop:
                bus_query_state.pop(user_id, None)
                return get_bus_arrival(confirmed_route, stop, city, exact=True)
            stop_opts = get_stop_options(confirmed_route, stop, city)
            if len(stop_opts) > 1:
                bus_query_state[user_id] = {"route": confirmed_route, "stop": stop, "city": city, "stop_options": stop_opts}
                lines = [f"「{stop}」這條路線有幾個站牌，請問您在哪一個？\n"]
                for i, name in enumerate(stop_opts, 1):
                    lines.append(f"{i}. {name}")
                lines.append(f"{len(stop_opts)+1}. 取消")
                return "\n".join(lines)
            bus_query_state.pop(user_id, None)
            exact_stop = stop_opts[0] if stop_opts else stop
            return get_bus_arrival(confirmed_route, exact_stop, city, exact=("(" in exact_stop))
        except (ValueError, TypeError):
            lines = [f"請輸入數字選擇路線："]
            for i, name in enumerate(options, 1):
                lines.append(f"{i}. {name}")
            lines.append(f"{cancel_num}. 以上皆非（取消）")
            return "\n".join(lines)

    # ── 使用者正在選子站 ──────────────────────────────────────
    if state.get("stop_options"):
        options = state["stop_options"]
        choice = user_text.strip()
        cancel_num = len(options) + 1
        if choice == str(cancel_num) or "取消" in choice:
            bus_query_state.pop(user_id, None)
            return "好的，已取消查詢。"
        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(options)):
                raise ValueError
            exact_stop = options[idx]
            route = state["route"]
            city = state["city"]
            bus_query_state.pop(user_id, None)
            return get_bus_arrival(route, exact_stop, city, exact=True)
        except (ValueError, TypeError):
            lines = [f"請輸入數字選擇站牌："]
            for i, name in enumerate(options, 1):
                lines.append(f"{i}. {name}")
            lines.append(f"{cancel_num}. 取消")
            return "\n".join(lines)

    # ── 一般對話：解析路線和站名 ──────────────────────────────
    parsed = parse_bus_query(user_text)

    route = parsed.get("route") or state.get("route")
    stop = parsed.get("stop") or state.get("stop")
    city = parsed.get("city") or state.get("city") or "台北"

    bus_query_state[user_id] = {"route": route, "stop": stop, "city": city}

    if not route:
        return (
            "請問您要查哪一路公車呢？🚌\n\n"
            "您可以這樣說：\n"
            "・「226公車到行天宮還有幾分鐘？」\n"
            "・「承德幹線、吳興國小站」\n"
            "・「22號，象山站」\n\n"
            "路線號碼在站牌或公車車頭都看得到喔！"
        )

    if not stop:
        route_label = f"{route}號公車" if route and route.isdigit() else route
        return (
            f"請問您要查 {route_label} 在哪個站的到站時間呢？🚏\n\n"
            f"站名照著站牌上寫的說就可以，例如：\n"
            f"・「台北車站」\n"
            f"・「捷運忠孝復興站」\n"
            f"・「行天宮站」"
        )

    # 查路線變體（如 212、212直、212夜），同時自動偵測實際城市（台北/新北）
    variants, city = get_route_variants(route, city)
    bus_query_state[user_id]["city"] = city  # 更新為實際城市
    if len(variants) > 1:
        bus_query_state[user_id] = {"route": route, "stop": stop, "city": city, "route_options": variants}
        lines = [f"「{route}」有幾條路線，請問您要搭哪一條？\n"]
        for i, name in enumerate(variants, 1):
            lines.append(f"{i}. {name}")
        lines.append(f"{len(variants)+1}. 以上皆非（取消）")
        return "\n".join(lines)

    # 資料齊全：先確認子站
    # 站名已含括號（如「捷運行天宮站(松江路)」）表示使用者已指定子站，直接查
    if "(" in stop:
        bus_query_state.pop(user_id, None)
        return get_bus_arrival(route, stop, city, exact=True)

    options = get_stop_options(route, stop, city)
    if len(options) > 1:
        bus_query_state[user_id] = {"route": route, "stop": stop, "city": city, "stop_options": options}
        lines = [f"「{stop}」這條路線有幾個站牌，請問您在哪一個？\n"]
        for i, name in enumerate(options, 1):
            lines.append(f"{i}. {name}")
        lines.append(f"{len(options)+1}. 取消")
        return "\n".join(lines)

    bus_query_state.pop(user_id, None)
    exact_stop = options[0] if options else stop
    return get_bus_arrival(route, exact_stop, city, exact=("(" in exact_stop))


RICH_MENU_HELP = {
    "查天氣說明": """🌤️ 查天氣

必須包含【城市名】，例如：
・「台北天氣」
・「今天台中天氣怎麼樣？」
・「高雄明天會下雨嗎？」

可查今天和明天的天氣預報。

🌧️ 雷達迴波圖：
https://www.cwa.gov.tw/V8/C/W/OBS_Radar.html

📅 一週預報：
https://www.cwa.gov.tw/V8/C/W/week.html""",

    "查公車說明": """🚌 查公車

必須包含【路線】和【站名】，例如：
・「226公車 行天宮站」
・「承德幹線 吳興國小站」
・「226到行天宮還有幾分鐘？」

🔗 台北公車動態：
https://pda5284.gov.taipei/MQS/routelist.jsp""",

    "查路線說明": """🗺️ 查路線

必須包含【起點】和【終點】，例如：
・「台北車站到行天宮怎麼去？」
・「家裡到台北車站」
・「從捷運南京復興站到忠孝敦化站」""",

    # 查筆記說明 改為動態產生（含 user_id 連結），見 handle_message

    "查提醒說明": """⏰ 提醒功能說明

【自己的提醒】
・「提醒我明天早上8點吃藥」
・「提醒我今天下午3點30分開會」
・「提醒我30分鐘後關火」
・「每天晚上9點提醒我喝水」
・「每週五下午3點提醒我領藥」

【查看／取消自己的】
・「我的提醒」查看清單
・「取消提醒 1」取消單筆
・「取消提醒 1,2,3」取消多筆
・「取消全部提醒」全部清除

──────────────────
【幫家人設定提醒】
可用名字：媽媽、爸爸、方方（或姊姊）、伊嵐（或妹妹）
可加 #浮誇 送卡片樣式提醒
・「幫媽媽提醒明天早上9點吃藥」
・「幫爸爸提醒下午3點看醫生 #浮誇」

【幫家人查看／取消】
・「幫媽媽查提醒」
・「幫媽媽取消提醒 1」
・「幫媽媽取消提醒 1,2」

──────────────────
🌐 也可以用網頁設定提醒：
說「我的提醒」可取得個人連結""",

    "問大小事說明": "【進入問大小事模式】",  # 動態處理，見 handle_message
}


def _parse_time_input(text: str, base_date):
    """從簡短回覆解析時間，例如「晚上9點」「9點」「21:00」"""
    from modules.reminder import _adjust_hour
    import re
    m = re.search(r"(?:早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})(?::(\d{2}))?(?:點|時)?", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        hour = _adjust_hour(text, hour)
        return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return None


def _parse_modes(text: str) -> list[int]:
    """解析選擇的提醒模式，支援阿拉伯數字和國字"""
    import re
    CN = {"一": "1", "二": "2", "三": "3", "四": "4"}
    normalized = text
    for cn, ar in CN.items():
        normalized = normalized.replace(cn, ar)
    nums = re.findall(r"[1-4]", normalized)
    return sorted(set(int(n) for n in nums))


FAMILY_ALIAS = {
    "媽媽": ("Uab8239f0b88f4061a5114be006f94f65", "媽媽"),
    "雷京": ("Uab8239f0b88f4061a5114be006f94f65", "媽媽"),
    "爸爸": ("U50fdecc36506a8c66f0b8388b4c96708", "爸爸"),
    "松哥": ("U50fdecc36506a8c66f0b8388b4c96708", "爸爸"),
    "方方": ("Uae768e5517dd14f206df9896d781626d", "方方"),
    "姊姊": ("Uae768e5517dd14f206df9896d781626d", "方方"),
    "伊嵐": ("Ucfd1c15e7ef296b7892fe874d215d945", "伊嵐"),
    "妹妹": ("Ucfd1c15e7ef296b7892fe874d215d945", "伊嵐"),
}

USER_DISPLAY_NAME = {
    "Ucfd1c15e7ef296b7892fe874d215d945": "伊嵐",
}


def handle_reminder(user_id: str, user_text: str) -> str:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Taipei")
    now = datetime.now(TZ)

    # 代設提醒：「幫媽媽提醒...」→ 改用對方 user_id，並替換回覆稱謂
    import re as _re
    proxy_name = None
    proxy_uid = None
    m = _re.match(r"幫(媽媽|雷京|爸爸|松哥|方方|姊姊|伊嵐|妹妹)(提醒|設提醒|查提醒|取消提醒)", user_text)
    if m:
        alias = m.group(1)
        proxy_uid, proxy_name = FAMILY_ALIAS[alias]
        action_word = m.group(2)
        # 把「幫XXX」去掉，剩下當作正常提醒指令
        user_text = user_text[m.end():]
        if action_word == "查提醒":
            user_text = "我的提醒"
        elif action_word == "取消提醒":
            user_text = "取消提醒" + user_text
        elif not user_text.startswith("提醒"):
            user_text = "提醒" + user_text

    target_uid = proxy_uid if proxy_uid else user_id
    setter_name = USER_DISPLAY_NAME.get(user_id) if proxy_uid else None
    fancy = "#浮誇" in user_text
    user_text = user_text.replace("#浮誇", "").strip()

    state = _rss_get(target_uid)

    # 代設模式下，多輪對話不適用（直接進入新指令解析）
    if not proxy_uid and state:
        step = state["step"]

        # 等待早上/下午確認
        if step == "ask_ampm":
            t = state["trigger_time"]
            if any(kw in user_text for kw in ["下午", "晚上"]):
                if t.hour < 12:
                    from datetime import timezone
                    t = t.replace(hour=t.hour + 12)
                state["trigger_time"] = t
            elif any(kw in user_text for kw in ["早上", "上午", "凌晨"]):
                pass  # 保持原來時間
            else:
                return f"請說「早上」或「下午」或「晚上」"
            state["step"] = "ask_mode"
            _rss_set(target_uid, state)
            t_str = state["trigger_time"].strftime("%m/%d %H:%M")
            return (
                f"好的！{state['content']}時間：{t_str}\n\n"
                f"請問要提前提醒嗎？可以複選，直接說數字：\n\n"
                f"1. 前一天提醒\n2. 當天提醒\n3. 提前1小時提醒\n4. 時間到再提醒"
            )

        # 等待時間輸入
        if step == "ask_time":
            from modules.reminder import parse_reminder as _pr
            parsed2 = _pr(user_text)
            if parsed2.get("trigger_time"):
                state["trigger_time"] = parsed2["trigger_time"]
                state["step"] = "ask_mode"
                _rss_set(target_uid, state)
                t_str = parsed2["trigger_time"].strftime("%m/%d %H:%M")
                return (
                    f"好的！{state['content']}時間：{t_str}\n\n"
                    f"請問要提前提醒嗎？可以複選，直接說數字：\n\n"
                    f"1. 前一天提醒\n"
                    f"2. 當天提醒\n"
                    f"3. 提前1小時提醒\n"
                    f"4. 時間到再提醒"
                )
            return f"請問幾點要{state['content']}？\n（請說早上或晚上，例如「早上10點」）"

        # 等待選擇模式
        if step == "ask_mode":
            modes = _parse_modes(user_text)
            if not modes:
                return "請輸入數字選擇，例如「1」或「1和2」：\n\n1. 前一天提醒\n2. 當天提醒\n3. 提前1小時提醒\n4. 時間到再提醒"

            state["modes"] = modes
            state["pending"] = [m for m in modes if m in (1, 2)]  # 需要問時間的

            # 模式4或3不需要問時間，直接設定
            if not state["pending"]:
                return _finalize_reminder(target_uid, state, modes, now)

            # 問第一個需要時間的模式
            return _ask_mode_time(target_uid, state)

        # 等待前一天時間
        if step == "ask_prev_day_time":
            t = _parse_time_input(user_text, state["trigger_time"] - timedelta(days=1))
            if not t or not any(kw in user_text for kw in ["早上","上午","中午","下午","晚上","凌晨"]):
                return "請問幾點提醒您？\n請說清楚早上或晚上，例如：\n・「早上7點」\n・「晚上9點」"
            state["prev_day_time"] = t
            state["pending"].pop(0)
            if state["pending"]:
                return _ask_mode_time(target_uid, state)
            return _finalize_reminder(target_uid, state, state["modes"], now)

        # 等待當天時間
        if step == "ask_same_day_time":
            t = _parse_time_input(user_text, state["trigger_time"])
            if not t or not any(kw in user_text for kw in ["早上","上午","中午","下午","晚上","凌晨"]):
                return "請問幾點提醒您？\n請說清楚早上或晚上，例如：\n・「早上7點」\n・「晚上9點」"
            state["same_day_time"] = t
            state["pending"].pop(0)
            if state["pending"]:
                return _ask_mode_time(target_uid, state)
            return _finalize_reminder(target_uid, state, state["modes"], now)

    # ── 新的提醒指令 ─────────────────────────────────────────
    parsed = parse_reminder(user_text)
    action = parsed.get("action")

    if action == "list":
        result = list_reminders(target_uid)
        if proxy_name:
            result = result.replace("📋 您的提醒清單", f"📋 {proxy_name}的提醒清單", 1)
        render_url = os.getenv("RENDER_URL", "https://line-bot-mama.onrender.com")
        result += f"\n\n🌐 網頁版管理：\n{render_url}/reminders?user_id={target_uid}"
        return result
    if action == "cancel":
        result = cancel_reminder(target_uid, parsed["index"])
        return result.replace("✅ 已取消", f"✅ 已幫{proxy_name}取消", 1) if proxy_name else result
    if action == "cancel_multi":
        result = cancel_multi_reminders(target_uid, parsed["indices"])
        return result.replace("✅ 已取消", f"✅ 已幫{proxy_name}取消", 1) if proxy_name else result
    if action == "cancel_all":
        result = cancel_all_reminders(target_uid)
        return result.replace("✅ 已取消", f"✅ 已幫{proxy_name}取消", 1) if proxy_name else result
    if action == "add":
        repeat = parsed.get("repeat")
        prefix = f"已幫{proxy_name}" if proxy_name else ""

        # 重複提醒不問提前，直接設定
        if repeat:
            result = add_reminder(target_uid, parsed["content"], parsed["trigger_time"], repeat, setter_name, fancy)
            return result.replace("✅ 已設定", f"✅ {prefix}設定", 1) if proxy_name else result

        # 代設提醒：不走多輪流程，直接設定（一次性，選「時間到」模式）
        if proxy_name:
            if not parsed.get("trigger_time"):
                return f"請加上時間，例如：\n幫{proxy_name}提醒明天早上8點吃藥"
            if not any(kw in user_text for kw in ["早上", "上午", "中午", "下午", "晚上", "凌晨", "分鐘後", "小時後"]):
                return f"請說清楚早上還是下午/晚上，例如：\n幫{proxy_name}提醒明天早上8點吃藥"
            result = add_reminder(target_uid, parsed["content"], parsed["trigger_time"], setter_name=setter_name, fancy=fancy)
            return result.replace("✅ 已設定", f"✅ 已幫{proxy_name}設定", 1)

        # 有時間但沒說早上/下午，先問清楚
        if parsed.get("trigger_time") and not any(
            kw in user_text for kw in ["早上", "上午", "中午", "下午", "晚上", "凌晨", "分鐘後", "小時後"]
        ):
            _rss_set(target_uid, {
                "step": "ask_ampm",
                "content": parsed["content"],
                "trigger_time": parsed["trigger_time"],
                "repeat": repeat,
                "modes": [],
                "pending": [],
                "prev_day_time": None,
                "same_day_time": None,
            })
            t_str = parsed["trigger_time"].strftime("%m/%d %H:%M")
            return f"請問「{parsed['content']}」是早上還是下午/晚上 {parsed['trigger_time'].strftime('%H:%M')}？\n請說「早上」或「下午」或「晚上」"

        # 有內容但沒有時間，先問時間
        if not parsed.get("trigger_time"):
            _rss_set(target_uid, {
                "step": "ask_time",
                "content": parsed["content"],
                "trigger_time": None,
                "repeat": None,
                "modes": [],
                "pending": [],
                "prev_day_time": None,
                "same_day_time": None,
            })
            return f"好的！請問幾點要{parsed['content']}？\n（請說早上或晚上，例如「早上10點」）"

        # 一次性提醒：問提前模式
        _rss_set(target_uid, {
            "step": "ask_mode",
            "content": parsed["content"],
            "trigger_time": parsed["trigger_time"],
            "repeat": repeat,
            "modes": [],
            "pending": [],
            "prev_day_time": None,
            "same_day_time": None,
        })
        t_str = parsed["trigger_time"].strftime("%m/%d %H:%M")
        return (
            f"好的！{parsed['content']}時間：{t_str}\n\n"
            f"請問要提前提醒嗎？可以複選，直接說數字：\n\n"
            f"1. 前一天提醒\n"
            f"2. 當天提醒\n"
            f"3. 提前1小時提醒\n"
            f"4. 時間到再提醒"
        )

    return "請問您要設定什麼提醒呢？\n\n例如：\n・「提醒我明天早上8點吃藥」\n・「每天晚上9點提醒我喝水」\n・「30分鐘後提醒我關火」\n・「我的提醒」查看清單"


def _ask_mode_time(user_id: str, state: dict) -> str:
    next_mode = state["pending"][0]
    if next_mode == 1:
        state["step"] = "ask_prev_day_time"
        _rss_set(user_id, state)
        return "前一天幾點提醒您？\n請說清楚早上或晚上，例如：\n・「早上7點」\n・「晚上9點」"
    elif next_mode == 2:
        state["step"] = "ask_same_day_time"
        _rss_set(user_id, state)
        return "當天幾點提醒您？\n請說清楚早上或晚上，例如：\n・「早上7點」\n・「晚上9點」"
    return ""


def _finalize_reminder(user_id: str, state: dict, modes: list, now) -> str:
    from datetime import timedelta
    _rss_del(user_id)
    content = state["content"]
    trigger_time = state["trigger_time"]
    lines = [f"✅ 已設定提醒：{content}"]
    lines.append(f"提醒時間：")

    if 1 in modes and state.get("prev_day_time"):
        add_reminder(user_id, content, state["prev_day_time"])
        lines.append(f"・{state['prev_day_time'].strftime('%m/%d %H:%M')}（前一天）")

    if 2 in modes and state.get("same_day_time"):
        add_reminder(user_id, content, state["same_day_time"])
        lines.append(f"・{state['same_day_time'].strftime('%m/%d %H:%M')}（當天）")

    if 3 in modes:
        early = trigger_time - timedelta(hours=1)
        add_reminder(user_id, content, early)
        lines.append(f"・{early.strftime('%m/%d %H:%M')}（提前1小時）")

    if 4 in modes or (not any(m in modes for m in [1, 2, 3])):
        add_reminder(user_id, content, trigger_time)
        lines.append(f"・{trigger_time.strftime('%m/%d %H:%M')}（時間到）")

    # 如果選了1或2或3但也沒選4，主時間也要設
    if any(m in modes for m in [1, 2, 3]) and 4 not in modes:
        add_reminder(user_id, content, trigger_time)
        lines.append(f"・{trigger_time.strftime('%m/%d %H:%M')}（時間到）")

    return "\n".join(lines)


def handle_message(user_id: str, user_text: str) -> str:
    """根據意圖分派到對應模組，回傳回覆文字"""

    # Rich Menu 說明
    if user_text == "查提醒說明":
        render_url = os.getenv("RENDER_URL", "https://line-bot-mama.onrender.com")
        url = f"{render_url}/reminders?user_id={user_id}"
        return (
            "⏰ 提醒功能\n\n"
            "【用說的設定】\n"
            "・「提醒我明天早上8點吃藥」\n"
            "・「每天晚上9點提醒我喝水」\n"
            "・「幫媽媽提醒下午3點看醫生 #浮誇」\n\n"
            "【用網頁設定（更方便）】\n"
            f"{url}\n\n"
            "⚠️ 此連結只限本人使用，請勿傳給他人"
        )
    if user_text == "查筆記說明":
        url = f"https://line-bot-mama.onrender.com/notes?user_id={user_id}"
        return (
            "📒 記事本\n\n"
            "【新增】記一下／記下來／幫我記／備忘\n"
            "・「記一下 明天買牛奶」\n\n"
            "查詢、編輯、刪除筆記請點連結：\n"
            f"{url}\n\n"
            "⚠️ 此連結只限本人使用，請勿傳給他人"
        )
    if user_text == "問大小事說明":
        qa_mode_users.add(user_id)
        return "💬 問大小事開始囉！\n\n什麼都可以問，例如：\n・今天吃什麼好？\n・感冒怎麼辦？\n・附近哪裡有郵局？\n\n說「結束」離開。"

    if user_text in RICH_MENU_HELP:
        qa_mode_users.discard(user_id)  # 按其他 Rich Menu 按鈕離開 QA 模式
        return RICH_MENU_HELP[user_text]

    # 說「結束」離開 QA 模式
    if user_text.strip() == "結束" and user_id in qa_mode_users:
        qa_mode_users.discard(user_id)
        return "好的，已離開問大小事。\n有需要再按按鈕叫我喔！"

    # 優先繼續未完成的查詢流程
    if user_id in bus_query_state:
        intent = "bus"
    elif user_id in transit_query_state:
        intent = "transit"
    elif _rss_has(user_id):
        intent = "reminder"
    elif user_id in qa_mode_users:
        intent = "qa"
    else:
        intent_result = detect_intent(user_text)
        intent = intent_result.get("intent")
        # 不符合任何意圖時，提示用戶而非直接進 AI
        if intent == "qa":
            return "我不太懂您的意思 😊\n\n請問是要：\n・查天氣\n・查公車\n・查路線\n・記事本\n・提醒\n・問大小事（請按按鈕）"

    logger.info(f"user={user_id} intent={intent} msg={user_text[:50]}")

    if intent == "weather":
        city = detect_intent(user_text).get("city")
        if city:
            reply = get_weather_forecast(city)
        else:
            reply = "請問您想查哪個城市的天氣呢？\n例如：台北、台中、高雄、花蓮…"

    elif intent == "bus":
        reply = handle_bus(user_id, user_text)

    elif intent == "transit":
        state = transit_query_state.get(user_id, {})

        # ── 使用者正在從候選清單選地點 ──────────────────────────
        if state.get("place_options"):
            options = state["place_options"]
            clarifying = state.get("clarifying")  # "origin" or "destination"
            cancel_num = len(options) + 1
            choice = user_text.strip()

            if choice == str(cancel_num) or "皆非" in choice or "取消" in choice:
                transit_query_state.pop(user_id, None)
                reply = "好的，已取消路線查詢。"
            else:
                try:
                    idx = int(choice) - 1
                    if not (0 <= idx < len(options)):
                        raise ValueError
                    selected = options[idx]
                    confirmed_addr = selected["address"]
                    confirmed_name = selected["name"]

                    if clarifying == "origin":
                        # 起點確認，繼續檢查終點
                        destination = state.get("destination")
                        dest_info = check_location_precision(destination) if destination else {"precise": True}
                        if destination and not dest_info["precise"]:
                            places = search_places(destination)
                            if places:
                                lines = [f"出發地：{confirmed_name}\n\n找不到「{destination}」的精確位置，您是指：\n"]
                                for i, p in enumerate(places, 1):
                                    lines.append(f"{i}. {p['name']}（{p['address']}）")
                                lines.append(f"{len(places)+1}. 以上皆非（取消查詢）")
                                transit_query_state[user_id] = {
                                    "origin": confirmed_addr,
                                    "origin_name": confirmed_name,
                                    "destination": destination,
                                    "arrival_time": state.get("arrival_time"),
                                    "query_type": state.get("query_type", "route"),
                                    "place_options": places,
                                    "clarifying": "destination",
                                }
                                reply = "\n".join(lines)
                            else:
                                transit_query_state.pop(user_id, None)
                                reply = get_directions(
                                    confirmed_addr, destination,
                                    orig_destination=destination,
                                    arrival_time_str=state.get("arrival_time"),
                                    query_type=state.get("query_type", "route"),
                                )
                        else:
                            transit_query_state.pop(user_id, None)
                            reply = get_directions(
                                confirmed_addr, destination or "家裡",
                                orig_destination=destination,
                                arrival_time_str=state.get("arrival_time"),
                                query_type=state.get("query_type", "route"),
                            )

                    else:  # clarifying == "destination"
                        transit_query_state.pop(user_id, None)
                        reply = get_directions(
                            state.get("origin") or "家裡",
                            confirmed_addr,
                            orig_destination=confirmed_name,
                            arrival_time_str=state.get("arrival_time"),
                            query_type=state.get("query_type", "route"),
                        )

                except (ValueError, IndexError):
                    reply = f"請輸入 1 到 {cancel_num} 的數字來選擇。"

        # ── 新的查詢 ─────────────────────────────────────────────
        else:
            parsed = parse_transit_query(user_text)
            origin = parsed.get("origin") or state.get("origin")
            destination = parsed.get("destination") or state.get("destination")
            arrival_time = parsed.get("arrival_time") or state.get("arrival_time")
            query_type = parsed.get("query_type", "route")

            BUSINESS_KEYWORDS = ("診所", "醫院", "藥局", "藥房", "餐廳", "餐館", "飯店",
                                 "旅館", "便利商店", "超市", "市場", "銀行", "郵局",
                                 "診療", "牙醫", "眼科", "耳鼻喉", "中醫", "診所")

            def needs_clarify(place_name: str) -> tuple[bool, list]:
                """
                回傳 (需要澄清, 候選清單)。
                條件一：Geocoding 不精確（neighborhood/sublocality 層級）。
                條件二：地點名稱含商家關鍵字（診所/醫院等），即使 Geocoding 精確也搜 Places 確認。
                LOCATION_ALIAS 裡的已知別名直接放行。
                """
                if place_name in LOCATION_ALIAS:
                    return False, []
                is_business = any(kw in place_name for kw in BUSINESS_KEYWORDS)
                geo = check_location_precision(place_name)
                if not geo["precise"] or is_business:
                    places = search_places(place_name)
                    if len(places) > 1:
                        return True, places
                    if not geo["precise"] and places:
                        return True, places
                return False, []

            def make_options_msg(place_name: str, places: list, label: str) -> str:
                lines = [f"「{place_name}」有多個{label}，請問您是指：\n"]
                for i, p in enumerate(places, 1):
                    lines.append(f"{i}. {p['name']}（{p['address']}）")
                lines.append(f"{len(places)+1}. 以上皆非（取消查詢）")
                return "\n".join(lines)

            if not origin and not arrival_time:
                transit_query_state[user_id] = {}
                reply = "請問您要從哪裡出發呢？\n例如：台北車站、板橋、家裡附近的捷運站"

            elif not destination:
                transit_query_state[user_id] = {"origin": origin, "arrival_time": arrival_time}
                reply = f"從「{origin}」出發，請問要去哪裡呢？"

            else:
                # 先檢查起點
                orig_ambig, orig_places = needs_clarify(origin) if origin else (False, [])
                if orig_ambig and orig_places:
                    transit_query_state[user_id] = {
                        "origin": origin, "destination": destination,
                        "arrival_time": arrival_time, "query_type": query_type,
                        "place_options": orig_places, "clarifying": "origin",
                    }
                    reply = make_options_msg(origin, orig_places, "出發地")
                else:
                    # 再檢查終點
                    dest_ambig, dest_places = needs_clarify(destination)
                    if dest_ambig and dest_places:
                        transit_query_state[user_id] = {
                            "origin": origin, "destination": destination,
                            "arrival_time": arrival_time, "query_type": query_type,
                            "place_options": dest_places, "clarifying": "destination",
                        }
                        reply = make_options_msg(destination, dest_places, "目的地")
                    else:
                        transit_query_state.pop(user_id, None)
                        reply = get_directions(origin or "家裡", destination,
                                               orig_destination=destination,
                                               arrival_time_str=arrival_time,
                                               query_type=query_type)

    elif intent == "note":
        parsed = parse_note_query(user_text)
        action = parsed.get("action")
        if action == "add":
            reply = add_note(parsed["content"], user_id=user_id)
        elif action == "search":
            reply = search_notes(
                keyword=parsed.get("keyword"),
                year=parsed.get("year"),
                month=parsed.get("month"),
                week=parsed.get("week"),
                user_id=user_id,
            )
        elif action == "delete":
            reply = delete_note(parsed["note_id"], user_id=user_id)
        elif action == "delete_last":
            reply = delete_last_note(user_id=user_id)
        else:
            reply = "請問您要記什麼呢？\n\n您可以這樣說：\n・「記一下 明天買牛奶」\n・「查筆記」\n・「查筆記 牛奶」\n・「刪除筆記 [1]」"

    elif intent == "reminder":
        reply = handle_reminder(user_id, user_text)

    else:
        history = get_history(user_id)
        reply = get_qa_response(user_text, history)

    update_history(user_id, user_text, reply)
    return reply


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.warning("Invalid LINE signature")
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    user_id = event.source.user_id
    user_text = event.message.text.strip()
    if not user_text:
        return
    reply_text = handle_message(user_id, user_text)
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )


@app.route("/notes", methods=["GET"])
def notes_page():
    from flask import render_template
    return render_template("notes.html")


@app.route("/api/notes", methods=["GET"])
def api_notes_list():
    from flask import request, jsonify
    from modules.notes import get_client
    sb = get_client()
    query = sb.table("notes").select("*").order("created_at", desc=True)
    user_id = request.args.get("user_id")
    if user_id:
        query = query.eq("user_id", user_id)
    result = query.execute()
    return jsonify(result.data)


@app.route("/api/notes", methods=["POST"])
def api_notes_add():
    from flask import request, jsonify
    from modules.notes import get_client
    data = request.get_json()
    content = (data or {}).get("content", "").strip()
    user_id = (data or {}).get("user_id") or None
    if not content:
        return jsonify({"error": "empty"}), 400
    sb = get_client()
    result = sb.table("notes").insert({"content": content, "user_id": user_id}).execute()
    return jsonify(result.data[0]), 201


@app.route("/api/notes/<int:note_id>", methods=["PATCH"])
def api_notes_update(note_id):
    from flask import request, jsonify
    from modules.notes import get_client
    data = request.get_json()
    content = (data or {}).get("content", "").strip()
    if not content:
        return jsonify({"error": "empty"}), 400
    sb = get_client()
    result = sb.table("notes").update({"content": content}).eq("id", note_id).execute()
    return jsonify(result.data[0] if result.data else {})


@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
def api_notes_delete(note_id):
    from flask import jsonify
    from modules.notes import get_client
    sb = get_client()
    sb.table("notes").delete().eq("id", note_id).execute()
    return jsonify({"ok": True})


@app.route("/reminders", methods=["GET"])
def reminders_page():
    return render_template("reminders.html")


@app.route("/api/reminders", methods=["GET"])
def api_reminders_get():
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify([])
    try:
        rows = _get_sb().table("reminders").select("*").eq("user_id", user_id).order("trigger_time").execute().data
        return jsonify(rows)
    except Exception as e:
        logger.error(f"api_reminders_get failed: {e}")
        return jsonify([])


@app.route("/api/reminders", methods=["POST"])
def api_reminders_post():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Taipei")
    data = request.get_json()
    user_id = data.get("user_id", "")
    content = (data.get("content") or "").strip()
    trigger_time_str = data.get("trigger_time", "")
    repeat = data.get("repeat") or None
    fancy = bool(data.get("fancy", False))
    setter_name = data.get("setter_name") or None
    early_list = data.get("early_list") or [{"type": "on_time"}]

    if not user_id or not content or not trigger_time_str:
        return jsonify({"error": "缺少必要欄位"}), 400

    try:
        base_time = datetime.fromisoformat(trigger_time_str).astimezone(TZ)
    except Exception:
        return jsonify({"error": "時間格式錯誤"}), 400

    weekday = data.get("weekday")

    # 若是每週，把 trigger_time 的星期調整到指定星期
    if repeat == "weekly" and weekday is not None:
        days_ahead = (int(weekday) - base_time.weekday()) % 7
        base_time = base_time + timedelta(days=days_ahead)

    inserted = 0
    sb = _get_sb()
    for item in early_list:
        t = item.get("type")
        if t == "on_time":
            fire_at = base_time
        elif t == "minus_1h":
            fire_at = base_time - timedelta(hours=1)
        elif t == "prev_day":
            early_time_str = item.get("time", base_time.strftime("%H:%M"))
            h, m = map(int, early_time_str.split(":"))
            fire_at = (base_time - timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
        elif t == "same_day":
            early_time_str = item.get("time", base_time.strftime("%H:%M"))
            h, m = map(int, early_time_str.split(":"))
            fire_at = base_time.replace(hour=h, minute=m, second=0, microsecond=0)
        else:
            continue

        label = "提前提醒：" if t not in ("on_time",) else ""
        row_content = f"{label}{content}" if label else content

        try:
            sb.table("reminders").insert({
                "user_id": user_id,
                "content": row_content,
                "trigger_time": fire_at.isoformat(),
                "repeat": repeat,
                "fancy": fancy,
                "setter_name": setter_name,
            }).execute()
            inserted += 1
        except Exception as e:
            logger.error(f"api_reminders_post insert failed: {e}")

    return jsonify({"ok": True, "count": inserted})


@app.route("/api/reminders/<int:rid>", methods=["DELETE"])
def api_reminders_delete(rid):
    try:
        _get_sb().table("reminders").delete().eq("id", rid).execute()
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"api_reminders_delete failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "version": "1.1.0"}


@app.route("/check_reminders", methods=["GET"])
def check_reminders():
    secret = os.getenv("CRON_SECRET", "")
    if secret and request.args.get("secret") != secret:
        abort(403)
    from modules.reminder import check_and_send_due_reminders
    check_and_send_due_reminders()
    return "ok"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
