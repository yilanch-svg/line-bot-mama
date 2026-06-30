import pathlib
from dotenv import load_dotenv
load_dotenv(pathlib.Path('C:/line-bot-mama/.env'))
from modules.bus import tdx_get

stop_keyword = '松山車'
data = tdx_get('/v2/Bus/EstimatedTimeOfArrival/City/Taipei/32', {
    '$filter': f"contains(StopName/Zh_tw,'{stop_keyword}')",
    '$format': 'JSON'
})
for item in data:
    print(item.get('RouteUID'), 'dir=', item.get('Direction'),
          'stop=', item.get('StopName',{}).get('Zh_tw'),
          'sec=', item.get('EstimateTime'),
          'status=', item.get('StopStatus'))
