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


def get_bus_arrival(route_name: str, stop_name: str, city: str,
                    direction: int | None = None, exact: bool = False) -> str:
    """
    查詢公車即時到站時間
    route_name: 路線名稱，例如 "1"、"敦化幹線"
    stop_name: 站名，例如 "台北車站"
    city: 城市名稱（中文），例如 "台北"
    exact: True 時用精確比對（從子站選單選出的站名），避免 replace("站","") 誤刪
    """
    city_code = CITY_CODE.get(city)
    if not city_code:
        return f"抱歉，目前不支援「{city}」的公車查詢。"

    try:
        path = f"/v2/Bus/EstimatedTimeOfArrival/City/{city_code}/{route_name}"
        if exact:
            # 精確站名（從 TDX 取得），直接用 eq 比對，避免 replace 誤刪字
            stop_keyword = stop_name
            params = {
                "$filter": f"StopName/Zh_tw eq '{stop_name}'",
                "$format": "JSON",
            }
        else:
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

    # 只保留站名以關鍵字開頭的資料（排除如「信義松仁路口(松仁)」這類不同站）
    # 若 startswith 無結果（如「行天宮」找「捷運行天宮站」），退回 contains 比對
    # exact 模式已用 eq 精確比對，不需要再過濾
    if not exact:
        startswith_data = [item for item in data
                           if item.get("StopName", {}).get("Zh_tw", "").startswith(stop_keyword)]
        if startswith_data:
            data = startswith_data
        else:
            data = [item for item in data
                    if stop_keyword in item.get("StopName", {}).get("Zh_tw", "")]

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

    # 每個 Direction 只選一個最佳 RouteUID：
    # 優先選「有實際 ETA 秒數」的，再比站數（站數多 = 主線）
    stop_count = {k: len(v) for k, v in valid_stops.items()}

    # 先收集各 (uid, d) 的 ETA 清單
    uid_dir_arrivals = {}  # (uid, d) → [(seconds, status), ...]
    for item in data:
        d = item.get("Direction", 0)
        if direction is not None and d != direction:
            continue
        uid = item.get("RouteUID", "")
        key_uid = (uid, d)
        if valid_stops and key_uid in valid_stops:
            if not any(stop_keyword in sn for sn in valid_stops[key_uid]):
                continue
        seconds = item.get("EstimateTime")
        status = item.get("StopStatus")
        uid_dir_arrivals.setdefault(key_uid, []).append((seconds, status))

    # 每個 Direction 選最佳 RouteUID
    best_uid_per_dir = {}  # d → uid
    for (uid, d) in uid_dir_arrivals:
        has_eta = any(s is not None for s, _ in uid_dir_arrivals[(uid, d)])
        n_stops = stop_count.get((uid, d), 0)
        if d not in best_uid_per_dir:
            best_uid_per_dir[d] = (uid, has_eta, n_stops)
        else:
            cur_uid, cur_has_eta, cur_stops = best_uid_per_dir[d]
            # 有 ETA 優先；同樣有 ETA 時選站數多的
            if (has_eta, n_stops) > (cur_has_eta, cur_stops):
                best_uid_per_dir[d] = (uid, has_eta, n_stops)

    # 收集最佳 RouteUID 的 ETA，並去重（同方向秒數相差 <120 秒視為同一班車）
    best_per_dir = {}
    for d, (best_uid, _, _) in best_uid_per_dir.items():
        key_uid = (best_uid, d)
        raw = uid_dir_arrivals.get(key_uid, [])
        # 去重：只保留不重複的班次（秒數差 >= 120 秒才算不同班）
        deduped = []
        for sec, status in sorted((x for x in raw if x[0] is not None), key=lambda x: x[0]):
            if not deduped or sec - deduped[-1][0] >= 180:
                deduped.append((sec, status))
        # 末班/未營運的放最後
        no_eta = [(sec, status) for sec, status in raw if sec is None]
        arrivals = deduped + no_eta
        best_per_dir[d] = {
            "arrivals": arrivals,
            "dest": dest_names.get(key_uid, ""),
            "has_eta": bool(deduped),
        }

    # 過濾掉完全沒有到站秒數的方向
    results = {d: v for d, v in best_per_dir.items() if v["has_eta"]}

    if not results:
        return f"找不到「{route_name}」在「{stop_name}」站的即時資料。"

    lines = [f"🚌 {route_name} ── {stop_name}\n"]
    for d, entry in sorted(results.items()):
        dest = entry["dest"]
        direction_label = f"往{dest}" if dest else ("方向一" if d == 0 else "方向二")
        lines.append(f"{direction_label}：")
        arrivals = entry["arrivals"]

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


