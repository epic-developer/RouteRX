import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
import folium

# --- CONFIGURATION ---
TARGET_STATE = "Massachusetts"  
ALPHA = 5                 # Weight of metric (higher = prioritize uninsured rate over distance)
EPS = 1e-3
num_stops = 15           

# File paths
DATA_PATH = "city_data_completed.csv" 

def haversine_dist(lat1, lon1, lat2, lon2):
    R = 3958.8  # radius in miles
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2)**2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2)**2
    return 2 * R * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

# 1. Load and Prepare Data
df = pd.read_csv(DATA_PATH)

# Rename columns for clarity based on your specific CSV structure
df = df.rename(columns={
    'Latitude': 'lat', 
    'Longitude': 'lng',
    'City': 'NAME',
    'S2701_C01_001E (Population)': 'pop',
    'S2701_C04_001E (Number Uninsured - Health Insurance)': 'uninsured'
})

# Filter by state if applicable
if 'State' in df.columns:
    df = df[df['State'] == TARGET_STATE].copy()

# Ensure numeric data
df['pop'] = pd.to_numeric(df['pop'], errors='coerce').fillna(0)
df['uninsured'] = pd.to_numeric(df['uninsured'], errors='coerce').fillna(0)

# 2. Calculate Percent Uninsured
df['Percent_Uninsured'] = df.apply(lambda row: row['uninsured'] / row['pop'] if row['pop'] > 0 else 0, axis=1)

# Select relevant columns
combined = df[['NAME', 'lat', 'lng', 'Percent_Uninsured']].copy()
combined = combined.dropna(subset=['lat', 'lng'])
combined = combined[combined['Percent_Uninsured'] >= 0].reset_index(drop=True)

print(f"Total cities available for clustering: {len(combined)}")

# 3. Clustering Logic
# We cluster the data to find 'num_stops' optimal locations
if len(combined) > num_stops:
    print(f"Clustering {len(combined)} cities into {num_stops} stops...")
    coords = combined[["lat", "lng"]].values
    weights = combined['Percent_Uninsured'].values
    
    if weights.sum() == 0: weights = None

    clusters = KMeans(n_clusters=num_stops, random_state=40, n_init=10)
    clusters.fit(coords, sample_weight=weights)

    combined['cluster_label'] = clusters.labels_

    stops = []
    
    # Store original coordinates to find the nearest actual city for each cluster center
    original_coords = combined[['lat', 'lng']].values
    original_names = combined['NAME'].values
    original_uninsured = combined['Percent_Uninsured'].values

    for i in range(num_stops):
        lat, lng = clusters.cluster_centers_[i]
        
        # 1. Calculate the nearest actual city to the cluster center
        dists_to_center = haversine_dist(lat, lng, original_coords[:,0], original_coords[:,1])
        nearest_idx = np.argmin(dists_to_center)
        
        # 2. Get all cities assigned to this specific cluster
        cluster_cities = combined[combined['cluster_label'] == i]['NAME'].tolist()
        
        # 3. Format the list for the popup (HTML string)
        cities_html_list = "<br>".join(cluster_cities)
        city_count = len(cluster_cities)
        
        nearest_city_name = original_names[nearest_idx]
        nearest_city_val = original_uninsured[nearest_idx]

        stops.append({
            'NAME': f"Stop {i+1}", 
            'Nearest_City': nearest_city_name,
            'Percent_Uninsured': nearest_city_val, 
            'lat': lat,
            'lng': lng,
            # --- Store the list and count ---
            'Cluster_Cities_HTML': cities_html_list,
            'Cluster_Count': city_count
        })
    combined = pd.DataFrame(stops)
else:
    print("Not enough cities to cluster. Each city is its own stop.")
    # If fewer cities than stops, just use the cities themselves
    combined['Nearest_City'] = combined['NAME']
    # --- FALLBACK: Add these columns so the map code doesn't crash ---
    combined['Cluster_Cities_HTML'] = combined['NAME']
    combined['Cluster_Count'] = 1

# 4. Routing Logic
n = len(combined)
if n == 0:
    print(f"No data found for state: {TARGET_STATE}")
