from __future__ import annotations

import argparse
import math
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

try:
    import googlemaps
except Exception:
    googlemaps = None

try:
    from flask import Flask, jsonify, request
except Exception:
    Flask = None
    jsonify = None
    request = None

try:
    import osmnx as ox
except Exception:
    ox = None

try:
    import folium
except Exception:
    folium = None

LatLon = Tuple[float, float]  # (lat, lon)


@dataclass(frozen=True)
class GoogleMapsOptions:
    mode: str = "driving"
    units: str = "metric"
    avoid_tolls: bool = False
    avoid_highways: bool = False


@dataclass(frozen=True)
class CountyNode:
    state: str
    county: str
    fips: str
    svi: float               # raw RPL_THEMES
    weighted_svi: float      # svi_weight * RPL_THEMES
    parking_pts: List[LatLon]


# ---------------------------
# Parsing + geometry helpers
# ---------------------------

def parse_latlon_list(cell) -> List[LatLon]:
    if cell is None or (isinstance(cell, float) and math.isnan(cell)):
        return []
    pts: List[LatLon] = []
    for part in str(cell).split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            lat_s, lon_s = part.split(",")
            pts.append((float(lat_s.strip()), float(lon_s.strip())))
        except Exception:
            continue
    return pts


def haversine_km(a: LatLon, b: LatLon) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


class DistanceProvider:
    def distance_km(self, a: LatLon, b: LatLon) -> float:
        raise NotImplementedError

    def distances_km(self, origin: LatLon, destinations: List[LatLon]) -> List[float]:
        return [self.distance_km(origin, dest) for dest in destinations]


class HaversineDistanceProvider(DistanceProvider):
    def distance_km(self, a: LatLon, b: LatLon) -> float:
        return haversine_km(a, b)


class GoogleMapsDistanceProvider(DistanceProvider):
    def __init__(self, api_key: str, options: Optional[GoogleMapsOptions] = None, max_destinations: int = 25):
        if googlemaps is None:
            raise RuntimeError("googlemaps is not installed. Install with: pip install googlemaps")
        if not api_key:
            raise ValueError("googlemaps API key is required.")
        self.client = googlemaps.Client(key=api_key)
        self.options = options or GoogleMapsOptions()
        self.max_destinations = max_destinations
        self._cache: Dict[Tuple[float, float, float, float, str, bool, bool], float] = {}

    def distance_km(self, a: LatLon, b: LatLon) -> float:
        key = self._cache_key(a, b)
        if key in self._cache:
            return self._cache[key]

        distances = self.distances_km(a, [b])
        return distances[0]

    def distances_km(self, origin: LatLon, destinations: List[LatLon]) -> List[float]:
        if not destinations:
            return []

        results: List[float] = []
        for i in range(0, len(destinations), self.max_destinations):
            chunk = destinations[i:i + self.max_destinations]
            results.extend(self._fetch_chunk(origin, chunk))
        return results

    def _fetch_chunk(self, origin: LatLon, destinations: List[LatLon]) -> List[float]:
        try:
            response = self.client.distance_matrix(
                origins=[self._format_latlon(origin)],
                destinations=[self._format_latlon(d) for d in destinations],
                mode=self.options.mode,
                units=self.options.units,
                avoid=self._avoid_list() or None,
            )
            rows = response.get("rows", [])
            elements = rows[0].get("elements", []) if rows else []
        except Exception:
            elements = []

        results: List[float] = []
        for dest, element in zip(destinations, elements):
            km = self._element_distance_km(origin, dest, element)
            results.append(km)

        if len(results) < len(destinations):
            for dest in destinations[len(results):]:
                results.append(haversine_km(origin, dest))
        return results

    def _element_distance_km(self, origin: LatLon, dest: LatLon, element: dict) -> float:
        if element.get("status") == "OK":
            distance = element.get("distance", {})
            if "value" in distance:
                km = float(distance["value"]) / 1000.0
                self._cache[self._cache_key(origin, dest)] = km
                return km
        return haversine_km(origin, dest)

    def _cache_key(self, a: LatLon, b: LatLon) -> Tuple[float, float, float, float, str, bool, bool]:
        return (
            round(a[0], 6),
            round(a[1], 6),
            round(b[0], 6),
            round(b[1], 6),
            self.options.mode,
            self.options.avoid_tolls,
            self.options.avoid_highways,
        )

    @staticmethod
    def _format_latlon(point: LatLon) -> str:
        return f"{point[0]},{point[1]}"

    def _avoid_list(self) -> List[str]:
        avoid: List[str] = []
        if self.options.avoid_tolls:
            avoid.append("tolls")
        if self.options.avoid_highways:
            avoid.append("highways")
        return avoid


