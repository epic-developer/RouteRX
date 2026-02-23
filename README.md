# RouteRX Backend + CLI

This project provides a CLI and a Flask API for computing county-level routes for a mobile clinic van.

**Quick Start (CLI)**

```bash
python route_finder.py \
  --svi_csv SVI_2022_US_county.csv \
  --state "Massachusetts" \
  --num_places 8 \
  --svi_weight 1.0 \
  --cache_csv ma_cached.csv \
  --out route_output.csv \
  --html route_map.html \
  --log_progress
```

**Quick Start (API)**

```bash
export GOOGLE_MAPS_API_KEY="YOUR_KEY"
flask --app route_finder:create_app run --host 0.0.0.0 --port 8000
```

**Environment Variables**

- `GOOGLE_MAPS_API_KEY` enables Google Distance Matrix requests.
- `CORS_ALLOW_ORIGINS` controls CORS for `/api/*`. Default is `*`. Comma-separated list, for example `https://app.example.com,http://localhost:5173`.

**How Routing Works**

1. **Selection (SVI-driven)**  
   The script selects the top `num_places` counties by `weighted_svi = svi_weight * RPL_THEMES`.

2. **Ordering (distance-driven)**  
   It orders the selected counties using a nearest-neighbor heuristic and optional 2‑opt improvement.

3. **Parking selection (local optimization)**  
   For each county, it picks the parking point that minimizes distance to the previous and next stops.

Distances are either haversine (fast, offline) or Google Distance Matrix (slower, paid).

**CLI Usage**

Common flags:

- `--svi_csv` (required): CSV with `STATE, COUNTY, FIPS, RPL_THEMES`.
- `--state` (required): Full state name, e.g. `Massachusetts`.
- `--num_places` (required): Number of counties to include.
- `--svi_weight`: Scale SVI before selection (default `1.0`).
- `--start_county`: Force-include and start route at this county.
- `--no_2opt`: Disable 2-opt improvement.
- `--no_centroid_fallback`: Disable centroid fallback if no parking lots found.
- `--sleep_s`: Delay between OSM queries (default `1.0`).
- `--cache_csv`: Cache parking lots to avoid re-querying OSM.
- `--out`: Output CSV (default `route_output.csv`).
- `--html`: Output map HTML (optional).
- `--log_progress`: Print progress logs.
- `--distance_mode`: `auto|haversine|google` (default `auto`).
- `--parking_distance_mode`: `auto|haversine|google` (default `auto`).

Notes:

- `distance_mode=auto` uses Google if `GOOGLE_MAPS_API_KEY` is set.
- `parking_distance_mode=auto` defaults to haversine to avoid excessive API calls.

**API Endpoints**

`GET /api/health`

Response:
```json
{"status":"ok"}
```

`POST /api/route`

Request body fields:

- `svi_csv` (string, required). CSV path relative to the project root, for example `MA_SVI_official.csv`.
- `state` (string, required). Full state name in the CSV, for example `Massachusetts`.
- `num_places` (int, required). Number of counties to include.
- `svi_weight` (float, optional, default `1.0`). Scales the SVI for selection.
- `start_county` (string, optional). Force-include county and start route there.
- `improve_2opt` (bool, optional, default `true`). Apply 2-opt improvement.
- `use_centroid_fallback` (bool, optional, default `true`). If no parking lots found, use county centroid.
- `sleep_s` (float, optional, default `0.0`). Delay between OSM queries.
- `cache_csv` (string, optional). Cache parking lots within project directory.
- `distance_mode` (string, optional, default `haversine`). `haversine` or `google`.
- `parking_distance_mode` (string, optional, default `haversine`). Use `google` if you want travel-time parking selection.
- `google_maps_api_key` (string, optional). Overrides the `GOOGLE_MAPS_API_KEY` env var.
- `google` (object, optional). Google Distance Matrix options. See below for fields.

Google options fields (inside `google`):

- `mode` (string, default `driving`).
- `units` (string, default `metric`).
- `avoid_tolls` (bool, default `false`).
- `avoid_highways` (bool, default `false`).

Example request:
```json
{
  "svi_csv": "MA_SVI_official.csv",
  "state": "Massachusetts",
  "num_places": 8,
  "svi_weight": 1.0,
  "start_county": "Suffolk",
  "improve_2opt": true,
  "use_centroid_fallback": true,
  "sleep_s": 0.0,
  "cache_csv": "ma_cached.csv",
  "distance_mode": "google",
  "parking_distance_mode": "haversine",
  "google": {
    "mode": "driving",
    "avoid_tolls": false,
    "avoid_highways": false
  }
}
```

Example `curl` (similar to the CLI command shown above):
```bash
curl -X POST http://localhost:8000/api/route \
  -H "Content-Type: application/json" \
  -d '{
    "svi_csv": "SVI_2022_US_county.csv",
    "state": "Massachusetts",
    "num_places": 8,
    "svi_weight": 1.0,
    "cache_csv": "ma_cached.csv",
    "distance_mode": "google",
    "parking_distance_mode": "haversine",
    "log_progress": true
  }'
```

Response:

```json
{
  "summary": {
    "num_stops": 8,
    "total_km": 145.2,
    "total_svi": 4.38,
    "total_weighted_svi": 4.38
  },
  "stops": [
    {
      "order": 1,
      "state": "Massachusetts",
      "county": "Suffolk",
      "fips": "25025",
      "svi_overall": 0.9735,
      "weighted_svi": 0.9735,
      "parking_lat": 42.3557,
      "parking_lon": -71.0562,
      "leg_km_from_prev": 0.0,
      "total_km": 0.0,
      "total_svi": 0.9735,
      "total_weighted_svi": 0.9735
    }
  ]
}
```

`POST /api/route.csv`

Returns the same data as `/api/route`, but as CSV (`text/csv`).

Example `curl`:
```bash
curl -X POST http://localhost:8000/api/route.csv \
  -H "Content-Type: application/json" \
  -d '{
    "svi_csv": "SVI_2022_US_county.csv",
    "state": "Massachusetts",
    "num_places": 8,
    "svi_weight": 1.0,
    "cache_csv": "ma_cached.csv",
    "distance_mode": "google",
    "parking_distance_mode": "haversine"
  }' > route_output.csv
```

`POST /api/route/map`

Returns an interactive HTML map (`text/html`) using Folium. Requires `folium` installed.

Example `curl`:
```bash
curl -X POST http://localhost:8000/api/route/map \
  -H "Content-Type: application/json" \
  -d '{
    "svi_csv": "SVI_2022_US_county.csv",
    "state": "Massachusetts",
    "num_places": 8,
    "svi_weight": 1.0,
    "cache_csv": "ma_cached.csv",
    "distance_mode": "google",
    "parking_distance_mode": "haversine"
  }' > route_map.html
```

`POST /api/route/preview`

Returns only the summary totals for quick previews.

**Notes**

- Paths are restricted to the project directory for safety.
- If `distance_mode` is `google` and no key is provided, the API returns a 400 error.
- Google distances are cached in-memory per process; restart clears the cache.
