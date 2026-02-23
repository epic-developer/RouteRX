/**
 * Map Module - Handles Leaflet map interactions
 */
const MapModule = {
    map: null,
    routeLayer: null,
    markersLayer: null,
    currentBounds: null,

    /**
     * Initialize the map
     */
    init() {
        // Create map centered on US
        this.map = L.map('map').setView([39.8283, -98.5795], 4);

        // Add tile layer
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 19
        }).addTo(this.map);

        // Create layer groups
        this.routeLayer = L.layerGroup().addTo(this.map);
        this.markersLayer = L.layerGroup().addTo(this.map);

        console.log('Map initialized');
    },

    /**
     * Display route on map
     * @param {Array} routeData - Array of route stops
     */
    displayRoute(routeData) {
        // Clear existing layers
        this.clear();

        if (!routeData || routeData.length === 0) {
            console.warn('No route data to display');
            return;
        }

        // Extract coordinates
        const coords = routeData.map(stop => [
            parseFloat(stop.parking_lat),
            parseFloat(stop.parking_lon)
        ]);

        // Draw route line
        const polyline = L.polyline(coords, {
            color: '#667eea',
            weight: 4,
            opacity: 0.7,
            dashArray: '10, 10',
            lineJoin: 'round'
        }).addTo(this.routeLayer);

        // Add markers for each stop
        routeData.forEach((stop, index) => {
            this.addMarker(stop, index, routeData.length);
        });

        // Fit map to route bounds
        this.currentBounds = polyline.getBounds();
        this.map.fitBounds(this.currentBounds, {
            padding: [50, 50]
        });
    },

    /**
     * Add a marker to the map
     * @param {Object} stop - Stop data
     * @param {number} index - Stop index
     * @param {number} total - Total number of stops
     */
    addMarker(stop, index, total) {
        const isStart = index === 0;
        const isEnd = index === total - 1;

        // Choose marker color
        let markerColor = 'blue';
        if (isStart) markerColor = 'green';
        if (isEnd) markerColor = 'red';

        // Create custom icon
        const iconUrl = `https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-${markerColor}.png`;
        const icon = L.icon({
            iconUrl: iconUrl,
            shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
            iconSize: [25, 41],
            iconAnchor: [12, 41],
            popupAnchor: [1, -34],
            shadowSize: [41, 41]
        });

        // Create marker
        const marker = L.marker(
            [parseFloat(stop.parking_lat), parseFloat(stop.parking_lon)],
            { icon: icon }
        ).addTo(this.markersLayer);

        // Generate label
        const label = isStart ? 'START' : isEnd ? 'END' : `Stop ${stop.order}`;

        // Create popup content
        const popupContent = `
            <div style="min-width: 220px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                <h3 style="margin: 0 0 12px 0; color: #667eea; font-size: 1.2rem; border-bottom: 2px solid #667eea; padding-bottom: 8px;">
                    ${label}
                </h3>
                <div style="line-height: 1.8;">
                    <p style="margin: 6px 0;">
                        <strong style="color: #4b5563;">County:</strong> 
                        <span style="color: #1f2937;">${stop.county}</span>
                    </p>
                    <p style="margin: 6px 0;">
                        <strong style="color: #4b5563;">State:</strong> 
                        <span style="color: #1f2937;">${stop.state}</span>
                    </p>
                    <p style="margin: 6px 0;">
                        <strong style="color: #4b5563;">SVI Score:</strong> 
                        <span style="color: #ef4444; font-weight: 600;">${parseFloat(stop.svi_overall).toFixed(4)}</span>
                    </p>
                    <p style="margin: 6px 0;">
                        <strong style="color: #4b5563;">Weighted SVI:</strong> 
                        <span style="color: #1f2937;">${parseFloat(stop.weighted_svi).toFixed(4)}</span>
                    </p>
                    ${stop.leg_km_from_prev > 0 ? `
                        <p style="margin: 6px 0;">
                            <strong style="color: #4b5563;">From Previous:</strong> 
                            <span style="color: #1f2937;">${parseFloat(stop.leg_km_from_prev).toFixed(2)} km</span>
                        </p>
                    ` : ''}
                    <p style="margin: 6px 0;">
                        <strong style="color: #4b5563;">Total Distance:</strong> 
                        <span style="color: #1f2937;">${parseFloat(stop.total_km).toFixed(2)} km</span>
                    </p>
                </div>
            </div>
        `;

        marker.bindPopup(popupContent, { maxWidth: 300 });
        marker.bindTooltip(label, {
            permanent: false,
            direction: 'top',
            offset: [0, -35]
        });
    },

    /**
     * Clear all layers from map
     */
    clear() {
        this.routeLayer.clearLayers();
        this.markersLayer.clearLayers();
    },

    /**
     * Reset map view to route bounds
     */
    resetView() {
        if (this.currentBounds) {
            this.map.fitBounds(this.currentBounds, {
                padding: [50, 50]
            });
        }
    },

    /**
     * Toggle fullscreen mode
     */
    toggleFullscreen() {
        const mapElement = document.getElementById('map');
        
        if (!document.fullscreenElement) {
            mapElement.requestFullscreen().then(() => {
                // Refresh map after entering fullscreen
                setTimeout(() => this.map.invalidateSize(), 100);
            });
        } else {
            document.exitFullscreen().then(() => {
                // Refresh map after exiting fullscreen
                setTimeout(() => this.map.invalidateSize(), 100);
            });
        }
    }
};

// Make MapModule available globally
window.MapModule = MapModule;

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => MapModule.init());
} else {
    MapModule.init();
}