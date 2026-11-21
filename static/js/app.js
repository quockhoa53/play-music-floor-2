/**
 * Master Application Controller — Floor 2 Music Studio
 * Dual Theme (Light/Dark) + Realtime As-You-Type YouTube Search
 */

const AVATAR_OPTIONS = ['🎧', '🎵', '🎸', '🎹', '🦊', '🐱', '🐼', '🐯', '🚀', '👑', '⚡', '☕', '🍉', '🔥', '💃', '🕺'];
const COLOR_OPTIONS = ['#4f46e5', '#db2777', '#7c3aed', '#059669', '#d97706', '#0891b2', '#e11d48', '#2563eb'];

const YT_URL_REGEX = /(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/|music\.youtube\.com\/watch\?v=)([a-zA-Z0-9_-]{11})|^([a-zA-Z0-9_-]{11})$/;

window.appState = {
    sessionToken: localStorage.getItem('floor2_session_token') || null,
    userId: null,
    userName: localStorage.getItem('floor2_uname') || getRandomName(),
    userAvatar: AVATAR_OPTIONS.includes(localStorage.getItem('floor2_avatar')) ? localStorage.getItem('floor2_avatar') : '🎧',
    userColor: /^#[0-9a-fA-F]{6}$/.test(localStorage.getItem('floor2_color') || '') ? localStorage.getItem('floor2_color') : '#4f46e5',
    theme: localStorage.getItem('floor2_theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
    roomId: null,
    inviteToken: null,
    roomData: null,
    isHost: false,
    isVideoHidden: localStorage.getItem('floor2_video_hidden') === 'true',
    // On mobile (< 1024px), always start with chat sheet closed so the music stage is immediately visible
    isChatCollapsed: window.innerWidth < 1024 ? true : (localStorage.getItem('floor2_chat_collapsed') === 'true'),
    mcEnabled: false,
    suggestions: [],
    pendingPrivateRoomId: null,
    pendingImageFile: null,
};

// Realtime search state
const searchState = {
    debounceTimer: null,
    abortController: null,
    results: [],
    highlightedIndex: -1,
    addedIds: new Set(),
    lastQuery: '',
    isOpen: false,
    cache: new Map(), // client-side instant cache: queryLower -> { ts, results }
};

function safeColor(val) {
    return /^#[0-9a-fA-F]{6}$/.test(val || '') ? val : '#4f46e5';
}

function safeThumbUrl(videoId) {
    if (typeof videoId === 'string' && /^[a-zA-Z0-9_-]{11}$/.test(videoId)) {
        return `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`;
    }
    return 'https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=300&q=80';
}

// Random secret identifying this browser for Em bé floor 2's long-term memory (taste, things you told it).
// Only a salted hash ever leaves the server side; clearing site data = a fresh, memory-less profile.
function getDeviceKey() {
    const KEY = 'floor2_device_key';
    try {
        let k = localStorage.getItem(KEY);
        if (!k || !/^[A-Za-z0-9_-]{24,128}$/.test(k)) {
            const bytes = new Uint8Array(24);
            crypto.getRandomValues(bytes);
            k = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
            localStorage.setItem(KEY, k);
        }
        return k;
    } catch (_) {
        return '';
    }
}

async function ensureSession(forceNew = false, verifyServer = false) {
    if (!forceNew && !verifyServer && window.appState.sessionToken && window.appState.userId) {
        return window.appState.sessionToken;
    }
    const headers = { 'Content-Type': 'application/json' };
    const deviceKey = getDeviceKey();
    if (deviceKey) headers['X-Device-Key'] = deviceKey;
    if (!forceNew && window.appState.sessionToken) {
        headers['X-Session-Token'] = window.appState.sessionToken;
    }
    const res = await fetch('/api/session', { method: 'POST', headers });
    if (!res.ok) {
        throw new Error('Không thể khởi tạo phiên làm việc bảo mật');
    }
    const data = await res.json();
    window.appState.sessionToken = data.token;
    window.appState.userId = data.user_id;
    localStorage.setItem('floor2_session_token', data.token);
    return data.token;
}

function getAuthHeaders(extra = {}) {
    const h = { ...extra };
    if (window.appState.sessionToken) {
        h['X-Session-Token'] = window.appState.sessionToken;
    }
    return h;
}

function getRandomName() {
    const adjectives = ['Chill', 'Vui Vẻ', 'Mộng Mơ', 'Bí Ẩn', 'Sôi Nổi', 'Đáng Yêu', 'Ngầu', 'Nhẹ Nhàng'];
    const nouns = ['Mèo Con', 'Cáo Nhỏ', 'Gấu Bắc Cực', 'Họa Sĩ', 'DJ', 'Phi Hành Gia', 'Thỏ Trắng', 'Cú Đêm'];
    const randAdj = adjectives[Math.floor(Math.random() * adjectives.length)];
    const randNoun = nouns[Math.floor(Math.random() * nouns.length)];
    const num = Math.floor(Math.random() * 90) + 10;
    return `${randAdj} ${randNoun} ${num}`;
}

// =============================================================================
// THEME ENGINE (LIGHT / DARK)
// =============================================================================
function applyTheme(theme) {
    window.appState.theme = theme;
    localStorage.setItem('floor2_theme', theme);
    const root = document.documentElement;
    if (theme === 'dark') {
        root.classList.add('dark');
    } else {
        root.classList.remove('dark');
    }
    updateThemeButtonsUI();
    if (window.syncPlayer) {
        window.syncPlayer.updateVolumeSliderFill();
    }
}

function toggleTheme() {
    const next = window.appState.theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
}

function updateThemeButtonsUI() {
    const isDark = window.appState.theme === 'dark';
    const sunIcon = `<svg class="w-4 h-4 text-amber-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path stroke-linecap="round" d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32l1.41-1.41"/></svg>`;
    const moonIcon = `<svg class="w-4 h-4 text-indigo-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>`;

    document.querySelectorAll('.js-theme-toggle').forEach(btn => {
        const iconSlot = btn.querySelector('.js-theme-icon');
        const labelSlot = btn.querySelector('.js-theme-label');
        if (iconSlot) iconSlot.innerHTML = isDark ? sunIcon : moonIcon;
        if (labelSlot) labelSlot.textContent = isDark ? 'Nền sáng' : 'Nền tối';
        btn.title = isDark ? 'Chuyển sang giao diện Sáng' : 'Chuyển sang giao diện Tối';
    });
}

// =============================================================================
// TOAST NOTIFICATIONS (BOTTOM-LEFT NON-OBSTRUCTIVE, ZERO-LAG, ANTI-SPAM)
// =============================================================================
const TOAST_META = {
    success: {
        title: 'Thành công',
        iconClass: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
        barClass: 'bg-emerald-500',
        svg: `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>`,
    },
    info: {
        title: 'Thông báo',
        iconClass: 'bg-indigo-500/15 text-indigo-600 dark:text-indigo-400',
        barClass: 'bg-indigo-500',
        svg: `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
    },
    warning: {
        title: 'Lưu ý',
        iconClass: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
        barClass: 'bg-amber-500',
        svg: `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>`,
    },
    error: {
        title: 'Có lỗi xảy ra',
        iconClass: 'bg-rose-500/15 text-rose-600 dark:text-rose-400',
        barClass: 'bg-rose-500',
        svg: `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.4" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
    },
};

const activeToastsByKey = new Map();
const MAX_VISIBLE_TOASTS = 2;

function showToast(message, type = 'info', options = {}) {
    const container = document.getElementById('toast-container');
    const tpl = document.getElementById('toast-template');
    if (!container) return;

    const cleanMsg = String(message || '').trim();
    if (!cleanMsg) return;

    const meta = TOAST_META[type] || TOAST_META.info;
    const duration = Number(options.duration || 2400);
    const title = options.title || meta.title;
    const dedupKey = `${type}::${cleanMsg}`;

    // Anti-spam deduplication: bump counter badge & refresh timer instead of spawning duplicate toasts
    const existing = activeToastsByKey.get(dedupKey);
    if (existing && existing.el && existing.el.isConnected) {
        existing.count += 1;
        existing.bump(duration);
        return;
    }

    // Strictly cap visible toasts at 2 to prevent UI clutter or spam lag
    while (container.children.length >= MAX_VISIBLE_TOASTS) {
        const oldest = container.firstElementChild;
        if (!oldest) break;
        if (oldest._toastKey) activeToastsByKey.delete(oldest._toastKey);
        if (oldest._toastTimer) clearTimeout(oldest._toastTimer);
        oldest.remove();
    }

    let toast;
    if (tpl && tpl.content && tpl.content.firstElementChild) {
        toast = tpl.content.firstElementChild.cloneNode(true);
    } else {
        toast = document.createElement('div');
        toast.className = 'toast-item rounded-2xl p-3 pr-2.5 flex items-start gap-2.5';
        toast.innerHTML = `
            <div class="js-toast-icon w-7 h-7 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5"></div>
            <div class="min-w-0 flex-1">
                <div class="flex items-center gap-1.5">
                    <div class="js-toast-title text-[11.5px] font-extrabold tracking-tight text-main leading-tight"></div>
                    <span class="js-toast-dup hidden px-1.5 py-0.2 rounded-full bg-indigo-500/15 text-indigo-600 dark:text-indigo-300 text-[10px] font-mono-num font-bold">×2</span>
                </div>
                <p class="js-toast-message text-[11.5px] text-sub leading-snug mt-0.5 line-clamp-2 break-words"></p>
            </div>
            <button type="button" class="js-toast-close w-6 h-6 rounded-lg text-dim hover:text-main flex items-center justify-center text-xs transition flex-shrink-0">✕</button>
            <div class="js-toast-progress toast-progress-bar"></div>
        `;
    }

    toast._toastKey = dedupKey;
    const iconEl = toast.querySelector('.js-toast-icon');
    const titleEl = toast.querySelector('.js-toast-title');
    const dupEl = toast.querySelector('.js-toast-dup');
    const msgEl = toast.querySelector('.js-toast-message');
    const closeBtn = toast.querySelector('.js-toast-close');
    const barEl = toast.querySelector('.js-toast-progress');

    if (iconEl) {
        iconEl.className = `js-toast-icon w-7 h-7 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5 ${meta.iconClass}`;
        iconEl.innerHTML = meta.svg;
    }
    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = cleanMsg;
    if (barEl) {
        barEl.className = `js-toast-progress toast-progress-bar ${meta.barClass}`;
        barEl.style.animationDuration = `${duration}ms`;
    }

    let dismissed = false;
    let remainingMs = duration;
    let startedAt = Date.now();
    let timerId = null;

    const dismiss = () => {
        if (dismissed) return;
        dismissed = true;
        if (timerId) clearTimeout(timerId);
        activeToastsByKey.delete(dedupKey);
        toast.classList.add('toast-leaving');
        setTimeout(() => {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 190);
    };

    const startTimer = () => {
        startedAt = Date.now();
        timerId = setTimeout(dismiss, Math.max(200, remainingMs));
        toast._toastTimer = timerId;
    };

    const entry = {
        el: toast,
        count: 1,
        bump(newDuration) {
            if (dismissed) return;
            if (timerId) clearTimeout(timerId);
            remainingMs = newDuration;
            if (dupEl) {
                dupEl.textContent = `×${this.count}`;
                dupEl.classList.remove('hidden');
            }
            if (barEl) {
                barEl.style.animation = 'none';
                void barEl.offsetWidth;
                barEl.style.animation = '';
                barEl.style.animationDuration = `${newDuration}ms`;
            }
            startTimer();
        },
    };
    activeToastsByKey.set(dedupKey, entry);

    toast.addEventListener('mouseenter', () => {
        if (timerId) {
            clearTimeout(timerId);
            timerId = null;
            remainingMs = Math.max(200, remainingMs - (Date.now() - startedAt));
        }
    });

    toast.addEventListener('mouseleave', () => {
        if (!dismissed && !timerId) startTimer();
    });

    if (closeBtn) closeBtn.onclick = dismiss;

    container.appendChild(toast);
    startTimer();
}
window.showToast = showToast;

// =============================================================================
// REUSABLE CONFIRMATION DIALOG MODAL (REPLACES NATIVE window.confirm())
// =============================================================================
function showConfirmDialog({
    title = 'Xác nhận thao tác',
    message = 'Bạn có chắc chắn muốn tiếp tục?',
    confirmText = 'Xác nhận',
    cancelText = 'Hủy',
    variant = 'danger',
} = {}) {
    const modal = document.getElementById('confirm-dialog-modal');
    if (!modal) return Promise.resolve(false);

    const titleEl = document.getElementById('confirm-dialog-title');
    const msgEl = document.getElementById('confirm-dialog-message');
    const iconEl = document.getElementById('confirm-dialog-icon');
    const okBtn = document.getElementById('btn-ok-confirm-dialog');
    const cancelBtn = document.getElementById('btn-cancel-confirm-dialog');
    const closeBtn = document.getElementById('btn-close-confirm-dialog');

    const variantStyles = {
        danger: {
            iconClass: 'w-11 h-11 rounded-2xl flex items-center justify-center flex-shrink-0 bg-rose-500/15 text-rose-500',
            iconSvg: `<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>`,
            okClass: 'px-5 py-2.5 rounded-xl text-xs font-bold text-white bg-rose-500 hover:bg-rose-600 shadow-md transition',
        },
        warning: {
            iconClass: 'w-11 h-11 rounded-2xl flex items-center justify-center flex-shrink-0 bg-amber-500/15 text-amber-500',
            iconSvg: `<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>`,
            okClass: 'px-5 py-2.5 rounded-xl text-xs font-bold text-slate-950 bg-amber-500 hover:bg-amber-600 shadow-md transition',
        },
        primary: {
            iconClass: 'w-11 h-11 rounded-2xl flex items-center justify-center flex-shrink-0 bg-indigo-500/15 text-indigo-500',
            iconSvg: `<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
            okClass: 'btn-gradient px-5 py-2.5 rounded-xl text-xs font-bold shadow-md transition',
        },
    };

    const style = variantStyles[variant] || variantStyles.danger;
    if (titleEl) titleEl.textContent = title;
    if (msgEl) msgEl.textContent = message;
    if (iconEl) {
        iconEl.className = style.iconClass;
        iconEl.innerHTML = style.iconSvg;
    }
    if (okBtn) {
        okBtn.className = style.okClass;
        okBtn.textContent = confirmText;
    }
    if (cancelBtn) cancelBtn.textContent = cancelText;

    return new Promise((resolve) => {
        let settled = false;
        const finish = (result) => {
            if (settled) return;
            settled = true;
            modal.classList.add('hidden');
            document.removeEventListener('keydown', onKeyDown);
            modal.removeEventListener('click', onBackdropClick);
            okBtn?.removeEventListener('click', onOk);
            cancelBtn?.removeEventListener('click', onCancel);
            closeBtn?.removeEventListener('click', onCancel);
            resolve(result);
        };

        const onOk = () => finish(true);
        const onCancel = () => finish(false);
        const onBackdropClick = (e) => {
            if (e.target === modal) finish(false);
        };
        const onKeyDown = (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                finish(false);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                finish(true);
            }
        };

        okBtn?.addEventListener('click', onOk);
        cancelBtn?.addEventListener('click', onCancel);
        closeBtn?.addEventListener('click', onCancel);
        modal.addEventListener('click', onBackdropClick);
        document.addEventListener('keydown', onKeyDown);

        modal.classList.remove('hidden');
        okBtn?.focus();
    });
}
window.showConfirmDialog = showConfirmDialog;

// =============================================================================
// INITIALIZATION
// =============================================================================
document.addEventListener('DOMContentLoaded', async () => {
    applyTheme(window.appState.theme);

    document.querySelectorAll('.js-theme-toggle').forEach(btn => {
        btn.addEventListener('click', toggleTheme);
    });

    initLobbyTabs();
    initPasscodeModal();
    initProfileUI();
    initPlayerListeners();
    initSocketEvents();
    initRoomControls();
    initChatCollapse();
    initChatRichMedia();
    initLightbox();
    initRealtimeSearch();
    initQuickSuggestions();
    initAiFeatures();
    loadPublicRooms();

    try {
        await ensureSession();
    } catch (e) {
        console.error('Session bootstrap error:', e);
    }

    // Direct room join via ?room=xxx(&invite=yyy)
    const urlParams = new URLSearchParams(window.location.search);
    const roomParam = urlParams.get('room');
    const inviteParam = urlParams.get('invite');
    if (roomParam) {
        const joinInput = document.getElementById('input-join-room-id');
        if (joinInput) joinInput.value = roomParam;
        joinRoom(roomParam, inviteParam);
    }
});

// =============================================================================
// LOBBY TABS, PRIVATE ROOM CONTROLS & PASSCODE MODAL
// =============================================================================
function initLobbyTabs() {
    const tabCreate = document.getElementById('lobby-tab-create');
    const tabJoin = document.getElementById('lobby-tab-join');
    const paneCreate = document.getElementById('lobby-pane-create');
    const paneJoin = document.getElementById('lobby-pane-join');
    const privateCb = document.getElementById('create-room-private');
    const passWrap = document.getElementById('create-room-passcode-wrap');

    if (privateCb && passWrap) {
        privateCb.addEventListener('change', () => {
            passWrap.classList.toggle('hidden', !privateCb.checked);
            if (!privateCb.checked) {
                const inp = document.getElementById('create-room-passcode');
                if (inp) inp.value = '';
            }
        });
    }

    if (tabCreate && tabJoin && paneCreate && paneJoin) {
        tabCreate.onclick = () => {
            tabCreate.classList.add('active');
            tabJoin.classList.remove('active');
            paneCreate.classList.remove('hidden');
            paneJoin.classList.add('hidden');
        };
        tabJoin.onclick = () => {
            tabJoin.classList.add('active');
            tabCreate.classList.remove('active');
            paneJoin.classList.remove('hidden');
            paneCreate.classList.add('hidden');
            document.getElementById('input-join-room-id')?.focus();
        };
    }
}

function openPasscodeModal(roomId, hasPasscode = true) {
    const modal = document.getElementById('passcode-modal');
    const input = document.getElementById('passcode-modal-input');
    const err = document.getElementById('passcode-modal-error');
    const desc = document.getElementById('passcode-modal-desc');
    if (!modal) return;

    window.appState.pendingPrivateRoomId = roomId;
    if (err) { err.textContent = ''; err.classList.add('hidden'); }
    if (input) input.value = '';
    if (desc) {
        desc.textContent = hasPasscode
            ? `Phòng #${roomId} là phòng riêng tư có mật khẩu. Vui lòng nhập mật khẩu để tham gia.`
            : `Phòng #${roomId} là phòng riêng tư chỉ cho phép truy cập qua liên kết mời từ Chủ phòng.`;
    }
    if (input) input.classList.toggle('hidden', !hasPasscode);
    modal.classList.remove('hidden');
    if (hasPasscode && input) input.focus();
}

function closePasscodeModal() {
    const modal = document.getElementById('passcode-modal');
    if (modal) modal.classList.add('hidden');
    window.appState.pendingPrivateRoomId = null;
}

function initPasscodeModal() {
    document.getElementById('btn-close-passcode-modal')?.addEventListener('click', closePasscodeModal);
    document.getElementById('btn-cancel-passcode-modal')?.addEventListener('click', closePasscodeModal);

    const form = document.getElementById('passcode-modal-form');
    if (form) {
        form.onsubmit = async (e) => {
            e.preventDefault();
            const roomId = window.appState.pendingPrivateRoomId;
            const input = document.getElementById('passcode-modal-input');
            const err = document.getElementById('passcode-modal-error');
            if (!roomId || !input) return;

            const passcode = input.value.trim();
            if (!passcode) {
                if (err) { err.textContent = 'Vui lòng nhập mật khẩu phòng'; err.classList.remove('hidden'); }
                return;
            }

            try {
                await ensureSession();
                const res = await fetch(`/api/rooms/${encodeURIComponent(roomId)}/access`, {
                    method: 'POST',
                    headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ passcode }),
                });
                const data = await res.json();
                if (!res.ok) {
                    if (err) { err.textContent = data.detail || 'Mật khẩu không đúng'; err.classList.remove('hidden'); }
                    return;
                }
                closePasscodeModal();
                joinRoom(roomId);
            } catch (ex) {
                if (err) { err.textContent = 'Lỗi xác thực quyền truy cập'; err.classList.remove('hidden'); }
            }
        };
    }
}

function updateProfilePreview() {
    const avatarEl = document.getElementById('profile-preview-avatar');
    const nameEl = document.getElementById('profile-preview-name');
    if (avatarEl) {
        avatarEl.textContent = window.appState.userAvatar;
        avatarEl.style.backgroundColor = `${window.appState.userColor}22`;
        avatarEl.style.borderColor = `${window.appState.userColor}88`;
    }
    if (nameEl) {
        nameEl.textContent = window.appState.userName;
        nameEl.style.color = window.appState.userColor;
    }
}

function initProfileUI() {
    const nameInput = document.getElementById('profile-name-input');
    const avatarGrid = document.getElementById('avatar-selector-grid');
    const colorGrid = document.getElementById('color-selector-grid');

    if (nameInput && !nameInput.dataset.bound) {
        nameInput.value = window.appState.userName;
        nameInput.addEventListener('input', (e) => {
            window.appState.userName = e.target.value.trim() || 'Khách';
            localStorage.setItem('floor2_uname', window.appState.userName);
            updateProfilePreview();
        });
        nameInput.dataset.bound = '1';
    }

    if (avatarGrid) {
        avatarGrid.innerHTML = '';
        AVATAR_OPTIONS.forEach(emoji => {
            const isSelected = window.appState.userAvatar === emoji;
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = `h-10 rounded-xl flex items-center justify-center text-lg transition-all duration-150 ${
                isSelected
                    ? 'bg-indigo-500/15 border-2 border-indigo-500 scale-105 shadow-sm'
                    : 'surface-subtle hover:scale-105'
            }`;
            btn.textContent = emoji;
            btn.onclick = () => {
                window.appState.userAvatar = emoji;
                localStorage.setItem('floor2_avatar', emoji);
                initProfileUI();
            };
            avatarGrid.appendChild(btn);
        });
    }

    if (colorGrid) {
        colorGrid.innerHTML = '';
        COLOR_OPTIONS.forEach(c => {
            const isSelected = window.appState.userColor === c;
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = `w-7 h-7 rounded-full transition-all duration-150 flex items-center justify-center ${
                isSelected ? 'ring-2 ring-offset-2 ring-indigo-500 dark:ring-offset-slate-900 scale-110' : 'hover:scale-110 opacity-85 hover:opacity-100'
            }`;
            btn.style.backgroundColor = c;
            if (isSelected) {
                btn.innerHTML = `<svg class="w-3.5 h-3.5 text-white" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>`;
            }
            btn.onclick = () => {
                window.appState.userColor = c;
                localStorage.setItem('floor2_color', c);
                initProfileUI();
            };
            colorGrid.appendChild(btn);
        });
    }

    updateProfilePreview();
}

// =============================================================================
// LOBBY & PUBLIC ROOMS
// =============================================================================
// Lobby shows the 5 busiest public rooms (server-ranked) + the caller's own rooms; search covers all rooms
window.lobbyRoomQuery = '';
let lobbyRoomsAbort = null;

function renderLobbyRoomItem(r) {
    const item = document.createElement('div');
    item.className = 'surface-subtle p-3 sm:p-3.5 rounded-2xl flex items-center justify-between gap-2.5 sm:gap-3 cursor-pointer group';

    const playingTitle = r.current_track ? r.current_track.title : 'Đang chờ thêm nhạc...';
    const isPlaying = r.status === 'playing';
    const thumbHtml = r.current_track
        ? `<img src="${safeThumbUrl(r.current_track.id)}" alt="" class="w-10 h-10 sm:w-12 sm:h-12 rounded-xl object-cover shadow-sm flex-shrink-0">`
        : `<div class="w-10 h-10 sm:w-12 sm:h-12 rounded-xl bg-gradient-to-br from-indigo-500/20 to-pink-500/20 border border-indigo-500/20 flex items-center justify-center text-indigo-500 dark:text-indigo-300 flex-shrink-0">
             <svg class="w-4 h-4 sm:w-5 sm:h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"/></svg>
           </div>`;

    const liveBadge = isPlaying
        ? `<span class="hidden sm:inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 text-[10px] font-bold uppercase tracking-wide flex-shrink-0"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>Live</span>`
        : `<span class="hidden sm:inline-flex items-center px-2 py-0.5 rounded-full bg-slate-500/10 text-dim text-[10px] font-semibold flex-shrink-0">Sẵn sàng</span>`;
    const mineBadge = r.is_mine
        ? `<span class="inline-flex items-center px-1.5 py-0.5 rounded-full bg-indigo-500/15 text-indigo-600 dark:text-indigo-300 text-[10px] font-bold flex-shrink-0">Của bạn</span>`
        : '';
    const lockIcon = r.is_private ? '<span class="text-[11px] flex-shrink-0" title="Phòng riêng tư">🔒</span>' : '';
    const deleteBtn = r.is_mine
        ? `<button type="button" class="js-delete-room w-8 h-8 rounded-xl surface-subtle flex items-center justify-center text-sub hover:text-rose-500 hover:border-rose-500/40 flex-shrink-0" title="Xóa phòng">
             <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
           </button>`
        : '';

    item.innerHTML = `
        <div class="flex items-center gap-2.5 sm:gap-3.5 min-w-0 flex-1">
            ${thumbHtml}
            <div class="min-w-0 flex-1">
                <div class="flex items-center gap-1.5 min-w-0">
                    ${isPlaying ? '<span class="sm:hidden w-1.5 h-1.5 rounded-full bg-emerald-500 flex-shrink-0"></span>' : ''}
                    ${lockIcon}
                    <h4 class="font-bold text-main text-xs sm:text-sm truncate group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition">${escapeHtml(r.name)}</h4>
                    ${mineBadge}
                    ${liveBadge}
                </div>
                <p class="text-[11px] sm:text-xs text-sub truncate mt-0.5">${escapeHtml(playingTitle)}</p>
            </div>
        </div>
        <div class="flex items-center gap-1.5 sm:gap-2 flex-shrink-0">
            <span class="inline-flex items-center gap-1 text-[11px] sm:text-xs font-semibold text-sub px-2 sm:px-2.5 py-1 rounded-full surface-subtle">
                <svg class="w-3 h-3 sm:w-3.5 sm:h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
                ${parseInt(r.user_count, 10) || 0}
            </span>
            ${deleteBtn}
            <span class="px-3 sm:px-3.5 py-1.5 rounded-xl btn-gradient text-xs font-semibold">Vào</span>
        </div>
    `;
    item.onclick = () => joinRoom(r.id);
    item.querySelector('.js-delete-room')?.addEventListener('click', (e) => {
        e.stopPropagation();
        deleteRoomById(r.id, r.name);
    });
    return item;
}

async function loadPublicRooms(query = window.lobbyRoomQuery || '') {
    const listContainer = document.getElementById('active-rooms-list');
    const footer = document.getElementById('active-rooms-footer');
    if (!listContainer) return;
    window.lobbyRoomQuery = (query || '').trim();

    if (lobbyRoomsAbort) lobbyRoomsAbort.abort();
    const controller = new AbortController();
    lobbyRoomsAbort = controller;

    try {
        const params = new URLSearchParams();
        if (window.lobbyRoomQuery) params.set('q', window.lobbyRoomQuery);
        const res = await fetch(`/api/rooms${params.toString() ? `?${params}` : ''}`, {
            headers: getAuthHeaders(),
            signal: controller.signal,
        });
        const data = await res.json();
        if (controller.signal.aborted) return;
        const roomsList = data.rooms || [];
        const myRooms = data.my_rooms || [];
        const total = parseInt(data.total, 10) || 0;

        listContainer.innerHTML = '';
        if (roomsList.length === 0 && myRooms.length === 0) {
            listContainer.innerHTML = `
                <div class="text-center py-8 text-dim text-sm">
                    ${window.lobbyRoomQuery
                        ? `Không tìm thấy phòng nào khớp “${escapeHtml(window.lobbyRoomQuery)}”`
                        : 'Chưa có phòng nào đang mở. Hãy tạo phòng đầu tiên nhé!'}
                </div>
            `;
        }
        roomsList.forEach(r => listContainer.appendChild(renderLobbyRoomItem(r)));

        if (myRooms.length > 0) {
            const label = document.createElement('div');
            label.className = 'pt-2 pb-0.5 px-1 text-[11px] font-bold uppercase tracking-wide text-dim';
            label.textContent = 'Phòng của bạn';
            listContainer.appendChild(label);
            myRooms.forEach(r => listContainer.appendChild(renderLobbyRoomItem(r)));
        }

        if (footer) {
            let text = '';
            if (window.lobbyRoomQuery) {
                text = total > roomsList.length ? `Hiển thị ${roomsList.length}/${total} kết quả` : `${total} kết quả`;
            } else if (total > roomsList.length) {
                text = `Top ${roomsList.length} phòng đông nhất · còn ${total - roomsList.length} phòng khác, gõ tên để tìm`;
            }
            footer.textContent = text;
            footer.classList.toggle('hidden', !text);
        }
    } catch (e) {
        if (e.name === 'AbortError') return;
        console.error('Error fetching rooms:', e);
    } finally {
        if (lobbyRoomsAbort === controller) lobbyRoomsAbort = null;
    }
}

function initLobbyRoomSearch() {
    const input = document.getElementById('lobby-room-search');
    if (!input || input.dataset.bound) return;
    input.dataset.bound = '1';
    let timer = null;
    input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => loadPublicRooms(input.value), 250);
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            input.value = '';
            loadPublicRooms('');
        }
    });
}

