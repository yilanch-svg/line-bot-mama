import pathlib
from dotenv import load_dotenv
load_dotenv(pathlib.Path('C:/line-bot-mama/.env'))
from modules.transit import search_places, LOCATION_ALIAS, check_location_precision

def needs_clarify(place_name):
    if place_name in LOCATION_ALIAS:
        return False, []
    places = search_places(place_name)
    if len(places) >= 2:
        return True, places
    geo = check_location_precision(place_name)
    if not geo['precise']:
        return True, places
    return False, []

for name in ['大同高中', '天母中醫', '台北車站', '家裡', '市立大同高中']:
    ambig, places = needs_clarify(name)
    names = [p['name'] for p in places]
    print(f'{name}: ambig={ambig} -> {names}')
