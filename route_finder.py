from __future__ import annotations

import argparse
import math
import os
import time
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

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

DEFAULT_SVI_CSV = "SVI_2022_US_county.csv"
DEFAULT_ZIP_COUNTS_CSV = "zip_facility_counts.csv"
DEFAULT_ZIP_CATALOG_CSV = "zip_facility_catalog.csv"
DEFAULT_HOSPITALS_CSV = "Hospitals_RAPT_6238935059120575774.csv"
DEFAULT_NURSING_HOMES_CSV = "Nursing_Homes_RAPT_4878197144364307278.csv"
DEFAULT_PUBLIC_HEALTH_CSV = "Public_Health_Departments_HIFLD_-4601558331780057158.csv"
DEFAULT_PHARMACIES_CSV = "RxOpen_041323_Pharmacies_-1189248154250082350.csv"
DEFAULT_OSM_BUFFER_KM = 5.0
FACILITY_COLUMNS = [
    "Hospitals",
    "Nursing Homes",
    "Public Health Departments",
    "Pharmacies",
]

STATE_NAME_TO_ABBR = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
STATE_ABBR_TO_NAME = {abbr: name.title() for name, abbr in STATE_NAME_TO_ABBR.items()}
LEADING_ZERO_STATE_ABBRS = {"CT", "DC", "DE", "MA", "ME", "NH", "NJ", "PR", "RI", "VT"}


@dataclass(frozen=True)
class OSMOptions:
    network_type: str = "drive"
    buffer_km: float = DEFAULT_OSM_BUFFER_KM


@dataclass(frozen=True)
class ZipNode:
    state: str
    state_abbr: str
    county: str
    zip_code: str
    svi: float
    weighted_svi: float
    hospitals: int
    nursing_homes: int
    public_health_departments: int
    pharmacies: int
    resource_sites: int
    representative_pt: LatLon
    parking_pts: List[LatLon]
    parking_source: str = "unknown"


