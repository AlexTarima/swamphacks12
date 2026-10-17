# Database

Sites are stored in SQLite. Starting the FastAPI app creates `sites.db` beside
`database.py` and adds the original demo site if it is absent. Existing sites
are preserved across restarts. No separate database server is required.

Optionally set `PLANIT_DATABASE_PATH` before starting the server to use another
database file. Its parent directory must exist. Use an absolute path for a
consistent location regardless of the working directory. Keep this file on
persistent storage when deploying the backend.

`POST /api/sites` accepts `name`, `latitude`, and `longitude` as JSON and returns
the saved site with its database-assigned `id` and status 201. Listing sites,
retrieving a site, and retrieving site weather all read from the database.

# Combined site risk API

The backend imports **60 distinct operating power plants** from the official
[EIA-860M August 2026 workbook](https://www.eia.gov/electricity/data/eia860m/xls/august_generator2026.xlsx).
The bundled `data/eia_plants.json` records the workbook URL, report month,
retrieval timestamp, SHA-256, selection method, and original plant IDs.
Selection rotates through states/territories, then ascending EIA plant ID.
Multiple generators at one plant become one site with combined technology names.
These are actual plant locations; the inventory does not establish that active
construction is occurring at them.

## Running and refreshing

Start the existing application with `.\.venv\Scripts\python.exe -m uvicorn main:app --reload`.
Startup creates/migrates SQLite tables and imports the bundled catalog without
network requests. Existing custom sites and site IDs are preserved. `GET /api/sites`
returns the original fields plus `eia_plant_id`, `utility`, `county`, `state`,
`plant_type`, `source`, `source_url`, and `source_period`. Custom sites have null
EIA metadata. The original demo is retained and is not counted toward the 60 plants.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/sites/{site_id}/risk` | One combined assessment; fetches feeds when the saved assessment has expired |
| `GET /api/risks` | Fast portfolio overview of all saved assessments, with `pending` and `stale` flags |
| `POST /api/risks/refresh?limit=60` | Fetch and save assessments for up to 100 EIA plants (default 60) |

The batch request waits for completion and can take several minutes on a cold
cache. Configure the frontend's request timeout accordingly. Assessments persist
across restarts. The overview never initiates hundreds of remote requests.

For a local batch with progress output:

```powershell
.\.venv\Scripts\python.exe -B scripts/refresh_risks.py
```

To update the bundled plant catalog from the latest workbook on EIA's page:

```powershell
.\.venv\Scripts\python.exe -B scripts/import_eia.py
```

Restart the backend after updating the catalog. EIA plants are matched by original
plant ID. Previously imported plants and custom sites are retained. The importer
uses Python's standard ZIP/XML libraries; no Excel installation is needed.

## Response contract

A site risk response contains:

- `site`: name, coordinates, internal ID, and EIA metadata.
- `generated_at`, `method_version`, `forecast_hours`, `cached`, and `stale`.
- `overall`: `level`, `highest_known_level`, `drivers`, `coverage_complete`,
  `coverage_gaps`, and an explanation of the aggregation method.
- `indicators`: seven named cards, each with `level`, `basis`, `evidence`,
  provider keys in `sources`, and `coverage` (`available`, `partial`, `unavailable`).
- `sources`: feed URLs, retrieval timestamps, availability, and failure reasons.
- `weather`: current conditions, 48-hour forecasts, and recent rainfall, or null
  when weather is unavailable.

The portfolio response contains `count` and a `sites` array. Each entry has the
site, assessment status, timestamp, stale flag, overall result, and `risk_url`.
An old result is retained for reference but always marked stale; `pending` means
that the site has never been assessed.

## Sources and interpretation

| Indicator | Inputs | Interpretation |
| --- | --- | --- |
| `weather` | Open-Meteo gusts and hourly precipitation; NWS storm/wind alerts | Planning screen over current conditions and the next 48 hours |
| `extreme_heat` | Open-Meteo apparent temperature; NWS heat alerts | Temperature screen, not worker-specific heat exposure |
| `lightning` | Open-Meteo thunderstorm codes 95, 96, 99; NWS thunderstorm alerts | Forecast proxy; measured lightning strikes are unavailable |
| `flooding` | NWS flood alerts, recent rainfall, maximum rolling 24-hour forecast precipitation | Rainfall/alert screen; no floodplain or inundation calculation |
| `wildfire` | NWS fire alerts and NASA EONET open wildfire points reported in the last 14 days | Proximity within 50 km; not fire perimeters or an exhaustive fire inventory |
| `soil_saturation` | Open-Meteo volumetric soil moisture at 3–9 cm and recent rainfall | Soil wetness proxy; actual saturation requires soil-specific porosity |
| `wildlife_sightings` | iNaturalist research-grade, non-captive Animalia observations in the last 30 days within 10 km | Ecological context; not species danger, absence, or protected-species clearance |

Wildlife evidence includes a total matching count and up to 20 recent sightings,
with species names, dates, source links, and an obscuration flag. Obscured public
locations can shift search membership. Exact sighting coordinates and observer
identities are not returned. Empty search results do not establish absence.

Wildfire distances use the latest reported point for each open event and the
haversine formula. Unsupported non-point geometries are counted and mark fire
coverage partial rather than being silently treated as no fire.

Provider documentation:
[EIA-860M](https://www.eia.gov/electricity/data/eia860m/),
[Open-Meteo](https://open-meteo.com/en/docs),
[NWS](https://www.weather.gov/documentation/services-web-api),
[NASA EONET](https://eonet.gsfc.nasa.gov/docs/v3),
[iNaturalist](https://api.inaturalist.org/v1/docs/).

## Transparent screening rules

These are application screening thresholds, not official hazard probabilities
or engineering determinations. Threshold lists below correspond to **watch / high /
severe**, inclusive at each boundary:

| Metric | Thresholds |
| --- | --- |
| Maximum gust | 30 / 45 / 65 mph |
| Maximum hourly precipitation | 0.30 / 0.75 / 1.00 inches |
| Maximum apparent temperature | 90 / 100 / 110 °F |
| Maximum rolling 24-hour forecast precipitation or last 24-hour rain | 1 / 2 / 4 inches |
| Maximum modeled soil water content at 3–9 cm | 0.30 / 0.40 / 0.50 m³/m³ (not calibrated to local soil) |

Thunderstorm codes trigger `high`. Wildfire points within 10 km trigger `high`;
within 25 km trigger `watch`. Relevant NWS alerts raise a card to at least the
alert's mapped severity: Extreme → severe, Severe → high, Moderate/Minor → watch.
Warnings are at least high. Expired, cancelled, and test alerts are excluded.

Overall severity is the **maximum** of environmental cards; wildlife is `info`
and never raises severity on sighting count alone. Missing data is never scored
as zero. When coverage is incomplete and all known signals are low, overall is
`unknown`. A known high/severe hazard remains visible even when other feeds fail.
`coverage_complete` describes the selected feeds/fields, not complete knowledge
of all hazards at the site.

## Caching and failures

- Completed risk snapshots: 5 minutes; incomplete snapshots: 1 minute.
- Open-Meteo: 15 minutes; NWS: 5 minutes; EONET: 30 minutes; wildlife: 24 hours.
- Provider errors: cached for 1 minute to avoid repeated failing calls.
- Timeout per upstream request: 15 seconds, with 5 seconds for connection setup.
- Batches use four concurrent sites, one request per provider at a time, and at
  least 1.1 seconds between wildlife requests. The global EONET feed is shared.

The caches live in SQLite; no API keys are needed for the configured public
endpoints. `PLANIT_USER_AGENT` can set the NWS application/contact identifier.
`PLANIT_DATABASE_PATH` can select a persistent database file.
Provider failures produce partial risk responses with explicit reasons instead
of concealing other available signals. A missing site still returns 404.

Run checks with `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`.

# Combined site risk API

The backend imports **60 distinct operating power plants** from the official
[EIA-860M August 2026 workbook](https://www.eia.gov/electricity/data/eia860m/xls/august_generator2026.xlsx).
The bundled `data/eia_plants.json` records the workbook URL, report month,
retrieval timestamp, SHA-256, selection method, and original plant IDs.
Selection rotates through states/territories, then ascending EIA plant ID.
Multiple generators at one plant become one site with combined technology names.
These are actual plant locations; the inventory does not establish that active
construction is occurring at them.

## Running and refreshing

Start the existing application with `.\.venv\Scripts\python.exe -m uvicorn main:app --reload`.
Startup creates/migrates SQLite tables and imports the bundled catalog without
network requests. Existing custom sites and site IDs are preserved. `GET /api/sites`
returns the original fields plus `eia_plant_id`, `utility`, `county`, `state`,
`plant_type`, `source`, `source_url`, and `source_period`. Custom sites have null
EIA metadata. The original demo is retained and is not counted toward the 60 plants.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/sites/{site_id}/risk` | One combined assessment; fetches feeds when the saved assessment has expired |
| `GET /api/risks` | Fast portfolio overview of all saved assessments, with `pending` and `stale` flags |
| `POST /api/risks/refresh?limit=60` | Fetch and save assessments for up to 100 EIA plants (default 60) |

The batch request waits for completion and can take several minutes on a cold
cache. Configure the frontend's request timeout accordingly. Assessments persist
across restarts. The overview never initiates hundreds of remote requests.

For a local batch with progress output:

```powershell
.\.venv\Scripts\python.exe -B scripts/refresh_risks.py
```

To update the bundled plant catalog from the latest workbook on EIA's page:

```powershell
.\.venv\Scripts\python.exe -B scripts/import_eia.py
```

Restart the backend after updating the catalog. EIA plants are matched by original
plant ID. Previously imported plants and custom sites are retained. The importer
uses Python's standard ZIP/XML libraries; no Excel installation is needed.

## Response contract

A site risk response contains:

- `site`: name, coordinates, internal ID, and EIA metadata.
- `generated_at`, `method_version`, `forecast_hours`, `cached`, and `stale`.
- `overall`: `level`, `highest_known_level`, `drivers`, `coverage_complete`,
  `coverage_gaps`, and an explanation of the aggregation method.
- `indicators`: seven named cards, each with `level`, `basis`, `evidence`,
  provider keys in `sources`, and `coverage` (`available`, `partial`, `unavailable`).
- `sources`: feed URLs, retrieval timestamps, availability, and failure reasons.
- `weather`: current conditions, 48-hour forecasts, and recent rainfall, or null
  when weather is unavailable.

The portfolio response contains `count` and a `sites` array. Each entry has the
site, assessment status, timestamp, stale flag, overall result, and `risk_url`.
An old result is retained for reference but always marked stale; `pending` means
that the site has never been assessed.

## Sources and interpretation

| Indicator | Inputs | Interpretation |
| --- | --- | --- |
| `weather` | Open-Meteo gusts and hourly precipitation; NWS storm/wind alerts | Planning screen over current conditions and the next 48 hours |
| `extreme_heat` | Open-Meteo apparent temperature; NWS heat alerts | Temperature screen, not worker-specific heat exposure |
| `lightning` | Open-Meteo thunderstorm codes 95, 96, 99; NWS thunderstorm alerts | Forecast proxy; measured lightning strikes are unavailable |
| `flooding` | NWS flood alerts, recent rainfall, maximum rolling 24-hour forecast precipitation | Rainfall/alert screen; no floodplain or inundation calculation |
| `wildfire` | NWS fire alerts and NASA EONET open wildfire points reported in the last 14 days | Proximity within 50 km; not fire perimeters or an exhaustive fire inventory |
| `soil_saturation` | Open-Meteo volumetric soil moisture at 3–9 cm and recent rainfall | Soil wetness proxy; actual saturation requires soil-specific porosity |
| `wildlife_sightings` | iNaturalist research-grade, non-captive Animalia observations in the last 30 days within 10 km | Ecological context; not species danger, absence, or protected-species clearance |

Wildlife evidence includes a total matching count and up to 20 recent sightings,
with species names, dates, source links, and an obscuration flag. Obscured public
locations can shift search membership. Exact sighting coordinates and observer
identities are not returned. Empty search results do not establish absence.

Wildfire distances use the latest reported point for each open event and the
haversine formula. Unsupported non-point geometries are counted and mark fire
coverage partial rather than being silently treated as no fire.

Provider documentation:
[EIA-860M](https://www.eia.gov/electricity/data/eia860m/),
[Open-Meteo](https://open-meteo.com/en/docs),
[NWS](https://www.weather.gov/documentation/services-web-api),
[NASA EONET](https://eonet.gsfc.nasa.gov/docs/v3),
[iNaturalist](https://api.inaturalist.org/v1/docs/).

## Transparent screening rules

These are application screening thresholds, not official hazard probabilities
or engineering determinations. Threshold lists below correspond to **watch / high /
severe**, inclusive at each boundary:

| Metric | Thresholds |
| --- | --- |
| Maximum gust | 30 / 45 / 65 mph |
| Maximum hourly precipitation | 0.30 / 0.75 / 1.00 inches |
| Maximum apparent temperature | 90 / 100 / 110 °F |
| Maximum rolling 24-hour forecast precipitation or last 24-hour rain | 1 / 2 / 4 inches |
| Maximum modeled soil water content at 3–9 cm | 0.30 / 0.40 / 0.50 m³/m³ (not calibrated to local soil) |

Thunderstorm codes trigger `high`. Wildfire points within 10 km trigger `high`;
within 25 km trigger `watch`. Relevant NWS alerts raise a card to at least the
alert's mapped severity: Extreme → severe, Severe → high, Moderate/Minor → watch.
Warnings are at least high. Expired, cancelled, and test alerts are excluded.

Overall severity is the **maximum** of environmental cards; wildlife is `info`
and never raises severity on sighting count alone. Missing data is never scored
as zero. When coverage is incomplete and all known signals are low, overall is
`unknown`. A known high/severe hazard remains visible even when other feeds fail.
`coverage_complete` describes the selected feeds/fields, not complete knowledge
of all hazards at the site.

## Caching and failures

- Completed risk snapshots: 5 minutes; incomplete snapshots: 1 minute.
- Open-Meteo: 15 minutes; NWS: 5 minutes; EONET: 30 minutes; wildlife: 24 hours.
- Provider errors: cached for 1 minute to avoid repeated failing calls.
- Timeout per upstream request: 15 seconds, with 5 seconds for connection setup.
- Batches use four concurrent sites, one request per provider at a time, and at
  least 1.1 seconds between wildlife requests. The global EONET feed is shared.

The caches live in SQLite; no API keys are needed for the configured public
endpoints. `PLANIT_USER_AGENT` can set the NWS application/contact identifier.
`PLANIT_DATABASE_PATH` can select a persistent database file.
Provider failures produce partial risk responses with explicit reasons instead
of concealing other available signals. A missing site still returns 404.

Run checks with `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`.