/**
 * YouTube IFrame + Radio Mode + Pure Audio Fallback Synchronized Player
 *
 * Key Guarantees:
 * 1. Separate `loadedVideoId` from `currentTrack` so tracks arriving before YT.Player
 *    is ready are ALWAYS loaded the instant `onPlayerReady` fires.
 * 2. Server-time calibrated `getRoomLiveTime()` eliminates client PC clock skew and
 *    keeps progress bar + timestamps live even while buffering or awaiting autoplay unlock.
 * 3. Automatic Browser Autoplay Policy detection + 1-click audio unlock banner.
 * 4. Strictly LOCAL-ONLY Pause / Play / Seek so no user can disrupt other room members,
 *    with a 1-click "Về trực tiếp" resync button whenever locally paused or scrubbed.
 */
class SyncPlayer {
    constructor() {
        this.player = null;
        this.audioEl = null;
        this.useAudioFallback = false;
        this.isReady = false;
        this.currentTrack = null;
        this.loadedVideoId = null;
        this.lastRoomState = null;
        this.status = 'idle'; // room status: 'playing', 'paused', 'idle'
        const savedMode = localStorage.getItem('floor2_player_mode');
        this.mode = ['radio', 'lyrics', 'video'].includes(savedMode) ? savedMode : 'radio'; // default: radio
        this.volume = parseInt(localStorage.getItem('floor2_volume') || '80', 10);
        this.isMuted = localStorage.getItem('floor2_muted') === 'true';
        this.isSyncing = false;
        this.progressInterval = null;
        this.driftThreshold = 2.5;
        this.onStateChangeCallbacks = [];
        this.userInteracted = false;

        // Clock calibration & smooth 100ms lyrics interpolation
        this.clockOffsetSec = 0; // localNow - serverNow
        this._lastYtTime = 0;
        this._lastYtSampleAt = 0;
        this._tickCounter = 0;

        // Personal local playback state (never broadcast to room!)
        this.localPaused = false;
        this.localDetached = false;
        this.isUserScrubbing = false;
        this.autoplayBlocked = false;
        this.isDuckedForMc = false;
        this._autoplayCheckTimers = [];
        this._globalUnlockBound = false;
    }

    init() {
        this.initFallbackAudioElement();

        if (!window.YT || !window.YT.Player) {
            if (!document.querySelector('script[src*="youtube.com/iframe_api"]')) {
                const tag = document.createElement('script');
                tag.src = "https://www.youtube.com/iframe_api";
                const firstScriptTag = document.getElementsByTagName('script')[0];
                firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);
            }

            window.onYouTubeIframeAPIReady = () => {
                this.createPlayer();
            };
        } else if (!this.player) {
            this.createPlayer();
        }

        this.startProgressTicker();
        this.applyModeUI();

