"""Hardened FastAPI backend for Floor 2 collaborative YouTube Music Room & Realtime Chat."""
import asyncio
import hmac
import math
import os
import re
import secrets
import time
import unicodedata
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Set, Tuple
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from services.ai import (
    AI_BOT_AVATAR,
    AI_BOT_COLOR,
    AI_BOT_ID,
    AI_BOT_NAME,
    analyze_room_vibe,
    build_mc_context,
    build_room_ai_context,
    chat_with_em_be_floor_2,
    extract_lyrics_excerpt,
    generate_ai_dj_playlist,
    get_ai_stats,
    get_or_create_mc_intro,
    is_ai_mention,
    mc_audio_cache,
)
from services.cache import TTLCache
from services.database import DatabaseManager
from services.ddos import DDoSGuard
from services.gifs import (
    get_curated_gifs,
    get_curated_stickers,
    get_gif_cache_stats,
    get_sticker_cache_stats,
    search_gifs,
    search_stickers,
)
from services.keep_alive import (
    get_keep_alive_status,
    keep_alive_targets,
    keep_alive_url,
    start_keep_alive,
)
from services.lyrics import get_lyrics_cache_stats, get_synced_lyrics, lyrics_cache
from services.security import (
    Session,
    SessionManager,
    SlidingWindowRateLimiter,
    detect_safe_image_mime,
    get_client_ip,
    hash_passcode,
    is_valid_ws_origin,
    sanitize_avatar,
    sanitize_color,
    sanitize_giphy_id,
    sanitize_media_id,
    sanitize_reaction,
    sanitize_text,
    verify_passcode,
)
from services.youtube import (
    extract_video_id,
    get_cached_related_videos,
    get_related_videos,
    get_video_info,
    get_youtube_cache_stats,
    search_youtube,
    format_duration,
    get_audio_stream_url,
    warmup_youtube_connection,
)

# Resource & security limits
MAX_ROOMS = 300
MAX_QUEUE_SIZE = 50
MAX_USER_QUEUE_ITEMS = 10
MAX_WS_FRAME_BYTES = 4096
MAX_CONNS_PER_IP_PER_ROOM = 5
EMPTY_ROOM_TTL_SEC = 1800
PRIORITY_VOTE_TTL_SEC = 60.0
MAX_UPLOAD_IMAGE_BYTES = 2_621_440  # 2.5 MB per image
MAX_MEDIA_ITEMS = 120
MAX_MEDIA_TOTAL_BYTES = 60 * 1024 * 1024  # 60 MB total LRU cap

session_manager = SessionManager(ttl_sec=86400, max_sessions=10_000)
rate_limiter = SlidingWindowRateLimiter()
ddos_guard = DDoSGuard()
public_rooms_cache: TTLCache[List[Dict]] = TTLCache("public_rooms", max_size=4, default_ttl=3.0)
room_suggestions_cache: TTLCache[Dict] = TTLCache("room_suggestions", max_size=100, default_ttl=60.0)
db = DatabaseManager()
db.init()


def invalidate_rooms_cache():
    public_rooms_cache.clear()

# Bounded in-memory LRU cache for uploaded chat images (backed by persistent DB)
chat_media_store: "OrderedDict[str, Tuple[bytes, str, str, float]]" = OrderedDict()
chat_media_total_bytes = 0


def store_chat_media(raw: bytes, mime: str, uploader_id: str) -> str:
    """Store validated image bytes in RAM LRU cache and persist asynchronously to DB."""
    global chat_media_total_bytes
    media_id = secrets.token_hex(12)

    while chat_media_store and (
        len(chat_media_store) >= MAX_MEDIA_ITEMS
        or (chat_media_total_bytes + len(raw)) > MAX_MEDIA_TOTAL_BYTES
    ):
        _, evicted = chat_media_store.popitem(last=False)
        chat_media_total_bytes = max(0, chat_media_total_bytes - len(evicted[0]))

    chat_media_store[media_id] = (raw, mime, uploader_id, time.time())
    chat_media_total_bytes += len(raw)
    db.schedule_save_media(media_id, raw, mime, uploader_id)
    return media_id


async def _prewarm_mc_for_room(room: "Room") -> None:
    """Pre-generate & cache female Radio MC intro + MP3 for current_track and #1 Up-Next track."""
    if not room or not room.mc_enabled:
        return
    targets: List["Track"] = []
    if room.current_track:
        targets.append(room.current_track)
    targets.extend(room.queue[:2])  # 2 Up-Next tracks so fast host skips still hit a ready intro
    if not targets:
        return
    for t in targets[:3]:
        try:
            await get_or_create_mc_intro(room.id, t.to_dict(), build_mc_context(room, t))
        except Exception:
            pass


def schedule_prewarm_room_related(room: "Room"):
    """Proactively pre-warm YouTube related tracks & MC Radio intro for current_track and #1 Up-Next in background."""
    vids: List[str] = []
    if room.current_track and room.current_track.id:
        vids.append(room.current_track.id)
    if room.queue and room.queue[0].id and room.queue[0].id not in vids:
        vids.append(room.queue[0].id)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for vid in vids:
        if get_cached_related_videos(vid) is None:
            loop.create_task(get_related_videos(vid, limit=14))
    if room.mc_enabled and (room.current_track or room.queue):
        loop.create_task(_prewarm_mc_for_room(room))


async def _startup_prewarm():
    await warmup_youtube_connection()
    for r in list(rooms.values()):
        schedule_prewarm_room_related(r)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_keep_alive()
    asyncio.create_task(_startup_prewarm())
    yield
    db.close()


app = FastAPI(title="Floor 2 - Sync Music Room & Chat", version="2.1.0", lifespan=lifespan)

# Strict CORS (no ambient cookies with wildcard origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Session-Token"],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Attach strict security headers & CSP to mitigate XSS, clickjacking, and MIME sniffing."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://www.youtube.com https://s.ytimg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https://i.ytimg.com https://images.unsplash.com https://media.giphy.com https://*.giphy.com https://i.giphy.com; "
        "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
        "media-src 'self' blob:; "
        "connect-src 'self' ws: wss:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none';"
    )
    if request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=300")
    return response


@app.middleware("http")
async def ddos_protection_middleware(request: Request, call_next):
    """Outermost L7 Anti-DDoS shield: IP auto-ban, burst/sustained rate limit, scanner trap & load shedding."""
    client_ip = get_client_ip(request.headers, request.client)
    raw_cl = request.headers.get("content-length")
    content_length: Optional[int] = None
    if raw_cl and raw_cl.strip().isdigit():
        content_length = int(raw_cl.strip())

    allowed, status_code, detail, retry_after = ddos_guard.check_http_request(
        ip=client_ip,
        path=request.url.path,
        method=request.method,
        full_url_len=len(str(request.url)),
        content_length=content_length,
    )
    if not allowed:
        headers = {"X-Content-Type-Options": "nosniff"}
        if retry_after > 0:
            headers["Retry-After"] = str(retry_after)
        return JSONResponse({"detail": detail}, status_code=status_code, headers=headers)

    try:
        return await call_next(request)
    finally:
        ddos_guard.release_http(client_ip)


# ============================================================================
# HELPERS: AUTH & RATE LIMIT
# ============================================================================

def require_rate_limit(key: str, limit: int, window_sec: float):
    allowed, retry_after = rate_limiter.allow(key, limit, window_sec)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Bạn thao tác quá nhanh. Vui lòng thử lại sau {int(math.ceil(retry_after))} giây.",
            headers={"Retry-After": str(int(math.ceil(retry_after)))},
        )


def resolve_session(x_session_token: Optional[str]) -> Optional[Session]:
    return session_manager.get_session(x_session_token)


def require_session(x_session_token: Optional[str]) -> Session:
    sess = session_manager.get_session(x_session_token)
    if not sess:
        raise HTTPException(status_code=401, detail="Phiên làm việc không hợp lệ hoặc đã hết hạn")
    return sess


# ============================================================================
# MODELS & ROOM STATE (WITH PER-ROOM LOCKING AGAINST RACE CONDITIONS)
# ============================================================================

class User:
    def __init__(self, user_id: str, name: str, avatar: str, color: str, client_ip: str = "unknown"):
        self.user_id = user_id
        self.name = sanitize_text(name, max_len=25, fallback="Khách")
        self.avatar = sanitize_avatar(avatar)
        self.color = sanitize_color(color)
        self.client_ip = client_ip
        self.joined_at = time.time()

    def to_dict(self, host_id: str):
        return {
            "id": self.user_id,
            "name": self.name,
            "avatar": self.avatar,
            "color": self.color,
            "is_host": self.user_id == host_id,
            "joined_at": self.joined_at,
        }


class Track:
    def __init__(
        self,
        track_id: str,
        title: str,
        author: str,
        duration: int,
        duration_str: str,
        added_by_id: str,
        added_by_name: str,
    ):
        self.uid = secrets.token_hex(6)
        self.id = track_id
        self.title = sanitize_text(title, max_len=160, fallback=f"Video {track_id}")
        self.author = sanitize_text(author, max_len=80, fallback="YouTube")
        # Always construct thumbnail strictly from validated 11-char video ID (prevents Stored XSS)
        self.thumbnail = f"https://i.ytimg.com/vi/{track_id}/hqdefault.jpg"
        self.duration = max(0, min(int(duration or 0), 86400))
        self.duration_str = sanitize_text(duration_str, max_len=16, fallback=format_duration(self.duration))
        self.added_by_id = added_by_id
        self.added_by_name = sanitize_text(added_by_name, max_len=30, fallback="Khách")
        self.added_at = time.time()

    def to_dict(self):
        return {
            "uid": self.uid,
            "id": self.id,
            "title": self.title,
            "author": self.author,
            "thumbnail": self.thumbnail,
            "duration": self.duration,
            "duration_str": self.duration_str,
            "added_by_id": self.added_by_id,
            "added_by_name": self.added_by_name,
            "added_at": self.added_at,
        }


