"""
交通路線查詢模組
使用 Google Maps Directions API
支援捷運、公車、台鐵、高鐵，含跨城市路線
"""

import os
import requests
from datetime import datetime

GOOGLE_MAPS_API_KEY = None  # 每次呼叫時從環境變數讀取

LOCATION_ALIAS = {
    "家裡": "台北市信義區吳興街518巷",
    "家": "台北市信義區吳興街518巷",
    "吳興街總站": "台北市信義區松仁路277號",
    "北門": "台北市大同區塔城街10號",
    "西門": "台北市萬華區西門町",
    "東門": "台北市大安區東門",
    "輔大捷運站": "新北市新莊區輔大站",
    "輔大站": "新北市新莊區輔大站",
}


def get_directions(origin: str, destination: str, orig_destination: str = None,
                   arrival_time_str: str = None, query_type: str = "route") -> str:
    """
    查詢從 origin 到 destination 的大眾運輸路線
    回傳格式化的中文路線說明
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")

    # 是否從家裡出發（決定是否顯示騎車提示）
    is_home_origin = origin in ("家裡", "家")

    # 別名對應
    origin = LOCATION_ALIAS.get(origin, origin)
    destination = LOCATION_ALIAS.get(destination, destination)

    # 先用 Geocoding API 確認地點存在（加上「台灣」避免找到國外地點）
    def geocode(place: str) -> str:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": f"{place} 台灣", "language": "zh-TW", "key": api_key},
            timeout=10,
        )
        results = r.json().get("results", [])
        if results:
            return results[0]["formatted_address"]
        return place

    # 將到達時間字串轉為 Unix timestamp
    arrival_ts = None
    if arrival_time_str:
        from datetime import datetime, date
        h, m = map(int, arrival_time_str.split(":"))
        arrival_dt = datetime.combine(date.today(), datetime.min.time().replace(hour=h, minute=m))
        arrival_ts = int(arrival_dt.timestamp())

    def query_routes(extra_params: dict) -> list:
        params = {
            "origin": origin + " 台灣",
            "destination": destination + " 台灣",
            "mode": "transit",
            "language": "zh-TW",
            "region": "tw",
            "alternatives": "true",
            "key": api_key,
            **extra_params,
        }
        if arrival_ts:
            params["arrival_time"] = arrival_ts
        r = requests.get(
            "https://maps.googleapis.com/maps/api/directions/json",
            params=params,
            timeout=15,
        )
        d = r.json()
        if d.get("status") == "OK":
            return d["routes"]
        return []

    import time as _time
    now_ts = int(_time.time())

    # 查四次：最快、少走路、偏好公車、偏好捷運，合併去重
    # 不帶 departure_time，回傳一般性最佳路線，結果更穩定
    routes_fast = query_routes({})
    routes_less_walk = query_routes({"transit_routing_preference": "less_walking"})
    routes_bus = query_routes({"transit_mode": "bus", "transit_routing_preference": "less_walking"})
    routes_rail = query_routes({"transit_mode": "subway|tram|rail", "transit_routing_preference": "less_walking"})

    if not any([routes_fast, routes_less_walk, routes_bus, routes_rail]):
        return f"找不到從「{origin}」到「{destination}」的大眾運輸路線。\n\n請確認地點名稱，或試試說更詳細的地址。"

    def route_key(route) -> str:
        """用主要乘車段的線路名稱當去重 key"""
        leg = route["legs"][0]
        transit_steps = [s for s in leg["steps"] if s.get("travel_mode") == "TRANSIT"]
        return "|".join(
            (s["transit_details"]["line"].get("short_name") or s["transit_details"]["line"].get("name", ""))
            for s in transit_steps
        )

    seen = set()
    all_routes = []
    for route in routes_fast + routes_less_walk + routes_bus + routes_rail:
        k = route_key(route)
        if k not in seen:
            seen.add(k)
            all_routes.append(route)

    # 用 TDX 查詢各路線班距，平行查詢節省時間
    def get_headway(route_name: str, city_code: str = "Taipei") -> int:
        """回傳代表性班距（分鐘），用最大班距的平均值，較接近實際等待時間。"""
        try:
            from modules.bus import tdx_get
            data = tdx_get(f"/v2/Bus/Schedule/City/{city_code}/{route_name}", {"$format": "JSON"})
            if not data:
                return 99
            freqs = data[0].get("Frequencys", [])
            if freqs:
                max_headways = [f.get("MaxHeadwayMins") for f in freqs if f.get("MaxHeadwayMins")]
                if max_headways:
                    return int(sum(max_headways) / len(max_headways))
            # 固定時刻表：從班次時間推算平均班距
            timetables = data[0].get("Timetables", [])
            if timetables:
                times = []
                for tt in timetables:
                    for stop in tt.get("StopTimes", []):
                        t = stop.get("DepartureTime", "")
                        if t:
                            try:
                                h, m = t.split(":")[:2]
                                times.append(int(h) * 60 + int(m))
                            except Exception:
                                pass
                times = sorted(set(times))
                if len(times) >= 2:
                    gaps = [times[i+1] - times[i] for i in range(len(times)-1) if times[i+1] - times[i] < 120]
                    if gaps:
                        return int(sum(gaps) / len(gaps))
                return 30  # 無法計算時給 30 分鐘
            return 99
        except Exception:
            return 99

    # 收集所有路線用到的公車路線名稱
    import threading
    bus_routes_needed = set()
    for route in all_routes:
        for s in route["legs"][0]["steps"]:
            if s.get("travel_mode") == "TRANSIT":
                vehicle = s["transit_details"]["line"].get("vehicle", {}).get("name", "")
                if vehicle in ("公車", "Bus"):
                    line = s["transit_details"]["line"].get("short_name") or s["transit_details"]["line"].get("name", "")
                    if line:
                        bus_routes_needed.add(line)

    headway_cache = {}
    threads = []
    lock = threading.Lock()

    def fetch_headway(name):
        hw = get_headway(name)
        with lock:
            headway_cache[name] = hw

    for name in bus_routes_needed:
        t = threading.Thread(target=fetch_headway, args=(name,))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    def headway_penalty(route) -> int:
        """班距懲罰秒數，上限 599 秒（不足10分鐘），確保班距不能蓋過時間差"""
        steps = route["legs"][0]["steps"]
        worst_headway = 0
        for s in steps:
            if s.get("travel_mode") == "TRANSIT":
                vehicle = s["transit_details"]["line"].get("vehicle", {}).get("name", "")
                if vehicle in ("公車", "Bus"):
                    line = s["transit_details"]["line"].get("short_name") or s["transit_details"]["line"].get("name", "")
                    hw = headway_cache.get(line, 99)
                    worst_headway = max(worst_headway, hw)
        return min(worst_headway * 30, 599)  # 最多加 599 秒，不超過 10 分鐘

    def walk_penalty(route) -> int:
        """長距離步行懲罰秒數"""
        steps = route["legs"][0]["steps"]
        penalty = 0
        if steps and steps[0].get("travel_mode") == "WALKING":
            first_walk_mins = steps[0]["duration"]["value"] // 60
            if first_walk_mins > 10:
                penalty += (first_walk_mins - 10) * 120
        if steps and steps[-1].get("travel_mode") == "WALKING":
            last_walk_mins = steps[-1]["duration"]["value"] // 60
            if last_walk_mins > 10:
                penalty += (last_walk_mins - 10) * 120
        return penalty

    def route_score(route) -> int:
        # 實際時間 + 步行懲罰（長距離步行）+ 班距懲罰（上限599秒，不超過10分鐘）
        # 確保時間差 > 10 分鐘時，照時間排；≤ 10 分鐘時班距才能影響結果
        return raw_seconds(route) + walk_penalty(route) + headway_penalty(route)

    def transit_count(route) -> int:
        return sum(1 for s in route["legs"][0]["steps"] if s.get("travel_mode") == "TRANSIT")

    def raw_seconds(route) -> int:
        return sum(s["duration"]["value"] for s in route["legs"][0]["steps"])

    # 全部用 route_score 排序（含步行懲罰 + 班距懲罰），不再強制直達優先
    all_routes = sorted(all_routes, key=route_score)[:3]

    import re as _re

    def clean_addr(addr: str) -> str:
        a = addr.replace("台灣", "").replace("Taiwan", "")
        a = _re.sub(r"^\d{3,5}\s*", "", a.strip())
        return a.strip().strip(",").strip()

    def is_vague(addr: str) -> bool:
        clean = clean_addr(addr)
        parts = [p for p in _re.split(r"[,，]", clean) if p.strip()]
        return len(parts) <= 1

    vehicle_map = {
        "地鐵": ("🚇", "捷運"), "捷運": ("🚇", "捷運"), "Subway": ("🚇", "捷運"),
        "公車": ("🚌", "公車"), "Bus": ("🚌", "公車"),
        "火車": ("🚆", "台鐵"), "Train": ("🚆", "台鐵"), "Rail": ("🚆", "台鐵"),
        "長途列車": ("🚆", "台鐵"), "Local Train": ("🚆", "台鐵區間車"),
        "Express Train": ("🚆", "台鐵自強號"), "Commuter rail": ("🚆", "台鐵"),
        "高速鐵路": ("🚄", "高鐵"), "高鐵": ("🚄", "高鐵"),
        "High-Speed Rail": ("🚄", "高鐵"), "高速火車": ("🚄", "高鐵"),
    }

    line_name_map = {
        "Tamsui-Xinyi Line": "淡水信義線",
        "Bannan Line": "板南線",
        "Zhonghe-Xinlu Line": "中和新蘆線",
        "Wenhu Line": "文湖線",
        "Songshan-Xindian Line": "松山新店線",
        "Circular Line": "環狀線",
        "High-Speed Rail": "",
        "Taiwan Railways": "",
    }

    def strip_html(text: str) -> str:
        """移除 HTML 標籤"""
        return _re.sub(r"<[^>]+>", "", text).strip()

    def format_walking_substeps(step: dict) -> list:
        """把步行的轉彎細節列出來"""
        sub = []
        for s in step.get("steps", []):
            instr = strip_html(s.get("html_instructions", ""))
            dist = s.get("distance", {}).get("text", "")
            if instr:
                sub.append(f"     • {instr}（{dist}）")
        return sub

    def format_route(leg, route_num: int, total_routes: int, orig_dest: str, show_bike: bool = False) -> list:
        steps = leg["steps"]
        step_seconds = sum(s["duration"]["value"] for s in steps)
        step_minutes = (step_seconds + 59) // 60
        total_distance = leg["distance"]["text"]

        # 如果第一段步行 > 10 分鐘且從家裡出發，計算改騎車的總時間
        bike_total_str = ""
        first_step = steps[0] if steps else None
        if show_bike and first_step and first_step.get("travel_mode") == "WALKING":
            first_walk_mins = first_step["duration"]["value"] // 60
            if first_walk_mins > 10:
                bike_mins = max(1, first_walk_mins // 3)
                bike_total = step_minutes - first_walk_mins + bike_mins
                bike_total_str = f"／🚲 騎車約 {bike_total} 分鐘"

        route_lines = []
        if total_routes > 1:
            route_lines.append(f"─── 方案{route_num} ───")
        route_lines.append(f"⏱️ 約 {step_minutes} 分鐘{bike_total_str}　距離：{total_distance}")
        route_lines.append("")

        step_num = 1
        for i, step in enumerate(steps):
            travel_mode = step.get("travel_mode", "")
            duration = step["duration"]["text"]
            distance = step["distance"]["text"]

            if travel_mode == "TRANSIT":
                transit = step.get("transit_details", {})
                line = transit.get("line", {})
                vehicle = line.get("vehicle", {}).get("name", "")
                line_name = line.get("short_name") or line.get("name", "")
                dep_stop = transit.get("departure_stop", {}).get("name", "")
                arr_stop = transit.get("arrival_stop", {}).get("name", "")
                num_stops = transit.get("num_stops", 0)
                headsign = transit.get("headsign", "")

                vehicle_emoji, vehicle_zh = vehicle_map.get(vehicle, ("🚌", vehicle))
                line_name_zh = line_name_map.get(line_name, line_name)
                full_name = f"{vehicle_zh}{' ' + line_name_zh if line_name_zh else ''}"
                headsign_clean = headsign.lstrip("往") if headsign else ""
                direction_text = f"，往{headsign_clean}方向" if headsign_clean else ""

                route_lines.append(f"【第{step_num}段】{vehicle_emoji} 搭{full_name}{direction_text}")
                def add_stop(name):
                    # 站名可能含括號如「信義安和站(信義)」，檢查括號前是否已有站
                    core = _re.sub(r"\([^)]*\)$", "", name).strip()
                    return name if core.endswith("站") else name + "站"
                dep_label = add_stop(dep_stop)
                arr_label = add_stop(arr_stop)
                route_lines.append(f"   🟢 在「{dep_label}」上車")
                route_lines.append(f"   🔴 坐到「{arr_label}」下車（共{num_stops}站，約{duration}）")
                step_num += 1
                route_lines.append("")

            elif travel_mode == "WALKING":
                is_last = (i == len(steps) - 1)
                is_first = (i == 0)
                end_addr = clean_addr(leg["end_address"])

                walk_mins = step["duration"]["value"] // 60
                def bike_hint(mins):
                    if show_bike and mins > 10:
                        return f"／🚲 騎車約 {max(1, mins // 3)} 分鐘"
                    return ""

                if is_first:
                    if walk_mins <= 3:
                        pass  # 出發點就在車站旁，省略
                    else:
                        next_step = steps[i + 1] if i + 1 < len(steps) else None
                        if next_step and next_step.get("travel_mode") == "TRANSIT":
                            dep = next_step["transit_details"]["departure_stop"]["name"]
                            route_lines.append(f"🚶 從出發地步行到「{dep}」站（約{duration}，{distance}）{bike_hint(walk_mins)}")
                        else:
                            route_lines.append(f"🚶 從出發地步行（約{duration}，{distance}）{bike_hint(walk_mins)}")
                elif is_last:
                    dest_label = orig_dest or end_addr
                    # 如果下車站名稱已包含目的地關鍵字，跳過這段多餘步行
                    last_transit = next((s for s in reversed(steps[:i]) if s.get("travel_mode") == "TRANSIT"), None)
                    last_arr = last_transit["transit_details"]["arrival_stop"]["name"] if last_transit else ""
                    dest_core = _re.sub(r"站$", "", dest_label)
                    dest_is_station = dest_label.endswith("站")
                    if (dest_core and dest_core in last_arr) or (dest_is_station and walk_mins <= 5):
                        pass  # 終點就是捷運站，省略出站短距離步行
                    else:
                        route_lines.append(f"🚶 從下車站步行到「{dest_label}」（約{duration}，{distance}）")
                else:
                    next_step = steps[i + 1] if i + 1 < len(steps) else None
                    if next_step and next_step.get("travel_mode") == "TRANSIT":
                        dep = next_step["transit_details"]["departure_stop"]["name"]
                        route_lines.append(f"🚶 換乘：步行到「{dep}」（約{duration}，{distance}）")
                    else:
                        route_lines.append(f"🚶 步行（約{duration}，{distance}）")

                route_lines.append("")

        return route_lines

    # 取第一條路線的出發/目的地顯示（各路線相同）
    first_leg = all_routes[0]["legs"][0]
    start_addr = clean_addr(first_leg["start_address"])
    end_addr = clean_addr(first_leg["end_address"])

    if query_type == "departure_time" and arrival_time_str:
        # 用實際行程時間最短的路線計算出發時間（不用排序後的第一條，避免懲罰分影響）
        best_route = min(all_routes, key=lambda r: sum(s["duration"]["value"] for s in r["legs"][0]["steps"]))
        best_leg = best_route["legs"][0]
        travel_secs = sum(s["duration"]["value"] for s in best_leg["steps"])
        travel_mins = (travel_secs + 59) // 60
        from datetime import datetime, date, timedelta
        h, m = map(int, arrival_time_str.split(":"))
        arrival_dt = datetime.combine(date.today(), datetime.min.time().replace(hour=h, minute=m))
        depart_dt = arrival_dt - timedelta(minutes=travel_mins)
        depart_str = depart_dt.strftime("%H:%M")
        lines = [
            f"⏰ 要在 {arrival_time_str} 到達「{orig_destination or destination}」",
            f"路程約 {travel_mins} 分鐘，建議 {depart_str} 出發。\n",
            f"📌 出發地：{start_addr}",
            f"📌 目的地：{end_addr}",
            "",
        ]
    else:
        lines = ["🗺️ 路線規劃\n"]
        if arrival_time_str:
            lines.append(f"🕗 指定到達時間：{arrival_time_str}\n")

        if is_vague(first_leg["start_address"]) or is_vague(first_leg["end_address"]):
            lines.append("⚠️ 您輸入的地點較模糊，系統以下列地點計算，若有偏差請說更詳細的地址（例如附近的捷運站或路名）：\n")

        lines.append(f"📌 出發地：{start_addr}")
        lines.append(f"📌 目的地：{end_addr}")
        lines.append("")

    for idx, route in enumerate(all_routes):
        leg = route["legs"][0]
        lines.extend(format_route(leg, idx + 1, len(all_routes), orig_destination, show_bike=is_home_origin))

    origin_enc = requests.utils.quote(origin + " 台灣")
    dest_enc = requests.utils.quote(destination + " 台灣")
    maps_url = f"https://www.google.com/maps/dir/?api=1&origin={origin_enc}&destination={dest_enc}&travelmode=transit"
    lines.append(f"📌 更多路線選項請點下方連結查看")
    lines.append(f"📱 Google Maps 導航：\n{maps_url}")

    return "\n".join(lines)


def parse_transit_query(user_message: str) -> dict:
    """
    從使用者訊息解析出發地、目的地、到達時間、查詢類型
    回傳 {
      "origin": ..., "destination": ..., "complete": bool,
      "arrival_time": "HH:MM" or None,
      "query_type": "route" | "departure_time"
    }
    """
    import re

    result = {
        "origin": None,
        "destination": None,
        "complete": False,
        "arrival_time": None,
        "query_type": "route",
    }

    # 判斷是否詢問出發時間
    if re.search(r"幾點出發|幾點搭|幾點要出門|才來得及|幾點走", user_message):
        result["query_type"] = "departure_time"

    # 抓到達時間，支援「8:00」「8點」「早上8點」「上午8:30」「8：00」
    time_match = re.search(
        r"(?:早上|上午|下午|晚上)?\s*(\d{1,2})[：:點](\d{0,2})\s*(?:到達|到|抵達)",
        user_message
    )
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2)) if time_match.group(2) else 0
        # 下午加12小時
        if "下午" in user_message or "晚上" in user_message:
            if hour < 12:
                hour += 12
        result["arrival_time"] = f"{hour:02d}:{minute:02d}"
        # 把時間部分從訊息中移除，避免干擾地名解析
        user_message = re.sub(
            r"(?:早上|上午|下午|晚上)?\s*\d{1,2}[：:點]\d{0,2}\s*(?:到達|到|抵達)?",
            "到達",
            user_message,
            count=1
        )

    # 抓地點：支援「從A到B」「A到B怎麼去」「A去B」「A→B」「到達B」「B怎麼去」
    # 注意：兩地點 pattern 必須在單地點 pattern 前面，避免「A到B怎麼去」被誤判為只有目的地
    patterns = [
        (r"從\s*(.+?)\s*(?:到|去|前往)\s*(.+?)(?:要怎麼去|怎麼去|要如何去|如何去|要怎麼搭|怎麼搭|路線|幾點|[？?]|$)", "both"),
        (r"(.+?)\s*(?:到|去|前往)\s*(.+?)(?:要怎麼去|怎麼去|要如何去|如何去|要怎麼搭|怎麼搭|路線|幾點|[？?]|$)", "both"),
        (r"(.+?)\s*[→➜]\s*(.+)", "both"),
        (r"到達\s*(.+?)(?:要怎麼去|怎麼去|怎麼搭|幾點出發|才來得及|我|怎麼坐|早上|上午|下午|晚上|[？?\s]|$)", "dest"),  # 「到達X」只有目的地
        (r"^(.+?)(?:要怎麼去|怎麼去|要如何去|如何去|要怎麼搭|怎麼搭|怎麼坐|搭什麼車)[？?]?$", "dest"),     # 「X怎麼去」只有目的地
    ]

    for pattern, ptype in patterns:
        m = re.search(pattern, user_message)
        if m:
            if ptype == "dest":
                result["destination"] = m.group(1).strip()
            else:
                result["origin"] = m.group(1).strip()
                result["destination"] = m.group(2).strip()
            break

    # 清理出發地尾端的雜訊
    if result["origin"]:
        result["origin"] = re.sub(r"\s*(要怎麼|怎麼|要如何|如何|要怎麼搭|怎麼搭|要怎麼坐|怎麼坐).*$", "", result["origin"])
        result["origin"] = re.sub(r"[，。？?！!\s]+$", "", result["origin"])

    # 清理目的地尾端的雜訊
    if result["destination"]:
        result["destination"] = re.sub(r"[，。？?！!\s]+$", "", result["destination"])
        result["destination"] = re.sub(r"\s+(我|怎麼|早上|上午|下午|晚上|幾點|要|才|搭).*$", "", result["destination"])
        result["destination"] = re.sub(r"\s*(大約|約|需要|要)?\s*幾分鐘.*$", "", result["destination"])
        result["destination"] = re.sub(r"[，。？?！!\s]+$", "", result["destination"])

    result["complete"] = bool(result["destination"])
    return result
