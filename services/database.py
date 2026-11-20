"""Persistent database layer: Supabase PostgreSQL (DATABASE_URL) -> Supabase REST (Secret Key) -> In-Memory fallback."""
import asyncio
import base64
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx


class DatabaseManager:
    """Unified async-safe persistence layer supporting Supabase Postgres and Supabase REST API."""

    def __init__(self):
        self.backend_name: str = "memory"
        self.connected: bool = False
        self.database_url: str = ""
        self.supabase_url: str = ""
        self.supabase_secret_key: str = ""
        self._rest_client: Optional[httpx.Client] = None

    def init(self):
        """Initialize the best available Supabase backend (Postgres > Supabase REST > In-Memory)."""
        self.database_url = (os.getenv("DATABASE_URL") or "").strip()
        self.supabase_url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
        self.supabase_secret_key = (
            os.getenv("SUPABASE_SECRET_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or ""
        ).strip()

        # 1. Priority 1: Direct Supabase / PostgreSQL via DATABASE_URL
        if self.database_url:
            try:
                self._init_postgres()
                self.backend_name = "postgres"
                self.connected = True
                print("[DB] Connected to PostgreSQL (DATABASE_URL)")
                return
            except Exception as e:
                print(f"[DB] PostgreSQL init failed ({e}), trying fallback...")

        # 2. Priority 2: Supabase PostgREST via SUPABASE_URL + SUPABASE_SECRET_KEY
        if self.supabase_url and self.supabase_secret_key:
            try:
                self._init_supabase_rest()
                self.backend_name = "supabase_rest"
                self.connected = True
                print(f"[DB] Connected to Supabase REST ({self.supabase_url})")
                return
            except Exception as e:
                print(f"[DB] Supabase REST init failed ({e}), running in-memory only")

        self.backend_name = "memory"
        self.connected = False
        print("[DB] Supabase not configured — running in-memory only")

    def close(self):
        if self._rest_client:
            try:
                self._rest_client.close()
            except Exception:
                pass
            self._rest_client = None

    # ------------------------------------------------------------------
    # BACKEND INITIALIZERS
    # ------------------------------------------------------------------
    def _pg_connect(self):
        import psycopg
        return psycopg.connect(self.database_url, autocommit=True, connect_timeout=8)

    def _init_postgres(self):
        # Schema is managed externally in Supabase SQL Editor; only verify connection & table presence.
        with self._pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM rooms LIMIT 1")

    def _init_supabase_rest(self):
        self._rest_client = httpx.Client(
            base_url=f"{self.supabase_url}/rest/v1",
            headers={
                "apikey": self.supabase_secret_key,
                "Authorization": f"Bearer {self.supabase_secret_key}",
                "Content-Type": "application/json",
            },
            timeout=8.0,
        )
        r = self._rest_client.get("/rooms", params={"select": "id", "limit": "1"})
        if r.status_code != 200:
            raise RuntimeError(f"Supabase REST check returned HTTP {r.status_code}: {r.text[:180]}")

    def health_info(self) -> Dict[str, Any]:
        return {
            "backend": self.backend_name,
            "connected": self.connected,
            "supabase_configured": bool(self.database_url or (self.supabase_url and self.supabase_secret_key)),
        }

    # ------------------------------------------------------------------
    # LOAD ALL ROOMS ON STARTUP
    # ------------------------------------------------------------------
    def load_all_rooms(self) -> List[Dict[str, Any]]:
        if not self.connected:
            return []
        try:
            if self.backend_name == "postgres":
                return self._pg_load_all_rooms()
            if self.backend_name == "supabase_rest":
                return self._rest_load_all_rooms()
        except Exception as e:
            print(f"[DB] load_all_rooms error: {e}")
        return []

    def _build_room_record(self, row: Dict[str, Any], queue: List[Dict], history: List[Dict], messages: List[Dict]) -> Dict[str, Any]:
        current_track = None
        raw_ct = row.get("current_track_json")
        if raw_ct:
            try:
                current_track = json.loads(raw_ct) if isinstance(raw_ct, str) else raw_ct
            except Exception:
                current_track = None

        return {
            "id": row["id"],
            "name": row["name"],
            "host_id": row.get("host_id") or "",
            "dj_mode": bool(row.get("dj_mode")),
            "is_private": bool(row.get("is_private")),
            "passcode_hash": row.get("passcode_hash") or "",
            "invite_token": row.get("invite_token") or "",
            "current_track": current_track,
            "status": row.get("status") or "idle",
            "current_time": float(row.get("playback_time") if row.get("playback_time") is not None else (row.get("current_time") or 0.0)),
            "updated_at": float(row.get("updated_at") or time.time()),
            "created_at": float(row.get("created_at") or time.time()),
            "queue": queue,
            "history": history,
            "messages": messages,
        }

    def _pg_load_all_rooms(self) -> List[Dict[str, Any]]:
        from psycopg.rows import dict_row
        result = []
        with self._pg_connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM rooms ORDER BY created_at ASC")
                room_rows = cur.fetchall()
                for r in room_rows:
                    rid = r["id"]
                    cur.execute("SELECT * FROM room_queue WHERE room_id = %s ORDER BY position ASC", (rid,))
                    q_rows = cur.fetchall()
                    cur.execute("SELECT * FROM room_history WHERE room_id = %s ORDER BY played_at DESC LIMIT 30", (rid,))
                    h_rows = list(reversed(cur.fetchall()))
                    cur.execute("SELECT * FROM room_messages WHERE room_id = %s ORDER BY created_at DESC LIMIT 100", (rid,))
                    m_rows = list(reversed(cur.fetchall()))
                    result.append(self._build_room_record(r, q_rows, h_rows, m_rows))
        return result

    def _rest_load_all_rooms(self) -> List[Dict[str, Any]]:
        if not self._rest_client:
            return []
        result = []
        r = self._rest_client.get("/rooms", params={"select": "*", "order": "created_at.asc"})
        r.raise_for_status()
        for row in r.json():
            rid = row["id"]
            rq = self._rest_client.get("/room_queue", params={"room_id": f"eq.{rid}", "order": "position.asc"})
            rh = self._rest_client.get("/room_history", params={"room_id": f"eq.{rid}", "order": "played_at.desc", "limit": "30"})
            rm = self._rest_client.get("/room_messages", params={"room_id": f"eq.{rid}", "order": "created_at.desc", "limit": "100"})
            q_rows = rq.json() if rq.status_code == 200 else []
            h_rows = list(reversed(rh.json())) if rh.status_code == 200 else []
            m_rows = list(reversed(rm.json())) if rm.status_code == 200 else []
            result.append(self._build_room_record(row, q_rows, h_rows, m_rows))
        return result

    # ------------------------------------------------------------------
    # WRITE OPERATIONS (SYNC HELPERS + NON-BLOCKING ASYNC WRAPPERS)
    # ------------------------------------------------------------------
    def save_room_sync(self, room_data: Dict[str, Any]):
        if not self.connected:
            return
        ct_json = json.dumps(room_data["current_track"], ensure_ascii=False) if room_data.get("current_track") else None
        vals = {
            "id": room_data["id"],
            "name": room_data["name"],
            "host_id": room_data.get("host_id", ""),
            "dj_mode": bool(room_data.get("dj_mode", False)),
            "is_private": bool(room_data.get("is_private", False)),
            "passcode_hash": room_data.get("passcode_hash", ""),
            "invite_token": room_data.get("invite_token", ""),
            "current_track_json": ct_json,
            "status": room_data.get("status", "idle"),
            "playback_time": float(room_data.get("current_time", 0.0)),
            "updated_at": float(room_data.get("updated_at", time.time())),
            "created_at": float(room_data.get("created_at", time.time())),
        }
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO rooms (id, name, host_id, dj_mode, is_private, passcode_hash, invite_token,
                                current_track_json, status, playback_time, updated_at, created_at)
                            VALUES (%(id)s, %(name)s, %(host_id)s, %(dj_mode)s, %(is_private)s, %(passcode_hash)s,
                                %(invite_token)s, %(current_track_json)s, %(status)s, %(playback_time)s, %(updated_at)s, %(created_at)s)
                            ON CONFLICT (id) DO UPDATE SET
                                name = EXCLUDED.name,
                                host_id = EXCLUDED.host_id,
                                dj_mode = EXCLUDED.dj_mode,
                                is_private = EXCLUDED.is_private,
                                passcode_hash = EXCLUDED.passcode_hash,
                                invite_token = EXCLUDED.invite_token,
                                current_track_json = EXCLUDED.current_track_json,
                                status = EXCLUDED.status,
                                playback_time = EXCLUDED.playback_time,
                                updated_at = EXCLUDED.updated_at
                            """,
                            vals,
                        )
            elif self.backend_name == "supabase_rest" and self._rest_client:
                self._rest_client.post(
                    "/rooms",
                    headers={"Prefer": "resolution=merge-duplicates"},
                    json=vals,
                )
        except Exception as e:
            print(f"[DB] save_room error: {e}")

    def replace_queue_sync(self, room_id: str, queue_list: List[Dict[str, Any]]):
        if not self.connected:
            return
        rows = [
            {
                "uid": t["uid"],
                "room_id": room_id,
                "position": idx,
                "video_id": t["id"],
                "title": t["title"],
                "author": t.get("author", "YouTube"),
                "thumbnail": t.get("thumbnail", ""),
                "duration": int(t.get("duration") or 0),
                "duration_str": t.get("duration_str", ""),
                "added_by_id": t.get("added_by_id", ""),
                "added_by_name": t.get("added_by_name", "Khách"),
                "added_at": float(t.get("added_at") or time.time()),
            }
            for idx, t in enumerate(queue_list)
        ]
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM room_queue WHERE room_id = %s", (room_id,))
                        for r in rows:
                            cur.execute(
                                """
                                INSERT INTO room_queue (uid, room_id, position, video_id, title, author, thumbnail,
                                    duration, duration_str, added_by_id, added_by_name, added_at)
                                VALUES (%(uid)s, %(room_id)s, %(position)s, %(video_id)s, %(title)s, %(author)s,
                                    %(thumbnail)s, %(duration)s, %(duration_str)s, %(added_by_id)s, %(added_by_name)s, %(added_at)s)
                                """,
                                r,
                            )
            elif self.backend_name == "supabase_rest" and self._rest_client:
                self._rest_client.delete("/room_queue", params={"room_id": f"eq.{room_id}"})
                if rows:
                    self._rest_client.post("/room_queue", json=rows)
        except Exception as e:
            print(f"[DB] replace_queue error: {e}")

    def add_history_sync(self, room_id: str, track: Dict[str, Any]):
        if not self.connected:
            return
        row = {
            "id": f"{track['uid']}_{int(time.time() * 1000)}",
            "room_id": room_id,
            "video_id": track["id"],
            "title": track["title"],
            "author": track.get("author", "YouTube"),
            "thumbnail": track.get("thumbnail", ""),
            "duration": int(track.get("duration") or 0),
            "duration_str": track.get("duration_str", ""),
            "added_by_id": track.get("added_by_id", ""),
            "added_by_name": track.get("added_by_name", "Khách"),
            "played_at": time.time(),
        }
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO room_history (id, room_id, video_id, title, author, thumbnail,
                                duration, duration_str, added_by_id, added_by_name, played_at)
                            VALUES (%(id)s, %(room_id)s, %(video_id)s, %(title)s, %(author)s, %(thumbnail)s,
                                %(duration)s, %(duration_str)s, %(added_by_id)s, %(added_by_name)s, %(played_at)s)
                            """,
                            row,
                        )
                        cur.execute(
                            """
                            DELETE FROM room_history
                            WHERE room_id = %s AND id NOT IN (
                                SELECT id FROM room_history WHERE room_id = %s ORDER BY played_at DESC LIMIT 50
                            )
                            """,
                            (room_id, room_id),
                        )
            elif self.backend_name == "supabase_rest" and self._rest_client:
                self._rest_client.post("/room_history", json=row)
        except Exception as e:
            print(f"[DB] add_history error: {e}")

    def add_message_sync(self, room_id: str, msg: Dict[str, Any]):
        if not self.connected:
            return
        row = {
            "id": f"{msg.get('id', 'm')}_{int(time.time() * 1000000) % 1000000}",
            "room_id": room_id,
            "msg_type": msg.get("type", "chat"),
            "user_id": msg.get("user_id", ""),
            "user_name": msg.get("user_name", ""),
            "user_avatar": msg.get("user_avatar", "🎧"),
            "user_color": msg.get("user_color", "#4f46e5"),
            "is_host": bool(msg.get("is_host", False)),
            "text": msg.get("text", ""),
            "media_type": msg.get("media_type"),
            "media_url": msg.get("media_url"),
            "created_at": float(msg.get("timestamp") or time.time()),
        }
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO room_messages (id, room_id, msg_type, user_id, user_name, user_avatar,
                                user_color, is_host, text, media_type, media_url, created_at)
                            VALUES (%(id)s, %(room_id)s, %(msg_type)s, %(user_id)s, %(user_name)s, %(user_avatar)s,
                                %(user_color)s, %(is_host)s, %(text)s, %(media_type)s, %(media_url)s, %(created_at)s)
                            """,
                            row,
                        )
                        cur.execute(
                            """
                            DELETE FROM room_messages
                            WHERE room_id = %s AND id NOT IN (
                                SELECT id FROM room_messages WHERE room_id = %s ORDER BY created_at DESC LIMIT 150
                            )
                            """,
                            (room_id, room_id),
                        )
            elif self.backend_name == "supabase_rest" and self._rest_client:
                self._rest_client.post("/room_messages", json=row)
        except Exception as e:
            print(f"[DB] add_message error: {e}")

    def save_media_sync(self, media_id: str, raw: bytes, mime_type: str, uploader_id: str):
        if not self.connected:
            return
        row = {
            "id": media_id,
            "mime_type": mime_type,
            "data_b64": base64.b64encode(raw).decode("ascii"),
            "uploader_id": uploader_id,
            "created_at": time.time(),
        }
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO chat_media (id, mime_type, data_b64, uploader_id, created_at)
                            VALUES (%(id)s, %(mime_type)s, %(data_b64)s, %(uploader_id)s, %(created_at)s)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            row,
                        )
                        cur.execute(
                            """
                            DELETE FROM chat_media
                            WHERE id NOT IN (SELECT id FROM chat_media ORDER BY created_at DESC LIMIT 80)
                            """
                        )
            elif self.backend_name == "supabase_rest" and self._rest_client:
                self._rest_client.post("/chat_media", json=row)
        except Exception as e:
            print(f"[DB] save_media error: {e}")

    def get_media_sync(self, media_id: str) -> Optional[Tuple[bytes, str, str]]:
        if not self.connected:
            return None
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT mime_type, data_b64, uploader_id FROM chat_media WHERE id = %s", (media_id,))
                        row = cur.fetchone()
                        if row:
                            return base64.b64decode(row[1]), row[0], row[2]
            elif self.backend_name == "supabase_rest" and self._rest_client:
                r = self._rest_client.get("/chat_media", params={"id": f"eq.{media_id}", "select": "mime_type,data_b64,uploader_id"})
                if r.status_code == 200 and r.json():
                    item = r.json()[0]
                    return base64.b64decode(item["data_b64"]), item["mime_type"], item["uploader_id"]
        except Exception as e:
            print(f"[DB] get_media error: {e}")
        return None

    # ------------------------------------------------------------------
    # SHARED LYRICS SYNC OFFSETS (per YouTube video, calibrated by room hosts)
    # Optional table `lyrics_offsets`; when it does not exist we silently stay in-memory.
    # ------------------------------------------------------------------
    _lyrics_offsets_table_missing: bool = False

    def get_lyrics_offset_sync(self, video_id: str) -> Optional[float]:
        if not self.connected or self._lyrics_offsets_table_missing:
            return None
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT offset_sec FROM lyrics_offsets WHERE video_id = %s", (video_id,))
                        row = cur.fetchone()
                        return float(row[0]) if row else None
            elif self.backend_name == "supabase_rest" and self._rest_client:
                r = self._rest_client.get(
                    "/lyrics_offsets", params={"video_id": f"eq.{video_id}", "select": "offset_sec"}
                )
                if r.status_code == 200:
                    data = r.json()
                    return float(data[0]["offset_sec"]) if data else None
                if r.status_code == 404 or "lyrics_offsets" in r.text:
                    self._lyrics_offsets_table_missing = True
        except Exception as e:
            if "lyrics_offsets" in str(e):
                self._lyrics_offsets_table_missing = True
            else:
                print(f"[DB] get_lyrics_offset error: {e}")
        return None

    def save_lyrics_offset_sync(self, video_id: str, offset_sec: float, updated_by: str = ""):
        if not self.connected or self._lyrics_offsets_table_missing:
            return
        row = {"video_id": video_id, "offset_sec": float(offset_sec), "updated_by": updated_by[:40], "updated_at": time.time()}
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO lyrics_offsets (video_id, offset_sec, updated_by, updated_at)
                            VALUES (%(video_id)s, %(offset_sec)s, %(updated_by)s, %(updated_at)s)
                            ON CONFLICT (video_id) DO UPDATE
                            SET offset_sec = EXCLUDED.offset_sec, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
                            """,
                            row,
                        )
            elif self.backend_name == "supabase_rest" and self._rest_client:
                r = self._rest_client.post(
                    "/lyrics_offsets",
                    json=row,
                    headers={"Prefer": "resolution=merge-duplicates"},
                    params={"on_conflict": "video_id"},
                )
                if r.status_code == 404 or (r.status_code >= 400 and "lyrics_offsets" in r.text):
                    self._lyrics_offsets_table_missing = True
        except Exception as e:
            if "lyrics_offsets" in str(e):
                self._lyrics_offsets_table_missing = True
            else:
                print(f"[DB] save_lyrics_offset error: {e}")

    def schedule_save_lyrics_offset(self, video_id: str, offset_sec: float, updated_by: str = ""):
        if not self.connected:
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, self.save_lyrics_offset_sync, video_id, offset_sec, updated_by)
        except RuntimeError:
            self.save_lyrics_offset_sync(video_id, offset_sec, updated_by)

    # ------------------------------------------------------------------
    # GENERIC JSON DOCUMENTS (member_profiles / room_memories for AI memory)
    # Optional tables; when missing we silently stay in-memory.
    # ------------------------------------------------------------------
    _missing_json_tables: set = set()

    def _json_table_missing(self, table: str, status: int, text: str) -> bool:
        if status == 404 or (status >= 400 and table in (text or "")):
            self._missing_json_tables.add(table)
            return True
        return False

    def get_json_doc_sync(self, table: str, key_col: str, key: str) -> Optional[Dict[str, Any]]:
        if not self.connected or table in self._missing_json_tables:
            return None
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(f"SELECT data FROM {table} WHERE {key_col} = %s", (key,))
                        row = cur.fetchone()
                        if not row:
                            return None
                        data = row[0]
                        return data if isinstance(data, dict) else json.loads(data)
            elif self.backend_name == "supabase_rest" and self._rest_client:
                r = self._rest_client.get(f"/{table}", params={key_col: f"eq.{key}", "select": "data"})
                if r.status_code == 200:
                    rows = r.json()
                    if rows:
                        data = rows[0].get("data")
                        return data if isinstance(data, dict) else json.loads(data or "{}")
                    return None
                self._json_table_missing(table, r.status_code, r.text)
        except Exception as e:
            if table in str(e):
                self._missing_json_tables.add(table)
            else:
                print(f"[DB] get_json_doc {table} error: {e}")
        return None

    def upsert_json_doc_sync(self, table: str, key_col: str, key: str, data: Dict[str, Any]):
        if not self.connected or table in self._missing_json_tables:
            return
        row = {key_col: key, "data": data, "updated_at": time.time()}
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            INSERT INTO {table} ({key_col}, data, updated_at) VALUES (%s, %s::jsonb, %s)
                            ON CONFLICT ({key_col}) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at
                            """,
                            (key, json.dumps(data, ensure_ascii=False), row["updated_at"]),
                        )
            elif self.backend_name == "supabase_rest" and self._rest_client:
                r = self._rest_client.post(
                    f"/{table}",
                    json=row,
                    headers={"Prefer": "resolution=merge-duplicates"},
                    params={"on_conflict": key_col},
                )
                if r.status_code >= 400:
                    if not self._json_table_missing(table, r.status_code, r.text):
                        print(f"[DB] upsert_json_doc {table} HTTP {r.status_code}: {r.text[:160]}")
        except Exception as e:
            if table in str(e):
                self._missing_json_tables.add(table)
            else:
                print(f"[DB] upsert_json_doc {table} error: {e}")

    def delete_json_doc_sync(self, table: str, key_col: str, key: str):
        if not self.connected or table in self._missing_json_tables:
            return
        try:
            if self.backend_name == "postgres":
                with self._pg_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(f"DELETE FROM {table} WHERE {key_col} = %s", (key,))
            elif self.backend_name == "supabase_rest" and self._rest_client:
                self._rest_client.delete(f"/{table}", params={key_col: f"eq.{key}"})
        except Exception as e:
            print(f"[DB] delete_json_doc {table} error: {e}")

    # Non-blocking async fire-and-forget helpers for hot WebSocket paths
    def schedule_save_room(self, room_data: Dict[str, Any]):
        if not self.connected:
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, self.save_room_sync, room_data)
        except RuntimeError:
            self.save_room_sync(room_data)

    def schedule_replace_queue(self, room_id: str, queue_list: List[Dict[str, Any]]):
        if not self.connected:
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, self.replace_queue_sync, room_id, queue_list)
        except RuntimeError:
            self.replace_queue_sync(room_id, queue_list)

    def schedule_add_history(self, room_id: str, track: Dict[str, Any]):
        if not self.connected:
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, self.add_history_sync, room_id, track)
        except RuntimeError:
            self.add_history_sync(room_id, track)

    def schedule_add_message(self, room_id: str, msg: Dict[str, Any]):
        if not self.connected:
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, self.add_message_sync, room_id, msg)
        except RuntimeError:
            self.add_message_sync(room_id, msg)

    def schedule_save_media(self, media_id: str, raw: bytes, mime_type: str, uploader_id: str):
        if not self.connected:
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, self.save_media_sync, media_id, raw, mime_type, uploader_id)
        except RuntimeError:
            self.save_media_sync(media_id, raw, mime_type, uploader_id)