def centroid_fallback(county_name: str, state_name: str) -> Optional[LatLon]:
    if ox is None:
        return None
    place = f"{county_name}, {state_name}, USA"
    try:
        gdf = ox.geocode_to_gdf(place)
        if gdf.empty:
            return None
        pt = gdf.iloc[0].geometry.centroid
        return (float(pt.y), float(pt.x))
    except Exception:
        return None

def export_route_map_html(route_df: pd.DataFrame, html_path: str) -> None:
    """
    Export an interactive HTML map of the route using Folium.
    Requires folium installed: pip install folium
    """
    if folium is None:
        raise RuntimeError("folium is not installed. Install with: pip install folium")

    if route_df.empty:
        raise ValueError("route_df is empty; nothing to map.")

    # Build ordered coordinate list (lat, lon)
    coords = list(zip(route_df["parking_lat"].astype(float), route_df["parking_lon"].astype(float)))

    # Center map on mean coordinate
    mean_lat = sum(lat for lat, _ in coords) / len(coords)
    mean_lon = sum(lon for _, lon in coords) / len(coords)

    m = folium.Map(location=[mean_lat, mean_lon], zoom_start=8, tiles="OpenStreetMap")

    # Draw route line
    folium.PolyLine(locations=coords, weight=4, opacity=0.8).add_to(m)

    # Add markers
    for i, row in route_df.iterrows():
        lat = float(row["parking_lat"])
        lon = float(row["parking_lon"])
        county = str(row.get("county", ""))
        svi = row.get("svi_overall", "")
        order = int(row.get("order", i + 1))

        popup = folium.Popup(
            f"<b>Stop {order}</b><br>{county}<br>SVI: {svi}",
            max_width=300
        )

        if i == 0:
            # Start marker
            icon = folium.Icon(color="green", icon="play", prefix="fa")
        elif i == len(route_df) - 1:
            # Finish marker
            icon = folium.Icon(color="red", icon="stop", prefix="fa")
        else:
            # Intermediate marker
            icon = folium.Icon(color="blue", icon="circle", prefix="fa")

        folium.Marker([lat, lon], popup=popup, icon=icon).add_to(m)

    m.save(html_path)

def get_parking_lots_for_county(county_name: str, state_name: str, sleep_s: float = 1.0) -> List[LatLon]:
    """
    Returns list of parking lot points (lat,lon) from OSM inside the county polygon.
    Requires osmnx and internet.
    """
    if ox is None:
        return []

    place_name = f"{county_name}, {state_name}, USA"
    try:
        gdf = ox.geocode_to_gdf(place_name)
        if gdf.empty:
            return []
        polygon = gdf.iloc[0].geometry

        tags = {"amenity": "parking"}
        parks = ox.features.features_from_polygon(polygon, tags)
        if parks.empty:
            return []

        # Best-effort "public-ish" filter
        if "access" in parks.columns:
            parks = parks[~parks["access"].isin(["private", "no", "customers"])]

        if parks.empty:
            return []

        pts: List[LatLon] = []
        for geom in parks.geometry:
            if geom.is_empty:
                continue
            pt = geom.centroid if geom.geom_type in ["Polygon", "MultiPolygon"] else geom
            pts.append((float(pt.y), float(pt.x)))  # lat, lon

        # Be nice to Overpass/Nominatim
        if sleep_s > 0:
            time.sleep(sleep_s)

        return pts

    except Exception:
        return []


