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
    isChatCollapsed: localStorage.getItem('floor2_chat_collapsed') === 'true',
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

async function ensureSession(forceNew = false, verifyServer = false) {
    if (!forceNew && !verifyServer && window.appState.sessionToken && window.appState.userId) {
        return window.appState.sessionToken;
    }
    const headers = { 'Content-Type': 'application/json' };
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
    showToast(next === 'dark' ? 'Đã chuyển sang giao diện Tối (Midnight)' : 'Đã chuyển sang giao diện Sáng (Daylight)', 'info');
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
// TOAST NOTIFICATIONS
// =============================================================================
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const accentDots = {
        info: 'bg-indigo-500',
        success: 'bg-emerald-500',
        warning: 'bg-amber-500',
        error: 'bg-rose-500',
    };

    const toast = document.createElement('div');
    toast.className = 'toast-item flex items-center gap-2.5 px-4 py-2.5 rounded-full text-xs md:text-sm font-medium';
    toast.innerHTML = `
        <span class="w-2 h-2 rounded-full flex-shrink-0 ${accentDots[type] || accentDots.info}"></span>
        <span>${escapeHtml(message)}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(12px) scale(0.95)';
        setTimeout(() => {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 260);
    }, 3200);
}

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
async function loadPublicRooms() {
    const listContainer = document.getElementById('active-rooms-list');
    if (!listContainer) return;

    try {
        const res = await fetch('/api/rooms');
        const data = await res.json();
        const rooms = data.rooms || [];

        if (rooms.length === 0) {
            listContainer.innerHTML = `
                <div class="text-center py-8 text-dim text-sm">
                    Chưa có phòng nào đang mở. Hãy tạo phòng đầu tiên nhé!
                </div>
            `;
            return;
        }

        listContainer.innerHTML = '';
        rooms.forEach(r => {
            const item = document.createElement('div');
            item.className = 'surface-subtle p-3.5 rounded-2xl flex items-center justify-between gap-3 cursor-pointer group';

            const playingTitle = r.current_track ? r.current_track.title : 'Đang chờ thêm nhạc...';
            const isPlaying = r.status === 'playing';
            const thumbHtml = r.current_track
                ? `<img src="${safeThumbUrl(r.current_track.id)}" alt="" class="w-12 h-12 rounded-xl object-cover shadow-sm flex-shrink-0">`
                : `<div class="w-12 h-12 rounded-xl bg-gradient-to-br from-indigo-500/20 to-pink-500/20 border border-indigo-500/20 flex items-center justify-center text-indigo-500 dark:text-indigo-300 flex-shrink-0">
                     <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"/></svg>
                   </div>`;

            const liveBadge = isPlaying
                ? `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 text-[10px] font-bold uppercase tracking-wide"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>Live</span>`
                : `<span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-500/10 text-dim text-[10px] font-semibold">Sẵn sàng</span>`;

            item.innerHTML = `
                <div class="flex items-center gap-3.5 min-w-0">
                    ${thumbHtml}
                    <div class="min-w-0">
                        <div class="flex items-center gap-2">
                            <h4 class="font-bold text-main text-sm truncate group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition">${escapeHtml(r.name)}</h4>
                            ${liveBadge}
                        </div>
                        <p class="text-xs text-sub truncate mt-0.5">${escapeHtml(playingTitle)}</p>
                    </div>
                </div>
                <div class="flex items-center gap-2.5 flex-shrink-0">
                    <span class="inline-flex items-center gap-1 text-xs font-semibold text-sub px-2.5 py-1 rounded-full surface-subtle">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
                        ${parseInt(r.user_count, 10) || 0}
                    </span>
                    <span class="px-3.5 py-1.5 rounded-xl btn-gradient text-xs font-semibold">
                        Vào
                    </span>
                </div>
            `;

            item.onclick = () => joinRoom(r.id);
            listContainer.appendChild(item);
        });
    } catch (e) {
        console.error('Error fetching rooms:', e);
    }
}

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
    window.appState.roomId = null;
    window.appState.inviteToken = null;
    window.appState.roomData = null;
    renderPriorityBanner(null);

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
        if (header) header.textContent = 'Gợi ý nhanh — Gõ tên bài hoặc dán link YouTube để tìm tức thì';

        searchState.results = window.appState.suggestions || [];
        searchState.highlightedIndex = -1;
        renderSearchResultsList(searchState.results);
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

