"""
公車即時到站模組
使用 TDX 運輸資料流通服務平台 API
文件：https://tdx.transportdata.tw/api-service/swagger
"""

import os
import requests
import time

TDX_CLIENT_ID = os.getenv("TDX_CLIENT_ID")
TDX_CLIENT_SECRET = os.getenv("TDX_CLIENT_SECRET")
TDX_AUTH_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
TDX_BASE_URL = "https://tdx.transportdata.tw/api/basic"

# 快取 token，避免每次都重新取得
_token_cache = {"token": None, "expires_at": 0}


def get_tdx_token() -> str:
    """取得 TDX API 的 access token（有快取，10 分鐘內不重複取）"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    resp = requests.post(
        TDX_AUTH_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": os.getenv("TDX_CLIENT_ID"),
            "client_secret": os.getenv("TDX_CLIENT_SECRET"),
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 600) - 30
    return _token_cache["token"]


def tdx_get(path: str, params: dict = None) -> dict:
    """發送 TDX API GET 請求"""
    token = get_tdx_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = TDX_BASE_URL + path
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


# 縣市代碼對應
CITY_CODE = {
    "台北": "Taipei", "臺北": "Taipei", "台北市": "Taipei",
    "新北": "NewTaipei", "新北市": "NewTaipei",
    "桃園": "Taoyuan", "桃園市": "Taoyuan",
    "台中": "Taichung", "臺中": "Taichung", "台中市": "Taichung",
    "台南": "Tainan", "臺南": "Tainan", "台南市": "Tainan",
    "高雄": "Kaohsiung", "高雄市": "Kaohsiung",
    "基隆": "Keelung", "基隆市": "Keelung",
    "新竹市": "Hsinchu", "新竹縣": "HsinchuCounty",
    "苗栗": "MiaoliCounty",
    "彰化": "ChanghuaCounty",
    "南投": "NantouCounty",
    "雲林": "YunlinCounty",
    "嘉義市": "Chiayi", "嘉義縣": "ChiayiCounty",
    "屏東": "PingtungCounty",
    "宜蘭": "YilanCounty",
    "花蓮": "HualienCounty",
    "台東": "TaitungCounty", "臺東": "TaitungCounty",
}

ARRIVAL_STATUS = {
    0: "即將進站",
    1: "即將進站",
    2: "交管不停靠",
    3: "末班車已過",
    4: "今日未營運",
}


def format_estimate(seconds: int | None, status: int | None) -> str:
    """把秒數轉成易讀的時間描述"""
    if status is not None and status in ARRIVAL_STATUS and status >= 2:
        return ARRIVAL_STATUS[status]
    if seconds is None:
        return "目前無班次資料（可能已末班）"
    if seconds <= 60:
        return "即將進站"
    minutes = seconds // 60
    if minutes < 60:
        return f"約 {minutes} 分鐘後到站"
    hours = minutes // 60
    mins = minutes % 60
    return f"約 {hours} 小時 {mins} 分鐘後到站"


def get_bus_arrival(route_name: str, stop_name: str, city: str, direction: int | None = None) -> str:
    """
    查詢公車即時到站時間
    route_name: 路線名稱，例如 "1"、"敦化幹線"
    stop_name: 站名，例如 "台北車站"
    city: 城市名稱（中文），例如 "台北"
    direction: 0=去程, 1=返程, None=兩個方向都查
    """
    city_code = CITY_CODE.get(city)
    if not city_code:
        return f"抱歉，目前不支援「{city}」的公車查詢。"

    try:
        # 查詢該路線在指定站的即時到站資料
        path = f"/v2/Bus/EstimatedTimeOfArrival/City/{city_code}/{route_name}"
        # 用 contains 模糊比對站名，因為 TDX 站名可能帶括號如「吳興國小(松仁)」
        stop_keyword = stop_name.replace("站", "")
        params = {
            "$filter": f"contains(StopName/Zh_tw,'{stop_keyword}')",
            "$format": "JSON",
        }
        data = tdx_get(path, params)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"TDX API error: {e}")
        return "公車資料暫時無法取得，請稍後再試。"

    if not data:
        return (
            f"找不到「{route_name}」在「{stop_name}」的資料 😅\n\n"
            f"可能的原因：\n"
            f"・路線號碼打錯了\n"
            f"・站名和公車上寫的不完全一樣\n\n"
            f"小提示：站牌上都有寫站名，可以照著說給我聽，例如「{route_name}，捷運忠孝復興站」"
        )

    # 先取 StopOfRoute，建立 (RouteUID, Direction) → 終點站名稱 的對應
    # 同時記錄每個 RouteUID 的合法站牌清單，用來過濾 ETA 結果
    dest_names = {}   # (route_uid, d) → 終點站名
    valid_stops = {}  # (route_uid, d) → set of stop name keywords
    stop_keyword = stop_name.replace("站", "")
    try:
        stop_data = tdx_get(f"/v2/Bus/StopOfRoute/City/{city_code}/{route_name}", {"$format": "JSON"})
        for item in stop_data:
            uid = item.get("RouteUID", "")
            d = item.get("Direction", 0)
            stops = item.get("Stops", [])
            key = (uid, d)
            if stops:
                dest_names[key] = stops[-1].get("StopName", {}).get("Zh_tw", "")
            valid_stops[key] = {s.get("StopName", {}).get("Zh_tw", "") for s in stops}
    except Exception:
        pass

    import logging as _log
    logger = _log.getLogger(__name__)
    logger.info(f"StopOfRoute keys: {list(dest_names.keys())}")

    # 依 (RouteUID, Direction) 分組 ETA，且只保留 stop 真的在該路線上的
    results = {}
    for item in data:
        d = item.get("Direction", 0)
        if direction is not None and d != direction:
            continue
        uid = item.get("RouteUID", "")
        key = (uid, d)
        # 若有 StopOfRoute 資料，確認此站確實在路線上
        if valid_stops and key in valid_stops:
            stop_names_in_route = valid_stops[key]
            if not any(stop_keyword in sn for sn in stop_names_in_route):
                continue
        seconds = item.get("EstimateTime")
        status = item.get("StopStatus")
        if key not in results:
            results[key] = []
        results[key].append((seconds, status))

    if not results:
        return f"找不到「{route_name}」在「{stop_name}」站的即時資料。"

    lines = [f"🚌 {route_name} ── {stop_name}\n"]
    for key, arrivals in sorted(results.items(), key=lambda x: x[0][1]):
        uid, d = key
        dest = dest_names.get(key, "")
        direction_label = f"往{dest}" if dest else ("方向一" if d == 0 else "方向二")
        lines.append(f"{direction_label}：")

        arrivals_sorted = sorted(
            [a for a in arrivals if a[0] is not None],
            key=lambda x: x[0]
        ) + [a for a in arrivals if a[0] is None]

        for i, (seconds, status) in enumerate(arrivals_sorted[:2]):
            label = "下一班" if i == 0 else "再下一班"
            lines.append(f"  {label}：{format_estimate(seconds, status)}")
        lines.append("")

    lines.append("⚠️ 資料來源：TDX，僅供參考")
    return "\n".join(lines)


def parse_bus_query(user_message: str) -> dict:
    """
    從使用者訊息中解析公車查詢所需資訊
    回傳 {"route": ..., "stop": ..., "city": ..., "complete": bool}
    """
    import re

    result = {"route": None, "stop": None, "city": "台北", "complete": False}

    # 抓路線號碼或名稱（支援「226公車」「公車226」「226到」「搭226」「22號」「承德幹線」等格式）
    route_match = re.search(
        r"(\d+)\s*[號路]?\s*公車"            # 226號公車 / 226公車
        r"|公車\s*[第]?\s*(\d+)\s*[號路]?"   # 公車226
        r"|[搭坐]\s*(\d+)"                   # 搭226
        r"|^(\d+)\s*[到至,，、]"             # 226到/226、（句首）
        r"|^(\d+)\s*[號路]"                  # 22號（句首）
        r"|([^\s]{1,6}幹線|[^\s]{1,6}快速)", # 承德幹線
        user_message
    )
    if route_match:
        result["route"] = next(g for g in route_match.groups() if g is not None)

    # 抓城市
    for city in CITY_CODE:
        if city in user_message:
            result["city"] = city
            break

    # 把路線相關文字先移除，避免干擾站名解析
    cleaned = re.sub(r"\d+\s*[號路]?\s*公車|公車\s*[第]?\s*\d+\s*[號路]?|\d+\s*[號路]|\d+\s*[到至]\s*", "", user_message)

    # 抓站名：支援「XXX站」「到XXX站」「到XXX（還有/多久）」等各種格式
    stop_match = re.search(
        r"(?:在|到|停)\s*([^\s在到停還有多久幾分鐘]{2,10}?)(?:站|還有|多久|幾分|$)"
        r"|([^\s在到停還有多久幾分鐘]{2,10}?)\s*站",
        cleaned
    )
    if stop_match:
        stop = stop_match.group(1) or stop_match.group(2)
        stop = re.sub(r"^[,，、\s]+|[,，、\s]+$", "", stop)  # 去掉頭尾標點
        if stop and not re.search(r"[多幾分鐘還有久]", stop):
            result["stop"] = stop if stop.endswith("站") else stop + "站"

    result["complete"] = bool(result["route"] and result["stop"])
    return result
