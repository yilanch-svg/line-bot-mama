import pathlib
from dotenv import load_dotenv
load_dotenv(pathlib.Path('C:/line-bot-mama/.env'))
from modules.bus import tdx_get

route = "226"
city = "Taipei"
stop_name = "捷運行天宮站(松江路)"

import re
base = re.sub(r'\([^)]*\)', '', stop_name).strip()
print(f"base keyword: {base}")

data = tdx_get(
    f"/v2/Bus/EstimatedTimeOfArrival/City/{city}/{route}",
    {"$filter": f"contains(StopName/Zh_tw,'{base}')", "$format": "JSON"}
)

print(f"\n=== ETA 原始資料（共{len(data)}筆）===")
for item in data:
    sn = item.get("StopName", {}).get("Zh_tw", "")
    uid = item.get("RouteUID")
    d = item.get("Direction")
    sec = item.get("EstimateTime")
    status = item.get("StopStatus")
    print(f"  站名={sn}  UID={uid}  方向={d}  秒數={sec}  狀態={status}")

print(f"\n=== 精確比對 '{stop_name}' 的筆數 ===")
exact = [i for i in data if i.get("StopName", {}).get("Zh_tw", "") == stop_name]
print(f"  共 {len(exact)} 筆")
