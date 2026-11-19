"""Application-layer (L7) Anti-DDoS & Anti-DoS Shield.

Provides defense-in-depth at the FastAPI/ASGI layer without external dependencies:
- Global per-IP sliding-window burst & sustained HTTP rate limiting
- Automatic temporary IP banning with escalating duration (strike system)
- Early rejection of oversized URLs, request bodies, and bot/scanner exploit probes
- Per-IP & global concurrent in-flight HTTP load shedding (prevents Slowloris / saturation)
- Global WebSocket handshake rate limiting & cross-room connection caps per IP / globally
- Bounded memory tables so spoofed-IP floods cannot OOM the process
"""
import math
import os
import re
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple


# Common automated scanner / exploit probe paths
SCANNER_PATH_RE = re.compile(
    r"^/(\.env|\.git|\.aws|\.htaccess|\.DS_Store|wp-admin|wp-login\.php|wp-content|wp-includes|"
    r"xmlrpc\.php|phpmyadmin|pma|adminer|cgi-bin|actuator|vendor/phpunit|config\.php|shell\.php|"
    r"server-status|boaform|HNAP1|eval-stdin\.php)(/|$)",
    re.IGNORECASE,
)


class DDoSGuard:
    """Thread-safe L7 Anti-DDoS & Load-Shedding Guard."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        burst_limit: int = 35,
        burst_window_sec: float = 5.0,
        sustained_limit: int = 120,
        sustained_window_sec: float = 60.0,
        health_limit: int = 40,
        health_window_sec: float = 60.0,
        ws_handshake_limit: int = 15,
        ws_handshake_window_sec: float = 60.0,
        max_inflight_per_ip: int = 20,
        max_inflight_global: int = 200,
        max_ws_per_ip: int = 12,
        max_ws_global: int = 500,
        max_url_length: int = 2048,
        max_json_body_bytes: int = 32_768,
        max_upload_body_bytes: int = 3_145_728,  # 3 MB hard envelope above 2.5 MB image cap
        strike_threshold: int = 6,
        strike_window_sec: float = 60.0,
        base_ban_sec: float = 300.0,
        max_ban_sec: float = 1800.0,
        max_tracked_ips: int = 20_000,
    ):
        if enabled is None:
            enabled = os.getenv("DDOS_GUARD_ENABLED", "true").lower() != "false"
        self.enabled = enabled

        self.burst_limit = burst_limit
        self.burst_window_sec = burst_window_sec
        self.sustained_limit = sustained_limit
        self.sustained_window_sec = sustained_window_sec
        self.health_limit = health_limit
        self.health_window_sec = health_window_sec
        self.ws_handshake_limit = ws_handshake_limit
        self.ws_handshake_window_sec = ws_handshake_window_sec

        self.max_inflight_per_ip = max_inflight_per_ip
        self.max_inflight_global = max_inflight_global
        self.max_ws_per_ip = max_ws_per_ip
        self.max_ws_global = max_ws_global

        self.max_url_length = max_url_length
        self.max_json_body_bytes = max_json_body_bytes
        self.max_upload_body_bytes = max_upload_body_bytes

        self.strike_threshold = strike_threshold
        self.strike_window_sec = strike_window_sec
        self.base_ban_sec = base_ban_sec
        self.max_ban_sec = max_ban_sec
        self.max_tracked_ips = max_tracked_ips

        self._lock = threading.RLock()
        self._burst: Dict[str, Deque[float]] = {}
        self._sustained: Dict[str, Deque[float]] = {}
        self._health: Dict[str, Deque[float]] = {}
        self._ws_handshakes: Dict[str, Deque[float]] = {}
        self._strikes: Dict[str, Deque[float]] = {}
        self._bans: Dict[str, Tuple[float, int]] = {}  # ip -> (expires_monotonic, ban_count)

        self._inflight_global = 0
        self._inflight_by_ip: Dict[str, int] = {}
        self._ws_global = 0
        self._ws_by_ip: Dict[str, int] = {}

        # Observability counters
        self.total_blocked = 0
        self.total_strikes = 0
        self.total_scanner_hits = 0
        self._last_cleanup = time.monotonic()

    def reset(self) -> None:
        """Reset all in-memory buckets/bans (useful for automated tests)."""
        with self._lock:
            self._burst.clear()
            self._sustained.clear()
            self._health.clear()
            self._ws_handshakes.clear()
            self._strikes.clear()
            self._bans.clear()
            self._inflight_global = 0
            self._inflight_by_ip.clear()
            self._ws_global = 0
            self._ws_by_ip.clear()
            self.total_blocked = 0
            self.total_strikes = 0
            self.total_scanner_hits = 0

    def _maybe_cleanup_locked(self, now: float) -> None:
        total_keys = len(self._sustained) + len(self._burst) + len(self._bans)
        if (now - self._last_cleanup) < 60.0 and total_keys < self.max_tracked_ips:
            return
        self._last_cleanup = now

        for table, ttl in (
            (self._burst, self.burst_window_sec * 2),
            (self._sustained, self.sustained_window_sec * 2),
            (self._health, self.health_window_sec * 2),
            (self._ws_handshakes, self.ws_handshake_window_sec * 2),
            (self._strikes, self.strike_window_sec * 2),
        ):
            stale = [ip for ip, dq in table.items() if not dq or (now - dq[-1]) > ttl]
            for ip in stale:
                table.pop(ip, None)

        expired_bans = [ip for ip, (exp, _) in self._bans.items() if now >= exp]
        for ip in expired_bans:
            self._bans.pop(ip, None)

        # Hard cap protection against random spoofed-IP memory flood
        for table in (self._burst, self._sustained, self._health, self._ws_handshakes, self._strikes):
            if len(table) > self.max_tracked_ips:
                excess = len(table) - int(self.max_tracked_ips * 0.8)
                for ip in list(table.keys())[:excess]:
                    table.pop(ip, None)

    def _sliding_allow_locked(
        self, table: Dict[str, Deque[float]], ip: str, limit: int, window_sec: float, now: float
    ) -> Tuple[bool, float]:
        dq = table.get(ip)
        if dq is None:
            dq = deque()
            table[ip] = dq
        cutoff = now - window_sec
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = max(1.0, window_sec - (now - dq[0]))
            return False, retry_after
        dq.append(now)
        return True, 0.0

    def _ban_remaining_locked(self, ip: str, now: float) -> float:
        entry = self._bans.get(ip)
        if not entry:
            return 0.0
        exp, _ = entry
        if now >= exp:
            self._bans.pop(ip, None)
            return 0.0
        return exp - now

    def record_strike(self, ip: str, weight: int = 1) -> float:
        """Record an abuse strike for an IP; returns ban duration (>0) if IP gets auto-banned."""
        if not self.enabled or not ip:
            return 0.0
        now = time.monotonic()
        with self._lock:
            self.total_strikes += max(1, weight)
            dq = self._strikes.get(ip)
            if dq is None:
                dq = deque()
                self._strikes[ip] = dq
            cutoff = now - self.strike_window_sec
            while dq and dq[0] <= cutoff:
                dq.popleft()
            for _ in range(max(1, weight)):
                dq.append(now)
            if len(dq) >= self.strike_threshold:
                _, prev_bans = self._bans.get(ip, (0.0, 0))
                next_ban_count = prev_bans + 1
                duration = min(self.max_ban_sec, self.base_ban_sec * (2 ** (next_ban_count - 1)))
                self._bans[ip] = (now + duration, next_ban_count)
                dq.clear()
                return duration
            return 0.0

    def check_http_request(
        self,
        ip: str,
        path: str,
        method: str,
        full_url_len: int,
        content_length: Optional[int],
    ) -> Tuple[bool, int, str, int]:
        """Validate an incoming HTTP request before entering route handlers.

        Returns: (allowed, http_status, detail_message, retry_after_sec)
        When allowed is True, caller MUST call release_http(ip) in a finally block!
        """
        if not self.enabled:
            return True, 200, "", 0

        now = time.monotonic()
        with self._lock:
            self._maybe_cleanup_locked(now)

            # 1. O(1) Instant drop for banned IPs
            ban_left = self._ban_remaining_locked(ip, now)
            if ban_left > 0:
                self.total_blocked += 1
                retry = int(math.ceil(ban_left))
                return (
                    False,
                    429,
                    f"Địa chỉ IP tạm thời bị chặn do gửi quá nhiều yêu cầu bất thường. Thử lại sau {retry} giây.",
                    retry,
                )

            # 2. Scanner / exploit probe trap
            if SCANNER_PATH_RE.match(path or ""):
                self.total_scanner_hits += 1
                self.total_blocked += 1
                self.record_strike(ip, weight=2)
                return False, 404, "Not Found", 0

            # 3. URL length guard (blocks URI flood / buffer probing)
            if full_url_len > self.max_url_length:
                self.total_blocked += 1
                self.record_strike(ip, weight=2)
                return False, 414, "Đường dẫn yêu cầu quá dài.", 0

            # 4. Early Content-Length guard (blocks body-bomb OOM before body is read)
            if method in ("POST", "PUT", "PATCH") and content_length is not None:
                body_cap = (
                    self.max_upload_body_bytes
                    if path == "/api/chat/upload"
                    else self.max_json_body_bytes
                )
                if content_length > body_cap:
                    self.total_blocked += 1
                    self.record_strike(ip, weight=2)
                    return False, 413, "Kích thước dữ liệu gửi lên vượt quá giới hạn cho phép.", 0

            # 5. Rate limiting (separate lightweight bucket for /api/health keep-alive probes)
            if path == "/api/health":
                ok_h, retry_h = self._sliding_allow_locked(
                    self._health, ip, self.health_limit, self.health_window_sec, now
                )
                if not ok_h:
                    self.total_blocked += 1
                    self.record_strike(ip, weight=1)
                    return False, 429, "Quá nhiều yêu cầu kiểm tra trạng thái.", int(math.ceil(retry_h))
            else:
                ok_b, retry_b = self._sliding_allow_locked(
                    self._burst, ip, self.burst_limit, self.burst_window_sec, now
                )
                if not ok_b:
                    self.total_blocked += 1
                    self.record_strike(ip, weight=1)
                    return (
                        False,
                        429,
                        f"Phát hiện gửi yêu cầu dồn dập. Vui lòng thử lại sau {int(math.ceil(retry_b))} giây.",
                        int(math.ceil(retry_b)),
                    )

                ok_s, retry_s = self._sliding_allow_locked(
                    self._sustained, ip, self.sustained_limit, self.sustained_window_sec, now
                )
                if not ok_s:
                    self.total_blocked += 1
                    self.record_strike(ip, weight=1)
                    return (
                        False,
                        429,
                        f"Bạn đã vượt quá giới hạn truy cập. Vui lòng thử lại sau {int(math.ceil(retry_s))} giây.",
                        int(math.ceil(retry_s)),
                    )

            # 6. Concurrent in-flight load shedding (mitigates Slowloris / connection exhaustion)
            cur_ip_inflight = self._inflight_by_ip.get(ip, 0)
            if cur_ip_inflight >= self.max_inflight_per_ip or self._inflight_global >= self.max_inflight_global:
                self.total_blocked += 1
                self.record_strike(ip, weight=1)
                return False, 503, "Hệ thống đang bận, vui lòng thử lại sau vài giây.", 3

            self._inflight_global += 1
            self._inflight_by_ip[ip] = cur_ip_inflight + 1
            return True, 200, "", 0

    def release_http(self, ip: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._inflight_global = max(0, self._inflight_global - 1)
            cur = self._inflight_by_ip.get(ip, 0)
            if cur <= 1:
                self._inflight_by_ip.pop(ip, None)
            else:
                self._inflight_by_ip[ip] = cur - 1

    def check_ws_pre_auth(self, ip: str) -> Tuple[bool, str]:
        """Early O(1) check before WS auth: blocks banned IPs and WS handshake floods (no slot acquired)."""
        if not self.enabled:
            return True, ""

        now = time.monotonic()
        with self._lock:
            self._maybe_cleanup_locked(now)

            ban_left = self._ban_remaining_locked(ip, now)
            if ban_left > 0:
                self.total_blocked += 1
                return False, f"IP tạm thời bị chặn ({int(math.ceil(ban_left))}s)"

            ok_ws, _ = self._sliding_allow_locked(
                self._ws_handshakes, ip, self.ws_handshake_limit, self.ws_handshake_window_sec, now
            )
            if not ok_ws:
                self.total_blocked += 1
                self.record_strike(ip, weight=1)
                return False, "Kết nối WebSocket quá nhanh, vui lòng thử lại sau"
            return True, ""

    def acquire_ws_slot(self, ip: str) -> Tuple[bool, str]:
        """Acquire a cross-room concurrent WS connection slot (must pair with release_ws in finally)."""
        if not self.enabled:
            return True, ""

        with self._lock:
            cur_ip_ws = self._ws_by_ip.get(ip, 0)
            if cur_ip_ws >= self.max_ws_per_ip:
                self.total_blocked += 1
                self.record_strike(ip, weight=1)
                return False, "Quá nhiều kết nối WebSocket đồng thời từ địa chỉ IP của bạn"

            if self._ws_global >= self.max_ws_global:
                self.total_blocked += 1
                return False, "Máy chủ đã đạt giới hạn kết nối đồng thời"

            self._ws_global += 1
            self._ws_by_ip[ip] = cur_ip_ws + 1
            return True, ""

    def check_ws_connect(self, ip: str) -> Tuple[bool, str]:
        """Combined WS pre-auth + slot acquisition (must call release_ws if True)."""
        ok, reason = self.check_ws_pre_auth(ip)
        if not ok:
            return False, reason
        return self.acquire_ws_slot(ip)

    def release_ws(self, ip: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._ws_global = max(0, self._ws_global - 1)
            cur = self._ws_by_ip.get(ip, 0)
            if cur <= 1:
                self._ws_by_ip.pop(ip, None)
            else:
                self._ws_by_ip[ip] = cur - 1

    def stats(self) -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            active_bans = sum(1 for exp, _ in self._bans.values() if now < exp)
            return {
                "enabled": self.enabled,
                "active_bans": active_bans,
                "total_blocked": self.total_blocked,
                "total_strikes": self.total_strikes,
                "scanner_probes_blocked": self.total_scanner_hits,
                "inflight_http": self._inflight_global,
                "active_ws": self._ws_global,
                "unique_ws_ips": len(self._ws_by_ip),
            }