def representative_point(pts: List[LatLon]) -> Optional[LatLon]:
    if not pts:
        return None
    mean_lat = sum(p[0] for p in pts) / len(pts)
    mean_lon = sum(p[1] for p in pts) / len(pts)
    return min(pts, key=lambda p: haversine_km(p, (mean_lat, mean_lon)))


# ---------------------------
# Routing heuristics
# ---------------------------

def nearest_neighbor_route(points: List[LatLon], start_idx: int, distance_provider: DistanceProvider) -> List[int]:
    n = len(points)
    unvisited = set(range(n))
    route = [start_idx]
    unvisited.remove(start_idx)
    cur = start_idx
    while unvisited:
        unvisited_list = list(unvisited)
        dists = distance_provider.distances_km(points[cur], [points[j] for j in unvisited_list])
        nxt = min(zip(unvisited_list, dists), key=lambda pair: pair[1])[0]
        route.append(nxt)
        unvisited.remove(nxt)
        cur = nxt
    return route


def route_length_km(points: List[LatLon], route: List[int], distance_provider: DistanceProvider) -> float:
    if len(route) <= 1:
        return 0.0
    return sum(distance_provider.distance_km(points[route[i]], points[route[i + 1]]) for i in range(len(route) - 1))


def two_opt(points: List[LatLon], route: List[int], distance_provider: DistanceProvider, max_iters: int = 500) -> List[int]:
    best = route[:]
    best_len = route_length_km(points, best, distance_provider)
    n = len(best)
    improved = True
    iters = 0
    while improved and iters < max_iters:
        improved = False
        iters += 1
        for i in range(1, n - 2):
            for k in range(i + 1, n - 1):
                new = best[:i] + list(reversed(best[i:k + 1])) + best[k + 1:]
                new_len = route_length_km(points, new, distance_provider)
                if new_len + 1e-9 < best_len:
                    best, best_len = new, new_len
                    improved = True
                    break
            if improved:
                break
    return best


def choose_parking_for_route(
    nodes: List[CountyNode],
    route: List[int],
    reps: List[LatLon],
    distance_provider: DistanceProvider,
) -> List[LatLon]:
    chosen: List[LatLon] = []
    for pos, idx in enumerate(route):
        node = nodes[idx]
        pts = node.parking_pts
        if not pts:
            chosen.append(reps[idx])
            continue

        if len(route) == 1:
            chosen.append(pts[0])
            continue

        if pos == 0:
            nxt_pt = reps[route[1]]
            best = min(pts, key=lambda p: distance_provider.distance_km(p, nxt_pt))
        elif pos == len(route) - 1:
            prev_pt = reps[route[-2]]
            best = min(pts, key=lambda p: distance_provider.distance_km(prev_pt, p))
        else:
            prev_pt = reps[route[pos - 1]]
            nxt_pt = reps[route[pos + 1]]
            best = min(
                pts,
                key=lambda p: distance_provider.distance_km(prev_pt, p) + distance_provider.distance_km(p, nxt_pt),
            )
        chosen.append(best)
    return chosen


# ---------------------------
# Data assembly + main
# ---------------------------

