/**
 * YouTube IFrame + Radio Mode + Pure Audio Fallback Synchronized Player
 */
class SyncPlayer {
    constructor() {
        this.player = null;
        this.audioEl = null;
        this.useAudioFallback = false;
        this.isReady = false;
        this.currentTrack = null;
        this.status = 'idle'; // 'playing', 'paused', 'idle'
        const savedMode = localStorage.getItem('floor2_player_mode');
        this.mode = ['radio', 'lyrics', 'video'].includes(savedMode) ? savedMode : 'radio'; // default: radio
        this.volume = parseInt(localStorage.getItem('floor2_volume') || '80', 10);
        this.isMuted = localStorage.getItem('floor2_muted') === 'true';
        this.isSyncing = false;
        this.progressInterval = null;
        this.driftThreshold = 2.5;
        this.onStateChangeCallbacks = [];
        this.userInteracted = false;
    }

    init() {
        this.initFallbackAudioElement();

        if (!window.YT || !window.YT.Player) {
            const tag = document.createElement('script');
            tag.src = "https://www.youtube.com/iframe_api";
            const firstScriptTag = document.getElementsByTagName('script')[0];
            firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);

            window.onYouTubeIframeAPIReady = () => {
                this.createPlayer();
            };
        } else if (!this.player) {
            this.createPlayer();
        }

        this.startProgressTicker();
        this.applyModeUI();

