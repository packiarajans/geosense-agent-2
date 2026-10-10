# geocode.py
# Look up a city's bounding box by name using OpenStreetMap Nominatim
# (free, no API key). This replaces manually copying a box from bboxfinder.
#
# Nominatim usage policy: send a descriptive User-Agent and don't hammer it.
# We fetch once per city and cache the result in cities.json, so a normal run
# hits the network at most once for a new city.

import json
import urllib.parse
import urllib.request

NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
USER_AGENT = 'GeoSense-Agent/1.0 (Phase-1 site scorer; contact: p15rajan@gmail.com)'


def geocode_bbox(city, country='India', timeout=20):
    """Return (min_lon, min_lat, max_lon, max_lat) for a city, via Nominatim.

    Raises ValueError if the place can't be found.
    """
    query = f'{city}, {country}' if country else city
    params = urllib.parse.urlencode({'q': query, 'format': 'json', 'limit': 1})
    url = f'{NOMINATIM_URL}?{params}'
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if not data:
        raise ValueError(f'Nominatim found no result for "{query}".')

    # Nominatim boundingbox = [south_lat, north_lat, west_lon, east_lon] (strings)
    south, north, west, east = (float(v) for v in data[0]['boundingbox'])
    display = data[0].get('display_name', query)
    print(f'  Geocoded "{query}" -> {display}')
    return (west, south, east, north)   # (min_lon, min_lat, max_lon, max_lat)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Look up a city bounding box.')
    ap.add_argument('city')
    ap.add_argument('--country', default='India')
    args = ap.parse_args()
    print(geocode_bbox(args.city, args.country))
