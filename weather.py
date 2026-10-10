#!/usr/bin/python3
# vim: set expandtab shiftwidth=4 tabstop=4 softtabstop=4:
##############################################################################
# Parses current observations at climo station from nws api and returns
# values delimited by '|' (--script) to be used as input for other scripts
# Andy Malato
# June 2023
# Updated May 31 2024 (Almost a year from initial code)
#   Removed dependacy on pytz and also added icon from seperated weather for script
#   output.  Additional cleanup of unneeded comments in code
# Updated May 11 2025
#   Fixed bug with sunrise/sunset times that preventing "Night" Icons from
#   Displaying during the day.
# Updated June 12 2025
#   Added --forecast option to display forecast for given stationID
# Updated October 10 2026
#   Weather icons now come from the NWS observation icon code, which already
#   knows day vs night.  Times are shown in the station's timezone.  Station
#   info and observations are cached, HTTP requests have a timeout, and
#   added --json and --nocache options.  General cleanup.
#   Added --discussion option to display the latest Area Forecast Discussion
#   from the station's NWS forecast office
##############################################################################
import argparse
import json
import os
import shutil
import sys
import textwrap
import time
import zoneinfo
from datetime import datetime, timezone
from urllib.parse import urlparse

import ephem
import requests

ME = os.path.basename(sys.argv[0])
API_URL = 'https://api.weather.gov'
USER_AGENT = 'weather.py, weather_py@malato.org'
REQUEST_TIMEOUT = 10

