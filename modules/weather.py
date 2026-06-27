"""
天氣預報模組
使用中央氣象署開放資料平台 API
文件：https://opendata.cwa.gov.tw/dist/opendata-swagger.html
"""

import os
import requests

CWB_API_KEY = os.getenv("CWB_API_KEY")
CWB_BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"

# 縣市名稱對應（支援簡稱輸入）
CITY_ALIASES = {
    "台北": "臺北市", "臺北": "臺北市", "北市": "臺北市",
    "新北": "新北市", "板橋": "新北市",
    "桃園": "桃園市",
    "台中": "臺中市", "臺中": "臺中市", "中市": "臺中市",
    "台南": "臺南市", "臺南": "臺南市", "南市": "臺南市",
    "高雄": "高雄市", "高市": "高雄市",
    "基隆": "基隆市",
    "新竹市": "新竹市", "新竹縣": "新竹縣", "新竹": "新竹市",
    "苗栗": "苗栗縣",
    "彰化": "彰化縣",
    "南投": "南投縣",
    "雲林": "雲林縣",
    "嘉義市": "嘉義市", "嘉義縣": "嘉義縣", "嘉義": "嘉義市",
    "屏東": "屏東縣",
    "宜蘭": "宜蘭縣",
    "花蓮": "花蓮縣",
    "台東": "臺東縣", "臺東": "臺東縣",
    "澎湖": "澎湖縣",
    "金門": "金門縣",
    "連江": "連江縣", "馬祖": "連江縣",
}

# 天氣描述轉換成 emoji
def weather_emoji(description: str) -> str:
    desc = description.lower()
    if "晴" in desc and "雲" not in desc:
        return "☀️"
    elif "多雲" in desc or "晴時多雲" in desc:
        return "⛅"
    elif "陰" in desc:
        return "☁️"
    elif "大雨" in desc or "豪雨" in desc:
        return "⛈️"
    elif "雨" in desc or "雷" in desc:
        return "🌧️"
    elif "霧" in desc:
        return "🌫️"
    else:
        return "🌤️"


def normalize_city(city_name: str) -> str | None:
    """把使用者輸入的城市名稱轉成 API 認識的標準名稱"""
    # 先直接比對
    for alias, standard in CITY_ALIASES.items():
        if alias in city_name:
            return standard
    return None


def get_weather_forecast(city_name: str) -> str:
    """
    查詢指定縣市的天氣預報（今天 + 未來三天）
    API：F-C0032-001（36小時預報）
    """
    city = normalize_city(city_name)
    if not city:
        return f"抱歉，找不到「{city_name}」的天氣資料。\n\n請輸入台灣的縣市名稱，例如：\n台北、台中、高雄、花蓮…"

    url = f"{CWB_BASE_URL}/F-C0032-001"
    params = {
        "Authorization": CWB_API_KEY,
        "locationName": city,
        "elementName": "Wx,PoP,MinT,MaxT",
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return "天氣查詢暫時無法使用，請稍後再試。"

    try:
        locations = data["records"]["location"]
        if not locations:
            return f"找不到「{city_name}」的天氣資料。"

        loc = locations[0]
        elements = {e["elementName"]: e["time"] for e in loc["weatherElement"]}

        lines = [f"📍 {city} 天氣預報\n"]

        # 36小時預報分三個時段
        time_slots = elements["Wx"]
        for i, slot in enumerate(time_slots[:3]):
            start = slot["startTime"][5:16].replace("T", " ")  # MM-DD HH:mm
            end = slot["endTime"][5:16].replace("T", " ")
            wx_desc = slot["parameter"]["parameterName"]
            pop = elements["PoP"][i]["parameter"]["parameterName"]  # 降雨機率
            min_t = elements["MinT"][i]["parameter"]["parameterName"]
            max_t = elements["MaxT"][i]["parameter"]["parameterName"]

            emoji = weather_emoji(wx_desc)

            # 第一個時段標示「今天」
            if i == 0:
                label = "今天"
            elif i == 1:
                label = "今晚/明晨"
            else:
                label = "明天"

            lines.append(
                f"{emoji} {label}（{start}～{end[5:]}）\n"
                f"   天氣：{wx_desc}\n"
                f"   氣溫：{min_t}°C ～ {max_t}°C\n"
                f"   降雨機率：{pop}%\n"
            )

        lines.append("💡 資料來源：中央氣象署")
        return "\n".join(lines)

    except (KeyError, IndexError):
        return "天氣資料解析失敗，請稍後再試。"
