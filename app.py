import os
import sys
from pathlib import Path

# Add RouteRX to path
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, jsonify, request, render_template
import pandas as pd

# Import from route_finder
try:
    from route_finder import (
        build_nodes,
        find_route,
        build_distance_provider,
    )
except ImportError as e:
    print(f"[ERROR] Failed to import from route_finder: {e}")
    sys.exit(1)

app = Flask(
    __name__,
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
    static_url_path="/static",
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


@app.route("/", methods=["GET"])
def index():
    """Serve the main page."""
    try:
        return render_template("index.html")
    except Exception as e:
        return f"Error loading template: {e}", 500


@app.route("/api/route", methods=["POST"])
def api_route():
    payload = request.get_json(silent=True) or {}

    try:
        state = payload.get("state")
        if state:
            state = str(state).strip()
        
        if not state:
            error_msg = "state is required and cannot be empty"
            print(f"[ERROR] {error_msg}")
            return jsonify({"error": error_msg}), 400

        num_places = payload.get("num_places")
        if num_places is None:
            error_msg = "num_places is required"
            print(f"[ERROR] {error_msg}")
            return jsonify({"error": error_msg}), 400

        try:
            num_places = int(num_places)
        except (ValueError, TypeError) as e:
            error_msg = f"num_places must be a valid integer, got: {num_places}"
            print(f"[ERROR] {error_msg}")
            return jsonify({"error": error_msg}), 400

        svi_weight = float(payload.get("svi_weight") or 1.0)
        
        start_county = payload.get("start_county")
        if start_county and str(start_county).strip():
            start_county = str(start_county).strip()
        else:
            start_county = None
        
        improve_2opt = bool(payload.get("improve_2opt", True))
        use_centroid_fallback = bool(payload.get("use_centroid_fallback", True))
        use_clustering = bool(payload.get("use_clustering", False))
        sleep_s = float(payload.get("sleep_s", 0.0))
        
        distance_mode = payload.get("distance_mode")
        if not distance_mode or distance_mode == "null":
            distance_mode = "haversine"
        distance_mode = str(distance_mode)

        print(f"[DEBUG] All parameters parsed successfully:")
        print(f"  - state: {state}")
        print(f"  - num_places: {num_places}")
        print(f"  - svi_weight: {svi_weight}")
        print(f"  - start_county: {start_county}")
        print(f"  - improve_2opt: {improve_2opt}")
        print(f"  - use_clustering: {use_clustering}")
        print(f"  - distance_mode: {distance_mode}")

        project_dir = os.path.dirname(__file__)
        svi_csv_path = os.path.join(project_dir, "SVI_2022_US_county.csv")

        if not os.path.exists(svi_csv_path):
            error_msg = f"SVI CSV not found at {svi_csv_path}"
            print(f"[ERROR] {error_msg}")
            return jsonify({"error": error_msg}), 400

        print(f"[DEBUG] SVI CSV found: {svi_csv_path}")

        google_api_key = payload.get("google_maps_api_key") or os.getenv("GOOGLE_MAPS_API_KEY")
        distance_provider = build_distance_provider(
            mode=distance_mode,
            google_api_key=google_api_key,
        )
        parking_distance_provider = build_distance_provider(
            mode="haversine",
            google_api_key=None,
        )
        print(f"[DEBUG] Distance providers initialized")

        print(f"[DEBUG] Building nodes from SVI data...")
        nodes = build_nodes(
            svi_csv=svi_csv_path,
            state_name=state,
            svi_weight=svi_weight,
            sleep_s=sleep_s,
            use_centroid_if_missing=use_centroid_fallback,
            cache_csv=None,
        )
        print(f"[DEBUG] Built {len(nodes)} nodes for state: {state}")

        if not nodes:
            error_msg = f"No counties found for state: '{state}'. Check state name spelling."
            print(f"[ERROR] {error_msg}")
            return jsonify({"error": error_msg}), 400

        print(f"[DEBUG] Calling find_route...")
        route_df, cluster_info = find_route(
        nodes=nodes,
        num_places=num_places,
        start_county=start_county,
        improve_2opt=improve_2opt,
        distance_provider=distance_provider,
        parking_distance_provider=parking_distance_provider,
        use_clustering=use_clustering,
        )   
        print(f"[DEBUG] Route generated with {len(route_df)} stops")

        records = route_df.to_dict(orient="records")
        
        if cluster_info:
            for record in records:
                order = record.get("order")
                if order in cluster_info:
                    record["cluster_counties"] = cluster_info[order]
                    record["cluster_count"] = len(cluster_info[order])

        for record in records:
            for key, value in record.items():
                if hasattr(value, 'item'):
                    record[key] = value.item()

        summary = {
            "num_stops": len(records),
            "total_km": float(route_df["total_km"].iloc[-1]) if not route_df.empty else 0.0,
            "total_svi": float(route_df["total_svi"].iloc[-1]) if not route_df.empty else 0.0,
            "total_weighted_svi": float(route_df["total_weighted_svi"].iloc[-1]) if not route_df.empty else 0.0,
        }

        print(f"[DEBUG] Response summary: {summary}")
        print(f"[DEBUG] ===== REQUEST COMPLETED SUCCESSFULLY =====\n")
        
        return jsonify({"stats": summary, "route": records})

    except Exception as exc:
        print(f"[ERROR] Exception occurred: {str(exc)}")
        import traceback
        traceback.print_exc()
        print(f"[DEBUG] ===== REQUEST FAILED =====\n")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("\n" + "="*60)
    print("[INFO] Starting RouteRX Flask app...")
    print("="*60 + "\n")
    app.run(debug=True, host="127.0.0.1", port=8080)