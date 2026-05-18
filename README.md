# RouteRX

RouteRX computes ZIP-level mobile-clinic routes inside a single county. It uses ZIP-level facility counts to derive a vulnerability proxy for each ZIP, then routes between public parking locations.

## Routing Model

Inputs from the client:

- `state`
- `county` (required)
- `num_places`
- `svi_weight`
- `improve_2opt`
- `use_clustering`

How routing works:

1. Load ZIP rows for the requested `state + county` from `zip_facility_catalog.csv`. If the catalog does not exist yet, RouteRX builds it from `zip_facility_counts.csv` plus the facility source CSVs.
2. Derive a ZIP vulnerability proxy from the facility-count columns. For each facility type, fewer facilities means a higher scarcity score relative to the other ZIPs in the same county. The ZIP score is the mean of those scarcity scores.
3. For each ZIP, collect candidate public parking points from cached data, embedded catalog data, or OpenStreetMap. If no parking is found, RouteRX falls back to the ZIP representative point and then optionally the ZIP centroid.
4. Select ZIP stops.
   - Default: sort ZIPs on `weighted_svi = svi_weight * zip_svi` and take the top `num_places`.
   - With clustering: cluster ZIPs geographically first, then take the top-vulnerability ZIP from each cluster.
5. Order the selected ZIPs with nearest-neighbor routing.
6. If `improve_2opt=true`, run 2-opt to shorten the route.
7. For each selected ZIP, choose the parking lot that minimizes local detour relative to adjacent stops.

Notes:

- ZIP SVI in this workflow is a locally derived proxy based on facility scarcity, not the CDC county `RPL_THEMES` value.
- The score is relative within the selected county, so values are best compared against other ZIPs in that same county.
- As with the old county-level workflow, selection happens before routing. Distance only affects route order and parking-point choice after the top ZIPs are chosen.
- Route ordering uses OSM road-network distance by default. Parking selection uses haversine by default because it is much faster.

## CLI

Basic example:

```bash
python route_finder.py \
  --state "Massachusetts" \
  --county "Suffolk" \
  --num_places 8 \
  --svi_weight 1.0 \
  --cache_csv ma_cached.csv \
  --out route_output.csv \
  --html route_map.html \
  --log_progress
```

Common flags:

- `--svi_csv`: Legacy county SVI CSV path. Kept for compatibility but not used in ZIP scoring.
- `--zip_catalog_csv`: ZIP catalog CSV. Default `zip_facility_catalog.csv`.
- `--zip_counts_csv`: Source counts CSV used to build the ZIP catalog if needed. Default `zip_facility_counts.csv`.
- `--state`: State name or abbreviation.
- `--county`: County name. Required.
- `--num_places`: Number of ZIP stops to select.
- `--svi_weight`: Multiplier applied to the derived ZIP vulnerability score before selecting the top ZIPs.
- `--use_clustering`: Spread selections geographically by choosing the top-vulnerability ZIP from each geographic cluster.
- `--no_2opt`: Skip 2-opt route improvement.
- `--no_centroid_fallback`: Disable ZIP centroid fallback when no parking lots are found.
- `--distance_mode`: `haversine|osm|auto|google`. Default `osm`. `google` is accepted only as a compatibility alias for `osm`.
- `--parking_distance_mode`: `haversine|osm|auto|google`. Default `haversine`.
- `--cache_csv`: Optional per-ZIP parking cache.
- `--out`: Output CSV path.
- `--html`: Optional Folium map HTML path.
- `--log_progress`: Print progress logs.

## Flask API

Run the server:

```bash
flask --app route_finder:create_app run --host 0.0.0.0 --port 8000
```

CORS:

- `/api/*` responses include CORS headers.
- Configure allowed origins with `CORS_ALLOW_ORIGINS`.
- Example: `CORS_ALLOW_ORIGINS="http://localhost:5173,https://your-app.example"`

### `GET /api/health`

Response:

```json
{"status":"ok"}
```

### `POST /api/route`

Client-style request:

```json
{
  "state": "Massachusetts",
  "county": "Suffolk",
  "num_places": 8,
  "svi_weight": 1.0,
  "improve_2opt": true,
  "use_clustering": false
}
```

Advanced optional fields:

- `svi_csv`
- `zip_catalog_csv`
- `zip_counts_csv`
- `cache_csv`
- `sleep_s`
- `log_progress`
- `use_centroid_fallback`
- `distance_mode`
- `parking_distance_mode`
- `osm`: `{ "network_type": "drive", "buffer_km": 5.0 }`

`svi_overall` in the response is the derived ZIP-level vulnerability proxy.

Example `curl`:

```bash
curl -X POST http://localhost:8000/api/route \
  -H "Content-Type: application/json" \
  -d '{
    "state": "Massachusetts",
    "county": "Suffolk",
    "num_places": 8,
    "svi_weight": 1.0,
    "improve_2opt": true,
    "use_clustering": false
  }'
```

Response shape:

```json
{
  "summary": {
    "num_stops": 8,
    "total_km": 42.7,
    "total_svi": 7.2,
    "total_weighted_svi": 7.2
  },
  "stops": [
    {
      "order": 1,
      "zip_code": "02111",
      "county": "Suffolk",
      "state": "Massachusetts",
      "state_abbr": "MA",
      "svi_overall": 0.9,
      "weighted_svi": 0.9,
      "hospitals": 0,
      "nursing_homes": 0,
      "public_health_departments": 0,
      "pharmacies": 1,
      "resource_sites": 1,
      "parking_source": "osm",
      "parking_lat": 42.3506,
      "parking_lon": -71.0597,
      "leg_km_from_prev": 0.0,
      "total_km": 0.0,
      "total_svi": 0.9,
      "total_weighted_svi": 0.9
    }
  ]
}
```

For backward compatibility, the same payload is also exposed as `stats` and `route`.

`parking_source` is one of:
- `osm`: a real parking candidate returned by OpenStreetMap
- `representative_fallback`: no parking lot was found, so the ZIP representative point was used
- `centroid_fallback`: no parking lot or representative point was available, so the ZIP centroid was used

### `POST /api/route.csv`

Returns the computed route as CSV.

```bash
curl -X POST http://localhost:8000/api/route.csv \
  -H "Content-Type: application/json" \
  -d '{
    "state": "Massachusetts",
    "county": "Suffolk",
    "num_places": 8,
    "svi_weight": 1.0,
    "improve_2opt": true,
    "use_clustering": false
  }' > route_output.csv
```

### `POST /api/route/map`

Returns a Folium HTML map.

```bash
curl -X POST http://localhost:8000/api/route/map \
  -H "Content-Type: application/json" \
  -d '{
    "state": "Massachusetts",
    "county": "Suffolk",
    "num_places": 8,
    "svi_weight": 1.0,
    "improve_2opt": true,
    "use_clustering": false
  }' > route_map.html
```

### `POST /api/route/preview`

Returns only the summary block.

## Test

```bash
pytest -q
```

## Files

- `route_finder.py`: core ZIP routing logic, CLI, and Flask API.
- `app.py`: thin Flask entrypoint wrapper.
- `zip_facility_counts.csv`: ZIP-level facility counts.
- `zip_facility_catalog.csv`: generated ZIP catalog with representative coordinates.
