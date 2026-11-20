"""Long-term memory & personalization for '@Em bé floor 2'.

- Member profiles (keyed by a stable, unguessable device-derived profile_id, NOT by display name):
  music taste learned from behaviour (add / finish / reaction / skip), recent tracks, dislikes,
  and facts the member told the bot.
- Room memory: nicknames, inside jokes, plans, and summaries of previous sessions.

Everything lives in memory first and is persisted (debounced) to Supabase tables `member_profiles`
and `room_memories` when they exist; without them the feature still works until the server restarts.
"""
import asyncio
import hashlib
import math
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

PROFILE_TABLE = "member_profiles"
ROOM_TABLE = "room_memories"

MAX_PROFILE_TRACKS = 40
MAX_PROFILE_ARTISTS = 60
MAX_DISLIKED = 60
MAX_PROFILE_FACTS = 20
MAX_ROOM_FACTS = 30
MAX_ROOM_SESSIONS = 6
TASTE_HALF_LIFE_DAYS = 30.0
SAVE_DEBOUNCE_SEC = 8.0
VN_TZ = timezone(timedelta(hours=7))

# Behaviour signals -> taste score deltas
SIGNAL_WEIGHTS = {
    "add": 2.0,          # the member chose this song
    "finish": 0.6,       # listened to the end while in the room
    "react": 0.8,        # positive reaction while it played
    "skip_early": -2.0,  # voted to skip within the first minute
    "skip": -0.8,        # voted to skip later
}
POSITIVE_REACTIONS = {"🔥", "❤️", "❤", "😍", "🥰", "👏", "💯", "🎉", "🤩", "😭", "🙌", "💖", "💕", "✨", "🎶", "🎵", "🕺", "💃"}

_PROFILE_PEPPER = os.getenv("PROFILE_ID_PEPPER", "floor2-em-be-memory-v1")
_DEVICE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{24,128}$")


def profile_id_from_device_key(device_key: Optional[str]) -> str:
    """Stable, unguessable profile id derived from the browser's secret device key ('' if invalid)."""
    key = (device_key or "").strip()
    if not _DEVICE_KEY_RE.match(key):
        return ""
    digest = hashlib.sha256(f"{_PROFILE_PEPPER}:{key}".encode("utf-8")).hexdigest()
    return f"p_{digest[:32]}"


def _now() -> float:
    return time.time()


VN_WEEKDAYS = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")


def now_label_vn() -> str:
    """'23:10, Thứ Sáu 20/11/2026' (Vietnam time) so the model can resolve 'hôm qua', 'tối nay', 'thứ 6'."""
    d = datetime.now(VN_TZ)
    return f"{d.strftime('%H:%M')}, {VN_WEEKDAYS[d.weekday()]} {d.strftime('%d/%m/%Y')}"


