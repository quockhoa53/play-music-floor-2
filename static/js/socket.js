/**
 * WebSocket manager with auto-reconnection and event dispatching.
 */
class RoomSocket {
    constructor() {
        this.ws = null;
        this.roomId = null;
        this.userId = null;
        this.userData = null;
        this.isConnected = false;
        this.listeners = {};
        this.reconnectTimer = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
    }

    on(event, callback) {
        if (!this.listeners[event]) {
            this.listeners[event] = [];
        }
        this.listeners[event].push(callback);
    }

    trigger(event, data) {
        if (this.listeners[event]) {
            this.listeners[event].forEach(cb => {
                try {
                    cb(data);
                } catch (e) {
                    console.error(`Error in listener for ${event}:`, e);
                }
            });
        }
    }

    connect(roomId, userId, userData) {
        this.roomId = roomId;
        this.userId = userId;
        this.userData = userData;

        if (this.ws) {
            try { this.ws.close(); } catch (e) {}
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/${roomId}/${userId}`;
        console.log(`[Socket] Connecting to: ${wsUrl}`);
        this.trigger('connecting');

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('[Socket] Connected!');
            this.isConnected = true;
            this.reconnectAttempts = 0;
            this.trigger('connected');

            // Send Join handshake
            this.send('join', {
                name: this.userData.name,
                avatar: this.userData.avatar,
                color: this.userData.color
            });
        };

        this.ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                const type = message.type;
                if (type) {
                    this.trigger(type, message);
                }
                this.trigger('*', message);
            } catch (err) {
                console.error('[Socket] Failed to parse incoming message:', err);
            }
        };

        this.ws.onclose = (event) => {
            console.warn('[Socket] Disconnected:', event);
            this.isConnected = false;
            this.trigger('disconnected');
            this.scheduleReconnect();
        };

        this.ws.onerror = (err) => {
            console.error('[Socket] Error:', err);
            this.trigger('error', err);
        };
    }

    scheduleReconnect() {
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.error('[Socket] Max reconnect attempts reached');
            this.trigger('reconnect_failed');
            return;
        }

        const delay = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), 10000);
        this.reconnectAttempts++;
        console.log(`[Socket] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})...`);
        this.reconnectTimer = setTimeout(() => {
            if (this.roomId && this.userId) {
                this.connect(this.roomId, this.userId, this.userData);
            }
        }, delay);
    }

    send(action, payload = {}) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            console.warn('[Socket] Cannot send message, socket not open:', action);
            return;
        }
        const data = { action, ...payload };
        this.ws.send(JSON.stringify(data));
    }

    sendChat(text) {
        this.send('chat', { text });
    }

    sendReaction(emoji) {
        this.send('reaction', { emoji });
    }

    addTrack(track) {
        this.send('add_track', { track });
    }

    removeTrack(uid) {
        this.send('remove_track', { uid });
    }

    reorderQueue(uids) {
        this.send('reorder_queue', { uids });
    }

    controlPlayback(subAction, currentTime) {
        this.send('control', { sub_action: subAction, current_time: currentTime });
    }

    voteSkip(force = false) {
        this.send('skip', { force });
    }

    trackEnded(trackId) {
        this.send('track_ended', { track_id: trackId });
    }

    toggleDjMode() {
        this.send('toggle_dj_mode');
    }

    disconnect() {
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        if (this.ws) {
            try { this.ws.close(); } catch (e) {}
        }
        this.isConnected = false;
    }
}

window.roomSocket = new RoomSocket();
