import pathlib
from dotenv import load_dotenv
load_dotenv(pathlib.Path('C:/line-bot-mama/.env'))
from modules.bus import parse_bus_query

tests = ['藍5 吳興街', '藍5 吳興街站', '紅2 台北車站', '22 松平路口', '226 行天宮']
for t in tests:
    r = parse_bus_query(t)
    print(f"{t} -> route={r['route']} stop={r['stop']}")
