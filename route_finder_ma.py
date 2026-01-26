#!/usr/bin/env python3
"""
RouteRX Massachusetts v1 route finder.

Inputs:
- MA_with_parking_lots.csv with columns:
  STATE, COUNTY, FIPS, RPL_THEMES, Parking Lots

Parking Lots format:
  "lat,lon; lat,lon; ..."

Algorithm (v1, intentionally simple + defensible):
1) Score counties by official SVI (RPL_THEMES). Select the top `num_places` counties.
   - If `start_county` is provided, force-include it (drops the lowest-SVI county if needed).
2) Build a route order that is distance-efficient:
   - Construct distances between counties using a representative point per county
   - Use Nearest-Neighbor TSP heuristic + optional 2-opt improvement.
3) Choose a parking coordinate in each county:
   - Given prev/next counties in the route, choose the parking point that minimizes
     dist(prev, p) + dist(p, next) (endpoints minimize to their single neighbor).
   - If a county has no parking points, optionally fall back to county centroid
     via OSM geocoding (requires internet).

Outputs:
- Ordered list of stops: (county, fips, svi, lat, lon)

This is a v1 baseline to compare against Tapestry and iterate.
"""

from __future__ import annotations

import math
import argparse
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

import pandas as pd

try:
    import osmnx as ox
except Exception:
    ox = None


LatLon = Tuple[float, float]  # (lat, lon)

@dataclass(frozen=True)
class CountyNode:
    state: str
    county: str
    fips: str
    svi: float
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
            lat = float(lat_s.strip())
            lon = float(lon_s.strip())
            pts.append((lat, lon))
        except Exception:
            continue
    return pts


def haversine_km(a: LatLon, b: LatLon) -> float:
    # Great-circle distance using WGS84 sphere approx
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def centroid_fallback(county_name: str, state_name: str) -> Optional[LatLon]:
    """
    If a county has no parking lots in OSM (or filtered out), use county polygon centroid.
    Requires osmnx and internet access (Nominatim).
    """
    if ox is None:
        return None
    place = f"{county_name}, {state_name}, USA"
    try:
        gdf = ox.geocode_to_gdf(place)
        if gdf.empty:
            return None
        geom = gdf.iloc[0].geometry
        pt = geom.centroid
        return (float(pt.y), float(pt.x))  # lat, lon
    except Exception:
        return None


def representative_point(pts: List[LatLon]) -> Optional[LatLon]:
    """
    Pick a single representative point for distance calculations: the parking point
    closest to the mean (lat,lon). This approximates a 'center' without requiring
    the county polygon.
    """
    if not pts:
        return None
    mean_lat = sum(p[0] for p in pts) / len(pts)
    mean_lon = sum(p[1] for p in pts) / len(pts)
    return min(pts, key=lambda p: haversine_km(p, (mean_lat, mean_lon)))


def nearest_neighbor_route(points: List[LatLon], start_idx: int) -> List[int]:
    n = len(points)
    unvisited = set(range(n))
    route = [start_idx]
    unvisited.remove(start_idx)
    cur = start_idx
    while unvisited:
        nxt = min(unvisited, key=lambda j: haversine_km(points[cur], points[j]))
        route.append(nxt)
        unvisited.remove(nxt)
        cur = nxt
    return route


def route_length_km(points: List[LatLon], route: List[int]) -> float:
    if len(route) <= 1:
        return 0.0
    return sum(haversine_km(points[route[i]], points[route[i + 1]]) for i in range(len(route) - 1))


def two_opt(points: List[LatLon], route: List[int], max_iters: int = 500) -> List[int]:
    """
    Standard 2-opt improvement for an *open* path (not a cycle).
    """
    best = route[:]
    best_len = route_length_km(points, best)
    n = len(best)
    improved = True
    iters = 0
    while improved and iters < max_iters:
        improved = False
        iters += 1
        for i in range(1, n - 2):
            for k in range(i + 1, n - 1):
                new = best[:i] + list(reversed(best[i:k + 1])) + best[k + 1:]
                new_len = route_length_km(points, new)
                if new_len + 1e-9 < best_len:
                    best, best_len = new, new_len
                    improved = True
                    break
            if improved:
                break
    return best


def choose_parking_for_route(nodes: List[CountyNode], route: List[int], reps: List[LatLon]) -> List[LatLon]:
    """
    For each county in the ordered route, choose the parking lot that best fits
    its neighbors to reduce local detours.
    """
    chosen: List[LatLon] = []
    for pos, idx in enumerate(route):
        node = nodes[idx]
        pts = node.parking_pts
        if not pts:
            # Should not happen if fallback applied; still guard.
            chosen.append(reps[idx])
            continue

        if len(route) == 1:
            chosen.append(pts[0])
            continue

        if pos == 0:
            nxt_pt = reps[route[1]]
            best = min(pts, key=lambda p: haversine_km(p, nxt_pt))
        elif pos == len(route) - 1:
            prev_pt = reps[route[-2]]
            best = min(pts, key=lambda p: haversine_km(prev_pt, p))
        else:
            prev_pt = reps[route[pos - 1]]
            nxt_pt = reps[route[pos + 1]]
            best = min(pts, key=lambda p: haversine_km(prev_pt, p) + haversine_km(p, nxt_pt))
        chosen.append(best)
    return chosen


