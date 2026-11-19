"""Offline smoke tests for Floor 2 Music Room (no external network calls).

Run: python tests/smoke_test.py
"""
import sys
from pathlib import Path

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from main import app, Room, Track
from services.youtube import extract_video_id, format_duration, parse_duration_str


def test_youtube_helpers():
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
    print("[OK] YouTube helpers")


def test_fifo_queue_unit():
    room = Room("unit-fifo", "Phòng Unit Test", "host-1")
    t1 = Track("id000000001", "Bài 1", "Ca sĩ 1", "", 180, "03:00", "u1", "An")
    t2 = Track("id000000002", "Bài 2", "Ca sĩ 2", "", 200, "03:20", "u2", "Bình")
    t3 = Track("id000000003", "Bài 3", "Ca sĩ 3", "", 240, "04:00", "u3", "Chi")

    room.current_track = t1
    room.status = "playing"
    room.queue.extend([t2, t3])

    nxt1 = room.advance_track()
    assert nxt1 is not None and nxt1.id == "id000000002"
    assert [t.id for t in room.queue] == ["id000000003"]
    assert [t.id for t in room.history] == ["id000000001"]

    nxt2 = room.advance_track()
    assert nxt2 is not None and nxt2.id == "id000000003"
    assert room.queue == []

    nxt3 = room.advance_track()
    assert nxt3 is None
    assert room.status == "idle"
    print("[OK] FIFO queue & history")


def test_http_endpoints():
    client = TestClient(app)

    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json().get("status") == "ok"

    r_index = client.get("/")
    assert r_index.status_code == 200
    assert "Floor 2" in r_index.text
    assert "radio-mode-deck" in r_index.text

    for asset in (
        "/static/css/style.css",
        "/static/js/socket.js",
        "/static/js/player.js",
        "/static/js/chat.js",
        "/static/js/app.js",
    ):
        ra = client.get(asset)
        assert ra.status_code == 200, f"Asset failed: {asset}"

    r_rooms = client.get("/api/rooms")
    assert r_rooms.status_code == 200
    assert isinstance(r_rooms.json().get("rooms"), list)

    r_create = client.post("/api/rooms", json={"name": "CI Test Room", "dj_mode": False})
    assert r_create.status_code == 200
    rid = r_create.json().get("room_id")
    assert rid

    r_get = client.get(f"/api/rooms/{rid}")
    assert r_get.status_code == 200
    assert r_get.json()["room"]["name"] == "CI Test Room"

    r_sug = client.get("/api/suggestions")
    assert r_sug.status_code == 200
    assert len(r_sug.json().get("suggestions", [])) >= 4
    print("[OK] HTTP & static endpoints")


def test_websocket_room_flow():
    client = TestClient(app)

    with client.websocket_connect("/ws/ci-ws-room/u-ci-1") as ws:
        ws.send_json({"action": "join", "name": "CI Host", "avatar": "🎧", "color": "#4f46e5"})
        init_msg = ws.receive_json()
        assert init_msg["type"] == "init_state"
        assert init_msg["is_host"] is True

        joined_msg = ws.receive_json()
        assert joined_msg["type"] == "user_joined"

        # Chat
        ws.send_json({"action": "chat", "text": "Hello CI!"})
        chat_msg = ws.receive_json()
        assert chat_msg["type"] == "chat"
        assert chat_msg["message"]["text"] == "Hello CI!"

        # Reaction
        ws.send_json({"action": "reaction", "emoji": "🔥"})
        react_msg = ws.receive_json()
        assert react_msg["type"] == "reaction"
        assert react_msg["emoji"] == "🔥"

        # Add Track 1 -> plays immediately
        ws.send_json({
            "action": "add_track",
            "track": {
                "id": "FN7ALfpGxiI",
                "title": "Bài 1",
                "author": "Artist 1",
                "duration": 200,
                "duration_str": "03:20",
            },
        })
        p1 = ws.receive_json()
        assert p1["type"] == "playback_changed"
        assert p1["current_track"]["id"] == "FN7ALfpGxiI"

        # Add Track 2 -> enters FIFO queue
        ws.send_json({
            "action": "add_track",
            "track": {
                "id": "SlQR9iu09bQ",
                "title": "Bài 2",
                "author": "Artist 2",
                "duration": 235,
                "duration_str": "03:55",
            },
        })
        q1 = ws.receive_json()
        assert q1["type"] == "queue_updated"
        assert len(q1["queue"]) == 1
        assert q1["queue"][0]["id"] == "SlQR9iu09bQ"

        # Pause control
        ws.send_json({"action": "control", "sub_action": "pause", "current_time": 12.5})
        sync_msg = ws.receive_json()
        assert sync_msg["type"] == "playback_sync"
        assert sync_msg["status"] == "paused"

        # Toggle DJ mode
        ws.send_json({"action": "toggle_dj_mode"})
        dj_msg = ws.receive_json()
        assert dj_msg["type"] == "dj_mode_changed"
        assert dj_msg["dj_mode"] is True

        # Track 1 ended -> advances FIFO to Track 2
        ws.send_json({"action": "track_ended", "track_id": "FN7ALfpGxiI"})
        p2 = ws.receive_json()
        assert p2["type"] == "playback_changed"
        assert p2["current_track"]["id"] == "SlQR9iu09bQ"
        assert p2["queue"] == []

    print("[OK] WebSocket room lifecycle")


if __name__ == "__main__":
    test_youtube_helpers()
    test_fifo_queue_unit()
    test_http_endpoints()
    test_websocket_room_flow()
    print("ALL SMOKE TESTS PASSED!")