# Station info never changes so it is cached forever.  NWS only updates
# observations about once an hour, so there is no point hitting the API
# every time polybar polls us.
CACHE_DIR = os.path.join(os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache')), 'weather_py')
OBSERVATION_CACHE_SECONDS = 300

session = requests.Session()
session.headers.update({'Accept': 'application/geo+json', 'User-Agent': USER_AGENT})

#############################################################################
# Emoji for each NWS icon condition code (see https://api.weather.gov/icons)
# Codes given as (day, night) pairs change based on time of day.
#############################################################################
WX_EMOJI = {
    'skc': ('🌞', '🌛'),
    'few': ('🌤️', '☾'),
    'sct': ('🌤️', '☾'),
    'bkn': ('🌥️', '☁️'),
    'ovc': '☁️',
    'wind_skc': '🌬️',
    'wind_few': '🌬️',
    'wind_sct': '🌬️',
    'wind_bkn': '🌬️',
    'wind_ovc': '🌬️',
    'snow': '❄️',
    'rain_snow': '❄',
    'rain_sleet': '❄',
    'snow_sleet': '❄',
    'fzra': '❄',
    'rain_fzra': '❄',
    'snow_fzra': '❄',
    'sleet': '🧊',
    'rain': '🌧️',
    'rain_showers': ('🌦️', '🌧️'),
    'rain_showers_hi': ('🌦️', '🌧️'),
    'tsra': '⛈️',
    'tsra_sct': '🌩️',
    'tsra_hi': '🌩️',
    'tornado': '🌪️',
    'hurricane': '🌀',
    'tropical_storm': '🌊',
    'dust': '🌫️',
    'smoke': '🌁',
    'haze': '🌫️',
    'hot': ('☀️', '🌛'),
    'cold': '🧊',
    'blizzard': '❄️',
    'fog': '🌫️',
}
UNKNOWN_EMOJI = '❓'

#############################################################################
# Convert NWS unit codes to the units we work in: degC, km/h and Pa
#############################################################################
UNIT_CONVERSIONS = {
    'wmoUnit:degC': lambda v: v,
    'wmoUnit:degF': lambda v: (v - 32) / 1.8,
    'wmoUnit:K': lambda v: v - 273.15,
    'wmoUnit:km_h-1': lambda v: v,
    'wmoUnit:m_s-1': lambda v: v * 3.6,
    'wmoUnit:Pa': lambda v: v,
    'wmoUnit:hPa': lambda v: v * 100,
    'wmoUnit:percent': lambda v: v,
    'wmoUnit:degree_(angle)': lambda v: v,
}

#############################################################################
# Print error message and exit
#############################################################################
def die(message):
    print('{}: {}'.format(ME, message), file=sys.stderr)
    sys.exit(1)

#############################################################################
# GET a url from the NWS api and return the decoded json
#############################################################################
def urlreq(url):
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            die('not found: {} -- possible invalid station ID?'.format(url))
        die('request failed: {}'.format(e))
    except (requests.RequestException, ValueError) as e:
        die('request failed: {}'.format(e))

#############################################################################
# Read json from cache, returns None if missing or older than max_age seconds
#############################################################################
def cache_read(name, max_age=None):
    path = os.path.join(CACHE_DIR, name)
    try:
        if max_age is not None and time.time() - os.path.getmtime(path) > max_age:
            return None
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None

#############################################################################
# Write json to cache.  Write to a temp file and rename so that two copies
# of this script running at once never see a half written file.
#############################################################################
def cache_write(name, data):
    path = os.path.join(CACHE_DIR, name)
    tmp_path = '{}.{}.tmp'.format(path, os.getpid())
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(tmp_path, 'w') as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except OSError:
        pass

#############################################################################
# Get WX Station Information -- returns dict with id, name, lon, lat, timezone
#############################################################################
def get_station(station_id, use_cache=True):
    cache_name = 'station_{}.json'.format(station_id)
    station = cache_read(cache_name) if use_cache else None
    if station is None:
        data = urlreq('{}/stations/{}'.format(API_URL, station_id))
        station = {
            'id': data['properties']['stationIdentifier'],
            'name': data['properties']['name'],
            'lon': data['geometry']['coordinates'][0],
            'lat': data['geometry']['coordinates'][1],
            'timezone': data['properties'].get('timeZone'),
        }
        cache_write(cache_name, station)
    return station

#############################################################################
# Get latest observation properties for station
#############################################################################
def get_observation(station_id, use_cache=True):
    cache_name = 'observation_{}.json'.format(station_id)
    observation = cache_read(cache_name, OBSERVATION_CACHE_SECONDS) if use_cache else None
    if observation is None:
        data = urlreq('{}/stations/{}/observations/latest'.format(API_URL, station_id))
        observation = data['properties']
        cache_write(cache_name, observation)
    return observation

#############################################################################
# Return the station's timezone, falling back to our local timezone
#############################################################################
def station_tz(station):
    try:
        return zoneinfo.ZoneInfo(station['timezone'])
    except (TypeError, ValueError, zoneinfo.ZoneInfoNotFoundError):
        return datetime.now().astimezone().tzinfo

#############################################################################
# Get today's sunrise and sunset in the station's timezone.  Either may be
# None when the sun doesn't rise or set today (polar day/night).
#############################################################################
def get_sunrise_sunset(latitude, longitude, tz):
    # Start the search at the station's local midnight (expressed in UTC) so
    # that next_rising/next_setting return today's sunrise and sunset
    local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    observer = ephem.Observer()
    observer.lat = str(latitude)
    observer.long = str(longitude)
    observer.date = local_midnight.astimezone(timezone.utc).replace(tzinfo=None)

    def event(find_next):
        try:
            return find_next(ephem.Sun()).datetime().replace(tzinfo=timezone.utc).astimezone(tz)
        except ephem.CircumpolarError:
            return None

    return(event(observer.next_rising), event(observer.next_setting))

#############################################################################
# Look up emoji from the NWS observation icon url, e.g.
# https://api.weather.gov/icons/land/night/rain_showers,40?size=medium
#############################################################################
def get_wx_emoji(icon_url):
    if not icon_url:
        return UNKNOWN_EMOJI
    parts = urlparse(icon_url).path.split('/')
    for i, part in enumerate(parts[:-1]):
        if part in ('day', 'night'):
            code = parts[i + 1].split(',')[0]
            emoji = WX_EMOJI.get(code, UNKNOWN_EMOJI)
            if isinstance(emoji, tuple):
                emoji = emoji[1] if part == 'night' else emoji[0]
            return emoji
    return UNKNOWN_EMOJI

#############################################################################
# Return value of an observation property converted to our working units,
# or None if it is missing
#############################################################################
def get_value(observation, prop):
    quantity = observation.get(prop) or {}
    value = quantity.get('value')
    if value is None:
        return None
    convert = UNIT_CONVERSIONS.get(quantity.get('unitCode'))
    if convert is None:
        print('{}: unexpected unit {} for {}'.format(ME, quantity.get('unitCode'), prop), file=sys.stderr)
        return None
    return convert(value)

#############################################################################
# Functions below each return (value, unit) for display
#############################################################################
def temperature(observation, prop, metric):
    c = get_value(observation, prop)
    if c is None:
        return(None, '')
    if metric:
        return(round(c), '°C')
    return(round(c * 1.8 + 32), '°F')

def speed(observation, prop, metric):
    kmh = get_value(observation, prop)
    if kmh is None:
        return(None, '')
    if metric:
        return(round(kmh), 'km/h')
    return(round(kmh * 0.62137), 'mph')

def humidity(observation, metric):
    value = get_value(observation, 'relativeHumidity')
    if value is None:
        return(None, '')
    return(round(value), '%')

def pressure(observation, metric):
    pa = get_value(observation, 'barometricPressure')
    if pa is None:
        return(None, '')
    return(round(pa * 0.00029530, 2), 'in')

def wind_direction(observation, metric):
    direction = get_value(observation, 'windDirection')
    # NWS reports a direction of 0 when calm, which is not really North
    if direction is None or speed(observation, 'windSpeed', metric)[0] == 0:
        return(None, '')
    sector = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW", "N"]
    return(sector[round(direction % 360 / 22.5)], '')

# Data fields in display order: (option name, title, function)
# The order matters -- --script output is parsed by position
FIELDS = [
    ('temp', 'Temperature', lambda o, m: temperature(o, 'temperature', m)),
    ('humidity', 'Humidity', humidity),
    ('dewpoint', 'Dewpoint', lambda o, m: temperature(o, 'dewpoint', m)),
    ('pressure', 'Pressure', pressure),
    ('windchill', 'Windchill', lambda o, m: temperature(o, 'windChill', m)),
    ('heatindex', 'Heatindex', lambda o, m: temperature(o, 'heatIndex', m)),
    ('windspeed', 'Wind', lambda o, m: speed(o, 'windSpeed', m)),
    ('windgust', 'Wind Gust', lambda o, m: speed(o, 'windGust', m)),
    ('winddirection', 'Wind Direction', wind_direction),
]
FIELD_NAMES = ['weather'] + [name for name, title, func in FIELDS]

#############################################################################
# Return list of field names to display based on command line options
#############################################################################
def selected_fields(args):
    if args.allvalues:
        return FIELD_NAMES
    selected = [name for name in FIELD_NAMES if getattr(args, name)]
    if args.icononly and 'weather' not in selected:
        selected.insert(0, 'weather')
    # Nothing asked for -- show everything
    return selected or FIELD_NAMES

#############################################################################
# Print station header
#############################################################################
def print_header(station, observation):
    tz = station_tz(station)
    timestamp = datetime.fromisoformat(observation['timestamp']).astimezone(tz)
    sunrise, sunset = get_sunrise_sunset(station['lat'], station['lon'], tz)
    sun_fmt = "%m/%d/%Y %H:%M:%S"
    sunrise = sunrise.strftime(sun_fmt) if sunrise else 'N/A'
    sunset = sunset.strftime(sun_fmt) if sunset else 'N/A'

    print("=" * 60)
    print("Station: {:<10}{}".format(station['id'], station['name']))
    print("Timestamp: {}".format(timestamp.strftime("%m/%d/%Y %H:%M:%S %Z")))
    print("Coordinates: LON[{}], LAT[{}]".format(station['lon'], station['lat']))
    print("Sunrise: {}, Sunset: {}".format(sunrise, sunset))
    print("=" * 60)

#############################################################################
# Return list of display strings for selected fields
#############################################################################
def weather_display_list(observation, fields, args):
    display_list = []
    show_titles = not (args.valuesonly or args.script or args.icononly)

    def add(title, text):
        display_list.append('Current {}: {}'.format(title, text) if show_titles else text)

    if 'weather' in fields:
        description = observation.get('textDescription') or 'N/A'
        if args.icononly:
            add('Weather', get_wx_emoji(observation.get('icon')))
        elif args.icon and args.script:
            # icon is its own field so scripts can use it separately
            add('Weather', description)
            add('Weather', get_wx_emoji(observation.get('icon')))
        elif args.icon:
            add('Weather', description + get_wx_emoji(observation.get('icon')))
        else:
            add('Weather', description)

    for name, title, func in FIELDS:
        if name in fields:
            value, unit = func(observation, args.metric)
            if value is None:
                text = 'N/A'
            elif name == 'windspeed' and value == 0:
                text = 'Calm'
            else:
                text = '{}{}'.format(value, unit)
            add(title, text)

    return(display_list)

#############################################################################
# Return dict of selected fields for --json output
#############################################################################
def weather_json(station, observation, fields, args):
    result = {
        'station': station['id'],
        'name': station['name'],
        'timestamp': observation['timestamp'],
    }
    if 'weather' in fields:
        result['weather'] = observation.get('textDescription') or None
        result['icon'] = get_wx_emoji(observation.get('icon'))
    for name, title, func in FIELDS:
        if name in fields:
            value, unit = func(observation, args.metric)
            result[name] = {'value': value, 'unit': unit}
    return(result)

#############################################################################
# Get forecast point info (forecast office, forecast urls) for station
#############################################################################
def get_points(station):
    points_url = '{}/points/{:.4f},{:.4f}'.format(API_URL, station['lat'], station['lon'])
    return urlreq(points_url)['properties']

#############################################################################
# Print Weather Forecast to screen
#############################################################################
def display_weather_forecast(station):
    forecast = urlreq(get_points(station)['forecast'])

    cwidth = shutil.get_terminal_size().columns
    print('Forecast Information for {}({})'.format(station['name'], station['id']))
    print('')
    for period in forecast['properties']['periods']:
        name = period['name']
        print(name.center(cwidth))
        print(('-' * len(name)).center(cwidth))
        print(textwrap.fill(period['detailedForecast'], cwidth))
        print('*' * cwidth)

#############################################################################
# Print latest Area Forecast Discussion from the station's forecast office
#############################################################################
def display_forecast_discussion(station):
    office = get_points(station)['cwa']
    products = urlreq('{}/products/types/AFD/locations/{}'.format(API_URL, office))['@graph']
    if not products:
        die('no forecast discussion available from office {}'.format(office))
    # Products are listed newest first
    discussion = urlreq(products[0]['@id'])

    print('Forecast Discussion for {}({}) from NWS office {}'.format(station['name'], station['id'], office))
    print('')
    # Text is already formatted by NWS so print it as is
    print(discussion['productText'].strip())

#############################################################################
# Parse command line arguments
#############################################################################
def parse_args():
    parser = argparse.ArgumentParser(usage='%(prog)s [options] StationID', description='Utility for printing weather conditions')
    parser.add_argument("stationID", metavar="StationID", type=str, help="The name of the wx station ID to get condition from")
    parser.add_argument("-t", "--temp", help="Print temperature for given stationID", action="store_true")
    parser.add_argument("-H", "--humidity", help="Print humidity for given stationID", action="store_true")
    parser.add_argument("-d", "--dewpoint", help="Print dewpoint for given stationID", action="store_true")
    parser.add_argument("-w", "--weather", help="Print weather for given stationID", action="store_true")
    parser.add_argument("-p", "--pressure", help="Print barometric pressure for given stationID", action="store_true")
    parser.add_argument("-W", "--windchill", help="Print windchill for given stationID", action="store_true")
    parser.add_argument("-i", "--heatindex", help="Print heatindex for given stationID", action="store_true")
    parser.add_argument("-c", "--winddirection", help="Print wind direction for given stationID", action="store_true")
    parser.add_argument("-g", "--windgust", help="Print wind gust for given stationID", action="store_true")
    parser.add_argument("-s", "--windspeed", help="Print wind speed for given stationID", action="store_true")
    parser.add_argument("--forecast", help="Print Forecast for given StationID", action="store_true")
    parser.add_argument("--discussion", help="Print latest scientific forecast discussion for given StationID", action="store_true")
    parser.add_argument("--script", help="Print all data formatted on a single line, delimited by '|', for parsing by external script", action="store_true")
    parser.add_argument("--json", help="Print data as JSON", action="store_true")
    parser.add_argument("--metric", help="Print all data in metric units", action="store_true")
    parser.add_argument("--valuesonly", help="Display only data values, no titles", action="store_true")
    parser.add_argument("--noheaders", help="Don't display header with station info", action="store_true")
    parser.add_argument("--allvalues", help="Display all data values (default when no values are selected)", action="store_true")
    parser.add_argument("--icon", help="Display weather icon for weather value", action="store_true")
    parser.add_argument("--icononly", help="Only display icon for weather value", action="store_true")
    parser.add_argument("--nocache", help="Ignore cached station info and observations", action="store_true")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    return parser.parse_args()

#############################################################################
# main()
#############################################################################
def main():
    args = parse_args()
    station = get_station(args.stationID.upper(), use_cache=not args.nocache)

    if args.forecast:
        display_weather_forecast(station)
        return

    if args.discussion:
        display_forecast_discussion(station)
        return

    observation = get_observation(station['id'], use_cache=not args.nocache)
    fields = selected_fields(args)

    if args.json:
        print(json.dumps(weather_json(station, observation, fields, args), ensure_ascii=False, indent=2))
        return

    if not (args.noheaders or args.script or args.icononly):
        print_header(station, observation)

    display_list = weather_display_list(observation, fields, args)
    if args.script:
        print('|'.join(display_list))
    else:
        print('\n'.join(display_list))

if __name__ == "__main__":
    main()
