"""Render free instance keep-alive heartbeat (tương tự cơ chế đánh thức server của songngu-sub).

Mỗi 8 phút ping /api/health của chính mình (qua URL công khai) và của các service Render khác
trong KEEP_ALIVE_EXTRA_URLS. Ping sang service khác là request từ bên ngoài nên đánh thức được
service đó dù nó đã ngủ; nhờ vậy các service giữ nhau thức, không phụ thuộc một mình nó tự ping.
"""
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

KEEP_ALIVE_EVERY = int(os.getenv("KEEP_ALIVE_EVERY", "480"))  # 8 phút (Render free ngủ sau 15 phút)
KEEP_ALIVE_DELAY = int(os.getenv("KEEP_ALIVE_DELAY", "60"))   # Đợi 60s sau khi server khởi động
DEFAULT_PUBLIC_URL = "https://play-music-floor-2.onrender.com"

_state_lock = threading.Lock()
_keep_alive_state: Dict[str, Any] = {
    "enabled": False,
    "interval_sec": KEEP_ALIVE_EVERY,
    "targets": [],
    "last_ping_at": None,
    "last_results": {},
}
_started = False


def _clean_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    return url if url.startswith(("http://", "https://")) else ""


def keep_alive_url() -> str:
    """Public URL of this service (APP_SERVER_PUBLIC_URL > KEEP_ALIVE_URL > RENDER_EXTERNAL_URL > DEFAULT_PUBLIC_URL)."""
    if os.getenv("KEEP_ALIVE_ENABLED", "true").lower() == "false":
        return ""
    for key in ("APP_SERVER_PUBLIC_URL", "KEEP_ALIVE_URL", "RENDER_EXTERNAL_URL"):
        url = _clean_url(os.getenv(key, ""))
        if url:
            return url
    return DEFAULT_PUBLIC_URL


def keep_alive_targets() -> List[Tuple[str, str]]:
    """[(tên, url health)] gồm service này + các service phụ (KEEP_ALIVE_EXTRA_URLS, phân tách bằng dấu phẩy)."""
    own = keep_alive_url()
    if not own:
        return []
    targets: List[Tuple[str, str]] = [("Floor 2 Music Room (chính mình)", f"{own}/api/health")]
    for raw in os.getenv("KEEP_ALIVE_EXTRA_URLS", "").split(","):
        url = _clean_url(raw)
        if url and url != own:
            health_url = url if url.endswith("/health") else f"{url}/api/health"
            targets.append((url, health_url))
    return targets


def ping_all(targets: List[Tuple[str, str]]) -> Dict[str, Any]:
    """Ping song song để một service đang thức dậy chậm không làm trễ các service còn lại."""
    results: Dict[str, Any] = {}
    res_lock = threading.Lock()

    def one(name: str, url: str) -> None:
        try:
            r = httpx.get(
                url,
                timeout=45.0,
                headers={"User-Agent": "Floor2MusicRoom-KeepAlive/1.0"},
                follow_redirects=True,
            )
            ok = 200 <= r.status_code < 400
            with res_lock:
                results[url] = {"ok": ok, "status_code": r.status_code}
            print(f"[keep-alive] {'OK' if ok else 'LỖI'} {name} -> {r.status_code}", flush=True)
        except Exception as e:
            with res_lock:
                results[url] = {"ok": False, "error": type(e).__name__}
            print(f"[keep-alive] {name} ping lỗi: {type(e).__name__}: {e}", flush=True)

    threads = [threading.Thread(target=one, args=t, daemon=True) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(50.0)

    with _state_lock:
        _keep_alive_state["last_ping_at"] = round(time.time(), 1)
        _keep_alive_state["last_results"] = results
    return results


def keep_alive_loop(targets: List[Tuple[str, str]]) -> None:
    """Chạy định kỳ, bắt đầu sau KEEP_ALIVE_DELAY giây kể từ khi server khởi động."""
    time.sleep(max(1, KEEP_ALIVE_DELAY))
    while True:
        print("[keep-alive] heartbeat...", flush=True)
        ping_all(targets)
        time.sleep(max(30, KEEP_ALIVE_EVERY))


def start_keep_alive() -> bool:
    """Start background heartbeat thread if a public URL is configured."""
    global _started
    targets = keep_alive_targets()
    with _state_lock:
        _keep_alive_state["interval_sec"] = KEEP_ALIVE_EVERY
        _keep_alive_state["targets"] = [u for _, u in targets]
        if not targets:
            _keep_alive_state["enabled"] = False
            print(
                "[keep-alive] tắt (chưa có APP_SERVER_PUBLIC_URL / KEEP_ALIVE_URL / RENDER_EXTERNAL_URL)",
                flush=True,
            )
            return False
        if _started:
            return True
        _started = True
        _keep_alive_state["enabled"] = True

    threading.Thread(target=keep_alive_loop, args=(targets,), daemon=True, name="keep-alive").start()
    print(
        f"[keep-alive] bật, mỗi {KEEP_ALIVE_EVERY // 60} phút ping: " + ", ".join(u for _, u in targets),
        flush=True,
    )
    return True


def get_keep_alive_status() -> Dict[str, Any]:
    with _state_lock:
        return {
            "enabled": _keep_alive_state["enabled"],
            "interval_sec": _keep_alive_state["interval_sec"],
            "targets_count": len(_keep_alive_state["targets"]),
            "last_ping_at": _keep_alive_state["last_ping_at"],
        }
