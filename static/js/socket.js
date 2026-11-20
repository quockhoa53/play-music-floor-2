/**
 * Authenticated WebSocket manager with session tokens, private room invite support & safe reconnection.
 */
class RoomSocket {
    constructor() {
        this.ws = null;
        this.roomId = null;
        this.sessionToken = null;
        this.inviteToken = null;
        this.userData = null;
        this.isConnected = false;
        this.listeners = {};
        this.reconnectTimer = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.manualClose = false;
        this.sessionRefresher = null;
    }

    setSessionRefresher(fn) {
        this.sessionRefresher = typeof fn === 'function' ? fn : null;
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

    connect(roomId, sessionToken, userData, inviteToken = null) {
        this.roomId = roomId;
        this.sessionToken = sessionToken;
        this.inviteToken = inviteToken;
        this.userData = userData;
        this.manualClose = false;

        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.ws) {
            try {
                this.ws.onclose = null;
                this.ws.close();
            } catch (e) {}
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const params = new URLSearchParams({ token: sessionToken });
        if (inviteToken) params.set('invite', inviteToken);
        const wsUrl = `${protocol}//${window.location.host}/ws/${encodeURIComponent(roomId)}?${params.toString()}`;

        this.trigger('connecting');
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            this.isConnected = true;
            this.reconnectAttempts = 0;
            this.trigger('connected');

            this.send('join', {
                name: this.userData.name,
                avatar: this.userData.avatar,
                color: this.userData.color,
            });
        };

        this.ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                if (message.type) {
                    this.trigger(message.type, message);
                }
                this.trigger('*', message);
            } catch (err) {
                console.error('[Socket] Failed to parse message:', err);
            }
        };

        this.ws.onclose = (event) => {
            this.isConnected = false;
            this.trigger('disconnected', event);

            if (this.manualClose) return;

            // Non-retryable security / authorization close codes
            if (event.code === 4003) {
                this.trigger('access_denied', { reason: event.reason || 'Không có quyền truy cập phòng riêng tư' });
                return;
            }
            if (event.code === 4004) {
                this.trigger('room_not_found', { reason: event.reason || 'Phòng không tồn tại' });
                return;
            }
            if (event.code === 4000) {
                // Replaced by newer tab of same session
                return;
            }
            if (event.code === 4001) {
                // Session expired — trigger re-auth
                this.trigger('session_expired');
                return;
            }
            if (event.code === 4029) {
                this.trigger('rate_limited', { reason: event.reason || 'Quá nhiều kết nối đồng thời' });
                return;
            }

            this.scheduleReconnect();
        };

        this.ws.onerror = (err) => {
            this.trigger('socket_error', err);
        };
    }

    scheduleReconnect() {
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            this.trigger('reconnect_failed');
            return;
        }

        const delay = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts), 10000);
        this.reconnectAttempts++;
        this.reconnectTimer = setTimeout(async () => {
            if (this.manualClose || !this.roomId) return;
            try {
                // Re-validate or refresh session token before reconnecting (recovers seamlessly after server restart/redeploy)
                if (this.sessionRefresher) {
                    const freshToken = await this.sessionRefresher();
                    if (freshToken) {
                        this.sessionToken = freshToken;
                    }
                }
            } catch (err) {
                // Server may still be restarting — schedule next backoff attempt
                this.scheduleReconnect();
                return;
            }
            if (!this.manualClose && this.roomId && this.sessionToken) {
                this.connect(this.roomId, this.sessionToken, this.userData, this.inviteToken);
            }
        }, delay);
    }

    send(action, payload = {}) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }
        this.ws.send(JSON.stringify({ action, ...payload }));
    }

    sendChat(text) {
        this.send('chat', { text });
    }

    sendGif(gifId, caption = '') {
        this.send('chat', { media_type: 'gif', gif_id: gifId, text: caption });
    }

    sendSticker(stickerId, caption = '') {
        this.send('chat', { media_type: 'sticker', gif_id: stickerId, text: caption });
    }

    sendImage(mediaId, caption = '') {
        this.send('chat', { media_type: 'image', media_id: mediaId, text: caption });
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

    requestPriority(trackUid) {
        this.send('request_priority', { track_uid: trackUid });
    }

    votePriority(trackUid) {
        this.send('vote_priority', { track_uid: trackUid });
    }

    cancelPriority() {
        this.send('cancel_priority');
    }

    voteSkip() {
        this.send('vote_skip');
    }

    controlPlayback(subAction, currentTime) {
        this.send('control', { sub_action: subAction, current_time: currentTime });
    }

    trackEnded(trackId, trackUid = null) {
        this.send('track_ended', { track_id: trackId, track_uid: trackUid });
    }

    toggleDjMode() {
        this.send('toggle_dj_mode');
    }

    disconnect() {
        this.manualClose = true;
        if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
        if (this.ws) {
            try { this.ws.close(1000, 'Client left room'); } catch (e) {}
        }
        this.isConnected = false;
    }
}

window.roomSocket = new RoomSocket();
