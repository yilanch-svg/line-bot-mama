"""
提醒功能模組
支援一次性和重複提醒，使用 APScheduler
提醒資料存在 Supabase，重啟後自動還原
"""

import os
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Taipei")

scheduler = BackgroundScheduler(timezone="Asia/Taipei")


def _get_sb():
    from supabase import create_client
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SECRET_KEY"))


def _send_push(user_id: str, text: str, reminder_id: int = None):
    """發送 LINE Push Message，一次性提醒發完後從 DB 刪除"""
    import httpx
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    try:
        httpx.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"to": user_id, "messages": [{"type": "text", "text": text}]},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Push message failed: {e}")

    # 一次性提醒發送後從 DB 刪除
    if reminder_id:
        try:
            _get_sb().table("reminders").delete().eq("id", reminder_id).execute()
        except Exception as e:
            logger.error(f"Delete reminder failed: {e}")


def _schedule_from_db(row: dict):
    """從 DB 資料建立 APScheduler job"""
    user_id = row["user_id"]
    content = row["content"]
    repeat = row["repeat"]
    rid = row["id"]
    msg = f"⏰ 提醒您：{content}"
    job_id = f"reminder_{rid}"

    now = datetime.now(TZ)

    if repeat == "daily":
        # trigger_time 只取時分
        t = datetime.fromisoformat(row["trigger_time"]).astimezone(TZ)
        trigger = CronTrigger(hour=t.hour, minute=t.minute, timezone="Asia/Taipei")
        scheduler.add_job(_send_push, trigger, args=[user_id, msg],
                          id=job_id, replace_existing=True)
    elif repeat == "weekly":
        t = datetime.fromisoformat(row["trigger_time"]).astimezone(TZ)
        trigger = CronTrigger(day_of_week=t.weekday(), hour=t.hour, minute=t.minute,
                              timezone="Asia/Taipei")
        scheduler.add_job(_send_push, trigger, args=[user_id, msg],
                          id=job_id, replace_existing=True)
    else:
        t = datetime.fromisoformat(row["trigger_time"]).astimezone(TZ)
        if t <= now:
            # 已過期，從 DB 刪除
            try:
                _get_sb().table("reminders").delete().eq("id", rid).execute()
            except Exception:
                pass
            return
        trigger = DateTrigger(run_date=t, timezone="Asia/Taipei")
        scheduler.add_job(_send_push, trigger, args=[user_id, msg, rid],
                          id=job_id, replace_existing=True)


def load_reminders_from_db():
    """重啟時從 DB 還原所有提醒"""
    try:
        sb = _get_sb()
        rows = sb.table("reminders").select("*").execute().data
        for row in rows:
            try:
                _schedule_from_db(row)
            except Exception as e:
                logger.error(f"Restore reminder {row['id']} failed: {e}")
        logger.info(f"已還原 {len(rows)} 筆提醒")
    except Exception as e:
        logger.error(f"Load reminders failed: {e}")


def add_reminder(user_id: str, content: str, trigger_time: datetime,
                 repeat: str = None) -> str:
    msg = f"⏰ 提醒您：{content}"
    now = datetime.now(TZ)

    # 存到 DB
    try:
        sb = _get_sb()
        row = sb.table("reminders").insert({
            "user_id": user_id,
            "content": content,
            "trigger_time": trigger_time.isoformat(),
            "repeat": repeat,
        }).execute().data[0]
        rid = row["id"]
    except Exception as e:
        logger.error(f"Save reminder failed: {e}")
        rid = None

    job_id = f"reminder_{rid}" if rid else f"reminder_{user_id}_{trigger_time.strftime('%Y%m%d%H%M%S')}"

    if repeat == "daily":
        trigger = CronTrigger(hour=trigger_time.hour, minute=trigger_time.minute,
                              timezone="Asia/Taipei")
        scheduler.add_job(_send_push, trigger, args=[user_id, msg],
                          id=job_id, replace_existing=True)
        time_str = trigger_time.strftime("%H:%M")
        return f"✅ 已設定每天 {time_str} 提醒您：{content}"
    elif repeat == "weekly":
        trigger = CronTrigger(day_of_week=trigger_time.weekday(),
                              hour=trigger_time.hour, minute=trigger_time.minute,
                              timezone="Asia/Taipei")
        scheduler.add_job(_send_push, trigger, args=[user_id, msg],
                          id=job_id, replace_existing=True)
        weekdays = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
        time_str = trigger_time.strftime("%H:%M")
        return f"✅ 已設定每{weekdays[trigger_time.weekday()]} {time_str} 提醒您：{content}"
    else:
        trigger = DateTrigger(run_date=trigger_time, timezone="Asia/Taipei")
        scheduler.add_job(_send_push, trigger, args=[user_id, msg, rid],
                          id=job_id, replace_existing=True)
        date_str = trigger_time.strftime("%m/%d %H:%M")
        return f"✅ 已設定 {date_str} 提醒您：{content}"