class Room:
    def __init__(
        self,
        room_id: str,
        name: str,
        host_id: str = "",
        dj_mode: bool = False,
        is_private: bool = False,
        passcode_hash: str = "",
    ):
        self.id = room_id
        self.name = sanitize_text(name, max_len=60, fallback=f"Phòng #{room_id}")
        self.created_at = time.time()
        self.last_empty_at: Optional[float] = time.time()
        self.host_id = host_id
        self.dj_mode = bool(dj_mode)
        self.is_private = bool(is_private or passcode_hash)
        self.passcode_hash = passcode_hash
        self.invite_token = secrets.token_urlsafe(24) if self.is_private else ""
        self.authorized_users: Set[str] = set()
        if host_id:
            self.authorized_users.add(host_id)

        # Concurrency lock & monotonic state version (prevents queue/skip/playback race conditions)
        self.lock = asyncio.Lock()
        self.state_version = 0

        # Playback state
        self.current_track: Optional[Track] = None
        self.status = "idle"  # "playing", "paused", "idle"
        self.current_time = 0.0
        self.updated_at = time.time()

        # Queues
        self.queue: List[Track] = []
        self.history: List[Track] = []

        # Active users & 1-to-1 socket binding per user_id
        self.users: Dict[str, User] = {}
        self.user_sockets: Dict[str, WebSocket] = {}

        # Chat
        self.messages: List[Dict] = []

        # Ended reports for unknown-duration tracks (tied to current_track.uid)
        self.ended_reports: Set[str] = set()

        # Fair majority skip votes (>= 50% of active room members, Anti-Sybil)
        self.skip_votes: Set[str] = set()

        # Active song priority vote request (1 per room at a time, majority >= 50%)
        self.priority_request: Optional[Dict] = None

        # AI Auto-DJ mode (when True, "@Em bé floor 2" auto-queues a vibe-matched track when queue runs dry)
        self.auto_dj: bool = False
        self._auto_dj_busy: bool = False

        # AI Female Radio MC mode (Host-only toggle, default OFF)
        self.mc_enabled: bool = False

    def is_host(self, user_id: str) -> bool:
        return bool(user_id) and self.host_id == user_id

    def is_authorized(self, user_id: Optional[str], invite_token: Optional[str] = None) -> bool:
        if not self.is_private:
            return True
        if user_id and user_id in self.authorized_users:
            return True
        if invite_token and self.invite_token and hmac.compare_digest(invite_token, self.invite_token):
            if user_id:
                self.authorized_users.add(user_id)
            return True
        return False

    def count_connections_for_ip(self, client_ip: str) -> int:
        return sum(1 for u in self.users.values() if u.client_ip == client_ip)

    def deduplicate_name(self, desired_name: str, user_id: str) -> str:
        """Prevent visual identity spoofing by ensuring distinct active nicknames in a room."""
        base = sanitize_text(desired_name, max_len=25, fallback="Khách")
        existing = {
            u.name.lower() for uid, u in self.users.items() if uid != user_id
        }
        if base.lower() not in existing:
            return base
        for idx in range(2, 100):
            candidate = f"{base[:20]} ({idx})"
            if candidate.lower() not in existing:
                return candidate
        return f"{base[:18]}_{secrets.token_hex(2)}"

    def get_synced_time(self) -> float:
        if self.status == "playing":
            elapsed = max(0.0, time.time() - self.updated_at)
            if self.current_track and self.current_track.duration > 0:
                return min(self.current_time + elapsed, float(self.current_track.duration))
            return self.current_time + elapsed
        return self.current_time

    def catch_up_if_expired(self) -> bool:
        """Advance past already-finished tracks (e.g. after room was empty/restarted). Caller holds lock."""
        changed = False
        now = time.time()
        guard = 0
        while (
            self.status == "playing"
            and self.current_track
            and self.current_track.duration > 0
            and guard < 50
        ):
            elapsed = max(0.0, now - self.updated_at)
            pos = self.current_time + elapsed
            dur = float(self.current_track.duration)
            if pos < dur:
                break
            overflow = pos - dur
            finished = self.current_track.to_dict()
            next_t = self.advance_track()
            db.schedule_add_history(self.id, finished)
            if next_t:
                self.updated_at = now - overflow
                self.current_time = 0.0
            changed = True
            guard += 1
        if changed:
            db.schedule_save_room(self.to_db_record())
            db.schedule_replace_queue(self.id, [t.to_dict() for t in self.queue])
        return changed

    @staticmethod
    def _calc_majority_needed(total: int) -> int:
        if total <= 1:
            return 1
        return max(2, (total + 1) // 2)

    def get_skip_stats(self) -> Tuple[int, int, List[str]]:
        """Anti-Sybil majority skip vote calculation (>= 50% of active room members, min 2 when >= 2 members)."""
        active_voters = [uid for uid in self.skip_votes if uid in self.users]
        distinct_ips = {u.client_ip for u in self.users.values()}

        if len(distinct_ips) > 1:
            voter_ips = {self.users[uid].client_ip for uid in active_voters}
            count = len(voter_ips)
            needed = self._calc_majority_needed(len(distinct_ips))
        else:
            count = len(active_voters)
            needed = self._calc_majority_needed(len(self.users))

        return count, needed, active_voters

    def get_skip_state(self) -> Dict:
        count, needed, active_voters = self.get_skip_stats()
        return {
            "count": count,
            "needed": needed,
            "voted_users": active_voters,
        }

    def get_priority_stats(self) -> Tuple[int, int, List[str]]:
        """Anti-Sybil majority vote calculation (>= 50% of active room members, min 2 when >= 2 members)."""
        if not self.priority_request:
            return 0, self._calc_majority_needed(len(self.users)), []

        active_voters = [uid for uid in self.priority_request["votes"] if uid in self.users]
        distinct_ips = {u.client_ip for u in self.users.values()}

        if len(distinct_ips) > 1:
            # Multi-IP production room: 1 IP = max 1 vote to prevent Sybil tab stuffing
            voter_ips = {self.users[uid].client_ip for uid in active_voters}
            count = len(voter_ips)
            needed = self._calc_majority_needed(len(distinct_ips))
        else:
            # Single-IP room (local dev / same LAN): 1 authenticated session = 1 vote
            count = len(active_voters)
            needed = self._calc_majority_needed(len(self.users))

        return count, needed, active_voters

    def get_priority_state(self) -> Optional[Dict]:
        """Return valid active priority request snapshot (auto-clears if expired or target no longer eligible)."""
        req = self.priority_request
        if not req:
            return None

        if time.time() >= req["expires_at"]:
            self.priority_request = None
            return None

        idx = next((i for i, t in enumerate(self.queue) if t.uid == req["track_uid"]), -1)
        if idx <= 0:
            # Track was removed or is already at #1
            self.priority_request = None
            return None

        count, needed, active_voters = self.get_priority_stats()
        return {
            "track_uid": req["track_uid"],
            "track_id": req["track_id"],
            "track_title": req["track_title"],
            "track_thumbnail": req["track_thumbnail"],
            "requested_by_id": req["requested_by_id"],
            "requested_by_name": req["requested_by_name"],
            "count": count,
            "needed": needed,
            "voted_users": active_voters,
            "expires_at": req["expires_at"],
        }

    def promote_track_to_top(self, track_uid: str) -> Optional[Track]:
        """Atomically move queued track to position #1 (caller must hold self.lock)."""
        idx = next((i for i, t in enumerate(self.queue) if t.uid == track_uid), -1)
        if idx <= 0:
            return None
        target = self.queue.pop(idx)
        self.queue.insert(0, target)
        self.priority_request = None
        self.state_version += 1
        return target

    def to_dict(self, include_invite: bool = False):
        now = time.time()
        synced_time = self.get_synced_time()
        data = {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "host_id": self.host_id,
            "dj_mode": self.dj_mode,
            "auto_dj": self.auto_dj,
            "mc_enabled": self.mc_enabled,
            "is_private": self.is_private,
            "has_passcode": bool(self.passcode_hash),
            "status": self.status,
            "current_time": synced_time,
            # IMPORTANT: updated_at in serialized snapshot matches synced_time's instant (now)
            # so clients never double-count elapsed time on join!
            "updated_at": now,
            "server_time": now,
            "state_version": self.state_version,
            "current_track": self.current_track.to_dict() if self.current_track else None,
            "queue": [t.to_dict() for t in self.queue],
            "history": [t.to_dict() for t in self.history[-10:]],
            "users": [u.to_dict(self.host_id) for u in self.users.values()],
            "user_count": len(self.users),
            "skip_vote": self.get_skip_state(),
            "priority_request": self.get_priority_state(),
            "related_suggestions": (
                get_cached_related_videos(self.current_track.id, limit=14)
                if self.current_track
                else None
            ),
        }
        if include_invite and self.is_private:
            data["invite_token"] = self.invite_token
        return data

    def to_public_preview(self):
        """Safe preview for unauthenticated callers (no internal user/host IDs leaked)."""
        return {
            "id": self.id,
            "name": self.name,
            "user_count": len(self.users),
            "dj_mode": self.dj_mode,
            "is_private": self.is_private,
            "status": self.status,
            "current_track": self.current_track.to_dict() if self.current_track else None,
            "queue_count": len(self.queue),
        }

    async def broadcast(self, message: dict):
        """Broadcast message to all connected sockets in the room."""
        sockets_snapshot = list(self.user_sockets.items())
        dead_uids = []
        for uid, ws in sockets_snapshot:
            try:
                await ws.send_json(message)
            except Exception:
                dead_uids.append(uid)
        for uid in dead_uids:
            if self.user_sockets.get(uid) is not None:
                self.user_sockets.pop(uid, None)

    def to_db_record(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "host_id": self.host_id,
            "dj_mode": self.dj_mode,
            "auto_dj": self.auto_dj,
            "mc_enabled": self.mc_enabled,
            "is_private": self.is_private,
            "passcode_hash": self.passcode_hash,
            "invite_token": self.invite_token,
            "current_track": self.current_track.to_dict() if self.current_track else None,
            "status": self.status,
            "current_time": self.get_synced_time(),
            "updated_at": self.updated_at,
            "created_at": self.created_at,
        }

    def advance_track(self) -> Optional[Track]:
        """Atomically move to the next track in FIFO queue (caller must hold self.lock)."""
        if self.current_track:
            self.history.append(self.current_track)
            if len(self.history) > 30:
                self.history.pop(0)

        self.ended_reports.clear()
        self.skip_votes.clear()
        self.state_version += 1

        if self.queue:
            self.current_track = self.queue.pop(0)
            self.status = "playing"
            self.current_time = 0.0
            self.updated_at = time.time()
            return self.current_track
        else:
            self.current_track = None
            self.status = "idle"
            self.current_time = 0.0
            self.updated_at = time.time()
            return None


rooms: Dict[str, Room] = {}
rooms_lock = asyncio.Lock()

# Default public chill room (always available)
DEFAULT_ROOM_ID = "chill-zone"


def _track_from_db_row(row: Dict) -> Track:
    t = Track(
        track_id=row.get("video_id") or row.get("id") or "",
        title=row.get("title") or "",
        author=row.get("author") or "YouTube",
        duration=int(row.get("duration") or 0),
        duration_str=row.get("duration_str") or "",
        added_by_id=row.get("added_by_id") or "",
        added_by_name=row.get("added_by_name") or "Khách",
    )
    if row.get("uid"):
        t.uid = str(row["uid"])
    if row.get("added_at"):
        t.added_at = float(row["added_at"])
    return t


def hydrate_rooms_from_db():
    """Restore saved rooms, FIFO queues, listening history & chat messages from persistent DB."""
    rooms.clear()
    saved_rooms = db.load_all_rooms()
    for rec in saved_rooms:
        rm = Room(
            room_id=rec["id"],
            name=rec["name"],
            host_id=rec.get("host_id") or "",
            dj_mode=bool(rec.get("dj_mode")),
            is_private=bool(rec.get("is_private")),
            passcode_hash=rec.get("passcode_hash") or "",
        )
        if rec.get("invite_token"):
            rm.invite_token = rec["invite_token"]
        rm.created_at = float(rec.get("created_at") or time.time())
        rm.updated_at = float(rec.get("updated_at") or time.time())
        rm.status = rec.get("status") or "idle"
        rm.current_time = float(rec.get("current_time") or 0.0)

        ct = rec.get("current_track")
        if isinstance(ct, dict) and extract_video_id(ct.get("id", "")):
            rm.current_track = _track_from_db_row(ct)

        for q_row in rec.get("queue") or []:
            if extract_video_id(q_row.get("video_id", "")):
                rm.queue.append(_track_from_db_row(q_row))

        for h_row in rec.get("history") or []:
            if extract_video_id(h_row.get("video_id", "")):
                rm.history.append(_track_from_db_row(h_row))

        for m_row in rec.get("messages") or []:
            rm.messages.append({
                "id": str(m_row.get("id", ""))[:12],
                "type": m_row.get("msg_type") or "chat",
                "user_id": m_row.get("user_id") or "",
                "user_name": m_row.get("user_name") or "",
                "user_avatar": sanitize_avatar(m_row.get("user_avatar")),
                "user_color": sanitize_color(m_row.get("user_color")),
                "is_host": bool(m_row.get("is_host")),
                "text": m_row.get("text") or "",
                "media_type": m_row.get("media_type"),
                "media_url": m_row.get("media_url"),
                "timestamp": float(m_row.get("created_at") or time.time()),
            })

        rooms[rm.id] = rm

    if DEFAULT_ROOM_ID not in rooms:
        default_rm = Room(
            room_id=DEFAULT_ROOM_ID,
            name="☕ Quán Cà Phê Chill - Phòng Chung",
            host_id="",
            dj_mode=False,
            is_private=False,
        )
        rooms[DEFAULT_ROOM_ID] = default_rm
        db.save_room_sync(default_rm.to_db_record())


hydrate_rooms_from_db()


def _evict_stale_rooms():
    """Evict expired empty rooms only when RAM cap is close to MAX_ROOMS."""
    if len(rooms) < int(MAX_ROOMS * 0.85):
        return
    now = time.time()
    stale_ids = [
        rid for rid, r in rooms.items()
        if rid != DEFAULT_ROOM_ID and not r.users and r.last_empty_at and (now - r.last_empty_at) > EMPTY_ROOM_TTL_SEC
    ]
    for rid in stale_ids:
        rooms.pop(rid, None)


# ============================================================================
# REST APIS
# ============================================================================

class CreateRoomRequest(BaseModel):
    id: Optional[str] = Field(default=None, max_length=32)
    name: str = Field(default="", max_length=60)
    dj_mode: bool = False
    is_private: bool = False
    passcode: Optional[str] = Field(default=None, max_length=64)


class RoomAccessRequest(BaseModel):
    passcode: Optional[str] = Field(default=None, max_length=64)
    invite_token: Optional[str] = Field(default=None, max_length=128)


class AiDjRequest(BaseModel):
    prompt: str = Field(default="", max_length=240)
    count: int = Field(default=5, ge=1, le=8)


async def get_room_lyrics_excerpt(room: "Room") -> str:
    """Fetch cached (or quick 2.5s lookup) lyrics excerpt of current track for AI context."""
    ct = room.current_track
    if not ct:
        return ""
    cached = lyrics_cache.get(ct.id)
    if cached is None:
        try:
            cached = await asyncio.wait_for(
                get_synced_lyrics(title=ct.title, author=ct.author, duration=ct.duration, video_id=ct.id),
                timeout=2.5,
            )
        except Exception:
            cached = None
    return extract_lyrics_excerpt(cached)


_VN_NUM_WORD_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bmười\s+hai\b", re.IGNORECASE), "12"),
    (re.compile(r"\bmười\s+một\b", re.IGNORECASE), "11"),
    (re.compile(r"\bmười\b", re.IGNORECASE), "10"),
    (re.compile(r"\bchín\b", re.IGNORECASE), "9"),
    (re.compile(r"\btám\b", re.IGNORECASE), "8"),
    (re.compile(r"\bbảy\b", re.IGNORECASE), "7"),
    (re.compile(r"\bsáu\b", re.IGNORECASE), "6"),
    (re.compile(r"\b(tháng|thứ)\s+năm\b", re.IGNORECASE), r"\1 5"),
    (re.compile(r"\b(tháng|thứ)\s+tư\b", re.IGNORECASE), r"\1 4"),
    (re.compile(r"\bbốn\b", re.IGNORECASE), "4"),
    (re.compile(r"\b(tháng|thứ)\s+ba\b", re.IGNORECASE), r"\1 3"),
    (re.compile(r"\bhai\b", re.IGNORECASE), "2"),
    (re.compile(r"\bmột\b", re.IGNORECASE), "1"),
]