        const markInteracted = () => {
            this.userInteracted = true;
            document.removeEventListener('click', markInteracted);
            document.removeEventListener('keydown', markInteracted);
        };
        document.addEventListener('click', markInteracted, { once: true });
        document.addEventListener('keydown', markInteracted, { once: true });
    }

    initFallbackAudioElement() {
        this.audioEl = document.getElementById('fallback-audio-player');
        if (!this.audioEl || this.audioEl.dataset.bound) return;
        this.audioEl.dataset.bound = '1';

        this.audioEl.addEventListener('ended', () => {
            if (this.useAudioFallback && this.currentTrack) {
                window.roomSocket.trackEnded(this.currentTrack.id, this.currentTrack.uid);
            }
        });

        this.audioEl.addEventListener('error', () => {
            if (this.useAudioFallback && this.currentTrack) {
                const ytDirectBtn = document.getElementById('yt-fallback-btn');
                if (ytDirectBtn) {
                    ytDirectBtn.href = `https://www.youtube.com/watch?v=${this.currentTrack.id}`;
                    ytDirectBtn.classList.remove('hidden');
                }
            }
        });
    }

    createPlayer() {
        const playerContainer = document.getElementById('youtube-iframe');
        if (!playerContainer) return;

        this.player = new YT.Player('youtube-iframe', {
            height: '100%',
            width: '100%',
            playerVars: {
                autoplay: 1,
                controls: 1,
                rel: 0,
                modestbranding: 1,
                playsinline: 1,
                enablejsapi: 1,
                origin: window.location.origin
            },
            events: {
                'onReady': (e) => this.onPlayerReady(e),
                'onStateChange': (e) => this.onPlayerStateChange(e),
                'onError': (e) => this.onPlayerError(e)
            }
        });
    }

    onPlayerReady() {
        this.isReady = true;
        this.setVolume(this.volume);
        if (this.isMuted && this.player.mute) {
            this.player.mute();
        }

        if (this.currentTrack && this.lastRoomState) {
            this.syncWithRoomState(this.lastRoomState);
        }
    }

    onPlayerStateChange(event) {
        if (!this.isReady || this.isSyncing || this.useAudioFallback) return;

        const state = event.data;
        if (state === YT.PlayerState.ENDED) {
            if (this.currentTrack) {
                window.roomSocket.trackEnded(this.currentTrack.id, this.currentTrack.uid);
            }
        } else if (state === YT.PlayerState.PLAYING) {
            const curTime = this.getCurrentTime();
            this.triggerStateChange('playing', curTime);
        } else if (state === YT.PlayerState.PAUSED) {
            const curTime = this.getCurrentTime();
            this.triggerStateChange('paused', curTime);
        }
    }

    onPlayerError(event) {
        console.warn('[Player] YouTube error code:', event.data);
        // Error 101 / 150: video cannot be embedded -> auto-fallback to backend Pure Audio stream!
        if ((event.data === 150 || event.data === 101) && this.currentTrack) {
            this.activateAudioFallback(this.currentTrack.id);
        }
    }

    activateAudioFallback(videoId) {
        if (!this.audioEl) return;
        this.useAudioFallback = true;

        try {
            if (this.isReady && this.player && this.player.stopVideo) {
                this.player.stopVideo();
            }
        } catch (e) {}

        showToast('Video hạn chế nhúng — Tự động chuyển sang luồng Pure Audio Radio!', 'info');

        const baseTime = this.lastRoomState ? parseFloat(this.lastRoomState.current_time || 0) : 0;
        const updatedAt = this.lastRoomState ? parseFloat(this.lastRoomState.updated_at || (Date.now() / 1000)) : (Date.now() / 1000);
        const targetTime = this.status === 'playing' ? baseTime + Math.max(0, (Date.now() / 1000) - updatedAt) : baseTime;

        this.audioEl.src = `/api/youtube/audio/${encodeURIComponent(videoId)}`;
        this.audioEl.volume = this.volume / 100;
        this.audioEl.muted = this.isMuted;

        const onLoaded = () => {
            if (targetTime > 0 && isFinite(this.audioEl.duration)) {
                this.audioEl.currentTime = Math.min(targetTime, this.audioEl.duration - 1);
            }
            if (this.status === 'playing') {
                this.audioEl.play().catch(() => {});
            }
            this.audioEl.removeEventListener('loadedmetadata', onLoaded);
        };
        this.audioEl.addEventListener('loadedmetadata', onLoaded);
        this.audioEl.load();

        this.applyModeUI();
        this.updatePlayerUI();
    }

    deactivateAudioFallback() {
        this.useAudioFallback = false;
        if (this.audioEl) {
            try {
                this.audioEl.pause();
                this.audioEl.removeAttribute('src');
                this.audioEl.load();
            } catch (e) {}
        }
    }

    setMode(mode) {
        const cleanMode = ['radio', 'lyrics', 'video'].includes(mode) ? mode : 'radio';
        this.mode = cleanMode;
        localStorage.setItem('floor2_player_mode', cleanMode);
        this.applyModeUI();
        this.updatePlayerUI();

        if (cleanMode === 'lyrics') {
            if (typeof window.loadLyricsForTrack === 'function') {
                window.loadLyricsForTrack(this.currentTrack);
            }
            if (typeof window.updateLyricsHighlight === 'function') {
                window.updateLyricsHighlight(this.getCurrentTime(), true);
            }
        }
    }

    applyModeUI() {
        // When Pure Audio fallback is active, video cannot be shown (Radio or Lyrics both work!)
        const effectiveMode = (this.useAudioFallback && this.mode === 'video') ? 'radio' : this.mode;
        const isRadio = effectiveMode === 'radio';
        const isLyrics = effectiveMode === 'lyrics';
        const isVideo = effectiveMode === 'video';

        const iframeContainer = document.getElementById('youtube-iframe-container');
        const radioDeck = document.getElementById('radio-mode-deck');
        const lyricsDeck = document.getElementById('lyrics-mode-deck');
        const emptyState = document.getElementById('player-empty-state');
        const videoMetaRow = document.getElementById('video-track-meta-row');
        const btnRadio = document.getElementById('btn-mode-radio');
        const btnLyrics = document.getElementById('btn-mode-lyrics');
        const btnVideo = document.getElementById('btn-mode-video');
        const headerRadioBtn = document.getElementById('btn-header-radio-toggle');
        const stageHeading = document.getElementById('stage-mode-heading');
        const stageDot = document.getElementById('stage-mode-dot');
        const fallbackBadge = document.getElementById('radio-fallback-badge');

        // Keep YouTube iframe alive in DOM (1px offscreen) in Radio & Lyrics modes so audio NEVER pauses!
        if (iframeContainer) {
            if (!isVideo || !this.currentTrack) {
                iframeContainer.classList.add('yt-audio-engine-hidden');
            } else {
                iframeContainer.classList.remove('yt-audio-engine-hidden');
            }
        }

        if (radioDeck) radioDeck.classList.toggle('hidden', !isRadio);
        if (lyricsDeck) lyricsDeck.classList.toggle('hidden', !isLyrics);

        // Show Video empty state ONLY in Video Mode when no track is playing
        if (emptyState) {
            emptyState.classList.toggle('hidden', !isVideo || !!this.currentTrack);
        }

        // Hide redundant track meta row in Radio & Lyrics modes since both decks display track info
        if (videoMetaRow) {
            videoMetaRow.classList.toggle('hidden', !isVideo);
        }

        if (fallbackBadge) {
            fallbackBadge.classList.toggle('hidden', !this.useAudioFallback);
        }

        // Sync segmented switcher buttons (Radio -> Lời bài hát -> Video)
        if (btnRadio) btnRadio.classList.toggle('active', isRadio);
        if (btnLyrics) btnLyrics.classList.toggle('active', isLyrics);
        if (btnVideo) btnVideo.classList.toggle('active', isVideo);

        // Sync top header Radio quick pill
        if (headerRadioBtn) {
            headerRadioBtn.classList.toggle('border-pink-500/50', isRadio);
            headerRadioBtn.classList.toggle('bg-pink-500/10', isRadio);
            const lbl = headerRadioBtn.querySelector('.js-header-radio-label');
            if (lbl) lbl.textContent = isRadio ? 'Radio: Bật' : (isLyrics ? 'Lời bài hát' : 'Radio');
        }

        if (stageHeading) {
            stageHeading.textContent = isRadio
                ? 'Đài phát Radio • Không cần Video'
                : (isLyrics ? 'Lời bài hát • Chạy đồng bộ theo nhạc' : 'Sân khấu Video');
        }
        if (stageDot) {
            stageDot.className = isRadio
                ? 'w-2 h-2 rounded-full bg-pink-500 animate-pulse'
                : (isLyrics ? 'w-2 h-2 rounded-full bg-violet-500 animate-pulse' : 'w-2 h-2 rounded-full bg-indigo-500');
        }
    }

    getCurrentTime() {
        if (this.useAudioFallback && this.audioEl) {
            return this.audioEl.currentTime || 0;
        }
        if (this.isReady && this.player && typeof this.player.getCurrentTime === 'function') {
            return this.player.getCurrentTime() || 0;
        }
        return 0;
    }

    getDuration() {
        if (this.useAudioFallback && this.audioEl && isFinite(this.audioEl.duration) && this.audioEl.duration > 0) {
            return this.audioEl.duration;
        }
        if (this.isReady && this.player && typeof this.player.getDuration === 'function') {
            return this.player.getDuration() || (this.currentTrack ? this.currentTrack.duration : 0);
        }
        return this.currentTrack ? this.currentTrack.duration : 0;
    }

    setVolume(val) {
        this.volume = Math.max(0, Math.min(100, parseInt(val, 10)));
        localStorage.setItem('floor2_volume', this.volume);
        this.updateVolumeSliderFill();

        if (this.audioEl) {
            this.audioEl.volume = this.volume / 100;
            if (this.isMuted && this.volume > 0) this.audioEl.muted = false;
        }

        if (this.isReady && this.player && typeof this.player.setVolume === 'function') {
            this.player.setVolume(this.volume);
            if (this.isMuted && this.volume > 0) {
                this.player.unMute();
                this.isMuted = false;
                localStorage.setItem('floor2_muted', 'false');
            }
        }
    }

    updateVolumeSliderFill() {
        const volSlider = document.getElementById('player-volume-slider');
        if (volSlider) {
            const pct = this.isMuted ? 0 : this.volume;
            volSlider.style.background = `linear-gradient(to right, var(--accent-primary) ${pct}%, var(--slider-track) ${pct}%)`;
        }
    }

    toggleMute() {
        this.isMuted = !this.isMuted;
        localStorage.setItem('floor2_muted', this.isMuted ? 'true' : 'false');
        this.updateVolumeSliderFill();

        if (this.audioEl) {
            this.audioEl.muted = this.isMuted;
        }
        if (this.isReady && this.player) {
            if (this.isMuted) {
                this.player.mute();
            } else {
                this.player.unMute();
            }
        }
        return this.isMuted;
    }

    seekTo(seconds) {
        if (this.useAudioFallback && this.audioEl) {
            this.audioEl.currentTime = seconds;
            return;
        }
        if (this.isReady && this.player && typeof this.player.seekTo === 'function') {
            this.player.seekTo(seconds, true);
        }
    }

    syncWithRoomState(room) {
        if (!room) return;
        this.lastRoomState = room;

        const track = room.current_track;
        const status = room.status;
        const baseTime = parseFloat(room.current_time || 0);
        const updatedAt = parseFloat(room.updated_at || (Date.now() / 1000));

        let targetTime = baseTime;
        if (status === 'playing') {
            const elapsed = Math.max(0, (Date.now() / 1000) - updatedAt);
            targetTime = baseTime + elapsed;
        }

        const ytDirectBtn = document.getElementById('yt-fallback-btn');
        if (ytDirectBtn) ytDirectBtn.classList.add('hidden');

        if (!track) {
            this.deactivateAudioFallback();
            this.currentTrack = null;
            this.status = 'idle';
            if (this.isReady && this.player && typeof this.player.stopVideo === 'function') {
                this.player.stopVideo();
            }
            this.applyModeUI();
            this.updatePlayerUI();
            return;
        }

        const isNewTrack = !this.currentTrack || (this.currentTrack.id !== track.id);
        this.currentTrack = track;
        this.status = status;

        if (isNewTrack) {
            this.deactivateAudioFallback();
        }

        // Handle sync on fallback HTML5 <audio> if active
        if (this.useAudioFallback && this.audioEl) {
            const drift = Math.abs((this.audioEl.currentTime || 0) - targetTime);
            if (drift > this.driftThreshold && isFinite(this.audioEl.duration)) {
                this.audioEl.currentTime = Math.min(targetTime, this.audioEl.duration);
            }
            if (status === 'playing' && this.audioEl.paused) {
                this.audioEl.play().catch(() => {});
            } else if (status === 'paused' && !this.audioEl.paused) {
                this.audioEl.pause();
            }
            this.applyModeUI();
            this.updatePlayerUI();
            return;
        }

        if (!this.isReady || !this.player) {
            this.applyModeUI();
            this.updatePlayerUI();
            return;
        }

        this.isSyncing = true;

        try {
            if (isNewTrack) {
                this.player.loadVideoById({
                    videoId: track.id,
                    startSeconds: targetTime
                });

                if (status === 'paused') {
                    setTimeout(() => {
                        try { this.player.pauseVideo(); } catch (e) {}
                    }, 220);
                }
            } else {
                const currentTime = this.getCurrentTime();
                const drift = Math.abs(currentTime - targetTime);

                if (drift > this.driftThreshold) {
                    this.player.seekTo(targetTime, true);
                }

                const playerState = this.player.getPlayerState ? this.player.getPlayerState() : -1;
                if (status === 'playing' && playerState !== YT.PlayerState.PLAYING && playerState !== YT.PlayerState.BUFFERING) {
                    this.player.playVideo();
                } else if (status === 'paused' && playerState !== YT.PlayerState.PAUSED) {
                    this.player.pauseVideo();
                }
            }
        } catch (err) {
            console.error('[Player] Sync error:', err);
        } finally {
            setTimeout(() => {
                this.isSyncing = false;
            }, 500);
        }

        this.applyModeUI();
        this.updatePlayerUI();
    }

    startProgressTicker() {
        if (this.progressInterval) clearInterval(this.progressInterval);
        this.progressInterval = setInterval(() => {
            const progressBar = document.getElementById('player-progress-bar');
            const curEl = document.getElementById('player-time-current');
            const durEl = document.getElementById('player-time-duration');

            if (!this.currentTrack || this.status === 'idle') {
                if (progressBar) {
                    progressBar.value = 0;
                    progressBar.style.background = `var(--slider-track)`;
                }
                if (curEl) curEl.textContent = '00:00';
                if (durEl) durEl.textContent = '00:00';
                return;
            }

            const cur = this.getCurrentTime();
            const dur = this.getDuration();

            if (progressBar && dur > 0) {
                const percent = Math.min(100, (cur / dur) * 100);
                progressBar.value = percent;
                progressBar.style.background = `linear-gradient(to right, var(--accent-primary) 0%, var(--accent-pink) ${percent}%, var(--slider-track) ${percent}%)`;
            }

            if (curEl) curEl.textContent = this.formatTime(cur);
            if (durEl) durEl.textContent = dur > 0 ? this.formatTime(dur) : (this.currentTrack.duration_str || 'LIVE');

            // Smooth realtime synced lyrics highlight
            if (this.mode === 'lyrics' && typeof window.updateLyricsHighlight === 'function') {
                window.updateLyricsHighlight(cur);
            }
        }, 250);
    }

    formatTime(sec) {
        if (!sec || isNaN(sec) || sec < 0) return '00:00';
        const s = Math.floor(sec);
        const m = Math.floor(s / 60);
        const remS = s % 60;
        const h = Math.floor(m / 60);
        const remM = m % 60;
        if (h > 0) {
            return `${h}:${remM < 10 ? '0' : ''}${remM}:${remS < 10 ? '0' : ''}${remS}`;
        }
        return `${m < 10 ? '0' : ''}${m}:${remS < 10 ? '0' : ''}${remS}`;
    }

    updateUpNext(queue) {
        const upNextTitle = document.getElementById('radio-up-next-title');
        if (!upNextTitle) return;
        if (queue && queue.length > 0) {
            upNextTitle.textContent = `${queue[0].title} (${queue[0].added_by_name})`;
        } else {
            upNextTitle.textContent = 'Chưa có bài kế tiếp trong hàng đợi';
        }
    }

    updatePlayerUI() {
        const titleEl = document.getElementById('now-playing-title');
        const authorEl = document.getElementById('now-playing-author');
        const adderEl = document.getElementById('now-playing-adder');
        const thumbEl = document.getElementById('now-playing-thumb');
        const radioBg = document.getElementById('radio-mode-bg');
        const radioTitle = document.getElementById('radio-mode-title');
        const radioAuthor = document.getElementById('radio-mode-author');
        const radioAdder = document.getElementById('radio-mode-adder');
        const radioOnAir = document.getElementById('radio-on-air-badge');
        const radioVisualizer = document.getElementById('radio-visualizer');
        const vinyl = document.getElementById('radio-vinyl');
        const vinylGlow = document.getElementById('radio-vinyl-glow');
        const playBtnIcon = document.getElementById('play-btn-icon');
        const equalizer = document.getElementById('now-playing-equalizer');
        const ambientBg = document.getElementById('room-ambient-bg');
        const lyricsBg = document.getElementById('lyrics-mode-bg');
        const lyricsThumb = document.getElementById('lyrics-track-thumb');
        const lyricsTitle = document.getElementById('lyrics-track-title');
        const lyricsAuthor = document.getElementById('lyrics-track-author');

        this.updateVolumeSliderFill();

        if (!this.currentTrack) {
            if (titleEl) titleEl.textContent = 'Chưa có bài hát nào đang phát';
            if (authorEl) authorEl.textContent = 'Gõ tên bài hát vào ô tìm kiếm phía trên để bắt đầu';
            if (adderEl) adderEl.innerHTML = '';

            if (radioTitle) radioTitle.textContent = 'Chế độ Radio đã sẵn sàng';
            if (radioAuthor) radioAuthor.textContent = 'Gõ tên bài hát vào thanh tìm kiếm hoặc chọn gợi ý để lên sóng';
            if (radioAdder) radioAdder.innerHTML = '';
            if (radioBg) radioBg.style.backgroundImage = '';
            if (lyricsBg) lyricsBg.style.backgroundImage = '';
            if (lyricsTitle) lyricsTitle.textContent = 'Lời bài hát đồng bộ';
            if (lyricsAuthor) lyricsAuthor.textContent = 'Phát một bài hát để xem lời chạy theo nhạc';
            if (radioOnAir) {
                radioOnAir.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-white/15 border border-white/20 text-[10px] font-extrabold uppercase tracking-widest text-white';
                radioOnAir.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-slate-300"></span>CHỜ PHÁT SÓNG`;
            }

            if (equalizer) equalizer.classList.add('equalizer-paused');
            if (vinyl) vinyl.classList.add('vinyl-paused');
            if (vinylGlow) vinylGlow.classList.add('paused');
            if (radioVisualizer) radioVisualizer.classList.add('paused');
            if (ambientBg) ambientBg.classList.remove('active');
            return;
        }

        const isPlaying = this.status === 'playing';
        const safeVid = /^[a-zA-Z0-9_-]{11}$/.test(this.currentTrack.id || '') ? this.currentTrack.id : '';
        const thumbUrl = safeVid
            ? `https://i.ytimg.com/vi/${safeVid}/hqdefault.jpg`
            : 'https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=300&q=80';

        if (titleEl) titleEl.textContent = this.currentTrack.title;
        if (authorEl) authorEl.textContent = this.currentTrack.author || 'YouTube';
        if (adderEl) {
            adderEl.innerHTML = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 border border-indigo-500/20">Thêm bởi ${escapeHtml(this.currentTrack.added_by_name)}</span>`;
        }

        if (thumbEl) thumbEl.src = thumbUrl;
        if (radioBg) radioBg.style.backgroundImage = `url('${thumbUrl}')`;
        if (radioTitle) radioTitle.textContent = this.currentTrack.title;
        if (radioAuthor) radioAuthor.textContent = this.currentTrack.author || 'YouTube';
        if (lyricsThumb) lyricsThumb.src = thumbUrl;
        if (lyricsBg) lyricsBg.style.backgroundImage = `url('${thumbUrl}')`;
        if (lyricsTitle) lyricsTitle.textContent = this.currentTrack.title;
        if (lyricsAuthor) lyricsAuthor.textContent = this.currentTrack.author || 'YouTube';
        if (radioAdder) {
            radioAdder.innerHTML = `<span class="inline-flex items-center px-2 py-0.5 rounded-full bg-white/15 text-white/95 text-[10px] font-semibold">DJ: ${escapeHtml(this.currentTrack.added_by_name)}</span>`;
        }

        if (radioOnAir) {
            if (isPlaying) {
                radioOnAir.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-rose-500/90 border border-rose-300/40 text-[10px] font-extrabold uppercase tracking-widest text-white shadow-sm';
                radioOnAir.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></span>ON AIR`;
            } else {
                radioOnAir.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-500/30 border border-amber-300/30 text-[10px] font-extrabold uppercase tracking-widest text-amber-200';
                radioOnAir.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-amber-300"></span>TẠM DỪNG`;
            }
        }

        if (ambientBg) {
            ambientBg.style.backgroundImage = `url('${thumbUrl}')`;
            ambientBg.classList.add('active');
        }

        if (playBtnIcon) {
            if (isPlaying) {
                playBtnIcon.innerHTML = `<rect x="6" y="4" width="4" height="16" rx="1.5" fill="currentColor"/><rect x="14" y="4" width="4" height="16" rx="1.5" fill="currentColor"/>`;
            } else {
                playBtnIcon.innerHTML = `<path d="M8 5.14v14l11-7-11-7z" fill="currentColor"/>`;
            }
        }

        if (equalizer) equalizer.classList.toggle('equalizer-paused', !isPlaying);
        if (vinyl) vinyl.classList.toggle('vinyl-paused', !isPlaying);
        if (vinylGlow) vinylGlow.classList.toggle('paused', !isPlaying);
        if (radioVisualizer) radioVisualizer.classList.toggle('paused', !isPlaying);
    }

    triggerStateChange(newStatus, curTime) {
        this.status = newStatus;
        this.onStateChangeCallbacks.forEach(cb => cb(newStatus, curTime));
    }

    onStateChange(cb) {
        this.onStateChangeCallbacks.push(cb);
    }
}

window.syncPlayer = new SyncPlayer();