def build_nodes(
    svi_csv: str,
    state_name: str,
    svi_weight: float,
    sleep_s: float,
    use_centroid_if_missing: bool,
    cache_csv: Optional[str],
) -> List[CountyNode]:
    df = pd.read_csv(svi_csv, dtype=str)

    required = {"STATE", "COUNTY", "FIPS", "RPL_THEMES"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"SVI CSV missing required columns: {sorted(missing)}")

    # Filter to chosen state (expects full state name like "Massachusetts")
    df = df[df["STATE"].astype(str).str.strip() == state_name].copy()
    if df.empty:
        raise ValueError(f"No rows found for STATE='{state_name}'. Check spelling/case in the CSV.")

    # If cached Parking Lots exists, load it
    if "Parking Lots" not in df.columns:
        df["Parking Lots"] = ""

    parking_cache: dict[str, str] = {}
    if cache_csv and os.path.exists(cache_csv):
        cached = pd.read_csv(cache_csv, dtype=str)
        if {"STATE", "COUNTY", "FIPS", "Parking Lots"}.issubset(set(cached.columns)):
            cached = cached[cached["STATE"].astype(str).str.strip() == state_name].copy()
            for _, r in cached.iterrows():
                key = str(r["FIPS"]).split(".")[0].zfill(5)
                parking_cache[key] = str(r.get("Parking Lots") or "").strip()

    nodes: List[CountyNode] = []

    # Build per-county parking lots if missing
    parking_strings: List[str] = []
    for _, row in df.iterrows():
        state = str(row["STATE"]).strip()
        county = str(row["COUNTY"]).strip()
        fips = str(row["FIPS"]).split(".")[0].zfill(5)
        svi = float(row["RPL_THEMES"])
        weighted = svi_weight * svi

        # Use cache first
        cell = parking_cache.get(fips, str(row.get("Parking Lots") or "").strip())
        pts = parse_latlon_list(cell)

        if not pts:
            pts = get_parking_lots_for_county(county, state, sleep_s=sleep_s)

        if not pts and use_centroid_if_missing:
            fallback = centroid_fallback(county, state)
            if fallback is not None:
                pts = [fallback]

        # Save back into df (for optional caching)
        parking_cell = "; ".join([f"{p[0]:.6f},{p[1]:.6f}" for p in pts])
        parking_strings.append(parking_cell)

        nodes.append(CountyNode(state=state, county=county, fips=fips, svi=svi, weighted_svi=weighted, parking_pts=pts))

    df["Parking Lots"] = parking_strings
    if cache_csv:
        df.to_csv(cache_csv, index=False)

    return nodes


def find_route(
    nodes: List[CountyNode],
    num_places: int,
    start_county: Optional[str],
    improve_2opt: bool,
    distance_provider: Optional[DistanceProvider] = None,
    parking_distance_provider: Optional[DistanceProvider] = None,
) -> pd.DataFrame:
    if num_places <= 0:
        raise ValueError("num_places must be >= 1")

    distance_provider = distance_provider or HaversineDistanceProvider()
    parking_distance_provider = parking_distance_provider or HaversineDistanceProvider()

    usable = [n for n in nodes if n.parking_pts]
    if len(usable) < num_places:
        raise ValueError(f"Only {len(usable)} counties have usable coordinates, but num_places={num_places}.")

    # Select top-k by WEIGHTED SVI
    usable_sorted = sorted(usable, key=lambda n: n.weighted_svi, reverse=True)
    selected = usable_sorted[:num_places]

    # Force include start_county if provided
    if start_county:
        sk = start_county.strip().lower()
        match = next((n for n in usable if n.county.lower() == sk), None)
        if match is None:
            raise ValueError(f"start_county='{start_county}' not found in the selected state.")
        if match not in selected:
            selected = selected[:-1] + [match]
        selected = sorted(selected, key=lambda n: n.weighted_svi, reverse=True)

    reps = [representative_point(n.parking_pts) for n in selected]
    if any(r is None for r in reps):
        raise ValueError("Some selected counties have no representative point.")
    reps = [r for r in reps]  # type: ignore

    if start_county:
        start_idx = next(i for i, n in enumerate(selected) if n.county.lower() == start_county.strip().lower())
    else:
        start_idx = 0

    route = nearest_neighbor_route(reps, start_idx, distance_provider)
    if improve_2opt and len(route) >= 4:
        route = two_opt(reps, route, distance_provider)

    chosen_pts = choose_parking_for_route(selected, route, reps, parking_distance_provider)

    out_rows = []
    for order, (idx, chosen) in enumerate(zip(route, chosen_pts), start=1):
        n = selected[idx]
        out_rows.append({
            "order": order,
            "state": n.state,
            "county": n.county,
            "fips": n.fips,
            "svi_overall": n.svi,
            "weighted_svi": n.weighted_svi,
            "parking_lat": chosen[0],
            "parking_lon": chosen[1],
        })

    out = pd.DataFrame(out_rows)
    out["leg_km_from_prev"] = 0.0
    for i in range(1, len(out)):
        prev = (out.loc[i - 1, "parking_lat"], out.loc[i - 1, "parking_lon"])
        cur = (out.loc[i, "parking_lat"], out.loc[i, "parking_lon"])
        out.loc[i, "leg_km_from_prev"] = distance_provider.distance_km(prev, cur)
    out["total_km"] = out["leg_km_from_prev"].cumsum()
    out["total_svi"] = out["svi_overall"].cumsum()
    out["total_weighted_svi"] = out["weighted_svi"].cumsum()
    return out