_NON_MUSIC_JUNK_KEYWORDS: Tuple[str, ...] = (
    "tu luyen",
    "chinh phap",
    "phap luan",
    "giang phap",
    "thuyet phap",
    "kinh phat",
    "tin tuc",
    "thoi su",
    "ban tin",
    "diem tin",
    "interlude",
    "teaser",
    "trailer",
    "reaction",
    "review phim",
    "tom tat phim",
    "podcast",
    "sach noi",
    "audiobook",
    "karaoke beat",
    "beat chuan",
    "nhac song dam cuoi",
    "1 hour loop",
    "10 hours",
)


def _normalize_vn_music_text(text: str) -> str:
    """Normalize Vietnamese song/artist text and convert accented number words ('sáu' -> '6') before stripping diacritics."""
    if not text:
        return ""
    s = text.strip().lower()
    for pat, repl in _VN_NUM_WORD_PATTERNS:
        s = pat.sub(repl, s)
    s = s.replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _prompt_wants_long_mix(prompt_norm: str) -> bool:
    return any(
        kw in prompt_norm
        for kw in ("lofi", "mix", "playlist", "tuyen tap", "lien khuc", "mashup", "1 gio", "1 tieng", "nonstop")
    )


def _prompt_wants_remix_or_cover(prompt_norm: str) -> bool:
    return any(kw in prompt_norm for kw in ("remix", "cover", "acoustic", "live", "karaoke", "sped up", "slowed"))


def _is_valid_music_track(
    item: Dict,
    *,
    allow_long_mix: bool = False,
    prompt_norm: str = "",
) -> bool:
    """Reject non-music sermons/news/podcasts, <85s interludes/shorts, and >10.5m compilations."""
    if not item or not item.get("id") or not item.get("title"):
        return False
    dur = int(item.get("duration") or 0)
    if dur > 0:
        if dur < 85:
            return False
        if not allow_long_mix and dur > 630:
            return False

    norm_all = _normalize_vn_music_text(f"{item.get('title', '')} {item.get('author', '')}")
    for junk in _NON_MUSIC_JUNK_KEYWORDS:
        if junk in norm_all and junk not in prompt_norm:
            return False
    return True


def _score_youtube_hit_for_song(
    hit: Dict,
    req_title: str,
    req_artist: str = "",
    *,
    allow_long_mix: bool = False,
    want_remix_or_cover: bool = False,
    prompt_norm: str = "",
) -> float:
    """Score how well a YouTube video matches a requested song title & artist, prioritizing official trending hits."""
    if not _is_valid_music_track(hit, allow_long_mix=allow_long_mix, prompt_norm=prompt_norm):
        return -999.0

    hit_title = _normalize_vn_music_text(hit.get("title", ""))
    hit_author = _normalize_vn_music_text(hit.get("author", ""))
    hit_combined = f"{hit_title} {hit_author}"
    t_norm = _normalize_vn_music_text(req_title)
    a_norm = _normalize_vn_music_text(req_artist)

    t_tokens = [w for w in t_norm.split() if len(w) > 1 or w.isdigit()]
    if not t_tokens:
        return -999.0

    hit_words = set(hit_combined.split())
    matched_t = sum(1 for w in t_tokens if w in hit_words or w in hit_title)
    title_ratio = matched_t / len(t_tokens)

    # Anti-hallucination guard: reject videos that do not match at least half of the song title words
    if title_ratio < 0.5:
        return -999.0

    score = title_ratio * 10.0
    if t_norm and t_norm in hit_title:
        score += 6.5

    if a_norm:
        a_tokens = [w for w in a_norm.split() if len(w) > 1]
        if a_tokens:
            matched_a = sum(1 for w in a_tokens if w in hit_words or w in hit_combined)
            if matched_a > 0:
                score += 5.0 * (matched_a / len(a_tokens))
            elif len(a_tokens) >= 2:
                score -= 2.5

    # Boost official artist channels & official MV / Lyric / Visualizer releases
    if any(k in hit_title or k in hit_author for k in ("official", "vevo", "topic")) or "mv" in hit_words:
        score += 4.5
    if a_norm and (hit_author == a_norm or hit_author == f"{a_norm} official"):
        score += 3.0

    # Penalize fan clips, TV talk/reaction clips, behind-the-scenes
    if any(k in hit_title for k in ("phan khich", "fan nu", "hau truong", "behind the scenes", "fancam", "phong van")):
        score -= 6.0

    if not want_remix_or_cover:
        if any(k in hit_title for k in ("remix", "cover", "karaoke", "sped up", "slowed", "nightcore", "8d", "nhac chuong")):
            score -= 4.5
        if "solo version" in hit_title or "khong co" in hit_title:
            score -= 2.5

    return score


async def _gather_ai_dj_youtube_candidates(
    room: "Room",
    vibe_prompt: str,
    exclude_ids: Set[str],
) -> Tuple[List[Dict], Optional[Dict]]:
    """Pre-fetch real trending YouTube tracks matching the user's query + related hits for grounded AI DJ selection."""
    clean_prompt = (vibe_prompt or "").strip()
    prompt_norm = _normalize_vn_music_text(clean_prompt)
    allow_long = _prompt_wants_long_mix(prompt_norm)
    want_remix = _prompt_wants_remix_or_cover(prompt_norm)

    candidates: List[Dict] = []
    seen_ids: Set[str] = set(exclude_ids)
    anchor_track: Optional[Dict] = None

    is_custom_query = bool(
        prompt_norm
        and not any(
            g in prompt_norm
            for g in (
                "hop gu phong",
                "ket hop gu cua tat ca thanh vien",
                "chill mua code",
                "ballad dem muon",
                "tuoi vui",
                "auto dj",
            )
        )
    )

    direct_hits: List[Dict] = []
    if clean_prompt:
        try:
            direct_hits = await search_youtube(clean_prompt[:90], limit=8)
        except Exception:
            direct_hits = []

    if is_custom_query and direct_hits:
        scored_direct = sorted(
            (
                (
                    _score_youtube_hit_for_song(
                        h,
                        clean_prompt,
                        "",
                        allow_long_mix=allow_long,
                        want_remix_or_cover=want_remix,
                        prompt_norm=prompt_norm,
                    ),
                    h,
                )
                for h in direct_hits
            ),
            key=lambda x: x[0],
            reverse=True,
        )
        if scored_direct and scored_direct[0][0] >= 11.5:
            best_h = dict(scored_direct[0][1])
            anchor_track = best_h

    # Gather related trending songs from YouTube's collaborative graph (anchor song + room's current song)
    related_tasks = []
    seed_ids: List[str] = []
    if anchor_track and anchor_track.get("id"):
        seed_ids.append(anchor_track["id"])
    elif direct_hits and _is_valid_music_track(direct_hits[0], allow_long_mix=allow_long, prompt_norm=prompt_norm):
        seed_ids.append(direct_hits[0]["id"])

    if room.current_track and room.current_track.id not in seed_ids:
        seed_ids.append(room.current_track.id)

    for sid in seed_ids[:2]:
        cached_rel = get_cached_related_videos(sid, limit=12)
        if cached_rel:
            for r in cached_rel:
                direct_hits.append(r)
        else:
            related_tasks.append(get_related_videos(sid, limit=12))

    if related_tasks:
        rel_lists = await asyncio.gather(*related_tasks, return_exceptions=True)
        for rl in rel_lists:
            if isinstance(rl, list):
                direct_hits.extend(rl)

    if anchor_track and anchor_track.get("id") not in seen_ids:
        seen_ids.add(anchor_track["id"])
        candidates.append(anchor_track)

    for h in direct_hits:
        vid = h.get("id")
        if not vid or vid in seen_ids:
            continue
        if not _is_valid_music_track(h, allow_long_mix=allow_long, prompt_norm=prompt_norm):
            continue
        if not want_remix and any(
            k in _normalize_vn_music_text(h.get("title", ""))
            for k in ("remix", "karaoke", "sped up", "slowed", "nhac chuong")
        ):
            continue
        seen_ids.add(vid)
        candidates.append(dict(h))
        if len(candidates) >= 16:
            break

    return candidates, anchor_track


async def _resolve_ai_song_suggestions(
    raw_songs: List[Dict[str, str]],
    exclude_ids: Set[str],
    limit: int = 6,
    *,
    vibe_prompt: str = "",
    prefetched_candidates: Optional[List[Dict]] = None,
    anchor_track: Optional[Dict] = None,
) -> List[Dict]:
    """Resolve AI {title, artist} pairs to verified, playable, trending YouTube music tracks in parallel."""
    prompt_norm = _normalize_vn_music_text(vibe_prompt)
    allow_long = _prompt_wants_long_mix(prompt_norm)
    want_remix = _prompt_wants_remix_or_cover(prompt_norm)
    candidate_pool = list(prefetched_candidates or [])

    async def _lookup_one(s: Dict[str, str]) -> Optional[Dict]:
        req_title = (s.get("title") or "").strip()
        req_artist = (s.get("artist") or "").strip()
        if not req_title:
            return None

        # 1. Check if any prefetched YouTube candidate already strongly matches this title + artist (0ms instant hit)
        best_cand: Optional[Dict] = None
        best_cand_score = -999.0
        for c in candidate_pool:
            cid = c.get("id")
            if not cid or cid in exclude_ids:
                continue
            sc = _score_youtube_hit_for_song(
                c,
                req_title,
                req_artist,
                allow_long_mix=allow_long,
                want_remix_or_cover=want_remix,
                prompt_norm=prompt_norm,
            )
            if sc > best_cand_score:
                best_cand_score = sc
                best_cand = c

        if best_cand is not None and best_cand_score >= 15.0:
            item = dict(best_cand)
            if s.get("reason"):
                item["ai_reason"] = s["reason"]
            return item

        # 2. Search YouTube and pick the highest-scoring verified official music video
        q = f"{req_title} {req_artist}".strip()
        try:
            hits = await search_youtube(q, limit=6)
            best_hit: Optional[Dict] = None
            best_score = -999.0
            for h in hits:
                vid = h.get("id")
                if not vid or vid in exclude_ids:
                    continue
                sc = _score_youtube_hit_for_song(
                    h,
                    req_title,
                    req_artist,
                    allow_long_mix=allow_long,
                    want_remix_or_cover=want_remix,
                    prompt_norm=prompt_norm,
                )
                if sc > best_score:
                    best_score = sc
                    best_hit = h

            if best_hit is not None and best_score >= 5.0:
                item = dict(best_hit)
                if s.get("reason"):
                    item["ai_reason"] = s["reason"]
                return item
        except Exception:
            pass
        return None

    results = await asyncio.gather(*(_lookup_one(s) for s in (raw_songs or [])[: max(limit + 2, 8)]))
    resolved: List[Dict] = []
    seen: Set[str] = set(exclude_ids)

    # Guarantee #1: If the user typed a song/artist query that directly matched an official anchor hit
    # (e.g. "mưa tháng 6" -> "VĂN MAI HƯƠNG - MƯA THÁNG SÁU"), pin it if not already present!
    for item in results:
        if item and item.get("id") and item["id"] not in seen:
            seen.add(item["id"])
            resolved.append(item)

    if anchor_track and anchor_track.get("id") and anchor_track["id"] not in exclude_ids:
        aid = anchor_track["id"]
        existing_idx = next((i for i, r in enumerate(resolved) if r.get("id") == aid), -1)
        if existing_idx > 0:
            pinned = resolved.pop(existing_idx)
            resolved.insert(0, pinned)
        elif existing_idx == -1:
            pinned = dict(anchor_track)
            pinned.setdefault("ai_reason", f"Bản hit gốc đình đám khớp ngay với '{vibe_prompt.strip()}'")
            resolved.insert(0, pinned)
            seen.add(aid)

    # Backfill from verified trending YouTube candidates if any LLM song was filtered out
    if len(resolved) < limit and candidate_pool:
        for c in candidate_pool:
            cid = c.get("id")
            if not cid or cid in seen:
                continue
            item = dict(c)
            item.setdefault("ai_reason", "Bản hit đang thịnh hành cùng tâm trạng & màu sắc âm nhạc")
            seen.add(cid)
            resolved.append(item)
            if len(resolved) >= limit:
                break

    return resolved[:limit]


async def _handle_ai_chat_reply(room: "Room", sender_name: str, user_text: str) -> None:
    """Background coroutine for '@Em bé floor 2' chat responses with full room & lyrics context."""
    try:
        await room.broadcast({"type": "ai_typing", "typing": True, "bot_name": AI_BOT_NAME})
        lyrics_excerpt = await get_room_lyrics_excerpt(room)
        ctx = build_room_ai_context(room, current_lyrics_excerpt=lyrics_excerpt)

        ai_out = await chat_with_em_be_floor_2(ctx, sender_name, user_text)
        reply_text = (ai_out.get("reply") or "").strip()
        if not reply_text:
            return

        exclude_ids: Set[str] = set()
        if room.current_track:
            exclude_ids.add(room.current_track.id)
        for t in room.queue:
            exclude_ids.add(t.id)

        suggested_tracks = await _resolve_ai_song_suggestions(
            ai_out.get("suggested_songs") or [],
            exclude_ids=exclude_ids,
            limit=3,
        )

        async with room.lock:
            ai_msg = {
                "id": secrets.token_hex(4),
                "type": "chat",
                "is_ai": True,
                "user_id": AI_BOT_ID,
                "user_name": AI_BOT_NAME,
                "user_avatar": AI_BOT_AVATAR,
                "user_color": AI_BOT_COLOR,
                "is_host": False,
                "text": reply_text,
                "media_type": None,
                "media_url": None,
                "suggested_tracks": suggested_tracks if suggested_tracks else None,
                "timestamp": time.time(),
            }
            room.messages.append(ai_msg)
            if len(room.messages) > 100:
                room.messages.pop(0)
            db.schedule_add_message(room.id, ai_msg)

        await room.broadcast({"type": "chat", "message": ai_msg})
    except Exception as exc:
        print(f"[ai chat reply err] {exc}")
    finally:
        try:
            await room.broadcast({"type": "ai_typing", "typing": False, "bot_name": AI_BOT_NAME})
        except Exception:
            pass


