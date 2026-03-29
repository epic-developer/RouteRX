from __future__ import annotations

import argparse
import math
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

try:
    from flask import Flask, Response, jsonify, render_template, request
except Exception:
    Flask = None
    Response = None
    jsonify = None
    render_template = None
    request = None

try:
    import folium
except Exception:
    folium = None

try:
    import networkx as nx
except Exception:
    nx = None

try:
    import osmnx as ox
except Exception:
    ox = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

LatLon = Tuple[float, float]


@dataclass(frozen=True)
class OSMOptions:
    network_type: str = "drive"
    buffer_km: float = 25.0


@dataclass(frozen=True)
class CountyNode:
    state: str
    county: str
    fips: str
    svi: float
    weighted_svi: float
    parking_pts: List[LatLon]


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
    def prepare(self, points: List[LatLon], log_progress: bool = False) -> None:
        return None

    def distance_km(self, a: LatLon, b: LatLon) -> float:
        raise NotImplementedError

    def distances_km(self, origin: LatLon, destinations: List[LatLon]) -> List[float]:
        return [self.distance_km(origin, dest) for dest in destinations]


class HaversineDistanceProvider(DistanceProvider):
    def distance_km(self, a: LatLon, b: LatLon) -> float:
        return haversine_km(a, b)


class OSMRouteDistanceProvider(DistanceProvider):
    # Uses an OpenStreetMap road network from osmnx and falls back to haversine if routing fails.
    def __init__(self, options: Optional[OSMOptions] = None, log_fn=None):
        self.options = options or OSMOptions()
        self.log_fn = log_fn or (lambda msg: print(msg, flush=True))
        self.graph = None
        self.graph_bbox: Optional[Tuple[float, float, float, float]] = None
        self.node_cache: Dict[Tuple[float, float], int] = {}
        self.distance_cache: Dict[Tuple[float, float, float, float, str], float] = {}
        self.disabled = False

    def prepare(self, points: List[LatLon], log_progress: bool = False) -> None:
        if self.disabled or not points:
            return

        if ox is None or nx is None:
            self._disable("OSM distance provider unavailable. Falling back to haversine.")
            return

        try:
            self._ensure_graph(points, log_progress=log_progress)
        except Exception as exc:
            self._disable(f"OSM routing unavailable ({exc}). Falling back to haversine.")

    def distance_km(self, a: LatLon, b: LatLon) -> float:
        if self.disabled:
            return haversine_km(a, b)

        self.prepare([a, b])
        if self.disabled or self.graph is None or nx is None:
            return haversine_km(a, b)

        key = self._cache_key(a, b)
        if key in self.distance_cache:
            return self.distance_cache[key]

        try:
            orig = self._nearest_node(a)
            dest = self._nearest_node(b)
            length_m = nx.shortest_path_length(self.graph, orig, dest, weight="length")
            km = float(length_m) / 1000.0
        except Exception:
            km = haversine_km(a, b)

        self.distance_cache[key] = km
        return km

    def distances_km(self, origin: LatLon, destinations: List[LatLon]) -> List[float]:
        if not destinations:
            return []
        self.prepare([origin] + destinations)
        return [self.distance_km(origin, dest) for dest in destinations]

    def _ensure_graph(self, points: List[LatLon], log_progress: bool = False) -> None:
        bbox = self._expanded_bbox(points)
        if self.graph_bbox is not None and self._bbox_contains(self.graph_bbox, points):
            return

        if self.graph_bbox is not None:
            bbox = self._union_bboxes(self.graph_bbox, bbox)

        if log_progress:
            self.log_fn(
                "[osm] Downloading OpenStreetMap road network "
                f"(network_type={self.options.network_type}, buffer_km={self.options.buffer_km})."
            )

        graph = ox.graph_from_bbox(
            bbox=bbox,
            network_type=self.options.network_type,
            simplify=True,
            retain_all=False,
        )
        self.graph = graph
        self.graph_bbox = bbox
        self.node_cache.clear()
        self.distance_cache.clear()

    def _nearest_node(self, point: LatLon) -> int:
        if self.graph is None or ox is None:
            raise RuntimeError("OSM graph is not available.")

        key = (round(point[0], 6), round(point[1], 6))
        if key not in self.node_cache:
            self.node_cache[key] = int(ox.distance.nearest_nodes(self.graph, X=point[1], Y=point[0]))
        return self.node_cache[key]

    def _expanded_bbox(self, points: List[LatLon]) -> Tuple[float, float, float, float]:
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        min_lat = min(lats)
        max_lat = max(lats)
        min_lon = min(lons)
        max_lon = max(lons)

        buffer_lat = self.options.buffer_km / 111.0
        mean_lat = sum(lats) / len(lats)
        cos_lat = math.cos(math.radians(mean_lat))
        buffer_lon = self.options.buffer_km / max(1.0, 111.0 * max(abs(cos_lat), 0.2))

        north = max_lat + buffer_lat
        south = min_lat - buffer_lat
        east = max_lon + buffer_lon
        west = min_lon - buffer_lon
        return (north, south, east, west)

    @staticmethod
    def _bbox_contains(bbox: Tuple[float, float, float, float], points: List[LatLon]) -> bool:
        north, south, east, west = bbox
        return all(south <= lat <= north and west <= lon <= east for lat, lon in points)

    @staticmethod
    def _union_bboxes(
        a: Tuple[float, float, float, float],
        b: Tuple[float, float, float, float],
    ) -> Tuple[float, float, float, float]:
        return (
            max(a[0], b[0]),
            min(a[1], b[1]),
            max(a[2], b[2]),
            min(a[3], b[3]),
        )

    def _cache_key(self, a: LatLon, b: LatLon) -> Tuple[float, float, float, float, str]:
        return (
            round(a[0], 6),
            round(a[1], 6),
            round(b[0], 6),
            round(b[1], 6),
            self.options.network_type,
        )

    def _disable(self, message: str) -> None:
        if not self.disabled:
            self.disabled = True
            self.log_fn(message)


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


