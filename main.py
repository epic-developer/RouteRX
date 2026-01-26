import osmnx as ox
import pandas as pd
import argparse

def parse_args():
    parser = argparse.ArgumentParser(
        description="Attach parking lot coordinates to MA counties"
    )
    parser.add_argument(
        "--in_csv",
        required=True,
        help="Input CSV (e.g. MA_SVI_official.csv)"
    )
    parser.add_argument(
        "--out_csv",
        required=True,
        help="Output CSV with Parking Lots column"
    )
    return parser.parse_args()

def get_parking_lots_for_county(county_name, state_name="Massachusetts"):
    place_name = f"{county_name}, {state_name}, USA"

    try:
        gdf = ox.geocode_to_gdf(place_name)
        if gdf.empty:
            print(f"No geometry for {place_name}")
            return ""
        polygon = gdf.iloc[0].geometry

        tags = {"amenity": "parking"}
        parks = ox.features.features_from_polygon(polygon, tags)

        if parks.empty:
            return ""

        # Best-effort "public-ish" filter
        if "access" in parks.columns:
            parks = parks[~parks["access"].isin(["private", "no", "customers"])]

        if parks.empty:
            return ""

        coords = []
        for geom in parks.geometry:
            if geom.is_empty:
                continue
            pt = geom.centroid if geom.geom_type in ["Polygon", "MultiPolygon"] else geom
            # store as lat,lon
            coords.append(f"{pt.y:.6f},{pt.x:.6f}")

        return "; ".join(coords)

    except Exception as e:
        print(f"Error for {place_name}: {e}")
        return ""

if __name__ == "__main__":
    args = parse_args()

    df = pd.read_csv(args.in_csv, dtype=str)

    parking_strings = []
    for _, row in df.iterrows():
        county = row.get("COUNTY") or row.get("county") or row.get("County")
        if not isinstance(county, str):
            raise KeyError("Could not find county column (COUNTY/county/County).")

        print(f"Processing {county}...")
        s = get_parking_lots_for_county(county_name=county)
        parking_strings.append(s)

    df["Parking Lots"] = parking_strings
    df.to_csv(args.out_csv, index=False)

    print(f"Wrote {args.out_csv}")