async def _run_auto_dj_for_room(room: "Room") -> None:
    """When Auto-DJ is ON and the room finishes its queue, automatically pick & play the next track."""
    if room._auto_dj_busy or not room.auto_dj:
        return
    room._auto_dj_busy = True
    try:
        ctx = build_room_ai_context(room)
        exclude_titles = [t.title for t in room.history[-12:]]
        exclude_ids = {t.id for t in room.history[-10:]}
        dj_res = await generate_ai_dj_playlist(
            ctx,
            vibe_prompt="Nối tiếp mạch cảm xúc và gu nhạc vừa phát của phòng (Auto-DJ)",
            count=2,
            exclude_titles=exclude_titles,
        )
        tracks = await _resolve_ai_song_suggestions(dj_res.get("songs") or [], exclude_ids=exclude_ids, limit=1)
        if not tracks:
            return
        picked = tracks[0]

        payload = None
        async with room.lock:
            if not room.auto_dj or room.current_track is not None or len(room.queue) > 0:
                return
            new_track = Track(
                track_id=picked["id"],
                title=picked.get("title") or f"Video {picked['id']}",
                author=picked.get("author") or "YouTube",
                duration=int(picked.get("duration") or 0),
                duration_str=picked.get("duration_str") or format_duration(int(picked.get("duration") or 0)),
                added_by_id=AI_BOT_ID,
                added_by_name=f"🎀 {AI_BOT_NAME}",
            )
            room.current_track = new_track
            room.status = "playing"
            room.current_time = 0.0
            room.updated_at = time.time()
            room.ended_reports.clear()
            room.skip_votes.clear()
            room.state_version += 1

            sys_msg = {
                "id": secrets.token_hex(4),
                "type": "system",
                "text": f"🎀 {AI_BOT_NAME} (Auto-DJ) đã tự động phát: {new_track.title}",
                "timestamp": time.time(),
            }
            room.messages.append(sys_msg)
            if len(room.messages) > 100:
                room.messages.pop(0)

            payload = {
                "type": "playback_changed",
                "current_track": room.current_track.to_dict(),
                "status": room.status,
                "current_time": 0.0,
                "updated_at": room.updated_at,
                "server_time": room.updated_at,
                "queue": [],
                "history": [t.to_dict() for t in room.history[-10:]],
                "skip_vote": room.get_skip_state(),
                "priority_request": room.get_priority_state(),
                "related_suggestions": get_cached_related_videos(room.current_track.id, limit=14),
                "message": sys_msg,
            }
            invalidate_rooms_cache()
            schedule_prewarm_room_related(room)
            db.schedule_save_room(room.to_db_record())
            db.schedule_add_message(room.id, sys_msg)

        if payload:
            await room.broadcast(payload)
    except Exception as exc:
        print(f"[auto-dj err] {exc}")
    finally:
        room._auto_dj_busy = False


def schedule_auto_dj_if_needed(room: "Room") -> None:
    if not room or not room.auto_dj or room.current_track is not None or len(room.queue) > 0:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_run_auto_dj_for_room(room))


@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check():
    return {
        "status": "ok",
        "rooms_count": len(rooms),
        "active_users": sum(len(r.users) for r in rooms.values()),
        "db": db.health_info(),
        "keep_alive": get_keep_alive_status(),
        "ddos_guard": ddos_guard.stats(),
        "ai": get_ai_stats(),
        "cache": {
            **get_youtube_cache_stats(),
            "gif_search": get_gif_cache_stats(),
            "sticker_search": get_sticker_cache_stats(),
            "lyrics": get_lyrics_cache_stats(),
            "public_rooms": public_rooms_cache.stats(),
            "room_suggestions": room_suggestions_cache.stats(),
            "chat_media": {
                "items": len(chat_media_store),
                "max_items": MAX_MEDIA_ITEMS,
                "bytes": chat_media_total_bytes,
            },
        },
    }


@app.post("/api/session")
async def create_or_resume_session(
    request: Request,
    x_session_token: Optional[str] = Header(default=None),
):
    """Issue or validate a server-authoritative session token."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"session:{ip}", limit=30, window_sec=60)

    existing = resolve_session(x_session_token)
    if existing:
        return {"token": existing.token, "user_id": existing.user_id}

    sess = session_manager.create_session()
    return {"token": sess.token, "user_id": sess.user_id}


@app.get("/api/rooms")
async def list_rooms():
    """List ONLY public rooms with short TTL caching (instant invalidation on mutations)."""
    cached = public_rooms_cache.get("list")
    if cached is not None:
        return {"rooms": cached}
    public_list = [r.to_public_preview() for r in rooms.values() if not r.is_private]
    public_rooms_cache.set("list", public_list)
    return {"rooms": public_list}


@app.post("/api/rooms")
async def create_room(
    req: CreateRoomRequest,
    request: Request,
    x_session_token: Optional[str] = Header(default=None),
):
    """Create a public or private room bound to the authenticated creator as initial Host."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"create_room:{ip}", limit=6, window_sec=60)

    sess = require_session(x_session_token)

    async with rooms_lock:
        _evict_stale_rooms()
        if len(rooms) >= MAX_ROOMS:
            raise HTTPException(status_code=503, detail="Hệ thống đang đạt giới hạn số phòng. Vui lòng thử lại sau.")

        raw_id = (req.id or secrets.token_hex(4)).strip().lower()
        room_id = re.sub(r"[^a-z0-9_-]", "-", raw_id).strip("-")[:32]
        if not room_id:
            room_id = secrets.token_hex(4)

        if room_id in rooms:
            existing = rooms[room_id]
            if existing.is_private and not existing.is_authorized(sess.user_id):
                raise HTTPException(status_code=409, detail="Mã phòng này đã tồn tại")
            return {
                "success": True,
                "room_id": room_id,
                "is_private": existing.is_private,
                "already_exists": True,
            }

        passcode_clean = (req.passcode or "").strip()
        if passcode_clean and len(passcode_clean) < 4:
            raise HTTPException(status_code=400, detail="Mật khẩu phòng cần tối thiểu 4 ký tự")

        is_private = bool(req.is_private or passcode_clean)
        passcode_hash = hash_passcode(passcode_clean) if passcode_clean else ""
        room_name = sanitize_text(req.name, max_len=60, fallback=f"Phòng #{room_id}")

        room = Room(
            room_id=room_id,
            name=room_name,
            host_id=sess.user_id,
            dj_mode=bool(req.dj_mode),
            is_private=is_private,
            passcode_hash=passcode_hash,
        )
        rooms[room_id] = room
        invalidate_rooms_cache()
        db.schedule_save_room(room.to_db_record())

        resp = {
            "success": True,
            "room_id": room_id,
            "is_private": room.is_private,
        }
        if room.is_private:
            resp["invite_token"] = room.invite_token
        return resp


@app.post("/api/rooms/{room_id}/access")
async def authorize_room_access(
    room_id: str,
    req: RoomAccessRequest,
    request: Request,
    x_session_token: Optional[str] = Header(default=None),
):
    """Verify passcode or invite token to grant the authenticated session access to a private room."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"room_access:{ip}:{room_id.lower()}", limit=10, window_sec=60)

    sess = require_session(x_session_token)
    clean_id = room_id.strip().lower()
    room = rooms.get(clean_id)
    if not room:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng")

    if not room.is_private:
        return {"authorized": True, "room_id": clean_id}

    async with room.lock:
        if room.is_authorized(sess.user_id, req.invite_token):
            return {"authorized": True, "room_id": clean_id}

        if room.passcode_hash and req.passcode and verify_passcode(req.passcode.strip(), room.passcode_hash):
            room.authorized_users.add(sess.user_id)
            return {"authorized": True, "room_id": clean_id}

    raise HTTPException(status_code=403, detail="Mật khẩu phòng hoặc liên kết mời không đúng")


@app.get("/api/rooms/{room_id}")
async def get_room_info(
    room_id: str,
    invite: Optional[str] = Query(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    """Get room snapshot with strict IDOR enforcement for private rooms."""
    clean_id = room_id.strip().lower()
    room = rooms.get(clean_id)
    if not room:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng")

    sess = resolve_session(x_session_token)
    user_id = sess.user_id if sess else None

    if room.is_private:
        if not room.is_authorized(user_id, invite):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Phòng riêng tư — Bạn cần mật khẩu hoặc liên kết mời để truy cập",
                    "is_private": True,
                    "has_passcode": bool(room.passcode_hash),
                },
            )
        return {"room": room.to_dict(include_invite=True)}

    # Unauthenticated callers get safe public preview without leaking internal IDs
    if not sess:
        return {"room": room.to_public_preview()}

    return {"room": room.to_dict()}


@app.get("/api/youtube/search")
async def api_search_youtube(request: Request, q: str = Query(..., min_length=1, max_length=150)):
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"yt_search:{ip}", limit=40, window_sec=60)
    results = await search_youtube(q, limit=8)
    return JSONResponse({"results": results}, headers={"Cache-Control": "public, max-age=120"})


@app.get("/api/youtube/info")
async def api_video_info(request: Request, url: str = Query(..., min_length=1, max_length=300)):
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"yt_info:{ip}", limit=30, window_sec=60)
    info = await get_video_info(url)
    if not info:
        raise HTTPException(status_code=400, detail="Không tìm thấy thông tin video từ đường dẫn này")
    return JSONResponse({"video": info}, headers={"Cache-Control": "public, max-age=600"})


@app.get("/api/youtube/related/{video_id}")
async def api_youtube_related(
    video_id: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=15),
):
    """Fetch YouTube Mix / related songs for the currently playing track."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"yt_related:{ip}", limit=45, window_sec=60)

    clean_vid = extract_video_id(video_id)
    if not clean_vid:
        raise HTTPException(status_code=400, detail="Mã video YouTube không hợp lệ")

    items = await get_related_videos(clean_vid, limit=limit)
    return JSONResponse(
        {"video_id": clean_vid, "suggestions": items},
        headers={"Cache-Control": "public, max-age=600"},
    )


