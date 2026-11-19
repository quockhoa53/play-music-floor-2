/**
 * Master Application Controller — Floor 2 Music Studio
 * Dual Theme (Light/Dark) + Realtime As-You-Type YouTube Search
 */

const AVATAR_OPTIONS = ['🎧', '🎵', '🎸', '🎹', '🦊', '🐱', '🐼', '🐯', '🚀', '👑', '⚡', '☕', '🍉', '🔥', '💃', '🕺'];
const COLOR_OPTIONS = ['#4f46e5', '#db2777', '#7c3aed', '#059669', '#d97706', '#0891b2', '#e11d48', '#2563eb'];

const YT_URL_REGEX = /(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/|music\.youtube\.com\/watch\?v=)([a-zA-Z0-9_-]{11})|^([a-zA-Z0-9_-]{11})$/;

window.appState = {
    userId: localStorage.getItem('floor2_uid') || generateUid(),
    userName: localStorage.getItem('floor2_uname') || getRandomName(),
    userAvatar: localStorage.getItem('floor2_avatar') || '🎧',
    userColor: localStorage.getItem('floor2_color') || '#4f46e5',
    theme: localStorage.getItem('floor2_theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
    roomId: null,
    roomData: null,
    isHost: false,
    isVideoHidden: localStorage.getItem('floor2_video_hidden') === 'true',
    suggestions: [],
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
};

function generateUid() {
    const id = 'u_' + Math.random().toString(36).substring(2, 9);
    localStorage.setItem('floor2_uid', id);
    return id;
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
document.addEventListener('DOMContentLoaded', () => {
    applyTheme(window.appState.theme);

    document.querySelectorAll('.js-theme-toggle').forEach(btn => {
        btn.addEventListener('click', toggleTheme);
    });

    initLobbyTabs();
    initProfileUI();
    initPlayerListeners();
    initSocketEvents();
    initRoomControls();
    initRealtimeSearch();
    initQuickSuggestions();
    loadPublicRooms();

    // Direct room join via ?room=xxx
    const urlParams = new URLSearchParams(window.location.search);
    const roomParam = urlParams.get('room');
    if (roomParam) {
        const joinInput = document.getElementById('input-join-room-id');
        if (joinInput) joinInput.value = roomParam;
        joinRoom(roomParam);
    }
});

// =============================================================================
// LOBBY TABS & LIVE PROFILE PREVIEW
// =============================================================================
function initLobbyTabs() {
    const tabCreate = document.getElementById('lobby-tab-create');
    const tabJoin = document.getElementById('lobby-tab-join');
    const paneCreate = document.getElementById('lobby-pane-create');
    const paneJoin = document.getElementById('lobby-pane-join');

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
                ? `<img src="${r.current_track.thumbnail}" alt="" class="w-12 h-12 rounded-xl object-cover shadow-sm flex-shrink-0">`
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
                        ${r.user_count}
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
// ROOM JOIN / CREATE FLOW
// =============================================================================
async function handleCreateRoom(e) {
    if (e) e.preventDefault();
    const nameInput = document.getElementById('create-room-name');
    const djModeCheckbox = document.getElementById('create-room-dj-mode');

    const name = nameInput.value.trim() || `Phòng của ${window.appState.userName}`;
    const djMode = djModeCheckbox ? djModeCheckbox.checked : false;

    try {
        const res = await fetch('/api/rooms', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, dj_mode: djMode })
        });
        const data = await res.json();
        if (data.room_id) {
            joinRoom(data.room_id);
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

function joinRoom(roomId) {
    const cleanId = roomId.trim().toLowerCase();
    window.appState.roomId = cleanId;

    const newUrl = `${window.location.pathname}?room=${cleanId}`;
    window.history.pushState({ path: newUrl }, '', newUrl);

    document.getElementById('lobby-view').classList.add('hidden');
    document.getElementById('room-view').classList.remove('hidden');

    window.syncPlayer.init();
    window.roomChat.init();
    window.roomChat.clear();

    window.roomSocket.connect(cleanId, window.appState.userId, {
        name: window.appState.userName,
        avatar: window.appState.userAvatar,
        color: window.appState.userColor
    });
}

function leaveRoom() {
    window.roomSocket.disconnect();
    window.appState.roomId = null;
    window.appState.roomData = null;

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

    const performRealtimeSearch = async (rawQuery) => {
        const query = rawQuery.trim();
        if (!query) {
            setLoading(false);
            renderDropdownSuggestions();
            return;
        }

        // Cancel any previous in-flight search request
        if (searchState.abortController) {
            searchState.abortController.abort();
        }
        const controller = new AbortController();
        searchState.abortController = controller;
        searchState.lastQuery = query;

        const isDirectUrl = YT_URL_REGEX.test(query);
        openDropdown();
        setLoading(true);
        renderSkeletons(isDirectUrl ? 'Đang lấy thông tin video từ link YouTube...' : `Đang tìm "${query}"...`);

        try {
            const res = await fetch(`/api/youtube/search?q=${encodeURIComponent(query)}`, {
                signal: controller.signal
            });
            const data = await res.json();
            if (controller.signal.aborted) return;

            const results = data.results || [];
            searchState.results = results;
            searchState.highlightedIndex = results.length > 0 ? 0 : -1;

            const header = document.getElementById('search-dropdown-header');
            if (header) {
                header.textContent = results.length > 0
                    ? (isDirectUrl ? 'Đã nhận diện video YouTube' : `Kết quả tức thì (${results.length}) — Bấm Enter để thêm nhanh`)
                    : `Không tìm thấy kết quả cho "${query}"`;
            }

            renderSearchResultsList(results);
        } catch (err) {
            if (err.name === 'AbortError') return;
            const list = document.getElementById('search-results-list');
            if (list) {
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
            setLoading(false);
            openDropdown();
            renderDropdownSuggestions();
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

        // Show subtle feedback immediately and debounce 280ms
        openDropdown();
        searchState.debounceTimer = setTimeout(() => {
            performRealtimeSearch(trimmed);
        }, 280);
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
                    <img src="${item.thumbnail}" alt="" class="w-full h-full object-cover" loading="lazy">
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

    socket.on('init_state', (data) => {
        window.appState.roomData = data.room;
        window.appState.isHost = data.is_host;
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

        // Reset skip vote button label
        resetSkipVoteButton();

        window.syncPlayer.syncWithRoomState(window.appState.roomData);
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
        renderQueue();

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
            renderMembers();
            updateHostControls();
            resetSkipVoteButton();
        }
        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('user_left', (data) => {
        if (window.appState.roomData) {
            window.appState.roomData.users = data.users;
            window.appState.roomData.host_id = data.host_id;
            window.appState.isHost = (data.host_id === window.appState.userId);
            renderMembers();
            updateHostControls();
            resetSkipVoteButton();
        }
        if (data.message) window.roomChat.addMessage(data.message);
    });

    socket.on('skip_vote_updated', (data) => {
        const skipBtn = document.getElementById('btn-vote-skip');
        if (skipBtn) {
            const isVoted = data.voted_users.includes(window.appState.userId);
            const label = skipBtn.querySelector('.js-skip-label');
            if (label) label.textContent = `Bỏ qua (${data.count}/${data.needed})`;
            skipBtn.classList.toggle('border-amber-500', isVoted);
            skipBtn.classList.toggle('text-amber-600', isVoted);
            skipBtn.classList.toggle('dark:text-amber-400', isVoted);
        }
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
}

function resetSkipVoteButton() {
    const skipBtn = document.getElementById('btn-vote-skip');
    if (!skipBtn) return;
    const usersCount = window.appState.roomData?.users?.length || 1;
    const needed = Math.max(1, Math.floor((usersCount + 1) / 2));
    const label = skipBtn.querySelector('.js-skip-label');
    if (label) label.textContent = `Bỏ qua (0/${needed})`;
    skipBtn.classList.remove('border-amber-500', 'text-amber-600', 'dark:text-amber-400');
}

// =============================================================================
// ROOM UI UPDATES
// =============================================================================
function updateRoomUI(room) {
    if (!room) return;
    const titleEl = document.getElementById('room-title-display');
    const idBadge = document.getElementById('room-id-badge');
    if (titleEl) titleEl.textContent = room.name;
    if (idBadge) idBadge.textContent = `#${room.id}`;

    updateHostControls();
    resetSkipVoteButton();
    renderQueue();
    renderHistory();
    renderMembers();
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

    const forceSkipBtn = document.getElementById('btn-force-skip');
    if (forceSkipBtn) forceSkipBtn.classList.toggle('hidden', !isHost);

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

    queue.forEach((track, index) => {
        const canDelete = isHost || (track.added_by_id === currentUid);
        const canPromote = isHost && index > 0;

        const item = document.createElement('div');
        item.className = 'surface-subtle flex items-center justify-between gap-3 p-2.5 rounded-2xl group';

        item.innerHTML = `
            <div class="flex items-center gap-3 min-w-0 flex-1">
                <span class="text-xs font-bold font-mono-num text-dim w-6 text-center flex-shrink-0">
                    ${String(index + 1).padStart(2, '0')}
                </span>
                <div class="relative w-14 h-9 rounded-lg overflow-hidden flex-shrink-0 bg-slate-900 shadow-sm">
                    <img src="${track.thumbnail}" alt="" class="w-full h-full object-cover" loading="lazy">
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
            <div class="flex items-center gap-1 flex-shrink-0 opacity-75 group-hover:opacity-100 transition">
                ${canPromote ? `
                    <button class="btn-promote-track p-1.5 rounded-lg text-sub hover:text-indigo-500 hover:bg-indigo-500/10 transition" title="Đưa lên phát kế tiếp">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 10l7-7m0 0l7 7m-7-7v18"/></svg>
                    </button>
                ` : ''}
                ${canDelete ? `
                    <button class="btn-delete-track p-1.5 rounded-lg text-sub hover:text-rose-500 hover:bg-rose-500/10 transition" title="Xóa khỏi hàng đợi">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                    </button>
                ` : ''}
            </div>
        `;

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
                <img src="${track.thumbnail}" alt="" class="w-12 h-8 object-cover rounded-lg flex-shrink-0 shadow-sm">
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

        item.innerHTML = `
            <div class="flex items-center gap-2.5 min-w-0">
                <div class="w-9 h-9 rounded-full flex items-center justify-center text-base shadow-sm flex-shrink-0"
                     style="background-color: ${u.color || '#6366f1'}22; border: 1.5px solid ${u.color || '#6366f1'}88">
                    ${u.avatar || '🎧'}
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
    const voteSkipBtn = document.getElementById('btn-vote-skip');
    const forceSkipBtn = document.getElementById('btn-force-skip');
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

    if (voteSkipBtn) voteSkipBtn.onclick = () => window.roomSocket.voteSkip(false);
    if (forceSkipBtn) forceSkipBtn.onclick = () => window.roomSocket.voteSkip(true);

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
    const url = window.location.href;
    navigator.clipboard.writeText(url).then(() => {
        showToast('Đã sao chép link mời phòng!', 'success');
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

    // Chat Form Submit
    const chatForm = document.getElementById('chat-input-form');
    const chatInput = document.getElementById('chat-text-input');
    if (chatForm && chatInput) {
        chatForm.onsubmit = (e) => {
            e.preventDefault();
            const text = chatInput.value.trim();
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
// QUICK SUGGESTIONS STRIP
// =============================================================================
async function initQuickSuggestions() {
    const container = document.getElementById('quick-suggestions-chips');
    try {
        const res = await fetch('/api/suggestions');
        const data = await res.json();
        const suggestions = data.suggestions || [];
        window.appState.suggestions = suggestions;

        if (!container) return;
        container.innerHTML = '';

        suggestions.forEach(song => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'surface-subtle flex items-center gap-2.5 pl-1.5 pr-3 py-1.5 rounded-full text-left flex-shrink-0 group hover:border-indigo-500/40';
            chip.innerHTML = `
                <img src="${song.thumbnail}" alt="" class="w-7 h-7 rounded-full object-cover shadow-sm flex-shrink-0">
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
