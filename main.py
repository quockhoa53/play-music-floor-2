"""Main application for collaborative YouTube Music Room and Realtime Chat."""
import asyncio
import os
import re
import time
import uuid
from typing import Dict, List, Optional, Set
import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from services.youtube import (
    extract_video_id,
    get_video_info,
    search_youtube,
    format_duration,
    get_audio_stream_url,
)

app = FastAPI(title="Floor 2 - Sync Music Room & Chat", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# MODELS & STATE MANAGEMENT
# ============================================================================

class User:
    def __init__(self, user_id: str, name: str, avatar: str, color: str, is_host: bool = False):
        self.user_id = user_id
        self.name = name
        self.avatar = avatar
        self.color = color
        self.is_host = is_host
        self.joined_at = time.time()

    def to_dict(self):
        return {
            "id": self.user_id,
            "name": self.name,
            "avatar": self.avatar,
            "color": self.color,
            "is_host": self.is_host,
            "joined_at": self.joined_at,
        }


class Track:
    def __init__(
        self,
        track_id: str,
        title: str,
        author: str,
        thumbnail: str,
        duration: int,
        duration_str: str,
        added_by_id: str,
        added_by_name: str,
    ):
        self.uid = str(uuid.uuid4())[:8]  # unique queue item id
        self.id = track_id
        self.title = title
        self.author = author
        self.thumbnail = thumbnail
        self.duration = duration
        self.duration_str = duration_str
        self.added_by_id = added_by_id
        self.added_by_name = added_by_name
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
    def __init__(self, room_id: str, name: str, host_id: str, dj_mode: bool = False):
        self.id = room_id
        self.name = name
        self.created_at = time.time()
        self.host_id = host_id
        self.dj_mode = dj_mode  # If True, only host can pause/play/skip

        # Playback state
        self.current_track: Optional[Track] = None
        self.status = "idle"  # "playing", "paused", "idle"
        self.current_time = 0.0  # seconds into the video
        self.updated_at = time.time()

        # Queues
        self.queue: List[Track] = []
        self.history: List[Track] = []

        # Users and connections
        self.users: Dict[str, User] = {}
        self.connections: Set[WebSocket] = set()

        # Chat
        self.messages: List[Dict] = []

        # Skip votes
        self.skip_votes: Set[str] = set()

    def get_synced_time(self) -> float:
        """Calculate current playback time accounting for elapsed time."""
        if self.status == "playing":
            elapsed = time.time() - self.updated_at
            if self.current_track and self.current_track.duration > 0:
                return min(self.current_time + elapsed, float(self.current_track.duration))
            return self.current_time + elapsed
        return self.current_time

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "host_id": self.host_id,
            "dj_mode": self.dj_mode,
            "status": self.status,
            "current_time": self.get_synced_time(),
            "updated_at": self.updated_at,
            "current_track": self.current_track.to_dict() if self.current_track else None,
            "queue": [t.to_dict() for t in self.queue],
            "history": [t.to_dict() for t in self.history[-10:]],
            "users": [u.to_dict() for u in self.users.values()],
            "user_count": len(self.users),
            "skip_votes_count": len(self.skip_votes),
            "skip_votes_needed": max(1, (len(self.users) + 1) // 2),
        }

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients in the room."""
        dead_sockets = set()
        for ws in list(self.connections):
            try:
                await ws.send_json(message)
            except Exception:
                dead_sockets.add(ws)
        for ws in dead_sockets:
            self.connections.discard(ws)

    def advance_track(self) -> Optional[Track]:
        """Move to the next track in queue (FIFO)."""
        if self.current_track:
            self.history.append(self.current_track)
            if len(self.history) > 30:
                self.history.pop(0)

        self.skip_votes.clear()

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


# In-memory storage for rooms
rooms: Dict[str, Room] = {}


def get_or_create_room(room_id: str, name: Optional[str] = None, host_id: Optional[str] = None) -> Room:
    clean_id = room_id.strip().lower()
    if clean_id not in rooms:
        room_name = name or f"Phòng #{clean_id}"
        rooms[clean_id] = Room(clean_id, room_name, host_id or "")
    return rooms[clean_id]


# Pre-populate a default cozy lobby room
default_room = get_or_create_room("chill-zone", "☕ Quán Cà Phê Chill - Phòng Chung")


# ============================================================================
# REST APIS
# ============================================================================

class CreateRoomRequest(BaseModel):
    id: Optional[str] = None
    name: str
    dj_mode: Optional[bool] = False


@app.get("/api/health")
async def health_check():
    """Health check endpoint for Render & uptime monitors."""
    return {
        "status": "ok",
        "rooms_count": len(rooms),
        "active_users": sum(len(r.users) for r in rooms.values()),
    }


@app.get("/api/rooms")
async def list_rooms():
    """List all active rooms with status and listener counts."""
    result = []
    for r in rooms.values():
        result.append({
            "id": r.id,
            "name": r.name,
            "user_count": len(r.users),
            "dj_mode": r.dj_mode,
            "status": r.status,
            "current_track": r.current_track.to_dict() if r.current_track else None,
            "queue_count": len(r.queue),
        })
    return {"rooms": result}


@app.post("/api/rooms")
async def create_room(req: CreateRoomRequest):
    """Create a new room."""
    room_id = (req.id or str(uuid.uuid4())[:6]).strip().lower()
    room_id = re.sub(r"[^a-zA-Z0-9_-]", "-", room_id)
    if not room_id:
        room_id = str(uuid.uuid4())[:6]

    if room_id in rooms:
        return {"success": True, "room_id": room_id, "already_exists": True}

    room = Room(room_id=room_id, name=req.name.strip() or f"Phòng #{room_id}", host_id="", dj_mode=bool(req.dj_mode))
    rooms[room_id] = room
    return {"success": True, "room_id": room_id}


@app.get("/api/rooms/{room_id}")
async def get_room_info(room_id: str):
    """Get snapshot of room data."""
    clean_id = room_id.strip().lower()
    if clean_id not in rooms:
        raise HTTPException(status_code=404, detail="Room not found")
    return {"room": rooms[clean_id].to_dict()}


@app.get("/api/youtube/search")
async def api_search_youtube(q: str = Query(..., min_length=1)):
    """Search YouTube videos or extract URL."""
    results = await search_youtube(q, limit=8)
    return {"results": results}


@app.get("/api/youtube/info")
async def api_video_info(url: str = Query(..., min_length=1)):
    """Get metadata for a specific YouTube video."""
    info = await get_video_info(url)
    if not info:
        raise HTTPException(status_code=400, detail="Không tìm thấy thông tin video từ đường dẫn này")
    return {"video": info}


@app.get("/api/youtube/audio/{video_id}")
async def api_audio_stream(video_id: str, request: Request):
    """Stream pure YouTube audio with HTTP Range support (for Radio fallback / embed-restricted tracks)."""
    stream_info = await get_audio_stream_url(video_id)
    if not stream_info:
        raise HTTPException(status_code=404, detail="Không thể trích xuất luồng âm thanh cho video này")

    stream_url, content_type = stream_info
    upstream_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    range_header = request.headers.get("range")
    if range_header:
        upstream_headers["Range"] = range_header

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


@app.get("/api/suggestions")
async def get_suggestions():
    """Curated suggestions for fast testing and immediate listening."""
    suggestions = [
        {
            "id": "FN7ALfpGxiI",
            "title": "NƠI NÀY CÓ ANH | SƠN TÙNG M-TP",
            "author": "Sơn Tùng M-TP Official",
            "thumbnail": "https://i.ytimg.com/vi/FN7ALfpGxiI/hqdefault.jpg",
            "duration": 278,
            "duration_str": "04:38",
            "tag": "V-Pop Hit",
        },
        {
            "id": "SlQR9iu09bQ",
            "title": "COME MY WAY | SƠN TÙNG M-TP x TYGA",
            "author": "Sơn Tùng M-TP Official",
            "thumbnail": "https://i.ytimg.com/vi/SlQR9iu09bQ/hqdefault.jpg",
            "duration": 235,
            "duration_str": "03:55",
            "tag": "Trending",
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
            "id": "knW7-x7Y7RE",
            "title": "HÃY TRAO CHO ANH ft. Snoop Dogg | SƠN TÙNG M-TP",
            "author": "Sơn Tùng M-TP Official",
            "thumbnail": "https://i.ytimg.com/vi/knW7-x7Y7RE/hqdefault.jpg",
            "duration": 263,
            "duration_str": "04:23",
            "tag": "Hit MV",
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
    ]
    return {"suggestions": suggestions}


# ============================================================================
# WEBSOCKET REALTIME SYNC & CHAT
# ============================================================================

@app.websocket("/ws/{room_id}/{user_id}")
async def websocket_room_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await websocket.accept()
    clean_id = room_id.strip().lower()
    room = get_or_create_room(clean_id)
    room.connections.add(websocket)

    current_user: Optional[User] = None

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            # ----------------------------------------------------------------
            # 1. USER JOIN
            # ----------------------------------------------------------------
            if action == "join":
                user_name = (data.get("name") or "Khách").strip()[:30]
                avatar = data.get("avatar") or "🎧"
                color = data.get("color") or "#6366f1"

                # Assign host if room has no host or previous host left
                is_host = False
                if not room.host_id or room.host_id not in room.users:
                    room.host_id = user_id
                    is_host = True

                current_user = User(user_id, user_name, avatar, color, is_host)
                room.users[user_id] = current_user

                # Send full state to this user
                await websocket.send_json({
                    "type": "init_state",
                    "room": room.to_dict(),
                    "messages": room.messages[-50:],
                    "your_user_id": user_id,
                    "is_host": is_host,
                })

                # Broadcast system message and updated user list
                join_msg = {
                    "id": str(uuid.uuid4())[:8],
                    "type": "system",
                    "text": f"👋 {user_name} đã vào phòng",
                    "timestamp": time.time(),
                }
                room.messages.append(join_msg)
                await room.broadcast({
                    "type": "user_joined",
                    "user": current_user.to_dict(),
                    "users": [u.to_dict() for u in room.users.values()],
                    "message": join_msg,
                    "host_id": room.host_id,
                })

            # ----------------------------------------------------------------
            # 2. CHAT MESSAGE
            # ----------------------------------------------------------------
            elif action == "chat":
                text = (data.get("text") or "").strip()
                if text and current_user:
                    msg = {
                        "id": str(uuid.uuid4())[:8],
                        "type": "chat",
                        "user_id": current_user.user_id,
                        "user_name": current_user.name,
                        "user_avatar": current_user.avatar,
                        "user_color": current_user.color,
                        "is_host": current_user.is_host,
                        "text": text[:500],
                        "timestamp": time.time(),
                    }
                    room.messages.append(msg)
                    if len(room.messages) > 100:
                        room.messages.pop(0)

                    await room.broadcast({"type": "chat", "message": msg})

            # ----------------------------------------------------------------
            # 3. EMOJI REACTION (FLOATING PARTICLES)
            # ----------------------------------------------------------------
            elif action == "reaction":
                emoji = data.get("emoji") or "❤️"
                if current_user:
                    await room.broadcast({
                        "type": "reaction",
                        "emoji": emoji,
                        "user_name": current_user.name,
                    })

            # ----------------------------------------------------------------
            # 4. ADD TRACK (FIFO QUEUE)
            # ----------------------------------------------------------------
            elif action == "add_track":
                track_data = data.get("track")
                if not track_data or not current_user:
                    continue

                v_id = extract_video_id(track_data.get("id") or track_data.get("url", ""))
                if not v_id:
                    continue

                title = track_data.get("title") or f"Video {v_id}"
                author = track_data.get("author") or "YouTube"
                thumbnail = track_data.get("thumbnail") or f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"
                duration = int(track_data.get("duration") or 0)
                duration_str = track_data.get("duration_str") or format_duration(duration)

                new_track = Track(
                    track_id=v_id,
                    title=title,
                    author=author,
                    thumbnail=thumbnail,
                    duration=duration,
                    duration_str=duration_str,
                    added_by_id=current_user.user_id,
                    added_by_name=current_user.name,
                )

                # If idle, play right away
                if room.current_track is None:
                    room.current_track = new_track
                    room.status = "playing"
                    room.current_time = 0.0
                    room.updated_at = time.time()
                    room.skip_votes.clear()

                    sys_msg = {
                        "id": str(uuid.uuid4())[:8],
                        "type": "system",
                        "text": f"🎶 {current_user.name} đã phát: {title}",
                        "timestamp": time.time(),
                    }
                    room.messages.append(sys_msg)

                    await room.broadcast({
                        "type": "playback_changed",
                        "current_track": room.current_track.to_dict(),
                        "status": room.status,
                        "current_time": room.current_time,
                        "updated_at": room.updated_at,
                        "queue": [t.to_dict() for t in room.queue],
                        "message": sys_msg,
                    })
                else:
                    # Append to FIFO queue
                    room.queue.append(new_track)
                    position = len(room.queue)

                    sys_msg = {
                        "id": str(uuid.uuid4())[:8],
                        "type": "system",
                        "text": f"➕ {current_user.name} đã thêm vào hàng đợi (#{position}): {title}",
                        "timestamp": time.time(),
                    }
                    room.messages.append(sys_msg)

                    await room.broadcast({
                        "type": "queue_updated",
                        "queue": [t.to_dict() for t in room.queue],
                        "message": sys_msg,
                    })

            # ----------------------------------------------------------------
            # 5. REMOVE TRACK FROM QUEUE
            # ----------------------------------------------------------------
            elif action == "remove_track":
                uid = data.get("uid")
                if uid and current_user:
                    target = next((t for t in room.queue if t.uid == uid), None)
                    if target and (current_user.is_host or target.added_by_id == current_user.user_id):
                        room.queue = [t for t in room.queue if t.uid != uid]
                        sys_msg = {
                            "id": str(uuid.uuid4())[:8],
                            "type": "system",
                            "text": f"🗑️ {current_user.name} đã xóa bài: {target.title}",
                            "timestamp": time.time(),
                        }
                        room.messages.append(sys_msg)
                        await room.broadcast({
                            "type": "queue_updated",
                            "queue": [t.to_dict() for t in room.queue],
                            "message": sys_msg,
                        })

            # ----------------------------------------------------------------
            # 6. REORDER QUEUE
            # ----------------------------------------------------------------
            elif action == "reorder_queue":
                # Only host can reorder entire queue
                if current_user and current_user.is_host:
                    uids = data.get("uids", [])
                    uid_map = {t.uid: t for t in room.queue}
                    new_q = []
                    for uid in uids:
                        if uid in uid_map:
                            new_q.append(uid_map[uid])
                    for t in room.queue:
                        if t not in new_q:
                            new_q.append(t)
                    room.queue = new_q
                    await room.broadcast({
                        "type": "queue_updated",
                        "queue": [t.to_dict() for t in room.queue],
                    })

            # ----------------------------------------------------------------
            # 7. PLAYBACK CONTROL (PLAY / PAUSE / SEEK)
            # ----------------------------------------------------------------
            elif action == "control":
                # Permission check
                if room.dj_mode and (not current_user or not current_user.is_host):
                    await websocket.send_json({
                        "type": "error",
                        "message": "Chế độ DJ đang bật: Chỉ chủ phòng (Host) mới được điều khiển phát nhạc",
                    })
                    continue

                sub_action = data.get("sub_action")  # "play", "pause", "seek"
                current_time = float(data.get("current_time", room.get_synced_time()))

                if sub_action == "play":
                    room.status = "playing"
                    room.current_time = current_time
                    room.updated_at = time.time()
                elif sub_action == "pause":
                    room.status = "paused"
                    room.current_time = current_time
                    room.updated_at = time.time()
                elif sub_action == "seek":
                    room.current_time = current_time
                    room.updated_at = time.time()

                await room.broadcast({
                    "type": "playback_sync",
                    "status": room.status,
                    "current_time": room.current_time,
                    "updated_at": room.updated_at,
                    "by_user": current_user.name if current_user else "Người dùng",
                    "sub_action": sub_action,
                })

            # ----------------------------------------------------------------
            # 8. TRACK ENDED
            # ----------------------------------------------------------------
            elif action == "track_ended":
                ended_id = data.get("track_id")
                # Only proceed if the ended track matches current track
                if room.current_track and room.current_track.id == ended_id:
                    next_t = room.advance_track()
                    sys_msg = {
                        "id": str(uuid.uuid4())[:8],
                        "type": "system",
                        "text": f"⏭️ Đang phát bài tiếp theo: {next_t.title}" if next_t else "🎵 Đã phát hết danh sách bài hát trong hàng đợi",
                        "timestamp": time.time(),
                    }
                    room.messages.append(sys_msg)

                    await room.broadcast({
                        "type": "playback_changed",
                        "current_track": next_t.to_dict() if next_t else None,
                        "status": room.status,
                        "current_time": 0.0,
                        "updated_at": room.updated_at,
                        "queue": [t.to_dict() for t in room.queue],
                        "history": [t.to_dict() for t in room.history[-10:]],
                        "message": sys_msg,
                    })

            # ----------------------------------------------------------------
            # 9. SKIP (VOTE OR FORCE)
            # ----------------------------------------------------------------
            elif action == "skip":
                force = data.get("force", False)
                if force and current_user and current_user.is_host:
                    # Host force skip
                    next_t = room.advance_track()
                    sys_msg = {
                        "id": str(uuid.uuid4())[:8],
                        "type": "system",
                        "text": f"⏩ Chủ phòng {current_user.name} đã chuyển sang bài tiếp theo",
                        "timestamp": time.time(),
                    }
                    room.messages.append(sys_msg)

                    await room.broadcast({
                        "type": "playback_changed",
                        "current_track": next_t.to_dict() if next_t else None,
                        "status": room.status,
                        "current_time": 0.0,
                        "updated_at": room.updated_at,
                        "queue": [t.to_dict() for t in room.queue],
                        "history": [t.to_dict() for t in room.history[-10:]],
                        "message": sys_msg,
                    })
                else:
                    # Vote skip
                    if current_user:
                        if user_id in room.skip_votes:
                            room.skip_votes.remove(user_id)
                        else:
                            room.skip_votes.add(user_id)

                        needed = max(1, (len(room.users) + 1) // 2)
                        if len(room.skip_votes) >= needed:
                            next_t = room.advance_track()
                            sys_msg = {
                                "id": str(uuid.uuid4())[:8],
                                "type": "system",
                                "text": f"⏩ Đã đủ {needed} phiếu bầu bỏ qua! Chuyển bài...",
                                "timestamp": time.time(),
                            }
                            room.messages.append(sys_msg)
                            await room.broadcast({
                                "type": "playback_changed",
                                "current_track": next_t.to_dict() if next_t else None,
                                "status": room.status,
                                "current_time": 0.0,
                                "updated_at": room.updated_at,
                                "queue": [t.to_dict() for t in room.queue],
                                "history": [t.to_dict() for t in room.history[-10:]],
                                "message": sys_msg,
                            })
                        else:
                            await room.broadcast({
                                "type": "skip_vote_updated",
                                "count": len(room.skip_votes),
                                "needed": needed,
                                "voted_users": list(room.skip_votes),
                            })

            # ----------------------------------------------------------------
            # 10. TOGGLE DJ MODE
            # ----------------------------------------------------------------
            elif action == "toggle_dj_mode":
                if current_user and current_user.is_host:
                    room.dj_mode = not room.dj_mode
                    mode_text = "BẬT (Chỉ Host mới có quyền chỉnh nhạc)" if room.dj_mode else "TẮT (Mọi người đều có thể chỉnh nhạc)"
                    sys_msg = {
                        "id": str(uuid.uuid4())[:8],
                        "type": "system",
                        "text": f"🎛️ Chế độ DJ đã {mode_text}",
                        "timestamp": time.time(),
                    }
                    room.messages.append(sys_msg)
                    await room.broadcast({
                        "type": "dj_mode_changed",
                        "dj_mode": room.dj_mode,
                        "message": sys_msg,
                    })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WebSocket error in room {clean_id}] {e}")
    finally:
        room.connections.discard(websocket)
        if current_user and user_id in room.users:
            del room.users[user_id]
            room.skip_votes.discard(user_id)

            # Handle host transfer if the host leaves
            new_host_msg = None
            if room.host_id == user_id:
                if room.users:
                    # Pick next user
                    next_host_id = next(iter(room.users))
                    room.host_id = next_host_id
                    room.users[next_host_id].is_host = True
                    new_host_msg = f"👑 {room.users[next_host_id].name} đã trở thành Chủ phòng mới"
                else:
                    room.host_id = ""

            leave_msg = {
                "id": str(uuid.uuid4())[:8],
                "type": "system",
                "text": f"🏃 {current_user.name} đã rời phòng" + (f". {new_host_msg}" if new_host_msg else ""),
                "timestamp": time.time(),
            }
            room.messages.append(leave_msg)

            await room.broadcast({
                "type": "user_left",
                "user_id": user_id,
                "users": [u.to_dict() for u in room.users.values()],
                "host_id": room.host_id,
                "message": leave_msg,
            })


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