@app.get("/api/youtube/audio/{video_id}")
async def api_audio_stream(video_id: str, request: Request):
    """Stream pure YouTube audio with HTTP Range support (for Radio fallback / embed-restricted tracks)."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"yt_audio:{ip}", limit=25, window_sec=60)

    clean_vid = extract_video_id(video_id)
    if not clean_vid:
        raise HTTPException(status_code=400, detail="Mã video YouTube không hợp lệ")

    stream_info = await get_audio_stream_url(clean_vid)
    if not stream_info:
        raise HTTPException(status_code=404, detail="Không thể trích xuất luồng âm thanh cho video này")

    stream_url, content_type = stream_info
    upstream_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    range_header = request.headers.get("range")
    if range_header and re.match(r"^bytes=\d*-\d*$", range_header.strip()):
        upstream_headers["Range"] = range_header.strip()

    client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    req = client.build_request("GET", stream_url, headers=upstream_headers)
    upstream_resp = await client.send(req, stream=True)

    if upstream_resp.status_code >= 400:
        await upstream_resp.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail="Luồng âm thanh tạm thời không khả dụng")

    resp_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    for h in ("content-length", "content-range"):
        if h in upstream_resp.headers:
            resp_headers[h.title()] = upstream_resp.headers[h]

    media_type = upstream_resp.headers.get("content-type", content_type)

    async def iter_chunks():
        try:
            async for chunk in upstream_resp.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(
        iter_chunks(),
        status_code=upstream_resp.status_code,
        media_type=media_type,
        headers=resp_headers,
    )


DEFAULT_CURATED_SUGGESTIONS: List[Dict] = [
    {
        "id": "FN7ALfpGxiI",
        "title": "NƠI NÀY CÓ ANH | SƠN TÙNG M-TP",
        "author": "Sơn Tùng M-TP Official",
        "thumbnail": "https://i.ytimg.com/vi/FN7ALfpGxiI/hqdefault.jpg",
        "duration": 278,
        "duration_str": "04:38",
        "tag": "V-Pop",
    },
    {
        "id": "AmduqflT79M",
        "title": "Yêu Một Người Có Lẽ - Lou Hoàng - Miu Lê",
        "author": "Miu Lê Official",
        "thumbnail": "https://i.ytimg.com/vi/AmduqflT79M/hqdefault.jpg",
        "duration": 265,
        "duration_str": "04:25",
        "tag": "V-Pop R&B",
    },
    {
        "id": "_E-7A81Ac8U",
        "title": "Cause I Love You - Noo Phước Thịnh",
        "author": "Noo Phước Thịnh",
        "thumbnail": "https://i.ytimg.com/vi/_E-7A81Ac8U/hqdefault.jpg",
        "duration": 374,
        "duration_str": "06:14",
        "tag": "Dance Pop",
    },
    {
        "id": "m7Wya6Z-QdM",
        "title": "Từng Là, Giữa Đại Lộ Đông Tây, Thu Cuối - Lofi Chill Nhẹ Nhàng",
        "author": "Soul Tracks",
        "thumbnail": "https://i.ytimg.com/vi/m7Wya6Z-QdM/hqdefault.jpg",
        "duration": 5554,
        "duration_str": "1:32:34",
        "tag": "Lofi Chill",
    },
    {
        "id": "JGwWNGJdvx8",
        "title": "Ed Sheeran - Shape of You (Official Music Video)",
        "author": "Ed Sheeran",
        "thumbnail": "https://i.ytimg.com/vi/JGwWNGJdvx8/hqdefault.jpg",
        "duration": 264,
        "duration_str": "04:24",
        "tag": "UK Pop",
    },
    {
        "id": "Pkh8UtuejGw",
        "title": "Shawn Mendes, Camila Cabello - Señorita",
        "author": "Shawn Mendes",
        "thumbnail": "https://i.ytimg.com/vi/Pkh8UtuejGw/hqdefault.jpg",
        "duration": 206,
        "duration_str": "03:26",
        "tag": "Pop Duet",
    },
    {
        "id": "papuvlVeZg8",
        "title": "Clean Bandit - Rockabye (feat. Sean Paul & Anne-Marie)",
        "author": "Clean Bandit",
        "thumbnail": "https://i.ytimg.com/vi/papuvlVeZg8/hqdefault.jpg",
        "duration": 254,
        "duration_str": "04:14",
        "tag": "Electronic",
    },
    {
        "id": "3AtDnEC4zak",
        "title": "Charlie Puth - We Don't Talk Anymore (feat. Selena Gomez)",
        "author": "Charlie Puth",
        "thumbnail": "https://i.ytimg.com/vi/3AtDnEC4zak/hqdefault.jpg",
        "duration": 230,
        "duration_str": "03:50",
        "tag": "Pop",
    },
    {
        "id": "kJQP7kiw5Fk",
        "title": "Luis Fonsi - Despacito ft. Daddy Yankee",
        "author": "Luis Fonsi",
        "thumbnail": "https://i.ytimg.com/vi/kJQP7kiw5Fk/hqdefault.jpg",
        "duration": 282,
        "duration_str": "04:42",
        "tag": "Latin Pop",
    },
    {
        "id": "6EEW-9NDM5k",
        "title": "Akon - Lonely (Official Music Video)",
        "author": "Akon",
        "thumbnail": "https://i.ytimg.com/vi/6EEW-9NDM5k/hqdefault.jpg",
        "duration": 265,
        "duration_str": "04:25",
        "tag": "Classic R&B",
    },
]


@app.get("/api/suggestions")
async def get_suggestions():
    return JSONResponse(
        {"suggestions": DEFAULT_CURATED_SUGGESTIONS},
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/rooms/{room_id}/suggestions")
async def get_room_personalized_suggestions(
    room_id: str,
    request: Request,
    limit: int = Query(default=10, ge=4, le=15),
    invite: Optional[str] = Query(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    """Build diverse quick suggestions tailored to the room's listening history (max 2 per artist)."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"room_sug:{ip}", limit=40, window_sec=60)

    clean_id = room_id.strip().lower()
    room = rooms.get(clean_id)
    if not room:
        return JSONResponse(
            {"mode": "curated", "suggestions": DEFAULT_CURATED_SUGGESTIONS[:limit]},
            headers={"Cache-Control": "public, max-age=60"},
        )

    # Protect private room history from unauthorized callers (IDOR-safe)
    if room.is_private:
        sess = resolve_session(x_session_token)
        uid = sess.user_id if sess else None
        if not room.is_authorized(uid, invite):
            return JSONResponse(
                {"mode": "curated", "suggestions": DEFAULT_CURATED_SUGGESTIONS[:limit]},
                headers={"Cache-Control": "no-store"},
            )

    # Gather diverse seed tracks from listening history + current track + queue
    raw_pool: List[Track] = []
    if room.current_track:
        raw_pool.append(room.current_track)
    raw_pool.extend(reversed(room.history[-15:]))
    raw_pool.extend(room.queue[:5])

    seeds: List[Track] = []
    seen_seed_ids: Set[str] = set()
    seen_seed_authors: Set[str] = set()

    # Pass 1: pick up to 3 seeds with DISTINCT artists for maximum variety
    for t in raw_pool:
        if t.id in seen_seed_ids:
            continue
        author_key = (t.author or "").strip().lower()
        if author_key and author_key in seen_seed_authors:
            continue
        seeds.append(t)
        seen_seed_ids.add(t.id)
        if author_key:
            seen_seed_authors.add(author_key)
        if len(seeds) >= 3:
            break

    # Pass 2: if room only listened to 1 artist so far, add a second distinct song seed
    if len(seeds) < 2:
        for t in raw_pool:
            if t.id not in seen_seed_ids:
                seeds.append(t)
                seen_seed_ids.add(t.id)
                if len(seeds) >= 2:
                    break

    if not seeds:
        return JSONResponse(
            {"mode": "curated", "suggestions": DEFAULT_CURATED_SUGGESTIONS[:limit]},
            headers={"Cache-Control": "private, max-age=30"},
        )

    exclude_ids: Set[str] = set()
    if room.current_track:
        exclude_ids.add(room.current_track.id)
    for t in room.queue:
        exclude_ids.add(t.id)
    recent_history_ids = {t.id for t in room.history[-6:]}

    cache_key = f"{clean_id}:{'-'.join(s.id for s in seeds)}:{'-'.join(sorted(exclude_ids))}:{limit}"
    cached = room_suggestions_cache.get(cache_key)
    if cached is not None:
        return JSONResponse(cached, headers={"Cache-Control": "private, max-age=30"})

    related_lists = await asyncio.gather(
        *(get_related_videos(s.id, limit=8) for s in seeds),
        return_exceptions=True,
    )
    clean_lists: List[List[Dict]] = [
        lst for lst in related_lists if isinstance(lst, list) and lst
    ]

    merged: List[Dict] = []
    seen_ids: Set[str] = set(exclude_ids)
    author_counts: Dict[str, int] = {}

    def _try_add(item: Dict, max_per_artist: int = 2, skip_recent_hist: bool = True) -> bool:
        vid = item.get("id")
        if not vid or vid in seen_ids:
            return False
        if skip_recent_hist and vid in recent_history_ids:
            return False
        a_key = (item.get("author") or "youtube").strip().lower()
        if author_counts.get(a_key, 0) >= max_per_artist:
            return False
        seen_ids.add(vid)
        author_counts[a_key] = author_counts.get(a_key, 0) + 1
        merged.append(item)
        return True

    # Interleave round-robin across all diverse seeds so no single artist dominates!
    max_len = max((len(lst) for lst in clean_lists), default=0)
    for idx in range(max_len):
        for lst in clean_lists:
            if idx < len(lst):
                _try_add(lst[idx], max_per_artist=2, skip_recent_hist=True)
                if len(merged) >= limit:
                    break
        if len(merged) >= limit:
            break

    # Pad remaining slots with diverse curated multi-genre tracks (max 1 per artist)
    if len(merged) < limit:
        for item in DEFAULT_CURATED_SUGGESTIONS:
            _try_add(item, max_per_artist=1, skip_recent_hist=False)
            if len(merged) >= limit:
                break

    payload = {
        "mode": "room_history" if clean_lists else "curated",
        "suggestions": merged[:limit],
    }
    room_suggestions_cache.set(cache_key, payload)
    return JSONResponse(payload, headers={"Cache-Control": "private, max-age=30"})


@app.get("/api/gifs/search")
async def api_search_gifs(request: Request, q: str = Query(default="", max_length=80)):
    """Search Giphy animated GIFs (or return instant curated GIFs when q is empty)."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"gif_search:{ip}", limit=35, window_sec=60)
    if not q.strip():
        return JSONResponse({"gifs": get_curated_gifs()}, headers={"Cache-Control": "public, max-age=600"})
    results = await search_gifs(q, limit=24)
    return JSONResponse({"gifs": results}, headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/stickers/search")
async def api_search_stickers(request: Request, q: str = Query(default="", max_length=80)):
    """Search Giphy animated Stickers (or return instant curated Stickers when q is empty)."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"sticker_search:{ip}", limit=35, window_sec=60)
    if not q.strip():
        return JSONResponse({"stickers": get_curated_stickers()}, headers={"Cache-Control": "public, max-age=600"})
    results = await search_stickers(q, limit=24)
    return JSONResponse({"stickers": results}, headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/lyrics")
async def api_get_lyrics(
    request: Request,
    title: str = Query(..., min_length=1, max_length=200),
    author: str = Query(default="", max_length=120),
    duration: int = Query(default=0, ge=0, le=86400),
    video_id: str = Query(default="", max_length=20),
):
    """Fetch synced LRC (or plain) lyrics for the currently playing track."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"lyrics:{ip}", limit=45, window_sec=60)

    clean_vid = extract_video_id(video_id) or ""
    result = await get_synced_lyrics(title=title, author=author, duration=duration, video_id=clean_vid)
    return JSONResponse(result, headers={"Cache-Control": "public, max-age=3600"})


# ============================================================================
# AI ENDPOINTS ("@Em bé floor 2" — AI DJ, Room Vibe Card, Female Radio MC TTS)
# ============================================================================

def _require_room_ai_access(
    room_id: str,
    x_session_token: Optional[str],
    invite: Optional[str] = None,
) -> Tuple["Room", Session]:
    sess = require_session(x_session_token)
    clean_id = room_id.strip().lower()
    room = rooms.get(clean_id)
    if not room:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng nhạc")
    if not room.is_authorized(sess.user_id, invite):
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập phòng riêng tư này")
    return room, sess


def _require_room_read_ai_access(
    room_id: str,
    request: Request,
    x_session_token: Optional[str],
    invite: Optional[str] = None,
) -> Tuple["Room", str]:
    """Read-only AI access (Vibe & MC Intro): enforces private room auth while surviving dev server reloads on public rooms."""
    clean_id = room_id.strip().lower()
    room = rooms.get(clean_id)
    if not room:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng nhạc")
    sess = resolve_session(x_session_token)
    if room.is_private:
        if not sess:
            raise HTTPException(status_code=401, detail="Phiên làm việc không hợp lệ hoặc đã hết hạn")
        if not room.is_authorized(sess.user_id, invite):
            raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập phòng riêng tư này")
        return room, sess.user_id
    rl_key = sess.user_id if sess else get_client_ip(request.headers, request.client)
    return room, rl_key


@app.post("/api/rooms/{room_id}/ai/dj")
async def api_room_ai_dj(
    room_id: str,
    req: AiDjRequest,
    invite: Optional[str] = Query(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    """AI DJ playlist generator tailored to natural-language vibe + live room taste."""
    room, sess = _require_room_ai_access(room_id, x_session_token, invite)
    require_rate_limit(f"ai_dj:{sess.user_id}", limit=6, window_sec=60)

    if room.dj_mode and not room.is_host(sess.user_id):
        raise HTTPException(
            status_code=403,
            detail="Chế độ DJ đang bật: Chỉ Chủ phòng (Host) mới được dùng AI DJ thêm nhạc",
        )

    lyrics_excerpt = await get_room_lyrics_excerpt(room)
    ctx = build_room_ai_context(room, current_lyrics_excerpt=lyrics_excerpt)

    exclude_titles: List[str] = []
    exclude_ids: Set[str] = set()
    if room.current_track:
        exclude_titles.append(room.current_track.title)
        exclude_ids.add(room.current_track.id)
    for t in room.queue:
        exclude_titles.append(t.title)
        exclude_ids.add(t.id)
    for t in room.history[-10:]:
        exclude_titles.append(t.title)
        exclude_ids.add(t.id)

    clean_prompt = sanitize_text(req.prompt, max_len=220)
    yt_candidates, anchor_track = await _gather_ai_dj_youtube_candidates(
        room,
        vibe_prompt=clean_prompt,
        exclude_ids=exclude_ids,
    )

    dj_res = await generate_ai_dj_playlist(
        ctx,
        vibe_prompt=clean_prompt,
        count=req.count,
        exclude_titles=exclude_titles,
        youtube_candidates=yt_candidates,
    )
    tracks = await _resolve_ai_song_suggestions(
        dj_res.get("songs") or [],
        exclude_ids=exclude_ids,
        limit=req.count,
        vibe_prompt=clean_prompt,
        prefetched_candidates=yt_candidates,
        anchor_track=anchor_track,
    )
    return JSONResponse(
        {
            "bot_name": AI_BOT_NAME,
            "vibe_note": dj_res.get("vibe_note") or "",
            "tracks": tracks,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/rooms/{room_id}/ai/vibe")
async def api_room_ai_vibe(
    room_id: str,
    request: Request,
    invite: Optional[str] = Query(default=None),
    x_session_token: Optional[str] = Header(default=None),
):
    """Room Vibe Card: analyze the room's listening history, playing song & members."""
    room, rl_key = _require_room_read_ai_access(room_id, request, x_session_token, invite)
    require_rate_limit(f"ai_vibe:{rl_key}", limit=20, window_sec=60)

    cur_id = room.current_track.id if room.current_track else "idle"
    q_sig = "-".join(t.id for t in room.queue[:3])
    h_sig = "-".join(t.id for t in room.history[-5:])
    cache_key = f"{room.id}:{cur_id}:{q_sig}:{h_sig}:{len(room.users)}"

    lyrics_excerpt = await get_room_lyrics_excerpt(room)
    ctx = build_room_ai_context(room, current_lyrics_excerpt=lyrics_excerpt)
    vibe = await analyze_room_vibe(ctx, cache_key=cache_key)
    return JSONResponse(vibe, headers={"Cache-Control": "private, max-age=45"})


@app.get("/api/rooms/{room_id}/ai/mc-intro")
async def api_room_ai_mc_intro(
    room_id: str,
    request: Request,
    invite: Optional[str] = Query(default=None),
    track_uid: Optional[str] = Query(default=None, max_length=64),
    x_session_token: Optional[str] = Header(default=None),
):
    """Generate & return cached expressive female Vietnamese Radio MC intro for the current track."""
    room, rl_key = _require_room_read_ai_access(room_id, request, x_session_token, invite)
    require_rate_limit(f"ai_mc:{rl_key}", limit=25, window_sec=60)
    no_store = {"Cache-Control": "no-store"}

    if not room.mc_enabled or not room.current_track:
        return JSONResponse({"found": False, "script": "", "audio_url": None}, headers=no_store)

    track = room.current_track
    # Client asked for a track that is no longer playing (fast skip) -> don't introduce the wrong song
    if track_uid and track_uid not in (track.uid, track.id):
        return JSONResponse({"found": False, "stale": True, "script": "", "audio_url": None}, headers=no_store)

    intro = await get_or_create_mc_intro(room.id, track.to_dict(), build_mc_context(room, track))
    # Track may have changed while the intro was being generated
    if room.current_track is not track:
        return JSONResponse({"found": False, "stale": True, "script": "", "audio_url": None}, headers=no_store)
    return JSONResponse({"found": True, **intro}, headers=no_store)


@app.get("/api/ai/mc-audio/{audio_id}")
async def api_serve_mc_audio(audio_id: str, request: Request):
    """Serve cached Vietnamese female Radio MC MP3 audio."""
    ip = get_client_ip(request.headers, request.client)
    require_rate_limit(f"mc_audio:{ip}", limit=30, window_sec=60)
    if not re.match(r"^[a-f0-9]{16}$", audio_id or ""):
        raise HTTPException(status_code=404, detail="Mã âm thanh MC không hợp lệ")
    audio_bytes = mc_audio_cache.get(audio_id)
    if not audio_bytes:
        raise HTTPException(status_code=404, detail="Bản thu âm MC đã hết hạn")
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=1800"},
    )


