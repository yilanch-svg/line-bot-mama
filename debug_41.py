import pathlib, re
from dotenv import load_dotenv
load_dotenv(pathlib.Path('C:/line-bot-mama/.env'))
from modules.bus import tdx_get, get_route_variants

# 直接測 get_route_variants
for route in ['202', '212', '41']:
    v = get_route_variants(route, '台北')
    print(f'{route} -> {v}')
