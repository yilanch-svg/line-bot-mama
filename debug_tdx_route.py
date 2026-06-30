import pathlib, math, time, requests
from dotenv import load_dotenv
load_dotenv(pathlib.Path('C:/line-bot-mama/.env'))
from modules.bus import tdx_get
import os

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

def geocode(address):
    r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address + " 台灣", "language": "zh-TW", "key": API_KEY})
    loc = r.json()["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]

def dist_m(lat1, lng1, lat2, lng2):
    return math.hypot((lat1-lat2)*111000, (lng1-lng2)*111000*math.cos(math.radians(lat1)))

origin = "台北市信義區吳興街518巷"
destination = "台北市信義區忠孝東路五段482號"

print("=== Geocoding ===")
olat, olng = geocode(origin)
dlat, dlng = geocode(destination)
print(f"起點: {olat:.6f}, {olng:.6f}")
print(f"終點: {dlat:.6f}, {dlng:.6f}")

# 直接查 32 號的所有站牌座標
print("\n=== 32號站牌清單 ===")
data = tdx_get("/v2/Bus/StopOfRoute/City/Taipei/32", {"$format": "JSON"})
for item in data:
    uid = item.get("RouteUID")
    d = item.get("Direction")
    for stop in item.get("Stops", []):
        name = stop["StopName"]["Zh_tw"]
        pos = stop.get("StopPosition", {})
        slat = pos.get("PositionLat")
        slng = pos.get("PositionLon")
        if slat is None: continue
        d_orig = dist_m(olat, olng, slat, slng)
        d_dest = dist_m(dlat, dlng, slat, slng)
        flag = ""
        if d_orig < 600: flag += f" ← 起點附近({int(d_orig)}m)"
        if d_dest < 600: flag += f" ← 終點附近({int(d_dest)}m)"
        if flag:
            print(f"  [{uid} 方向{d}] {name}{flag}")
