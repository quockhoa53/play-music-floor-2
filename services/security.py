"""Security primitives: server sessions, rate limiting, passcode hashing, sanitization & WS origin guard."""
import hashlib
import hmac
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple
from urllib.parse import urlparse

ALLOWED_AVATARS = {
    "🎧", "🎵", "🎸", "🎹", "🦊", "🐱", "🐼", "🐯",
    "🚀", "👑", "⚡", "☕", "🍉", "🔥", "💃", "🕺",
}

ALLOWED_REACTIONS = {"❤️", "🔥", "🎵", "👏", "🕺", "🍻", "🚀", "😂"}

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
GIPHY_ID_RE = re.compile(r"^[A-Za-z0-9]{8,28}$")
MEDIA_ID_RE = re.compile(r"^[a-f0-9]{16,32}$")

PBKDF2_ITERATIONS = 120_000


def sanitize_giphy_id(value: Optional[str]) -> Optional[str]:
    """Allow only strict alphanumeric Giphy IDs to prevent URL injection/XSS."""
    if isinstance(value, str) and GIPHY_ID_RE.match(value.strip()):
        return value.strip()
    return None


def sanitize_media_id(value: Optional[str]) -> Optional[str]:
    """Allow only server-issued lowercase hex media IDs."""
    if isinstance(value, str) and MEDIA_ID_RE.match(value.strip()):
        return value.strip()
    return None