def list_reminders(user_id: str) -> str:
    try:
        sb = _get_sb()
        rows = sb.table("reminders").select("*").eq("user_id", user_id)\
                 .order("trigger_time").execute().data
    except Exception:
        rows = []

    if not rows:
        return "目前沒有設定任何提醒。"

    lines = ["📋 您的提醒清單\n"]
    for i, row in enumerate(rows, 1):
        t = datetime.fromisoformat(row["trigger_time"]).astimezone(TZ)
        repeat = row["repeat"]
        if repeat == "daily":
            time_str = f"每天 {t.strftime('%H:%M')}"
            repeat_str = "（重複）"
        elif repeat == "weekly":
            weekdays = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
            time_str = f"每{weekdays[t.weekday()]} {t.strftime('%H:%M')}"
            repeat_str = "（重複）"
        else:
            time_str = t.strftime("%m/%d %H:%M")
            repeat_str = "（一次）"
        lines.append(f"[{i}] {time_str} {repeat_str}\n    {row['content']}")

    lines.append("\n取消請照以下格式說：\n・「取消提醒 1」\n・「取消提醒 1,2,3」\n・「取消全部提醒」")
    return "\n".join(lines)


def cancel_reminder(user_id: str, index: int) -> str:
    try:
        sb = _get_sb()
        rows = sb.table("reminders").select("*").eq("user_id", user_id)\
                 .order("trigger_time").execute().data
    except Exception:
        rows = []

    if not rows or index < 1 or index > len(rows):
        return f"找不到提醒 [{index}]，請先說「我的提醒」查看清單。"

    row = rows[index - 1]
    content = row["content"]
    rid = row["id"]

    # 從 DB 刪除
    try:
        _get_sb().table("reminders").delete().eq("id", rid).execute()
    except Exception as e:
        logger.error(f"Cancel reminder failed: {e}")

    # 從 scheduler 移除
    job_id = f"reminder_{rid}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    return f"✅ 已取消提醒：{content}"


def cancel_all_reminders(user_id: str) -> str:
    try:
        sb = _get_sb()
        rows = sb.table("reminders").select("id").eq("user_id", user_id).execute().data
        if not rows:
            return "目前沒有任何提醒。"
        for row in rows:
            rid = row["id"]
            try:
                sb.table("reminders").delete().eq("id", rid).execute()
                scheduler.remove_job(f"reminder_{rid}")
            except Exception:
                pass
        return f"✅ 已取消全部 {len(rows)} 筆提醒。"
    except Exception as e:
        return "取消失敗，請再試一次。"


def cancel_multi_reminders(user_id: str, indices: list) -> str:
    try:
        sb = _get_sb()
        rows = sb.table("reminders").select("*").eq("user_id", user_id)\
                 .order("trigger_time").execute().data
    except Exception:
        rows = []

    if not rows:
        return "目前沒有任何提醒。"

    cancelled = []
    for idx in sorted(set(indices), reverse=True):
        if 1 <= idx <= len(rows):
            row = rows[idx - 1]
            rid = row["id"]
            try:
                _get_sb().table("reminders").delete().eq("id", rid).execute()
                scheduler.remove_job(f"reminder_{rid}")
                cancelled.append(row["content"])
            except Exception:
                pass

    if not cancelled:
        return "找不到指定的提醒，請先說「我的提醒」查看清單。"
    return "✅ 已取消：\n" + "\n".join(f"・{c}" for c in cancelled)