def build_distance_provider(
    mode: str,
    google_api_key: Optional[str],
    google_options: Optional[GoogleMapsOptions] = None,
) -> DistanceProvider:
    normalized = (mode or "haversine").strip().lower()
    if normalized == "google":
        if not google_api_key:
            raise ValueError("google_maps_api_key is required when distance_mode is 'google'.")
        return GoogleMapsDistanceProvider(api_key=google_api_key, options=google_options)
    if normalized != "haversine":
        raise ValueError(f"Unsupported distance_mode '{mode}'. Use 'haversine' or 'google'.")
    return HaversineDistanceProvider()


def _project_root() -> str:
    return os.path.abspath(os.path.dirname(__file__))


def _resolve_project_path(path_value: Optional[str]) -> Optional[str]:
    if path_value is None:
        return None
    raw = os.path.expanduser(path_value)
    abs_path = raw if os.path.isabs(raw) else os.path.join(_project_root(), raw)
    abs_path = os.path.abspath(abs_path)
    root = _project_root()
    if abs_path != root and not abs_path.startswith(root + os.sep):
        raise ValueError("Path must be within the project directory.")
    return abs_path


def _require_flask() -> None:
    if Flask is None:
        raise RuntimeError("Flask is not installed. Install with: pip install flask")
    if jsonify is None or request is None:
        raise RuntimeError("Flask import failed; cannot start API.")


def _parse_cors_origins(raw: Optional[str]) -> List[str]:
    if not raw:
        return ["*"]
    origins = [o.strip() for o in raw.split(",")]
    return [o for o in origins if o]


