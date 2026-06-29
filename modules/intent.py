"""
意圖判斷模組
用關鍵字比對判斷使用者的訊息屬於哪個功能
（Gemini 額度不足時的備用方案，也是第一階段的主要方案）
"""

import os
import re

# 各功能的關鍵字清單
WEATHER_KEYWORDS = [
    "天氣", "氣溫", "溫度", "下雨", "會不會雨", "帶傘", "要帶傘", "需要傘",
    "晴", "陰", "颱風", "空氣品質", "PM2.5", "紫外線", "濕度", "預報",
]

CITY_LIST = [
    "台北", "臺北", "新北", "板橋", "桃園", "台中", "臺中", "台南", "臺南",
    "高雄", "基隆", "新竹", "苗栗", "彰化", "南投", "雲林", "嘉義",
    "屏東", "宜蘭", "花蓮", "台東", "臺東", "澎湖", "金門", "馬祖",
]

BUS_KEYWORDS = ["公車", "幾分鐘到", "到站", "幾號公車", "班次", "還有多久", "幾分鐘", "幾點到", "幹線", "快速公車", "捷運公車", "號公車", "號車"]

TRANSIT_KEYWORDS = ["怎麼去", "如何去", "搭捷運", "捷運", "高鐵", "台鐵", "火車", "路線", "怎麼搭", "幾站",
                    "幾點出發", "幾點到", "到達", "要到", "才來得及", "搭什麼車"]

NOTE_KEYWORDS = ["記一下", "記下來", "備忘", "記事", "待辦", "購物清單", "記得買",
                 "查筆記", "找筆記", "看筆記", "我的筆記", "所有筆記", "刪除筆記", "刪掉筆記",
                 "查這週筆記", "查上週筆記", "這週筆記", "上週筆記"]

REMINDER_KEYWORDS = ["提醒我", "幾點提醒", "設提醒", "叫我", "到時候提醒",
                     "我的提醒", "提醒清單", "查提醒", "取消提醒", "取消全部提醒",
                     "刪除全部提醒", "清除全部提醒",
                     "幫媽媽提醒", "幫雷京提醒", "幫爸爸提醒", "幫松哥提醒", "幫方方提醒", "幫姊姊提醒", "幫伊嵐提醒", "幫妹妹提醒",
                     "幫媽媽查提醒", "幫雷京查提醒", "幫爸爸查提醒", "幫松哥查提醒", "幫方方查提醒", "幫姊姊查提醒", "幫伊嵐查提醒", "幫妹妹查提醒",
                     "幫媽媽取消提醒", "幫雷京取消提醒", "幫爸爸取消提醒", "幫松哥取消提醒", "幫方方取消提醒", "幫姊姊取消提醒", "幫伊嵐取消提醒", "幫妹妹取消提醒"]


def detect_city(text: str) -> str | None:
    """從文字中找出城市名稱"""
    for city in CITY_LIST:
        if city in text:
            return city
    return None


def detect_intent(user_message: str) -> dict:
    """用關鍵字判斷使用者訊息的意圖，回傳 dict"""

    # 天氣：有天氣關鍵字，或有城市名稱加上天氣相關詞
    for kw in WEATHER_KEYWORDS:
        if kw in user_message:
            city = detect_city(user_message)
            return {"intent": "weather", "city": city}

    # 如果只說城市名稱（例如「台北」），也當作天氣查詢
    city = detect_city(user_message)
    if city and len(user_message) <= 6:
        return {"intent": "weather", "city": city}

    # 記事本關鍵字優先（避免長文內含「從...到...」被誤判為交通）
    for kw in NOTE_KEYWORDS:
        if kw in user_message:
            return {"intent": "note"}

    # 有明確出發地「從A到B」或「A到B怎麼去」→ 優先走 transit
    import re as _re
    if _re.search(r"從.+到.+|.+到.+怎麼去|.+怎麼去", user_message):
        return {"intent": "transit"}

    # 公車
    for kw in BUS_KEYWORDS:
        if kw in user_message:
            return {"intent": "bus"}

    # 「22號象山站」「226到捷運行天宮站」這種句首數字寫法
    import re
    if re.search(r"^\d+[號路,，、到至]|^\d+\s+", user_message):
        return {"intent": "bus"}

    # 交通路線
    for kw in TRANSIT_KEYWORDS:
        if kw in user_message:
            return {"intent": "transit"}

    # 提醒
    for kw in REMINDER_KEYWORDS:
        if kw in user_message:
            return {"intent": "reminder"}

    # 其他都走生活問答
    return {"intent": "qa"}
