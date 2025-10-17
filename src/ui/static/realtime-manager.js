/**
 * Real-time request connection manager
 * Handles WebSocket connections and event distribution for multiple proxy services
 */
class RealTimeManager {
    constructor() {
        this.connections = new Map(); // service -> WebSocket
        this.reconnectAttempts = new Map(); // service -> number
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000; // Initial delay of one second
        this.listeners = new Set();
        this.isDestroyed = false;

        // Service configuration
        this.services = [
            { name: 'claude', port: 3210 },
            { name: 'codex', port: 3211 },
            { name: 'legacy', port: 3212 }
        ];

        // Connection status tracking
        this.connectionStatus = new Map();
        this.services.forEach(service => {
            this.connectionStatus.set(service.name, false);
        });
    }

    /**
     * Add an event listener
     * @param {Function} callback Event callback
     * @returns {Function} Function that removes the listener
     */
    addListener(callback) {
        if (typeof callback !== 'function') {
            throw new Error('Callback must be a function');
        }
        this.listeners.add(callback);
        return () => this.listeners.delete(callback);
    }

    /**
     * Connect all configured services
     */
    async connectAll() {
        if (this.isDestroyed) {
            console.warn('Manager has been destroyed; cannot connect services');
            return;
        }

        console.log('Starting connections for all real-time services...');

        for (const service of this.services) {
            this.connect(service.name, service.port);
        }
    }

    /**
     * Connect an individual service
     * @param {string} serviceName Service name
     * @param {number} port Port number
     */
    connect(serviceName, port) {
        if (this.isDestroyed) {
            console.warn(`Manager has been destroyed; cannot connect to ${serviceName}`);
            return;
        }

        // Skip when an existing connection is already open
        const existingWs = this.connections.get(serviceName);
        if (existingWs && existingWs.readyState === WebSocket.OPEN) {
            console.log(`${serviceName} WebSocket already connected; skipping duplicate connection`);
            return;
        }

        const wsUrl = `ws://${window.location.hostname}:${port}/ws/realtime`;
        console.log(`Connecting ${serviceName} WebSocket: ${wsUrl}`);

        try {
            const ws = new WebSocket(wsUrl);

            // Configure connection timeout
            const connectTimeout = setTimeout(() => {
                if (ws.readyState === WebSocket.CONNECTING) {
                    console.error(`${serviceName} WebSocket connection timed out`);
                    ws.close();
                    // Trigger a reconnect if the connection times out
                    this.scheduleReconnect(serviceName, port);
                }
            }, 5000); // Five second timeout for faster feedback

            ws.onopen = () => {
                clearTimeout(connectTimeout);
                console.log(`${serviceName} WebSocket connected successfully`);
                this.connections.set(serviceName, ws);
                this.reconnectAttempts.set(serviceName, 0);
                this.connectionStatus.set(serviceName, true);

                // Kick off heartbeat checks
                this.startHeartbeat(serviceName, ws);

                // Notify listeners about the successful connection
                this.notifyListeners({
                    type: 'connection',
                    service: serviceName,
                    status: 'connected'
                });

            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);

                    // Ignore heartbeat messages
                    if (data.type === 'ping') {
                        return;
                    }

                    // Attach service metadata before dispatching events
                    this.notifyListeners({
                        ...data,
                        service: serviceName
                    });
                } catch (error) {
                    console.error(`Failed to parse ${serviceName} WebSocket message:`, error, event.data);
                }
            };

            ws.onclose = (event) => {
                clearTimeout(connectTimeout);
                console.log(`${serviceName} WebSocket closed`, event.code, event.reason);

                this.connections.delete(serviceName);
                this.connectionStatus.set(serviceName, false);

                // Notify listeners about the closed connection
                this.notifyListeners({
                    type: 'connection',
                    service: serviceName,
                    status: 'disconnected',
                    code: event.code,
                    reason: event.reason
                });

                // Schedule a reconnect if the close was abnormal
                if (!this.isDestroyed && event.code !== 1000) {
                    this.scheduleReconnect(serviceName, port);
                }
            };