def detect_safe_image_mime(data: bytes) -> Optional[str]:
    """Verify real image binary via magic bytes (PNG/JPEG/GIF/WebP only — blocks SVG/HTML/XSS)."""
    if not data or len(data) < 12:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sanitize_text(value: Optional[str], max_len: int = 200, fallback: str = "") -> str:
    """Strip unsafe control characters and enforce strict length bounds."""
    if not isinstance(value, str):
        return fallback
    cleaned = CONTROL_CHARS_RE.sub("", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return fallback
    return cleaned[:max_len]


def sanitize_avatar(value: Optional[str]) -> str:
    """Allow only known avatar emojis to prevent XSS injection."""
    if isinstance(value, str) and value in ALLOWED_AVATARS:
        return value
    return "🎧"


def sanitize_color(value: Optional[str]) -> str:
    """Allow only strict 6-digit hex colors to prevent CSS/HTML attribute injection."""
    if isinstance(value, str) and HEX_COLOR_RE.match(value.strip()):
        return value.strip().lower()
    return "#4f46e5"


def sanitize_reaction(value: Optional[str]) -> Optional[str]:
    """Allow only predefined reaction emojis."""
    if isinstance(value, str) and value in ALLOWED_REACTIONS:
        return value
    return None


def hash_passcode(passcode: str) -> str:
    """Hash a room passcode with PBKDF2-HMAC-SHA256 + random salt."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        passcode.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return f"{salt}${dk.hex()}"


def verify_passcode(passcode: str, stored_hash: str) -> bool:
    """Constant-time verification of room passcode against salted PBKDF2 hash."""
    if not passcode or not stored_hash or "$" not in stored_hash:
        return False
    try:
        salt, expected_hex = stored_hash.split("$", 1)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            passcode.encode("utf-8"),
            salt.encode("utf-8"),
            PBKDF2_ITERATIONS,
        )
        return hmac.compare_digest(dk.hex(), expected_hex)
    except Exception:
        return False


def get_client_ip(headers, client) -> str:
    """Extract client IP safely (supports Render reverse proxy X-Forwarded-For)."""
    xff = headers.get("x-forwarded-for") if headers else None
    if xff:
        first_ip = xff.split(",")[0].strip()
        if first_ip:
            return first_ip
    if client and getattr(client, "host", None):
        return str(client.host)
    return "unknown"


def is_valid_ws_origin(websocket) -> bool:
    """Prevent Cross-Site WebSocket Hijacking (CSWSH) by validating Origin against Host / X-Forwarded-Host."""
    origin = websocket.headers.get("origin")
    if not origin:
        # Non-browser clients / TestClient do not send Origin
        return True
    host = websocket.headers.get("host")
    xf_host = websocket.headers.get("x-forwarded-host")
    if not host and not xf_host:
        return False

    def _norm_host(raw: str, scheme: str = "") -> str:
        val = (raw or "").split(",")[0].strip().lower()
        if scheme == "https" and val.endswith(":443"):
            val = val[:-4]
        elif scheme == "http" and val.endswith(":80"):
            val = val[:-3]
        return val

    try:
        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https"):
            return False
        origin_host = _norm_host(parsed.netloc, parsed.scheme)
        candidates = []
        if host:
            candidates.append(_norm_host(host, parsed.scheme))
        if xf_host:
            candidates.append(_norm_host(xf_host, parsed.scheme))

        for target_host in candidates:
            if origin_host == target_host:
                return True
            # Allow localhost <-> 127.0.0.1 on same port during local development
            o_name, _, o_port = origin_host.partition(":")
            t_name, _, t_port = target_host.partition(":")
            if o_name in ("localhost", "127.0.0.1") and t_name in ("localhost", "127.0.0.1") and o_port == t_port:
                return True
        return False
    except Exception:
        return False


@dataclass
class Session:
    token: str
    user_id: str
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class SessionManager:
    """Server-authoritative opaque session tokens to prevent Identity Spoofing & Host Hijacking."""

    def __init__(self, ttl_sec: int = 86400, max_sessions: int = 10_000):
        self._sessions: Dict[str, Session] = {}
        self._ttl_sec = ttl_sec
        self._max_sessions = max_sessions

    def _prune(self):
        now = time.time()
        expired = [tok for tok, s in self._sessions.items() if (now - s.last_seen) > self._ttl_sec]
        for tok in expired:
            self._sessions.pop(tok, None)
        if len(self._sessions) >= self._max_sessions:
            # Evict oldest 10%
            oldest = sorted(self._sessions.items(), key=lambda kv: kv[1].last_seen)[: max(1, self._max_sessions // 10)]
            for tok, _ in oldest:
                self._sessions.pop(tok, None)

    def create_session(self) -> Session:
        if len(self._sessions) >= self._max_sessions:
            self._prune()
        token = secrets.token_urlsafe(32)
        user_id = f"u_{secrets.token_hex(6)}"
        sess = Session(token=token, user_id=user_id)
        self._sessions[token] = sess
        return sess

    def get_session(self, token: Optional[str]) -> Optional[Session]:
        if not token or not isinstance(token, str):
            return None
        sess = self._sessions.get(token)
        if not sess:
            return None
        now = time.time()
        if (now - sess.last_seen) > self._ttl_sec:
            self._sessions.pop(token, None)
            return None
        sess.last_seen = now
        return sess


class SlidingWindowRateLimiter:
    """In-memory sliding-window rate limiter for REST & WebSocket abuse protection."""

    def __init__(self, max_buckets: int = 20_000):
        self._buckets: Dict[str, Deque[float]] = {}
        self._max_buckets = max_buckets
        self._last_cleanup = time.time()

    def allow(self, key: str, limit: int, window_sec: float) -> Tuple[bool, float]:
        """Return (is_allowed, retry_after_seconds)."""
        now = time.time()
        if (now - self._last_cleanup) > 60 or len(self._buckets) > self._max_buckets:
            self._cleanup(now)

        dq = self._buckets.get(key)
        if dq is None:
            dq = deque()
            self._buckets[key] = dq

        cutoff = now - window_sec
        while dq and dq[0] <= cutoff:
            dq.popleft()

        if len(dq) >= limit:
            retry_after = max(0.5, window_sec - (now - dq[0]))
            return False, retry_after

        dq.append(now)
        return True, 0.0

    def _cleanup(self, now: float):
        self._last_cleanup = now
        stale = [k for k, dq in self._buckets.items() if not dq or (now - dq[-1]) > 300]
        for k in stale:
            self._buckets.pop(k, None)