def get_parking_lots_for_county(county_name: str, state_name: str, sleep_s: float = 1.0) -> List[LatLon]:
    if ox is None:
        return []

    place_name = f"{county_name}, {state_name}, USA"
    try:
        gdf = ox.geocode_to_gdf(place_name)
        if gdf.empty:
            return []
        polygon = gdf.iloc[0].geometry

        parks = ox.features.features_from_polygon(polygon, {"amenity": "parking"})
        if parks.empty:
            return []

        if "access" in parks.columns:
            parks = parks[~parks["access"].isin(["private", "no", "customers"])]
        if parks.empty:
            return []

        pts: List[LatLon] = []
        for geom in parks.geometry:
            if geom.is_empty:
                continue
            pt = geom.centroid if geom.geom_type in ["Polygon", "MultiPolygon"] else geom
            pts.append((float(pt.y), float(pt.x)))

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


def _build_folium_map(route_df: pd.DataFrame) -> "folium.Map":
    if folium is None:
        raise RuntimeError("folium is not installed. Install with: pip install folium")
    if route_df.empty:
        raise ValueError("route_df is empty; nothing to map.")

    coords = list(zip(route_df["parking_lat"].astype(float), route_df["parking_lon"].astype(float)))
    mean_lat = sum(lat for lat, _ in coords) / len(coords)
    mean_lon = sum(lon for _, lon in coords) / len(coords)

    m = folium.Map(location=[mean_lat, mean_lon], zoom_start=8, tiles="OpenStreetMap")
    folium.PolyLine(locations=coords, weight=4, opacity=0.8).add_to(m)

    for i, row in route_df.iterrows():
        lat = float(row["parking_lat"])
        lon = float(row["parking_lon"])
        county = str(row.get("county", ""))
        svi = row.get("svi_overall", "")
        order = int(row.get("order", i + 1))

        popup = folium.Popup(f"<b>Stop {order}</b><br>{county}<br>SVI: {svi}", max_width=300)
        if i == 0:
            icon = folium.Icon(color="green", icon="play", prefix="fa")
        elif i == len(route_df) - 1:
            icon = folium.Icon(color="red", icon="stop", prefix="fa")
        else:
            icon = folium.Icon(color="blue", icon="circle", prefix="fa")

        folium.Marker([lat, lon], popup=popup, icon=icon).add_to(m)

    return m


