/**
 * API Module - Handles all backend communication
 */
const API = {
    baseURL: window.location.origin,

    /**
     * Check API health status
     */
    async checkHealth() {
        try {
            const response = await fetch(`${this.baseURL}/api/health`);
            return await response.json();
        } catch (error) {
            console.error('Health check failed:', error);
            return { status: 'error' };
        }
    },

    /**
     * Generate route based on user parameters
     * @param {Object} params - Route configuration
     */
    async generateRoute(params) {
        try {
            const response = await fetch(`${this.baseURL}/api/route`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(params)
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Failed to generate route');
            }

            return data;
        } catch (error) {
            console.error('Route generation failed:', error);
            throw error;
        }
    },

    /**
     * Export map as HTML
     * @param {Array} route - Route data
     */
    async exportMap(route) {
        try {
            const response = await fetch(`${this.baseURL}/api/export-map`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ route })
            });

            if (!response.ok) {
                throw new Error('Failed to export map');
            }

            const blob = await response.blob();
            return blob;
        } catch (error) {
            console.error('Map export failed:', error);
            throw error;
        }
    }
};

// Make API available globally
window.API = API;