@app.post("/api/chat/upload")
async def api_upload_chat_image(
    request: Request,
    x_session_token: Optional[str] = Header(default=None),
):
    """Upload a verified safe image (PNG/JPEG/GIF/WebP <= 2.5MB) for realtime chat."""
    sess = require_session(x_session_token)
    require_rate_limit(f"chat_upload:{sess.user_id}", limit=8, window_sec=60)

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Không có dữ liệu hình ảnh")
    if len(raw) > MAX_UPLOAD_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Hình ảnh quá lớn (tối đa 2.5 MB)")

    mime = detect_safe_image_mime(raw)
    if not mime:
        raise HTTPException(
            status_code=400,
            detail="Chỉ hỗ trợ định dạng hình ảnh an toàn (PNG, JPG, GIF, WebP)",
        )

    media_id = store_chat_media(raw, mime, sess.user_id)
    return {
        "media_id": media_id,
        "url": f"/api/chat/media/{media_id}",
        "mime": mime,
    }


@app.get("/api/chat/media/{media_id}")
async def api_get_chat_media(media_id: str):
    """Serve an uploaded chat image (RAM cache with persistent DB fallback)."""
    clean_id = sanitize_media_id(media_id)
    if not clean_id:
        raise HTTPException(status_code=404, detail="Hình ảnh không tồn tại hoặc đã hết hạn")

    if clean_id in chat_media_store:
        raw, mime, _, _ = chat_media_store[clean_id]
        chat_media_store.move_to_end(clean_id)
    else:
        loop = asyncio.get_running_loop()
        loaded = await loop.run_in_executor(None, db.get_media_sync, clean_id)
        if not loaded:
            raise HTTPException(status_code=404, detail="Hình ảnh không tồn tại hoặc đã hết hạn")
        raw, mime, uploader_id = loaded
        chat_media_store[clean_id] = (raw, mime, uploader_id, time.time())

    return Response(
        content=raw,
        media_type=mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400, immutable",
        },
    )


# ============================================================================
# WEBSOCKET REALTIME SYNC & CHAT (AUTHENTICATED, AUTHORIZED, LOCKED, RATE-LIMITED)
# ============================================================================