def create_app() -> "Flask":
    _require_flask()
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    cors_origins = _parse_cors_origins(os.getenv("CORS_ALLOW_ORIGINS"))

    @app.after_request
    def _add_cors_headers(response):
        path = request.path if request else ""
        if path.startswith("/api/"):
            origin = request.headers.get("Origin") if request else None
            allow_origin = None
            if "*" in cors_origins:
                allow_origin = "*"
            elif origin in cors_origins:
                allow_origin = origin
            if allow_origin:
                response.headers["Access-Control-Allow-Origin"] = allow_origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Max-Age"] = "600"
        return response

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/api/route")
    def api_route():
        payload = request.get_json(silent=True) or {}

        try:
            svi_csv = payload.get("svi_csv")
            state = payload.get("state")
            num_places = payload.get("num_places")
            if not svi_csv or not state or num_places is None:
                return jsonify({"error": "svi_csv, state, and num_places are required."}), 400

            num_places = int(num_places)
            svi_weight = float(payload.get("svi_weight", 1.0))
            start_county = payload.get("start_county")
            improve_2opt = bool(payload.get("improve_2opt", True))
            use_centroid_fallback = bool(payload.get("use_centroid_fallback", True))
            sleep_s = float(payload.get("sleep_s", 0.0))

            svi_csv_path = _resolve_project_path(str(svi_csv))
            cache_csv_path = _resolve_project_path(payload.get("cache_csv"))

            if not svi_csv_path or not os.path.exists(svi_csv_path):
                return jsonify({"error": f"svi_csv not found: {svi_csv}"}), 400

            google_config = payload.get("google", {}) or {}
            google_options = GoogleMapsOptions(
                mode=str(google_config.get("mode", "driving")),
                units=str(google_config.get("units", "metric")),
                avoid_tolls=bool(google_config.get("avoid_tolls", False)),
                avoid_highways=bool(google_config.get("avoid_highways", False)),
            )

            google_api_key = payload.get("google_maps_api_key") or os.getenv("GOOGLE_MAPS_API_KEY")

            distance_provider = build_distance_provider(
                mode=str(payload.get("distance_mode", "haversine")),
                google_api_key=google_api_key,
                google_options=google_options,
            )
            parking_distance_provider = build_distance_provider(
                mode=str(payload.get("parking_distance_mode", "haversine")),
                google_api_key=google_api_key,
                google_options=google_options,
            )

            nodes = build_nodes(
                svi_csv=svi_csv_path,
                state_name=str(state),
                svi_weight=svi_weight,
                sleep_s=sleep_s,
                use_centroid_if_missing=use_centroid_fallback,
                cache_csv=cache_csv_path,
            )

            route_df = find_route(
                nodes=nodes,
                num_places=num_places,
                start_county=start_county,
                improve_2opt=improve_2opt,
                distance_provider=distance_provider,
                parking_distance_provider=parking_distance_provider,
            )

            records = route_df.to_dict(orient="records")
            summary = {
                "num_stops": len(records),
                "total_km": float(route_df["total_km"].iloc[-1]) if not route_df.empty else 0.0,
                "total_svi": float(route_df["total_svi"].iloc[-1]) if not route_df.empty else 0.0,
                "total_weighted_svi": float(route_df["total_weighted_svi"].iloc[-1]) if not route_df.empty else 0.0,
            }

            return jsonify({"summary": summary, "stops": records})

        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svi_csv", required=True, help="County SVI CSV with STATE, COUNTY, FIPS, RPL_THEMES")
    ap.add_argument("--state", required=True, help='Full state name, e.g. "Massachusetts"')
    ap.add_argument("--num_places", type=int, required=True)
    ap.add_argument("--svi_weight", type=float, default=1.0, help="Multiplier applied to RPL_THEMES before selecting counties")
    ap.add_argument("--start_county", type=str, default=None)
    ap.add_argument("--no_2opt", action="store_true")
    ap.add_argument("--no_centroid_fallback", action="store_true")
    ap.add_argument("--sleep_s", type=float, default=1.0, help="Sleep between OSM queries to reduce rate limiting")
    ap.add_argument("--cache_csv", type=str, default=None, help="Optional: cache per-county Parking Lots here to avoid re-querying OSM")
    ap.add_argument("--out", type=str, default="route_output.csv")
    ap.add_argument("--html", type=str, default=None, help="Optional: write an interactive route map to this HTML file")
    args = ap.parse_args()

    nodes = build_nodes(
        svi_csv=args.svi_csv,
        state_name=args.state,
        svi_weight=args.svi_weight,
        sleep_s=args.sleep_s,
        use_centroid_if_missing=not args.no_centroid_fallback,
        cache_csv=args.cache_csv,
    )

    route_df = find_route(
        nodes=nodes,
        num_places=args.num_places,
        start_county=args.start_county,
        improve_2opt=not args.no_2opt,
    )
    route_df.to_csv(args.out, index=False)
    print(route_df.to_string(index=False))
    print(f"\nWrote: {args.out}")

    if args.html:
        export_route_map_html(route_df, args.html)
        print(f"Wrote map HTML: {args.html}")


if __name__ == "__main__":
    main()
