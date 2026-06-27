"""
記事本模組
支援新增、查詢（關鍵字/年月）、刪除筆記
每位 LINE 用戶只能存取自己的筆記（user_id 隔離）
"""

import os
from supabase import create_client


def get_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY")
    return create_client(url, key)


def add_note(content: str, user_id: str = None) -> str:
    try:
        sb = get_client()
        sb.table("notes").insert({"content": content, "user_id": user_id}).execute()
        return f"✅ 已記下來了！\n\n「{content}」"
    except Exception:
        return "抱歉，記事失敗了，請再試一次。"


def search_notes(keyword: str = None, year: int = None, month: int = None,
                 week: str = None, user_id: str = None) -> str:
    try:
        from datetime import datetime, timezone, timedelta
        sb = get_client()
        query = sb.table("notes").select("*").order("created_at", desc=True)

        if user_id:
            query = query.eq("user_id", user_id)

        if week:
            now = datetime.now(timezone(timedelta(hours=8)))
            today = now.date()
            weekday = today.weekday()
            if week == "this":
                start = today - timedelta(days=weekday)
            else:
                start = today - timedelta(days=weekday + 7)
            end = start + timedelta(days=6)
            query = query.gte("created_at", f"{start}T00:00:00+08:00") \
                         .lte("created_at", f"{end}T23:59:59+08:00")
        elif year and month:
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            query = query.gte("created_at", f"{year}-{month:02d}-01") \
                         .lte("created_at", f"{year}-{month:02d}-{last_day}T23:59:59")
        elif year:
            query = query.gte("created_at", f"{year}-01-01") \
                         .lte("created_at", f"{year}-12-31T23:59:59")

        result = query.execute()
        notes = result.data

        if keyword:
            notes = [n for n in notes if keyword in n["content"]]

        if not notes:
            if keyword:
                return f"找不到含有「{keyword}」的筆記。"
            elif year and month:
                return f"找不到 {year} 年 {month} 月的筆記。"
            else:
                return "目前還沒有任何筆記。\n\n您可以說「記一下 XXX」來新增筆記。"

        lines = ["📒 筆記清單\n"]
        for n in notes[:20]:
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromisoformat(n["created_at"].replace("Z", "+00:00"))
            tw = dt.astimezone(timezone(timedelta(hours=8)))
            date_str = tw.strftime("%m/%d %H:%M")
            lines.append(f"[{n['id']}] {date_str}\n{n['content']}\n")

        if len(result.data) > 20:
            lines.append(f"（共 {len(result.data)} 筆，僅顯示最新 20 筆）")

        uid = user_id or ""
        lines.append(f"\n📱 網頁版筆記本：\nhttps://line-bot-mama.onrender.com/notes?user_id={uid}")
        lines.append("⚠️ 此連結只限本人使用，請勿傳給他人")
        return "\n".join(lines)
    except Exception:
        return "抱歉，查詢筆記失敗了，請再試一次。"


def delete_last_note(user_id: str = None) -> str:
    try:
        sb = get_client()
        query = sb.table("notes").select("id").order("created_at", desc=True).limit(1)
        if user_id:
            query = query.eq("user_id", user_id)
        result = query.execute()
        if not result.data:
            return "目前還沒有任何筆記。"
        note_id = result.data[0]["id"]
        sb.table("notes").delete().eq("id", note_id).execute()
        return f"✅ 已刪除最後一筆筆記 [{note_id}]。"
    except Exception:
        return "抱歉，刪除失敗了，請再試一次。"


def delete_note(note_id: int, user_id: str = None) -> str:
    try:
        sb = get_client()
        query = sb.table("notes").delete().eq("id", note_id)
        if user_id:
            query = query.eq("user_id", user_id)  # 只能刪自己的
        result = query.execute()
        if result.data:
            return f"✅ 已刪除筆記 [{note_id}]。"
        return f"找不到筆記 [{note_id}]，請確認編號是否正確。"
    except Exception:
        return "抱歉，刪除失敗了，請再試一次。"


def parse_note_query(user_message: str) -> dict:
    import re

    # 刪除：「刪除筆記5」「刪除第5筆」「刪除5」「刪掉筆記[5]」「刪掉最後一筆」
    m = re.search(r"(?:刪除|刪掉|移除)(?:筆記|記事|第)?\s*[\[#＃【]?(\d+)[\]】]?(?:\s*筆)?", user_message)
    if m:
        return {"action": "delete", "note_id": int(m.group(1))}

    if any(kw in user_message for kw in ["刪掉最後一筆", "刪除最後一筆", "刪掉剛才", "刪除剛才", "刪掉上一筆", "刪除上一筆"]):
        return {"action": "delete_last"}

    # 查詢：這週 / 上週
    if any(kw in user_message for kw in ["這週筆記", "本週筆記", "這周筆記", "本周筆記"]):
        return {"action": "search", "week": "this"}
    if any(kw in user_message for kw in ["上週筆記", "上周筆記"]):
        return {"action": "search", "week": "last"}

    # 查詢：202606 格式
    m = re.search(r"查筆記\s*(\d{6})", user_message)
    if m:
        ym = m.group(1)
        return {"action": "search", "year": int(ym[:4]), "month": int(ym[4:])}

    # 查詢：年月
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", user_message)
    if m and any(kw in user_message for kw in ["查", "找", "筆記", "記事"]):
        return {"action": "search", "year": int(m.group(1)), "month": int(m.group(2))}

    m = re.search(r"(\d{4})\s*年", user_message)
    if m and any(kw in user_message for kw in ["查", "找", "筆記", "記事"]):
        return {"action": "search", "year": int(m.group(1))}

    # 查詢：關鍵字
    m = re.search(r"(?:查筆記|找筆記|查記事|搜尋筆記)\s*(.+)?", user_message)
    if m:
        keyword = m.group(1).strip() if m.group(1) else None
        return {"action": "search", "keyword": keyword}

    if any(kw in user_message for kw in ["查筆記", "找筆記", "看筆記", "我的筆記", "所有筆記"]):
        return {"action": "search"}

    # 新增
    m = re.search(r"(?:記一下|記下來|幫我記|備忘)[：:\s]*(.+)", user_message, re.DOTALL)
    if m:
        return {"action": "add", "content": m.group(1).strip()}

    return {"action": None}