def build_nodes(csv_path: str, use_centroid_if_missing: bool = True) -> List[CountyNode]:
    df = pd.read_csv(csv_path)
    required = {"STATE", "COUNTY", "FIPS", "RPL_THEMES", "Parking Lots"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")

    nodes: List[CountyNode] = []
    for _, row in df.iterrows():
        state = str(row["STATE"]).strip()
        county = str(row["COUNTY"]).strip()
        fips = str(row["FIPS"]).split(".")[0].zfill(5)
        svi = float(row["RPL_THEMES"])
        pts = parse_latlon_list(row["Parking Lots"])

        if not pts and use_centroid_if_missing:
            fallback = centroid_fallback(county, state)
            if fallback is not None:
                pts = [fallback]

        nodes.append(CountyNode(state=state, county=county, fips=fips, svi=svi, parking_pts=pts))
    return nodes


def find_route(
    nodes: List[CountyNode],
    num_places: int,
    start_county: Optional[str] = None,
    improve_2opt: bool = True,
) -> pd.DataFrame:
    """
    Returns a DataFrame of ordered stops with chosen parking coordinate.
    """
    if num_places <= 0:
        raise ValueError("num_places must be >= 1")

    # Filter out counties with no coordinates at all (even after fallback)
    usable = [n for n in nodes if n.parking_pts]
    if len(usable) < num_places:
        raise ValueError(f"Only {len(usable)} counties have usable coordinates, but num_places={num_places}.")

    # Select top-k by SVI
    usable_sorted = sorted(usable, key=lambda n: n.svi, reverse=True)
    selected = usable_sorted[:num_places]

    # Force include start_county if provided
    if start_county:
        start_key = start_county.strip().lower()
        match = next((n for n in usable if n.county.lower() == start_key), None)
        if match is None:
            raise ValueError(f"start_county='{start_county}' not found among usable counties.")
        if match not in selected:
            selected = selected[:-1] + [match]  # drop lowest SVI in selected
        # Keep deterministic order by SVI for later indexing
        selected = sorted(selected, key=lambda n: n.svi, reverse=True)

    # Representative points for distance heuristics
    reps = [representative_point(n.parking_pts) for n in selected]
    assert all(r is not None for r in reps)
    reps = [r for r in reps]  # type: ignore

    # Choose start index
    if start_county:
        start_idx = next(i for i, n in enumerate(selected) if n.county.lower() == start_county.strip().lower())
    else:
        start_idx = 0  # highest SVI (because selected is SVI-sorted)

    route = nearest_neighbor_route(reps, start_idx)
    if improve_2opt and len(route) >= 4:
        route = two_opt(reps, route)

    # Choose parking points aligned to route
    chosen_pts = choose_parking_for_route(selected, route, reps)

    out_rows = []
    for order, (idx, chosen) in enumerate(zip(route, chosen_pts), start=1):
        n = selected[idx]
        out_rows.append({
            "order": order,
            "state": n.state,
            "county": n.county,
            "fips": n.fips,
            "svi_overall": n.svi,
            "parking_lat": chosen[0],
            "parking_lon": chosen[1],
        })

    out = pd.DataFrame(out_rows)
    out["leg_km_from_prev"] = 0.0
    for i in range(1, len(out)):
        prev = (out.loc[i-1, "parking_lat"], out.loc[i-1, "parking_lon"])
        cur = (out.loc[i, "parking_lat"], out.loc[i, "parking_lon"])
        out.loc[i, "leg_km_from_prev"] = haversine_km(prev, cur)
    out["total_km"] = out["leg_km_from_prev"].cumsum()
    out["total_svi"] = out["svi_overall"].cumsum()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="Path to MA_with_parking_lots.csv")
    ap.add_argument("--num_places", type=int, required=True)
    ap.add_argument("--start_county", type=str, default=None, help="Optional: e.g. 'Hampden County'")
    ap.add_argument("--no_2opt", action="store_true", help="Disable 2-opt improvement")
    ap.add_argument("--no_centroid_fallback", action="store_true", help="Disable centroid fallback for missing parking lots")
    ap.add_argument("--out", type=str, default="route_output.csv")
    args = ap.parse_args()

    nodes = build_nodes(args.csv, use_centroid_if_missing=not args.no_centroid_fallback)
    route_df = find_route(
        nodes,
        num_places=args.num_places,
        start_county=args.start_county,
        improve_2opt=not args.no_2opt,
    )
    route_df.to_csv(args.out, index=False)
    print(route_df.to_string(index=False))
    print(f"\nWrote: {args.out}")

if __name__ == "__main__":
    main()