def build_route_map_html(route_df: pd.DataFrame) -> str:
    return _build_folium_map(route_df).get_root().render()


def export_route_map_html(route_df: pd.DataFrame, html_path: str) -> None:
    _build_folium_map(route_df).save(html_path)


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


def two_opt(
    points: List[LatLon],
    route: List[int],
    distance_provider: DistanceProvider,
    max_iters: int = 500,
) -> List[int]:
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
                new = best[:i] + list(reversed(best[i : k + 1])) + best[k + 1 :]
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
        pts = nodes[idx].parking_pts
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


def build_nodes(
    svi_csv: str,
    state_name: str,
    svi_weight: float,
    sleep_s: float,
    use_centroid_if_missing: bool,
    cache_csv: Optional[str],
    log_progress: bool = False,
) -> List[CountyNode]:
    df = pd.read_csv(svi_csv, dtype=str)

    required = {"STATE", "COUNTY", "FIPS", "RPL_THEMES"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"SVI CSV missing required columns: {sorted(missing)}")

    df = df[df["STATE"].astype(str).str.strip() == state_name].copy()
    if df.empty:
        raise ValueError(f"No rows found for STATE='{state_name}'. Check spelling/case in the CSV.")

    if log_progress:
        print(f"[build_nodes] Loaded {len(df)} counties for state '{state_name}'.", flush=True)

    if "Parking Lots" not in df.columns:
        df["Parking Lots"] = ""

    parking_cache: Dict[str, str] = {}
    if cache_csv and os.path.exists(cache_csv):
        cached = pd.read_csv(cache_csv, dtype=str)
        if {"STATE", "COUNTY", "FIPS", "Parking Lots"}.issubset(set(cached.columns)):
            cached = cached[cached["STATE"].astype(str).str.strip() == state_name].copy()
            for _, r in cached.iterrows():
                key = str(r["FIPS"]).split(".")[0].zfill(5)
                parking_cache[key] = str(r.get("Parking Lots") or "").strip()

    nodes: List[CountyNode] = []
    parking_strings: List[str] = []
    total = len(df)

    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        state = str(row["STATE"]).strip()
        county = str(row["COUNTY"]).strip()
        fips = str(row["FIPS"]).split(".")[0].zfill(5)
        svi = float(row["RPL_THEMES"])
        weighted = svi_weight * svi

        if log_progress:
            print(f"[build_nodes] {idx}/{total} {county}", flush=True)

        cell = parking_cache.get(fips, str(row.get("Parking Lots") or "").strip())
        pts = parse_latlon_list(cell)

        if not pts:
            if log_progress:
                print("[build_nodes]   querying OSM parking lots...", flush=True)
            pts = get_parking_lots_for_county(county, state, sleep_s=sleep_s)

        if not pts and use_centroid_if_missing:
            fallback = centroid_fallback(county, state)
            if fallback is not None:
                pts = [fallback]
                if log_progress:
                    print("[build_nodes]   using centroid fallback", flush=True)

        parking_strings.append("; ".join([f"{p[0]:.6f},{p[1]:.6f}" for p in pts]))
        nodes.append(
            CountyNode(
                state=state,
                county=county,
                fips=fips,
                svi=svi,
                weighted_svi=weighted,
                parking_pts=pts,
            )
        )

    df["Parking Lots"] = parking_strings
    if cache_csv:
        df.to_csv(cache_csv, index=False)

    if log_progress:
        print("[build_nodes] Done building nodes.", flush=True)

    return nodes


