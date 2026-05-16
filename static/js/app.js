const form = document.getElementById('routeForm');
const loadingSpinner = document.getElementById('loadingSpinner');
const resultsSection = document.getElementById('resultsSection');
const errorSection = document.getElementById('errorSection');
const downloadBtn = document.getElementById('downloadBtn');
const detailsBtn = document.getElementById('detailsBtn');

let lastRoute = null;
let map = null;
let routeLayer = null;
let markers = [];

// Initialize map
function initMap() {
    const mapContainer = document.getElementById('mapContainer');
    if (!mapContainer || map) return;
    
    map = L.map('mapContainer').setView([39.8283, -98.5795], 4); // Center of USA
    
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors',
        maxZoom: 19
    }).addTo(map);
    
    console.log('[DEBUG] Map initialized');
}

// Clear existing route from map
function clearRoute() {
    if (routeLayer) {
        map.removeLayer(routeLayer);
        routeLayer = null;
    }
    markers.forEach(marker => map.removeLayer(marker));
    markers = [];
}

// Display route on map
function displayRouteOnMap(route) {
    if (!route || route.length === 0) return;
    if (!map) initMap();
    
    clearRoute();
    
    const coords = route.map(s => [s.parking_lat, s.parking_lon]);
    
    // Draw polyline
    routeLayer = L.polyline(coords, {
        color: '#3b82f6',
        weight: 3,
        opacity: 0.7
    }).addTo(map);
    
        // Add markers
    route.forEach((stop, idx) => {
        let color = '#3b82f6';
        if (idx === 0) color = '#10b981';
        else if (idx === route.length - 1) color = '#ef4444';
        
        const icon = L.divIcon({
            html: `<div style="background:${color};width:32px;height:32px;border-radius:50%;border:3px solid white;display:flex;align-items:center;justify-content:center;color:white;font-weight:600;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,0.2);">${stop.order}</div>`,
            iconSize: [32, 32],
            className: ''
        });
        
        const popupContent = `
            <div style="font-size:13px; max-width: 250px; max-height: 300px; overflow-y: auto;">
                <strong>Stop ${stop.order}</strong><br>
                ZIP ${stop.zip_code}<br>
                ${stop.county}, ${stop.state}<br>
                SVI: ${(stop.svi_overall * 100).toFixed(1)}%<br>
                Resource sites: ${stop.resource_sites}<br>
                Parking source: ${stop.parking_source}<br>
                Distance: ${stop.total_km.toFixed(1)} km
            </div>
        `;

        const tooltipText = `Stop ${stop.order}: ZIP ${stop.zip_code}`;
        
        const marker = L.marker([stop.parking_lat, stop.parking_lon], { icon })
            .bindPopup(popupContent)
            .bindTooltip(tooltipText, { sticky: true })
            .addTo(map);
        
        markers.push(marker);
    });
    
    // Fit map to route bounds
    map.fitBounds(routeLayer.getBounds(), { padding: [50, 50] });
    
    console.log('[DEBUG] Route displayed on map');
}