/** Creator deletes a room: everyone inside is notified and sent back to the lobby. */
async function deleteRoomById(roomId, roomName = '') {
    const confirmed = await showConfirmDialog({
        title: 'Xóa phòng nhạc?',
        message: `Phòng “${roomName || roomId}” cùng hàng đợi, lịch sử và tin nhắn sẽ bị xóa vĩnh viễn. Mọi người đang trong phòng sẽ được đưa về sảnh.`,
        confirmText: 'Xóa phòng',
        cancelText: 'Hủy',
        variant: 'danger',
    });
    if (!confirmed) return;
    try {
        await ensureSession();
        let res = await fetch(`/api/rooms/${encodeURIComponent(roomId)}`, { method: 'DELETE', headers: getAuthHeaders() });
        if (res.status === 401) {
            await ensureSession(true, true).catch(() => {});
            res = await fetch(`/api/rooms/${encodeURIComponent(roomId)}`, { method: 'DELETE', headers: getAuthHeaders() });
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(data.detail || 'Không xóa được phòng', 'error');
            return;
        }
        showToast('Đã xóa phòng', 'success');
        if (window.appState.roomId === roomId) {
            leaveRoom();
        } else {
            loadPublicRooms();
        }
    } catch (e) {
        showToast('Không xóa được phòng, thử lại sau nhé', 'error');
    }
}
window.deleteRoomById = deleteRoomById;

// =============================================================================
// ROOM JOIN / CREATE FLOW (AUTHENTICATED + PRIVATE ROOM ACCESS)
// =============================================================================
async function handleCreateRoom(e) {
    if (e) e.preventDefault();
    const nameInput = document.getElementById('create-room-name');
    const djModeCheckbox = document.getElementById('create-room-dj-mode');
    const privateCheckbox = document.getElementById('create-room-private');
    const passcodeInput = document.getElementById('create-room-passcode');

    const name = nameInput.value.trim() || `Phòng của ${window.appState.userName}`;
    const djMode = djModeCheckbox ? djModeCheckbox.checked : false;
    const isPrivate = privateCheckbox ? privateCheckbox.checked : false;
    const passcode = (isPrivate && passcodeInput) ? passcodeInput.value.trim() : '';

    if (passcode && passcode.length < 4) {
        showToast('Mật khẩu phòng cần tối thiểu 4 ký tự', 'warning');
        return;
    }

    try {
        await ensureSession();
        const res = await fetch('/api/rooms', {
            method: 'POST',
            headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                name,
                dj_mode: djMode,
                is_private: isPrivate,
                passcode: passcode || null,
            })
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(data.detail || 'Không thể tạo phòng', 'error');
            return;
        }
        if (data.room_id) {
            joinRoom(data.room_id, data.invite_token || null);
        }
    } catch (err) {
        showToast('Lỗi khi tạo phòng: ' + err.message, 'error');
    }
}

function handleJoinRoom(e) {
    if (e) e.preventDefault();
    const idInput = document.getElementById('input-join-room-id');
    const id = idInput.value.trim();
    if (!id) {
        showToast('Vui lòng nhập mã phòng muốn vào', 'warning');
        return;
    }
    joinRoom(id);
}

async function joinRoom(roomId, inviteToken = null) {
    const cleanId = roomId.trim().toLowerCase();
    try {
        await ensureSession(false, true);

        // Pre-check room existence & private room access before switching views
        const query = inviteToken ? `?invite=${encodeURIComponent(inviteToken)}` : '';
        const checkRes = await fetch(`/api/rooms/${encodeURIComponent(cleanId)}${query}`, {
            headers: getAuthHeaders(),
        });

        if (checkRes.status === 404) {
            showToast(`Không tìm thấy phòng #${cleanId}`, 'warning');
            return;
        }
        if (checkRes.status === 403) {
            const errData = await checkRes.json().catch(() => ({}));
            openPasscodeModal(cleanId, !!errData.has_passcode);
            return;
        }
        if (!checkRes.ok) {
            showToast('Không thể tham gia phòng lúc này', 'error');
            return;
        }
    } catch (err) {
        showToast('Lỗi kết nối phiên bảo mật: ' + err.message, 'error');
        return;
    }

    window.appState.roomId = cleanId;
    window.appState.inviteToken = inviteToken;

    const urlParams = new URLSearchParams({ room: cleanId });
    if (inviteToken) urlParams.set('invite', inviteToken);
    const newUrl = `${window.location.pathname}?${urlParams.toString()}`;
    window.history.pushState({ path: newUrl }, '', newUrl);

    document.getElementById('lobby-view').classList.add('hidden');
    document.getElementById('room-view').classList.remove('hidden');

    window.syncPlayer.init();
    window.roomChat.init();
    window.roomChat.clear();

    window.roomSocket.connect(
        cleanId,
        window.appState.sessionToken,
        {
            name: window.appState.userName,
            avatar: window.appState.userAvatar,
            color: window.appState.userColor,
        },
        inviteToken
    );
}

function leaveRoom() {
    window.roomSocket.disconnect();
    if (window.syncPlayer) window.syncPlayer.reset();
    stopMcRadioSpeech();
    document.getElementById('room-vibe-card')?.classList.add('hidden');
    document.getElementById('ai-dj-modal')?.classList.add('hidden');
    document.getElementById('btn-delete-room')?.classList.add('hidden');
    window.appState.isRoomOwner = false;
    window.appState.roomId = null;
    window.appState.inviteToken = null;
    window.appState.roomData = null;
    window.appState.roomSuggestions = null;
    window.appState.roomSuggestionsMode = 'trending';
    window.appState.roomSuggestionMembers = [];
    renderPriorityBanner(null);
    resetRelatedSuggestions();
    resetLyricsState();

    const ambientBg = document.getElementById('room-ambient-bg');
    if (ambientBg) ambientBg.classList.remove('active');

    window.history.pushState({}, '', window.location.pathname);

    document.getElementById('room-view').classList.add('hidden');
    document.getElementById('lobby-view').classList.remove('hidden');

    loadPublicRooms();
}

// =============================================================================
// REALTIME AS-YOU-TYPE YOUTUBE SEARCH (NO SEARCH BUTTON REQUIRED)
// =============================================================================
function initRealtimeSearch() {
    const wrapper = document.getElementById('realtime-search-wrapper');
    const input = document.getElementById('search-music-input');
    const clearBtn = document.getElementById('btn-clear-search');
    const spinner = document.getElementById('search-spinner');
    const kbdHint = document.getElementById('search-kbd-hint');
    const dropdown = document.getElementById('search-results-box');

    if (!input || !dropdown) return;

    const openDropdown = () => {
        searchState.isOpen = true;
        dropdown.classList.remove('hidden');
        if (!input.value.trim()) {
            renderDropdownSuggestions();
        }
    };

    const closeDropdown = () => {
        searchState.isOpen = false;
        searchState.highlightedIndex = -1;
        dropdown.classList.add('hidden');
    };

    const setLoading = (loading) => {
        if (spinner) spinner.classList.toggle('hidden', !loading);
        if (clearBtn) clearBtn.classList.toggle('hidden', loading || !input.value.trim());
        if (kbdHint) kbdHint.classList.toggle('hidden', loading || !!input.value.trim());
    };

    const renderSkeletons = (label = 'Đang tìm kiếm trên YouTube...') => {
        const list = document.getElementById('search-results-list');
        const header = document.getElementById('search-dropdown-header');
        if (header) header.textContent = label;
        if (!list) return;

        list.innerHTML = Array.from({ length: 4 }).map(() => `
            <div class="flex items-center justify-between gap-3 p-2.5 rounded-xl">
                <div class="flex items-center gap-3 flex-1 min-w-0">
                    <div class="w-16 h-10 rounded-lg skeleton-box flex-shrink-0"></div>
                    <div class="flex-1 space-y-2">
                        <div class="h-3.5 w-3/4 rounded skeleton-box"></div>
                        <div class="h-2.5 w-1/2 rounded skeleton-box"></div>
                    </div>
                </div>
                <div class="w-16 h-8 rounded-xl skeleton-box flex-shrink-0"></div>
            </div>
        `).join('');
    };

    const renderDropdownSuggestions = () => {
        const list = document.getElementById('search-results-list');
        const header = document.getElementById('search-dropdown-header');
        if (!list) return;

        const hasPersonalized = Array.isArray(window.appState.roomSuggestions) && window.appState.roomSuggestions.length > 0;
        if (header) {
            header.textContent = hasPersonalized
                ? quickSuggestionsHeaderText()
                : '🔥 Đang thịnh hành tại Việt Nam — Gõ tên bài hoặc dán link YouTube để tìm';
        }

        searchState.results = hasPersonalized ? window.appState.roomSuggestions : (window.appState.suggestions || []);
        searchState.highlightedIndex = -1;
        renderSearchResultsList(searchState.results);

        // Refresh room-personalized suggestions asynchronously in background if room history changed
        if (window.appState.roomId) {
            loadRoomQuickSuggestions(window.appState.roomId).then((updated) => {
                if (updated && searchState.isOpen && !input.value.trim()) {
                    const hdr = document.getElementById('search-dropdown-header');
                    if (hdr) hdr.textContent = quickSuggestionsHeaderText();
                    searchState.results = window.appState.roomSuggestions || window.appState.suggestions || [];
                    renderSearchResultsList(searchState.results);
                }
            });
        }
    };

    const applyResultsToUI = (results, query, isDirectUrl) => {
        const list = document.getElementById('search-results-list');
        if (list) list.classList.remove('opacity-60');

        searchState.results = results;
        searchState.highlightedIndex = results.length > 0 ? 0 : -1;

        const header = document.getElementById('search-dropdown-header');
        if (header) {
            header.textContent = results.length > 0
                ? (isDirectUrl ? 'Đã nhận diện video YouTube' : `Kết quả tức thì (${results.length}) — Bấm Enter để thêm nhanh`)
                : `Không tìm thấy kết quả cho "${query}"`;
        }
        renderSearchResultsList(results);
    };

    const performRealtimeSearch = async (rawQuery) => {
        const query = rawQuery.trim();
        if (!query) {
            setLoading(false);
            searchState.lastQuery = '';
            renderDropdownSuggestions();
            return;
        }

        const cacheKey = query.toLowerCase();
        const isDirectUrl = YT_URL_REGEX.test(query);

        // 0ms Instant Client-Side Cache Hit
        const cached = searchState.cache.get(cacheKey);
        if (cached && (Date.now() - cached.ts) < 300000) {
            if (searchState.abortController) searchState.abortController.abort();
            searchState.lastQuery = cacheKey;
            openDropdown();
            setLoading(false);
            applyResultsToUI(cached.results, query, isDirectUrl);
            return;
        }

        // Cancel any previous in-flight search request
        if (searchState.abortController) {
            searchState.abortController.abort();
        }
        const controller = new AbortController();
        searchState.abortController = controller;
        searchState.lastQuery = cacheKey;

        openDropdown();
        setLoading(true);

        const list = document.getElementById('search-results-list');
        const header = document.getElementById('search-dropdown-header');
        // Keep previous results visible & gently dimmed while updating (zero flicker!)
        if (searchState.results.length > 0 && list && list.children.length > 0) {
            list.classList.add('opacity-60', 'transition-opacity');
            if (header) header.textContent = isDirectUrl ? 'Đang lấy thông tin link...' : `Đang cập nhật "${query}"...`;
        } else {
            renderSkeletons(isDirectUrl ? 'Đang lấy thông tin video từ link YouTube...' : `Đang tìm "${query}"...`);
        }

        try {
            const res = await fetch(`/api/youtube/search?q=${encodeURIComponent(query)}`, {
                signal: controller.signal
            });
            const data = await res.json();
            if (controller.signal.aborted) return;

            const results = data.results || [];
            if (results.length > 0) {
                if (searchState.cache.size >= 100) {
                    const firstKey = searchState.cache.keys().next().value;
                    searchState.cache.delete(firstKey);
                }
                searchState.cache.set(cacheKey, { ts: Date.now(), results });
            }

            applyResultsToUI(results, query, isDirectUrl);
        } catch (err) {
            if (err.name === 'AbortError') return;
            if (list) {
                list.classList.remove('opacity-60');
                list.innerHTML = `<div class="text-center py-6 text-rose-500 text-xs">Không thể tìm kiếm lúc này. Hãy thử lại!</div>`;
            }
        } finally {
            if (searchState.abortController === controller) {
                setLoading(false);
            }
        }
    };

    // Instant As-You-Type Listener
    input.addEventListener('input', () => {
        const val = input.value;
        const trimmed = val.trim();

        if (clearBtn) clearBtn.classList.toggle('hidden', !trimmed);
        if (kbdHint) kbdHint.classList.toggle('hidden', !!trimmed);

        if (searchState.debounceTimer) {
            clearTimeout(searchState.debounceTimer);
        }

        if (!trimmed) {
            if (searchState.abortController) searchState.abortController.abort();
            searchState.lastQuery = '';
            setLoading(false);
            openDropdown();
            renderDropdownSuggestions();
            return;
        }

        // Skip redundant search if only trailing spaces changed
        if (trimmed.toLowerCase() === searchState.lastQuery && searchState.results.length > 0) {
            openDropdown();
            return;
        }

        // If user pasted a YouTube URL or 11-char ID -> fire IMMEDIATELY (0ms debounce!)
        if (YT_URL_REGEX.test(trimmed)) {
            performRealtimeSearch(trimmed);
            return;
        }

        // Wait for at least 2 characters before querying YouTube
        if (trimmed.length < 2) {
            openDropdown();
            const header = document.getElementById('search-dropdown-header');
            if (header) header.textContent = 'Nhập thêm ký tự để tìm kiếm realtime...';
            return;
        }

        // Instant hit if already in client cache (0ms!)
        const cached = searchState.cache.get(trimmed.toLowerCase());
        if (cached && (Date.now() - cached.ts) < 300000) {
            performRealtimeSearch(trimmed);
            return;
        }

        // Snappy 180ms debounce for realtime feel
        openDropdown();
        searchState.debounceTimer = setTimeout(() => {
            performRealtimeSearch(trimmed);
        }, 180);
    });

    input.addEventListener('focus', () => {
        openDropdown();
        if (input.value.trim().length >= 2 && searchState.results.length > 0) {
            renderSearchResultsList(searchState.results);
        } else if (!input.value.trim()) {
            renderDropdownSuggestions();
        }
    });

    // Keyboard navigation inside realtime search (ArrowUp, ArrowDown, Enter, Escape)
    input.addEventListener('keydown', (e) => {
        if (!searchState.isOpen) return;

        if (e.key === 'Escape') {
            e.preventDefault();
            closeDropdown();
            input.blur();
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (searchState.results.length > 0) {
                searchState.highlightedIndex = (searchState.highlightedIndex + 1) % searchState.results.length;
                updateSearchHighlight();
            }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (searchState.results.length > 0) {
                searchState.highlightedIndex = (searchState.highlightedIndex - 1 + searchState.results.length) % searchState.results.length;
                updateSearchHighlight();
            }
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (searchState.results.length > 0) {
                const idx = searchState.highlightedIndex >= 0 ? searchState.highlightedIndex : 0;
                const picked = searchState.results[idx];
                if (picked) {
                    addTrackFromSearch(picked, idx);
                }
            } else if (input.value.trim().length >= 2) {
                if (searchState.debounceTimer) clearTimeout(searchState.debounceTimer);
                performRealtimeSearch(input.value);
            }
        }
    });

    // Clear button
    if (clearBtn) {
        clearBtn.onclick = () => {
            input.value = '';
            searchState.results = [];
            searchState.lastQuery = '';
            searchState.addedIds.clear();
            if (searchState.debounceTimer) clearTimeout(searchState.debounceTimer);
            if (searchState.abortController) searchState.abortController.abort();
            setLoading(false);
            renderDropdownSuggestions();
            input.focus();
        };
    }

    // Close on click outside
    document.addEventListener('click', (e) => {
        if (wrapper && !wrapper.contains(e.target)) {
            closeDropdown();
        }
    });

    // Global "/" or Ctrl+K shortcut to focus realtime search
    document.addEventListener('keydown', (e) => {
        if (!window.appState.roomId) return;
        const tag = (document.activeElement?.tagName || '').toLowerCase();
        const isTyping = tag === 'input' || tag === 'textarea';

        if ((e.key === '/' && !isTyping) || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k')) {
            e.preventDefault();
            input.focus();
            input.select();
            openDropdown();
        }
    });
}