def find_route(
    nodes: List[CountyNode],
    num_places: int,
    start_county: Optional[str],
    improve_2opt: bool,
    distance_provider: Optional[DistanceProvider] = None,
    parking_distance_provider: Optional[DistanceProvider] = None,
    log_progress: bool = False,
) -> pd.DataFrame:
    if num_places <= 0:
        raise ValueError("num_places must be >= 1")

    distance_provider = distance_provider or HaversineDistanceProvider()
    parking_distance_provider = parking_distance_provider or HaversineDistanceProvider()

    usable = [n for n in nodes if n.parking_pts]
    if len(usable) < num_places:
        raise ValueError(f"Only {len(usable)} counties have usable coordinates, but num_places={num_places}.")

    if log_progress:
        print(f"[find_route] Selecting top {num_places} counties by weighted SVI.", flush=True)
    usable_sorted = sorted(usable, key=lambda n: n.weighted_svi, reverse=True)
    selected = usable_sorted[:num_places]

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
    reps = [r for r in reps if r is not None]

    parking_points: List[LatLon] = []
    for node in selected:
        parking_points.extend(node.parking_pts)

    distance_provider.prepare(reps, log_progress=log_progress)
    parking_distance_provider.prepare(reps + parking_points, log_progress=log_progress)

    if start_county:
        start_idx = next(i for i, n in enumerate(selected) if n.county.lower() == start_county.strip().lower())
    else:
        start_idx = 0

    if log_progress:
        print("[find_route] Building route with nearest neighbor heuristic.", flush=True)
    route = nearest_neighbor_route(reps, start_idx, distance_provider)

    if improve_2opt and len(route) >= 4:
        if log_progress:
            print("[find_route] Improving route with 2-opt.", flush=True)
        route = two_opt(reps, route, distance_provider)

    chosen_pts = choose_parking_for_route(selected, route, reps, parking_distance_provider)

    if log_progress:
        print("[find_route] Route complete.", flush=True)

    rows = []
    for order, (idx, chosen) in enumerate(zip(route, chosen_pts), start=1):
        node = selected[idx]
        rows.append(
            {
                "order": order,
                "state": node.state,
                "county": node.county,
                "fips": node.fips,
                "svi_overall": node.svi,
                "weighted_svi": node.weighted_svi,
                "parking_lat": chosen[0],
                "parking_lon": chosen[1],
            }
        )

    out = pd.DataFrame(rows)
    out["leg_km_from_prev"] = 0.0
    for i in range(1, len(out)):
        prev = (float(out.loc[i - 1, "parking_lat"]), float(out.loc[i - 1, "parking_lon"]))
        cur = (float(out.loc[i, "parking_lat"]), float(out.loc[i, "parking_lon"]))
        out.loc[i, "leg_km_from_prev"] = distance_provider.distance_km(prev, cur)

    out["total_km"] = out["leg_km_from_prev"].cumsum()
    out["total_svi"] = out["svi_overall"].cumsum()
    out["total_weighted_svi"] = out["weighted_svi"].cumsum()
    return out


def build_distance_provider(
    mode: str,
    osm_options: Optional[OSMOptions] = None,
    log_fn=None,
) -> DistanceProvider:
    normalized = (mode or "haversine").strip().lower()
    if normalized == "google":
        normalized = "osm"
        if log_fn:
            log_fn("[distance] 'google' mode is deprecated. Using OpenStreetMap routing instead.")
    if normalized == "auto":
        normalized = "osm"
    if normalized == "osm":
        return OSMRouteDistanceProvider(options=osm_options, log_fn=log_fn)
    if normalized == "haversine":
        return HaversineDistanceProvider()
    raise ValueError(f"Unsupported distance_mode '{mode}'. Use 'haversine' or 'osm'.")


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
    if Flask is None or Response is None or jsonify is None or request is None:
        raise RuntimeError("Flask is not installed. Install with: pip install flask")


def _parse_cors_origins(raw: Optional[str]) -> List[str]:
    if not raw:
        return ["*"]
    origins = [o.strip() for o in raw.split(",")]
    return [o for o in origins if o]


def _route_summary(route_df: pd.DataFrame) -> dict:
    return {
        "num_stops": len(route_df),
        "total_km": float(route_df["total_km"].iloc[-1]) if not route_df.empty else 0.0,
        "total_svi": float(route_df["total_svi"].iloc[-1]) if not route_df.empty else 0.0,
        "total_weighted_svi": float(route_df["total_weighted_svi"].iloc[-1]) if not route_df.empty else 0.0,
    }