def tdx_nearest_stop_name(route_name: str, lat: float, lng: float,
                          city_code: str = "Taipei") -> str | None:
    """
    給定路線名稱和座標，從 TDX StopOfRoute 找最近的站牌名稱。
    用於修正 Google Maps 回傳的站名與實際站牌不符的問題。
    """
    import math
    try:
        stops_data = tdx_get(
            f"/v2/Bus/StopOfRoute/City/{city_code}/{route_name}",
            {"$format": "JSON"}
        )
    except Exception:
        return None

    best_name = None
    best_dist = float("inf")
    for item in stops_data:
        for stop in item.get("Stops", []):
            pos = stop.get("StopPosition", {})
            slat = pos.get("PositionLat")
            slng = pos.get("PositionLon")
            if slat is None or slng is None:
                continue
            # 用簡化歐氏距離（小範圍夠精確）
            dist = math.hypot(slat - lat, slng - lng)
            if dist < best_dist:
                best_dist = dist
                best_name = stop.get("StopName", {}).get("Zh_tw", "")
    return best_name or None


def get_stop_options(route_name: str, stop_name: str, city: str) -> list[str]:
    """
    查詢該路線在指定站名有哪些子站（以站名開頭比對）。
    回傳唯一站名清單；只有 1 個表示不需要澄清。
    """
    city_code = CITY_CODE.get(city)
    if not city_code:
        return []
    stop_keyword = stop_name.replace("站", "")
    try:
        data = tdx_get(
            f"/v2/Bus/EstimatedTimeOfArrival/City/{city_code}/{route_name}",
            {"$filter": f"contains(StopName/Zh_tw,'{stop_keyword}')", "$format": "JSON"},
        )
    except Exception:
        return []
    def sn(item):
        return item.get("StopName", {}).get("Zh_tw", "")
    startswith_names = sorted({sn(i) for i in data if sn(i).startswith(stop_keyword)})
    names = startswith_names if startswith_names else sorted({sn(i) for i in data if stop_keyword in sn(i)})
    return names


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
        r"|^(\d+)\s+[一-鿿]"         # 22 松平路口（句首數字+空格+中文）
        r"|[查問]\s*(\d+)"                   # 查22、問22
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
    cleaned = re.sub(r"\d+\s*[號路]?\s*公車|公車\s*[第]?\s*\d+\s*[號路]?|\d+\s*[號路]|\d+\s*[到至]\s*|^[查問]", "", user_message)

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

    # 口語輸入fallback：沒抓到站名時，把路線/城市/標點移除後的剩餘文字當站名
    if not result["stop"]:
        fallback = re.sub(
            r"\d+\s*[號路]?\s*公車|公車\s*[第]?\s*\d+\s*[號路]?|\d+\s*[號路]|\d+"
            r"|[搭坐到在停至查問,，、\s]+|還有|多久|幾分|公車",
            "", cleaned
        ).strip()
        # 移除城市名
        for city in CITY_CODE:
            fallback = fallback.replace(city, "")
        fallback = fallback.strip()
        if 2 <= len(fallback) <= 10 and not re.search(r"[多幾分鐘還有久]", fallback):
            result["stop"] = fallback if fallback.endswith("站") else fallback + "站"

    result["complete"] = bool(result["route"] and result["stop"])
    return result