function renderSearchResultsList(results) {
    const list = document.getElementById('search-results-list');
    if (!list) return;

    if (!results || results.length === 0) {
        list.innerHTML = `
            <div class="text-center py-8 text-dim text-xs">
                Thử gõ tên bài hát, tên ca sĩ hoặc dán trực tiếp đường link YouTube.
            </div>
        `;
        return;
    }

    list.innerHTML = '';
    results.forEach((item, idx) => {
        const isAdded = searchState.addedIds.has(item.id);
        const isHighlighted = idx === searchState.highlightedIndex;

        const row = document.createElement('div');
        row.className = `search-result-row flex items-center justify-between gap-3 p-2 rounded-xl cursor-pointer ${isHighlighted ? 'highlighted' : ''}`;
        row.dataset.index = idx;

        const metaParts = [escapeHtml(item.author || 'YouTube')];
        if (item.views) metaParts.push(escapeHtml(item.views));

        row.innerHTML = `
            <div class="flex items-center gap-3 min-w-0 flex-1">
                <div class="relative w-16 h-10 rounded-lg overflow-hidden flex-shrink-0 bg-slate-900 shadow-sm">
                    <img src="${safeThumbUrl(item.id)}" alt="" class="w-full h-full object-cover" loading="lazy">
                    ${item.duration_str ? `<span class="absolute bottom-0.5 right-0.5 px-1 py-0.2 rounded bg-black/80 text-white text-[10px] font-mono-num leading-tight">${escapeHtml(item.duration_str)}</span>` : ''}
                </div>
                <div class="min-w-0 flex-1">
                    <h5 class="text-xs md:text-[13px] font-semibold text-main truncate">${escapeHtml(item.title)}</h5>
                    <p class="text-[11px] text-sub truncate mt-0.5">${metaParts.join(' • ')}</p>
                </div>
            </div>
            <button type="button" class="js-add-btn px-3 py-1.5 rounded-xl text-xs font-semibold transition flex items-center gap-1 flex-shrink-0 ${
                isAdded
                    ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30'
                    : 'btn-gradient'
            }">
                ${isAdded
                    ? `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg><span>Đã thêm</span>`
                    : `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/></svg><span>Thêm</span>`
                }
            </button>
        `;

        row.addEventListener('mouseenter', () => {
            searchState.highlightedIndex = idx;
            updateSearchHighlight(false);
        });

        row.onclick = () => addTrackFromSearch(item, idx);
        list.appendChild(row);
    });
}

function updateSearchHighlight(scrollIntoView = true) {
    const rows = document.querySelectorAll('#search-results-list .search-result-row');
    rows.forEach((row, idx) => {
        const active = idx === searchState.highlightedIndex;
        row.classList.toggle('highlighted', active);
        if (active && scrollIntoView) {
            row.scrollIntoView({ block: 'nearest' });
        }
    });
}

function canAddTrackInCurrentRoom() {
    const isDj = !!(window.appState.roomData && window.appState.roomData.dj_mode);
    if (isDj && !window.appState.isHost) {
        showToast('Chế độ DJ đang bật: Chỉ Chủ phòng (Host) mới được thêm bài hát', 'warning');
        return false;
    }
    return true;
}
window.canAddTrackInCurrentRoom = canAddTrackInCurrentRoom;