def _coerce_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def create_app() -> "Flask":
    _require_flask()
    if load_dotenv is not None:
        load_dotenv()

    app = Flask(
        __name__,
        static_folder=os.path.join(_project_root(), "static"),
        static_url_path="/static",
        template_folder=os.path.join(_project_root(), "templates"),
    )
    try:
        app.json.sort_keys = False
    except Exception:
        app.config["JSON_SORT_KEYS"] = False

    cors_origins = _parse_cors_origins(os.getenv("CORS_ALLOW_ORIGINS"))

    @app.after_request
    def _add_cors_headers(response):
        path = request.path if request else ""
        if path.startswith("/api/"):
            origin = request.headers.get("Origin") if request else None
            allow_origin = "*" if "*" in cors_origins else (origin if origin in cors_origins else None)
            if allow_origin:
                response.headers["Access-Control-Allow-Origin"] = allow_origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Max-Age"] = "600"
        return response

    def _log_progress(enabled: bool):
        return (lambda msg: print(msg, flush=True)) if enabled else (lambda msg: None)

    def _compute_route(payload: dict) -> Tuple[pd.DataFrame, dict]:
        svi_csv = payload.get("svi_csv") or "SVI_2022_US_county.csv"
        state = payload.get("state")
        num_places = payload.get("num_places")
        if not state or num_places is None:
            raise ValueError("state and num_places are required.")

        num_places = int(num_places)
        svi_weight = float(payload.get("svi_weight", 1.0))
        start_county = payload.get("start_county")
        improve_2opt = _coerce_bool(payload.get("improve_2opt"), True)
        use_centroid_fallback = _coerce_bool(payload.get("use_centroid_fallback"), True)
        sleep_s = float(payload.get("sleep_s", 0.0))
        log_progress = _coerce_bool(payload.get("log_progress"), False)

        svi_csv_path = _resolve_project_path(str(svi_csv))
        cache_csv_path = _resolve_project_path(payload.get("cache_csv"))
        if not svi_csv_path or not os.path.exists(svi_csv_path):
            raise ValueError(f"svi_csv not found: {svi_csv}")

        osm_payload = payload.get("osm", {}) or {}
        osm_options = OSMOptions(
            network_type=str(osm_payload.get("network_type", "drive")),
            buffer_km=float(osm_payload.get("buffer_km", 25.0)),
        )
        log_fn = _log_progress(log_progress)

        distance_provider = build_distance_provider(
            mode=str(payload.get("distance_mode", "osm")),
            osm_options=osm_options,
            log_fn=log_fn,
        )
        parking_distance_provider = build_distance_provider(
            mode=str(payload.get("parking_distance_mode", "haversine")),
            osm_options=osm_options,
            log_fn=log_fn,
        )

        nodes = build_nodes(
            svi_csv=svi_csv_path,
            state_name=str(state),
            svi_weight=svi_weight,
            sleep_s=sleep_s,
            use_centroid_if_missing=use_centroid_fallback,
            cache_csv=cache_csv_path,
            log_progress=log_progress,
        )

        route_df = find_route(
            nodes=nodes,
            num_places=num_places,
            start_county=start_county,
            improve_2opt=improve_2opt,
            distance_provider=distance_provider,
            parking_distance_provider=parking_distance_provider,
            log_progress=log_progress,
        )
        return route_df, _route_summary(route_df)

    @app.get("/")
    def index():
        if render_template is None:
            return jsonify({"status": "ok", "message": "RouteRX API"}), 200
        return render_template("index.html")

    @app.get("/about")
    def about():
        if render_template is None:
            return jsonify({"status": "ok", "message": "RouteRX API"}), 200
        return render_template("about.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/api/route")
    def api_route():
        payload = request.get_json(silent=True) or {}
        try:
            route_df, summary = _compute_route(payload)
            records = route_df.to_dict(orient="records")
            return jsonify({"summary": summary, "stops": records, "stats": summary, "route": records})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/route.csv")
    def api_route_csv():
        payload = request.get_json(silent=True) or {}
        try:
            route_df, summary = _compute_route(payload)
            csv_text = route_df.to_csv(index=False)
            filename = f"route_{summary['num_stops']}_stops.csv"
            response = Response(csv_text, mimetype="text/csv")
            response.headers["Content-Disposition"] = f"attachment; filename={filename}"
            return response
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/route/map")
    def api_route_map():
        payload = request.get_json(silent=True) or {}
        try:
            route_df, _ = _compute_route(payload)
            return Response(build_route_map_html(route_df), mimetype="text/html")
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/route/preview")
    def api_route_preview():
        payload = request.get_json(silent=True) or {}
        try:
            _, summary = _compute_route(payload)
            return jsonify({"summary": summary, "stats": summary})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--svi_csv", required=True, help="County SVI CSV with STATE, COUNTY, FIPS, RPL_THEMES")
    ap.add_argument("--state", required=True, help='Full state name, e.g. "Massachusetts"')
    ap.add_argument("--num_places", type=int, required=True)
    ap.add_argument("--svi_weight", type=float, default=1.0, help="Multiplier applied to RPL_THEMES before selecting counties")
    ap.add_argument("--start_county", type=str, default=None)
    ap.add_argument("--no_2opt", action="store_true")
    ap.add_argument("--no_centroid_fallback", action="store_true")
    ap.add_argument("--sleep_s", type=float, default=1.0, help="Sleep between OSM queries to reduce rate limiting")
    ap.add_argument("--log_progress", action="store_true", help="Print progress while building nodes and routing")
    ap.add_argument(
        "--distance_mode",
        type=str,
        default="osm",
        choices=["haversine", "osm", "auto", "google"],
        help="Distance source for routing. 'google' is accepted as a compatibility alias for OSM.",
    )
    ap.add_argument(
        "--parking_distance_mode",
        type=str,
        default="haversine",
        choices=["haversine", "osm", "auto", "google"],
        help="Distance source for parking selection. Haversine is the pragmatic default.",
    )
    ap.add_argument("--cache_csv", type=str, default=None, help="Optional cache of per-county Parking Lots")
    ap.add_argument("--out", type=str, default="route_output.csv")
    ap.add_argument("--html", type=str, default=None, help="Optional: write an interactive route map to this HTML file")
    args = ap.parse_args()

    if load_dotenv is not None:
        load_dotenv()

    def _log(msg: str) -> None:
        print(msg, flush=True)

    osm_options = OSMOptions(network_type="drive", buffer_km=25.0)
    distance_provider = build_distance_provider(args.distance_mode, osm_options=osm_options, log_fn=_log)
    parking_distance_provider = build_distance_provider(
        args.parking_distance_mode,
        osm_options=osm_options,
        log_fn=_log,
    )

    if args.log_progress:
        route_label = type(distance_provider).__name__.replace("DistanceProvider", "")
        parking_label = type(parking_distance_provider).__name__.replace("DistanceProvider", "")
        print(f"[distance] Route provider: {route_label}", flush=True)
        print(f"[parking_distance] Parking provider: {parking_label}", flush=True)

    nodes = build_nodes(
        svi_csv=args.svi_csv,
        state_name=args.state,
        svi_weight=args.svi_weight,
        sleep_s=args.sleep_s,
        use_centroid_if_missing=not args.no_centroid_fallback,
        cache_csv=args.cache_csv,
        log_progress=args.log_progress,
    )

    route_df = find_route(
        nodes=nodes,
        num_places=args.num_places,
        start_county=args.start_county,
        improve_2opt=not args.no_2opt,
        distance_provider=distance_provider,
        parking_distance_provider=parking_distance_provider,
        log_progress=args.log_progress,
    )

    route_df.to_csv(args.out, index=False)
    print(route_df.to_string(index=False))
    print(f"\nWrote: {args.out}")

    if args.html:
        export_route_map_html(route_df, args.html)
        print(f"Wrote map HTML: {args.html}")


if __name__ == "__main__":
    main()