def normalize_zip_code(value) -> Optional[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None

    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None

    # Pandas often round-trips ZIP codes like 02125 as the float string "2125.0".
    # Strip the synthetic decimal suffix before extracting digits so we recover 02125.
    if text.endswith(".0"):
        text = text[:-2]

    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    return digits[:5].zfill(5)


def normalize_zip_code_for_state(value, state_value: Optional[str]) -> Optional[str]:
    normalized = normalize_zip_code(value)
    if normalized is None:
        return None

    if not state_value:
        return normalized

    _, state_abbr = canonical_state(str(state_value))
    raw = str(value).strip()
    if raw.endswith(".0"):
        raw = raw[:-2]
    digits = "".join(ch for ch in raw if ch.isdigit())

    # Some source files serialize leading-zero ZIPs like 02116 as 21160.
    if state_abbr in LEADING_ZERO_STATE_ABBRS and len(digits) == 5 and not digits.startswith("0") and digits.endswith("0"):
        corrected = digits[:-1].zfill(5)
        if corrected.startswith("0"):
            return corrected

    return normalized


def normalize_name(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def normalize_county_name(value: str) -> str:
    name = normalize_name(value)
    if name.endswith(" county"):
        name = name[: -len(" county")]
    return name


def same_point(a: Optional[LatLon], b: Optional[LatLon], tolerance_km: float = 0.02) -> bool:
    if a is None or b is None:
        return False
    return haversine_km(a, b) <= tolerance_km


def infer_parking_source(
    pts: Sequence[LatLon],
    representative_pt: Optional[LatLon] = None,
    centroid_pt: Optional[LatLon] = None,
    source_hint: Optional[str] = None,
) -> str:
    normalized_hint = (source_hint or "").strip().lower()
    valid_sources = {"osm", "representative_fallback", "centroid_fallback"}
    if normalized_hint in valid_sources:
        return normalized_hint

    if not pts:
        return "unknown"

    if len(pts) == 1:
        if same_point(pts[0], centroid_pt):
            return "centroid_fallback"
        if same_point(pts[0], representative_pt):
            return "representative_fallback"

    return "osm"


def canonical_state(state_name: str) -> Tuple[str, str]:
    raw = str(state_name).strip()
    upper = raw.upper()
    if upper in STATE_ABBR_TO_NAME:
        return STATE_ABBR_TO_NAME[upper], upper

    norm = normalize_name(raw)
    abbr = STATE_NAME_TO_ABBR.get(norm)
    if abbr:
        return STATE_ABBR_TO_NAME[abbr], abbr

    return raw, upper


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


def web_mercator_to_latlon(x_value, y_value) -> Optional[LatLon]:
    try:
        x = float(x_value)
        y = float(y_value)
    except Exception:
        return None

    lon = (x / 20037508.34) * 180.0
    lat = (y / 20037508.34) * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return (lat, lon)


class DistanceProvider:
    def prepare(self, points: Sequence[LatLon], log_progress: bool = False) -> None:
        return None

    def distance_km(self, a: LatLon, b: LatLon) -> float:
        raise NotImplementedError

    def distances_km(self, origin: LatLon, destinations: Sequence[LatLon]) -> List[float]:
        return [self.distance_km(origin, dest) for dest in destinations]


class HaversineDistanceProvider(DistanceProvider):
    def distance_km(self, a: LatLon, b: LatLon) -> float:
        return haversine_km(a, b)


class OSMRouteDistanceProvider(DistanceProvider):
    # Uses an OpenStreetMap road network and falls back to haversine if routing fails.
    def __init__(self, options: Optional[OSMOptions] = None, log_fn=None):
        self.options = options or OSMOptions()
        self.log_fn = log_fn or (lambda msg: print(msg, flush=True))
        self.graph = None
        self.graph_bbox: Optional[Tuple[float, float, float, float]] = None
        self.node_cache: Dict[Tuple[float, float], int] = {}
        self.distance_cache: Dict[Tuple[float, float, float, float, str], float] = {}
        self.disabled = False

    def prepare(self, points: Sequence[LatLon], log_progress: bool = False) -> None:
        if self.disabled or not points:
            return

        if ox is None or nx is None:
            self._disable("OSM distance provider unavailable. Falling back to haversine.")
            return

        try:
            self._ensure_graph(list(points), log_progress=log_progress)
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

    def distances_km(self, origin: LatLon, destinations: Sequence[LatLon]) -> List[float]:
        if not destinations:
            return []
        self.prepare([origin] + list(destinations))
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

    def _expanded_bbox(self, points: Sequence[LatLon]) -> Tuple[float, float, float, float]:
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

        # OSMnx 2.x expects bbox order: (left, bottom, right, top) i.e.
        # (west, south, east, north). Returning any other order can explode
        # the effective query area and trigger Overpass OOM/timeouts.
        return (west, south, east, north)

    @staticmethod
    def _bbox_contains(bbox: Tuple[float, float, float, float], points: Sequence[LatLon]) -> bool:
        west, south, east, north = bbox
        return all(south <= lat <= north and west <= lon <= east for lat, lon in points)

    @staticmethod
    def _union_bboxes(
        a: Tuple[float, float, float, float],
        b: Tuple[float, float, float, float],
    ) -> Tuple[float, float, float, float]:
        return (
            min(a[0], b[0]),
            min(a[1], b[1]),
            max(a[2], b[2]),
            max(a[3], b[3]),
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


def geocode_place_geometry(place_name: str):
    if ox is None:
        return None
    try:
        gdf = ox.geocode_to_gdf(place_name)
        if gdf.empty:
            return None
        return gdf.iloc[0].geometry
    except Exception:
        return None


def geometry_centroid(geometry) -> Optional[LatLon]:
    if geometry is None:
        return None
    try:
        pt = geometry.centroid
        return (float(pt.y), float(pt.x))
    except Exception:
        return None


def geometry_for_zip(zip_code: str, state_name: str):
    return geocode_place_geometry(f"{zip_code}, {state_name}, USA")


def parking_points_from_features(features_df) -> List[LatLon]:
    if features_df is None or features_df.empty:
        return []

    parks = features_df
    if "access" in parks.columns:
        parks = parks[~parks["access"].isin(["private", "no", "customers"])]
    if parks.empty:
        return []

    pts: List[LatLon] = []
    seen = set()
    for geom in parks.geometry:
        if geom.is_empty:
            continue
        pt = geom.centroid if geom.geom_type in ["Polygon", "MultiPolygon"] else geom
        latlon = (round(float(pt.y), 6), round(float(pt.x), 6))
        if latlon in seen:
            continue
        seen.add(latlon)
        pts.append((float(pt.y), float(pt.x)))
    return pts


def get_parking_lots_for_zip(
    zip_code: str,
    state_name: str,
    representative_pt: Optional[LatLon] = None,
    sleep_s: float = 1.0,
    nearby_search_m: float = 1200.0,
) -> List[LatLon]:
    if ox is None:
        return []

    geometry = geometry_for_zip(zip_code, state_name)
    pts: List[LatLon] = []

    if geometry is not None:
        try:
            polygon = geometry
            if polygon.geom_type == "Point":
                polygon = polygon.buffer(0.03)
            pts = parking_points_from_features(ox.features.features_from_polygon(polygon, {"amenity": "parking"}))
        except Exception:
            pts = []

    if not pts and representative_pt is not None:
        try:
            pts = parking_points_from_features(
                ox.features.features_from_point(representative_pt, {"amenity": "parking"}, dist=nearby_search_m)
            )
        except Exception:
            pts = []

    if sleep_s > 0:
        time.sleep(sleep_s)
    return pts


def representative_point(pts: Sequence[LatLon]) -> Optional[LatLon]:
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

    m = folium.Map(location=[mean_lat, mean_lon], zoom_start=11, tiles="OpenStreetMap")
    folium.PolyLine(locations=coords, weight=4, opacity=0.8).add_to(m)

    for i, row in route_df.iterrows():
        lat = float(row["parking_lat"])
        lon = float(row["parking_lon"])
        zip_code = str(row.get("zip_code", ""))
        county = str(row.get("county", ""))
        state = str(row.get("state", ""))
        svi = row.get("svi_overall", "")
        order = int(row.get("order", i + 1))

        popup = folium.Popup(
            f"<b>Stop {order}</b><br>ZIP {zip_code}<br>{county}, {state}<br>SVI: {svi}",
            max_width=320,
        )
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


def nearest_neighbor_route(points: Sequence[LatLon], start_idx: int, distance_provider: DistanceProvider) -> List[int]:
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


def route_length_km(points: Sequence[LatLon], route: Sequence[int], distance_provider: DistanceProvider) -> float:
    if len(route) <= 1:
        return 0.0
    return sum(distance_provider.distance_km(points[route[i]], points[route[i + 1]]) for i in range(len(route) - 1))


def two_opt(
    points: Sequence[LatLon],
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
    nodes: Sequence[ZipNode],
    route: Sequence[int],
    reps: Sequence[LatLon],
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


def squared_euclidean(a: LatLon, b: LatLon) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def kmeans_assignments(points: Sequence[LatLon], k: int, max_iters: int = 30) -> List[int]:
    if k <= 0:
        raise ValueError("k must be positive")
    if k >= len(points):
        return list(range(len(points)))

    centers = [points[0]]
    while len(centers) < k:
        next_point = max(points, key=lambda p: min(squared_euclidean(p, c) for c in centers))
        centers.append(next_point)

    assignments = [0] * len(points)
    for _ in range(max_iters):
        changed = False
        for i, point in enumerate(points):
            cluster = min(range(k), key=lambda idx: squared_euclidean(point, centers[idx]))
            if cluster != assignments[i]:
                assignments[i] = cluster
                changed = True

        new_centers: List[LatLon] = []
        for cluster_idx in range(k):
            cluster_points = [p for p, a in zip(points, assignments) if a == cluster_idx]
            if not cluster_points:
                new_centers.append(centers[cluster_idx])
                continue
            mean_lat = sum(p[0] for p in cluster_points) / len(cluster_points)
            mean_lon = sum(p[1] for p in cluster_points) / len(cluster_points)
            new_centers.append((mean_lat, mean_lon))

        centers = new_centers
        if not changed:
            break

    return assignments


def parse_optional_float(value) -> Optional[float]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def parse_optional_int(value) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def scarcity_rank_score(values: pd.Series) -> pd.Series:
    # Lower facility counts imply higher scarcity. The score is relative within the county.
    numeric = values.fillna(0).astype(float)
    if len(numeric) <= 1 or numeric.nunique(dropna=False) <= 1:
        return pd.Series([0.5] * len(numeric), index=numeric.index, dtype=float)

    ranks = numeric.rank(method="average", ascending=True)
    return 1.0 - ((ranks - 1.0) / (len(numeric) - 1.0))


def apply_dynamic_zip_svi(df: pd.DataFrame) -> pd.DataFrame:
    scored = df.copy()
    scarcity_columns: List[str] = []
    for column in FACILITY_COLUMNS:
        scored[column] = scored[column].map(parse_optional_int)
        scarcity_column = f"_{column}_scarcity"
        scored[scarcity_column] = scarcity_rank_score(scored[column])
        scarcity_columns.append(scarcity_column)

    scored["resource_sites"] = scored[FACILITY_COLUMNS].sum(axis=1)
    scored["zip_svi"] = scored[scarcity_columns].mean(axis=1)
    return scored


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


def build_zip_catalog(
    zip_counts_csv: str,
    output_csv: str,
    hospitals_csv: str = DEFAULT_HOSPITALS_CSV,
    nursing_homes_csv: str = DEFAULT_NURSING_HOMES_CSV,
    public_health_csv: str = DEFAULT_PUBLIC_HEALTH_CSV,
    pharmacies_csv: str = DEFAULT_PHARMACIES_CSV,
    log_fn=None,
) -> pd.DataFrame:
    log_fn = log_fn or (lambda msg: None)
    source_specs = [
        {
            "path": hospitals_csv,
            "facility_col": "Hospitals",
            "zip_col": "ZIP",
            "state_col": "STATE",
            "county_col": "COUNTY",
            "lat_col": "LATITUDE",
            "lon_col": "LONGITUDE",
        },
        {
            "path": nursing_homes_csv,
            "facility_col": "Nursing Homes",
            "zip_col": "ZIP",
            "state_col": "STATE",
            "county_col": "COUNTY",
            "lat_col": "LATITUDE",
            "lon_col": "LONGITUDE",
        },
        {
            "path": public_health_csv,
            "facility_col": "Public Health Departments",
            "zip_col": "ZIP",
            "state_col": "STATE",
            "county_col": "COUNTY",
            "x_col": "x2",
            "y_col": "y2",
        },
        {
            "path": pharmacies_csv,
            "facility_col": "Pharmacies",
            "zip_col": "Zip",
            "state_col": "State",
            "x_col": "x",
            "y_col": "y",
        },
    ]

    available_specs = [spec for spec in source_specs if os.path.exists(spec["path"])]
    if not available_specs and not os.path.exists(zip_counts_csv):
        raise ValueError("Cannot build ZIP catalog: no facility source CSVs or zip_counts_csv were found.")

    metadata: Dict[str, Dict[str, object]] = {}
    for spec in available_specs:
        usecols = [spec["zip_col"], spec["state_col"]]
        if "county_col" in spec:
            usecols.append(spec["county_col"])
        if "lat_col" in spec:
            usecols.extend([spec["lat_col"], spec["lon_col"]])
        else:
            usecols.extend([spec["x_col"], spec["y_col"]])

        df = pd.read_csv(spec["path"], usecols=usecols, dtype=str)
        for _, row in df.iterrows():
            raw_state = str(row.get(spec["state_col"], "") or "").strip()
            zip_code = normalize_zip_code_for_state(row.get(spec["zip_col"]), raw_state)
            if not zip_code:
                continue

            bucket = metadata.setdefault(
                zip_code,
                {
                    "state_votes": Counter(),
                    "county_votes": Counter(),
                    "coords": [],
                    "Hospitals": 0,
                    "Nursing Homes": 0,
                    "Public Health Departments": 0,
                    "Pharmacies": 0,
                },
            )
            bucket[spec["facility_col"]] += 1

            if raw_state and raw_state.lower() != "nan":
                bucket["state_votes"].update([canonical_state(raw_state)[1]])

            if "county_col" in spec:
                county_value = str(row.get(spec["county_col"], "") or "").strip()
                if county_value and county_value.lower() != "nan":
                    bucket["county_votes"].update([county_value.upper()])

            coord = None
            if "lat_col" in spec:
                lat = parse_optional_float(row.get(spec["lat_col"]))
                lon = parse_optional_float(row.get(spec["lon_col"]))
                if lat is not None and lon is not None:
                    coord = (lat, lon)
            else:
                coord = web_mercator_to_latlon(row.get(spec["x_col"]), row.get(spec["y_col"]))
            if coord is not None:
                bucket["coords"].append(coord)

    supplemental_counties: Dict[str, str] = {}
    if os.path.exists(zip_counts_csv):
        counts_df = pd.read_csv(zip_counts_csv, dtype=str)
        if {"ZIP Code", "County"}.issubset(set(counts_df.columns)):
            counts_df["ZIP Code"] = counts_df["ZIP Code"].map(normalize_zip_code)
            counts_df = counts_df[counts_df["ZIP Code"].notna()].copy()
            for _, row in counts_df.iterrows():
                zip_code = row["ZIP Code"]
                county_name = str(row.get("County") or "").strip()
                if county_name:
                    supplemental_counties[zip_code] = county_name.upper()

    rows = []
    for zip_code in sorted(metadata):
        bucket = metadata[zip_code]
        state_votes: Counter = bucket["state_votes"]  # type: ignore[assignment]
        county_votes: Counter = bucket["county_votes"]  # type: ignore[assignment]
        coords: List[LatLon] = bucket["coords"]  # type: ignore[assignment]

        state_abbr = state_votes.most_common(1)[0][0] if state_votes else ""
        county_name = county_votes.most_common(1)[0][0] if county_votes else ""
        if not county_name:
            county_name = supplemental_counties.get(zip_code, "")

        rep_lat = None
        rep_lon = None
        if coords:
            rep_lat = sum(p[0] for p in coords) / len(coords)
            rep_lon = sum(p[1] for p in coords) / len(coords)

        rows.append(
            {
                "ZIP Code": zip_code,
                "State": state_abbr,
                "County": county_name,
                "Hospitals": int(bucket["Hospitals"]),
                "Nursing Homes": int(bucket["Nursing Homes"]),
                "Public Health Departments": int(bucket["Public Health Departments"]),
                "Pharmacies": int(bucket["Pharmacies"]),
                "Representative Lat": rep_lat,
                "Representative Lon": rep_lon,
            }
        )

    catalog_df = pd.DataFrame(rows)
    catalog_df.to_csv(output_csv, index=False)
    log_fn(f"[zip_catalog] Wrote {len(catalog_df)} ZIP rows to {output_csv}.")
    return catalog_df


def ensure_zip_catalog(
    zip_catalog_csv: str,
    zip_counts_csv: str = DEFAULT_ZIP_COUNTS_CSV,
    log_fn=None,
) -> str:
    log_fn = log_fn or (lambda msg: None)
    if os.path.exists(zip_catalog_csv):
        return zip_catalog_csv

    counts_path = _resolve_project_path(zip_counts_csv) or zip_counts_csv
    source_paths = [
        _resolve_project_path(DEFAULT_HOSPITALS_CSV) or DEFAULT_HOSPITALS_CSV,
        _resolve_project_path(DEFAULT_NURSING_HOMES_CSV) or DEFAULT_NURSING_HOMES_CSV,
        _resolve_project_path(DEFAULT_PUBLIC_HEALTH_CSV) or DEFAULT_PUBLIC_HEALTH_CSV,
        _resolve_project_path(DEFAULT_PHARMACIES_CSV) or DEFAULT_PHARMACIES_CSV,
    ]
    if (not counts_path or not os.path.exists(counts_path)) and not any(os.path.exists(path) for path in source_paths):
        raise ValueError(f"ZIP counts CSV not found: {zip_counts_csv}")

    build_zip_catalog(
        zip_counts_csv=counts_path or zip_counts_csv,
        output_csv=zip_catalog_csv,
        hospitals_csv=source_paths[0],
        nursing_homes_csv=source_paths[1],
        public_health_csv=source_paths[2],
        pharmacies_csv=source_paths[3],
        log_fn=log_fn,
    )
    return zip_catalog_csv


def build_zip_nodes(
    svi_csv: Optional[str],
    zip_catalog_csv: str,
    state_name: str,
    county_name: str,
    svi_weight: float,
    sleep_s: float,
    use_centroid_if_missing: bool,
    cache_csv: Optional[str],
    log_progress: bool = False,
) -> List[ZipNode]:
    del svi_csv

    state_full, state_abbr = canonical_state(state_name)
    county_full = str(county_name).strip()
    county_norm = normalize_county_name(county_full)

    catalog_path = ensure_zip_catalog(zip_catalog_csv, log_fn=(lambda msg: print(msg, flush=True)) if log_progress else None)
    df = pd.read_csv(catalog_path, dtype=str)

    required = {
        "ZIP Code",
        "State",
        "County",
        "Hospitals",
        "Nursing Homes",
        "Public Health Departments",
        "Pharmacies",
        "Representative Lat",
        "Representative Lon",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"ZIP catalog CSV missing required columns: {sorted(missing)}")

    df["ZIP Code"] = df["ZIP Code"].map(normalize_zip_code)
    mask = (
        df["State"].fillna("").astype(str).str.upper() == state_abbr
    ) & (
        df["County"].fillna("").astype(str).map(normalize_county_name) == county_norm
    )
    df = df[mask].copy()
    if df.empty:
        raise ValueError(f"No ZIP catalog rows found for county='{county_full}', state='{state_full}'.")

    df = apply_dynamic_zip_svi(df)

    if log_progress:
        print(f"[build_zip_nodes] Loaded {len(df)} ZIP rows for {county_full}, {state_full}.", flush=True)

    parking_cache: Dict[str, Tuple[str, str]] = {}
    if cache_csv and os.path.exists(cache_csv):
        cached = pd.read_csv(cache_csv, dtype=str)
        if {"STATE", "COUNTY", "ZIP", "Parking Lots"}.issubset(set(cached.columns)):
            cache_mask = (
                cached["STATE"].fillna("").astype(str).map(normalize_name) == normalize_name(state_abbr)
            ) & (
                cached["COUNTY"].fillna("").astype(str).map(normalize_county_name) == county_norm
            )
            for _, cache_row in cached[cache_mask].iterrows():
                zip_code = normalize_zip_code(cache_row.get("ZIP"))
                if zip_code:
                    parking_cache[zip_code] = (
                        str(cache_row.get("Parking Lots") or "").strip(),
                        str(cache_row.get("Parking Source") or "").strip(),
                    )

    nodes: List[ZipNode] = []
    cache_rows: List[dict] = []
    total = len(df)

    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        zip_code = row["ZIP Code"]
        rep_lat = parse_optional_float(row["Representative Lat"])
        rep_lon = parse_optional_float(row["Representative Lon"])
        representative = (rep_lat, rep_lon) if rep_lat is not None and rep_lon is not None else None

        if log_progress:
            print(f"[build_zip_nodes] {idx}/{total} ZIP {zip_code}", flush=True)

        centroid_fallback_pt: Optional[LatLon] = None
        parking_source_hint = ""
        cached_entry = parking_cache.get(zip_code)
        if cached_entry is not None:
            cell, parking_source_hint = cached_entry
        else:
            cell = str(row.get("Parking Lots") or "").strip()
            parking_source_hint = str(row.get("Parking Source") or "").strip()

        pts = parse_latlon_list(cell)
        parking_source = infer_parking_source(pts, representative_pt=representative, source_hint=parking_source_hint)
        if not pts:
            if log_progress:
                print("[build_zip_nodes]   querying OSM parking lots...", flush=True)
            pts = get_parking_lots_for_zip(zip_code, state_full, representative_pt=representative, sleep_s=sleep_s)
            parking_source = "osm" if pts else parking_source

        if not pts and representative is not None:
            if log_progress:
                print("[build_zip_nodes]   using representative-point fallback", flush=True)
            pts = [representative]
            parking_source = "representative_fallback"

        if not pts and use_centroid_if_missing:
            geometry = geometry_for_zip(zip_code, state_full)
            centroid_fallback_pt = geometry_centroid(geometry)
            if centroid_fallback_pt is not None:
                pts = [centroid_fallback_pt]
                if representative is None:
                    representative = centroid_fallback_pt
                if log_progress:
                    print("[build_zip_nodes]   using ZIP centroid fallback", flush=True)
                parking_source = "centroid_fallback"

        if representative is None:
            representative = representative_point(pts)
        if representative is None or not pts:
            continue

        parking_source = infer_parking_source(
            pts,
            representative_pt=representative,
            centroid_pt=centroid_fallback_pt,
            source_hint=parking_source,
        )

        parking_string = "; ".join([f"{p[0]:.6f},{p[1]:.6f}" for p in pts])
        cache_rows.append(
            {
                "STATE": state_abbr,
                "COUNTY": county_full,
                "ZIP": zip_code,
                "Parking Lots": parking_string,
                "Parking Source": parking_source,
            }
        )

        hospitals = int(row["Hospitals"])
        nursing_homes = int(row["Nursing Homes"])
        public_health_departments = int(row["Public Health Departments"])
        pharmacies = int(row["Pharmacies"])
        resource_sites = int(row["resource_sites"])
        zip_svi = float(row["zip_svi"])

        nodes.append(
            ZipNode(
                state=state_full,
                state_abbr=state_abbr,
                county=county_full,
                zip_code=zip_code,
                svi=zip_svi,
                weighted_svi=svi_weight * zip_svi,
                hospitals=hospitals,
                nursing_homes=nursing_homes,
                public_health_departments=public_health_departments,
                pharmacies=pharmacies,
                resource_sites=resource_sites,
                representative_pt=representative,
                parking_pts=pts,
                parking_source=parking_source,
            )
        )

    if cache_csv:
        pd.DataFrame(cache_rows).to_csv(cache_csv, index=False)

    if log_progress:
        print(f"[build_zip_nodes] Built {len(nodes)} ZIP nodes.", flush=True)

    return nodes


def selection_key(node: ZipNode) -> Tuple[float, str]:
    # Higher vulnerability wins. ZIP code breaks ties deterministically.
    return (-node.weighted_svi, node.zip_code)


def select_zip_nodes(
    nodes: Sequence[ZipNode],
    num_places: int,
    use_clustering: bool,
    log_progress: bool = False,
) -> List[ZipNode]:
    if not use_clustering or len(nodes) <= num_places:
        return sorted(nodes, key=selection_key)[:num_places]

    if log_progress:
        print("[find_route] Clustering ZIP candidates before selection.", flush=True)

    assignments = kmeans_assignments([node.representative_pt for node in nodes], num_places)
    cluster_map: Dict[int, List[ZipNode]] = {cluster_id: [] for cluster_id in range(num_places)}
    for node, cluster_id in zip(nodes, assignments):
        cluster_map[cluster_id].append(node)

    selected = [min(cluster_nodes, key=selection_key) for cluster_nodes in cluster_map.values() if cluster_nodes]
    selected_by_zip = {node.zip_code for node in selected}
    if len(selected) < num_places:
        remainder = [node for node in sorted(nodes, key=selection_key) if node.zip_code not in selected_by_zip]
        selected.extend(remainder[: num_places - len(selected)])

    return sorted(selected, key=selection_key)[:num_places]


def find_route(
    nodes: List[ZipNode],
    num_places: int,
    improve_2opt: bool,
    use_clustering: bool,
    distance_provider: Optional[DistanceProvider] = None,
    parking_distance_provider: Optional[DistanceProvider] = None,
    log_progress: bool = False,
) -> pd.DataFrame:
    if num_places <= 0:
        raise ValueError("num_places must be >= 1")

    distance_provider = distance_provider or HaversineDistanceProvider()
    parking_distance_provider = parking_distance_provider or HaversineDistanceProvider()

    usable = [n for n in nodes if n.parking_pts and n.representative_pt is not None]
    if len(usable) < num_places:
        raise ValueError(f"Only {len(usable)} ZIP nodes have usable coordinates, but num_places={num_places}.")

    if log_progress:
        print(f"[find_route] Selecting {num_places} ZIP stops inside {usable[0].county}, {usable[0].state}.", flush=True)

    selected = select_zip_nodes(usable, num_places=num_places, use_clustering=use_clustering, log_progress=log_progress)

    if len(selected) < num_places:
        raise ValueError(f"Only {len(selected)} ZIP nodes were selected, but num_places={num_places}.")

    reps = [node.representative_pt for node in selected]
    parking_points: List[LatLon] = []
    for node in selected:
        parking_points.extend(node.parking_pts)

    distance_provider.prepare(reps, log_progress=log_progress)
    parking_distance_provider.prepare(reps + parking_points, log_progress=log_progress)

    if log_progress:
        print("[find_route] Building route with nearest neighbor heuristic.", flush=True)
    route = nearest_neighbor_route(reps, 0, distance_provider)

    if improve_2opt and len(route) >= 4:
        if log_progress:
            print("[find_route] Improving route with 2-opt.", flush=True)
        route = two_opt(reps, route, distance_provider)

    chosen_pts = choose_parking_for_route(selected, route, reps, parking_distance_provider)

    rows = []
    for order, (idx, chosen) in enumerate(zip(route, chosen_pts), start=1):
        node = selected[idx]
        rows.append(
            {
                "order": order,
                "zip_code": node.zip_code,
                "county": node.county,
                "state": node.state,
                "state_abbr": node.state_abbr,
                "svi_overall": node.svi,
                "weighted_svi": node.weighted_svi,
                "hospitals": node.hospitals,
                "nursing_homes": node.nursing_homes,
                "public_health_departments": node.public_health_departments,
                "pharmacies": node.pharmacies,
                "resource_sites": node.resource_sites,
                "parking_source": node.parking_source,
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


def build_distance_provider(mode: str, osm_options: Optional[OSMOptions] = None, log_fn=None) -> DistanceProvider:
    normalized = (mode or "osm").strip().lower()
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
        svi_csv = payload.get("svi_csv") or DEFAULT_SVI_CSV
        zip_catalog_csv = payload.get("zip_catalog_csv") or DEFAULT_ZIP_CATALOG_CSV
        zip_counts_csv = payload.get("zip_counts_csv") or DEFAULT_ZIP_COUNTS_CSV
        state = payload.get("state")
        county = payload.get("county")
        num_places = payload.get("num_places")
        if not state or not county or num_places is None:
            raise ValueError("state, county, and num_places are required.")

        svi_csv_path = _resolve_project_path(str(svi_csv)) if svi_csv else None
        zip_catalog_path = _resolve_project_path(str(zip_catalog_csv))
        zip_counts_path = _resolve_project_path(str(zip_counts_csv))
        cache_csv_path = _resolve_project_path(payload.get("cache_csv"))

        if not zip_catalog_path:
            raise ValueError("zip_catalog_csv must be within the project directory.")
        if zip_counts_path and not os.path.exists(zip_counts_path) and not os.path.exists(zip_catalog_path):
            raise ValueError(f"zip_counts_csv not found: {zip_counts_csv}")

        num_places = int(num_places)
        svi_weight = float(payload.get("svi_weight", 1.0))
        improve_2opt = _coerce_bool(payload.get("improve_2opt"), True)
        use_clustering = _coerce_bool(payload.get("use_clustering"), False)
        use_centroid_fallback = _coerce_bool(payload.get("use_centroid_fallback"), True)
        sleep_s = float(payload.get("sleep_s", 0.0))
        log_progress = _coerce_bool(payload.get("log_progress"), False)

        osm_payload = payload.get("osm", {}) or {}
        osm_options = OSMOptions(
            network_type=str(osm_payload.get("network_type", "drive")),
            buffer_km=float(osm_payload.get("buffer_km", DEFAULT_OSM_BUFFER_KM)),
        )
        log_fn = _log_progress(log_progress)

        if not os.path.exists(zip_catalog_path):
            ensure_zip_catalog(zip_catalog_path, zip_counts_csv=zip_counts_path or DEFAULT_ZIP_COUNTS_CSV, log_fn=log_fn)

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

        nodes = build_zip_nodes(
            svi_csv=svi_csv_path,
            zip_catalog_csv=zip_catalog_path,
            state_name=str(state),
            county_name=str(county),
            svi_weight=svi_weight,
            sleep_s=sleep_s,
            use_centroid_if_missing=use_centroid_fallback,
            cache_csv=cache_csv_path,
            log_progress=log_progress,
        )
        route_df = find_route(
            nodes=nodes,
            num_places=num_places,
            improve_2opt=improve_2opt,
            use_clustering=use_clustering,
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
            filename = f"zip_route_{summary['num_stops']}_stops.csv"
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
    ap.add_argument("--svi_csv", default=DEFAULT_SVI_CSV, help="Legacy county SVI CSV path. ZIP routing now derives stop SVI from facility scarcity.")
    ap.add_argument("--zip_catalog_csv", default=DEFAULT_ZIP_CATALOG_CSV, help="ZIP catalog CSV")
    ap.add_argument("--zip_counts_csv", default=DEFAULT_ZIP_COUNTS_CSV, help="ZIP counts CSV used to build the ZIP catalog if needed")
    ap.add_argument("--state", required=True, help='State name or abbreviation, e.g. "Massachusetts" or "MA"')
    ap.add_argument("--county", required=True, help='County name, e.g. "Suffolk"')
    ap.add_argument("--num_places", type=int, required=True)
    ap.add_argument(
        "--svi_weight",
        type=float,
        default=1.0,
        help="Multiplier applied to the derived ZIP vulnerability score before top-ZIP selection.",
    )
    ap.add_argument("--no_2opt", action="store_true")
    ap.add_argument("--use_clustering", action="store_true")
    ap.add_argument("--no_centroid_fallback", action="store_true")
    ap.add_argument("--sleep_s", type=float, default=1.0, help="Sleep between OSM queries to reduce rate limiting")
    ap.add_argument("--log_progress", action="store_true", help="Print progress while building ZIP nodes and routing")
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
    ap.add_argument("--cache_csv", type=str, default=None, help="Optional cache of per-ZIP parking lots")
    ap.add_argument("--out", type=str, default="route_output.csv")
    ap.add_argument("--html", type=str, default=None, help="Optional: write an interactive route map to this HTML file")
    args = ap.parse_args()

    if load_dotenv is not None:
        load_dotenv()

    def _log(msg: str) -> None:
        print(msg, flush=True)

    if not os.path.exists(args.zip_catalog_csv):
        ensure_zip_catalog(args.zip_catalog_csv, zip_counts_csv=args.zip_counts_csv, log_fn=_log if args.log_progress else None)

    osm_options = OSMOptions(network_type="drive", buffer_km=DEFAULT_OSM_BUFFER_KM)
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

    nodes = build_zip_nodes(
        svi_csv=args.svi_csv,
        zip_catalog_csv=args.zip_catalog_csv,
        state_name=args.state,
        county_name=args.county,
        svi_weight=args.svi_weight,
        sleep_s=args.sleep_s,
        use_centroid_if_missing=not args.no_centroid_fallback,
        cache_csv=args.cache_csv,
        log_progress=args.log_progress,
    )

    route_df = find_route(
        nodes=nodes,
        num_places=args.num_places,
        improve_2opt=not args.no_2opt,
        use_clustering=args.use_clustering,
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