function addTrackFromSearch(item, idx) {
    if (!canAddTrackInCurrentRoom()) return;
    window.roomSocket.addTrack(item);
    searchState.addedIds.add(item.id);

    // Update just that button so user can keep adding more songs without losing context
    const row = document.querySelector(`#search-results-list .search-result-row[data-index="${idx}"]`);
    if (row) {
        const btn = row.querySelector('.js-add-btn');
        if (btn) {
            btn.className = 'js-add-btn px-3 py-1.5 rounded-xl text-xs font-semibold transition flex items-center gap-1 flex-shrink-0 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30';
            btn.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg><span>Đã thêm</span>`;
        }
    }
}

// =============================================================================
// SOCKET EVENTS
// =============================================================================
function initSocketEvents() {
    const socket = window.roomSocket;
    socket.setSessionRefresher(() => ensureSession(false, true));

    socket.on('init_state', (data) => {
        window.appState.roomData = data.room;
        window.appState.isHost = data.is_host;
        window.appState.isRoomOwner = !!data.is_owner;
        document.getElementById('btn-delete-room')?.classList.toggle('hidden', !data.is_owner);
        if (data.your_user_id) window.appState.userId = data.your_user_id;
        if (data.room && data.room.invite_token) {
            window.appState.inviteToken = data.room.invite_token;
        }
        updateRoomUI(data.room);

        if (data.messages) {
            data.messages.forEach(m => window.roomChat.addMessage(m, true));
            window.roomChat.scrollToBottom();
        }

        window.syncPlayer.syncWithRoomState(data.room);
    });

    socket.on('playback_changed', (data) => {
        if (!window.appState.roomData) return;
        window.appState.roomData.current_track = data.current_track;
        window.appState.roomData.status = data.status;
        window.appState.roomData.current_time = data.current_time;
        window.appState.roomData.updated_at = data.updated_at;
        if ('server_time' in data) window.appState.roomData.server_time = data.server_time;
        window.appState.roomData.queue = data.queue || [];
        if (data.history) window.appState.roomData.history = data.history;
        if ('skip_vote' in data) {
            window.appState.roomData.skip_vote = data.skip_vote;
        }
        if ('priority_request' in data) {
            window.appState.roomData.priority_request = data.priority_request;
        }

        window.syncPlayer.syncWithRoomState(window.appState.roomData);
        renderSkipVoteButton(window.appState.roomData.skip_vote);
        renderPriorityBanner(window.appState.roomData.priority_request);
        renderQueue();
        renderHistory();
        loadRelatedSuggestionsForTrack(window.appState.roomData.current_track, data.related_suggestions || null);
        loadLyricsForTrack(window.appState.roomData.current_track);
        loadRoomQuickSuggestions(window.appState.roomId);
        if (parseFloat(data.current_time || 0) <= 5.0) {
            mcRadioState.lastSpokenUid = null;
            mcRadioState.fetchingUid = null;
        }
        triggerMcRadioIntroForTrack(window.appState.roomData.current_track);
        refreshRoomVibeIfOpen();

        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('playback_sync', (data) => {
        if (!window.appState.roomData) return;
        window.appState.roomData.status = data.status;
        window.appState.roomData.current_time = data.current_time;
        window.appState.roomData.updated_at = data.updated_at;
        if ('server_time' in data) window.appState.roomData.server_time = data.server_time;

        window.syncPlayer.syncWithRoomState(window.appState.roomData, {
            fromHostControl: data.sub_action || null,
        });
    });

    socket.on('queue_updated', (data) => {
        if (!window.appState.roomData) return;
        window.appState.roomData.queue = data.queue;
        if ('priority_request' in data) {
            window.appState.roomData.priority_request = data.priority_request;
        }
        renderPriorityBanner(window.appState.roomData.priority_request);
        renderQueue();
        refreshVisibleSuggestionChips();

        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('priority_vote_updated', (data) => {
        if (!window.appState.roomData) return;
        window.appState.roomData.priority_request = data.priority_request || null;
        renderPriorityBanner(window.appState.roomData.priority_request);
        renderQueue();

        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('skip_vote_updated', (data) => {
        if (!window.appState.roomData) return;
        window.appState.roomData.skip_vote = data.skip_vote || { count: 0, needed: 1, voted_users: [] };
        renderSkipVoteButton(window.appState.roomData.skip_vote);

        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('chat', (data) => {
        window.roomChat.addMessage(data.message);
    });

    socket.on('reaction', (data) => {
        window.roomChat.spawnFloatingEmoji(data.emoji, data.user_name);
    });

    socket.on('user_joined', (data) => {
        if (window.appState.roomData) {
            window.appState.roomData.users = data.users;
            window.appState.roomData.host_id = data.host_id;
            window.appState.isHost = (data.host_id === window.appState.userId);
            if ('skip_vote' in data) {
                window.appState.roomData.skip_vote = data.skip_vote;
                renderSkipVoteButton(window.appState.roomData.skip_vote);
            }
            if ('priority_request' in data) {
                window.appState.roomData.priority_request = data.priority_request;
                renderPriorityBanner(window.appState.roomData.priority_request);
            }
            renderMembers();
            updateHostControls();
        }
        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('user_left', (data) => {
        if (window.appState.roomData) {
            window.appState.roomData.users = data.users;
            window.appState.roomData.host_id = data.host_id;
            window.appState.isHost = (data.host_id === window.appState.userId);
            if ('skip_vote' in data) {
                window.appState.roomData.skip_vote = data.skip_vote;
                renderSkipVoteButton(window.appState.roomData.skip_vote);
            }
            if ('priority_request' in data) {
                window.appState.roomData.priority_request = data.priority_request;
                renderPriorityBanner(window.appState.roomData.priority_request);
            }
            renderMembers();
            updateHostControls();
        }
        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('dj_mode_changed', (data) => {
        if (window.appState.roomData) {
            window.appState.roomData.dj_mode = data.dj_mode;
            updateHostControls();
        }
        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('ai_proactive_changed', (data) => {
        if (window.appState.roomData) {
            window.appState.roomData.ai_proactive = !!data.ai_proactive;
            updateAiProactiveToggleUI();
        }
        if (data.message) window.roomChat.addMessage(data.message);
    });

    // Host calibrated the lyrics sync for the current video -> applies to everyone instantly
    socket.on('lyrics_offset', (data) => {
        if (!data || !data.video_id) return;
        const ct = window.appState.roomData && window.appState.roomData.current_track;
        if (ct && ct.id === data.video_id) ct.lyrics_offset = Number(data.offset) || 0;
        if (lyricsState.lastVideoId === data.video_id) {
            setSharedLyricsOffset(Number(data.offset) || 0);
        }
    });

    socket.on('mc_mode_changed', (data) => {
        if (window.appState.roomData) {
            window.appState.roomData.mc_enabled = !!data.mc_enabled;
            window.appState.mcEnabled = !!data.mc_enabled;
            updateMcToggleUI();
            if (!data.mc_enabled) {
                stopMcRadioSpeech();
            } else if (window.appState.roomData.current_track) {
                mcRadioState.lastSpokenUid = null;
                triggerMcRadioIntroForTrack(window.appState.roomData.current_track);
            }
        }
        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('auto_dj_changed', (data) => {
        if (window.appState.roomData) {
            window.appState.roomData.auto_dj = !!data.auto_dj;
            updateAutoDjUI();
        }
        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('ai_typing', (data) => {
        if (window.roomChat && typeof window.roomChat.setAiTyping === 'function') {
            window.roomChat.setAiTyping(!!data.typing, data.bot_name || 'Em bé floor 2', data.status || '');
        }
    });

    // Live token streaming of Em bé floor 2's reply (final 'chat' message with the same id replaces it)
    socket.on('ai_stream', (data) => {
        if (window.roomChat && typeof window.roomChat.upsertAiStream === 'function' && data && data.stream_id) {
            window.roomChat.upsertAiStream(data.stream_id, data.text || '', data);
        }
    });

    socket.on('error', (err) => {
        if (err.message) showToast(err.message, 'warning');
    });

    socket.on('access_denied', (data) => {
        const failedRoom = window.appState.roomId;
        leaveRoom();
        showToast(data?.reason || 'Không có quyền truy cập phòng riêng tư', 'error');
        if (failedRoom) openPasscodeModal(failedRoom, true);
    });

    socket.on('room_deleted', (data) => {
        leaveRoom();
        showToast((data && data.message) || 'Phòng đã bị xóa', 'warning');
    });

    socket.on('room_not_found', (data) => {
        leaveRoom();
        showToast(data?.reason || 'Phòng không tồn tại', 'warning');
    });

    socket.on('rate_limited', (data) => {
        leaveRoom();
        showToast(data?.reason || 'Quá nhiều kết nối đồng thời từ địa chỉ IP của bạn', 'warning');
    });

    socket.on('session_expired', async () => {
        if (!window.appState.roomId) return;
        try {
            const roomId = window.appState.roomId;
            const invite = window.appState.inviteToken;
            await ensureSession(true);
            window.roomSocket.connect(
                roomId,
                window.appState.sessionToken,
                {
                    name: window.appState.userName,
                    avatar: window.appState.userAvatar,
                    color: window.appState.userColor,
                },
                invite
            );
        } catch (e) {
            leaveRoom();
            showToast('Phiên làm việc đã hết hạn, vui lòng vào lại phòng', 'warning');
        }
    });
}

// =============================================================================
// ROOM UI UPDATES
// =============================================================================
function updateRoomUI(room) {
    if (!room) return;
    const titleEl = document.getElementById('room-title-display');
    const idBadge = document.getElementById('room-id-badge');
    const privBadge = document.getElementById('room-private-badge');
    if (titleEl) titleEl.textContent = room.name;
    if (idBadge) idBadge.textContent = `#${room.id}`;
    if (privBadge) privBadge.classList.toggle('hidden', !room.is_private);

    updateHostControls();
    updateAutoDjUI();
    renderSkipVoteButton(room.skip_vote || null);
    renderPriorityBanner(room.priority_request || null);
    renderQueue();
    renderHistory();
    renderMembers();
    loadRelatedSuggestionsForTrack(room.current_track || null, room.related_suggestions || null);
    loadLyricsForTrack(room.current_track || null);
    loadRoomQuickSuggestions(room.id);
    if (room.current_track && (parseFloat(room.current_time || 0) < 20)) {
        triggerMcRadioIntroForTrack(room.current_track);
    }
}

function renderSkipVoteButton(skipVote) {
    const btn = document.getElementById('btn-vote-skip');
    const labelEl = document.getElementById('skip-vote-label');
    const countEl = document.getElementById('skip-vote-count');
    if (!btn) return;

    const hasTrack = !!(window.appState.roomData && window.appState.roomData.current_track);
    const count = Number((skipVote && skipVote.count) || 0);
    const needed = Math.max(1, Number((skipVote && skipVote.needed) || 1));
    const myUid = window.appState.userId;
    const hasVoted = !!(skipVote && Array.isArray(skipVote.voted_users) && skipVote.voted_users.includes(myUid));

    btn.disabled = !hasTrack;
    btn.classList.toggle('opacity-50', !hasTrack);
    btn.classList.toggle('cursor-not-allowed', !hasTrack);

    if (hasVoted && hasTrack) {
        btn.classList.add('border-rose-500/40', 'bg-rose-500/15', 'text-rose-600', 'dark:text-rose-300');
        btn.classList.remove('text-main');
        if (labelEl) labelEl.textContent = '✓ Đã vote qua bài';
        btn.title = 'Bấm lại để rút phiếu bỏ qua bài';
    } else {
        btn.classList.remove('border-rose-500/40', 'bg-rose-500/15', 'text-rose-600', 'dark:text-rose-300');
        btn.classList.add('text-main');
        if (labelEl) labelEl.textContent = 'Qua bài';
        btn.title = `Bình chọn qua bài (cần ${needed} phiếu — ≥ 50% thành viên)`;
    }

    if (countEl) {
        countEl.textContent = `${count}/${needed}`;
    }
}

let priorityBannerTimer = null;

function renderPriorityBanner(priorityReq) {
    const banner = document.getElementById('priority-vote-banner');
    if (!banner) return;

    if (priorityBannerTimer) {
        clearInterval(priorityBannerTimer);
        priorityBannerTimer = null;
    }

    if (!priorityReq || !priorityReq.track_uid) {
        banner.classList.add('hidden');
        return;
    }

    const thumbEl = document.getElementById('priority-banner-thumb');
    const titleEl = document.getElementById('priority-banner-title');
    const reqEl = document.getElementById('priority-banner-requester');
    const progressEl = document.getElementById('priority-banner-progress');
    const countEl = document.getElementById('priority-banner-count');
    const countdownEl = document.getElementById('priority-banner-countdown');
    const voteBtn = document.getElementById('btn-vote-priority');
    const cancelBtn = document.getElementById('btn-cancel-priority');

    if (thumbEl) thumbEl.src = safeThumbUrl(priorityReq.track_id);
    if (titleEl) titleEl.textContent = priorityReq.track_title || '';
    if (reqEl) reqEl.textContent = `Đề xuất bởi ${priorityReq.requested_by_name || 'Thành viên'}`;

    const count = Number(priorityReq.count || 0);
    const needed = Math.max(1, Number(priorityReq.needed || 1));
    const pct = Math.min(100, Math.round((count / needed) * 100));

    if (progressEl) progressEl.style.width = `${pct}%`;
    if (countEl) countEl.textContent = `${count}/${needed} phiếu`;

    const myUid = window.appState.userId;
    const voted = Array.isArray(priorityReq.voted_users) && priorityReq.voted_users.includes(myUid);

    if (voteBtn) {
        if (voted) {
            voteBtn.className = 'px-3.5 py-1.5 rounded-xl bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 border border-emerald-500/40 text-xs font-extrabold transition flex items-center gap-1.5';
            voteBtn.innerHTML = '<span>✓ Đã đồng ý</span>';
            voteBtn.title = 'Bấm lại để rút phiếu';
        } else {
            voteBtn.className = 'px-3.5 py-1.5 rounded-xl bg-amber-500 hover:bg-amber-600 text-slate-950 text-xs font-extrabold transition shadow-sm flex items-center gap-1.5';
            voteBtn.innerHTML = '<span>⚡ Đồng ý ưu tiên</span>';
            voteBtn.title = 'Bình chọn đưa bài này lên phát kế tiếp (#1)';
        }
    }

    if (cancelBtn) {
        const canCancel = window.appState.isHost || priorityReq.requested_by_id === myUid;
        cancelBtn.classList.toggle('hidden', !canCancel);
    }

    const updateCountdown = () => {
        if (!countdownEl || !priorityReq.expires_at) return;
        const left = Math.max(0, Math.ceil(priorityReq.expires_at - Date.now() / 1000));
        countdownEl.textContent = `${left}s`;
        if (left <= 0) {
            clearInterval(priorityBannerTimer);
            priorityBannerTimer = null;
            banner.classList.add('hidden');
            if (window.appState.roomData) {
                window.appState.roomData.priority_request = null;
                renderQueue();
            }
        }
    };
    updateCountdown();
    priorityBannerTimer = setInterval(updateCountdown, 1000);

    banner.classList.remove('hidden');
}

function updateHostControls() {
    const isHost = (window.appState.roomData && window.appState.roomData.host_id === window.appState.userId);
    window.appState.isHost = isHost;

    const isDj = !!(window.appState.roomData && window.appState.roomData.dj_mode);

    const hostBadge = document.getElementById('user-role-badge');
    if (hostBadge) {
        if (isHost) {
            hostBadge.innerHTML = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-[11px] font-bold">👑 Host</span>`;
        } else if (isDj) {
            hostBadge.innerHTML = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-[11px] font-bold" title="Chế độ DJ đang bật: Chỉ Chủ phòng được thêm nhạc">🎛️ Chế độ DJ</span>`;
        } else {
            hostBadge.innerHTML = `<span class="inline-flex items-center px-2 py-0.5 rounded-full surface-subtle text-sub text-[11px] font-medium">Thành viên</span>`;
        }
    }

    const djToggleBtn = document.getElementById('btn-dj-mode-toggle');
    if (djToggleBtn) {
        djToggleBtn.classList.toggle('hidden', !isHost);
        djToggleBtn.classList.toggle('sm:inline-flex', isHost);
        djToggleBtn.innerHTML = isDj
            ? `<span class="w-1.5 h-1.5 rounded-full bg-amber-500"></span><span>DJ: Bật</span>`
            : `<span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span><span>DJ: Tắt</span>`;
    }

    updateMcToggleUI();

    const searchInput = document.getElementById('search-music-input');
    if (searchInput) {
        searchInput.placeholder = (isDj && !isHost)
            ? '🎛️ Chế độ DJ đang bật — Chỉ Chủ phòng được thêm nhạc'
            : 'Tìm bài hát hoặc dán link YouTube...';
    }

    const playPauseBtn = document.getElementById('btn-play-pause');
    if (playPauseBtn) {
        playPauseBtn.title = isHost
            ? 'Phát / Tạm dừng đồng bộ cả phòng (Chủ phòng)'
            : 'Phát / Tạm dừng (chỉ trên máy bạn, không ảnh hưởng người khác)';
    }
    const progressBar = document.getElementById('player-progress-bar');
    if (progressBar) {
        progressBar.title = isHost
            ? 'Tua nhạc đồng bộ cả phòng (Chủ phòng)'
            : 'Tua nhanh (chỉ áp dụng trên máy bạn)';
    }
}

// =============================================================================
// PLAYLIST QUEUE (FIFO) & HISTORY RENDERING
// =============================================================================
function formatTotalMinutes(totalSec) {
    if (!totalSec || totalSec <= 0) return '';
    const mins = Math.ceil(totalSec / 60);
    if (mins >= 60) {
        const h = Math.floor(mins / 60);
        const m = mins % 60;
        return `${h}g ${m}p`;
    }
    return `${mins} phút`;
}

function renderQueue() {
    // Keep "+ Thêm" buttons on Em bé floor 2's song cards in sync with what is actually playing / queued
    if (window.roomChat && typeof window.roomChat.refreshAiTrackButtons === 'function') {
        window.roomChat.refreshAiTrackButtons();
    }
    const queueList = document.getElementById('playlist-queue-list');
    const queueCountBadge = document.getElementById('queue-count-badge');
    const queueDurationBadge = document.getElementById('queue-duration-badge');
    const queueEmpty = document.getElementById('queue-empty-state');
    if (!queueList) return;

    const queue = (window.appState.roomData && window.appState.roomData.queue) || [];
    if (queueCountBadge) queueCountBadge.textContent = queue.length;
    if (window.syncPlayer) window.syncPlayer.updateUpNext(queue);
    prefetchRelatedForNextTrack(queue);

    const totalSec = queue.reduce((acc, t) => acc + (parseInt(t.duration, 10) || 0), 0);
    if (queueDurationBadge) {
        const durText = formatTotalMinutes(totalSec);
        queueDurationBadge.textContent = durText ? `Tổng ~${durText}` : 'Phát theo thứ tự thêm';
    }

    if (queue.length === 0) {
        queueList.innerHTML = '';
        if (queueEmpty) queueEmpty.classList.remove('hidden');
        return;
    }

    if (queueEmpty) queueEmpty.classList.add('hidden');
    queueList.innerHTML = '';

    const isHost = window.appState.isHost;
    const currentUid = window.appState.userId;
    const activePriority = window.appState.roomData && window.appState.roomData.priority_request;

    queue.forEach((track, index) => {
        const canDelete = isHost || (track.added_by_id === currentUid);
        const canPromote = isHost && index > 0;
        const canRequestPriority = index > 0;
        const isPriorityTarget = !!(activePriority && activePriority.track_uid === track.uid);
        const hasVotedPriority = isPriorityTarget && Array.isArray(activePriority.voted_users) && activePriority.voted_users.includes(currentUid);

        const item = document.createElement('div');
        item.className = `surface-subtle flex items-center justify-between gap-2 sm:gap-3 p-2 sm:p-2.5 rounded-2xl group ${
            isPriorityTarget ? 'ring-1 ring-amber-500/50 bg-amber-500/5' : ''
        }`;

        const priorityBtnHtml = canRequestPriority
            ? (isPriorityTarget
                ? `<button type="button" class="btn-priority-track px-2.5 py-1 rounded-lg text-[11px] font-bold transition flex items-center gap-1 ${
                    hasVotedPriority
                        ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/35'
                        : 'bg-amber-500 text-slate-950 hover:bg-amber-600 shadow-sm'
                }" title="Bình chọn ưu tiên bài này lên #1">
                    <span>⚡ ${activePriority.count}/${activePriority.needed}</span>
                </button>`
                : `<button type="button" class="btn-priority-track px-2 py-1 rounded-lg text-[11px] font-semibold text-amber-600 dark:text-amber-400 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/25 transition flex items-center gap-1" title="Yêu cầu ưu tiên phát bài này kế tiếp (cần ≥50% thành viên vote)">
                    <span>⚡</span><span class="hidden sm:inline">Ưu tiên</span>
                </button>`)
            : `<span class="px-1.5 sm:px-2 py-0.5 rounded-md bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 text-[10px] font-bold flex-shrink-0" title="Sẽ phát kế tiếp">#1<span class="hidden sm:inline"> Kế tiếp</span></span>`;

        item.innerHTML = `
            <div class="flex items-center gap-2.5 sm:gap-3 min-w-0 flex-1">
                <span class="hidden sm:block text-xs font-bold font-mono-num text-dim w-6 text-center flex-shrink-0">
                    ${String(index + 1).padStart(2, '0')}
                </span>
                <div class="relative w-12 h-8 sm:w-14 sm:h-9 rounded-lg overflow-hidden flex-shrink-0 bg-slate-900 shadow-sm">
                    <img src="${safeThumbUrl(track.id)}" alt="" class="w-full h-full object-cover" loading="lazy">
                    ${track.duration_str ? `<span class="absolute bottom-0.5 right-0.5 px-1 rounded bg-black/75 text-white text-[9px] font-mono-num">${escapeHtml(track.duration_str)}</span>` : ''}
                </div>
                <div class="min-w-0 flex-1">
                    <h5 class="text-xs md:text-[13px] font-semibold text-main truncate">${escapeHtml(track.title)}</h5>
                    <div class="flex items-center gap-1.5 text-[11px] text-sub mt-0.5 min-w-0">
                        <span class="truncate">${escapeHtml(track.author)}</span>
                        <span class="hidden sm:inline text-dim">•</span>
                        <span class="hidden sm:inline text-indigo-600 dark:text-indigo-400 font-medium truncate">${escapeHtml(track.added_by_name)}</span>
                    </div>
                </div>
            </div>
            <div class="flex items-center gap-1 flex-shrink-0 opacity-100 sm:opacity-85 sm:group-hover:opacity-100 transition">
                ${priorityBtnHtml}
                ${canPromote ? `
                    <button type="button" class="btn-promote-track p-1.5 rounded-lg text-sub hover:text-indigo-500 hover:bg-indigo-500/10 transition" title="Host đưa ngay lên phát kế tiếp">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 10l7-7m0 0l7 7m-7-7v18"/></svg>
                    </button>
                ` : ''}
                ${canDelete ? `
                    <button type="button" class="btn-delete-track p-1.5 rounded-lg text-sub hover:text-rose-500 hover:bg-rose-500/10 transition" title="Xóa khỏi hàng đợi">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                    </button>
                ` : ''}
            </div>
        `;

        const priorityBtn = item.querySelector('.btn-priority-track');
        if (priorityBtn) {
            priorityBtn.onclick = (e) => {
                e.stopPropagation();
                if (isPriorityTarget) {
                    window.roomSocket.votePriority(track.uid);
                } else {
                    window.roomSocket.requestPriority(track.uid);
                }
            };
        }

        const promoteBtn = item.querySelector('.btn-promote-track');
        if (promoteBtn) {
            promoteBtn.onclick = (e) => {
                e.stopPropagation();
                const uids = queue.map(t => t.uid).filter(id => id !== track.uid);
                uids.unshift(track.uid);
                window.roomSocket.reorderQueue(uids);
            };
        }

        const delBtn = item.querySelector('.btn-delete-track');
        if (delBtn) {
            delBtn.onclick = (e) => {
                e.stopPropagation();
                window.roomSocket.removeTrack(track.uid);
            };
        }

        queueList.appendChild(item);
    });
}

function renderHistory() {
    const historyList = document.getElementById('playlist-history-list');
    if (!historyList) return;

    const history = (window.appState.roomData && window.appState.roomData.history) || [];
    if (history.length === 0) {
        historyList.innerHTML = `<div class="text-center py-8 text-dim text-xs">Chưa có bài hát nào trong lịch sử phát.</div>`;
        return;
    }

    historyList.innerHTML = '';
    [...history].reverse().forEach(track => {
        const item = document.createElement('div');
        item.className = 'surface-subtle flex items-center justify-between gap-3 p-2.5 rounded-2xl';

        item.innerHTML = `
            <div class="flex items-center gap-3 min-w-0 flex-1">
                <img src="${safeThumbUrl(track.id)}" alt="" class="w-12 h-8 object-cover rounded-lg flex-shrink-0 shadow-sm">
                <div class="min-w-0 flex-1">
                    <h5 class="text-xs font-semibold text-main truncate">${escapeHtml(track.title)}</h5>
                    <p class="text-[11px] text-sub truncate">${escapeHtml(track.author)}</p>
                </div>
            </div>
            <button class="btn-replay-track px-3 py-1.5 rounded-xl bg-indigo-500/10 hover:bg-indigo-500 text-indigo-600 dark:text-indigo-300 hover:text-white text-xs font-semibold transition flex items-center gap-1 flex-shrink-0">
                <span>+</span> Phát lại
            </button>
        `;

        item.querySelector('.btn-replay-track').onclick = () => {
            if (!canAddTrackInCurrentRoom()) return;
            window.roomSocket.addTrack(track);
        };

        historyList.appendChild(item);
    });
}

// =============================================================================
// MEMBERS LIST RENDERING
// =============================================================================
function renderMembers() {
    const list = document.getElementById('room-members-list');
    const countBadge = document.getElementById('members-count-badge');
    if (!list) return;

    const users = (window.appState.roomData && window.appState.roomData.users) || [];
    if (countBadge) countBadge.textContent = users.length;

    list.innerHTML = '';
    users.forEach(u => {
        const isMe = (u.id === window.appState.userId);
        const hostBadge = u.is_host
            ? `<span class="px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 text-[11px] font-bold">👑 Host</span>`
            : `<span class="w-2 h-2 rounded-full bg-emerald-500" title="Đang trực tuyến"></span>`;

        const item = document.createElement('div');
        item.className = 'surface-subtle flex items-center justify-between p-2.5 rounded-2xl';

        const uColor = safeColor(u.color);
        const uAvatar = escapeHtml(u.avatar || '🎧');
        item.innerHTML = `
            <div class="flex items-center gap-2.5 min-w-0">
                <div class="w-9 h-9 rounded-full flex items-center justify-center text-base shadow-sm flex-shrink-0"
                     style="background-color: ${uColor}22; border: 1.5px solid ${uColor}88">
                    ${uAvatar}
                </div>
                <div class="min-w-0">
                    <div class="flex items-center gap-1.5">
                        <span class="text-sm font-semibold text-main truncate">${escapeHtml(u.name)}</span>
                        ${isMe ? '<span class="text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-indigo-500/15 text-indigo-600 dark:text-indigo-300">Bạn</span>' : ''}
                    </div>
                    <span class="text-[11px] text-dim">Đang nghe cùng phòng</span>
                </div>
            </div>
            <div class="flex-shrink-0">${hostBadge}</div>
        `;
        list.appendChild(item);
    });
}

// =============================================================================
// PLAYER & ROOM CONTROLS
// =============================================================================
function initPlayerListeners() {
    const playPauseBtn = document.getElementById('btn-play-pause');
    const progressBar = document.getElementById('player-progress-bar');
    const volumeSlider = document.getElementById('player-volume-slider');
    const muteBtn = document.getElementById('btn-toggle-mute');
    const btnModeVideo = document.getElementById('btn-mode-video');
    const btnModeRadio = document.getElementById('btn-mode-radio');
    const btnHeaderRadio = document.getElementById('btn-header-radio-toggle');

    if (playPauseBtn) {
        playPauseBtn.onclick = () => {
            const player = window.syncPlayer;
            if (!player.currentTrack) {
                showToast('Hãy thêm bài hát trước khi phát!', 'info');
                return;
            }
            if (window.appState.isHost) {
                // Host is Master: Play / Pause syncs the entire room!
                if (player.autoplayBlocked) {
                    player.unlockAudioAndSync();
                    return;
                }
                const newAction = (player.status === 'playing') ? 'pause' : 'play';
                window.roomSocket.controlPlayback(newAction, player.getCurrentTime());
            } else {
                // Regular members: local-only Play / Pause on their own device
                player.toggleLocalPlayPause();
            }
        };
    }

    const syncLiveBtn = document.getElementById('btn-sync-live');
    if (syncLiveBtn) {
        syncLiveBtn.onclick = () => {
            if (window.syncPlayer) window.syncPlayer.resyncToLive();
        };
    }

    const autoplayBanner = document.getElementById('autoplay-unlock-banner');
    if (autoplayBanner) {
        autoplayBanner.onclick = () => {
            if (window.syncPlayer) window.syncPlayer.unlockAudioAndSync();
        };
    }

    const skipBtn = document.getElementById('btn-vote-skip');
    if (skipBtn) {
        skipBtn.onclick = () => {
            if (!window.appState.roomData || !window.appState.roomData.current_track) {
                showToast('Hiện chưa có bài hát nào đang phát!', 'info');
                return;
            }
            window.roomSocket.voteSkip();
        };
    }

    if (progressBar) {
        progressBar.addEventListener('pointerdown', () => {
            window.syncPlayer.isUserScrubbing = true;
        });
        const releaseScrub = () => {
            window.syncPlayer.isUserScrubbing = false;
        };
        progressBar.addEventListener('pointerup', releaseScrub);
        progressBar.addEventListener('pointercancel', releaseScrub);
        progressBar.addEventListener('blur', releaseScrub);
        progressBar.addEventListener('input', (e) => {
            const percent = parseFloat(e.target.value);
            const dur = window.syncPlayer.getDuration();
            if (dur > 0) {
                const targetSec = (percent / 100) * dur;
                const curEl = document.getElementById('player-time-current');
                if (curEl) curEl.textContent = window.syncPlayer.formatTime(targetSec);
                progressBar.style.background = `linear-gradient(to right, var(--accent-primary) 0%, var(--accent-pink) ${percent}%, var(--slider-track) ${percent}%)`;
            }
        });
        const finishScrub = (e) => {
            window.syncPlayer.isUserScrubbing = false;
            const percent = parseFloat(e.target.value);
            const dur = window.syncPlayer.getDuration();
            if (dur > 0) {
                const targetSec = (percent / 100) * dur;
                if (window.appState.isHost) {
                    // Host is Master: seek syncs the entire room!
                    window.syncPlayer.seekLocal(targetSec, true);
                    window.roomSocket.controlPlayback('seek', targetSec);
                } else {
                    // Regular members: local-only seek on their own device
                    window.syncPlayer.seekLocal(targetSec, false);
                }
            }
        };
        progressBar.addEventListener('change', finishScrub);
    }

    const renderMuteIcon = (isMuted) => {
        if (!muteBtn) return;
        muteBtn.innerHTML = isMuted
            ? `<svg class="w-4 h-4 text-rose-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"/><path stroke-linecap="round" stroke-linejoin="round" d="M17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2"/></svg>`
            : `<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"/></svg>`;
    };

    if (volumeSlider) {
        volumeSlider.value = window.syncPlayer.volume;
        window.syncPlayer.updateVolumeSliderFill();
        volumeSlider.addEventListener('input', (e) => {
            const wasMuted = window.syncPlayer.isMuted;
            // fromUser=true: dragging the slider up is the only automatic way to lift your own mute
            window.syncPlayer.setVolume(e.target.value, true);
            if (wasMuted !== window.syncPlayer.isMuted) renderMuteIcon(window.syncPlayer.isMuted);
        });
    }

    if (muteBtn) {
        renderMuteIcon(window.syncPlayer.isMuted);  // a member who muted last time sees it right away
        muteBtn.onclick = () => {
            renderMuteIcon(window.syncPlayer.toggleMute());
        };
    }

    // Stage Mode Switcher: Radio (Default) -> Lyrics -> Video
    const btnModeLyrics = document.getElementById('btn-mode-lyrics');

    if (btnModeRadio) {
        btnModeRadio.onclick = () => {
            window.syncPlayer.setMode('radio');
        };
    }

    if (btnModeLyrics) {
        btnModeLyrics.onclick = () => {
            window.syncPlayer.setMode('lyrics');
        };
    }

    if (btnModeVideo) {
        btnModeVideo.onclick = () => {
            if (window.syncPlayer.useAudioFallback) {
                showToast('Bài hát này đang phát qua luồng Pure Audio Radio vì bị chặn nhúng video', 'info');
                return;
            }
            window.syncPlayer.setMode('video');
        };
    }

    if (btnHeaderRadio) {
        btnHeaderRadio.onclick = () => {
            const order = window.syncPlayer.useAudioFallback ? ['radio', 'lyrics'] : ['radio', 'lyrics', 'video'];
            const idx = order.indexOf(window.syncPlayer.mode);
            const nextMode = order[(idx + 1) % order.length];
            window.syncPlayer.setMode(nextMode);
        };
    }

    initLyricsControls();
    window.syncPlayer.applyModeUI();
}

function copyInviteLink() {
    const params = new URLSearchParams({ room: window.appState.roomId || '' });
    if (window.appState.inviteToken) {
        params.set('invite', window.appState.inviteToken);
    }
    const url = `${window.location.origin}${window.location.pathname}?${params.toString()}`;
    navigator.clipboard.writeText(url).then(() => {
        showToast(
            window.appState.inviteToken
                ? 'Đã sao chép link mời riêng tư (kèm khóa truy cập)!'
                : 'Đã sao chép link mời phòng!',
            'success'
        );
    }).catch(() => {
        showToast(`Link phòng: ${url}`, 'info');
    });
}

function initRoomControls() {
    document.getElementById('btn-share-room')?.addEventListener('click', copyInviteLink);
    document.getElementById('room-id-badge')?.addEventListener('click', copyInviteLink);

    document.getElementById('btn-delete-room')?.addEventListener('click', () => {
        const rd = window.appState.roomData;
        if (rd) deleteRoomById(rd.id, rd.name);
    });
    initLobbyRoomSearch();

    document.getElementById('btn-leave-room')?.addEventListener('click', async () => {
        const confirmed = await showConfirmDialog({
            title: 'Rời khỏi phòng nhạc?',
            message: 'Bạn có chắc chắn muốn rời phòng hiện tại và quay lại sảnh chờ không?',
            confirmText: 'Rời phòng',
            cancelText: 'Ở lại',
            variant: 'danger',
        });
        if (confirmed) {
            leaveRoom();
        }
    });

    const soundBtn = document.getElementById('btn-toggle-sound-fx');
    if (soundBtn) {
        const updateSoundUI = (enabled) => {
            soundBtn.title = enabled ? 'Âm thanh thông báo: Bật' : 'Âm thanh thông báo: Tắt';
            soundBtn.innerHTML = enabled
                ? `<svg class="w-4 h-4 text-indigo-500 dark:text-indigo-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/></svg>`
                : `<svg class="w-4 h-4 text-dim" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13.73 21a2 2 0 01-3.46 0M18.63 13A17.888 17.888 0 0118 8M6.26 6.26A5.86 5.86 0 006 8c0 7-3 9-3 9h14M18 8a6 6 0 00-9.33-5M1 1l22 22"/></svg>`;
        };
        updateSoundUI(window.roomChat.soundEnabled);
        soundBtn.onclick = () => {
            const enabled = window.roomChat.toggleSound();
            updateSoundUI(enabled);
        };
    }

    document.getElementById('btn-dj-mode-toggle')?.addEventListener('click', () => {
        window.roomSocket.toggleDjMode();
    });

    // Priority Vote Banner Actions
    document.getElementById('btn-vote-priority')?.addEventListener('click', () => {
        const req = window.appState.roomData && window.appState.roomData.priority_request;
        if (req && req.track_uid) {
            window.roomSocket.votePriority(req.track_uid);
        }
    });

    document.getElementById('btn-cancel-priority')?.addEventListener('click', () => {
        window.roomSocket.cancelPriority();
    });

    // Queue vs History Tabs
    const tabQueueBtn = document.getElementById('tab-btn-queue');
    const tabHistoryBtn = document.getElementById('tab-btn-history');
    const queuePane = document.getElementById('tab-pane-queue');
    const historyPane = document.getElementById('tab-pane-history');

    if (tabQueueBtn && tabHistoryBtn) {
        tabQueueBtn.onclick = () => {
            tabQueueBtn.classList.add('active');
            tabHistoryBtn.classList.remove('active');
            queuePane.classList.remove('hidden');
            historyPane.classList.add('hidden');
        };
        tabHistoryBtn.onclick = () => {
            tabHistoryBtn.classList.add('active');
            tabQueueBtn.classList.remove('active');
            historyPane.classList.remove('hidden');
            queuePane.classList.add('hidden');
        };
    }

    // Chat vs Members Tabs
    const tabChatBtn = document.getElementById('tab-btn-chat');
    const tabMembersBtn = document.getElementById('tab-btn-members');
    const chatPane = document.getElementById('tab-pane-chat');
    const membersPane = document.getElementById('tab-pane-members');

    if (tabChatBtn && tabMembersBtn) {
        tabChatBtn.onclick = () => {
            tabChatBtn.classList.add('active');
            tabMembersBtn.classList.remove('active');
            chatPane.classList.remove('hidden');
            membersPane.classList.add('hidden');
        };
        tabMembersBtn.onclick = () => {
            tabMembersBtn.classList.add('active');
            tabChatBtn.classList.remove('active');
            membersPane.classList.remove('hidden');
            chatPane.classList.add('hidden');
        };
    }

    // Chat Form Submit (Text + Optional Pending Uploaded Image)
    const chatForm = document.getElementById('chat-input-form');
    const chatInput = document.getElementById('chat-text-input');
    if (chatForm && chatInput) {
        chatForm.onsubmit = async (e) => {
            e.preventDefault();
            if (chatMentionState.isOpen && chatMentionState.filtered.length > 0) {
                selectChatMentionCandidate(chatMentionState.activeIndex);
                return;
            }
            const text = chatInput.value.trim();
            const pendingFile = window.appState.pendingImageFile;

            if (pendingFile) {
                const sendBtn = document.getElementById('btn-chat-send');
                if (sendBtn) sendBtn.disabled = true;
                try {
                    const uploadBlob = await compressImageIfNeeded(pendingFile);
                    await ensureSession();
                    const res = await fetch('/api/chat/upload', {
                        method: 'POST',
                        headers: getAuthHeaders({ 'Content-Type': uploadBlob.type || 'application/octet-stream' }),
                        body: uploadBlob,
                    });
                    const data = await res.json();
                    if (!res.ok) {
                        showToast(data.detail || 'Không thể tải hình ảnh lên', 'error');
                        return;
                    }
                    window.roomSocket.sendImage(data.media_id, text);
                    chatInput.value = '';
                    updateChatInputMentionMirror();
                    closeChatMentionPopover();
                    clearPendingChatImage();
                } catch (err) {
                    showToast('Lỗi khi gửi hình ảnh: ' + err.message, 'error');
                } finally {
                    if (sendBtn) sendBtn.disabled = false;
                }
                return;
            }

            if (text) {
                window.roomSocket.sendChat(text);
                chatInput.value = '';
                updateChatInputMentionMirror();
                closeChatMentionPopover();
            }
        };
    }

    // Floating Reactions
    document.querySelectorAll('.reaction-pill-btn').forEach(btn => {
        btn.onclick = () => {
            const emoji = btn.getAttribute('data-emoji');
            if (emoji) {
                window.roomSocket.sendReaction(emoji);
                window.roomChat.spawnFloatingEmoji(emoji, window.appState.userName);
            }
        };
    });
}

// =============================================================================
// COLLAPSIBLE / MINIMIZABLE CHAT SIDEBAR
// =============================================================================
function setChatCollapsed(collapsed) {
    window.appState.isChatCollapsed = !!collapsed;
    if (window.innerWidth >= 1024) {
        localStorage.setItem('floor2_chat_collapsed', collapsed ? 'true' : 'false');
    }

    const sidebar = document.getElementById('chat-sidebar');
    const backdrop = document.getElementById('mobile-chat-backdrop');
    const fab = document.getElementById('btn-expand-chat-fab');

    if (sidebar) sidebar.classList.toggle('hidden', !!collapsed);
    if (backdrop) backdrop.classList.toggle('hidden', !!collapsed);
    if (fab) fab.classList.toggle('hidden', !collapsed);

    if (!collapsed && window.roomChat) {
        window.roomChat.resetUnread();
        window.roomChat.scrollToBottom();
    }
}

function initChatCollapse() {
    document.getElementById('btn-minimize-chat')?.addEventListener('click', () => setChatCollapsed(true));
    document.getElementById('mobile-chat-backdrop')?.addEventListener('click', () => setChatCollapsed(true));
    document.getElementById('btn-expand-chat-fab')?.addEventListener('click', () => setChatCollapsed(false));
    setChatCollapsed(window.appState.isChatCollapsed);
}

// =============================================================================
// RICH CHAT MEDIA: ANIMATED STICKERS, DIVERSE GIF SEARCH & IMAGE UPLOAD / PASTE
// =============================================================================
let pendingPreviewObjectUrl = null;
let gifSearchTimer = null;
let gifAbortController = null;
let stickerSearchTimer = null;
let stickerAbortController = null;

function clearPendingChatImage() {
    window.appState.pendingImageFile = null;
    if (pendingPreviewObjectUrl) {
        URL.revokeObjectURL(pendingPreviewObjectUrl);
        pendingPreviewObjectUrl = null;
    }
    const bar = document.getElementById('chat-image-preview-bar');
    const thumb = document.getElementById('chat-image-preview-thumb');
    const fileInput = document.getElementById('chat-image-file-input');
    if (bar) bar.classList.add('hidden');
    if (thumb) thumb.src = '';
    if (fileInput) fileInput.value = '';
}

function stageChatImageFile(file) {
    if (!file) return;
    const allowedTypes = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
    if (!allowedTypes.includes(file.type)) {
        showToast('Chỉ hỗ trợ ảnh PNG, JPG, GIF hoặc WebP', 'warning');
        return;
    }
    if (file.type === 'image/gif' && file.size > 2_500_000) {
        showToast('Ảnh GIF tải lên tối đa 2.5 MB', 'warning');
        return;
    }
    if (file.size > 12_000_000) {
        showToast('Ảnh gốc quá lớn (vui lòng chọn ảnh dưới 12 MB)', 'warning');
        return;
    }

    clearPendingChatImage();
    window.appState.pendingImageFile = file;
    pendingPreviewObjectUrl = URL.createObjectURL(file);

    const bar = document.getElementById('chat-image-preview-bar');
    const thumb = document.getElementById('chat-image-preview-thumb');
    const nameEl = document.getElementById('chat-image-preview-name');
    if (thumb) thumb.src = pendingPreviewObjectUrl;
    if (nameEl) nameEl.textContent = file.name || 'Ảnh đã dán từ clipboard';
    if (bar) bar.classList.remove('hidden');

    document.getElementById('chat-text-input')?.focus();
}

async function compressImageIfNeeded(file) {
    // Keep animated GIFs and small files untouched
    if (file.type === 'image/gif' || file.size <= 350_000) {
        return file;
    }
    return new Promise((resolve) => {
        const img = new Image();
        const url = URL.createObjectURL(file);
        img.onload = () => {
            URL.revokeObjectURL(url);
            const maxDim = 1080;
            let { width, height } = img;
            if (width > maxDim || height > maxDim) {
                const ratio = Math.min(maxDim / width, maxDim / height);
                width = Math.round(width * ratio);
                height = Math.round(height * ratio);
            }
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, width, height);
            canvas.toBlob(
                (blob) => resolve(blob || file),
                'image/webp',
                0.84
            );
        };
        img.onerror = () => {
            URL.revokeObjectURL(url);
            resolve(file);
        };
        img.src = url;
    });
}

function initChatRichMedia() {
    const stickerPopover = document.getElementById('sticker-picker-popover');
    const gifPopover = document.getElementById('gif-picker-popover');
    const stickerBtn = document.getElementById('btn-toggle-sticker-picker');
    const gifBtn = document.getElementById('btn-toggle-gif-picker');
    const uploadBtn = document.getElementById('btn-chat-upload-image');
    const fileInput = document.getElementById('chat-image-file-input');
    const chatInput = document.getElementById('chat-text-input');

    const closeAllPopovers = () => {
        stickerPopover?.classList.add('hidden');
        gifPopover?.classList.add('hidden');
    };

    // Prevent clicks inside either popover from bubbling to document and closing it
    stickerPopover?.addEventListener('click', (e) => e.stopPropagation());
    gifPopover?.addEventListener('click', (e) => e.stopPropagation());

    const highlightTagChips = (selector, activeQuery) => {
        const normActive = (activeQuery || '').trim().toLowerCase();
        document.querySelectorAll(selector).forEach(tag => {
            const isMatch = (tag.getAttribute('data-q') || '').trim().toLowerCase() === normActive;
            tag.classList.toggle('bg-indigo-500/20', isMatch);
            tag.classList.toggle('text-indigo-600', isMatch);
            tag.classList.toggle('dark:text-indigo-300', isMatch);
            tag.classList.toggle('ring-1', isMatch);
            tag.classList.toggle('ring-indigo-500/40', isMatch);
        });
    };

    const resolveMediaItemPreview = (item) => {
        const id = item && typeof item.id === 'string' ? item.id : '';
        const localMatch = /^local:([a-z0-9_]{1,64}\.gif)$/.exec(id);
        if (localMatch) {
            return { id, src: `/images/stickers/${localMatch[1]}`, isLocal: true };
        }
        if (/^[A-Za-z0-9]{8,28}$/.test(id)) {
            return { id, src: `https://media.giphy.com/media/${id}/200w.gif`, isLocal: false };
        }
        return null;
    };

    // 1. ANIMATED STICKER (NHÃN DÁN) PICKER (Curated + Live Search with Client Cache)
    const stickerInput = document.getElementById('sticker-search-input');
    const stickerGrid = document.getElementById('sticker-results-grid');
    const stickerSpinner = document.getElementById('sticker-search-spinner');
    const stickerCache = new Map();
    const MEDIA_CACHE_TTL_MS = 10 * 60 * 1000;
    let stickersLoadedOnce = false;

    const renderStickerItems = (stickers) => {
        if (!stickerGrid) return;
        stickerGrid.innerHTML = '';
        if (!stickers || stickers.length === 0) {
            stickerGrid.innerHTML = `<div class="col-span-3 text-center py-6 text-xs text-dim">Không tìm thấy nhãn dán phù hợp</div>`;
            return;
        }
        stickers.forEach(st => {
            const resolved = resolveMediaItemPreview(st);
            if (!resolved) return;
            const card = document.createElement('button');
            card.type = 'button';
            card.title = st.title || 'Nhãn dán';
            card.className = 'relative h-20 p-1.5 rounded-xl overflow-hidden surface-subtle hover:border-indigo-500 transition group flex items-center justify-center';
            const img = document.createElement('img');
            img.src = resolved.src;
            img.alt = st.title || 'Nhãn dán';
            img.loading = 'lazy';
            img.className = 'w-full h-full object-contain group-hover:scale-110 transition-transform duration-200';
            card.appendChild(img);
            card.onclick = () => {
                window.roomSocket.sendSticker(resolved.id);
                closeAllPopovers();
            };
            stickerGrid.appendChild(card);
        });
    };

    const loadStickers = async (query = '') => {
        if (!stickerGrid) return;
        const cacheKey = (query || '').trim().toLowerCase();
        highlightTagChips('.js-sticker-tag', query);

        const cached = stickerCache.get(cacheKey);
        if (cached && (Date.now() - cached.ts) < MEDIA_CACHE_TTL_MS) {
            if (stickerAbortController) stickerAbortController.abort();
            stickerSpinner?.classList.add('hidden');
            stickersLoadedOnce = true;
            renderStickerItems(cached.items);
            return;
        }

        if (stickerAbortController) stickerAbortController.abort();
        const controller = new AbortController();
        stickerAbortController = controller;

        stickerSpinner?.classList.remove('hidden');
        if (!stickersLoadedOnce || stickerGrid.children.length === 0) {
            stickerGrid.innerHTML = Array.from({ length: 6 }).map(() => `<div class="h-20 rounded-xl skeleton-box"></div>`).join('');
        }

        try {
            const res = await fetch(`/api/stickers/search?q=${encodeURIComponent(query)}`, { signal: controller.signal });
            const data = await res.json();
            if (controller.signal.aborted) return;

            stickersLoadedOnce = true;
            const items = data.stickers || [];
            if (items.length > 0) {
                if (stickerCache.size >= 50) {
                    const oldestKey = stickerCache.keys().next().value;
                    stickerCache.delete(oldestKey);
                }
                stickerCache.set(cacheKey, { ts: Date.now(), items });
            }
            renderStickerItems(items);
        } catch (err) {
            if (err.name === 'AbortError') return;
        } finally {
            if (stickerAbortController === controller) {
                stickerSpinner?.classList.add('hidden');
            }
        }
    };

    stickerBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = stickerPopover?.classList.contains('hidden');
        closeAllPopovers();
        if (willOpen) {
            stickerPopover?.classList.remove('hidden');
            if (!stickersLoadedOnce) loadStickers('');
            stickerInput?.focus();
        }
    });
    document.getElementById('btn-close-sticker-picker')?.addEventListener('click', closeAllPopovers);

    // Combined 😊 button: opens the last used tab (Nhãn dán / GIF); tabs switch between the two pickers
    const MEDIA_TAB_KEY = 'floor2_media_tab';
    const lastMediaTab = () => {
        try { return localStorage.getItem(MEDIA_TAB_KEY) === 'gif' ? 'gif' : 'sticker'; } catch (_) { return 'sticker'; }
    };
    const openMediaTab = (tab) => {
        try { localStorage.setItem(MEDIA_TAB_KEY, tab); } catch (_) {}
        const target = tab === 'gif' ? gifPopover : stickerPopover;
        if (target && target.classList.contains('hidden')) {
            (tab === 'gif' ? gifBtn : stickerBtn)?.click();
        }
    };
    document.getElementById('btn-toggle-media-picker')?.addEventListener('click', (e) => {
        e.stopPropagation();
        const anyOpen = !stickerPopover?.classList.contains('hidden') || !gifPopover?.classList.contains('hidden');
        if (anyOpen) { closeAllPopovers(); return; }
        openMediaTab(lastMediaTab());
    });
    document.querySelectorAll('.js-media-tab').forEach(tabBtn => {
        tabBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            openMediaTab(tabBtn.getAttribute('data-tab') === 'gif' ? 'gif' : 'sticker');
        });
    });

    stickerInput?.addEventListener('input', () => {
        if (stickerSearchTimer) clearTimeout(stickerSearchTimer);
        stickerSearchTimer = setTimeout(() => {
            loadStickers(stickerInput.value.trim());
        }, 260);
    });

    document.querySelectorAll('.js-sticker-tag').forEach(tag => {
        tag.addEventListener('click', (e) => {
            e.stopPropagation();
            const q = tag.getAttribute('data-q') || '';
            if (stickerInput) stickerInput.value = q;
            loadStickers(q);
        });
    });

    // 2. DIVERSE GIF PICKER (30 Curated + Live Search with Client-Side LRU+TTL Cache)
    const gifInput = document.getElementById('gif-search-input');
    const gifGrid = document.getElementById('gif-results-grid');
    const gifSpinner = document.getElementById('gif-search-spinner');
    const gifCache = new Map(); // query -> { ts, gifs }
    let gifsLoadedOnce = false;

    const renderGifItems = (gifs) => {
        if (!gifGrid) return;
        gifGrid.innerHTML = '';
        if (!gifs || gifs.length === 0) {
            gifGrid.innerHTML = `<div class="col-span-2 text-center py-6 text-xs text-dim">Không tìm thấy GIF phù hợp</div>`;
            return;
        }
        const isLocalPack = gifs.some(g => typeof g.id === 'string' && g.id.startsWith('local:'));
        gifGrid.classList.toggle('grid-cols-3', isLocalPack);
        gifGrid.classList.toggle('grid-cols-2', !isLocalPack);

        gifs.forEach(gif => {
            const resolved = resolveMediaItemPreview(gif);
            if (!resolved) return;
            const card = document.createElement('button');
            card.type = 'button';
            card.title = gif.title || 'GIF';
            const img = document.createElement('img');
            img.src = resolved.src;
            img.alt = gif.title || 'GIF';
            img.loading = 'lazy';
            if (resolved.isLocal) {
                card.className = 'relative h-24 p-1.5 rounded-xl overflow-hidden surface-subtle border border-theme hover:border-indigo-500 transition group flex items-center justify-center';
                img.className = 'w-full h-full object-contain group-hover:scale-105 transition-transform duration-200';
            } else {
                card.className = 'relative h-24 rounded-xl overflow-hidden bg-slate-900/50 border border-theme hover:border-indigo-500 transition group';
                img.className = 'w-full h-full object-cover group-hover:scale-105 transition-transform duration-200';
            }
            card.appendChild(img);
            card.onclick = () => {
                window.roomSocket.sendGif(resolved.id);
                closeAllPopovers();
            };
            gifGrid.appendChild(card);
        });
    };

    const loadGifs = async (query = '') => {
        if (!gifGrid) return;
        const cacheKey = (query || '').trim().toLowerCase();
        highlightTagChips('.js-gif-tag', query);

        const cached = gifCache.get(cacheKey);
        if (cached && (Date.now() - cached.ts) < MEDIA_CACHE_TTL_MS) {
            if (gifAbortController) gifAbortController.abort();
            gifSpinner?.classList.add('hidden');
            gifsLoadedOnce = true;
            renderGifItems(cached.gifs);
            return;
        }

        if (gifAbortController) gifAbortController.abort();
        const controller = new AbortController();
        gifAbortController = controller;

        gifSpinner?.classList.remove('hidden');
        if (!gifsLoadedOnce || gifGrid.children.length === 0) {
            gifGrid.innerHTML = Array.from({ length: 6 }).map(() => `<div class="h-24 rounded-xl skeleton-box"></div>`).join('');
        }

        try {
            const res = await fetch(`/api/gifs/search?q=${encodeURIComponent(query)}`, { signal: controller.signal });
            const data = await res.json();
            if (controller.signal.aborted) return;

            gifsLoadedOnce = true;
            const gifs = data.gifs || [];
            if (gifs.length > 0) {
                if (gifCache.size >= 50) {
                    const oldestKey = gifCache.keys().next().value;
                    gifCache.delete(oldestKey);
                }
                gifCache.set(cacheKey, { ts: Date.now(), gifs });
            }
            renderGifItems(gifs);
        } catch (err) {
            if (err.name === 'AbortError') return;
        } finally {
            if (gifAbortController === controller) {
                gifSpinner?.classList.add('hidden');
            }
        }
    };

    gifBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = gifPopover?.classList.contains('hidden');
        closeAllPopovers();
        if (willOpen) {
            gifPopover?.classList.remove('hidden');
            if (!gifsLoadedOnce) loadGifs('');
            gifInput?.focus();
        }
    });
    document.getElementById('btn-close-gif-picker')?.addEventListener('click', closeAllPopovers);

    gifInput?.addEventListener('input', () => {
        if (gifSearchTimer) clearTimeout(gifSearchTimer);
        gifSearchTimer = setTimeout(() => {
            loadGifs(gifInput.value.trim());
        }, 260);
    });

    document.querySelectorAll('.js-gif-tag').forEach(tag => {
        tag.addEventListener('click', (e) => {
            e.stopPropagation();
            const q = tag.getAttribute('data-q') || '';
            if (gifInput) gifInput.value = q;
            loadGifs(q);
        });
    });

    // 3. IMAGE UPLOAD + CLIPBOARD PASTE (Ctrl+V)
    uploadBtn?.addEventListener('click', () => {
        closeAllPopovers();
        fileInput?.click();
    });

    fileInput?.addEventListener('change', () => {
        const file = fileInput.files && fileInput.files[0];
        if (file) stageChatImageFile(file);
    });

    document.getElementById('btn-cancel-chat-image')?.addEventListener('click', clearPendingChatImage);

    chatInput?.addEventListener('paste', (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of items) {
            if (item.type && item.type.startsWith('image/')) {
                const file = item.getAsFile();
                if (file) {
                    e.preventDefault();
                    stageChatImageFile(file);
                    break;
                }
            }
        }
    });

    // Close popovers on outside click
    document.addEventListener('click', (e) => {
        if (
            stickerPopover && !stickerPopover.classList.contains('hidden') &&
            !stickerPopover.contains(e.target) && !stickerBtn?.contains(e.target)
        ) {
            stickerPopover.classList.add('hidden');
        }
        if (
            gifPopover && !gifPopover.classList.contains('hidden') &&
            !gifPopover.contains(e.target) && !gifBtn?.contains(e.target)
        ) {
            gifPopover.classList.add('hidden');
        }
    });
}

