/**
 * Realtime Chat (Text, Emoji, GIF, Image), Floating Reactions & Sound Chimes
 */
class RoomChat {
    constructor() {
        this.container = null;
        this.soundEnabled = localStorage.getItem('floor2_sound_fx') !== 'false';
        this.audioCtx = null;
        this.isUserScrolledUp = false;
        this.unreadCount = 0;
    }

    init() {
        this.container = document.getElementById('chat-messages-container');
        if (this.container && !this.container.dataset.boundScroll) {
            this.container.dataset.boundScroll = '1';
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

    isSafeMediaUrl(url, mediaType) {
        if (typeof url !== 'string') return false;
        if (mediaType === 'gif' || mediaType === 'sticker') {
            return /^https:\/\/media\.giphy\.com\/media\/[A-Za-z0-9]{8,28}\/giphy\.gif$/.test(url);
        }
        if (mediaType === 'image') {
            return /^\/api\/chat\/media\/[a-f0-9]{16,32}$/.test(url);
        }
        return false;
    }

    resetUnread() {
        this.unreadCount = 0;
        this.updateUnreadBadges();
    }

    updateUnreadBadges() {
        const count = this.unreadCount;
        const text = count > 99 ? '99+' : String(count);
        document.querySelectorAll('.js-chat-unread-badge').forEach(badge => {
            if (count > 0) {
                badge.textContent = text;
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        });
    }

    addMessage(msg, isHistory = false) {
        if (!this.container) return;

        const isMe = (window.appState && window.appState.userId === msg.user_id);
        const timeStr = this.formatTime(msg.timestamp);

        const el = document.createElement('div');
        el.className = 'flex flex-col mb-3 animate-msg-in';

        if (msg.type === 'system') {
            el.className = 'flex flex-col mb-2 animate-msg-in';
            el.innerHTML = this.renderSystemMessage(msg.text || '', timeStr);
        } else {
            const safeColor = /^#[0-9a-fA-F]{6}$/.test(msg.user_color || '') ? msg.user_color : '#4f46e5';
            const safeAvatar = this.escapeHtml(msg.user_avatar || '🎧');
            const hostBadge = msg.is_host
                ? `<span class="inline-flex items-center gap-0.5 px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400 text-[10px] font-bold flex-shrink-0">👑 Host</span>`
                : '';

            const hasValidMedia = msg.media_url && this.isSafeMediaUrl(msg.media_url, msg.media_type);
            const hasText = Boolean(msg.text && msg.text.trim());

            let contentHtml = '';
            if (hasValidMedia) {
                if (msg.media_type === 'sticker') {
                    contentHtml += `
                        <div class="relative p-1 max-w-[144px] cursor-zoom-in group js-chat-media-wrap">
                            <img src="${msg.media_url}" alt="Nhãn dán" loading="lazy"
                                 class="js-chat-media-img block w-32 h-32 object-contain drop-shadow-md transition-transform duration-200 group-hover:scale-105">
                        </div>
                    `;
                } else {
                    const gifTag = msg.media_type === 'gif'
                        ? `<span class="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded-md bg-black/65 text-white text-[9px] font-extrabold tracking-wider">GIF</span>`
                        : '';
                    contentHtml += `
                        <div class="relative rounded-2xl overflow-hidden shadow-md border border-theme max-w-[230px] bg-black/10 cursor-zoom-in group js-chat-media-wrap">
                            <img src="${msg.media_url}" alt="${msg.media_type === 'gif' ? 'GIF' : 'Hình ảnh'}" loading="lazy"
                                 class="js-chat-media-img block max-w-full max-h-56 object-contain rounded-2xl transition-transform duration-200 group-hover:scale-[1.02]">
                            ${gifTag}
                        </div>
                    `;
                }
            }

            if (hasText) {
                contentHtml += `
                    <div class="${hasValidMedia ? 'mt-1.5 ' : ''}rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed max-w-full break-words [overflow-wrap:anywhere] shadow-sm ${
                        isMe
                            ? 'bg-gradient-to-br from-indigo-600 to-violet-600 text-white rounded-br-sm'
                            : 'chat-bubble-other rounded-bl-sm'
                    }">
                        ${this.escapeHtml(msg.text)}
                    </div>
                `;
            }

            el.innerHTML = `
                <div class="flex items-end gap-2 min-w-0 ${isMe ? 'flex-row-reverse' : ''}">
                    <div class="w-8 h-8 rounded-full flex items-center justify-center text-sm shadow-sm flex-shrink-0"
                         style="background-color: ${safeColor}22; border: 1.5px solid ${safeColor}88">
                        ${safeAvatar}
                    </div>
                    <div class="max-w-[78%] min-w-0 flex flex-col ${isMe ? 'items-end' : 'items-start'}">
                        <div class="flex items-center gap-1.5 mb-1 px-1 text-[11px] max-w-full">
                            <span class="font-semibold truncate" style="color: ${safeColor}">
                                ${this.escapeHtml(msg.user_name || 'Khách')}
                            </span>
                            ${hostBadge}
                            <span class="text-[10px] text-dim font-mono-num flex-shrink-0">${timeStr}</span>
                        </div>
                        ${contentHtml}
                    </div>
                </div>
            `;

            // Bind lightbox zoom & scroll-on-load for images/GIFs
            const mediaImg = el.querySelector('.js-chat-media-img');
            if (mediaImg) {
                mediaImg.addEventListener('load', () => {
                    if (!this.isUserScrolledUp || isHistory || isMe) {
                        this.scrollToBottom();
                    }
                });
                mediaImg.addEventListener('click', () => {
                    if (typeof window.openImageLightbox === 'function') {
                        window.openImageLightbox(msg.media_url, msg.user_name || '');
                    }
                });
            }
        }

        this.container.appendChild(el);

        // Keep chat DOM bounded (max 120 messages) for smooth long-session scrolling
        while (this.container.children.length > 120) {
            this.container.firstElementChild?.remove();
        }

        if (!isHistory && (!this.isUserScrolledUp || isMe)) {
            this.isUserScrolledUp = false;
            this.scrollToBottom();
        }

        // Increment unread badge if chat panel is currently collapsed
        if (!isHistory && !isMe && msg.type === 'chat' && window.appState && window.appState.isChatCollapsed) {
            this.unreadCount++;
            this.updateUnreadBadges();
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
        this.unreadCount = 0;
        this.updateUnreadBadges();
        if (this.container) {
            this.container.innerHTML = '';
        }
    }

    renderSystemMessage(rawText) {
        const text = String(rawText || '').trim();
        const esc = (s) => this.escapeHtml(s);
        const nameSpan = (s) => `<span class="font-semibold text-main">${esc(s)}</span>`;
        const cleanTitle = (s) => {
            const stripped = String(s || '')
                .replace(/\s*[\(\[][^\)\]]*(?:official|music\s*video|mv|4k|hd|lyric|audio)[^\)\]]*[\)\]]/gi, '')
                .replace(/\s*\|\s*official\s*(?:music\s*video|mv|video)\s*/gi, '')
                .replace(/\s+/g, ' ')
                .trim();
            const finalTitle = stripped || s || '';
            return finalTitle.length > 52 ? `${finalTitle.slice(0, 51)}…` : finalTitle;
        };
        const titleSpan = (s) => `<span class="text-sub">“${esc(cleanTitle(s))}”</span>`;

        const pill = (icon, bodyHtml, extraCls = '') => `
            <div class="flex items-center justify-center my-0.5 min-w-0">
                <span class="sys-event-card ${extraCls} inline-flex items-center flex-wrap justify-center gap-1 text-[11px] px-2.5 py-0.5 rounded-full leading-snug max-w-[92%] text-center break-words [overflow-wrap:anywhere]">
                    <span class="flex-shrink-0">${icon}</span>
                    <span>${bodyHtml}</span>
                </span>
            </div>
        `;

        const plainLine = (icon, bodyHtml) => `
            <div class="flex items-center justify-center my-0.5 px-3 min-w-0">
                <p class="text-[11px] text-dim text-center leading-relaxed max-w-[94%] break-words [overflow-wrap:anywhere]">
                    <span class="mr-1">${icon}</span>${bodyHtml}
                </p>
            </div>
        `;

        let m;

        // 1. Vào phòng -> pill nền xanh lá gọn
        if ((m = text.match(/^👋\s+(.+?)\s+đã vào phòng$/))) {
            return pill('👋', `${nameSpan(m[1])} <span class="text-sub">đã vào phòng</span>`, 'sys-event-emerald');
        }

        // 2. Rời phòng -> pill nền xám gọn
        if ((m = text.match(/^🏃\s+(.+?)\s+đã rời phòng(?:\.\s*👑\s+(.+?)\s+đã trở thành Chủ phòng mới)?$/))) {
            if (m[2]) {
                return pill('🏃', `${nameSpan(m[1])} <span class="text-sub">đã rời phòng • 👑</span> ${nameSpan(m[2])} <span class="text-sub">là Chủ phòng mới</span>`);
            }
            return pill('🏃', `${nameSpan(m[1])} <span class="text-sub">đã rời phòng</span>`);
        }

        // 3. Các thông báo khác -> không nền, căn giữa đồng nhất, chỉ in đậm tên
        if ((m = text.match(/^➕\s+(.+?)\s+đã thêm vào hàng đợi\s+\((#\d+)\):\s+(.+)$/))) {
            return plainLine('➕', `${nameSpan(m[1])} đã thêm ${titleSpan(m[3])} (${esc(m[2])})`);
        }

        if ((m = text.match(/^🎶\s+(.+?)\s+đã phát:\s+(.+)$/))) {
            return plainLine('🎶', `${nameSpan(m[1])} đã phát ${titleSpan(m[2])}`);
        }

        if ((m = text.match(/^⚡\s+(.+?)\s+yêu cầu ưu tiên bài\s+"(.+?)"\s+\((\d+\/\d+\s+phiếu)\)/))) {
            return plainLine('⚡', `${nameSpan(m[1])} xin ưu tiên ${titleSpan(m[2])} (${esc(m[3])})`);
        }

        if ((m = text.match(/^🚀\s+Đã đủ\s+(\d+\/\d+\s+phiếu)!\s+Bài\s+"(.+?)"\s+được ưu tiên lên phát kế tiếp\s+\(#1\)!?$/))) {
            return plainLine('🚀', `Đủ ${esc(m[1])} • ${titleSpan(m[2])} lên #1 kế tiếp`);
        }

        if ((m = text.match(/^✖️\s+(.+?)\s+đã hủy yêu cầu ưu tiên bài\s+"(.+?)"$/))) {
            return plainLine('✖️', `${nameSpan(m[1])} đã hủy ưu tiên ${titleSpan(m[2])}`);
        }

        if ((m = text.match(/^⏭️\s+(.+?)\s+đề xuất qua bài\s+"(.+?)"\s+\((\d+\/\d+\s+phiếu)\)/))) {
            return plainLine('⏭️', `${nameSpan(m[1])} vote qua bài ${titleSpan(m[2])} (${esc(m[3])})`);
        }

        if ((m = text.match(/^⏭️\s+Đã đủ\s+(\d+\/\d+\s+phiếu)\s+bỏ qua!\s+Chuyển sang bài:\s+(.+)$/))) {
            return plainLine('⏭️', `Đủ ${esc(m[1])} qua bài • Phát ${titleSpan(m[2])}`);
        }

        if ((m = text.match(/^⏭️\s+Đã đủ\s+(\d+\/\d+\s+phiếu)\s+bỏ qua bài\s+"(.+?)"\.\s+(.+)$/))) {
            return plainLine('⏭️', `Đủ ${esc(m[1])} bỏ qua ${titleSpan(m[2])}`);
        }

        if ((m = text.match(/^⏭️\s+Đang phát bài tiếp theo:\s+(.+)$/))) {
            return plainLine('⏭️', `Tiếp theo: ${titleSpan(m[1])}`);
        }

        if ((m = text.match(/^🗑️\s+(.+?)\s+đã xóa bài:\s+(.+)$/))) {
            return plainLine('🗑️', `${nameSpan(m[1])} đã xóa ${titleSpan(m[2])}`);
        }

        if ((m = text.match(/^🎛️\s+Chế độ DJ đã\s+(BẬT|TẮT)\s*\((.+)\)$/))) {
            return plainLine('🎛️', `Chế độ DJ: ${nameSpan(m[1])}`);
        }

        return plainLine('•', esc(text));
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
        // Cap simultaneous floating particles at 12 to keep 60fps during rapid clicks
        const existing = document.querySelectorAll('.floating-particle');
        if (existing.length >= 12) {
            existing[0]?.remove();
        }

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
        }, 2500);
    }
}

window.roomChat = new RoomChat();