def parse_reminder(user_message: str) -> dict:
    """解析提醒指令"""

    # 取消提醒（支援批次：「取消123」「取消提醒1,2,3」「取消全部提醒」）
    if any(kw in user_message for kw in ["取消全部提醒", "刪除全部提醒", "清除全部提醒"]):
        return {"action": "cancel_all"}

    m = re.search(r"取消提醒?\s*([\d,，和與及\s]+)", user_message)
    if m:
        nums = re.findall(r"\d+", m.group(1))
        if nums:
            return {"action": "cancel_multi", "indices": [int(n) for n in nums]}

    # 查看提醒清單
    if any(kw in user_message for kw in ["我的提醒", "提醒清單", "查提醒", "有什麼提醒"]):
        return {"action": "list"}

    now = datetime.now(TZ)
    repeat = None

    # 重複：每天
    is_daily = bool(re.search(r"每天|每日", user_message))
    weekday_map = {"週一": 0, "星期一": 0, "週二": 1, "星期二": 1,
                   "週三": 2, "星期三": 2, "週四": 3, "星期四": 3,
                   "週五": 4, "星期五": 4, "週六": 5, "星期六": 5,
                   "週日": 6, "星期日": 6, "週天": 6, "星期天": 6}
    weekly_day = None
    for kw, day in weekday_map.items():
        if kw in user_message:
            weekly_day = day
            break

    if is_daily:
        repeat = "daily"
    elif weekly_day is not None:
        repeat = "weekly"

    trigger_time = None

    def _parse_hm(msg, h_str, m_str):
        hour = int(h_str)
        minute = int(m_str) if m_str else 0
        return _adjust_hour(msg, hour), minute

    # MM/DD + 時間（先抓日期，再抓時間）
    if not trigger_time:
        md = re.search(r"(\d{1,2})/(\d{1,2})", user_message)
        if md:
            try:
                month, day = int(md.group(1)), int(md.group(2))
                # 找日期後面的時間
                after = user_message[md.end():]
                tm = re.search(r"(?:早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})[：:：](\d{2})", after)
                if not tm:
                    tm = re.search(r"(?:早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})(?:[：:：](\d{2}))?(?:點|時)", after)
                if tm and tm.group(1):
                    hour, minute = _parse_hm(user_message, tm.group(1), tm.group(2))
                    t = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
                    trigger_time = t if t >= now else t.replace(year=now.year + 1)
            except Exception:
                pass

    # 明天/後天 + 時間
    if not trigger_time:
        m = re.search(r"(明天|後天|大後天)\s*(?:早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})[：:](\d{2})", user_message)
        if m:
            day_offset = {"明天": 1, "後天": 2, "大後天": 3}[m.group(1)]
            hour, minute = _parse_hm(user_message, m.group(2), m.group(3))
            trigger_time = (now + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    if not trigger_time:
        m = re.search(r"(明天|後天|大後天)\s*(?:早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})(?:[：:](\d{2}))?(?:點|時)", user_message)
        if m:
            day_offset = {"明天": 1, "後天": 2, "大後天": 3}[m.group(1)]
            hour, minute = _parse_hm(user_message, m.group(2), m.group(3))
            trigger_time = (now + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    # 今天 + 時間
    if not trigger_time:
        m = re.search(r"今天\s*(?:早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})[：:](\d{2})", user_message)
        if m:
            hour, minute = _parse_hm(user_message, m.group(1), m.group(2))
            trigger_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if not trigger_time:
        m = re.search(r"今天\s*(?:早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})(?:[：:](\d{2}))?(?:點|時)", user_message)
        if m:
            hour, minute = _parse_hm(user_message, m.group(1), m.group(2))
            trigger_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # 時間（無日期）- HH:MM 格式
    if not trigger_time:
        m = re.search(r"(?:早上|上午|中午|下午|晚上|凌晨)\s*(\d{1,2})[：:](\d{2})", user_message)
        if m:
            hour, minute = _parse_hm(user_message, m.group(1), m.group(2))
            trigger_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if repeat is None and trigger_time <= now:
                trigger_time += timedelta(days=1)

    # 時間（無日期）- N點/N時 格式
    if not trigger_time:
        m = re.search(r"(?:早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})(?:[：:](\d{2}))?(?:點|時)", user_message)
        if m:
            hour, minute = _parse_hm(user_message, m.group(1), m.group(2))
            trigger_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if repeat is None and trigger_time <= now:
                trigger_time += timedelta(days=1)

    # X分鐘後
    if not trigger_time:
        m = re.search(r"(\d+)\s*分鐘後", user_message)
        if m:
            trigger_time = now + timedelta(minutes=int(m.group(1)))

    # X小時後
    if not trigger_time:
        m = re.search(r"(\d+)\s*(?:小時|鐘頭)後", user_message)
        if m:
            trigger_time = now + timedelta(hours=int(m.group(1)))

    # 解析內容
    content = re.sub(
        r"每?(?:週|星期)[一二三四五六日天]|"
        r"提醒我|幫我提醒|設提醒|設定提醒|每天|每日|每週|每周|"
        r"明天|後天|大後天|今天|早上|上午|中午|下午|晚上|凌晨|"
        r"\d{1,2}/\d{1,2}|"
        r"\d{1,2}:\d{2}|"
        r"\d{1,2}(?::\d{2})?(?:點|時)|\d+分鐘後|\d+小時後",
        "", user_message
    ).strip()
    content = re.sub(r"^[要去]*", "", content).strip()

    # 有內容但沒時間
    if not trigger_time:
        if content:
            return {"action": "add", "content": content, "trigger_time": None, "repeat": None}
        return {"action": None}

    if not content:
        content = "（未命名提醒）"

    # 重複提醒，找下一個符合的星期幾
    if repeat and weekly_day is not None:
        days_ahead = (weekly_day - now.weekday()) % 7
        if days_ahead == 0 and trigger_time <= now:
            days_ahead = 7
        trigger_time = (now + timedelta(days=days_ahead)).replace(
            hour=trigger_time.hour, minute=trigger_time.minute, second=0, microsecond=0)

    return {"action": "add", "content": content, "trigger_time": trigger_time, "repeat": repeat}


def _adjust_hour(message: str, hour: int) -> int:
    if any(kw in message for kw in ["下午", "晚上"]) and hour < 12:
        return hour + 12
    if any(kw in message for kw in ["凌晨"]) and hour >= 12:
        return hour - 12
    return hour