// =============================================================================
// IMAGE / GIF LIGHTBOX MODAL
// =============================================================================
window.openImageLightbox = function (url, senderName) {
    const modal = document.getElementById('image-lightbox-modal');
    const img = document.getElementById('lightbox-img');
    const cap = document.getElementById('lightbox-caption');
    if (!modal || !img) return;
    img.src = url;
    if (cap) cap.textContent = senderName ? `Gửi bởi ${senderName}` : '';
    modal.classList.remove('hidden');
};

function initLightbox() {
    const modal = document.getElementById('image-lightbox-modal');
    const close = () => modal?.classList.add('hidden');
    document.getElementById('btn-close-lightbox')?.addEventListener('click', close);
    modal?.addEventListener('click', (e) => {
        if (e.target === modal) close();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
            close();
        }
    });
}

// =============================================================================
// QUICK SUGGESTIONS & DYNAMIC RELATED SONGS STRIP (10 ITEMS, DRAG + WHEEL + ARROWS)
// =============================================================================
const SUGGESTIONS_CACHE_KEY = 'floor2_suggestions_v4';
const SUGGESTIONS_CACHE_TTL_MS = 5 * 60 * 1000;
const RELATED_STORAGE_KEY = 'floor2_related_cache_v1';
const RELATED_STORAGE_MAX = 30;
const RELATED_CACHE_TTL_MS = 15 * 60 * 1000;
const MAX_SUGGESTION_CHIPS = 10;

const roomQuickSugState = {
    lastSig: '',
    fetchingSig: '',
};

// Chips to show when the current track has no related songs (or none is playing):
// the room's member-taste suggestions first, then real-time trending — never a stale hardcoded list.
function getFallbackSuggestionItems() {
    const roomItems = window.appState.roomId ? window.appState.roomSuggestions : null;
    if (Array.isArray(roomItems) && roomItems.length > 0) return roomItems;
    return window.appState.suggestions || [];
}

function quickSuggestionsHeaderText() {
    const mode = window.appState.roomSuggestionsMode || 'trending';
    const members = window.appState.roomSuggestionMembers || [];
    if (mode === 'members_history' && members.length) {
        const shown = members.slice(0, 3).join(', ') + (members.length > 3 ? '…' : '');
        return `✨ Gợi ý theo gu của ${shown} — Gõ để tìm tức thì`;
    }
    if (mode === 'room_history') return '✨ Gợi ý theo lịch sử nghe của phòng — Gõ để tìm tức thì';
    return '🔥 Đang thịnh hành tại Việt Nam — Gõ tên bài hoặc dán link YouTube để tìm';
}