function addTrackFromSearch(item, idx) {
    window.roomSocket.addTrack(item);
    searchState.addedIds.add(item.id);
    showToast(`Đã thêm vào hàng đợi: ${item.title}`, 'success');

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
        if (data.your_user_id) window.appState.userId = data.your_user_id;
        if (data.room && data.room.invite_token) {
            window.appState.inviteToken = data.room.invite_token;
        }
        updateRoomUI(data.room);

        if (data.messages) {
            data.messages.forEach(m => window.roomChat.addMessage(m, true));
        }

        window.syncPlayer.syncWithRoomState(data.room);
    });

    socket.on('playback_changed', (data) => {
        if (!window.appState.roomData) return;
        window.appState.roomData.current_track = data.current_track;
        window.appState.roomData.status = data.status;
        window.appState.roomData.current_time = data.current_time;
        window.appState.roomData.updated_at = data.updated_at;
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

        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('playback_sync', (data) => {
        if (!window.appState.roomData) return;
        window.appState.roomData.status = data.status;
        window.appState.roomData.current_time = data.current_time;
        window.appState.roomData.updated_at = data.updated_at;

        window.syncPlayer.syncWithRoomState(window.appState.roomData);
    });

    socket.on('queue_updated', (data) => {
        if (!window.appState.roomData) return;
        window.appState.roomData.queue = data.queue;
        if ('priority_request' in data) {
            window.appState.roomData.priority_request = data.priority_request;
        }
        renderPriorityBanner(window.appState.roomData.priority_request);
        renderQueue();

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

    socket.on('error', (err) => {
        if (err.message) showToast(err.message, 'warning');
    });

    socket.on('access_denied', (data) => {
        const failedRoom = window.appState.roomId;
        leaveRoom();
        showToast(data?.reason || 'Không có quyền truy cập phòng riêng tư', 'error');
        if (failedRoom) openPasscodeModal(failedRoom, true);
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
    renderSkipVoteButton(room.skip_vote || null);
    renderPriorityBanner(room.priority_request || null);
    renderQueue();
    renderHistory();
    renderMembers();
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

    const hostBadge = document.getElementById('user-role-badge');
    if (hostBadge) {
        if (isHost) {
            hostBadge.innerHTML = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-[11px] font-bold">👑 Host</span>`;
        } else {
            hostBadge.innerHTML = `<span class="inline-flex items-center px-2 py-0.5 rounded-full surface-subtle text-sub text-[11px] font-medium">Thành viên</span>`;
        }
    }

    const djToggleBtn = document.getElementById('btn-dj-mode-toggle');
    if (djToggleBtn) {
        djToggleBtn.classList.toggle('hidden', !isHost);
        const isDj = window.appState.roomData && window.appState.roomData.dj_mode;
        djToggleBtn.innerHTML = isDj
            ? `<span class="w-1.5 h-1.5 rounded-full bg-amber-500"></span><span>DJ: Bật</span>`
            : `<span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span><span>DJ: Tắt</span>`;
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
    const queueList = document.getElementById('playlist-queue-list');
    const queueCountBadge = document.getElementById('queue-count-badge');
    const queueDurationBadge = document.getElementById('queue-duration-badge');
    const queueEmpty = document.getElementById('queue-empty-state');
    if (!queueList) return;

    const queue = (window.appState.roomData && window.appState.roomData.queue) || [];
    if (queueCountBadge) queueCountBadge.textContent = queue.length;
    if (window.syncPlayer) window.syncPlayer.updateUpNext(queue);

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
        item.className = `surface-subtle flex items-center justify-between gap-3 p-2.5 rounded-2xl group ${
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
            : `<span class="px-2 py-0.5 rounded-md bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 text-[10px] font-bold" title="Sẽ phát kế tiếp">#1 Kế tiếp</span>`;

        item.innerHTML = `
            <div class="flex items-center gap-3 min-w-0 flex-1">
                <span class="text-xs font-bold font-mono-num text-dim w-6 text-center flex-shrink-0">
                    ${String(index + 1).padStart(2, '0')}
                </span>
                <div class="relative w-14 h-9 rounded-lg overflow-hidden flex-shrink-0 bg-slate-900 shadow-sm">
                    <img src="${safeThumbUrl(track.id)}" alt="" class="w-full h-full object-cover" loading="lazy">
                    ${track.duration_str ? `<span class="absolute bottom-0.5 right-0.5 px-1 rounded bg-black/75 text-white text-[9px] font-mono-num">${escapeHtml(track.duration_str)}</span>` : ''}
                </div>
                <div class="min-w-0 flex-1">
                    <h5 class="text-xs md:text-[13px] font-semibold text-main truncate">${escapeHtml(track.title)}</h5>
                    <div class="flex items-center gap-1.5 text-[11px] text-sub mt-0.5">
                        <span class="truncate max-w-[140px]">${escapeHtml(track.author)}</span>
                        <span class="text-dim">•</span>
                        <span class="text-indigo-600 dark:text-indigo-400 font-medium truncate">${escapeHtml(track.added_by_name)}</span>
                    </div>
                </div>
            </div>
            <div class="flex items-center gap-1 flex-shrink-0 opacity-85 group-hover:opacity-100 transition">
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
                showToast(`Đã đưa "${track.title}" lên phát tiếp theo`, 'info');
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
            window.roomSocket.addTrack(track);
            showToast(`Đã thêm lại: ${track.title}`, 'success');
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
            const newAction = (player.status === 'playing') ? 'pause' : 'play';
            window.roomSocket.controlPlayback(newAction, player.getCurrentTime());
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
        progressBar.addEventListener('change', (e) => {
            const percent = parseFloat(e.target.value);
            const dur = window.syncPlayer.getDuration();
            if (dur > 0) {
                const targetSec = (percent / 100) * dur;
                window.syncPlayer.seekTo(targetSec);
                window.roomSocket.controlPlayback('seek', targetSec);
            }
        });
    }

    if (volumeSlider) {
        volumeSlider.value = window.syncPlayer.volume;
        window.syncPlayer.updateVolumeSliderFill();
        volumeSlider.addEventListener('input', (e) => {
            window.syncPlayer.setVolume(e.target.value);
        });
    }

    if (muteBtn) {
        muteBtn.onclick = () => {
            const isMuted = window.syncPlayer.toggleMute();
            muteBtn.innerHTML = isMuted
                ? `<svg class="w-4 h-4 text-rose-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"/><path stroke-linecap="round" stroke-linejoin="round" d="M17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2"/></svg>`
                : `<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z"/></svg>`;
        };
    }

    // Video / Radio Mode Switcher
    if (btnModeVideo) {
        btnModeVideo.onclick = () => {
            if (window.syncPlayer.useAudioFallback) {
                showToast('Bài hát này đang phát qua luồng Pure Audio Radio vì bị chặn nhúng video', 'info');
                return;
            }
            window.syncPlayer.setMode('video', true);
        };
    }

    if (btnModeRadio) {
        btnModeRadio.onclick = () => {
            window.syncPlayer.setMode('radio', true);
        };
    }

    if (btnHeaderRadio) {
        btnHeaderRadio.onclick = () => {
            const nextMode = window.syncPlayer.mode === 'radio' ? 'video' : 'radio';
            if (nextMode === 'video' && window.syncPlayer.useAudioFallback) {
                showToast('Bài hát này đang phát qua luồng Pure Audio Radio vì bị chặn nhúng video', 'info');
                return;
            }
            window.syncPlayer.setMode(nextMode, true);
        };
    }

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

    document.getElementById('btn-leave-room')?.addEventListener('click', () => {
        if (confirm('Bạn có chắc muốn rời phòng?')) leaveRoom();
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
            showToast(enabled ? 'Đã bật chuông thông báo' : 'Đã tắt chuông thông báo', 'info');
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
    localStorage.setItem('floor2_chat_collapsed', collapsed ? 'true' : 'false');

    const sidebar = document.getElementById('chat-sidebar');
    const fab = document.getElementById('btn-expand-chat-fab');
    const headerBtn = document.getElementById('btn-header-chat-toggle');

    if (sidebar) sidebar.classList.toggle('hidden', !!collapsed);
    if (fab) fab.classList.toggle('hidden', !collapsed);

    if (headerBtn) {
        headerBtn.classList.toggle('border-indigo-500/50', !collapsed);
        headerBtn.classList.toggle('bg-indigo-500/10', !collapsed);
        const lbl = headerBtn.querySelector('.js-header-chat-label');
        if (lbl) lbl.textContent = collapsed ? 'Mở Chat' : 'Chat';
    }

    if (!collapsed && window.roomChat) {
        window.roomChat.resetUnread();
        window.roomChat.scrollToBottom();
    }
}

function initChatCollapse() {
    document.getElementById('btn-minimize-chat')?.addEventListener('click', () => setChatCollapsed(true));
    document.getElementById('btn-expand-chat-fab')?.addEventListener('click', () => setChatCollapsed(false));
    document.getElementById('btn-header-chat-toggle')?.addEventListener('click', () => {
        setChatCollapsed(!window.appState.isChatCollapsed);
    });
    setChatCollapsed(window.appState.isChatCollapsed);
}

// =============================================================================
// RICH CHAT MEDIA: EMOJI PICKER, LIVE GIF SEARCH & IMAGE UPLOAD / PASTE
// =============================================================================
const EMOJI_CATEGORIES = [
    {
        icon: '🎵',
        label: 'Nhạc & Quẩy',
        items: ['🎵', '🎶', '🎧', '🎤', '🎸', '🎹', '🥁', '🎷', '🔥', '✨', '💃', '🕺', '🎉', '🎊', '🥳', '🚀', '⚡', '🌟', '💥', '🔊', '📻', '💿', '🏆', '👑'],
    },
    {
        icon: '😀',
        label: 'Cảm xúc',
        items: ['😀', '😄', '😁', '😂', '🤣', '🥹', '😍', '🥰', '😘', '😎', '🤩', '😇', '🤔', '😮', '😱', '😢', '😭', '😤', '😴', '🤯', '🫠', '🫡', '🤭', '😏'],
    },
    {
        icon: '💖',
        label: 'Tương tác',
        items: ['❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '💖', '💘', '💯', '👍', '👎', '👏', '🙌', '🤝', '🙏', '✌️', '🤘', '🫶', '👌', '💪', '👀', '💬'],
    },
    {
        icon: '☕',
        label: 'Chill & Đồ ăn',
        items: ['☕', '🧋', '🍵', '🍻', '🥂', '🍷', '🍹', '🍕', '🍔', '🍟', '🍜', '🍣', '🍰', '🍉', '🍓', '🌙', '⭐', '🌈', '🌸', '🐱', '🐶', '🦊', '🐼', '🦋'],
    },
];

let pendingPreviewObjectUrl = null;
let gifSearchTimer = null;
let gifAbortController = null;

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
    const emojiPopover = document.getElementById('emoji-picker-popover');
    const gifPopover = document.getElementById('gif-picker-popover');
    const emojiBtn = document.getElementById('btn-toggle-emoji-picker');
    const gifBtn = document.getElementById('btn-toggle-gif-picker');
    const uploadBtn = document.getElementById('btn-chat-upload-image');
    const fileInput = document.getElementById('chat-image-file-input');
    const chatInput = document.getElementById('chat-text-input');

    const closeAllPopovers = () => {
        emojiPopover?.classList.add('hidden');
        gifPopover?.classList.add('hidden');
    };

    // 1. EMOJI / ICON PICKER
    const catTabs = document.getElementById('emoji-category-tabs');
    const emojiGrid = document.getElementById('emoji-picker-grid');
    let activeEmojiCat = 0;

    const renderEmojiCategory = (catIdx) => {
        activeEmojiCat = catIdx;
        if (catTabs) {
            catTabs.innerHTML = '';
            EMOJI_CATEGORIES.forEach((cat, idx) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = `px-2 py-1 rounded-lg text-sm transition ${idx === activeEmojiCat ? 'bg-indigo-500/20 ring-1 ring-indigo-500/40' : 'hover:bg-slate-500/10'}`;
                b.textContent = cat.icon;
                b.title = cat.label;
                b.onclick = () => renderEmojiCategory(idx);
                catTabs.appendChild(b);
            });
        }
        if (emojiGrid) {
            emojiGrid.innerHTML = '';
            EMOJI_CATEGORIES[catIdx].items.forEach(em => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'h-8 rounded-lg hover:bg-indigo-500/15 active:scale-90 transition text-lg flex items-center justify-center';
                b.textContent = em;
                b.onclick = () => {
                    if (chatInput) {
                        const start = chatInput.selectionStart ?? chatInput.value.length;
                        const end = chatInput.selectionEnd ?? chatInput.value.length;
                        chatInput.value = chatInput.value.slice(0, start) + em + chatInput.value.slice(end);
                        const nextPos = start + em.length;
                        chatInput.setSelectionRange(nextPos, nextPos);
                        chatInput.focus();
                    }
                };
                emojiGrid.appendChild(b);
            });
        }
    };
    renderEmojiCategory(0);

    emojiBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = emojiPopover?.classList.contains('hidden');
        closeAllPopovers();
        if (willOpen) emojiPopover?.classList.remove('hidden');
    });
    document.getElementById('btn-close-emoji-picker')?.addEventListener('click', closeAllPopovers);

    // 2. GIF PICKER (Curated + Live Search with Client-Side LRU+TTL Cache)
    const gifInput = document.getElementById('gif-search-input');
    const gifGrid = document.getElementById('gif-results-grid');
    const gifSpinner = document.getElementById('gif-search-spinner');
    const gifCache = new Map(); // query -> { ts, gifs }
    const GIF_CACHE_TTL_MS = 10 * 60 * 1000;
    let gifsLoadedOnce = false;

    const renderGifItems = (gifs) => {
        if (!gifGrid) return;
        gifGrid.innerHTML = '';
        if (!gifs || gifs.length === 0) {
            gifGrid.innerHTML = `<div class="col-span-2 text-center py-6 text-xs text-dim">Không tìm thấy GIF phù hợp</div>`;
            return;
        }
        gifs.forEach(gif => {
            if (!/^[A-Za-z0-9]{8,28}$/.test(gif.id || '')) return;
            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'relative h-24 rounded-xl overflow-hidden bg-slate-900/50 border border-theme hover:border-indigo-500 transition group';
            card.innerHTML = `<img src="https://media.giphy.com/media/${gif.id}/200w.gif" alt="GIF" loading="lazy" class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200">`;
            card.onclick = () => {
                window.roomSocket.sendGif(gif.id);
                closeAllPopovers();
            };
            gifGrid.appendChild(card);
        });
    };

    const loadGifs = async (query = '') => {
        if (!gifGrid) return;
        const cacheKey = (query || '').trim().toLowerCase();
        const cached = gifCache.get(cacheKey);
        if (cached && (Date.now() - cached.ts) < GIF_CACHE_TTL_MS) {
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
        tag.addEventListener('click', () => {
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
            emojiPopover && !emojiPopover.classList.contains('hidden') &&
            !emojiPopover.contains(e.target) && e.target !== emojiBtn
        ) {
            emojiPopover.classList.add('hidden');
        }
        if (
            gifPopover && !gifPopover.classList.contains('hidden') &&
            !gifPopover.contains(e.target) && e.target !== gifBtn
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
// QUICK SUGGESTIONS STRIP (WITH SESSION STORAGE CACHE)
// =============================================================================
const SUGGESTIONS_CACHE_KEY = 'floor2_suggestions_v1';
const SUGGESTIONS_CACHE_TTL_MS = 5 * 60 * 1000;

async function initQuickSuggestions() {
    const container = document.getElementById('quick-suggestions-chips');
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

        if (!container) return;
        container.innerHTML = '';

        suggestions.forEach(song => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'surface-subtle flex items-center gap-2.5 pl-1.5 pr-3 py-1.5 rounded-full text-left flex-shrink-0 group hover:border-indigo-500/40';
            chip.innerHTML = `
                <img src="${safeThumbUrl(song.id)}" alt="" class="w-7 h-7 rounded-full object-cover shadow-sm flex-shrink-0">
                <div class="min-w-0 max-w-[160px]">
                    <div class="text-xs font-semibold text-main truncate group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition">${escapeHtml(song.title)}</div>
                </div>
                <span class="w-5 h-5 rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 flex items-center justify-center text-xs font-bold group-hover:bg-indigo-600 group-hover:text-white transition">+</span>
            `;

            chip.onclick = () => {
                window.roomSocket.addTrack(song);
                showToast(`Đã thêm: ${song.title}`, 'success');
            };

            container.appendChild(chip);
        });
    } catch (e) {
        console.error('Error fetching suggestions:', e);
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