        if (!this._globalUnlockBound) {
            this._globalUnlockBound = true;
            const onFirstGesture = () => {
                this.userInteracted = true;
                if (this.autoplayBlocked && this.currentTrack && !this.localPaused) {
                    this.unlockAudioAndSync();
                }
            };
            document.addEventListener('pointerdown', onFirstGesture, { passive: true });
            document.addEventListener('keydown', onFirstGesture, { passive: true });
        }
    }

    reset() {
        this.clearAutoplayTimers();
        this.deactivateAudioFallback();
        this.currentTrack = null;
        this.loadedVideoId = null;
        this.lastRoomState = null;
        this.status = 'idle';
        this.localPaused = false;
        this.localDetached = false;
        this.autoplayBlocked = false;
        this._lastYtTime = 0;
        this._lastYtSampleAt = 0;
        this.setAutoplayBannerVisible(false);
        this.updateSyncLiveButton(false);

        try {
            if (this.isReady && this.player && typeof this.player.stopVideo === 'function') {
                this.player.stopVideo();
            }
        } catch (_) {}

        this.applyModeUI();
        this.updatePlayerUI();
    }

    initFallbackAudioElement() {
        this.audioEl = document.getElementById('fallback-audio-player');
        if (!this.audioEl || this.audioEl.dataset.bound) return;
        this.audioEl.dataset.bound = '1';

        this.audioEl.addEventListener('playing', () => {
            if (!this.useAudioFallback) return;
            this.autoplayBlocked = false;
            this.setAutoplayBannerVisible(false);
            this.clearAutoplayTimers();
            this.updatePlayerUI();
        });

        this.audioEl.addEventListener('ended', () => {
            if (this.useAudioFallback && this.currentTrack) {
                this._maybeNotifyTrackEnded();
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
        if (!playerContainer || this.player) return;

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
        this.loadedVideoId = null;
        this.setVolume(this.volume);
        if (this.isMuted && this.player.mute) {
            this.player.mute();
        } else if (this.player.unMute) {
            this.player.unMute();
        }

        if (this.lastRoomState && this.lastRoomState.current_track) {
            this.syncWithRoomState(this.lastRoomState);
        }
    }

    _maybeNotifyTrackEnded() {
        if (!this.currentTrack || this.localPaused) return;
        const dur = this.getDuration();
        const roomLive = this.getRoomLiveTime();
        // Never advance the room early if this user locally scrubbed to the end!
        if (dur > 0 && roomLive < Math.max(0, dur - 6.0)) {
            this.localDetached = true;
            this.updateSyncLiveButton(true);
            return;
        }
        window.roomSocket.trackEnded(this.currentTrack.id, this.currentTrack.uid);
    }

    onPlayerStateChange(event) {
        if (!this.isReady || this.useAudioFallback) return;

        const state = event.data;
        if (state === YT.PlayerState.PLAYING) {
            this.autoplayBlocked = false;
            this.localPaused = false;
            this.setAutoplayBannerVisible(false);
            this.clearAutoplayTimers();
            this._applyEffectiveVolume();

            const curTime = this.getRawPlayerTime();
            this._lastYtTime = curTime;
            this._lastYtSampleAt = performance.now();
            this.updatePlayerUI();
            if (this.mode === 'lyrics' && typeof window.updateLyricsHighlight === 'function') {
                window.updateLyricsHighlight(curTime, true);
            }
        } else if (state === YT.PlayerState.PAUSED) {
            const curTime = this.getRawPlayerTime();
            this._lastYtTime = curTime;
            this._lastYtSampleAt = 0;
            if (!this.isSyncing && this.status === 'playing') {
                // Local pause inside YouTube iframe controls -> stays strictly local!
                this.localPaused = true;
                this.updateSyncLiveButton(true);
            }
            this.updatePlayerUI();
        } else if (state === YT.PlayerState.ENDED) {
            if (!this.isSyncing) {
                this._maybeNotifyTrackEnded();
            }
        }
    }

    onPlayerError(event) {
        console.warn('[Player] YouTube error code:', event.data);
        // Error 101 / 150 (embed restricted) or 5 (HTML5 playback error) -> Pure Audio fallback!
        if ((event.data === 150 || event.data === 101 || event.data === 5) && this.currentTrack) {
            this.activateAudioFallback(this.currentTrack.id);
        }
    }

    clearAutoplayTimers() {
        this._autoplayCheckTimers.forEach(id => clearTimeout(id));
        this._autoplayCheckTimers = [];
    }

    setAutoplayBannerVisible(visible) {
        const banner = document.getElementById('autoplay-unlock-banner');
        if (banner) {
            banner.classList.toggle('hidden', !visible);
        }
    }

    updateSyncLiveButton(show) {
        const btn = document.getElementById('btn-sync-live');
        if (!btn) return;
        const shouldShow = !!(show && this.currentTrack && this.status === 'playing');
        btn.classList.toggle('hidden', !shouldShow);
    }

    scheduleAutoplayCheck() {
        this.clearAutoplayTimers();
        const check = () => {
            if (!this.currentTrack || this.status !== 'playing' || this.localPaused) {
                this.autoplayBlocked = false;
                this.setAutoplayBannerVisible(false);
                return;
            }
            if (this.useAudioFallback && this.audioEl) {
                if (this.audioEl.paused) {
                    this.autoplayBlocked = true;
                    this.setAutoplayBannerVisible(true);
                }
                return;
            }
            if (!this.isReady || !this.player || typeof this.player.getPlayerState !== 'function') {
                return;
            }
            const pState = this.player.getPlayerState();
            // UNSTARTED (-1), CUED (5), or PAUSED (2) when room is playing -> browser blocked autoplay!
            if (pState === -1 || pState === 5 || pState === 2) {
                this.autoplayBlocked = true;
                this.setAutoplayBannerVisible(true);
                this.updatePlayerUI();
            }
        };
        this._autoplayCheckTimers.push(setTimeout(check, 1500));
        this._autoplayCheckTimers.push(setTimeout(check, 3200));
    }

    unlockAudioAndSync() {
        if (!this.currentTrack) return;
        this.userInteracted = true;
        this.autoplayBlocked = false;
        this.localPaused = false;
        this.localDetached = false;
        this.setAutoplayBannerVisible(false);
        this.updateSyncLiveButton(false);

        const targetTime = this.getRoomLiveTime();

        if (this.useAudioFallback && this.audioEl) {
            if (isFinite(this.audioEl.duration) && this.audioEl.duration > 0) {
                this.audioEl.currentTime = Math.min(targetTime, Math.max(0, this.audioEl.duration - 1));
            }
            this.audioEl.muted = this.isMuted;
            this._applyEffectiveVolume();
            this.audioEl.play().catch(() => {
                this.autoplayBlocked = true;
                this.setAutoplayBannerVisible(true);
            });
            this.updatePlayerUI();
            return;
        }

        if (!this.isReady || !this.player) return;

        this.isSyncing = true;
        try {
            if (!this.isMuted && this.player.unMute) {
                this.player.unMute();
            }
            this._applyEffectiveVolume();
            if (this.loadedVideoId !== this.currentTrack.id) {
                this.loadedVideoId = this.currentTrack.id;
                this.player.loadVideoById({
                    videoId: this.currentTrack.id,
                    startSeconds: targetTime,
                });
            } else {
                this.player.seekTo(targetTime, true);
                this.player.playVideo();
            }
            this._lastYtTime = targetTime;
            this._lastYtSampleAt = performance.now();
        } catch (err) {
            console.error('[Player] Unlock audio error:', err);
        } finally {
            setTimeout(() => { this.isSyncing = false; }, 500);
        }

        this.updatePlayerUI();
        if (this.mode === 'lyrics' && typeof window.updateLyricsHighlight === 'function') {
            window.updateLyricsHighlight(targetTime, true);
        }
    }

    activateAudioFallback(videoId) {
        if (!this.audioEl) return;
        this.useAudioFallback = true;
        this.loadedVideoId = null;

        try {
            if (this.isReady && this.player && this.player.stopVideo) {
                this.player.stopVideo();
            }
        } catch (_) {}

        showToast('Video hạn chế nhúng — Tự động chuyển sang luồng Pure Audio Radio!', 'info');

        const targetTime = this.getRoomLiveTime();

        this.audioEl.src = `/api/youtube/audio/${encodeURIComponent(videoId)}`;
        this._applyEffectiveVolume();
        this.audioEl.muted = this.isMuted;

        const onLoaded = () => {
            if (targetTime > 0 && isFinite(this.audioEl.duration)) {
                this.audioEl.currentTime = Math.min(targetTime, Math.max(0, this.audioEl.duration - 1));
            }
            if (this.status === 'playing' && !this.localPaused) {
                this.audioEl.play().catch(() => {
                    this.autoplayBlocked = true;
                    this.setAutoplayBannerVisible(true);
                });
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
            } catch (_) {}
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
                window.updateLyricsHighlight(this.getSmoothCurrentTime(), true);
            }
        }
    }

    applyModeUI() {
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

        if (iframeContainer) {
            if (!isVideo || !this.currentTrack) {
                iframeContainer.classList.add('yt-audio-engine-hidden');
            } else {
                iframeContainer.classList.remove('yt-audio-engine-hidden');
            }
        }

        if (radioDeck) radioDeck.classList.toggle('hidden', !isRadio);
        if (lyricsDeck) lyricsDeck.classList.toggle('hidden', !isLyrics);

        if (emptyState) {
            emptyState.classList.toggle('hidden', !isVideo || !!this.currentTrack);
        }

        if (videoMetaRow) {
            videoMetaRow.classList.toggle('hidden', !isVideo);
        }

        if (fallbackBadge) {
            fallbackBadge.classList.toggle('hidden', !this.useAudioFallback);
        }

        if (btnRadio) btnRadio.classList.toggle('active', isRadio);
        if (btnLyrics) btnLyrics.classList.toggle('active', isLyrics);
        if (btnVideo) btnVideo.classList.toggle('active', isVideo);

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

    getRoomLiveTime() {
        if (!this.lastRoomState || !this.currentTrack) return 0;
        const baseTime = parseFloat(this.lastRoomState.current_time || 0) || 0;
        const updatedAt = parseFloat(this.lastRoomState.updated_at || 0);
        let live = baseTime;
        if (this.status === 'playing' && updatedAt > 0) {
            const serverNow = (Date.now() / 1000) - this.clockOffsetSec;
            const elapsed = Math.max(0, serverNow - updatedAt);
            live = baseTime + elapsed;
        }
        const dur = this.getDuration();
        if (dur > 0) {
            live = Math.min(live, Math.max(0, dur - 0.5));
        }
        return Math.max(0, live);
    }

    getRawPlayerTime() {
        if (this.useAudioFallback && this.audioEl) {
            return this.audioEl.currentTime || 0;
        }
        if (this.isReady && this.player && typeof this.player.getCurrentTime === 'function') {
            return this.player.getCurrentTime() || 0;
        }
        return 0;
    }

    isActuallyPlayingLocally() {
        if (!this.currentTrack || this.status === 'idle' || this.localPaused) return false;
        if (this.useAudioFallback && this.audioEl) {
            return !this.audioEl.paused;
        }
        if (this.isReady && this.player && typeof this.player.getPlayerState === 'function') {
            const st = this.player.getPlayerState();
            return st === YT.PlayerState.PLAYING || st === YT.PlayerState.BUFFERING;
        }
        return !this.autoplayBlocked && this.status === 'playing';
    }

    getCurrentTime() {
        const raw = this.getRawPlayerTime();
        // If player hasn't buffered/started yet (e.g. iframe still loading or autoplay waiting),
        // show the room's live synced position so users always see where the track is!
        if (!this.localPaused && !this.localDetached) {
            const roomLive = this.getRoomLiveTime();
            if (!this.isReady && !this.useAudioFallback) {
                return roomLive;
            }
            if (!this.useAudioFallback && this.player && typeof this.player.getPlayerState === 'function') {
                const st = this.player.getPlayerState();
                if (st === -1 || st === 5 || (st !== YT.PlayerState.PLAYING && raw < 0.5 && roomLive > 1.0)) {
                    return roomLive;
                }
            }
            if (this.useAudioFallback && this.audioEl && this.audioEl.paused && raw < 0.5 && roomLive > 1.0) {
                return roomLive;
            }
        }
        return raw;
    }

    getSmoothCurrentTime() {
        if (this.useAudioFallback && this.audioEl) {
            if (this.audioEl.paused && !this.localPaused && !this.localDetached) {
                return this.getCurrentTime();
            }
            return this.audioEl.currentTime || 0;
        }
        if (!this.isReady || !this.player || typeof this.player.getCurrentTime !== 'function') {
            return this.getCurrentTime();
        }
        const raw = this.player.getCurrentTime() || 0;
        const pState = this.player.getPlayerState ? this.player.getPlayerState() : -1;
        if (pState !== YT.PlayerState.PLAYING) {
            this._lastYtTime = raw;
            this._lastYtSampleAt = 0;
            return this.getCurrentTime();
        }
        const now = performance.now();
        // YouTube IFrame updates getCurrentTime() in ~200-250ms steps; interpolate smoothly between steps
        if (Math.abs(raw - this._lastYtTime) > 0.015 || this._lastYtSampleAt === 0) {
            this._lastYtTime = raw;
            this._lastYtSampleAt = now;
            return raw;
        }
        const delta = Math.min(0.32, Math.max(0, (now - this._lastYtSampleAt) / 1000));
        return this._lastYtTime + delta;
    }

    getDuration() {
        if (this.useAudioFallback && this.audioEl && isFinite(this.audioEl.duration) && this.audioEl.duration > 0) {
            return this.audioEl.duration;
        }
        if (this.isReady && this.player && typeof this.player.getDuration === 'function') {
            const ytDur = this.player.getDuration();
            if (ytDur && isFinite(ytDur) && ytDur > 0) return ytDur;
        }
        return (this.currentTrack && Number(this.currentTrack.duration)) || 0;
    }

    setVolume(val) {
        this.volume = Math.max(0, Math.min(100, parseInt(val, 10)));
        localStorage.setItem('floor2_volume', this.volume);
        this.updateVolumeSliderFill();
        this._applyEffectiveVolume();
    }

    setMcDucking(active) {
        this.isDuckedForMc = !!active;
        this._applyEffectiveVolume();
    }

    _applyEffectiveVolume() {
        const effVol = this.isDuckedForMc ? Math.max(6, Math.round(this.volume * 0.18)) : this.volume;

        if (this.audioEl) {
            this.audioEl.volume = effVol / 100;
            if (this.isMuted && this.volume > 0) this.audioEl.muted = false;
        }

        if (this.isReady && this.player && typeof this.player.setVolume === 'function') {
            this.player.setVolume(effVol);
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

    // Personal Local-Only Play / Pause (NEVER affects other members!)
    toggleLocalPlayPause() {
        if (!this.currentTrack) return;
        this.userInteracted = true;

        if (this.autoplayBlocked) {
            this.unlockAudioAndSync();
            return;
        }

        const currentlyPlaying = this.isActuallyPlayingLocally();
        if (currentlyPlaying) {
            this.localPaused = true;
            this._lastYtSampleAt = 0;
            this.clearAutoplayTimers();
            this.isSyncing = true;
            try {
                if (this.useAudioFallback && this.audioEl) {
                    this.audioEl.pause();
                } else if (this.isReady && this.player && typeof this.player.pauseVideo === 'function') {
                    this.player.pauseVideo();
                }
            } catch (_) {}
            setTimeout(() => { this.isSyncing = false; }, 300);
            this.updateSyncLiveButton(true);
            this.updatePlayerUI();
        } else {
            this.localPaused = false;
            this.autoplayBlocked = false;
            this.setAutoplayBannerVisible(false);
            this.isSyncing = true;
            try {
                if (this.useAudioFallback && this.audioEl) {
                    this.audioEl.play().catch(() => {
                        this.autoplayBlocked = true;
                        this.setAutoplayBannerVisible(true);
                    });
                } else if (this.isReady && this.player) {
                    if (this.loadedVideoId !== this.currentTrack.id) {
                        const liveTime = this.getRoomLiveTime();
                        this.loadedVideoId = this.currentTrack.id;
                        this.player.loadVideoById({
                            videoId: this.currentTrack.id,
                            startSeconds: liveTime,
                        });
                        this._lastYtTime = liveTime;
                    } else if (typeof this.player.playVideo === 'function') {
                        this.player.playVideo();
                    }
                    this._lastYtSampleAt = performance.now();
                }
            } catch (_) {}
            setTimeout(() => { this.isSyncing = false; }, 400);
            const cur = this.getCurrentTime();
            const drift = Math.abs(cur - this.getRoomLiveTime());
            this.localDetached = drift > 3.5;
            this.updateSyncLiveButton(this.localDetached);
            this.updatePlayerUI();
        }
    }

    // Personal Local-Only Seek (or immediate local jump for Host Master Seek)
    seekLocal(seconds, isMasterSeek = false) {
        if (!this.currentTrack) return;
        const dur = this.getDuration();
        const clamped = dur > 0 ? Math.max(0, Math.min(seconds, dur)) : Math.max(0, seconds);
        this.userInteracted = true;
        this._lastYtTime = clamped;
        this._lastYtSampleAt = performance.now();

        if (isMasterSeek) {
            this.localDetached = false;
            this.updateSyncLiveButton(false);
        } else {
            const roomLive = this.getRoomLiveTime();
            this.localDetached = Math.abs(clamped - roomLive) > 3.5;
            this.updateSyncLiveButton(this.localPaused || this.localDetached);
        }

        if (this.useAudioFallback && this.audioEl) {
            this.audioEl.currentTime = clamped;
            if (!this.localPaused && this.audioEl.paused) {
                this.audioEl.play().catch(() => {});
            }
        } else if (this.isReady && this.player) {
            if (this.loadedVideoId !== this.currentTrack.id) {
                this.loadedVideoId = this.currentTrack.id;
                this.player.loadVideoById({
                    videoId: this.currentTrack.id,
                    startSeconds: clamped,
                });
                if (this.localPaused) {
                    setTimeout(() => {
                        try { this.player.pauseVideo(); } catch (_) {}
                    }, 220);
                }
            } else if (typeof this.player.seekTo === 'function') {
                this.player.seekTo(clamped, true);
                if (!this.localPaused && this.autoplayBlocked) {
                    this.autoplayBlocked = false;
                    this.setAutoplayBannerVisible(false);
                    this.player.playVideo();
                }
            }
        }

        this._renderProgressUI(clamped, dur);
        if (this.mode === 'lyrics' && typeof window.updateLyricsHighlight === 'function') {
            window.updateLyricsHighlight(clamped, true);
        }
    }

    seekTo(seconds) {
        this.seekLocal(seconds);
    }

    resyncToLive() {
        if (!this.currentTrack) return;
        this.unlockAudioAndSync();
    }

    syncWithRoomState(room, opts = {}) {
        if (!room) return;
        const fromHostControl = opts && opts.fromHostControl ? opts.fromHostControl : null;
        if (typeof room.server_time === 'number' && isFinite(room.server_time)) {
            this.clockOffsetSec = (Date.now() / 1000) - room.server_time;
        }
        this.lastRoomState = room;

        const track = room.current_track;
        const status = room.status;

        const ytDirectBtn = document.getElementById('yt-fallback-btn');
        if (ytDirectBtn) ytDirectBtn.classList.add('hidden');

        if (!track) {
            this.clearAutoplayTimers();
            this.deactivateAudioFallback();
            this.currentTrack = null;
            this.loadedVideoId = null;
            this.status = 'idle';
            this.localPaused = false;
            this.localDetached = false;
            this.autoplayBlocked = false;
            this.setAutoplayBannerVisible(false);
            this.updateSyncLiveButton(false);
            if (this.isReady && this.player && typeof this.player.stopVideo === 'function') {
                this.player.stopVideo();
            }
            this.applyModeUI();
            this.updatePlayerUI();
            return;
        }

        const isNewTrack = !this.currentTrack || (this.currentTrack.id !== track.id) || (this.currentTrack.uid !== track.uid);
        this.currentTrack = track;
        this.status = status;

        if (isNewTrack) {
            this.deactivateAudioFallback();
            // On a brand-new track, rejoin live sync unless the user explicitly paused locally
            this.localDetached = false;
            this.autoplayBlocked = false;
            this.setAutoplayBannerVisible(false);
        }

        // If Host (Master) triggers pause/play/seek for the room:
        // - Host always stays synced (never locally paused against themselves)
        // - Non-host members who are actively listening (not locally paused) follow Host immediately!
        if (fromHostControl) {
            if (window.appState && window.appState.isHost) {
                this.localPaused = false;
                this.localDetached = false;
            } else if (!this.localPaused) {
                this.localDetached = false;
            }
        }

        const targetTime = this.getRoomLiveTime();

        if (isNewTrack || (fromHostControl === 'seek' && !this.localPaused)) {
            this._lastYtTime = targetTime;
            this._lastYtSampleAt = status === 'playing' ? performance.now() : 0;
        }

        // Immediately paint live progress bar & timestamps so user sees where the song is at 0ms!
        this._renderProgressUI(this.localPaused ? this.getRawPlayerTime() : targetTime, this.getDuration());

        const effectiveDriftThreshold = fromHostControl ? 0.6 : this.driftThreshold;

        // Handle sync on fallback HTML5 <audio> if active
        if (this.useAudioFallback && this.audioEl) {
            if (!this.localPaused && !this.localDetached) {
                const drift = Math.abs((this.audioEl.currentTime || 0) - targetTime);
                if (drift > effectiveDriftThreshold && isFinite(this.audioEl.duration)) {
                    this.audioEl.currentTime = Math.min(targetTime, Math.max(0, this.audioEl.duration - 1));
                }
                if (status === 'playing' && this.audioEl.paused) {
                    this.audioEl.play().catch(() => {
                        this.autoplayBlocked = true;
                        this.setAutoplayBannerVisible(true);
                    });
                } else if (status === 'paused' && !this.audioEl.paused) {
                    this.audioEl.pause();
                }
            }
            this.updateSyncLiveButton(this.localPaused || this.localDetached);
            this.applyModeUI();
            this.updatePlayerUI();
            return;
        }

        if (!this.isReady || !this.player) {
            // YT.Player is still initializing; loadedVideoId remains null so onPlayerReady WILL load it!
            this.applyModeUI();
            this.updatePlayerUI();
            return;
        }

        // CRITICAL FIX: Compare against `loadedVideoId` (what YT actually loaded), NOT `currentTrack`!
        const needsLoad = (this.loadedVideoId !== track.id) || isNewTrack;

        this.isSyncing = true;

        try {
            if (needsLoad) {
                this.loadedVideoId = track.id;
                if (status === 'paused' || this.localPaused) {
                    if (typeof this.player.cueVideoById === 'function') {
                        this.player.cueVideoById({
                            videoId: track.id,
                            startSeconds: targetTime,
                        });
                    } else {
                        this.player.loadVideoById({
                            videoId: track.id,
                            startSeconds: targetTime,
                        });
                        setTimeout(() => {
                            try { this.player.pauseVideo(); } catch (_) {}
                        }, 240);
                    }
                } else {
                    this.player.loadVideoById({
                        videoId: track.id,
                        startSeconds: targetTime,
                    });
                    this.scheduleAutoplayCheck();
                }
            } else if (!this.localPaused && !this.localDetached) {
                const currentTime = this.getRawPlayerTime();
                const drift = Math.abs(currentTime - targetTime);

                if (drift > effectiveDriftThreshold) {
                    this.player.seekTo(targetTime, true);
                }

                const playerState = this.player.getPlayerState ? this.player.getPlayerState() : -1;
                if (status === 'playing' && playerState !== YT.PlayerState.PLAYING && playerState !== YT.PlayerState.BUFFERING) {
                    this.player.playVideo();
                    this.scheduleAutoplayCheck();
                } else if (status === 'paused' && playerState !== YT.PlayerState.PAUSED) {
                    this.player.pauseVideo();
                }
            } else if (status === 'paused' && this.player.getPlayerState && this.player.getPlayerState() === YT.PlayerState.PLAYING) {
                // Host paused the whole room -> pause even if member had scrubbed earlier
                this.player.pauseVideo();
            }
        } catch (err) {
            console.error('[Player] Sync error:', err);
        } finally {
            setTimeout(() => {
                this.isSyncing = false;
            }, 500);
        }

        if (fromHostControl === 'seek' && !this.localPaused && this.mode === 'lyrics' && typeof window.updateLyricsHighlight === 'function') {
            window.updateLyricsHighlight(targetTime, true);
        }

        this.updateSyncLiveButton(this.localPaused || this.localDetached);
        this.applyModeUI();
        this.updatePlayerUI();
    }

    _renderProgressUI(cur, dur) {
        const progressBar = document.getElementById('player-progress-bar');
        const curEl = document.getElementById('player-time-current');
        const durEl = document.getElementById('player-time-duration');

        if (progressBar && !this.isUserScrubbing && dur > 0) {
            const percent = Math.min(100, Math.max(0, (cur / dur) * 100));
            progressBar.value = percent;
            progressBar.style.background = `linear-gradient(to right, var(--accent-primary) 0%, var(--accent-pink) ${percent}%, var(--slider-track) ${percent}%)`;
        }

        if (curEl) curEl.textContent = this.formatTime(cur);
        if (durEl) durEl.textContent = dur > 0 ? this.formatTime(dur) : ((this.currentTrack && this.currentTrack.duration_str) || 'LIVE');
    }

    startProgressTicker() {
        if (this.progressInterval) clearInterval(this.progressInterval);
        this.progressInterval = setInterval(() => {
            this._tickCounter = (this._tickCounter + 1) % 3;

            if (!this.currentTrack || this.status === 'idle') {
                if (this._tickCounter === 0) {
                    const progressBar = document.getElementById('player-progress-bar');
                    const curEl = document.getElementById('player-time-current');
                    const durEl = document.getElementById('player-time-duration');
                    if (progressBar) {
                        progressBar.value = 0;
                        progressBar.style.background = `var(--slider-track)`;
                    }
                    if (curEl) curEl.textContent = '00:00';
                    if (durEl) durEl.textContent = '00:00';
                }
                return;
            }

            // High-precision 100ms lyrics highlight using interpolated player clock
            if (this.mode === 'lyrics' && typeof window.updateLyricsHighlight === 'function') {
                window.updateLyricsHighlight(this.getSmoothCurrentTime());
            }

            // Update progress bar & time labels every 300ms (zero unnecessary DOM thrashing)
            if (this._tickCounter !== 0) return;

            const cur = this.getCurrentTime();
            const dur = this.getDuration();
            this._renderProgressUI(cur, dur);

            // Keep "Về trực tiếp" button accurate if user paused or drifted away from live room
            if (this.status === 'playing') {
                const roomLive = this.getRoomLiveTime();
                const drifted = this.localPaused || this.localDetached || (
                    this.isActuallyPlayingLocally() && Math.abs(cur - roomLive) > 4.5
                );
                this.updateSyncLiveButton(drifted);
            }
        }, 100);
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

        const isPlayingLocal = this.status === 'playing' && !this.localPaused && !this.autoplayBlocked;
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
            if (isPlayingLocal) {
                radioOnAir.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-rose-500/90 border border-rose-300/40 text-[10px] font-extrabold uppercase tracking-widest text-white shadow-sm';
                radioOnAir.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></span>ON AIR`;
            } else if (this.autoplayBlocked) {
                radioOnAir.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-500/30 border border-amber-300/40 text-[10px] font-extrabold uppercase tracking-widest text-amber-200';
                radioOnAir.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-amber-300 animate-pulse"></span>BẤM ĐỂ NGHE`;
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
            if (isPlayingLocal) {
                playBtnIcon.innerHTML = `<rect x="6" y="4" width="4" height="16" rx="1.5" fill="currentColor"/><rect x="14" y="4" width="4" height="16" rx="1.5" fill="currentColor"/>`;
            } else {
                playBtnIcon.innerHTML = `<path d="M8 5.14v14l11-7-11-7z" fill="currentColor"/>`;
            }
        }

        if (equalizer) equalizer.classList.toggle('equalizer-paused', !isPlayingLocal);
        if (vinyl) vinyl.classList.toggle('vinyl-paused', !isPlayingLocal);
        if (vinylGlow) vinylGlow.classList.toggle('paused', !isPlayingLocal);
        if (radioVisualizer) radioVisualizer.classList.toggle('paused', !isPlayingLocal);
    }

    triggerStateChange(newStatus, curTime) {
        this.onStateChangeCallbacks.forEach(cb => cb(newStatus, curTime));
    }

    onStateChange(cb) {
        this.onStateChangeCallbacks.push(cb);
    }
}

window.syncPlayer = new SyncPlayer();
