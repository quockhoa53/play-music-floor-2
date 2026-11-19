/**
 * Realtime Chat, Floating Emoji Reactions & Sound Chimes (Dual-Theme Ready)
 */
class RoomChat {
    constructor() {
        this.container = null;
        this.soundEnabled = localStorage.getItem('floor2_sound_fx') !== 'false';
        this.audioCtx = null;
        this.isUserScrolledUp = false;
    }

    init() {
        this.container = document.getElementById('chat-messages-container');
        if (this.container) {
            this.container.addEventListener('scroll', () => {
                const threshold = 60;
                const atBottom = (this.container.scrollHeight - this.container.scrollTop - this.container.clientHeight) < threshold;
                this.isUserScrolledUp = !atBottom;
            });
        }
    }

    toggleSound() {
        this.soundEnabled = !this.soundEnabled;
        localStorage.setItem('floor2_sound_fx', this.soundEnabled ? 'true' : 'false');
        return this.soundEnabled;
    }

    playDing(type = 'chat') {
        if (!this.soundEnabled) return;
        try {
            if (!this.audioCtx) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                this.audioCtx = new AudioContext();
            }
            if (this.audioCtx.state === 'suspended') {
                this.audioCtx.resume();
            }

            const now = this.audioCtx.currentTime;
            const osc = this.audioCtx.createOscillator();
            const gain = this.audioCtx.createGain();

            osc.connect(gain);
            gain.connect(this.audioCtx.destination);

            if (type === 'chat') {
                osc.type = 'sine';
                osc.frequency.setValueAtTime(1046.5, now);
                osc.frequency.exponentialRampToValueAtTime(1567.98, now + 0.12);
                gain.gain.setValueAtTime(0.055, now);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.24);
                osc.start(now);
                osc.stop(now + 0.24);
            } else if (type === 'join') {
                osc.type = 'triangle';
                osc.frequency.setValueAtTime(523.25, now);
                osc.frequency.exponentialRampToValueAtTime(659.25, now + 0.14);
                gain.gain.setValueAtTime(0.07, now);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.32);
                osc.start(now);
                osc.stop(now + 0.32);
            }
        } catch (e) {}
    }

    addMessage(msg, isHistory = false) {
        if (!this.container) return;

        const isMe = (window.appState && window.appState.userId === msg.user_id);
        const timeStr = this.formatTime(msg.timestamp);

        const el = document.createElement('div');
        el.className = 'flex flex-col mb-3 animate-msg-in';

        if (msg.type === 'system') {
            el.innerHTML = `
                <div class="flex items-center justify-center my-0.5 min-w-0">
                    <span class="chat-system-pill text-[11px] font-medium px-3 py-1 rounded-2xl text-center leading-snug max-w-[92%] break-words [overflow-wrap:anywhere]">
                        ${this.escapeHtml(msg.text)}
                    </span>
                </div>
            `;
        } else {
            const hostBadge = msg.is_host
                ? `<span class="inline-flex items-center gap-0.5 px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400 text-[10px] font-bold flex-shrink-0">👑 Host</span>`
                : '';

            el.innerHTML = `
                <div class="flex items-end gap-2 min-w-0 ${isMe ? 'flex-row-reverse' : ''}">
                    <div class="w-8 h-8 rounded-full flex items-center justify-center text-sm shadow-sm flex-shrink-0"
                         style="background-color: ${msg.user_color || '#6366f1'}22; border: 1.5px solid ${msg.user_color || '#6366f1'}88">
                        ${msg.user_avatar || '🎧'}
                    </div>
                    <div class="max-w-[78%] min-w-0 flex flex-col ${isMe ? 'items-end' : 'items-start'}">
                        <div class="flex items-center gap-1.5 mb-1 px-1 text-[11px] max-w-full">
                            <span class="font-semibold truncate" style="color: ${msg.user_color || 'var(--accent-primary)'}">
                                ${this.escapeHtml(msg.user_name || 'Khách')}
                            </span>
                            ${hostBadge}
                            <span class="text-[10px] text-dim font-mono-num flex-shrink-0">${timeStr}</span>
                        </div>
                        <div class="rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed max-w-full break-words [overflow-wrap:anywhere] shadow-sm ${
                            isMe
                                ? 'bg-gradient-to-br from-indigo-600 to-violet-600 text-white rounded-br-sm'
                                : 'chat-bubble-other rounded-bl-sm'
                        }">
                            ${this.escapeHtml(msg.text)}
                        </div>
                    </div>
                </div>
            `;
        }

        this.container.appendChild(el);

        if (!this.isUserScrolledUp || isHistory || isMe) {
            this.isUserScrolledUp = false;
            this.scrollToBottom();
        }

        if (!isHistory && !isMe) {
            this.playDing(msg.type === 'system' ? 'join' : 'chat');
        }
    }

    scrollToBottom() {
        if (this.container) {
            this.container.scrollTop = this.container.scrollHeight;
            requestAnimationFrame(() => {
                if (this.container) {
                    this.container.scrollTop = this.container.scrollHeight;
                }
            });
        }
    }

    clear() {
        this.isUserScrolledUp = false;
        if (this.container) {
            this.container.innerHTML = '';
        }
    }

    formatTime(ts) {
        if (!ts) return '';
        const d = new Date(ts * 1000);
        return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
    }

    escapeHtml(str) {
        if (!str) return '';
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    spawnFloatingEmoji(emoji) {
        const particle = document.createElement('div');
        particle.className = 'floating-particle';
        particle.textContent = emoji;

        const leftPercent = 14 + Math.random() * 72;
        particle.style.left = `${leftPercent}%`;

        document.body.appendChild(particle);

        setTimeout(() => {
            if (particle.parentNode) {
                particle.parentNode.removeChild(particle);
            }
        }, 2700);
    }
}

window.roomChat = new RoomChat();