async function loadRoomQuickSuggestions(roomId) {
    if (!roomId) return false;
    const rd = window.appState.roomData;
    const histTail = ((rd && rd.history) || []).slice(-4).map(t => t.id).join(',');
    const curId = (rd && rd.current_track && rd.current_track.id) || '';
    // Suggestions follow the ONLINE members' tastes, so a join/leave must refresh them too
    const memberSig = ((rd && rd.users) || []).map(u => u.user_id || u.id || u.name).sort().join(',');
    const queueSig = ((rd && rd.queue) || []).length;
    const sig = `${roomId}|${curId}|${histTail}|${memberSig}|${queueSig}`;

    if (roomQuickSugState.lastSig === sig && Array.isArray(window.appState.roomSuggestions)) {
        return false;
    }
    if (roomQuickSugState.fetchingSig === sig) {
        return false;
    }
    roomQuickSugState.fetchingSig = sig;

    try {
        const params = new URLSearchParams({ limit: '10' });
        if (window.appState.inviteToken) params.set('invite', window.appState.inviteToken);
        const res = await fetch(`/api/rooms/${encodeURIComponent(roomId)}/suggestions?${params.toString()}`, {
            headers: getAuthHeaders(),
        });
        if (!res.ok) return false;
        const data = await res.json();
        const items = Array.isArray(data.suggestions) ? data.suggestions : [];
        if (items.length > 0 && window.appState.roomId === roomId) {
            window.appState.roomSuggestions = items;
            window.appState.roomSuggestionsMode = data.mode || 'trending';
            window.appState.roomSuggestionMembers = Array.isArray(data.seed_members) ? data.seed_members : [];
            roomQuickSugState.lastSig = sig;
            // Also refresh idle quick-add chips strip if no track is playing
            if (!relatedSuggestionsState.currentItems) {
                renderQuickSuggestionChips(items, false);
            }
            return true;
        }
    } catch (_) {
        // Fallback silently to curated suggestions
    } finally {
        if (roomQuickSugState.fetchingSig === sig) {
            roomQuickSugState.fetchingSig = '';
        }
    }
    return false;
}

const relatedSuggestionsState = {
    cache: new Map(), // videoId -> { ts, items }
    lastVideoId: null,
    currentItems: null, // raw related items for active track (null = default curated)
    abortController: null,
    suppressClickUntil: 0,
    scrollControlsBound: false,
    prefetchTimer: null,
    prefetchingIds: new Set(),
};

function hydrateRelatedCacheFromStorage() {
    try {
        const raw = sessionStorage.getItem(RELATED_STORAGE_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return;
        const now = Date.now();
        Object.entries(parsed).forEach(([vid, entry]) => {
            if (entry && Array.isArray(entry.items) && entry.items.length > 0 && (now - entry.ts) < RELATED_CACHE_TTL_MS) {
                relatedSuggestionsState.cache.set(vid, { ts: entry.ts, items: entry.items });
            }
        });
    } catch (_) {}
}

function persistRelatedCacheToStorage() {
    try {
        const now = Date.now();
        const entries = Array.from(relatedSuggestionsState.cache.entries())
            .filter(([, entry]) => entry && Array.isArray(entry.items) && (now - entry.ts) < RELATED_CACHE_TTL_MS)
            .slice(-RELATED_STORAGE_MAX);
        const obj = {};
        entries.forEach(([vid, entry]) => {
            obj[vid] = entry;
        });
        sessionStorage.setItem(RELATED_STORAGE_KEY, JSON.stringify(obj));
    } catch (_) {}
}

function setRelatedCacheEntry(vid, items) {
    if (!vid || !Array.isArray(items) || items.length === 0) return;
    if (relatedSuggestionsState.cache.has(vid)) {
        relatedSuggestionsState.cache.delete(vid);
    } else if (relatedSuggestionsState.cache.size >= 60) {
        const oldestKey = relatedSuggestionsState.cache.keys().next().value;
        relatedSuggestionsState.cache.delete(oldestKey);
    }
    relatedSuggestionsState.cache.set(vid, { ts: Date.now(), items });
    persistRelatedCacheToStorage();
}

function getFreshRelatedCache(vid) {
    if (!vid) return null;
    const cached = relatedSuggestionsState.cache.get(vid);
    if (cached && (Date.now() - cached.ts) < RELATED_CACHE_TTL_MS && Array.isArray(cached.items) && cached.items.length > 0) {
        return cached.items;
    }
    return null;
}

function prefetchRelatedForNextTrack(queue) {
    if (relatedSuggestionsState.prefetchTimer) {
        clearTimeout(relatedSuggestionsState.prefetchTimer);
        relatedSuggestionsState.prefetchTimer = null;
    }
    if (!Array.isArray(queue) || queue.length === 0) return;
    const nextVid = queue[0] && queue[0].id;
    if (!nextVid) return;
    if (getFreshRelatedCache(nextVid) || relatedSuggestionsState.prefetchingIds.has(nextVid)) return;

    relatedSuggestionsState.prefetchTimer = setTimeout(async () => {
        relatedSuggestionsState.prefetchTimer = null;
        if (getFreshRelatedCache(nextVid) || relatedSuggestionsState.prefetchingIds.has(nextVid)) return;
        relatedSuggestionsState.prefetchingIds.add(nextVid);
        try {
            const res = await fetch(`/api/youtube/related/${encodeURIComponent(nextVid)}?limit=14`);
            if (!res.ok) return;
            const data = await res.json();
            const items = Array.isArray(data.suggestions) ? data.suggestions : [];
            if (items.length > 0) {
                setRelatedCacheEntry(nextVid, items);
                // If user already switched to this track while prefetch was in-flight, apply instantly!
                if (relatedSuggestionsState.lastVideoId === nextVid && !relatedSuggestionsState.currentItems) {
                    relatedSuggestionsState.currentItems = items;
                    renderQuickSuggestionChips(items, true);
                }
            }
        } catch (_) {
            // Silent background prefetch
        } finally {
            relatedSuggestionsState.prefetchingIds.delete(nextVid);
        }
    }, 900);
}

hydrateRelatedCacheFromStorage();

function updateSuggestionsNavButtons() {
    const container = document.getElementById('quick-suggestions-chips');
    const prevBtn = document.getElementById('btn-suggestions-prev');
    const nextBtn = document.getElementById('btn-suggestions-next');
    if (!container || !prevBtn || !nextBtn) return;

    const maxScroll = Math.max(0, container.scrollWidth - container.clientWidth);
    prevBtn.disabled = container.scrollLeft <= 2;
    nextBtn.disabled = container.scrollLeft >= maxScroll - 2;
}

function initSuggestionsHorizontalScroll() {
    if (relatedSuggestionsState.scrollControlsBound) return;
    const container = document.getElementById('quick-suggestions-chips');
    if (!container) return;
    relatedSuggestionsState.scrollControlsBound = true;

    let isDown = false;
    let startX = 0;
    let startScrollLeft = 0;
    let dragged = false;

    container.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        isDown = true;
        dragged = false;
        startX = e.pageX;
        startScrollLeft = container.scrollLeft;
        container.classList.add('cursor-grabbing');
    });

    window.addEventListener('mousemove', (e) => {
        if (!isDown) return;
        const dx = e.pageX - startX;
        if (Math.abs(dx) > 6) {
            dragged = true;
            e.preventDefault();
            container.scrollLeft = startScrollLeft - dx * 1.15;
            updateSuggestionsNavButtons();
        }
    });

    const endDrag = () => {
        if (!isDown) return;
        isDown = false;
        container.classList.remove('cursor-grabbing');
        if (dragged) {
            // Prevent accidental song click right after dragging
            relatedSuggestionsState.suppressClickUntil = Date.now() + 180;
        }
    };
    window.addEventListener('mouseup', endDrag);

    // Mouse wheel vertical scroll -> smooth horizontal scroll while hovering over chips
    container.addEventListener('wheel', (e) => {
        if (Math.abs(e.deltaY) > Math.abs(e.deltaX) && container.scrollWidth > container.clientWidth) {
            e.preventDefault();
            container.scrollLeft += e.deltaY * 0.9;
            updateSuggestionsNavButtons();
        }
    }, { passive: false });

    container.addEventListener('scroll', updateSuggestionsNavButtons, { passive: true });
    window.addEventListener('resize', updateSuggestionsNavButtons, { passive: true });

    document.getElementById('btn-suggestions-prev')?.addEventListener('click', () => {
        container.scrollBy({ left: -340, behavior: 'smooth' });
    });
    document.getElementById('btn-suggestions-next')?.addEventListener('click', () => {
        container.scrollBy({ left: 340, behavior: 'smooth' });
    });
}

function resetRelatedSuggestions() {
    if (relatedSuggestionsState.abortController) {
        relatedSuggestionsState.abortController.abort();
        relatedSuggestionsState.abortController = null;
    }
    if (relatedSuggestionsState.prefetchTimer) {
        clearTimeout(relatedSuggestionsState.prefetchTimer);
        relatedSuggestionsState.prefetchTimer = null;
    }
    relatedSuggestionsState.lastVideoId = null;
    relatedSuggestionsState.currentItems = null;
    renderQuickSuggestionChips(getFallbackSuggestionItems(), false);
}

function renderQuickSuggestionSkeletons() {
    const container = document.getElementById('quick-suggestions-chips');
    const titleEl = document.getElementById('quick-suggestions-title');
    const badgeEl = document.getElementById('quick-suggestions-badge');
    if (titleEl) titleEl.textContent = '✨ Đang tìm bài hát liên quan...';
    if (badgeEl) {
        badgeEl.textContent = 'Đang tải...';
        badgeEl.classList.remove('hidden');
    }
    if (!container) return;
    container.classList.remove('opacity-60');
    container.innerHTML = Array.from({ length: 5 }).map(() => `
        <div class="surface-subtle flex items-center gap-2.5 pl-1.5 pr-3 py-1.5 rounded-full flex-shrink-0 w-52">
            <div class="w-7 h-7 rounded-full skeleton-box flex-shrink-0"></div>
            <div class="flex-1 space-y-1">
                <div class="h-2.5 w-28 rounded skeleton-box"></div>
                <div class="h-2 w-16 rounded skeleton-box"></div>
            </div>
        </div>
    `).join('');
}

function renderQuickSuggestionChips(songs, isRelated = false) {
    const container = document.getElementById('quick-suggestions-chips');
    const titleEl = document.getElementById('quick-suggestions-title');
    const badgeEl = document.getElementById('quick-suggestions-badge');
    if (titleEl) {
        titleEl.textContent = isRelated ? '✨ Gợi ý liên quan' : 'Thêm nhanh 1 chạm';
    }
    if (badgeEl) {
        badgeEl.textContent = 'Liên quan';
        badgeEl.classList.toggle('hidden', !isRelated);
    }
    if (!container) return;
    container.classList.remove('opacity-60');

    const roomData = window.appState.roomData;
    const playingId = roomData && roomData.current_track ? roomData.current_track.id : null;
    const queuedIds = new Set(((roomData && roomData.queue) || []).map(t => t.id));

    // Exclude currently playing track; keep queued tracks out when showing related songs
    let filtered = (songs || []).filter(s => s && s.id && s.id !== playingId && (!isRelated || !queuedIds.has(s.id)));
    if (filtered.length === 0 && isRelated) {
        filtered = (songs || []).filter(s => s && s.id && s.id !== playingId);
    }
    if (filtered.length === 0) {
        filtered = window.appState.suggestions || [];
        if (titleEl) titleEl.textContent = 'Thêm nhanh 1 chạm';
        if (badgeEl) badgeEl.classList.add('hidden');
    }

    // Show up to 10 songs cleanly
    filtered = filtered.slice(0, MAX_SUGGESTION_CHIPS);

    container.innerHTML = '';
    filtered.forEach(song => {
        const isQueued = queuedIds.has(song.id);
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.draggable = false;
        chip.className = 'surface-subtle flex items-center gap-2.5 pl-1.5 pr-3 py-1.5 rounded-full text-left flex-shrink-0 group hover:border-indigo-500/40 transition';
        chip.title = `${song.title}${song.author ? ' — ' + song.author : ''}${song.duration_str ? ' (' + song.duration_str + ')' : ''}`;

        const subMeta = [song.author, song.duration_str].filter(Boolean).join(' • ');
        chip.innerHTML = `
            <img src="${safeThumbUrl(song.id)}" alt="" draggable="false" class="w-7 h-7 rounded-full object-cover shadow-sm flex-shrink-0 pointer-events-none" loading="lazy">
            <div class="min-w-0 max-w-[175px]">
                <div class="text-xs font-semibold text-main truncate group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition">${escapeHtml(song.title)}</div>
                ${subMeta ? `<div class="text-[10px] text-dim truncate leading-tight">${escapeHtml(subMeta)}</div>` : ''}
            </div>
            <span class="js-chip-plus w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold transition flex-shrink-0 ${
                isQueued
                    ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                    : 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 group-hover:bg-indigo-600 group-hover:text-white'
            }">${isQueued ? '✓' : '+'}</span>
        `;

        chip.onclick = (e) => {
            if (Date.now() < relatedSuggestionsState.suppressClickUntil) {
                e.preventDefault();
                e.stopPropagation();
                return;
            }
            if (!canAddTrackInCurrentRoom()) return;
            window.roomSocket.addTrack(song);
            const badge = chip.querySelector('.js-chip-plus');
            if (badge) {
                badge.textContent = '✓';
                badge.className = 'js-chip-plus w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold transition flex-shrink-0 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400';
            }
        };

        container.appendChild(chip);
    });

    requestAnimationFrame(updateSuggestionsNavButtons);
}

function refreshVisibleSuggestionChips() {
    if (relatedSuggestionsState.currentItems && relatedSuggestionsState.currentItems.length > 0) {
        renderQuickSuggestionChips(relatedSuggestionsState.currentItems, true);
    } else {
        renderQuickSuggestionChips(getFallbackSuggestionItems(), false);
    }
}

