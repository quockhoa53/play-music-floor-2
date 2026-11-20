/**
 * Realtime Chat (Text, Emoji, GIF, Image), Floating Reactions & Sound Chimes
 */
// Em bé floor 2 mascot (used for every bot message, live stream bubble and typing indicator)
const EM_BE_MASCOT_URL = '/static/img/em-be-floor-2.svg';

class RoomChat {
    constructor() {
        this.container = null;
        this.soundEnabled = localStorage.getItem('floor2_sound_fx') !== 'false';
        this.audioCtx = null;
        this.isUserScrolledUp = false;
        this.unreadCount = 0;
        this.typingEl = null;
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
            return (
                /^https:\/\/media\.giphy\.com\/media\/[A-Za-z0-9]{8,28}\/giphy\.gif$/.test(url) ||
                /^\/images\/stickers\/[a-z0-9_]{1,64}\.gif$/.test(url)
            );
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
        el.className = 'flex flex-col mb-5 animate-msg-in';

        if (msg.type === 'system') {
            el.className = 'flex flex-col my-3 animate-msg-in';
            el.innerHTML = this.renderSystemMessage(msg.text || '', timeStr);
        } else {
            const isAi = !!(msg.is_ai || msg.user_id === 'ai_em_be_floor_2');
            const safeColor = /^#[0-9a-fA-F]{6}$/.test(msg.user_color || '') ? msg.user_color : (isAi ? '#ec4899' : '#4f46e5');
            const safeAvatar = this.escapeHtml(msg.user_avatar || (isAi ? '🎀' : '🎧'));
            const hostBadge = isAi
                ? `<span class="inline-flex items-center gap-0.5 px-1.5 py-0.2 rounded-full bg-gradient-to-r from-pink-500/20 to-violet-500/20 text-pink-600 dark:text-pink-300 border border-pink-500/30 text-[10px] font-extrabold flex-shrink-0">✨ AI</span>`
                : (msg.is_host
                    ? `<span class="inline-flex items-center gap-0.5 px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400 text-[10px] font-bold flex-shrink-0">👑 Host</span>`
                    : '');

            const hasValidMedia = msg.media_url && this.isSafeMediaUrl(msg.media_url, msg.media_type);
            const hasText = Boolean(msg.text && msg.text.trim());

            let contentHtml = '';
            if (hasValidMedia) {
                const isLocalFloor2Sticker = msg.media_url.startsWith('/images/stickers/');
                if (msg.media_type === 'sticker' || isLocalFloor2Sticker) {
                    const sizeClass = isLocalFloor2Sticker ? 'w-36 h-36' : 'w-32 h-32';
                    contentHtml += `
                        <div class="relative p-1 max-w-[160px] cursor-zoom-in group js-chat-media-wrap">
                            <img src="${msg.media_url}" alt="${isLocalFloor2Sticker ? 'Bé Floor 2' : 'Nhãn dán'}" loading="lazy"
                                 class="js-chat-media-img block ${sizeClass} object-contain drop-shadow-md transition-transform duration-200 group-hover:scale-105">
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
                            : (isAi
                                ? 'chat-bubble-other rounded-bl-sm ring-1 ring-pink-500/35 bg-gradient-to-br from-pink-500/10 via-violet-500/10 to-indigo-500/5'
                                : 'chat-bubble-other rounded-bl-sm')
                    }">
                        ${isAi ? this.formatAiMessage(msg.text) : this.formatMessageWithMentions(msg.text, isMe)}
                    </div>
                `;
            }

            // Optional 1-click suggested tracks from "@Em bé floor 2"
            if (isAi && Array.isArray(msg.suggested_tracks) && msg.suggested_tracks.length > 0) {
                const tracksHtml = msg.suggested_tracks.slice(0, 3).map((t, idx) => {
                    const safeVid = /^[a-zA-Z0-9_-]{11}$/.test(t.id || '') ? t.id : '';
                    const thumb = safeVid ? `https://i.ytimg.com/vi/${safeVid}/hqdefault.jpg` : '';
                    return `
                        <div class="js-ai-chat-track surface-subtle flex items-center justify-between gap-2 p-1.5 rounded-xl border border-pink-500/25" data-track-idx="${idx}">
                            <div class="flex items-center gap-2 min-w-0 flex-1">
                                ${thumb ? `<img src="${thumb}" alt="" class="w-9 h-9 rounded-lg object-cover flex-shrink-0">` : ''}
                                <div class="min-w-0 flex-1">
                                    <div class="text-[11.5px] font-bold text-main truncate">${this.escapeHtml(t.title || '')}</div>
                                    <div class="text-[10px] text-sub truncate">${this.escapeHtml(t.author || '')}</div>
                                </div>
                            </div>
                            <button type="button" class="js-btn-add-ai-track px-2.5 py-1 rounded-lg btn-gradient text-[11px] font-bold flex-shrink-0" data-track-idx="${idx}" data-video-id="${safeVid}">
                                + Thêm
                            </button>
                        </div>
                    `;
                }).join('');
                contentHtml += `<div class="mt-1.5 w-full space-y-1">${tracksHtml}</div>`;
            }

            el.innerHTML = `
                <div class="flex items-end gap-2 min-w-0 ${isMe ? 'flex-row-reverse' : ''}">
                    ${isAi
                        ? `<img src="${EM_BE_MASCOT_URL}" alt="Em bé floor 2" class="em-be-mascot w-9 h-9 flex-shrink-0" loading="lazy">`
                        : `<div class="w-8 h-8 rounded-full flex items-center justify-center text-sm shadow-sm flex-shrink-0"
                         style="background-color: ${safeColor}22; border: 1.5px solid ${safeColor}88">
                        ${safeAvatar}
                    </div>`}
                    <div class="max-w-[82%] min-w-0 flex flex-col ${isMe ? 'items-end' : 'items-start'}">
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

            if (isAi && Array.isArray(msg.suggested_tracks) && msg.suggested_tracks.length > 0) {
                el.querySelectorAll('.js-btn-add-ai-track').forEach(btn => {
                    this.applyAiTrackButtonState(btn);
                    btn.addEventListener('click', () => {
                        if (btn.disabled) return;
                        const idx = parseInt(btn.getAttribute('data-track-idx') || '0', 10);
                        const track = msg.suggested_tracks[idx];
                        if (!track) return;
                        // Already playing / queued (e.g. the bot just added it): never add a duplicate
                        if (this.getTrackRoomStatus(track.id)) {
                            this.applyAiTrackButtonState(btn);
                            return;
                        }
                        if (typeof window.canAddTrackInCurrentRoom === 'function' && !window.canAddTrackInCurrentRoom()) {
                            return;
                        }
                        window.roomSocket.addTrack(track);
                        // Lock immediately; the next queue update keeps it locked with the real status
                        this.setAiTrackButton(btn, 'pending');
                    });
                });
            }

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

        const safeMsgId = String(msg.id || '').replace(/[^a-zA-Z0-9_-]/g, '');
        if (safeMsgId) el.dataset.msgId = safeMsgId;
        // A streamed AI reply finishing: swap the live bubble for the final message in place
        const streaming = safeMsgId ? this.container.querySelector(`.js-ai-stream[data-msg-id="${safeMsgId}"]`) : null;
        if (streaming) {
            el.classList.remove('animate-msg-in');
            streaming.replaceWith(el);
        } else {
            this.container.appendChild(el);
        }
        if (this.typingEl && this.typingEl.parentNode === this.container) {
            this.container.appendChild(this.typingEl);
        }

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
        this.typingEl = null;
        this.updateUnreadBadges();
        if (this.container) {
            this.container.innerHTML = '';
        }
    }

    setAiTyping(isTyping, botName = 'Em bé floor 2', status = '') {
        if (!this.container) return;
        if (!isTyping) {
            if (this.typingEl && this.typingEl.parentNode) {
                this.typingEl.remove();
            }
            this.typingEl = null;
            return;
        }
        if (!this.typingEl) {
            const el = document.createElement('div');
            el.id = 'ai-typing-indicator';
            el.className = 'flex items-center gap-2 px-2 py-1.5 text-xs text-pink-600 dark:text-pink-300 font-semibold animate-pulse';
            el.innerHTML = `
                <img src="${EM_BE_MASCOT_URL}" alt="" class="em-be-mascot em-be-mascot-bounce w-7 h-7">
                <span class="js-ai-typing-text"></span>
            `;
            this.typingEl = el;
        }
        // Tool progress ("đang tra cứu Wikipedia...") so members see what the bot is doing
        const label = this.typingEl.querySelector('.js-ai-typing-text');
        if (label) label.textContent = `${botName} ${status ? status : 'đang suy nghĩ'}...`;
        this.container.appendChild(this.typingEl);
        if (!this.isUserScrolledUp) {
            this.scrollToBottom();
        }
    }

    /** 'playing' | 'queued' | null for a video id, based on the live room state. */
    getTrackRoomStatus(videoId) {
        const rd = window.appState && window.appState.roomData;
        if (!rd || !videoId) return null;
        if (rd.current_track && rd.current_track.id === videoId) return 'playing';
        if (Array.isArray(rd.queue) && rd.queue.some(t => t && t.id === videoId)) return 'queued';
        return null;
    }

    setAiTrackButton(btn, state) {
        const base = 'js-btn-add-ai-track px-2.5 py-1 rounded-lg text-[11px] font-bold flex-shrink-0';
        const locked = 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30 cursor-default';
        const labels = { playing: '▶ Đang phát', queued: '✓ Trong hàng đợi', pending: '✓ Đã thêm' };
        if (state && labels[state]) {
            btn.textContent = labels[state];
            btn.className = `${base} ${locked}`;
            btn.disabled = true;
            btn.title = state === 'pending' ? 'Đã gửi yêu cầu thêm bài' : 'Bài này đã có trong phòng';
            btn.dataset.state = state;
        } else {
            btn.textContent = '+ Thêm';
            btn.className = `${base} btn-gradient`;
            btn.disabled = false;
            btn.title = 'Thêm bài này vào hàng đợi';
            btn.dataset.state = '';
        }
    }

    applyAiTrackButtonState(btn) {
        const status = this.getTrackRoomStatus(btn.getAttribute('data-video-id') || '');
        // A pending click stays locked until the server confirms (or the song has since finished)
        if (!status && btn.dataset.state === 'pending') return;
        this.setAiTrackButton(btn, status);
    }

    /** Re-sync every AI song card button with the room (call after queue / playback changes). */
    refreshAiTrackButtons() {
        if (!this.container) return;
        this.container.querySelectorAll('.js-btn-add-ai-track').forEach(btn => {
            if (btn.dataset.state === 'pending') btn.dataset.state = '';
            this.applyAiTrackButtonState(btn);
        });
    }

    /** Create/update the live (streaming) bubble of the AI reply; replaced by the final message later. */
    upsertAiStream(streamId, text, meta = {}) {
        if (!this.container || !streamId) return;
        const safeId = String(streamId).replace(/[^a-zA-Z0-9_-]/g, '');
        let el = this.container.querySelector(`[data-msg-id="${safeId}"]`);
        if (el && !el.classList.contains('js-ai-stream')) return; // final message already rendered
        if (!el) {
            el = document.createElement('div');
            el.className = 'flex flex-col mb-5 animate-msg-in js-ai-stream';
            el.dataset.msgId = safeId;
            const avatar = this.escapeHtml(meta.user_avatar || '🎀');
            const name = this.escapeHtml(meta.bot_name || 'Em bé floor 2');
            el.innerHTML = `
                <div class="flex items-center gap-1.5 mb-1 ml-9">
                    <span class="text-[11px] font-bold text-pink-600 dark:text-pink-300">${name}</span>
                    <span class="inline-flex items-center gap-0.5 px-1.5 py-0.2 rounded-full bg-gradient-to-r from-pink-500/20 to-violet-500/20 text-pink-600 dark:text-pink-300 border border-pink-500/30 text-[10px] font-extrabold">✨ AI</span>
                </div>
                <div class="flex items-end gap-2">
                    <img src="${EM_BE_MASCOT_URL}" alt="Em bé floor 2" class="em-be-mascot em-be-mascot-bounce w-9 h-9 flex-shrink-0">
                    <div class="js-ai-stream-body rounded-2xl rounded-bl-sm px-3.5 py-2 text-[13px] leading-relaxed max-w-full break-words [overflow-wrap:anywhere] shadow-sm chat-bubble-other ring-1 ring-pink-500/35 bg-gradient-to-br from-pink-500/10 via-violet-500/10 to-indigo-500/5"></div>
                </div>
            `;
            this.container.appendChild(el);
        }
        const body = el.querySelector('.js-ai-stream-body');
        if (body) body.innerHTML = this.formatAiMessage(text) + '<span class="ai-stream-caret">▍</span>';
        if (this.typingEl && this.typingEl.parentNode === this.container) {
            this.container.appendChild(this.typingEl);
        }
        if (!this.isUserScrolledUp) this.scrollToBottom();
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

    formatMessageWithMentions(rawText, isMe = false) {
        const safe = this.escapeHtml(rawText || '');
        if (!safe.includes('@')) return safe;

        const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const users = (window.appState && window.appState.roomData && Array.isArray(window.appState.roomData.users))
            ? window.appState.roomData.users
            : [];
        const memberPatterns = users
            .map(u => this.escapeHtml((u && u.name) || '').trim())
            .filter(Boolean)
            .sort((a, b) => b.length - a.length)
            .map(n => `@${escapeRegex(n)}`);

        const aiPattern = `@(?:Em\\s+b(?:é|e)\\s+floor\\s+2|embefloor2)`;
        const combined = new RegExp(`(${[aiPattern, ...memberPatterns].join('|')})`, 'gi');

        return safe.replace(combined, (match) => {
            const isAiTag = /^@(?:em\s+b(?:é|e)\s+floor\s+2|embefloor2)$/i.test(match);
            if (isMe) {
                return `<strong class="font-extrabold px-1.5 py-0.5 rounded-md bg-white/25 text-white">${match}</strong>`;
            }
            if (isAiTag) {
                return `<strong class="font-extrabold px-1.5 py-0.5 rounded-md bg-pink-500/15 text-pink-600 dark:text-pink-300">${match}</strong>`;
            }
            return `<strong class="font-extrabold px-1.5 py-0.5 rounded-md bg-indigo-500/15 text-indigo-600 dark:text-indigo-300">${match}</strong>`;
        });
    }

    /**
     * Safe mini-markdown for "@Em bé floor 2" replies: paragraphs, line breaks, "-"/"•" bullets,
     * "1." numbered lists, **bold** and *italic*. Text is HTML-escaped first, so no injection is possible.
     */
    formatAiMessage(rawText) {
        const text = String(rawText || '').replace(/\r\n?/g, '\n').trim();
        if (!text) return '';
        const inline = (line) => this.formatMessageWithMentions(line, false)
            .replace(/\*\*(.+?)\*\*/g, '<strong class="font-extrabold text-main">$1</strong>')
            .replace(/(^|[\s(])\*(?!\s)([^*]+?)\*(?=[\s).,!?:;]|$)/g, '$1<em>$2</em>');

        const blocks = [];
        let list = null; // { type: 'ul' | 'ol', items: [] }
        let para = [];
        const flushPara = () => {
            if (para.length) blocks.push(`<p>${para.map(inline).join('<br>')}</p>`);
            para = [];
        };
        const flushList = () => {
            if (list) {
                const items = list.items.map(it => `<li>${inline(it)}</li>`).join('');
                blocks.push(`<${list.type} class="ai-list ai-list-${list.type}">${items}</${list.type}>`);
            }
            list = null;
        };

        for (const rawLine of text.split('\n')) {
            const line = rawLine.trim();
            if (!line) { flushPara(); flushList(); continue; }
            const bullet = line.match(/^(?:[-*•]|🎵|🎶)\s+(.+)$/);
            const numbered = line.match(/^\d{1,2}[.)]\s+(.+)$/);
            if (bullet || numbered) {
                const type = bullet ? 'ul' : 'ol';
                flushPara();
                if (!list || list.type !== type) { flushList(); list = { type, items: [] }; }
                list.items.push((bullet || numbered)[1]);
            } else {
                flushList();
                para.push(line);
            }
        }
        flushPara();
        flushList();
        return `<div class="ai-rich">${blocks.join('')}</div>`;
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
