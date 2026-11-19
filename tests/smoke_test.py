"""Offline functional & security smoke tests for Floor 2 Music Room (no external network calls).

Run: python tests/smoke_test.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Keep smoke tests 100% offline/isolated (never touch real Supabase DB or spawn keep-alive pings)
os.environ["KEEP_ALIVE_ENABLED"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_SECRET_KEY"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from main import app, Room, Track, User, rooms, rate_limiter, ddos_guard, db
from services.security import (
    detect_safe_image_mime,
    hash_passcode,
    sanitize_avatar,
    sanitize_color,
    sanitize_giphy_id,
    sanitize_reaction,
    verify_passcode,
)
from services.youtube import extract_video_id, format_duration, parse_duration_str

# Minimal valid 1x1 PNG binary (for upload tests)
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _new_session(client: TestClient) -> tuple[str, str]:
    r = client.post("/api/session")
    assert r.status_code == 200
    d = r.json()
    return d["token"], d["user_id"]


def test_youtube_and_security_helpers():
    assert extract_video_id("https://www.youtube.com/watch?v=FN7ALfpGxiI") == "FN7ALfpGxiI"
    assert extract_video_id("https://youtu.be/FN7ALfpGxiI?t=42") == "FN7ALfpGxiI"
    assert extract_video_id("https://www.youtube.com/shorts/FN7ALfpGxiI") == "FN7ALfpGxiI"
    assert extract_video_id("https://music.youtube.com/watch?v=FN7ALfpGxiI&si=abc") == "FN7ALfpGxiI"
    assert extract_video_id("FN7ALfpGxiI") == "FN7ALfpGxiI"
    assert extract_video_id("not-a-valid-url") is None

    assert parse_duration_str("3:55") == 235
    assert parse_duration_str("1:02:15") == 3735
    assert parse_duration_str("") == 0
    assert format_duration(235) == "03:55"
    assert format_duration(3735) == "1:02:15"
    assert format_duration(0) == ""

    # XSS sanitizer unit checks
    assert sanitize_avatar("<img src=x onerror=alert(1)>") == "🎧"
    assert sanitize_avatar("🦊") == "🦊"
    assert sanitize_color('"><script>alert(1)</script>') == "#4f46e5"
    assert sanitize_color("#10b981") == "#10b981"
    assert sanitize_reaction("<svg onload=alert(1)>") is None
    assert sanitize_reaction("🔥") == "🔥"
    assert sanitize_giphy_id("GK7dGndK4kyOozR9Xk") == "GK7dGndK4kyOozR9Xk"
    assert sanitize_giphy_id("javascript:alert(1)") is None

    # Magic-byte image validation (blocks SVG/HTML XSS!)
    assert detect_safe_image_mime(TINY_PNG) == "image/png"
    assert detect_safe_image_mime(b"<svg onload=alert(1)></svg>") is None
    assert detect_safe_image_mime(b"<html><script>alert(1)</script></html>") is None

    # Passcode PBKDF2 hashing
    h = hash_passcode("secret1234")
    assert verify_passcode("secret1234", h) is True
    assert verify_passcode("wrongpass", h) is False
    print("[OK] YouTube, media & security helpers")


def test_fifo_queue_and_race_condition_locking():
    room = Room("unit-fifo", "Phòng Unit Test", "host-1")
    t1 = Track("id000000001", "Bài 1", "Ca sĩ 1", 180, "03:00", "u1", "An")
    t2 = Track("id000000002", "Bài 2", "Ca sĩ 2", 200, "03:20", "u2", "Bình")
    t3 = Track("id000000003", "Bài 3", "Ca sĩ 3", 240, "04:00", "u3", "Chi")

    assert t1.thumbnail == "https://i.ytimg.com/vi/id000000001/hqdefault.jpg"

    room.current_track = t1
    room.status = "playing"
    room.queue.extend([t2, t3])

    async def concurrent_advances():
        async def try_advance():
            async with room.lock:
                if room.current_track and room.current_track.uid == t1.uid:
                    return room.advance_track()
                return None

        results = await asyncio.gather(*[try_advance() for _ in range(5)])
        advanced = [r for r in results if r is not None]
        assert len(advanced) == 1
        assert room.current_track.id == "id000000002"
        assert [t.id for t in room.queue] == ["id000000003"]
        assert [t.id for t in room.history] == ["id000000001"]

    asyncio.run(concurrent_advances())
    print("[OK] FIFO queue & race-condition lock")


def test_http_headers_ui_and_media_endpoints():
    client = TestClient(app)

    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json().get("status") == "ok"
    assert r_health.headers.get("x-content-type-options") == "nosniff"
    assert r_health.headers.get("x-frame-options") == "DENY"
    assert "content-security-policy" in {k.lower() for k in r_health.headers.keys()}

    r_index = client.get("/")
    assert r_index.status_code == 200
    assert "Floor 2" in r_index.text
    assert "radio-mode-deck" in r_index.text
    assert "btn-minimize-chat" in r_index.text
    assert "emoji-picker-popover" in r_index.text
    assert "gif-picker-popover" in r_index.text
    assert "chat-image-file-input" in r_index.text
    # Skip buttons must be removed
    assert "btn-vote-skip" not in r_index.text
    assert "btn-force-skip" not in r_index.text

    for asset in (
        "/static/css/style.css",
        "/static/js/socket.js",
        "/static/js/player.js",
        "/static/js/chat.js",
        "/static/js/app.js",
    ):
        assert client.get(asset).status_code == 200

    # Curated GIFs (offline, no query)
    r_gifs = client.get("/api/gifs/search")
    assert r_gifs.status_code == 200
    assert len(r_gifs.json().get("gifs", [])) >= 8

    # Image upload security checks
    assert client.post("/api/chat/upload", content=TINY_PNG).status_code == 401
    tok, _ = _new_session(client)

    # SVG XSS payload rejected
    r_svg = client.post(
        "/api/chat/upload",
        headers={"X-Session-Token": tok, "Content-Type": "image/svg+xml"},
        content=b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>",
    )
    assert r_svg.status_code == 400

    # Valid PNG accepted & served with nosniff
    r_up = client.post(
        "/api/chat/upload",
        headers={"X-Session-Token": tok, "Content-Type": "image/png"},
        content=TINY_PNG,
    )
    assert r_up.status_code == 200
    media_url = r_up.json()["url"]
    r_img = client.get(media_url)
    assert r_img.status_code == 200
    assert r_img.headers.get("content-type") == "image/png"
    assert r_img.headers.get("x-content-type-options") == "nosniff"
    assert r_img.content == TINY_PNG

    print("[OK] HTTP endpoints, UI checks, GIFs & secure image upload")


def test_idor_private_room_protection():
    client = TestClient(app)
    host_tok, _ = _new_session(client)
    other_tok, _ = _new_session(client)

    r = client.post(
        "/api/rooms",
        headers={"X-Session-Token": host_tok},
        json={"name": "Phòng Mật VIP", "is_private": True, "passcode": "pass2026"},
    )
    assert r.status_code == 200
    data = r.json()
    rid = data["room_id"]
    assert data["is_private"] is True
    assert data["invite_token"]

    public_ids = [rm["id"] for rm in client.get("/api/rooms").json()["rooms"]]
    assert rid not in public_ids
    assert client.get(f"/api/rooms/{rid}", headers={"X-Session-Token": other_tok}).status_code == 403

    rejected = False
    try:
        with client.websocket_connect(f"/ws/{rid}?token={other_tok}"):
            pass
    except WebSocketDisconnect as exc:
        rejected = exc.code == 4003
    assert rejected

    assert client.post(
        f"/api/rooms/{rid}/access",
        headers={"X-Session-Token": other_tok},
        json={"passcode": "wrong123"},
    ).status_code == 403

    assert client.post(
        f"/api/rooms/{rid}/access",
        headers={"X-Session-Token": other_tok},
        json={"passcode": "pass2026"},
    ).status_code == 200

    assert client.get(f"/api/rooms/{rid}", headers={"X-Session-Token": other_tok}).status_code == 200
    print("[OK] IDOR & Private Room authorization")


def test_websocket_auth_media_chat_access_control_and_ratelimit():
    client = TestClient(app)
    host_tok, host_uid = _new_session(client)
    member_tok, member_uid = _new_session(client)

    r_create = client.post(
        "/api/rooms",
        headers={"X-Session-Token": host_tok},
        json={"name": "Security Test Room", "dj_mode": True},
    )
    assert r_create.status_code == 200
    rid = r_create.json()["room_id"]

    # Unauthenticated WS rejected (4001)
    unauth_rejected = False
    try:
        with client.websocket_connect(f"/ws/{rid}?token=fake_forged_token"):
            pass
    except WebSocketDisconnect as exc:
        unauth_rejected = exc.code == 4001
    assert unauth_rejected

    # Cross-origin WS rejected (4003)
    cswsh_rejected = False
    try:
        with client.websocket_connect(
            f"/ws/{rid}?token={host_tok}",
            headers={"Origin": "https://evil-attacker.example"},
        ):
            pass
    except WebSocketDisconnect as exc:
        cswsh_rejected = exc.code == 4003
    assert cswsh_rejected

    # Upload an image as host
    up = client.post(
        "/api/chat/upload",
        headers={"X-Session-Token": host_tok, "Content-Type": "image/png"},
        content=TINY_PNG,
    )
    host_media_id = up.json()["media_id"]

    with client.websocket_connect(f"/ws/{rid}?token={host_tok}") as ws:
        ws.send_json({"action": "chat", "text": "Before join"})
        assert ws.receive_json()["type"] == "error"

        ws.send_json({
            "action": "join",
            "name": "Host Real",
            "avatar": "<img src=x onerror=alert(1)>",
            "color": '"><script>alert(1)</script>',
        })
        init_msg = ws.receive_json()
        assert init_msg["is_host"] is True
        joined = ws.receive_json()
        assert joined["user"]["avatar"] == "🎧"
        assert joined["user"]["color"] == "#4f46e5"

        # Send GIF chat
        ws.send_json({"action": "chat", "media_type": "gif", "gif_id": "GK7dGndK4kyOozR9Xk"})
        gif_msg = ws.receive_json()
        assert gif_msg["type"] == "chat"
        assert gif_msg["message"]["media_type"] == "gif"
        assert gif_msg["message"]["media_url"] == "https://media.giphy.com/media/GK7dGndK4kyOozR9Xk/giphy.gif"

        # Send Image chat with caption
        ws.send_json({"action": "chat", "media_type": "image", "media_id": host_media_id, "text": "Ảnh test"})
        img_msg = ws.receive_json()
        assert img_msg["type"] == "chat"
        assert img_msg["message"]["media_type"] == "image"
        assert img_msg["message"]["media_url"] == f"/api/chat/media/{host_media_id}"
        assert img_msg["message"]["text"] == "Ảnh test"

        # Forged GIF ID rejected
        ws.send_json({"action": "chat", "media_type": "gif", "gif_id": "javascript:alert(1)"})
        assert ws.receive_json()["type"] == "error"

        # Add tracks
        ws.send_json({
            "action": "add_track",
            "track": {
                "id": "FN7ALfpGxiI",
                "title": "Bài 1",
                "author": "Artist 1",
                "thumbnail": '" onerror="alert(1)',
                "duration": 278,
                "duration_str": "04:38",
            },
        })
        p1 = ws.receive_json()
        assert p1["current_track"]["thumbnail"] == "https://i.ytimg.com/vi/FN7ALfpGxiI/hqdefault.jpg"

        ws.send_json({
            "action": "add_track",
            "track": {"id": "SlQR9iu09bQ", "title": "Bài 2", "author": "Artist 2", "duration": 235},
        })
        q1 = ws.receive_json()
        track2_uid = q1["queue"][0]["uid"]

    # Non-host member security checks
    rooms[rid].host_id = host_uid
    rooms[rid].users[host_uid] = User(host_uid, "Host Real", "🎧", "#4f46e5")

    with client.websocket_connect(f"/ws/{rid}?token={member_tok}") as mws:
        mws.send_json({"action": "join", "name": "Host Real", "avatar": "🦊", "color": "#ec4899"})
        m_init = mws.receive_json()
        assert m_init["is_host"] is False
        m_joined = mws.receive_json()
        assert m_joined["user"]["name"] == "Host Real (2)"

        # Cannot hijack host's uploaded media_id
        mws.send_json({"action": "chat", "media_type": "image", "media_id": host_media_id})
        assert mws.receive_json()["type"] == "error"

        # Cannot execute Host-only controls
        mws.send_json({"action": "toggle_dj_mode"})
        assert mws.receive_json()["type"] == "error"

        mws.send_json({"action": "control", "sub_action": "pause", "current_time": 10})
        assert mws.receive_json()["type"] == "error"

        mws.send_json({"action": "remove_track", "uid": track2_uid})
        assert mws.receive_json()["type"] == "error"

        mws.send_json({"action": "reorder_queue", "uids": [track2_uid]})
        assert mws.receive_json()["type"] == "error"

        # Premature track_ended blocked
        mws.send_json({"action": "track_ended", "track_id": "FN7ALfpGxiI"})
        assert rooms[rid].current_track.id == "FN7ALfpGxiI"

    print("[OK] WS Auth, Rich Media Chat, Access Control & Anti-Cheat")


def test_database_persistence_across_restart():
    import json
    import httpx
    from services.database import DatabaseManager

    # In-memory fake Supabase PostgREST backend to verify full round-trip persistence offline
    store = {"rooms": {}, "room_queue": [], "room_history": [], "room_messages": [], "chat_media": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        if request.method == "POST":
            payload = json.loads(request.content.decode("utf-8"))
            if table == "rooms":
                store["rooms"][payload["id"]] = payload
            elif table == "room_queue":
                items = payload if isinstance(payload, list) else [payload]
                store["room_queue"].extend(items)
            elif table == "room_history":
                store["room_history"].append(payload)
            elif table == "room_messages":
                store["room_messages"].append(payload)
            elif table == "chat_media":
                store["chat_media"][payload["id"]] = payload
            return httpx.Response(201, json=payload)

        if request.method == "DELETE":
            if table == "room_queue":
                rid = (request.url.params.get("room_id") or "").removeprefix("eq.")
                store["room_queue"] = [x for x in store["room_queue"] if x["room_id"] != rid]
            return httpx.Response(204)

        if request.method == "GET":
            if table == "rooms":
                return httpx.Response(200, json=list(store["rooms"].values()))
            if table == "room_queue":
                rid = (request.url.params.get("room_id") or "").removeprefix("eq.")
                rows = sorted([x for x in store["room_queue"] if x["room_id"] == rid], key=lambda x: x["position"])
                return httpx.Response(200, json=rows)
            if table == "room_history":
                rid = (request.url.params.get("room_id") or "").removeprefix("eq.")
                rows = [x for x in store["room_history"] if x["room_id"] == rid]
                return httpx.Response(200, json=rows)
            if table == "room_messages":
                rid = (request.url.params.get("room_id") or "").removeprefix("eq.")
                rows = [x for x in store["room_messages"] if x["room_id"] == rid]
                return httpx.Response(200, json=rows)
            if table == "chat_media":
                mid = (request.url.params.get("id") or "").removeprefix("eq.")
                item = store["chat_media"].get(mid)
                return httpx.Response(200, json=[item] if item else [])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    db1 = DatabaseManager()
    db1.backend_name = "supabase_rest"
    db1.connected = True
    db1._rest_client = httpx.Client(base_url="https://mock.supabase.co/rest/v1", transport=transport)

    db1.save_room_sync({
        "id": "persist-room",
        "name": "Phòng Lưu Bền Vững",
        "host_id": "u_host1",
        "dj_mode": True,
        "is_private": True,
        "passcode_hash": hash_passcode("9999"),
        "invite_token": "tok_invite_xyz",
        "current_track": {
            "uid": "ct1",
            "id": "FN7ALfpGxiI",
            "title": "Nơi Này Có Anh",
            "author": "Sơn Tùng",
            "thumbnail": "https://i.ytimg.com/vi/FN7ALfpGxiI/hqdefault.jpg",
            "duration": 278,
            "duration_str": "04:38",
            "added_by_id": "u_host1",
            "added_by_name": "Khoa",
        },
        "status": "playing",
        "current_time": 45.0,
    })
    db1.replace_queue_sync("persist-room", [
        {"uid": "q1", "id": "SlQR9iu09bQ", "title": "Bài Chờ 1", "author": "A", "thumbnail": "", "duration": 200, "duration_str": "03:20", "added_by_id": "u1", "added_by_name": "An"},
        {"uid": "q2", "id": "knW7-x7Y7RE", "title": "Bài Chờ 2", "author": "B", "thumbnail": "", "duration": 263, "duration_str": "04:23", "added_by_id": "u2", "added_by_name": "Bình"},
    ])
    db1.add_history_sync("persist-room", {
        "uid": "h1", "id": "kJQP7kiw5Fk", "title": "Bài Đã Nghe", "author": "C", "thumbnail": "", "duration": 282, "duration_str": "04:42", "added_by_id": "u1", "added_by_name": "An",
    })
    db1.add_message_sync("persist-room", {
        "id": "m1", "type": "chat", "user_id": "u1", "user_name": "An", "user_avatar": "🎧", "user_color": "#4f46e5",
        "is_host": False, "text": "Nhạc hay quá!", "media_type": "gif", "media_url": "https://media.giphy.com/media/GK7dGndK4kyOozR9Xk/giphy.gif",
    })
    db1.save_media_sync("abcdef0123456789", TINY_PNG, "image/png", "u1")
    db1.close()

    # Simulate fresh server restart reading from Supabase REST
    db2 = DatabaseManager()
    db2.backend_name = "supabase_rest"
    db2.connected = True
    db2._rest_client = httpx.Client(base_url="https://mock.supabase.co/rest/v1", transport=transport)
    loaded = db2.load_all_rooms()
    assert len(loaded) == 1
    r = loaded[0]
    assert r["id"] == "persist-room"
    assert r["is_private"] is True
    assert r["invite_token"] == "tok_invite_xyz"
    assert r["current_track"]["id"] == "FN7ALfpGxiI"
    assert r["current_time"] == 45.0
    assert [q["video_id"] for q in r["queue"]] == ["SlQR9iu09bQ", "knW7-x7Y7RE"]
    assert len(r["history"]) == 1 and r["history"][0]["video_id"] == "kJQP7kiw5Fk"
    assert len(r["messages"]) == 1 and r["messages"][0]["text"] == "Nhạc hay quá!"
    media = db2.get_media_sync("abcdef0123456789")
    assert media is not None and media[0] == TINY_PNG and media[1] == "image/png"
    db2.close()

    print("[OK] Supabase REST persistence across restart")


def test_ttl_cache_and_http_cache_headers():
    import time as _t
    from services.cache import TTLCache

    c = TTLCache("unit_test", max_size=2, default_ttl=0.15)
    assert c.get("missing") is None
    c.set("a", 1)
    c.set("b", 2)
    assert c.get("a") == 1  # 'a' becomes MRU, 'b' is LRU
    c.set("c", 3)           # evicts 'b'
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3
    st = c.stats()
    assert st["hits"] == 3 and st["misses"] == 2 and st["evictions"] >= 1

    _t.sleep(0.2)
    assert c.get("a") is None  # TTL expired

    client = TestClient(app)
    health = client.get("/api/health").json()
    assert "cache" in health
    for key in ("yt_search", "yt_info", "yt_audio", "gif_search", "public_rooms", "chat_media"):
        assert key in health["cache"], f"Missing cache metric: {key}"

    sug = client.get("/api/suggestions")
    assert sug.status_code == 200
    assert "max-age=300" in sug.headers.get("cache-control", "")

    gifs = client.get("/api/gifs/search")
    assert gifs.status_code == 200
    assert "max-age=" in gifs.headers.get("cache-control", "")

    static_js = client.get("/static/js/app.js")
    assert static_js.status_code == 200
    assert "max-age=" in static_js.headers.get("cache-control", "")

    print("[OK] TTLCache LRU/TTL & HTTP Cache-Control headers")


def test_song_priority_request_and_majority_vote():
    client = TestClient(app)
    rate_limiter._buckets.clear()

    # Create 3 distinct authenticated sessions
    s1 = client.post("/api/session").json()
    s2 = client.post("/api/session").json()
    s3 = client.post("/api/session").json()
    tok1, uid1 = s1["token"], s1["user_id"]
    tok2, uid2 = s2["token"], s2["user_id"]
    tok3, uid3 = s3["token"], s3["user_id"]

    cr = client.post(
        "/api/rooms",
        headers={"X-Session-Token": tok1},
        json={"name": "Priority Test Room", "dj_mode": False, "is_private": False},
    )
    assert cr.status_code == 200
    rid = cr.json()["room_id"]
    room = rooms[rid]

    # Seed current track + 3 queued tracks
    room.current_track = Track("FN7ALfpGxiI", "Playing Now", "A", 200, "03:20", uid1, "U1")
    t1 = Track("SlQR9iu09bQ", "Queue #1", "B", 200, "03:20", uid1, "U1")
    t2 = Track("knW7-x7Y7RE", "Queue #2 Target", "C", 200, "03:20", uid2, "U2")
    t3 = Track("kJQP7kiw5Fk", "Queue #3 Other", "D", 200, "03:20", uid3, "U3")
    room.queue = [t1, t2, t3]

    # Populate 3 active members with 3 distinct IPs (majority needed = 2)
    room.host_id = uid1
    room.users[uid1] = User(uid1, "Host U1", "🎧", "#4f46e5", client_ip="10.0.0.1")
    room.users[uid2] = User(uid2, "Member U2", "🦊", "#ec4899", client_ip="10.0.0.2")
    room.users[uid3] = User(uid3, "Member U3", "🐼", "#10b981", client_ip="10.0.0.3")

    # 1. Requesting priority on #1 (index 0) is rejected
    with client.websocket_connect(f"/ws/{rid}?token={tok2}") as ws2:
        ws2.send_json({"action": "join", "name": "Member U2"})
        ws2.receive_json()  # init_state
        ws2.receive_json()  # user_joined
        room.users[uid2].client_ip = "10.0.0.2"  # restore distinct IP after testclient loopback join

        ws2.send_json({"action": "request_priority", "track_uid": t1.uid})
        err = ws2.receive_json()
        assert err["type"] == "error"

        # 2. Request priority on #2 -> starts vote (1/2), does NOT promote yet
        ws2.send_json({"action": "request_priority", "track_uid": t2.uid})
        pv = ws2.receive_json()
        assert pv["type"] == "priority_vote_updated"
        assert pv["priority_request"]["track_uid"] == t2.uid
        assert pv["priority_request"]["count"] == 1
        assert pv["priority_request"]["needed"] == 2
        assert room.queue[0].uid == t1.uid  # still #1

        # 3. Competing request for #3 is rejected while #2 vote is open
        ws2.send_json({"action": "request_priority", "track_uid": t3.uid})
        err_comp = ws2.receive_json()
        assert err_comp["type"] == "error"

    # Re-pin U2 in room (disconnect removed ws2) and verify Anti-Sybil multi-IP vote counting
    room.users[uid2] = User(uid2, "Member U2", "🦊", "#ec4899", client_ip="10.0.0.2")
    room.priority_request = {
        "track_uid": t2.uid,
        "track_id": t2.id,
        "track_title": t2.title,
        "track_thumbnail": t2.thumbnail,
        "requested_by_id": uid2,
        "requested_by_name": "Member U2",
        "votes": {uid2},
        "created_at": 1e12,
        "expires_at": 9e12,
    }
    # Add a Sybil clone sharing U2's exact IP and voting -> still counts as 1/2!
    room.users["u_sybil"] = User("u_sybil", "Sybil", "🐱", "#6366f1", client_ip="10.0.0.2")
    room.priority_request["votes"].add("u_sybil")
    cnt, needed, _ = room.get_priority_stats()
    assert cnt == 1 and needed == 2, f"Anti-Sybil failed: {cnt}/{needed}"
    room.users.pop("u_sybil", None)
    room.priority_request["votes"].discard("u_sybil")

    # 4. Member U3 (neither requester nor host) cannot cancel U2's request
    with client.websocket_connect(f"/ws/{rid}?token={tok3}") as ws3:
        ws3.send_json({"action": "join", "name": "Member U3"})
        ws3.receive_json()
        ws3.receive_json()
        room.users[uid3].client_ip = "10.0.0.3"

        ws3.send_json({"action": "cancel_priority"})
        assert ws3.receive_json()["type"] == "error"

        # 5. Member U3 votes priority -> reaches 2/2 and atomically promotes t2 to queue[0]!
        ws3.send_json({"action": "vote_priority", "track_uid": t2.uid})
        qu = ws3.receive_json()
        assert qu["type"] == "queue_updated"
        assert qu["priority_request"] is None
        assert [t["uid"] for t in qu["queue"]] == [t2.uid, t1.uid, t3.uid]

    # 6. Host can cancel an active priority request
    room.users[uid2] = User(uid2, "Member U2", "🦊", "#ec4899", client_ip="10.0.0.2")
    room.priority_request = {
        "track_uid": t3.uid,
        "track_id": t3.id,
        "track_title": t3.title,
        "track_thumbnail": t3.thumbnail,
        "requested_by_id": uid2,
        "requested_by_name": "Member U2",
        "votes": {uid2},
        "created_at": 1e12,
        "expires_at": 9e12,
    }
    with client.websocket_connect(f"/ws/{rid}?token={tok1}") as ws1:
        ws1.send_json({"action": "join", "name": "Host U1"})
        ws1.receive_json()
        ws1.receive_json()
        ws1.send_json({"action": "cancel_priority"})
        cancelled = ws1.receive_json()
        assert cancelled["type"] == "priority_vote_updated"
        assert cancelled["priority_request"] is None

    print("[OK] Song priority request, majority vote (>=50%), Anti-Sybil & cancel auth")


def test_ddos_guard_protection():
    from services.ddos import DDoSGuard

    ddos_guard.reset()
    client = TestClient(app)

    # 1. Health endpoint supports GET & HEAD and exposes ddos_guard + keep_alive stats
    h_head = client.head("/api/health", headers={"X-Forwarded-For": "203.0.113.10"})
    assert h_head.status_code == 200
    h_get = client.get("/api/health", headers={"X-Forwarded-For": "203.0.113.10"})
    assert h_get.status_code == 200
    h_json = h_get.json()
    assert "ddos_guard" in h_json and h_json["ddos_guard"]["enabled"] is True
    assert "keep_alive" in h_json

    # 2. Oversized URL rejected with 414
    r_uri = client.get(
        "/api/suggestions?q=" + ("a" * 2200),
        headers={"X-Forwarded-For": "198.51.100.11"},
    )
    assert r_uri.status_code == 414

    # 3. Oversized JSON POST body rejected early with 413
    r_big = client.post(
        "/api/rooms",
        headers={
            "X-Forwarded-For": "198.51.100.12",
            "Content-Type": "application/json",
            "Content-Length": "100000",
        },
        content=b"{" + (b" " * 40000) + b"}",
    )
    assert r_big.status_code == 413

    # 4. Scanner probe trap (3 hits x 2 strikes = 6 strikes -> automatic IP ban -> 429!)
    scanner_ip = "198.51.100.50"
    for probe in ("/.env", "/wp-login.php", "/phpmyadmin"):
        rp = client.get(probe, headers={"X-Forwarded-For": scanner_ip})
        assert rp.status_code == 404
    # Next normal request from that scanner IP is immediately auto-banned (429 + Retry-After)
    banned_resp = client.get("/api/suggestions", headers={"X-Forwarded-For": scanner_ip})
    assert banned_resp.status_code == 429
    assert int(banned_resp.headers.get("retry-after", "0")) > 0
    # Legitimate user on another IP is completely unaffected!
    ok_resp = client.get("/api/suggestions", headers={"X-Forwarded-For": "203.0.113.10"})
    assert ok_resp.status_code == 200

    # 5. Burst flood + WS cross-room connection cap unit check
    g = DDoSGuard(enabled=True, burst_limit=3, burst_window_sec=5.0, max_ws_per_ip=2, ws_handshake_limit=5, strike_threshold=2)
    for _ in range(3):
        ok, st, _, _ = g.check_http_request("192.0.2.1", "/api/rooms", "GET", 20, None)
        assert ok and st == 200
        g.release_http("192.0.2.1")
    ok, st, _, _ = g.check_http_request("192.0.2.1", "/api/rooms", "GET", 20, None)
    assert not ok and st == 429

    # WS concurrent cap per IP across rooms
    ok1, _ = g.check_ws_connect("192.0.2.2")
    ok2, _ = g.check_ws_connect("192.0.2.2")
    ok3, _ = g.check_ws_connect("192.0.2.2")
    assert ok1 and ok2 and not ok3
    g.release_ws("192.0.2.2")
    ok4, _ = g.check_ws_connect("192.0.2.2")
    assert ok4
    g.release_ws("192.0.2.2")
    g.release_ws("192.0.2.2")
    assert g.stats()["active_ws"] == 0

    ddos_guard.reset()
    print("[OK] L7 Anti-DDoS shield (burst, auto-ban, scanner trap, payload/URI & WS caps)")


def test_keep_alive_scheduler():
    from services.keep_alive import keep_alive_targets, keep_alive_url

    keys = ("KEEP_ALIVE_ENABLED", "APP_SERVER_PUBLIC_URL", "KEEP_ALIVE_URL", "RENDER_EXTERNAL_URL", "KEEP_ALIVE_EXTRA_URLS")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)

        # Disabled when explicitly false
        os.environ["KEEP_ALIVE_ENABLED"] = "false"
        os.environ["RENDER_EXTERNAL_URL"] = "https://play-music-floor-2.onrender.com"
        assert keep_alive_url() == ""
        assert keep_alive_targets() == []

        # Default hardcoded URL when no URL env vars are set
        os.environ["KEEP_ALIVE_ENABLED"] = "true"
        os.environ.pop("RENDER_EXTERNAL_URL", None)
        assert keep_alive_url() == "https://play-music-floor-2.onrender.com"

        # Precedence: APP_SERVER_PUBLIC_URL > KEEP_ALIVE_URL > RENDER_EXTERNAL_URL > default
        os.environ["RENDER_EXTERNAL_URL"] = "https://render-auto.onrender.com"
        assert keep_alive_url() == "https://render-auto.onrender.com"

        os.environ["KEEP_ALIVE_URL"] = "https://custom-keep.onrender.com/"
        assert keep_alive_url() == "https://custom-keep.onrender.com"

        os.environ["APP_SERVER_PUBLIC_URL"] = "https://floor2-main.onrender.com/"
        assert keep_alive_url() == "https://floor2-main.onrender.com"

        # Extra peer URLs (self duplicate ignored, /health suffix preserved)
        os.environ["KEEP_ALIVE_EXTRA_URLS"] = (
            "https://sub-songngu.onrender.com, "
            "https://floor2-main.onrender.com, "
            "https://other-app.onrender.com/health"
        )
        targets = keep_alive_targets()
        urls = [u for _, u in targets]
        assert urls == [
            "https://floor2-main.onrender.com/api/health",
            "https://sub-songngu.onrender.com/api/health",
            "https://other-app.onrender.com/health",
        ]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("[OK] Keep-alive server wake-up URL precedence & peer targets")


if __name__ == "__main__":
    try:
        rate_limiter._buckets.clear()
        ddos_guard.reset()
        test_youtube_and_security_helpers()
        test_fifo_queue_and_race_condition_locking()
        test_http_headers_ui_and_media_endpoints()
        test_idor_private_room_protection()
        test_websocket_auth_media_chat_access_control_and_ratelimit()
        test_database_persistence_across_restart()
        test_ttl_cache_and_http_cache_headers()
        test_song_priority_request_and_majority_vote()
        test_ddos_guard_protection()
        test_keep_alive_scheduler()
        print("ALL TESTS PASSED!")
    finally:
        db.close()
