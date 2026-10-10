# weather.py

A command line utility that prints current weather conditions, forecasts and
forecast discussions for a National Weather Service (NWS) observation station,
using the free [api.weather.gov](https://www.weather.gov/documentation/services-web-api)
API. Its `--script` output is designed to feed status bars such as polybar.

```
$ weather.py KNYC --icon
============================================================
Station: KNYC      New York City, Central Park
Timestamp: 10/10/2026 07:51:00 EDT
Coordinates: LON[-73.96667], LAT[40.78333]
Sunrise: 10/10/2026 07:01:33, Sunset: 10/10/2026 18:23:26
============================================================
Current Weather: Clear🌞
Current Temperature: 54°F
Current Humidity: 64%
Current Dewpoint: 42°F
Current Pressure: 30.42in
Current Windchill: N/A
Current Heatindex: N/A
Current Wind: Calm
Current Wind Gust: N/A
Current Wind Direction: N/A
```

## Requirements

- Python 3.9 or newer
- [requests](https://pypi.org/project/requests/)
- [ephem](https://pypi.org/project/ephem/) (sunrise/sunset times in the header)

```
pip install requests ephem
```

On Debian/Ubuntu: `apt install python3-requests python3-ephem`

## Installation

Copy `weather.py` somewhere on your `PATH` and make it executable:

```
cp weather.py ~/bin/
chmod +x ~/bin/weather.py
```

## Finding a station ID

Most stations are identified by a 4 letter ICAO ID, usually an airport, for
example `KNYC` (Central Park) or `KSEA` (Seattle-Tacoma Airport). To list the
stations in a state (the list also includes smaller stations with other ID
formats, such as `0512W`):

```
curl -s https://api.weather.gov/stations?state=NY | grep stationIdentifier
```

Station IDs are not case sensitive.

## Usage

```
weather.py [options] StationID
```

### Data values

Select which values to print. If none are selected, all of them are printed.

| Option | Value |
|---|---|
| `-w`, `--weather` | Current weather description (e.g. "Clear", "Light Rain") |
| `-t`, `--temp` | Temperature |
| `-H`, `--humidity` | Relative humidity |
| `-d`, `--dewpoint` | Dewpoint |
| `-p`, `--pressure` | Barometric pressure (inches of mercury) |
| `-W`, `--windchill` | Windchill |
| `-i`, `--heatindex` | Heat index |
| `-s`, `--windspeed` | Wind speed ("Calm" when there is no wind) |
| `-g`, `--windgust` | Wind gust |
| `-c`, `--winddirection` | Wind direction as a compass point ("N/A" when calm) |
| `--allvalues` | All of the above |

Values the station does not report are shown as `N/A`.

### Output format

| Option | Effect |
|---|---|
| `--metric` | Use °C and km/h instead of °F and mph |
| `--icon` | Add a weather emoji to the weather value |
| `--icononly` | Show only the weather emoji, no titles or header |
| `--valuesonly` | Leave out the "Current ...:" titles |
| `--noheaders` | Leave out the station header block |
| `--script` | Print all values on one line separated by `\|`, with no titles or header |
| `--json` | Print the selected values as JSON |

### Forecasts

| Option | Effect |
|---|---|
| `--forecast` | Print the 7 day forecast for the station's location |
| `--discussion` | Print the latest Area Forecast Discussion, the forecasters' scientific write-up, from the station's NWS office |

### Other

| Option | Effect |
|---|---|
| `--nocache` | Fetch fresh data instead of using the cache (see [Caching](#caching)) |
| `-h`, `--help` | Show help |

## Examples

Temperature and wind for a single station:

```
$ weather.py KNYC --noheaders -t -s -c
Current Temperature: 54°F
Current Wind: Calm
Current Wind Direction: N/A
```

Script output, for status bars and other scripts:

```
$ weather.py KNYC --script --allvalues --icon
Clear|🌞|54°F|64%|42°F|30.42in|N/A|N/A|Calm|N/A|N/A
```

Values always appear in this order, regardless of the order of the options:

```
weather | icon | temperature | humidity | dewpoint | pressure | windchill | heatindex | wind | wind gust | wind direction
```

Only the selected values are included. With `--script`, the icon is a separate
field after the weather description so scripts can use them independently.

Just the icon:

```
$ weather.py KNYC --icononly
🌞
```

JSON:

```
$ weather.py KNYC --json -w -t
{
  "station": "KNYC",
  "name": "New York City, Central Park",
  "timestamp": "2026-10-10T11:51:00+00:00",
  "weather": "Clear",
  "icon": "🌞",
  "temp": {
    "value": 54,
    "unit": "°F"
  }
}
```

Forecast discussion (long, so a pager helps):

```
$ weather.py KNYC --discussion | less
```

## Polybar

Example `custom/script` module:

```ini
[module/weather]
type = custom/script
exec = ~/bin/weather.py KNYC --script -w -t --icon
interval = 600
```

Errors are printed to stderr with a non-zero exit code, so a failed request
leaves the module empty instead of showing an error message in the bar.

## Weather icons

The icon comes from the condition code in the icon NWS attaches to each
observation (see [api.weather.gov/icons](https://api.weather.gov/icons)).
NWS decides whether it is day or night at the station, so clear and partly
cloudy skies show a sun during the day and a moon at night. Conditions without
a matching emoji show `❓`.

## Caching

To avoid hitting the API every time a status bar polls, results are cached in
`~/.cache/weather_py/` (or `$XDG_CACHE_HOME/weather_py/`):

- Station info (name, location, timezone) is cached permanently.
- Observations are cached for 5 minutes. NWS stations usually report about
  once an hour.

Use `--nocache` to force a fresh fetch, or delete the cache directory to reset
it. Forecasts and discussions are never cached.

## Times

The header timestamp and sunrise/sunset times are shown in the station's own
timezone. In the far north or south, where the sun may not rise or set on a
given day, sunrise and sunset are shown as `N/A`.

## Author

Andy Malato