async function loadRelatedSuggestionsForTrack(track, preloadedItems = null) {
    const vid = track && track.id ? track.id : null;
    if (!vid) {
        resetRelatedSuggestions();
        return;
    }

    // 0ms Instant Render if server already pushed pre-warmed related tracks over WebSocket
    if (Array.isArray(preloadedItems) && preloadedItems.length > 0) {
        if (relatedSuggestionsState.abortController) {
            relatedSuggestionsState.abortController.abort();
            relatedSuggestionsState.abortController = null;
        }
        relatedSuggestionsState.lastVideoId = vid;
        setRelatedCacheEntry(vid, preloadedItems);
        relatedSuggestionsState.currentItems = preloadedItems;
        renderQuickSuggestionChips(preloadedItems, true);
        return;
    }

    if (relatedSuggestionsState.lastVideoId === vid && relatedSuggestionsState.currentItems) {
        renderQuickSuggestionChips(relatedSuggestionsState.currentItems, true);
        return;
    }

    relatedSuggestionsState.lastVideoId = vid;

    // 0ms Instant Client-Side (Memory + sessionStorage) Cache Hit
    const cachedItems = getFreshRelatedCache(vid);
    if (cachedItems) {
        if (relatedSuggestionsState.abortController) {
            relatedSuggestionsState.abortController.abort();
            relatedSuggestionsState.abortController = null;
        }
        relatedSuggestionsState.currentItems = cachedItems;
        renderQuickSuggestionChips(cachedItems, true);
        return;
    }

    if (relatedSuggestionsState.abortController) {
        relatedSuggestionsState.abortController.abort();
    }
    const controller = new AbortController();
    relatedSuggestionsState.abortController = controller;

    // Stale-While-Revalidate: keep existing chips clickable & gently dimmed instead of wiping to blank skeletons!
    const container = document.getElementById('quick-suggestions-chips');
    const badgeEl = document.getElementById('quick-suggestions-badge');
    const hasExistingChips = !!(container && container.querySelector('button'));
    if (hasExistingChips) {
        container.classList.add('opacity-60', 'transition-opacity');
        if (badgeEl) {
            badgeEl.textContent = 'Đang cập nhật...';
            badgeEl.classList.remove('hidden');
        }
    } else {
        renderQuickSuggestionSkeletons();
    }

    try {
        const res = await fetch(`/api/youtube/related/${encodeURIComponent(vid)}?limit=14`, {
            signal: controller.signal,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (controller.signal.aborted) return;

        const items = Array.isArray(data.suggestions) ? data.suggestions : [];
        if (items.length > 0) {
            setRelatedCacheEntry(vid, items);
            relatedSuggestionsState.currentItems = items;
            renderQuickSuggestionChips(items, true);
        } else {
            relatedSuggestionsState.currentItems = null;
            renderQuickSuggestionChips(getFallbackSuggestionItems(), false);
        }
    } catch (err) {
        if (err.name === 'AbortError') return;
        relatedSuggestionsState.currentItems = null;
        renderQuickSuggestionChips(getFallbackSuggestionItems(), false);
    } finally {
        if (relatedSuggestionsState.abortController === controller) {
            relatedSuggestionsState.abortController = null;
            container?.classList.remove('opacity-60');
        }
    }
}

async function initQuickSuggestions() {
    initSuggestionsHorizontalScroll();
    try {
        let suggestions = null;
        try {
            const raw = sessionStorage.getItem(SUGGESTIONS_CACHE_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (parsed && Array.isArray(parsed.items) && (Date.now() - parsed.ts) < SUGGESTIONS_CACHE_TTL_MS) {
                    suggestions = parsed.items;
                }
            }
        } catch (_) {}

        if (!suggestions) {
            const res = await fetch('/api/suggestions');
            const data = await res.json();
            suggestions = data.suggestions || [];
            try {
                sessionStorage.setItem(SUGGESTIONS_CACHE_KEY, JSON.stringify({ ts: Date.now(), items: suggestions }));
            } catch (_) {}
        }

        window.appState.suggestions = suggestions;
        // Only render defaults if we haven't already loaded related songs for an active track
        if (!relatedSuggestionsState.currentItems) {
            renderQuickSuggestionChips(suggestions, false);
        }
    } catch (e) {
        console.error('Error fetching suggestions:', e);
    }
}

// =============================================================================
// SYNCED LYRICS ENGINE (KARAOKE-STYLE REALTIME HIGHLIGHT & SEEK ON CLICK)
// =============================================================================
// v2: values are now PERSONAL adjustments on top of the room's shared calibration
const LYRICS_OFFSETS_STORAGE_KEY = 'floor2_lyrics_offsets_v2';
// Bump to bypass browser-cached /api/lyrics responses from the old (unaligned) engine
const LYRICS_API_VERSION = '3';

const lyricsState = {
    cache: new Map(), // videoId -> { ts, data }
    lastVideoId: null,
    lines: [],        // [{ time, text }]
    plainLines: [],
    synced: false,
    activeIndex: -1,
    offsetSec: 0,        // effective = sharedOffset + personalOffset
    sharedOffset: 0,     // room-wide calibration for this video (set by Host, stored on server)
    personalOffset: 0,   // this device only (e.g. Bluetooth latency), stored in localStorage
    shareTimer: null,
    syncConfidence: '',
    abortController: null,
    userScrollPauseUntil: 0,
    controlsBound: false,
};

function getSavedLyricsOffset(vid) {
    if (!vid) return 0;
    try {
        const raw = localStorage.getItem(LYRICS_OFFSETS_STORAGE_KEY);
        if (!raw) return 0;
        const map = JSON.parse(raw);
        const v = map && typeof map[vid] === 'number' ? map[vid] : 0;
        return Math.max(-90, Math.min(90, Math.round(v * 10) / 10));
    } catch (_) {
        return 0;
    }
}

function saveLyricsOffset(vid, val) {
    if (!vid) return;
    try {
        let map = {};
        const raw = localStorage.getItem(LYRICS_OFFSETS_STORAGE_KEY);
        if (raw) map = JSON.parse(raw) || {};
        if (Math.abs(val) < 0.05) {
            delete map[vid];
        } else {
            map[vid] = Math.round(val * 10) / 10;
            const keys = Object.keys(map);
            if (keys.length > 50) delete map[keys[0]];
        }
        localStorage.setItem(LYRICS_OFFSETS_STORAGE_KEY, JSON.stringify(map));
    } catch (_) {}
}

function updateLyricsOffsetUI() {
    lyricsState.offsetSec = Math.round((lyricsState.sharedOffset + lyricsState.personalOffset) * 10) / 10;
    const valEl = document.getElementById('lyrics-offset-value');
    if (valEl) {
        const v = lyricsState.offsetSec;
        valEl.textContent = v === 0 ? '0s' : `${v > 0 ? '+' : ''}${v.toFixed(1)}s`;
        const fmt = (x) => `${x > 0 ? '+' : ''}${x.toFixed(1)}s`;
        valEl.title = window.appState.isHost
            ? `Chỉnh lệch cho CẢ PHÒNG (lưu cho video này, lần sau tự áp dụng). Hiện tại: ${fmt(lyricsState.sharedOffset)}. Bấm để đặt lại 0.`
            : `Phòng: ${fmt(lyricsState.sharedOffset)} · Riêng máy bạn: ${fmt(lyricsState.personalOffset)}. Bấm để bỏ chỉnh riêng.`;
    }
}

function refreshLyricsAfterOffsetChange() {
    updateLyricsOffsetUI();
    if (window.syncPlayer) window.updateLyricsHighlight(window.syncPlayer.getSmoothCurrentTime(), true);
}

function setSharedLyricsOffset(val) {
    lyricsState.sharedOffset = Math.max(-90, Math.min(90, Math.round((Number(val) || 0) * 10) / 10));
    refreshLyricsAfterOffsetChange();
}
window.setSharedLyricsOffset = setSharedLyricsOffset;

function adjustLyricsOffset(delta, reset = false) {
    const vid = lyricsState.lastVideoId;
    if (!vid) return;
    if (window.appState.isHost) {
        // Host calibrates for the whole room; debounce so rapid clicks send one update
        lyricsState.sharedOffset = reset ? 0 : Math.max(-90, Math.min(90, Math.round((lyricsState.sharedOffset + delta) * 10) / 10));
        refreshLyricsAfterOffsetChange();
        clearTimeout(lyricsState.shareTimer);
        const value = lyricsState.sharedOffset;
        lyricsState.shareTimer = setTimeout(() => {
            if (window.roomSocket && typeof window.roomSocket.setLyricsOffset === 'function') {
                window.roomSocket.setLyricsOffset(vid, value);
            }
        }, 450);
    } else {
        lyricsState.personalOffset = reset ? 0 : Math.max(-90, Math.min(90, Math.round((lyricsState.personalOffset + delta) * 10) / 10));
        saveLyricsOffset(vid, lyricsState.personalOffset);
        refreshLyricsAfterOffsetChange();
    }
}

function initLyricsControls() {
    if (lyricsState.controlsBound) return;
    lyricsState.controlsBound = true;

    const viewport = document.getElementById('lyrics-lines-container');
    if (viewport) {
        const pauseAutoScroll = () => {
            lyricsState.userScrollPauseUntil = Date.now() + 4000;
        };
        viewport.addEventListener('wheel', pauseAutoScroll, { passive: true });
        viewport.addEventListener('touchstart', pauseAutoScroll, { passive: true });
    }

    document.getElementById('btn-lyrics-offset-minus')?.addEventListener('click', (e) => {
        adjustLyricsOffset(-(e.shiftKey ? 2.0 : 0.5));
    });

    document.getElementById('btn-lyrics-offset-plus')?.addEventListener('click', (e) => {
        adjustLyricsOffset(e.shiftKey ? 2.0 : 0.5);
    });

    document.getElementById('lyrics-offset-value')?.addEventListener('click', () => {
        adjustLyricsOffset(0, true);
    });
}

function resetLyricsState() {
    if (lyricsState.abortController) {
        lyricsState.abortController.abort();
        lyricsState.abortController = null;
    }
    lyricsState.lastVideoId = null;
    lyricsState.lines = [];
    lyricsState.plainLines = [];
    lyricsState.synced = false;
    lyricsState.activeIndex = -1;
    lyricsState.sharedOffset = 0;
    lyricsState.personalOffset = 0;
    lyricsState.syncConfidence = '';
    clearTimeout(lyricsState.shareTimer);
    updateLyricsOffsetUI();

    const badge = document.getElementById('lyrics-sync-badge');
    const offsetCtrl = document.getElementById('lyrics-offset-controls');
    const container = document.getElementById('lyrics-lines-container');
    if (badge) badge.textContent = 'Chờ nhạc';
    if (offsetCtrl) offsetCtrl.classList.add('hidden');
    if (container) {
        container.innerHTML = `
            <div class="h-full flex flex-col items-center justify-center text-center px-4 text-white/65">
                <span class="text-2xl mb-2">🎤</span>
                <p class="text-xs sm:text-sm font-semibold text-white/85">Chưa có bài hát nào đang phát</p>
                <p class="text-[11px] text-white/60 mt-1">Thêm một bài hát để lời bài hát tự động chạy và highlight theo nhạc!</p>
            </div>
        `;
    }
}

function renderLyricsData(data) {
    const container = document.getElementById('lyrics-lines-container');
    const badge = document.getElementById('lyrics-sync-badge');
    const offsetCtrl = document.getElementById('lyrics-offset-controls');
    if (!container) return;

    lyricsState.activeIndex = -1;
    lyricsState.lines = (data && data.synced && Array.isArray(data.lines)) ? data.lines : [];
    lyricsState.plainLines = (data && Array.isArray(data.plain_lines)) ? data.plain_lines : [];
    lyricsState.synced = lyricsState.lines.length > 0;

    if (lyricsState.synced) {
        lyricsState.syncConfidence = (data && data.sync_confidence) || '';
        if (badge) {
            const autoOff = Number(data && data.auto_offset) || 0;
            const src = (data && data.source) || '';
            const calibrated = Math.abs(lyricsState.sharedOffset) >= 0.05;
            badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-500/20 border border-emerald-400/35 text-[10px] font-bold text-emerald-200';
            if (calibrated) {
                badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>Đã hiệu chỉnh`;
                badge.title = 'Chủ phòng đã căn chỉnh lời cho video này — áp dụng cho mọi người';
            } else if (lyricsState.syncConfidence === 'low') {
                // No caption track & no same-length lyric version: timing may drift with MV intros
                badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-500/20 border border-amber-400/40 text-[10px] font-bold text-amber-200';
                badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span>Có thể lệch`;
                badge.title = window.appState.isHost
                    ? 'Video này khác bản thu gốc nên lời có thể lệch. Dùng nút −/+ để chỉnh 1 lần, cả phòng và các lần phát sau đều được áp dụng.'
                    : 'Video này khác bản thu gốc nên lời có thể lệch. Chủ phòng có thể chỉnh lệch cho cả phòng bằng nút −/+.';
            } else if (Math.abs(autoOff) >= 0.35) {
                badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>Đã khớp MV`;
                badge.title = `Tự động căn khớp lời theo MV YouTube (${autoOff > 0 ? '+' : ''}${autoOff.toFixed(1)}s)`;
            } else if (src === 'YouTube') {
                badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>Khớp YouTube`;
                badge.title = 'Lời đồng bộ trực tiếp theo video YouTube đang phát';
            } else {
                badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>Đồng bộ theo nhạc`;
                badge.title = 'Lời bài hát đồng bộ thời gian thực';
            }
        }
        if (offsetCtrl) offsetCtrl.classList.remove('hidden');

        container.innerHTML = '';
        lyricsState.lines.forEach((line, idx) => {
            const p = document.createElement('div');
            p.className = 'lyric-line synced-clickable';
            p.dataset.index = String(idx);
            p.dataset.time = String(line.time);
            p.textContent = line.text;
            p.title = `Bấm để tua đến ${window.syncPlayer ? window.syncPlayer.formatTime(line.time) : ''}`;
            p.onclick = () => {
                const targetTime = Math.max(0, line.time - lyricsState.offsetSec);
                if (window.appState.isHost) {
                    // Host is Master: clicking a lyric line seeks for the whole room
                    window.syncPlayer.seekLocal(targetTime, true);
                    window.roomSocket.controlPlayback('seek', targetTime);
                } else {
                    // Regular members: local-only seek on their own device
                    window.syncPlayer.seekLocal(targetTime, false);
                }
                lyricsState.userScrollPauseUntil = 0;
                window.updateLyricsHighlight(targetTime, true);
            };
            container.appendChild(p);
        });
        container.scrollTop = 0;
        if (window.syncPlayer) {
            window.updateLyricsHighlight(window.syncPlayer.getSmoothCurrentTime(), true);
        }
        return;
    }

    if (offsetCtrl) offsetCtrl.classList.add('hidden');

    if (lyricsState.plainLines.length > 0) {
        if (badge) {
            badge.className = 'inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-indigo-500/20 border border-indigo-400/30 text-[10px] font-bold text-indigo-200';
            badge.textContent = 'Lời đầy đủ';
        }
        container.innerHTML = '';
        lyricsState.plainLines.forEach(lineText => {
            const p = document.createElement('div');
            p.className = 'lyric-line text-white/85';
            p.textContent = lineText;
            container.appendChild(p);
        });
        container.scrollTop = 0;
        return;
    }

    if (badge) {
        badge.className = 'inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-white/10 border border-white/15 text-[10px] font-bold text-white/70';
        badge.textContent = 'Chưa có lời';
    }
    container.innerHTML = `
        <div class="h-full flex flex-col items-center justify-center text-center px-6 text-white/65">
            <span class="text-2xl mb-2">🎼</span>
            <p class="text-xs sm:text-sm font-semibold text-white/85">Chưa tìm thấy lời bài hát cho bản này</p>
            <p class="text-[11px] text-white/60 mt-1">Một số bản nhạc không lời, remix hoặc mashup dài có thể chưa có dữ liệu lyric đồng bộ.</p>
        </div>
    `;
}

async function loadLyricsForTrack(track) {
    const vid = track && track.id ? track.id : null;
    if (!vid) {
        resetLyricsState();
        return;
    }

    if (lyricsState.lastVideoId === vid && (lyricsState.lines.length > 0 || lyricsState.plainLines.length > 0)) {
        if (window.syncPlayer && window.syncPlayer.mode === 'lyrics') {
            window.updateLyricsHighlight(window.syncPlayer.getCurrentTime(), true);
        }
        return;
    }

    lyricsState.lastVideoId = vid;
    lyricsState.sharedOffset = Number(track.lyrics_offset) || 0;
    lyricsState.personalOffset = getSavedLyricsOffset(vid);
    updateLyricsOffsetUI();

    const cached = lyricsState.cache.get(vid);
    if (cached) {
        if (lyricsState.abortController) lyricsState.abortController.abort();
        renderLyricsData(cached);
        return;
    }

    if (lyricsState.abortController) lyricsState.abortController.abort();
    const controller = new AbortController();
    lyricsState.abortController = controller;

    const badge = document.getElementById('lyrics-sync-badge');
    const container = document.getElementById('lyrics-lines-container');
    if (badge) {
        badge.className = 'inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-white/10 border border-white/15 text-[10px] font-bold text-indigo-200';
        badge.textContent = 'Đang tìm lời...';
    }
    if (container) {
        container.innerHTML = `
            <div class="space-y-3 px-4 py-4">
                <div class="h-4 w-3/4 rounded-lg bg-white/10 animate-pulse"></div>
                <div class="h-4 w-2/3 rounded-lg bg-white/15 animate-pulse"></div>
                <div class="h-4 w-4/5 rounded-lg bg-white/10 animate-pulse"></div>
                <div class="h-4 w-1/2 rounded-lg bg-white/10 animate-pulse"></div>
            </div>
        `;
    }

    try {
        const effectiveDuration = Math.round(
            parseInt(track.duration, 10) ||
            (window.syncPlayer ? window.syncPlayer.getDuration() : 0) ||
            0
        );
        const params = new URLSearchParams({
            title: track.title || '',
            author: track.author || '',
            duration: String(effectiveDuration),
            video_id: vid,
            v: LYRICS_API_VERSION,
        });
        const res = await fetch(`/api/lyrics?${params.toString()}`, { signal: controller.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (controller.signal.aborted) return;

        if (lyricsState.cache.size >= 60) {
            const oldestKey = lyricsState.cache.keys().next().value;
            lyricsState.cache.delete(oldestKey);
        }
        lyricsState.cache.set(vid, data);
        renderLyricsData(data);
    } catch (err) {
        if (err.name === 'AbortError') return;
        renderLyricsData({ found: false, synced: false, lines: [], plain_lines: [] });
    }
}
window.loadLyricsForTrack = loadLyricsForTrack;

function updateLyricsHighlight(currentTime, forceScroll = false) {
    if (!lyricsState.synced || lyricsState.lines.length === 0) return;

    const adjustedTime = Number(currentTime || 0) + lyricsState.offsetSec;
    const lines = lyricsState.lines;

    // Find the last line whose timestamp <= adjustedTime (tight 0.08s lead for snappy vocal match)
    let nextIdx = -1;
    for (let i = 0; i < lines.length; i++) {
        if (lines[i].time <= adjustedTime + 0.08) {
            nextIdx = i;
        } else {
            break;
        }
    }

    if (nextIdx === lyricsState.activeIndex && !forceScroll) {
        return;
    }

    const prevIdx = lyricsState.activeIndex;
    lyricsState.activeIndex = nextIdx;

    const container = document.getElementById('lyrics-lines-container');
    if (!container) return;

    const children = container.children;
    for (let i = 0; i < children.length; i++) {
        const el = children[i];
        const isActive = i === nextIdx;
        const isPast = nextIdx >= 0 && i < nextIdx;
        el.classList.toggle('active', isActive);
        el.classList.toggle('past', isPast);
    }

    if (nextIdx >= 0 && children[nextIdx] && (forceScroll || nextIdx !== prevIdx)) {
        if (forceScroll || Date.now() >= lyricsState.userScrollPauseUntil) {
            const activeEl = children[nextIdx];
            const cRect = container.getBoundingClientRect();
            const eRect = activeEl.getBoundingClientRect();
            if (cRect.height > 0) {
                const relTop = (eRect.top - cRect.top) + container.scrollTop;
                const targetScroll = relTop - (cRect.height / 2) + (eRect.height / 2);
                container.scrollTo({
                    top: Math.max(0, targetScroll),
                    behavior: forceScroll ? 'auto' : 'smooth',
                });
            }
        }
    }
}
window.updateLyricsHighlight = updateLyricsHighlight;

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// =============================================================================
// AI FEATURES ("@Em bé floor 2" — Chat Tag, AI DJ, Room Vibe Card & Female Radio MC)
// =============================================================================
const mcRadioState = {
    lastSpokenUid: null,
    fetchingUid: null,
    abortController: null,
    watchdogTimer: null,
    isSpeaking: false,
    currentTracks: [],
};

function updateAiProactiveToggleUI() {
    const btn = document.getElementById('btn-ai-proactive-toggle');
    const lbl = document.getElementById('ai-proactive-toggle-label');
    if (!btn) return;
    const isHost = !!window.appState.isHost;
    const rd = window.appState.roomData;
    const on = !rd || rd.ai_proactive !== false; // default ON
    btn.classList.toggle('hidden', !isHost);
    if (lbl) lbl.textContent = on ? 'Em bé chủ động: Bật' : 'Em bé chủ động: Tắt';
    btn.classList.toggle('border-pink-500/45', on);
    btn.classList.toggle('bg-pink-500/15', on);
    btn.classList.toggle('text-pink-600', on);
    btn.classList.toggle('dark:text-pink-300', on);
}

function updateMcToggleUI() {
    updateAiProactiveToggleUI();
    const btn = document.getElementById('btn-mc-radio-toggle');
    const lbl = document.getElementById('mc-radio-toggle-label');
    if (!btn) return;
    const isHost = !!window.appState.isHost;
    const on = !!(window.appState.roomData && window.appState.roomData.mc_enabled);
    window.appState.mcEnabled = on;
    btn.classList.toggle('hidden', !isHost);
    if (lbl) lbl.textContent = on ? 'MC: Bật' : 'MC: Tắt';
    btn.classList.toggle('border-pink-500/45', on);
    btn.classList.toggle('bg-pink-500/15', on);
    btn.classList.toggle('text-pink-600', on);
    btn.classList.toggle('dark:text-pink-300', on);
}

function stopMcRadioSpeech() {
    if (mcRadioState.abortController) {
        mcRadioState.abortController.abort();
        mcRadioState.abortController = null;
    }
    if (mcRadioState.watchdogTimer) {
        clearTimeout(mcRadioState.watchdogTimer);
        mcRadioState.watchdogTimer = null;
    }
    mcRadioState.fetchingUid = null;
    mcRadioState.isSpeaking = false;
    const audioEl = document.getElementById('mc-tts-audio-player');
    if (audioEl) {
        try {
            audioEl.onended = null;
            audioEl.onerror = null;
            audioEl.pause();
            audioEl.removeAttribute('src');
            audioEl.load();
        } catch (_) {}
    }
    if (window.speechSynthesis) {
        try { window.speechSynthesis.cancel(); } catch (_) {}
    }
    document.getElementById('mc-radio-subtitle-bar')?.classList.add('hidden');
    if (window.syncPlayer && typeof window.syncPlayer.setMcDucking === 'function') {
        window.syncPlayer.setMcDucking(false);
    }
}

function pickNaturalVietnameseFemaleVoice() {
    if (!window.speechSynthesis) return null;
    const voices = (window.speechSynthesis.getVoices() || []).filter(v => /^vi/i.test(v.lang || ''));
    if (voices.length === 0) return null;
    const score = (v) => {
        const n = (v.name || '').toLowerCase();
        let s = 0;
        if (/natural|neural|online/.test(n)) s += 50;      // Edge "Microsoft HoaiMy Online (Natural)"
        if (/hoaimy|hoai my/.test(n)) s += 30;
        if (/microsoft/.test(n)) s += 10;
        if (/an|linh|female|nữ|nu\b/.test(n)) s += 5;
        if (/google/.test(n)) s -= 40;                       // robotic "chị Google" — last resort only
        if (/nam ?minh|male/.test(n)) s -= 60;               // male voices
        return s;
    };
    return voices.sort((a, b) => score(b) - score(a))[0];
}

if (window.speechSynthesis) {
    // Voices load asynchronously in Chrome/Edge — warm the list early
    try { window.speechSynthesis.getVoices(); } catch (_) {}
    window.speechSynthesis.onvoiceschanged = () => { try { window.speechSynthesis.getVoices(); } catch (_) {} };
}

function speakWithBrowserFemaleFallback(scriptText) {
    if (!window.speechSynthesis || !scriptText) {
        stopMcRadioSpeech();
        return;
    }
    const voice = pickNaturalVietnameseFemaleVoice();
    if (!voice) {
        // No Vietnamese voice on this device: never let the browser read Vietnamese with an English voice.
        // Keep the subtitle on screen briefly instead, then restore music volume.
        if (mcRadioState.watchdogTimer) clearTimeout(mcRadioState.watchdogTimer);
        mcRadioState.watchdogTimer = setTimeout(() => stopMcRadioSpeech(), 6000);
        return;
    }
    try {
        window.speechSynthesis.cancel();
        const utter = new SpeechSynthesisUtterance(scriptText);
        utter.lang = voice.lang || 'vi-VN';
        utter.voice = voice;
        const isNatural = voice && /natural|neural|online/i.test(voice.name || '');
        utter.rate = isNatural ? 1.0 : 0.95;
        utter.pitch = isNatural ? 1.0 : 1.05;
        utter.onend = () => stopMcRadioSpeech();
        utter.onerror = () => stopMcRadioSpeech();
        window.speechSynthesis.speak(utter);
    } catch (_) {
        stopMcRadioSpeech();
    }
}

async function triggerMcRadioIntroForTrack(track) {
    const mcOn = !!(window.appState.roomData && window.appState.roomData.mc_enabled);
    if (!mcOn || !track || !track.id || !window.appState.roomId) {
        return;
    }
    const trackKey = track.uid || track.id;
    if (mcRadioState.lastSpokenUid === trackKey || mcRadioState.fetchingUid === trackKey) {
        return;
    }
    stopMcRadioSpeech();
    mcRadioState.fetchingUid = trackKey;

    const controller = new AbortController();
    mcRadioState.abortController = controller;

    // Immediately duck music volume & show intro banner right as the track starts
    const bar = document.getElementById('mc-radio-subtitle-bar');
    const txt = document.getElementById('mc-radio-subtitle-text');
    if (txt) txt.textContent = `🎙️ MC Em bé floor 2 đang giới thiệu bài hát "${track.title || ''}"...`;
    if (bar) bar.classList.remove('hidden');
    if (window.syncPlayer && typeof window.syncPlayer.setMcDucking === 'function') {
        window.syncPlayer.setMcDucking(true);
    }

    try {
        const params = new URLSearchParams();
        if (window.appState.inviteToken) params.set('invite', window.appState.inviteToken);
        params.set('track_uid', trackKey);
        const url = `/api/rooms/${encodeURIComponent(window.appState.roomId)}/ai/mc-intro?${params.toString()}`;
        const fetchOpts = () => ({ headers: getAuthHeaders(), signal: controller.signal, cache: 'no-store' });

        let res = await fetch(url, fetchOpts());
        if (res.status === 401) {
            await ensureSession(true, true).catch(() => {});
            res = await fetch(url, fetchOpts());
        }
        if (!res.ok) {
            stopMcRadioSpeech();
            return;
        }
        const data = await res.json();
        if (controller.signal.aborted || !(window.appState.roomData && window.appState.roomData.mc_enabled)) {
            stopMcRadioSpeech();
            return;
        }
        if (!data || !data.found || !data.script) {
            stopMcRadioSpeech();
            return;
        }
        // Drop the intro if the room already moved to another song while it was generating
        const nowTrack = window.appState.roomData && window.appState.roomData.current_track;
        const nowKey = nowTrack ? (nowTrack.uid || nowTrack.id) : null;
        if (nowKey !== trackKey || (data.track_uid && data.track_uid !== trackKey && data.track_uid !== track.id)) {
            if (mcRadioState.abortController === controller) stopMcRadioSpeech();
            return;
        }

        mcRadioState.lastSpokenUid = trackKey;
        mcRadioState.fetchingUid = null;
        mcRadioState.isSpeaking = true;

        if (txt) txt.textContent = `🎀 Em bé floor 2: “${data.script}”`;
        if (bar) bar.classList.remove('hidden');
        if (window.syncPlayer && typeof window.syncPlayer.setMcDucking === 'function') {
            window.syncPlayer.setMcDucking(true);
        }

        // Safety watchdog: always restore full music volume within 22s even if browser tab backgrounded
        mcRadioState.watchdogTimer = setTimeout(() => {
            stopMcRadioSpeech();
        }, 22000);

        const audioEl = document.getElementById('mc-tts-audio-player');
        if (data.audio_url && audioEl) {
            audioEl.src = data.audio_url;
            audioEl.volume = 1.0;
            audioEl.onended = () => stopMcRadioSpeech();
            audioEl.onerror = () => speakWithBrowserFemaleFallback(data.script);
            audioEl.play().catch((err) => {
                if (err && err.name === 'NotAllowedError') {
                    // Autoplay blocked: play the Vietnamese MP3 on the next tap/click instead of a browser voice
                    const resume = () => {
                        if (mcRadioState.abortController === controller && audioEl.src) {
                            audioEl.play().catch(() => stopMcRadioSpeech());
                        }
                    };
                    document.addEventListener('pointerdown', resume, { once: true });
                    return;
                }
                speakWithBrowserFemaleFallback(data.script);
            });
        } else {
            speakWithBrowserFemaleFallback(data.script);
        }
    } catch (err) {
        if (err && err.name === 'AbortError') return;
        stopMcRadioSpeech();
    }
}

function updateAutoDjUI() {
    const badge = document.getElementById('ai-auto-dj-badge');
    const btn = document.getElementById('btn-toggle-auto-dj');
    const isOn = !!(window.appState.roomData && window.appState.roomData.auto_dj);
    const isHost = !!window.appState.isHost;

    if (badge) {
        badge.textContent = isOn ? 'Đang bật' : 'Tắt';
        badge.className = isOn
            ? 'px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-600 dark:text-emerald-300 text-[10px] font-extrabold'
            : 'px-2 py-0.5 rounded-full bg-slate-500/15 text-dim text-[10px] font-bold';
    }
    if (btn) {
        btn.disabled = !isHost;
        btn.classList.toggle('opacity-50', !isHost);
        btn.classList.toggle('cursor-not-allowed', !isHost);
        btn.textContent = isOn ? 'Tắt Auto-DJ' : 'Bật Auto-DJ';
        btn.title = isHost ? 'Bật / Tắt tự động nối nhạc khi hết hàng đợi' : 'Chỉ Chủ phòng mới bật/tắt được Auto-DJ';
    }
}

async function loadRoomVibeCard() {
    if (!window.appState.roomId) return;
    const card = document.getElementById('room-vibe-card');
    const emojiEl = document.getElementById('room-vibe-emoji');
    const titleEl = document.getElementById('room-vibe-title');
    const genresEl = document.getElementById('room-vibe-genres');
    const summaryEl = document.getElementById('room-vibe-summary');
    if (!card) return;

    card.classList.remove('hidden');
    if (titleEl) titleEl.textContent = 'Em bé floor 2 đang đọc vị gu nhạc phòng...';
    if (summaryEl) summaryEl.textContent = 'Đang phân tích bài đang phát, hàng đợi và gu của từng thành viên...';

    try {
        const params = new URLSearchParams();
        if (window.appState.inviteToken) params.set('invite', window.appState.inviteToken);
        const qStr = params.toString() ? `?${params.toString()}` : '';
        const res = await fetch(
            `/api/rooms/${encodeURIComponent(window.appState.roomId)}/ai/vibe${qStr}`,
            { headers: getAuthHeaders() }
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (emojiEl) emojiEl.textContent = data.emoji || '✨';
        if (titleEl) titleEl.textContent = data.title || 'Góc nhạc đầy cảm xúc';
        if (summaryEl) summaryEl.textContent = data.summary || '';
        if (genresEl) {
            const genres = Array.isArray(data.genres) ? data.genres : [];
            genresEl.innerHTML = genres.map(g => `
                <span class="px-2 py-0.5 rounded-full surface-subtle text-[10.5px] font-bold text-main">
                    ${escapeHtml(g.name || 'Mix')} <span class="text-indigo-500 dark:text-indigo-300 font-mono-num">${parseInt(g.pct, 10) || 50}%</span>
                </span>
            `).join('');
        }
    } catch (_) {
        if (titleEl) titleEl.textContent = 'Chưa thể đọc vị lúc này';
        if (summaryEl) summaryEl.textContent = 'Hãy thử bấm làm mới lại sau vài giây nhé!';
    }
}

function refreshRoomVibeIfOpen() {
    const card = document.getElementById('room-vibe-card');
    if (card && !card.classList.contains('hidden')) {
        loadRoomVibeCard();
    }
}

async function runAiDjPlaylist(promptText) {
    if (!window.appState.roomId) return;
    if (!canAddTrackInCurrentRoom()) return;

    const listEl = document.getElementById('ai-dj-results-list');
    const hdrEl = document.getElementById('ai-dj-results-header');
    const noteEl = document.getElementById('ai-dj-vibe-note');
    const submitBtn = document.getElementById('btn-submit-ai-dj');

    if (submitBtn) submitBtn.disabled = true;
    if (hdrEl) hdrEl.classList.add('hidden');
    if (listEl) {
        listEl.innerHTML = `
            <div class="text-center py-8 space-y-2">
                <div class="text-2xl animate-bounce">🎀</div>
                <p class="text-xs font-bold text-main">Em bé floor 2 đang lựa bài hợp vibe phòng...</p>
                <p class="text-[11px] text-dim">Đang đối chiếu lịch sử nghe và tìm bản nhạc chuẩn trên YouTube</p>
            </div>
        `;
    }

    try {
        const params = new URLSearchParams();
        if (window.appState.inviteToken) params.set('invite', window.appState.inviteToken);
        const qStr = params.toString() ? `?${params.toString()}` : '';
        const res = await fetch(
            `/api/rooms/${encodeURIComponent(window.appState.roomId)}/ai/dj${qStr}`,
            {
                method: 'POST',
                headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ prompt: promptText || '', count: 5 }),
            }
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(data.detail || 'Không thể tạo playlist AI DJ lúc này', 'warning');
            if (listEl) {
                listEl.innerHTML = `<div class="text-center py-6 text-rose-500 text-xs">${escapeHtml(data.detail || 'Thử lại sau vài giây nhé!')}</div>`;
            }
            return;
        }

        const tracks = Array.isArray(data.tracks) ? data.tracks : [];
        mcRadioState.currentTracks = tracks;

        if (tracks.length === 0) {
            if (listEl) {
                listEl.innerHTML = `<div class="text-center py-6 text-dim text-xs">Chưa tìm thấy bài hát phù hợp. Hãy thử mô tả vibe khác nhé!</div>`;
            }
            return;
        }

        if (noteEl) noteEl.textContent = `🎀 ${data.vibe_note || 'Set nhạc tuyển chọn cho phòng mình'}`;
        if (hdrEl) hdrEl.classList.remove('hidden');

        const addAllBtn = document.getElementById('btn-ai-dj-add-all');
        if (addAllBtn) {
            addAllBtn.textContent = `+ Thêm tất cả (${tracks.length})`;
            addAllBtn.className = 'px-3 py-1 rounded-xl btn-gradient text-[11px] font-extrabold flex-shrink-0';
        }

        if (listEl) {
            listEl.innerHTML = '';
            tracks.forEach((track, idx) => {
                const row = document.createElement('div');
                row.className = 'surface-subtle flex items-center justify-between gap-2.5 p-2 rounded-2xl';
                row.innerHTML = `
                    <div class="flex items-center gap-2.5 min-w-0 flex-1">
                        <img src="${safeThumbUrl(track.id)}" alt="" class="w-12 h-8 rounded-lg object-cover flex-shrink-0">
                        <div class="min-w-0 flex-1">
                            <div class="text-xs font-bold text-main truncate">${escapeHtml(track.title)}</div>
                            <div class="text-[10.5px] text-sub truncate">${escapeHtml(track.author || '')}${track.duration_str ? ' • ' + escapeHtml(track.duration_str) : ''}</div>
                            ${track.ai_reason ? `<div class="text-[10px] text-pink-600 dark:text-pink-300 truncate mt-0.5">✨ ${escapeHtml(track.ai_reason)}</div>` : ''}
                        </div>
                    </div>
                    <button type="button" class="js-ai-dj-add-one px-3 py-1.5 rounded-xl btn-gradient text-xs font-bold flex-shrink-0" data-idx="${idx}">
                        + Thêm
                    </button>
                `;
                const addBtn = row.querySelector('.js-ai-dj-add-one');
                if (addBtn) {
                    addBtn.onclick = () => {
                        if (!canAddTrackInCurrentRoom()) return;
                        window.roomSocket.addTrack(track);
                        addBtn.textContent = '✓ Đã thêm';
                        addBtn.className = 'js-ai-dj-add-one px-3 py-1.5 rounded-xl bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30 text-xs font-bold flex-shrink-0';
                    };
                }
                listEl.appendChild(row);
            });
        }
    } catch (err) {
        if (listEl) {
            listEl.innerHTML = `<div class="text-center py-6 text-rose-500 text-xs">Lỗi kết nối AI DJ. Vui lòng thử lại!</div>`;
        }
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

function initAiFeatures() {
    updateMcToggleUI();

    // 1. Toggle Female Radio MC ON / OFF (Host-only room control)
    document.getElementById('btn-mc-radio-toggle')?.addEventListener('click', () => {
        if (!window.appState.isHost) {
            showToast('Chỉ Chủ phòng mới được bật/tắt MC Radio', 'warning');
            return;
        }
        // Prime audio element on user click gesture so browser allows future MC speech playback
        const audioEl = document.getElementById('mc-tts-audio-player');
        if (audioEl && !mcRadioState.isSpeaking) {
            try {
                audioEl.volume = 1.0;
                audioEl.muted = false;
            } catch (_) {}
        }
        window.roomSocket.toggleMcMode();
    });

    document.getElementById('btn-stop-mc-speech')?.addEventListener('click', stopMcRadioSpeech);

    // 1b. Toggle proactive Em bé floor 2 (welcome / unanswered questions / reminders) — Host-only
    document.getElementById('btn-ai-proactive-toggle')?.addEventListener('click', () => {
        if (!window.appState.isHost) {
            showToast('Chỉ Chủ phòng mới được bật/tắt chế độ chủ động của Em bé floor 2', 'warning');
            return;
        }
        window.roomSocket.send('toggle_ai_proactive');
    });

    // 2. Messenger-style '@' Mention Autocomplete + Live Input Bold Highlight
    initChatMentionAutocomplete();

    // 3. Room Vibe Card Toggle & Refresh
    document.getElementById('btn-toggle-room-vibe')?.addEventListener('click', () => {
        const card = document.getElementById('room-vibe-card');
        if (!card) return;
        if (card.classList.contains('hidden')) {
            loadRoomVibeCard();
        } else {
            card.classList.add('hidden');
        }
    });
    document.getElementById('btn-refresh-room-vibe')?.addEventListener('click', () => loadRoomVibeCard());
    document.getElementById('btn-close-room-vibe')?.addEventListener('click', () => {
        document.getElementById('room-vibe-card')?.classList.add('hidden');
    });

    // 4. AI DJ Modal & Auto-DJ
    const djModal = document.getElementById('ai-dj-modal');
    const closeDjModal = () => djModal?.classList.add('hidden');

    document.getElementById('btn-open-ai-dj')?.addEventListener('click', () => {
        if (!canAddTrackInCurrentRoom()) return;
        updateAutoDjUI();
        djModal?.classList.remove('hidden');
        document.getElementById('ai-dj-prompt-input')?.focus();
    });
    document.getElementById('btn-close-ai-dj-modal')?.addEventListener('click', closeDjModal);
    djModal?.addEventListener('click', (e) => {
        if (e.target === djModal) closeDjModal();
    });

    document.getElementById('btn-toggle-auto-dj')?.addEventListener('click', () => {
        if (!window.appState.isHost) {
            showToast('Chỉ Chủ phòng mới bật/tắt được Auto-DJ', 'warning');
            return;
        }
        window.roomSocket.toggleAutoDj();
    });

    document.getElementById('ai-dj-form')?.addEventListener('submit', (e) => {
        e.preventDefault();
        const inp = document.getElementById('ai-dj-prompt-input');
        runAiDjPlaylist(inp ? inp.value.trim() : '');
    });

    document.querySelectorAll('.js-ai-vibe-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const p = chip.getAttribute('data-prompt') || '';
            const inp = document.getElementById('ai-dj-prompt-input');
            if (inp) inp.value = p;
            runAiDjPlaylist(p);
        });
    });

    // Enable vertical mouse-wheel -> horizontal scroll on all horizontal chip strips
    document.querySelectorAll('.overflow-x-auto').forEach(strip => {
        if (strip.id === 'quick-suggestions-chips') return; // already bound
        strip.addEventListener('wheel', (e) => {
            if (Math.abs(e.deltaY) > Math.abs(e.deltaX) && strip.scrollWidth > strip.clientWidth) {
                e.preventDefault();
                strip.scrollLeft += e.deltaY * 0.85;
            }
        }, { passive: false });
    });

    document.getElementById('btn-ai-dj-add-all')?.addEventListener('click', () => {
        if (!canAddTrackInCurrentRoom()) return;
        const tracks = mcRadioState.currentTracks || [];
        if (tracks.length === 0) return;
        tracks.slice(0, 4).forEach((t, i) => {
            setTimeout(() => window.roomSocket.addTrack(t), i * 180);
        });
        const addAllBtn = document.getElementById('btn-ai-dj-add-all');
        if (addAllBtn) {
            addAllBtn.textContent = '✓ Đã thêm vào hàng đợi';
            addAllBtn.className = 'px-3 py-1 rounded-xl bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30 text-[11px] font-extrabold flex-shrink-0';
        }
    });
}

// =============================================================================
// MESSENGER-STYLE '@' MENTION AUTOCOMPLETE & LIVE BOLD INPUT HIGHLIGHT
// =============================================================================
const chatMentionState = {
    isOpen: false,
    filtered: [],
    activeIndex: 0,
    mentionStartIdx: -1,
    mentionEndIdx: -1,
};

function stripViAccents(str) {
    return (str || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/đ/g, 'd')
        .replace(/Đ/g, 'D')
        .toLowerCase();
}

function getChatMentionCandidates() {
    const list = [
        {
            name: 'Em bé floor 2',
            avatar: '🎀',
            color: '#ec4899',
            badge: '✨ Trợ lý AI',
            subtitle: 'Tán gẫu mọi chuyện & gợi ý nhạc',
            isAi: true,
        },
    ];
    const users = (window.appState.roomData && Array.isArray(window.appState.roomData.users))
        ? window.appState.roomData.users
        : [];
    users.forEach(u => {
        if (u && u.name) {
            list.push({
                name: u.name,
                avatar: u.avatar || '🎧',
                color: safeColor(u.color),
                badge: u.is_host ? '👑 Host' : 'Thành viên',
                subtitle: u.id === window.appState.userId ? 'Bạn' : 'Đang trong phòng',
                isAi: false,
            });
        }
    });
    return list;
}

function closeChatMentionPopover() {
    chatMentionState.isOpen = false;
    chatMentionState.filtered = [];
    chatMentionState.activeIndex = 0;
    document.getElementById('chat-mention-popover')?.classList.add('hidden');
}

function renderChatMentionPopover() {
    const pop = document.getElementById('chat-mention-popover');
    const listEl = document.getElementById('chat-mention-list');
    if (!pop || !listEl) return;

    if (!chatMentionState.isOpen || chatMentionState.filtered.length === 0) {
        pop.classList.add('hidden');
        return;
    }

    pop.classList.remove('hidden');
    listEl.innerHTML = '';

    chatMentionState.filtered.forEach((cand, idx) => {
        const isActive = idx === chatMentionState.activeIndex;
        const row = document.createElement('button');
        row.type = 'button';
        row.className = `w-full flex items-center justify-between gap-2.5 p-2 rounded-xl text-left transition ${
            isActive ? 'bg-indigo-500/15 ring-1 ring-indigo-500/40' : 'hover:bg-black/5 dark:hover:bg-white/5'
        }`;

        const badgeCls = cand.isAi
            ? 'bg-gradient-to-r from-pink-500/20 to-violet-500/20 text-pink-600 dark:text-pink-300 border border-pink-500/35'
            : 'surface-subtle text-sub';

        row.innerHTML = `
            <div class="flex items-center gap-2.5 min-w-0 flex-1">
                <div class="w-8 h-8 rounded-full flex items-center justify-center text-sm flex-shrink-0 shadow-sm"
                     style="background-color: ${cand.color}22; border: 1.5px solid ${cand.color}88">
                    ${escapeHtml(cand.avatar)}
                </div>
                <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5">
                        <span class="text-xs font-extrabold text-main truncate">@${escapeHtml(cand.name)}</span>
                        <span class="px-1.5 py-0.2 rounded-full text-[9.5px] font-extrabold flex-shrink-0 ${badgeCls}">${escapeHtml(cand.badge)}</span>
                    </div>
                    <p class="text-[10.5px] text-dim truncate">${escapeHtml(cand.subtitle)}</p>
                </div>
            </div>
        `;

        row.addEventListener('mousedown', (e) => {
            e.preventDefault();
            selectChatMentionCandidate(idx);
        });
        row.addEventListener('mouseenter', () => {
            chatMentionState.activeIndex = idx;
        });

        listEl.appendChild(row);
    });
}

function selectChatMentionCandidate(idx) {
    const input = document.getElementById('chat-text-input');
    const cand = chatMentionState.filtered[idx];
    if (!input || !cand) return;

    const val = input.value;
    const before = val.slice(0, chatMentionState.mentionStartIdx);
    const after = val.slice(chatMentionState.mentionEndIdx);
    const insertText = `@${cand.name} `;
    const nextVal = before + insertText + after.replace(/^\s+/, '');

    input.value = nextVal;
    const nextCaret = (before + insertText).length;
    input.focus();
    try {
        input.setSelectionRange(nextCaret, nextCaret);
    } catch (_) {}

    closeChatMentionPopover();
    updateChatInputMentionMirror();
}

function updateChatInputMentionMirror() {
    const input = document.getElementById('chat-text-input');
    const mirror = document.getElementById('chat-input-mirror');
    const wrap = document.getElementById('chat-input-box-wrap');
    if (!input || !mirror) return;

    const val = input.value || '';
    if (!val || !val.includes('@')) {
        input.classList.remove('has-mention-highlight');
        mirror.classList.add('hidden');
        mirror.innerHTML = '';
        wrap?.classList.remove('ring-1', 'ring-pink-500/45');
        return;
    }

    const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const users = (window.appState.roomData && Array.isArray(window.appState.roomData.users))
        ? window.appState.roomData.users
        : [];
    const memberNames = users
        .map(u => escapeHtml((u && u.name) || '').trim())
        .filter(Boolean)
        .sort((a, b) => b.length - a.length)
        .map(n => `@${escapeRegex(n)}`);

    const aiPat = `@(?:Em\\s+b(?:é|e)\\s+floor\\s+2|embefloor2)`;
    const combined = new RegExp(`(${[aiPat, ...memberNames].join('|')})`, 'gi');
    const safeVal = escapeHtml(val);

    if (!combined.test(safeVal)) {
        input.classList.remove('has-mention-highlight');
        mirror.classList.add('hidden');
        mirror.innerHTML = '';
        wrap?.classList.remove('ring-1', 'ring-pink-500/45');
        return;
    }

    combined.lastIndex = 0;
    let hasAiTag = false;
    const highlightedHtml = safeVal.replace(combined, (match) => {
        const isAi = /^@(?:em\s+b(?:é|e)\s+floor\s+2|embefloor2)$/i.test(match);
        if (isAi) hasAiTag = true;
        const cls = isAi ? 'chat-input-mention-ai' : 'chat-input-mention-user';
        return `<span class="${cls}">${match}</span>`;
    });

    input.classList.add('has-mention-highlight');
    mirror.classList.remove('hidden');
    mirror.innerHTML = highlightedHtml;
    mirror.scrollLeft = input.scrollLeft;
    wrap?.classList.toggle('ring-1', hasAiTag);
    wrap?.classList.toggle('ring-pink-500/45', hasAiTag);
}

function initChatMentionAutocomplete() {
    const input = document.getElementById('chat-text-input');
    const mirror = document.getElementById('chat-input-mirror');
    if (!input) return;

    const syncScroll = () => {
        if (mirror && !mirror.classList.contains('hidden')) {
            mirror.scrollLeft = input.scrollLeft;
        }
    };

    const checkMentionTrigger = () => {
        updateChatInputMentionMirror();
        const val = input.value || '';
        const caret = typeof input.selectionStart === 'number' ? input.selectionStart : val.length;
        const beforeCaret = val.slice(0, caret);

        // Find last '@' that is either at index 0 or preceded by whitespace
        const m = beforeCaret.match(/(?:^|\s)@([^\n@]{0,22})$/);
        if (!m) {
            closeChatMentionPopover();
            return;
        }

        const rawQuery = m[1];
        // If user already finished typing '@Em bé floor 2 ' with trailing space, close popup
        if (/^(?:em\s+b(?:é|e)\s+floor\s+2|embefloor2)\s+/i.test(rawQuery)) {
            closeChatMentionPopover();
            return;
        }

        const atIdx = beforeCaret.lastIndexOf('@');
        const qNorm = stripViAccents(rawQuery.trim());
        const candidates = getChatMentionCandidates().filter(c => {
            if (!qNorm) return true;
            const cNorm = stripViAccents(c.name);
            return cNorm.includes(qNorm) || (c.isAi && 'ai tro ly em be floor 2'.includes(qNorm));
        });

        if (candidates.length === 0) {
            closeChatMentionPopover();
            return;
        }

        chatMentionState.isOpen = true;
        chatMentionState.filtered = candidates;
        chatMentionState.activeIndex = 0;
        chatMentionState.mentionStartIdx = atIdx;
        chatMentionState.mentionEndIdx = caret;
        renderChatMentionPopover();
    };

    input.addEventListener('input', checkMentionTrigger);
    input.addEventListener('click', checkMentionTrigger);
    input.addEventListener('keyup', syncScroll);
    input.addEventListener('scroll', syncScroll);

    input.addEventListener('keydown', (e) => {
        if (!chatMentionState.isOpen || chatMentionState.filtered.length === 0) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            chatMentionState.activeIndex = (chatMentionState.activeIndex + 1) % chatMentionState.filtered.length;
            renderChatMentionPopover();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            chatMentionState.activeIndex = (chatMentionState.activeIndex - 1 + chatMentionState.filtered.length) % chatMentionState.filtered.length;
            renderChatMentionPopover();
        } else if (e.key === 'Enter' || e.key === 'Tab') {
            e.preventDefault();
            selectChatMentionCandidate(chatMentionState.activeIndex);
        } else if (e.key === 'Escape') {
            e.preventDefault();
            closeChatMentionPopover();
        }
    });

    document.addEventListener('click', (e) => {
        const pop = document.getElementById('chat-mention-popover');
        if (chatMentionState.isOpen && pop && !pop.contains(e.target) && e.target !== input) {
            closeChatMentionPopover();
        }
    });
}