            ws.onerror = (error) => {
                clearTimeout(connectTimeout);
                console.error(`${serviceName} WebSocket error:`, error);

                // Notify listeners about the error
                this.notifyListeners({
                    type: 'connection',
                    service: serviceName,
                    status: 'error',
                    error: error
                });

                // Always trigger a reconnect when errors occur
                this.scheduleReconnect(serviceName, port);
            };

        } catch (error) {
            console.error(`Failed to create ${serviceName} WebSocket connection:`, error);
            this.scheduleReconnect(serviceName, port);
        }
    }

    /**
     * Start the heartbeat mechanism
     * @param {string} serviceName Service name
     * @param {WebSocket} ws WebSocket connection
     */
    startHeartbeat(serviceName, ws) {
        const heartbeatInterval = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
                try {
                    ws.send('{"type":"ping"}');
                } catch (error) {
                    console.error(`${serviceName} heartbeat failed:`, error);
                    clearInterval(heartbeatInterval);
                }
            } else {
                clearInterval(heartbeatInterval);
            }
        }, 30000); // Send heartbeat every 30 seconds

        // Clean up heartbeat when the socket closes
        ws.addEventListener('close', () => {
            clearInterval(heartbeatInterval);
        });
    }

    /**
     * Schedule a reconnect attempt
     * @param {string} serviceName Service name
     * @param {number} port Port number
     */
    scheduleReconnect(serviceName, port) {
        if (this.isDestroyed) {
            return;
        }

        const attempts = this.reconnectAttempts.get(serviceName) || 0;
        if (attempts >= this.maxReconnectAttempts) {
            console.error(`${serviceName} reconnect attempts exceeded (${attempts}/${this.maxReconnectAttempts}); stopping retries`);
            return;
        }

        // Retry quickly three times, then use exponential backoff
        let delay;
        if (attempts < 3) {
            delay = 2000; // First three retries every two seconds
        } else {
            delay = this.reconnectDelay * Math.pow(2, attempts - 3);
        }

        this.reconnectAttempts.set(serviceName, attempts + 1);

        console.log(`${serviceName} will reconnect in ${delay}ms... (attempt ${attempts + 1}/${this.maxReconnectAttempts})`);

        setTimeout(() => {
            if (!this.isDestroyed) {
                this.connect(serviceName, port);
            }
        }, delay);
    }

    /**
     * Notify all registered listeners
     * @param {Object} event Event payload
     */
    notifyListeners(event) {
        if (this.listeners.size === 0) {
            return;
        }

        this.listeners.forEach(listener => {
            try {
                listener(event);
            } catch (error) {
                console.error('Event listener execution error:', error);
            }
        });
    }

    /**
     * Get the current connection state for each service
     * @returns {Object} Service connection status map
     */
    getConnectionStatus() {
        const status = {};
        this.services.forEach(service => {
            const ws = this.connections.get(service.name);
            status[service.name] = ws ? ws.readyState === WebSocket.OPEN : false;
        });
        return status;
    }

    /**
     * Get summarized connection statistics
     * @returns {Object} Connection summary
     */
    getConnectionStats() {
        let connected = 0;
        let total = this.services.length;

        this.services.forEach(service => {
            const ws = this.connections.get(service.name);
            if (ws && ws.readyState === WebSocket.OPEN) {
                connected++;
            }
        });

        return {
            connected,
            total,
            services: this.getConnectionStatus()
        };
    }

    /**
     * Manually reconnect a specific service
     * @param {string} serviceName Service name
     */
    reconnectService(serviceName) {
        const service = this.services.find(s => s.name === serviceName);
        if (!service) {
            console.error(`Unknown service: ${serviceName}`);
            return;
        }

        const ws = this.connections.get(serviceName);
        if (ws) {
            ws.close();
        }

        // Reset reconnect counters before attempting again
        this.reconnectAttempts.set(serviceName, 0);
        this.connect(serviceName, service.port);
    }

    /**
     * Manually reconnect all services
     */
    reconnectAll() {
        console.log('Manually reconnecting all services...');
        // Reset retry counters for every service
        this.services.forEach(service => {
            this.reconnectAttempts.set(service.name, 0);
        });

        this.services.forEach(service => {
            this.reconnectService(service.name);
        });
    }

    /**
     * Disconnect all services without destroying the manager
     */
    disconnectAll() {
        console.log('Manually disconnecting every service...');
        
        // Close active connections
        this.connections.forEach((ws, serviceName) => {
            if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                console.log(`Disconnecting ${serviceName} WebSocket`);
                ws.close(1000, 'User requested disconnect');
            }
        });

        // Clear stored connection state
        this.connections.clear();
        this.reconnectAttempts.clear();
        
        // Update connection status to false
        this.services.forEach(service => {
            this.connectionStatus.set(service.name, false);
            // Notify listeners about the manual disconnect
            this.emitEvent({
                type: 'connection',
                service: service.name,
                status: 'disconnected'
            });
        });
    }

    /**
     * Destroy the manager instance
     */
    destroy() {
        console.log('Destroying RealTimeManager...');
        this.isDestroyed = true;

        // Close all existing connections
        this.connections.forEach((ws, serviceName) => {
            if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                console.log(`Closing ${serviceName} WebSocket connection`);
                ws.close(1000, 'Manager destroyed');
            }
        });

        // Reset internal state
        this.connections.clear();
        this.listeners.clear();
        this.reconnectAttempts.clear();
        this.connectionStatus.clear();

        console.log('RealTimeManager destroyed');
    }

    /**
     * Get a snapshot of the manager state
     * @returns {Object} Status information
     */
    getStatus() {
        return {
            isDestroyed: this.isDestroyed,
            connections: this.getConnectionStats(),
            listeners: this.listeners.size,
            services: this.services.map(service => ({
                name: service.name,
                port: service.port,
                connected: this.connectionStatus.get(service.name),
                reconnectAttempts: this.reconnectAttempts.get(service.name) || 0
            }))
        };
    }
}

// Export class for other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = RealTimeManager;
}

// Provide global namespace support for browsers
if (typeof window !== 'undefined') {
    window.RealTimeManager = RealTimeManager;
}