// Form submission
if (form) {
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        console.log('[DEBUG] Form submitted');
        
        if (resultsSection) resultsSection.style.display = 'none';
        if (errorSection) errorSection.style.display = 'none';
        if (loadingSpinner) loadingSpinner.style.display = 'flex';
        
        const formData = new FormData(form);
        const payload = {
            state: formData.get('state'),
            county: formData.get('county'),
            num_places: parseInt(formData.get('num_places')),
            svi_weight: parseFloat(formData.get('svi_weight')),
            improve_2opt: form.elements['improve_2opt']?.checked ?? true,
            use_clustering: form.elements['use_clustering']?.checked ?? false,
        };
        
        console.log('[DEBUG] Payload:', payload);

        try {
            const response = await fetch('/api/route', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            
            console.log('[DEBUG] Response status:', response.status);

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}`);
            }
            
            const data = await response.json();
            console.log('[DEBUG] Route data received:', data);
            
            displayResults(data);
            displayRouteOnMap(data.stops || data.route || []);
            lastRoute = data.stops || data.route || [];
            
        } catch (error) {
            console.error('[ERROR]', error);
            showError(error.message);
        } finally {
            if (loadingSpinner) loadingSpinner.style.display = 'none';
        }
    });
}

function displayResults(data) {
    const stats = data.summary || data.stats;
    
    if (!resultsSection) {
        console.error('[ERROR] Results section not found');
        return;
    }
    
    const totalStops = document.getElementById('totalStops');
    const totalDistance = document.getElementById('totalDistance');
    const totalSVI = document.getElementById('totalSVI');
    
    if (totalStops) totalStops.textContent = stats.num_stops;
    if (totalDistance) totalDistance.textContent = stats.total_km.toFixed(1);
    if (totalSVI) totalSVI.textContent = stats.total_svi.toFixed(1);
    
    resultsSection.style.display = 'block';
    console.log('[DEBUG] Results displayed');
}

function showError(message) {
    if (!errorSection) return;
    const errorMessage = document.getElementById('errorMessage');
    if (errorMessage) errorMessage.textContent = message;
    errorSection.style.display = 'block';
}

// Download CSV
if (downloadBtn) {
    downloadBtn.addEventListener('click', () => {
        if (!lastRoute) {
            alert('No route data available');
            return;
        }
        
        const csv = [
            [
                'Order',
                'ZIP Code',
                'County',
                'State',
                'SVI',
                'Weighted SVI',
                'Hospitals',
                'Nursing Homes',
                'Public Health Departments',
                'Pharmacies',
                'Resource Sites',
                'Distance (km)',
                'Total (km)',
                'Lat',
                'Lon',
                'Parking Source'
            ],
            ...lastRoute.map(s => [
                s.order, s.zip_code, s.county, s.state,
                (s.svi_overall * 100).toFixed(2) + '%',
                s.weighted_svi.toFixed(4),
                s.hospitals,
                s.nursing_homes,
                s.public_health_departments,
                s.pharmacies,
                s.resource_sites,
                s.leg_km_from_prev.toFixed(2),
                s.total_km.toFixed(2),
                s.parking_lat.toFixed(6),
                s.parking_lon.toFixed(6),
                s.parking_source
            ])
        ].map(row => row.join(',')).join('\n');
        
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `route_${Date.now()}.csv`;
        a.click();
        URL.revokeObjectURL(url);
        
        console.log('[DEBUG] CSV downloaded');
    });
}

// Details button - open detailed view in new window
if (detailsBtn) {
    detailsBtn.addEventListener('click', () => {
        if (!lastRoute) {
            alert('No route data available');
            return;
        }
        
        const detailsHTML = generateDetailsHTML(lastRoute);
        const detailsWindow = window.open('', '_blank');
        detailsWindow.document.write(detailsHTML);
        detailsWindow.document.close();
    });
}

function generateDetailsHTML(route) {
    const rows = route.map(stop => `
        <tr>
            <td>${stop.order}</td>
            <td>${stop.zip_code}</td>
            <td>${stop.county}</td>
            <td>${stop.state}</td>
            <td>${(stop.svi_overall * 100).toFixed(2)}%</td>
            <td>${stop.resource_sites}</td>
            <td>${stop.leg_km_from_prev.toFixed(2)}</td>
            <td>${stop.total_km.toFixed(2)}</td>
            <td>${stop.parking_lat.toFixed(4)}, ${stop.parking_lon.toFixed(4)}</td>
            <td>${stop.parking_source}</td>
        </tr>
    `).join('');
    
    return `
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Route Details</title>
            <style>
                body { font-family: Arial, sans-serif; padding: 20px; }
                h1 { color: #1a1a1a; margin-bottom: 20px; }
                table { width: 100%; border-collapse: collapse; }
                th, td { padding: 10px; text-align: left; border-bottom: 1px solid #e5e5e5; }
                th { background: #f3f4f6; font-weight: 600; }
                tr:hover { background: #f9fafb; }
            </style>
        </head>
        <body>
            <h1>Route Details</h1>
            <table>
                <thead>
                    <tr>
                        <th>Order</th>
                        <th>ZIP Code</th>
                        <th>County</th>
                        <th>State</th>
                        <th>SVI</th>
                        <th>Resource Sites</th>
                        <th>Distance from Prev (km)</th>
                        <th>Total Distance (km)</th>
                        <th>Coordinates</th>
                        <th>Parking Source</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </body>
        </html>
    `;
}

// Initialize map on load
window.addEventListener('load', () => {
    console.log('[DEBUG] Window loaded, initializing map...');
    setTimeout(initMap, 100); // Small delay to ensure DOM is ready
});

console.log('[DEBUG] app.js loaded');