@app.websocket("/ws/{room_id}")
async def websocket_room_endpoint(
    websocket: WebSocket,
    room_id: str,
    token: Optional[str] = Query(default=None),
    invite: Optional[str] = Query(default=None),
):
    client_ip = get_client_ip(websocket.headers, websocket.client)

    # 0. L7 Anti-DDoS pre-auth check (banned IP & WS handshake flood)
    ws_pre_ok, ws_pre_reason = ddos_guard.check_ws_pre_auth(client_ip)
    if not ws_pre_ok:
        await websocket.close(code=4029, reason=ws_pre_reason)
        return

    # 1. Anti-CSWSH Origin check
    if not is_valid_ws_origin(websocket):
        await websocket.close(code=4003, reason="Origin không được phép")
        return

    # 2. Mandatory server-side session authentication (prevents Identity Spoofing & Host Hijack)
    sess = session_manager.get_session(token)
    if not sess:
        await websocket.close(code=4001, reason="Phiên đăng nhập không hợp lệ")
        return

    user_id = sess.user_id
    clean_id = room_id.strip().lower()

    # 3. Room existence & Private Room Authorization (prevents IDOR & unauthorized WS room creation)
    room = rooms.get(clean_id)
    if not room:
        await websocket.close(code=4004, reason="Phòng không tồn tại")
        return

    async with room.lock:
        if not room.is_authorized(user_id, invite):
            await websocket.close(code=4003, reason="Bạn không có quyền vào phòng riêng tư này")
            return

        # Anti-Sybil connection cap per IP in the same room
        already_in_room = user_id in room.users
        if not already_in_room and room.count_connections_for_ip(client_ip) >= MAX_CONNS_PER_IP_PER_ROOM:
            await websocket.close(code=4029, reason="Quá nhiều kết nối đồng thời từ cùng địa chỉ IP")
            return

    # 4. Global cross-room WebSocket connection cap per IP / server
    ws_slot_ok, ws_slot_reason = ddos_guard.acquire_ws_slot(client_ip)
    if not ws_slot_ok:
        await websocket.close(code=4029, reason=ws_slot_reason)
        return

    await websocket.accept()
    joined = False
    current_user: Optional[User] = None

    try:
        while True:
            raw_text = await websocket.receive_text()
            if len(raw_text.encode("utf-8")) > MAX_WS_FRAME_BYTES:
                await websocket.send_json({"type": "error", "message": "Dữ liệu gửi lên vượt quá kích thước cho phép"})
                continue

            try:
                import json
                data = json.loads(raw_text)
            except Exception:
                await websocket.send_json({"type": "error", "message": "Định dạng dữ liệu không hợp lệ"})
                continue

            if not isinstance(data, dict):
                continue

            action = data.get("action")

            # Enforce join handshake before any other action
            if not joined and action != "join":
                await websocket.send_json({"type": "error", "message": "Vui lòng hoàn tất tham gia phòng trước"})
                continue

            # ----------------------------------------------------------------
            # 1. JOIN ROOM
            # ----------------------------------------------------------------
            if action == "join":
                async with room.lock:
                    # Replace stale socket if same authenticated user reconnects
                    old_ws = room.user_sockets.get(user_id)
                    if old_ws and old_ws is not websocket:
                        try:
                            await old_ws.close(code=4000, reason="Đã mở kết nối mới")
                        except Exception:
                            pass

                    # Assign host ONLY if room has no active host (creator keeps host!)
                    if not room.host_id or (room.host_id != user_id and room.host_id not in room.users):
                        # Only transfer away from absent creator if room already had users or creator never joined within 60s
                        if not room.host_id or (time.time() - room.created_at) > 60 or room.host_id not in room.authorized_users:
                            room.host_id = user_id

                    safe_name = room.deduplicate_name(data.get("name") or "Khách", user_id)
                    current_user = User(
                        user_id=user_id,
                        name=safe_name,
                        avatar=data.get("avatar"),
                        color=data.get("color"),
                        client_ip=client_ip,
                    )
                    room.users[user_id] = current_user
                    room.user_sockets[user_id] = websocket
                    room.last_empty_at = None
                    joined = True
                    room.catch_up_if_expired()

                    is_host = room.is_host(user_id)
                    init_payload = {
                        "type": "init_state",
                        "room": room.to_dict(include_invite=room.is_private),
                        "messages": room.messages[-50:],
                        "your_user_id": user_id,
                        "is_host": is_host,
                    }
                    join_msg = {
                        "id": secrets.token_hex(4),
                        "type": "system",
                        "text": f"👋 {current_user.name} đã vào phòng",
                        "timestamp": time.time(),
                    }
                    room.messages.append(join_msg)
                    if len(room.messages) > 100:
                        room.messages.pop(0)

                    users_snapshot = [u.to_dict(room.host_id) for u in room.users.values()]
                    host_snapshot = room.host_id
                    skip_snapshot = room.get_skip_state()
                    priority_snapshot = room.get_priority_state()
                    invalidate_rooms_cache()
                    schedule_prewarm_room_related(room)

                await websocket.send_json(init_payload)
                await room.broadcast({
                    "type": "user_joined",
                    "user": current_user.to_dict(host_snapshot),
                    "users": users_snapshot,
                    "message": join_msg,
                    "host_id": host_snapshot,
                    "skip_vote": skip_snapshot,
                    "priority_request": priority_snapshot,
                })

            # ----------------------------------------------------------------
            # 2. CHAT MESSAGE — TEXT, GIF & IMAGE (RATE-LIMITED & SANITIZED)
            # ----------------------------------------------------------------
            elif action == "chat":
                allowed, _ = rate_limiter.allow(f"ws_chat:{user_id}", limit=6, window_sec=5)
                if not allowed:
                    await websocket.send_json({"type": "error", "message": "Bạn đang nhắn quá nhanh, vui lòng chậm lại!"})
                    continue

                if not current_user:
                    continue

                text = sanitize_text(data.get("text"), max_len=400)
                media_type = data.get("media_type")
                media_url = None
                verified_media_type = None

                if media_type in ("gif", "sticker"):
                    gid = sanitize_giphy_id(data.get("gif_id"))
                    if not gid:
                        await websocket.send_json({"type": "error", "message": "Mã nhãn dán / ảnh GIF không hợp lệ"})
                        continue
                    verified_media_type = media_type
                    media_url = f"https://media.giphy.com/media/{gid}/giphy.gif"
                elif media_type == "image":
                    mid = sanitize_media_id(data.get("media_id"))
                    entry = chat_media_store.get(mid) if mid else None
                    if not entry or entry[2] != user_id:
                        await websocket.send_json({"type": "error", "message": "Hình ảnh không hợp lệ hoặc không thuộc phiên của bạn"})
                        continue
                    verified_media_type = "image"
                    media_url = f"/api/chat/media/{mid}"

                if not text and not media_url:
                    continue

                async with room.lock:
                    msg = {
                        "id": secrets.token_hex(4),
                        "type": "chat",
                        "user_id": current_user.user_id,
                        "user_name": current_user.name,
                        "user_avatar": current_user.avatar,
                        "user_color": current_user.color,
                        "is_host": room.is_host(user_id),
                        "text": text,
                        "media_type": verified_media_type,
                        "media_url": media_url,
                        "timestamp": time.time(),
                    }
                    room.messages.append(msg)
                    if len(room.messages) > 100:
                        room.messages.pop(0)
                    db.schedule_add_message(room.id, msg)

                await room.broadcast({"type": "chat", "message": msg})

                # Trigger "@Em bé floor 2" conversational AI reply when tagged in chat!
                if text and is_ai_mention(text):
                    ai_ok, _ = rate_limiter.allow(f"ai_chat:{user_id}", limit=8, window_sec=60)
                    if ai_ok:
                        asyncio.create_task(_handle_ai_chat_reply(room, current_user.name, text))
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Bạn gọi @{AI_BOT_NAME} hơi nhanh, chờ vài giây để Em bé thở xíu nhé!",
                        })

            # ----------------------------------------------------------------
            # 3. EMOJI REACTION (ALLOWLISTED & RATE-LIMITED)
            # ----------------------------------------------------------------
            elif action == "reaction":
                allowed, _ = rate_limiter.allow(f"ws_react:{user_id}", limit=8, window_sec=5)
                if not allowed:
                    continue

                emoji = sanitize_reaction(data.get("emoji"))
                if emoji and current_user:
                    await room.broadcast({
                        "type": "reaction",
                        "emoji": emoji,
                        "user_name": current_user.name,
                    })

            # ----------------------------------------------------------------
            # 4. ADD TRACK (LOCKED, RATE-LIMITED, QUOTA-CAPPED, SANITIZED)
            # ----------------------------------------------------------------
            elif action == "add_track":
                allowed, _ = rate_limiter.allow(f"ws_add:{user_id}", limit=5, window_sec=15)
                if not allowed:
                    await websocket.send_json({"type": "error", "message": "Bạn thêm nhạc quá nhanh, chờ vài giây nhé!"})
                    continue

                track_data = data.get("track")
                if not isinstance(track_data, dict) or not current_user:
                    continue

                v_id = extract_video_id(str(track_data.get("id") or track_data.get("url") or ""))
                if not v_id:
                    await websocket.send_json({"type": "error", "message": "Mã video YouTube không hợp lệ"})
                    continue

                try:
                    duration = int(track_data.get("duration") or 0)
                except (TypeError, ValueError):
                    duration = 0

                new_track = Track(
                    track_id=v_id,
                    title=track_data.get("title") or f"Video {v_id}",
                    author=track_data.get("author") or "YouTube",
                    duration=duration,
                    duration_str=track_data.get("duration_str") or format_duration(duration),
                    added_by_id=user_id,
                    added_by_name=current_user.name,
                )

                async with room.lock:
                    if room.dj_mode and not room.is_host(user_id):
                        await websocket.send_json({
                            "type": "error",
                            "message": "Chế độ DJ đang bật: Chỉ Chủ phòng (Host) mới được thêm bài hát",
                        })
                        continue
                    if room.current_track is not None:
                        if len(room.queue) >= MAX_QUEUE_SIZE:
                            await websocket.send_json({"type": "error", "message": f"Hàng đợi đã đầy (tối đa {MAX_QUEUE_SIZE} bài)"})
                            continue
                        user_q_count = sum(1 for t in room.queue if t.added_by_id == user_id)
                        if not room.is_host(user_id) and user_q_count >= MAX_USER_QUEUE_ITEMS:
                            await websocket.send_json({"type": "error", "message": f"Mỗi thành viên chỉ được xếp tối đa {MAX_USER_QUEUE_ITEMS} bài trong hàng đợi"})
                            continue

                    room.state_version += 1
                    if room.current_track is None:
                        room.current_track = new_track
                        room.status = "playing"
                        room.current_time = 0.0
                        room.updated_at = time.time()
                        room.ended_reports.clear()
                        room.skip_votes.clear()

                        sys_msg = {
                            "id": secrets.token_hex(4),
                            "type": "system",
                            "text": f"🎶 {current_user.name} đã phát: {new_track.title}",
                            "timestamp": time.time(),
                        }
                        room.messages.append(sys_msg)
                        if len(room.messages) > 100:
                            room.messages.pop(0)

                        payload = {
                            "type": "playback_changed",
                            "current_track": room.current_track.to_dict(),
                            "status": room.status,
                            "current_time": room.current_time,
                            "updated_at": room.updated_at,
                            "server_time": room.updated_at,
                            "queue": [t.to_dict() for t in room.queue],
                            "skip_vote": room.get_skip_state(),
                            "priority_request": room.get_priority_state(),
                            "related_suggestions": get_cached_related_videos(room.current_track.id, limit=14),
                            "message": sys_msg,
                        }
                        invalidate_rooms_cache()
                        schedule_prewarm_room_related(room)
                        db.schedule_save_room(room.to_db_record())
                        db.schedule_add_message(room.id, sys_msg)
                    else:
                        room.queue.append(new_track)
                        position = len(room.queue)

                        sys_msg = {
                            "id": secrets.token_hex(4),
                            "type": "system",
                            "text": f"➕ {current_user.name} đã thêm vào hàng đợi (#{position}): {new_track.title}",
                            "timestamp": time.time(),
                        }
                        room.messages.append(sys_msg)
                        if len(room.messages) > 100:
                            room.messages.pop(0)

                        payload = {
                            "type": "queue_updated",
                            "queue": [t.to_dict() for t in room.queue],
                            "message": sys_msg,
                        }
                        if position == 1:
                            schedule_prewarm_room_related(room)
                        db.schedule_replace_queue(room.id, [t.to_dict() for t in room.queue])
                        db.schedule_add_message(room.id, sys_msg)

                await room.broadcast(payload)

            # ----------------------------------------------------------------
            # 5. REMOVE TRACK FROM QUEUE (STRICT OWNERSHIP OR HOST CHECK)
            # ----------------------------------------------------------------
            elif action == "remove_track":
                uid = data.get("uid")
                if not isinstance(uid, str) or not current_user:
                    continue

                payload = None
                unauthorized = False
                async with room.lock:
                    target = next((t for t in room.queue if t.uid == uid), None)
                    if target:
                        if room.is_host(user_id) or target.added_by_id == user_id:
                            room.queue = [t for t in room.queue if t.uid != uid]
                            room.state_version += 1
                            sys_msg = {
                                "id": secrets.token_hex(4),
                                "type": "system",
                                "text": f"🗑️ {current_user.name} đã xóa bài: {target.title}",
                                "timestamp": time.time(),
                            }
                            room.messages.append(sys_msg)
                            if len(room.messages) > 100:
                                room.messages.pop(0)
                            payload = {
                                "type": "queue_updated",
                                "queue": [t.to_dict() for t in room.queue],
                                "priority_request": room.get_priority_state(),
                                "message": sys_msg,
                            }
                            schedule_prewarm_room_related(room)
                            db.schedule_replace_queue(room.id, [t.to_dict() for t in room.queue])
                            db.schedule_add_message(room.id, sys_msg)
                        else:
                            unauthorized = True

                if unauthorized:
                    await websocket.send_json({"type": "error", "message": "Bạn chỉ có thể xóa bài hát do chính mình thêm"})
                elif payload:
                    await room.broadcast(payload)

            # ----------------------------------------------------------------
            # 6. REORDER QUEUE (HOST ONLY + LOCKED)
            # ----------------------------------------------------------------
            elif action == "reorder_queue":
                allowed, _ = rate_limiter.allow(f"ws_ctrl:{user_id}", limit=12, window_sec=10)
                if not allowed:
                    continue

                uids = data.get("uids")
                if not isinstance(uids, list):
                    continue

                async with room.lock:
                    if not room.is_host(user_id):
                        await websocket.send_json({"type": "error", "message": "Chỉ Chủ phòng (Host) mới được sắp xếp lại hàng đợi"})
                        continue
                    uid_map = {t.uid: t for t in room.queue}
                    new_q = []
                    seen = set()
                    for uid in uids:
                        if isinstance(uid, str) and uid in uid_map and uid not in seen:
                            new_q.append(uid_map[uid])
                            seen.add(uid)
                    for t in room.queue:
                        if t.uid not in seen:
                            new_q.append(t)
                    room.queue = new_q
                    room.state_version += 1
                    payload = {
                        "type": "queue_updated",
                        "queue": [t.to_dict() for t in room.queue],
                        "priority_request": room.get_priority_state(),
                    }
                    schedule_prewarm_room_related(room)
                    db.schedule_replace_queue(room.id, [t.to_dict() for t in room.queue])

                await room.broadcast(payload)

            # ----------------------------------------------------------------
            # 7. PLAYBACK CONTROL — HOST-ONLY MASTER SYNC (MEMBERS ARE LOCAL-ONLY)
            # ----------------------------------------------------------------
            elif action == "control":
                allowed, _ = rate_limiter.allow(f"ws_ctrl:{user_id}", limit=12, window_sec=10)
                if not allowed:
                    continue

                sub_action = data.get("sub_action")
                if sub_action not in ("play", "pause", "seek"):
                    continue

                async with room.lock:
                    if not room.is_host(user_id):
                        await websocket.send_json({
                            "type": "error",
                            "message": "Chỉ Chủ phòng (Host) mới điều khiển nhạc cả phòng — thao tác của bạn chỉ áp dụng riêng trên máy bạn",
                        })
                        continue

                    if not room.current_track:
                        continue

                    raw_time = data.get("current_time", room.get_synced_time())
                    try:
                        parsed_time = float(raw_time)
                        if not math.isfinite(parsed_time) or parsed_time < 0:
                            parsed_time = 0.0
                    except (TypeError, ValueError):
                        parsed_time = room.get_synced_time()

                    if room.current_track.duration > 0:
                        parsed_time = min(parsed_time, float(room.current_track.duration))

                    now = time.time()
                    if sub_action == "play":
                        room.status = "playing"
                        room.current_time = parsed_time
                        room.updated_at = now
                    elif sub_action == "pause":
                        room.status = "paused"
                        room.current_time = parsed_time
                        room.updated_at = now
                    elif sub_action == "seek":
                        room.current_time = parsed_time
                        room.updated_at = now

                    payload = {
                        "type": "playback_sync",
                        "status": room.status,
                        "current_time": room.current_time,
                        "updated_at": room.updated_at,
                        "server_time": now,
                        "by_user": current_user.name if current_user else "Chủ phòng",
                        "sub_action": sub_action,
                    }
                    db.schedule_save_room(room.to_db_record())

                await room.broadcast(payload)

            # ----------------------------------------------------------------
            # 8. TRACK ENDED (ANTI-CHEAT SERVER CLOCK / QUORUM + LOCKED)
            # ----------------------------------------------------------------
            elif action == "track_ended":
                ended_id = data.get("track_id")
                ended_uid = data.get("track_uid")
                if not isinstance(ended_id, str):
                    continue

                payload = None
                async with room.lock:
                    ct = room.current_track
                    if not ct or ct.id != ended_id:
                        continue
                    if ended_uid and ct.uid != ended_uid:
                        continue

                    should_advance = False
                    if ct.duration > 0:
                        # Enforce server clock even for Host so local seeking to end never skips room early!
                        if room.get_synced_time() >= max(0.0, float(ct.duration) - 6.0):
                            should_advance = True
                    elif room.is_host(user_id):
                        should_advance = True
                    else:
                        room.ended_reports.add(user_id)
                        active_reports = [u for u in room.ended_reports if u in room.users]
                        needed = max(1, (len(room.users) + 1) // 2)
                        if len(active_reports) >= needed:
                            should_advance = True

                    if should_advance:
                        finished_track_dict = ct.to_dict()
                        next_t = room.advance_track()
                        sys_msg = {
                            "id": secrets.token_hex(4),
                            "type": "system",
                            "text": f"⏭️ Đang phát bài tiếp theo: {next_t.title}" if next_t else "🎵 Đã phát hết danh sách bài hát trong hàng đợi",
                            "timestamp": time.time(),
                        }
                        room.messages.append(sys_msg)
                        if len(room.messages) > 100:
                            room.messages.pop(0)

                        payload = {
                            "type": "playback_changed",
                            "current_track": next_t.to_dict() if next_t else None,
                            "status": room.status,
                            "current_time": 0.0,
                            "updated_at": room.updated_at,
                            "server_time": room.updated_at,
                            "queue": [t.to_dict() for t in room.queue],
                            "history": [t.to_dict() for t in room.history[-10:]],
                            "skip_vote": room.get_skip_state(),
                            "priority_request": room.get_priority_state(),
                            "related_suggestions": get_cached_related_videos(next_t.id, limit=14) if next_t else None,
                            "message": sys_msg,
                        }
                        invalidate_rooms_cache()
                        schedule_prewarm_room_related(room)
                        db.schedule_add_history(room.id, finished_track_dict)
                        db.schedule_save_room(room.to_db_record())
                        db.schedule_replace_queue(room.id, [t.to_dict() for t in room.queue])
                        db.schedule_add_message(room.id, sys_msg)

                if payload:
                    await room.broadcast(payload)
                    if payload.get("current_track") is None:
                        schedule_auto_dj_if_needed(room)

            # ----------------------------------------------------------------
            # 9. SONG PRIORITY REQUEST & MAJORITY VOTE (>= 50% OF MEMBERS)
            # ----------------------------------------------------------------
            elif action in ("request_priority", "vote_priority"):
                allowed, _ = rate_limiter.allow(f"ws_priority:{user_id}", limit=8, window_sec=15)
                if not allowed:
                    await websocket.send_json({"type": "error", "message": "Bạn thao tác bình chọn ưu tiên quá nhanh!"})
                    continue

                track_uid = data.get("track_uid")
                if not isinstance(track_uid, str) or not current_user:
                    continue

                payload = None
                err_msg = None
                async with room.lock:
                    idx = next((i for i, t in enumerate(room.queue) if t.uid == track_uid), -1)
                    if idx == -1:
                        err_msg = "Bài hát không còn trong hàng đợi"
                    elif idx == 0:
                        err_msg = "Bài hát này đã ở vị trí #1 (phát kế tiếp) rồi!"
                    else:
                        target_track = room.queue[idx]
                        active_req = room.get_priority_state()

                        if action == "request_priority":
                            if active_req and active_req["track_uid"] != track_uid:
                                err_msg = f"Đang có yêu cầu ưu tiên khác cho bài \"{active_req['track_title']}\". Hãy hoàn tất hoặc chờ hết giờ!"
                            elif active_req and active_req["track_uid"] == track_uid:
                                # Already requested for this track -> toggle vote
                                if user_id in room.priority_request["votes"]:
                                    room.priority_request["votes"].remove(user_id)
                                else:
                                    room.priority_request["votes"].add(user_id)
                            else:
                                # Create brand-new priority request (requester casts vote #1)
                                now_ts = time.time()
                                room.priority_request = {
                                    "track_uid": target_track.uid,
                                    "track_id": target_track.id,
                                    "track_title": target_track.title,
                                    "track_thumbnail": target_track.thumbnail,
                                    "requested_by_id": user_id,
                                    "requested_by_name": current_user.name,
                                    "votes": {user_id},
                                    "created_at": now_ts,
                                    "expires_at": now_ts + PRIORITY_VOTE_TTL_SEC,
                                }
                        else:
                            # vote_priority
                            if not active_req or active_req["track_uid"] != track_uid:
                                err_msg = "Yêu cầu ưu tiên này đã kết thúc hoặc hết hạn"
                            else:
                                if user_id in room.priority_request["votes"]:
                                    room.priority_request["votes"].remove(user_id)
                                else:
                                    room.priority_request["votes"].add(user_id)

                        if not err_msg and room.priority_request:
                            count, needed, _ = room.get_priority_stats()
                            if count >= needed:
                                promoted = room.promote_track_to_top(track_uid)
                                if promoted:
                                    sys_msg = {
                                        "id": secrets.token_hex(4),
                                        "type": "system",
                                        "text": f"🚀 Đã đủ {needed}/{needed} phiếu! Bài \"{promoted.title}\" được ưu tiên lên phát kế tiếp (#1)!",
                                        "timestamp": time.time(),
                                    }
                                    room.messages.append(sys_msg)
                                    if len(room.messages) > 100:
                                        room.messages.pop(0)
                                    payload = {
                                        "type": "queue_updated",
                                        "queue": [t.to_dict() for t in room.queue],
                                        "priority_request": None,
                                        "message": sys_msg,
                                    }
                                    db.schedule_replace_queue(room.id, [t.to_dict() for t in room.queue])
                                    db.schedule_add_message(room.id, sys_msg)
                                    schedule_prewarm_room_related(room)
                            elif count == 0:
                                room.priority_request = None
                                payload = {
                                    "type": "priority_vote_updated",
                                    "priority_request": None,
                                    "message": None,
                                }
                            else:
                                p_state = room.get_priority_state()
                                sys_msg = None
                                if action == "request_priority" and not active_req:
                                    sys_msg = {
                                        "id": secrets.token_hex(4),
                                        "type": "system",
                                        "text": f"⚡ {current_user.name} yêu cầu ưu tiên bài \"{target_track.title}\" ({count}/{needed} phiếu). Mọi người cùng bình chọn nhé!",
                                        "timestamp": time.time(),
                                    }
                                    room.messages.append(sys_msg)
                                    if len(room.messages) > 100:
                                        room.messages.pop(0)
                                    db.schedule_add_message(room.id, sys_msg)
                                payload = {
                                    "type": "priority_vote_updated",
                                    "priority_request": p_state,
                                    "message": sys_msg,
                                }

                if err_msg:
                    await websocket.send_json({"type": "error", "message": err_msg})
                elif payload:
                    await room.broadcast(payload)

            elif action == "cancel_priority":
                payload = None
                unauthorized = False
                async with room.lock:
                    req = room.priority_request
                    if req:
                        if room.is_host(user_id) or req["requested_by_id"] == user_id:
                            title = req["track_title"]
                            room.priority_request = None
                            sys_msg = {
                                "id": secrets.token_hex(4),
                                "type": "system",
                                "text": f"✖️ {current_user.name} đã hủy yêu cầu ưu tiên bài \"{title}\"",
                                "timestamp": time.time(),
                            }
                            room.messages.append(sys_msg)
                            if len(room.messages) > 100:
                                room.messages.pop(0)
                            db.schedule_add_message(room.id, sys_msg)
                            payload = {
                                "type": "priority_vote_updated",
                                "priority_request": None,
                                "message": sys_msg,
                            }
                        else:
                            unauthorized = True

                if unauthorized:
                    await websocket.send_json({"type": "error", "message": "Chỉ người yêu cầu hoặc Chủ phòng mới được hủy"})
                elif payload:
                    await room.broadcast(payload)

            # ----------------------------------------------------------------
            # 10. FAIR MAJORITY SKIP VOTE (>= 50% OF MEMBERS, ANTI-SYBIL)
            # ----------------------------------------------------------------
            elif action == "vote_skip":
                allowed, _ = rate_limiter.allow(f"ws_skip:{user_id}", limit=8, window_sec=15)
                if not allowed:
                    await websocket.send_json({"type": "error", "message": "Bạn bình chọn qua bài quá nhanh!"})
                    continue

                if not current_user:
                    continue

                payload = None
                err_msg = None
                async with room.lock:
                    ct = room.current_track
                    if not ct:
                        err_msg = "Hiện chưa có bài hát nào đang phát"
                    else:
                        had_votes_before = len(room.skip_votes) > 0
                        if user_id in room.skip_votes:
                            room.skip_votes.remove(user_id)
                        else:
                            room.skip_votes.add(user_id)

                        count, needed, _ = room.get_skip_stats()
                        if count >= needed:
                            finished_track_dict = ct.to_dict()
                            next_t = room.advance_track()
                            sys_msg = {
                                "id": secrets.token_hex(4),
                                "type": "system",
                                "text": (
                                    f"⏭️ Đã đủ {needed}/{needed} phiếu bỏ qua! Chuyển sang bài: {next_t.title}"
                                    if next_t
                                    else f"⏭️ Đã đủ {needed}/{needed} phiếu bỏ qua bài \"{ct.title}\". Hàng đợi hiện đang trống."
                                ),
                                "timestamp": time.time(),
                            }
                            room.messages.append(sys_msg)
                            if len(room.messages) > 100:
                                room.messages.pop(0)
                            payload = {
                                "type": "playback_changed",
                                "current_track": next_t.to_dict() if next_t else None,
                                "status": room.status,
                                "current_time": 0.0,
                                "updated_at": room.updated_at,
                                "server_time": room.updated_at,
                                "queue": [t.to_dict() for t in room.queue],
                                "history": [t.to_dict() for t in room.history[-10:]],
                                "skip_vote": room.get_skip_state(),
                                "priority_request": room.get_priority_state(),
                                "related_suggestions": get_cached_related_videos(next_t.id, limit=14) if next_t else None,
                                "message": sys_msg,
                            }
                            invalidate_rooms_cache()
                            schedule_prewarm_room_related(room)
                            db.schedule_add_history(room.id, finished_track_dict)
                            db.schedule_save_room(room.to_db_record())
                            db.schedule_replace_queue(room.id, [t.to_dict() for t in room.queue])
                            db.schedule_add_message(room.id, sys_msg)
                        else:
                            sys_msg = None
                            if not had_votes_before and count > 0:
                                sys_msg = {
                                    "id": secrets.token_hex(4),
                                    "type": "system",
                                    "text": f"⏭️ {current_user.name} đề xuất qua bài \"{ct.title}\" ({count}/{needed} phiếu). Mọi người cùng vote nhé!",
                                    "timestamp": time.time(),
                                }
                                room.messages.append(sys_msg)
                                if len(room.messages) > 100:
                                    room.messages.pop(0)
                                db.schedule_add_message(room.id, sys_msg)
                            payload = {
                                "type": "skip_vote_updated",
                                "skip_vote": room.get_skip_state(),
                                "message": sys_msg,
                            }

                if err_msg:
                    await websocket.send_json({"type": "error", "message": err_msg})
                elif payload:
                    await room.broadcast(payload)
                    if payload.get("type") == "playback_changed" and payload.get("current_track") is None:
                        schedule_auto_dj_if_needed(room)

            # ----------------------------------------------------------------
            # 11B. TOGGLE AI AUTO-DJ (HOST ONLY)
            # ----------------------------------------------------------------
            elif action == "toggle_auto_dj":
                async with room.lock:
                    if not room.is_host(user_id):
                        await websocket.send_json({"type": "error", "message": "Chỉ Chủ phòng (Host) mới được bật/tắt Auto-DJ"})
                        continue
                    room.auto_dj = not room.auto_dj
                    mode_text = (
                        f"BẬT ({AI_BOT_NAME} sẽ tự nối nhạc hợp gu khi hết hàng đợi)"
                        if room.auto_dj
                        else "TẮT"
                    )
                    sys_msg = {
                        "id": secrets.token_hex(4),
                        "type": "system",
                        "text": f"🎀 Auto-DJ đã {mode_text}",
                        "timestamp": time.time(),
                    }
                    room.messages.append(sys_msg)
                    if len(room.messages) > 100:
                        room.messages.pop(0)
                    payload = {
                        "type": "auto_dj_changed",
                        "auto_dj": room.auto_dj,
                        "message": sys_msg,
                    }
                    db.schedule_add_message(room.id, sys_msg)

                await room.broadcast(payload)
                if room.auto_dj and room.current_track is None and len(room.queue) == 0:
                    schedule_auto_dj_if_needed(room)

            # ----------------------------------------------------------------
            # 11C. TOGGLE AI FEMALE RADIO MC (HOST ONLY, DEFAULT OFF)
            # ----------------------------------------------------------------
            elif action == "toggle_mc_mode":
                async with room.lock:
                    if not room.is_host(user_id):
                        await websocket.send_json({"type": "error", "message": "Chỉ Chủ phòng (Host) mới được bật/tắt MC Radio"})
                        continue
                    room.mc_enabled = not room.mc_enabled
                    mode_text = f"BẬT (Nữ MC {AI_BOT_NAME} sẽ dẫn chuyện khi chuyển bài)" if room.mc_enabled else "TẮT"
                    sys_msg = {
                        "id": secrets.token_hex(4),
                        "type": "system",
                        "text": f"🎙️ MC Radio đã {mode_text}",
                        "timestamp": time.time(),
                    }
                    room.messages.append(sys_msg)
                    if len(room.messages) > 100:
                        room.messages.pop(0)
                    payload = {
                        "type": "mc_mode_changed",
                        "mc_enabled": room.mc_enabled,
                        "message": sys_msg,
                    }
                    schedule_prewarm_room_related(room)
                    db.schedule_add_message(room.id, sys_msg)

                await room.broadcast(payload)

            # ----------------------------------------------------------------
            # 11. TOGGLE DJ MODE (HOST ONLY + LOCKED)
            # ----------------------------------------------------------------
            elif action == "toggle_dj_mode":
                async with room.lock:
                    if not room.is_host(user_id):
                        await websocket.send_json({"type": "error", "message": "Chỉ Chủ phòng (Host) mới được bật/tắt chế độ DJ"})
                        continue
                    room.dj_mode = not room.dj_mode
                    mode_text = (
                        "BẬT (Chỉ Chủ phòng mới được thêm bài hát vào hàng đợi)"
                        if room.dj_mode
                        else "TẮT (Mọi người đều có thể thêm bài hát vào hàng đợi)"
                    )
                    sys_msg = {
                        "id": secrets.token_hex(4),
                        "type": "system",
                        "text": f"🎛️ Chế độ DJ đã {mode_text}",
                        "timestamp": time.time(),
                    }
                    room.messages.append(sys_msg)
                    if len(room.messages) > 100:
                        room.messages.pop(0)
                    payload = {
                        "type": "dj_mode_changed",
                        "dj_mode": room.dj_mode,
                        "message": sys_msg,
                    }
                    db.schedule_save_room(room.to_db_record())
                    db.schedule_add_message(room.id, sys_msg)

                await room.broadcast(payload)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WebSocket error in room {clean_id}] {e}")
    finally:
        ddos_guard.release_ws(client_ip)
        leave_payload = None
        async with room.lock:
            # Only remove if this websocket is still the active socket for this user
            if room.user_sockets.get(user_id) is websocket:
                room.user_sockets.pop(user_id, None)
                if user_id in room.users:
                    leaving_user = room.users.pop(user_id)
                    room.ended_reports.discard(user_id)
                    room.skip_votes.discard(user_id)
                    if room.priority_request:
                        room.priority_request["votes"].discard(user_id)
                        if not any(uid in room.users for uid in room.priority_request["votes"]):
                            room.priority_request = None

                    new_host_msg = None
                    if room.host_id == user_id:
                        if room.users:
                            # Transfer host deterministically to earliest-joined remaining member
                            next_host_id = min(room.users, key=lambda uid: room.users[uid].joined_at)
                            room.host_id = next_host_id
                            new_host_msg = f"👑 {room.users[next_host_id].name} đã trở thành Chủ phòng mới"
                        else:
                            room.host_id = ""
                            room.last_empty_at = time.time()
                    elif not room.users:
                        room.last_empty_at = time.time()

                    leave_msg = {
                        "id": secrets.token_hex(4),
                        "type": "system",
                        "text": f"🏃 {leaving_user.name} đã rời phòng" + (f". {new_host_msg}" if new_host_msg else ""),
                        "timestamp": time.time(),
                    }
                    room.messages.append(leave_msg)
                    if len(room.messages) > 100:
                        room.messages.pop(0)

                    invalidate_rooms_cache()
                    leave_payload = {
                        "type": "user_left",
                        "user_id": user_id,
                        "users": [u.to_dict(room.host_id) for u in room.users.values()],
                        "host_id": room.host_id,
                        "skip_vote": room.get_skip_state(),
                        "priority_request": room.get_priority_state(),
                        "message": leave_msg,
                    }

        if leave_payload:
            await room.broadcast(leave_payload)


# ============================================================================
# STATIC FILES & ROOT
# ============================================================================

static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def serve_index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "Website Music Room Backend Running!"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