else:
    lats, lons = combined["lat"].values, combined["lng"].values
    values = combined["Percent_Uninsured"].values 

    # Start at the location with highest Percent Uninsured
    start_idx = combined["Percent_Uninsured"].idxmax()
    visited = [start_idx]
    remaining = list(set(range(n)) - {start_idx})

    current = start_idx
    while remaining:
        rem_arr = np.array(remaining)
        dists = haversine_dist(lats[current], lons[current], lats[rem_arr], lons[rem_arr])
        
        # Decide next stop: Balance distance vs. Uninsured %
        scores = dists / ((values[rem_arr] + EPS) ** ALPHA)
        
        best_idx_in_rem = np.argmin(scores)
        current = remaining.pop(best_idx_in_rem)
        visited.append(current)

    route_df = combined.iloc[visited].reset_index(drop=True)
    filename = f"{TARGET_STATE.lower().replace(' ', '_')}_route.csv"
    route_df.to_csv(filename, index=False)
    print(f"Generated route for {TARGET_STATE} ({n} stops).")

# 5. Map Generation with Custom Popup
start_lat = route_df.iloc[0]['lat']
start_lon = route_df.iloc[0]['lng']
m = folium.Map(location=[start_lat, start_lon], zoom_start=8)

route_coords = route_df[['lat', 'lng']].values.tolist()

# Draw the path
folium.PolyLine(
    route_coords, 
    color="blue", 
    weight=2.5, 
    opacity=0.8,
    dash_array='5, 5'
).add_to(m)

# Add Markers
for i, row in route_df.iterrows():
    # Define Icons
    if i == 0:
        label_text = "START"
        icon_color = 'green'
        icon_type = 'play'
    elif i == len(route_df) - 1:
        label_text = "END"
        icon_color = 'red'
        icon_type = 'stop'
    else:
        label_text = f"Stop {i+1}"
        icon_color = 'blue'
        icon_type = 'info-sign'

    # --- CUSTOM POPUP CONTENT ---
    city_name = row['Nearest_City']
    percent_val = row['Percent_Uninsured'] * 100 

    # --- RETRIEVE DATA ---
    cities_html_list = row['Cluster_Cities_HTML']
    city_count = row['Cluster_Count']
    
    # HTML formatted string for the popup
    popup_html = f"""
    <div style="width: 250px; max-height: 250px; overflow-y: auto;">
        <b>{label_text}</b><br>
        <b>Center City:</b> {city_name}<br>
        <b>Uninsured Rate:</b> {percent_val:.1f}%<br>
        <hr>
        <b>Cities in this Cluster ({city_count}):</b><br>
        <div style="font-size: 0.9em; color: #555;">
            {cities_html_list}
        </div>
    </div>
    """
    info_html = f"""
    <div style="font-family: sans-serif; font-size: 0.9em; width: 200px;">
        <b>{label_text}</b><br>
        <b>Center:</b> {city_name}<br>
        <b>Rate:</b> {percent_val:.1f}%<br>
        <hr style="margin: 5px 0;">
        <b>Cities ({city_count}):</b><br>
        {cities_html_list}
    </div>
    """

    folium.Marker(
        location=[row['lat'], row['lng']],
        
        # 1. POPUP (Shows when you Click)
        popup=folium.Popup(info_html, max_width=300), 
        
        # 2. TOOLTIP (Shows when you Hover)
        # We wrap the HTML in folium.Tooltip to ensure it renders the tags (like <br>) correctly
        tooltip=folium.Tooltip(info_html, sticky=True),
        
        icon=folium.Icon(color=icon_color, icon=icon_type)
    ).add_to(m)

    folium.Marker(
        location=[row['lat'], row['lng']],
        popup=folium.Popup(popup_html, max_width=300), 
        tooltip=f"{label_text}: {city_name}",
        icon=folium.Icon(color=icon_color, icon=icon_type)
    ).add_to(m)

# Save map
map_filename = f"{TARGET_STATE.lower().replace(' ', '_')}_map.html"
m.save(map_filename)
print(f"Map saved to {map_filename}")