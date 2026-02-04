# RouteRX Backend (Flask)

This backend exposes the existing routing pipeline in `route_finder.py` over HTTP so a frontend can request routes with clean JSON.

**Quick Start**

1. Create a virtual environment and install dependencies.
2. Set `GOOGLE_MAPS_API_KEY` if you want Google travel distances.
3. Run the API:

```bash
export GOOGLE_MAPS_API_KEY="YOUR_KEY"
flask --app route_finder:create_app run --host 0.0.0.0 --port 8000
```

**Environment Variables**

- `GOOGLE_MAPS_API_KEY` enables Google Distance Matrix requests when `distance_mode` is `google`.
- `CORS_ALLOW_ORIGINS` controls CORS for `/api/*`. Default is `*`. Comma-separated list, for example `https://app.example.com,http://localhost:5173`.

**Endpoints**

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

**Notes**

- Paths are restricted to the project directory for safety.
- If `distance_mode` is `google` and no key is provided, the API returns a 400 error.
- Google distances are cached in-memory per process; restart clears the cache.