def parse_vn_datetime(text: str) -> Optional[float]:
    """'2026-11-20 21:00' / '2026-11-20T21:00' / '21:00 20/11/2026' (Vietnam time) -> epoch seconds."""
    s = (text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%H:%M %d/%m/%Y", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=VN_TZ).timestamp()
        except ValueError:
            continue
    return None


def relative_day_label(date_str: str, today: Optional[Any] = None) -> str:
    """'hôm qua (19/11/2026)', '3 ngày trước (17/11/2026)' for a stored dd/mm/YYYY date."""
    try:
        d = datetime.strptime(date_str, "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return date_str or "trước đó"
    today = today or datetime.now(VN_TZ).date()
    delta = (today - d).days
    if delta == 1:
        rel = "hôm qua"
    elif delta == 2:
        rel = "hôm kia"
    elif 2 < delta < 7:
        rel = f"{delta} ngày trước"
    elif delta >= 7:
        rel = f"{delta // 7} tuần trước"
    else:
        rel = "hôm nay"
    return f"{rel} ({VN_WEEKDAYS[d.weekday()]} {date_str})"


def artist_key(author: str) -> str:
    s = unicodedata.normalize("NFD", (author or "").lower()).replace("đ", "d")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\b(official|topic|vevo|music|channel|entertainment|records)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s[:60]


def _clean_artist_name(author: str) -> str:
    name = re.sub(r"\s*-\s*Topic$", "", author or "", flags=re.IGNORECASE)
    name = re.sub(r"\b(Official|VEVO)\b", "", name, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", name).strip(" -|")[:50] or (author or "")[:50]


def _decayed(score: float, ts: float, now: float) -> float:
    days = max(0.0, (now - ts) / 86400.0)
    return score * math.pow(0.5, days / TASTE_HALF_LIFE_DAYS)


def _new_profile(pid: str) -> Dict[str, Any]:
    return {
        "profile_id": pid,
        "names": [],
        "created_at": _now(),
        "last_seen": _now(),
        "visits": 0,
        "artists": {},
        "tracks": [],
        "disliked": {},
        "facts": [],
    }


def _new_room_memory(room_id: str) -> Dict[str, Any]:
    return {"room_id": room_id, "facts": [], "sessions": [], "created_at": _now()}


def _norm_fact(text: str) -> str:
    s = unicodedata.normalize("NFD", (text or "").lower()).replace("đ", "d")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _fact_overlap(a: str, b: str) -> float:
    wa, wb = set(_norm_fact(a).split()), set(_norm_fact(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


class MemoryStore:
    """In-memory store with lazy DB loading and debounced persistence."""

    def __init__(self) -> None:
        self.db: Any = None
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._rooms: Dict[str, Dict[str, Any]] = {}
        self._loading: Dict[str, "asyncio.Task[None]"] = {}
        self._dirty: Dict[Tuple[str, str], float] = {}
        self._flush_task: Optional["asyncio.Task[None]"] = None

    def bind_db(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------ loading / saving
    async def _load(self, kind: str, key: str) -> None:
        table, key_col, cache = (
            (PROFILE_TABLE, "profile_id", self._profiles) if kind == "profile" else (ROOM_TABLE, "room_id", self._rooms)
        )
        data: Optional[Dict[str, Any]] = None
        if self.db is not None and not key.startswith("anon_"):
            try:
                loop = asyncio.get_running_loop()
                data = await asyncio.wait_for(
                    loop.run_in_executor(None, self.db.get_json_doc_sync, table, key_col, key), timeout=5.0
                )
            except Exception:
                data = None
        if key not in cache:
            base = _new_profile(key) if kind == "profile" else _new_room_memory(key)
            if isinstance(data, dict):
                base.update(data)
            cache[key] = base

    async def ensure_profile(self, pid: str) -> Dict[str, Any]:
        if pid in self._profiles:
            return self._profiles[pid]
        task = self._loading.get(f"p:{pid}")
        if task is None or task.done():
            task = asyncio.create_task(self._load("profile", pid))
            self._loading[f"p:{pid}"] = task
        await asyncio.shield(task)
        self._loading.pop(f"p:{pid}", None)
        return self._profiles[pid]

    async def ensure_room(self, room_id: str) -> Dict[str, Any]:
        if room_id in self._rooms:
            return self._rooms[room_id]
        task = self._loading.get(f"r:{room_id}")
        if task is None or task.done():
            task = asyncio.create_task(self._load("room", room_id))
            self._loading[f"r:{room_id}"] = task
        await asyncio.shield(task)
        self._loading.pop(f"r:{room_id}", None)
        return self._rooms[room_id]

    def _mark_dirty(self, kind: str, key: str) -> None:
        if key.startswith("anon_"):
            return
        self._dirty[(kind, key)] = _now()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = loop.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        await asyncio.sleep(SAVE_DEBOUNCE_SEC)
        await self.flush()

    async def flush(self) -> None:
        if not self._dirty or self.db is None:
            self._dirty.clear()
            return
        items = list(self._dirty.keys())
        self._dirty.clear()
        loop = asyncio.get_running_loop()
        for kind, key in items:
            doc = self._profiles.get(key) if kind == "profile" else self._rooms.get(key)
            if not doc:
                continue
            table, key_col = (PROFILE_TABLE, "profile_id") if kind == "profile" else (ROOM_TABLE, "room_id")
            try:
                await loop.run_in_executor(None, self.db.upsert_json_doc_sync, table, key_col, key, dict(doc))
            except Exception as exc:
                print(f"[memory] save {kind} {key[:10]} failed: {exc}")

    def _with_profile(self, pid: str, fn: Callable[[Dict[str, Any]], None]) -> None:
        """Apply a mutation now if loaded, otherwise after loading (never overwrite unloaded DB data)."""
        if not pid:
            return
        prof = self._profiles.get(pid)
        if prof is not None:
            fn(prof)
            self._mark_dirty("profile", pid)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _later() -> None:
            p = await self.ensure_profile(pid)
            fn(p)
            self._mark_dirty("profile", pid)

        loop.create_task(_later())

    # ------------------------------------------------------------------ profile updates
    async def touch_profile(self, pid: str, display_name: str) -> None:
        if not pid:
            return
        prof = await self.ensure_profile(pid)
        name = (display_name or "").strip()[:30]
        if name:
            names = [n for n in prof.get("names", []) if n != name]
            prof["names"] = ([name] + names)[:5]
        # A new visit only after 30 min away (WebSocket reconnects / page reloads don't count)
        if _now() - float(prof.get("last_seen", 0) or 0) > 1800 or int(prof.get("visits", 0)) == 0:
            if int(prof.get("visits", 0)) > 0:
                prof["prev_seen"] = float(prof.get("last_seen", 0) or 0)  # "last time we saw you"
            prof["visits"] = int(prof.get("visits", 0)) + 1
        prof["last_seen"] = _now()
        self._mark_dirty("profile", pid)

    def record_signal(self, pid: str, track: Dict[str, Any], kind: str) -> None:
        """Learn taste from behaviour: add / finish / react / skip / skip_early."""
        weight = SIGNAL_WEIGHTS.get(kind)
        vid = str(track.get("id") or "")
        if not pid or weight is None or not vid:
            return

        def _apply(prof: Dict[str, Any]) -> None:
            now = _now()
            akey = artist_key(track.get("author", ""))
            if akey:
                artists = prof.setdefault("artists", {})
                entry = artists.get(akey) or {"name": _clean_artist_name(track.get("author", "")), "score": 0.0, "ts": now}
                entry["score"] = round(_decayed(float(entry.get("score", 0.0)), float(entry.get("ts", now)), now) + weight, 3)
                entry["ts"] = now
                artists[akey] = entry
                if len(artists) > MAX_PROFILE_ARTISTS:
                    weakest = sorted(artists.items(), key=lambda kv: abs(_decayed(kv[1]["score"], kv[1]["ts"], now)))
                    for k, _ in weakest[: len(artists) - MAX_PROFILE_ARTISTS]:
                        artists.pop(k, None)

            tracks = [t for t in prof.get("tracks", []) if t.get("id") != vid]
            prev = next((t for t in prof.get("tracks", []) if t.get("id") == vid), None)
            score = (float(prev.get("score", 0.0)) if prev else 0.0) + weight
            tracks.insert(0, {
                "id": vid,
                "title": str(track.get("title", ""))[:120],
                "author": str(track.get("author", ""))[:60],
                "duration": int(track.get("duration") or 0),
                "duration_str": str(track.get("duration_str", ""))[:10],
                "score": round(score, 2),
                "last": kind,
                "ts": now,
            })
            prof["tracks"] = tracks[:MAX_PROFILE_TRACKS]

            disliked = prof.setdefault("disliked", {})
            if kind == "skip_early" or score <= -1.5:
                disliked[vid] = now
                if len(disliked) > MAX_DISLIKED:
                    for k, _ in sorted(disliked.items(), key=lambda kv: kv[1])[: len(disliked) - MAX_DISLIKED]:
                        disliked.pop(k, None)
            elif kind in ("add", "react") and vid in disliked:
                disliked.pop(vid, None)  # changed their mind

        self._with_profile(pid, _apply)

    def add_profile_fact(self, pid: str, fact: str, source: str = "chat") -> bool:
        text = re.sub(r"\s+", " ", (fact or "").strip())[:200]
        if not pid or len(text) < 4:
            return False

        def _apply(prof: Dict[str, Any]) -> None:
            existing = prof.get("facts", [])
            dup = next((f for f in existing if _fact_overlap(f.get("text", ""), text) >= 0.8), None)
            if dup is not None and source == "auto":
                # Auto-extracted near-duplicates only refresh recency; never overwrite what a member told us
                dup["ts"] = _now()
                return
            facts = [f for f in existing if f is not dup]
            facts.insert(0, {"text": text, "ts": _now(), "source": source})
            prof["facts"] = facts[:MAX_PROFILE_FACTS]

        self._with_profile(pid, _apply)
        return True

    def forget_profile(self, pid: str) -> None:
        """Wipe everything the bot learned about this member (keeps nothing)."""
        if not pid:
            return
        self._profiles[pid] = _new_profile(pid)
        if self.db is not None and not pid.startswith("anon_"):
            try:
                asyncio.get_running_loop().run_in_executor(None, self.db.delete_json_doc_sync, PROFILE_TABLE, "profile_id", pid)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------ room memory
    def add_room_fact(self, room_id: str, fact: str, who: str = "") -> bool:
        text = re.sub(r"\s+", " ", (fact or "").strip())[:220]
        mem = self._rooms.get(room_id)
        if mem is None or len(text) < 4:
            return False
        facts = [f for f in mem.get("facts", []) if _fact_overlap(f.get("text", ""), text) < 0.8]
        facts.insert(0, {"text": text, "ts": _now(), "who": (who or "")[:30]})
        mem["facts"] = facts[:MAX_ROOM_FACTS]
        self._mark_dirty("room", room_id)
        return True

    # ------------------------------------------------------------------ plans / reminders
    def add_plan(self, room_id: str, when_ts: float, what: str, who: str = "") -> Optional[Dict[str, Any]]:
        """Remember a timed plan for the room; Em bé announces it when due. Dedupes near-identical plans."""
        mem = self._rooms.get(room_id)
        text = re.sub(r"\s+", " ", (what or "").strip())[:160]
        now = _now()
        if mem is None or len(text) < 3 or not (now - 300 < when_ts < now + 30 * 86400):
            return None
        plans = mem.setdefault("plans", [])
        for p in plans:
            if not p.get("done") and abs(float(p.get("when", 0)) - when_ts) < 1800 and _fact_overlap(p.get("what", ""), text) >= 0.6:
                return p  # same plan mentioned again
        plan = {"id": f"pl{int(now * 1000) % 10**9}", "when": float(when_ts), "what": text, "who": (who or "")[:30], "done": False}
        plans.append(plan)
        # Keep the list small: drop finished plans older than 2 days
        mem["plans"] = [p for p in plans if not (p.get("done") and now - float(p.get("when", 0)) > 2 * 86400)][-20:]
        self._mark_dirty("room", room_id)
        return plan

    def due_plans(self, room_id: str, now: Optional[float] = None, grace_sec: float = 1800) -> List[Dict[str, Any]]:
        """Plans whose time has come (within the grace window) and were not announced yet."""
        mem = self._rooms.get(room_id)
        if not mem:
            return []
        now = now or _now()
        due, changed = [], False
        for p in mem.get("plans", []):
            if p.get("done"):
                continue
            when = float(p.get("when", 0))
            if when <= now <= when + grace_sec:
                due.append(p)
            elif now > when + grace_sec:
                p["done"] = True  # missed while nobody was online: drop silently
                changed = True
        if changed:
            self._mark_dirty("room", room_id)
        return due

    def mark_plan_done(self, room_id: str, plan_id: str) -> None:
        mem = self._rooms.get(room_id)
        if not mem:
            return
        for p in mem.get("plans", []):
            if p.get("id") == plan_id:
                p["done"] = True
                self._mark_dirty("room", room_id)

    def upcoming_plans(self, room_id: str, n: int = 4) -> List[Dict[str, Any]]:
        mem = self._rooms.get(room_id)
        if not mem:
            return []
        now = _now()
        return sorted((p for p in mem.get("plans", []) if not p.get("done") and float(p.get("when", 0)) > now - 60), key=lambda p: p["when"])[:n]

    def set_session_summary(self, room_id: str, summary: str) -> None:
        mem = self._rooms.get(room_id)
        if mem is None or not (summary or "").strip():
            return
        today = datetime.now(VN_TZ).strftime("%d/%m/%Y")
        sessions = [s for s in mem.get("sessions", []) if s.get("date") != today]
        sessions.insert(0, {"date": today, "summary": summary.strip()[:900], "ts": _now()})
        mem["sessions"] = sessions[:MAX_ROOM_SESSIONS]
        self._mark_dirty("room", room_id)

    # ------------------------------------------------------------------ reading
    def get_profile(self, pid: str) -> Optional[Dict[str, Any]]:
        return self._profiles.get(pid)

    def top_artists(self, pid: str, n: int = 5, *, negative: bool = False) -> List[str]:
        prof = self._profiles.get(pid)
        if not prof:
            return []
        now = _now()
        scored = [(_decayed(float(v.get("score", 0)), float(v.get("ts", now)), now), v.get("name", k)) for k, v in prof.get("artists", {}).items()]
        if negative:
            return [name for s, name in sorted(scored) if s <= -1.0][:n]
        return [name for s, name in sorted(scored, reverse=True) if s >= 1.0][:n]

    def liked_tracks(self, pid: str, n: int = 5) -> List[Dict[str, Any]]:
        """Recent tracks with positive signals (seeds for recommendations), newest first."""
        prof = self._profiles.get(pid)
        if not prof:
            return []
        disliked = prof.get("disliked", {})
        return [t for t in prof.get("tracks", []) if float(t.get("score", 0)) > 0 and t.get("id") not in disliked][:n]

    def disliked_ids(self, pids: Iterable[str]) -> set:
        out: set = set()
        for pid in pids:
            prof = self._profiles.get(pid)
            if prof:
                out.update(prof.get("disliked", {}).keys())
        return out

    def describe_member(self, pid: str, name: str) -> str:
        prof = self._profiles.get(pid)
        if not prof:
            return ""
        parts: List[str] = []
        likes = self.top_artists(pid, 5)
        if likes:
            parts.append("thích " + ", ".join(likes))
        dislikes = self.top_artists(pid, 3, negative=True)
        if dislikes:
            parts.append("hay bỏ qua " + ", ".join(dislikes))
        recent = [t["title"][:40] for t in self.liked_tracks(pid, 3)]
        if recent:
            parts.append("gần đây mê: " + "; ".join(recent))
        facts = [f["text"] for f in prof.get("facts", [])[:5]]
        if facts:
            parts.append(f"điều về CHÍNH {name}: " + " | ".join(facts))
        visits = int(prof.get("visits", 0))
        if visits > 1:
            prev = float(prof.get("prev_seen") or prof.get("last_seen") or _now())
            seen = datetime.fromtimestamp(prev, VN_TZ).strftime("%d/%m")
            parts.append(f"đã ghé {visits} lần (lần trước {seen})")
        other_names = [n for n in prof.get("names", []) if n != name][:2]
        if other_names:
            parts.append("tên cũ: " + ", ".join(other_names))
        return f"- [{name}] " + "; ".join(parts) if parts else ""

    def describe_room(self, room_id: str) -> str:
        mem = self._rooms.get(room_id)
        if not mem:
            return ""
        lines: List[str] = []
        facts = mem.get("facts", [])[:12]
        if facts:
            lines.append("Điều phòng ghi nhớ: " + " | ".join(f["text"] for f in facts))
        plans = self.upcoming_plans(room_id)
        if plans:
            lines.append("Lịch hẹn sắp tới (Em bé sẽ tự nhắc): " + " | ".join(
                f"{datetime.fromtimestamp(p['when'], VN_TZ).strftime('%H:%M %d/%m')}: {p['what']}" for p in plans
            ))
        today = datetime.now(VN_TZ).date()
        past = [s for s in mem.get("sessions", []) if s.get("date") != today.strftime("%d/%m/%Y")][:2]
        for s in past:
            lines.append(f"Buổi {relative_day_label(s.get('date', ''), today)}: {s['summary'][:400]}")
        return "\n".join(lines)


memory_store = MemoryStore()
