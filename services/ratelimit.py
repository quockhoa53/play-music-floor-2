"""Client-side Groq rate limiting, per (API key, model) — Groq quotas are per model.

Free tier (GroqCloud > Limits): gpt-oss-120b / gpt-oss-20b / qwen3.8-27b = 30 RPM, 8K TPM, 200K TPD each.
Instead of firing requests and eating 429s, we:
  - estimate each request's tokens (prompt chars + max_tokens) and keep a 60s sliding window per (key, model)
    under a safety budget, skipping to another key / model when a request would not fit;
  - after a 429, cool down exactly that (key, model) for the server's retry-after (parsed from header or
    message: '690ms', '7.2s', '24m17.568s', '1h2m3s'), so a daily-quota exhaustion is not retried every 20s;
  - record real usage from responses to correct the estimates.
"""
import json
import re
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import httpx

DEFAULT_TPM = 8000
MODEL_TPM: Dict[str, int] = {
    "openai/gpt-oss-120b": 8000,
    "openai/gpt-oss-20b": 8000,
    "qwen/qwen3.8-27b": 8000,
    "meta-llama/llama-prompt-guard-2-86m": 15000,
}
SAFETY_RATIO = 0.88  # stay ~12% under the published TPM
CHARS_PER_TOKEN = 2.6  # Vietnamese-heavy prompts (measured against Groq usage)

_DURATION_RE = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?")


def parse_duration(text: str) -> Optional[float]:
    """'690ms' -> 0.69, '7.2s' -> 7.2, '24m17.568s' -> 1457.568, '1h2m3s' -> 3723."""
    t = (text or "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        pass
    m = _DURATION_RE.fullmatch(t)
    if not m or not any(m.groups()):
        return None
    h, mi, s, ms = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def retry_after_seconds(resp: httpx.Response, body: str = "") -> float:
    """Server-suggested wait: retry-after header, else 'try again in Xm Ys' from the error message."""
    hdr = resp.headers.get("retry-after")
    val = parse_duration(hdr) if hdr else None
    if val is None:
        m = re.search(r"try again in ([0-9hms.]+)", body or "")
        val = parse_duration(m.group(1).rstrip(".")) if m else None
    if val is None:
        for h in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
            if resp.headers.get(h):
                val = parse_duration(resp.headers[h])
                if val is not None:
                    break
    return max(1.0, val if val is not None else 20.0)


def estimate_tokens(payload_messages: List[Dict[str, Any]], max_tokens: int, tools: Optional[List[Dict[str, Any]]] = None) -> int:
    chars = len(json.dumps(payload_messages, ensure_ascii=False))
    if tools:
        chars += len(json.dumps(tools, ensure_ascii=False))
    return int(chars / CHARS_PER_TOKEN) + int(max_tokens or 0)


class ModelRateGuard:
    def __init__(self) -> None:
        self._usage: Dict[Tuple[int, str], Deque[Tuple[float, int]]] = {}
        self._cooldown: Dict[Tuple[int, str], float] = {}
        self.skipped_for_budget = 0

    def _window(self, key: Tuple[int, str], now: float) -> Deque[Tuple[float, int]]:
        dq = self._usage.setdefault(key, deque())
        while dq and now - dq[0][0] > 60.0:
            dq.popleft()
        return dq

    def budget(self, model: str) -> int:
        return int(MODEL_TPM.get(model, DEFAULT_TPM) * SAFETY_RATIO)

    def cooling_for(self, key_idx: int, model: str) -> float:
        return max(0.0, self._cooldown.get((key_idx, model), 0.0) - time.time())

    def check(self, key_idx: int, model: str, est_tokens: int) -> float:
        """0.0 if the request fits now, else seconds until it would (cooldown or sliding window)."""
        now = time.time()
        cool = self.cooling_for(key_idx, model)
        if cool > 0:
            return cool
        dq = self._window((key_idx, model), now)
        used = sum(n for _, n in dq)
        limit = self.budget(model)
        if used + est_tokens <= limit or not dq:
            return 0.0
        # Wait until enough old entries slide out of the 60s window
        need = used + est_tokens - limit
        freed = 0
        for ts, n in dq:
            freed += n
            if freed >= need:
                return max(0.05, 60.0 - (now - ts))
        return 60.0

    def reserve(self, key_idx: int, model: str, est_tokens: int) -> int:
        """Record an estimated spend now; returns a handle to correct it with real usage later."""
        dq = self._window((key_idx, model), time.time())
        dq.append((time.time(), int(est_tokens)))
        return len(dq) - 1

    def settle(self, key_idx: int, model: str, est_tokens: int, actual_tokens: Optional[int]) -> None:
        """Replace the most recent matching estimate with the real token count from the API response."""
        if actual_tokens is None:
            return
        dq = self._usage.get((key_idx, model))
        if not dq:
            return
        for i in range(len(dq) - 1, -1, -1):
            if dq[i][1] == int(est_tokens):
                dq[i] = (dq[i][0], int(actual_tokens))
                return

    def cool(self, key_idx: int, model: str, seconds: float) -> None:
        self._cooldown[(key_idx, model)] = max(self._cooldown.get((key_idx, model), 0.0), time.time() + seconds)

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "tpm_used": {f"k{k}:{m}": sum(n for t, n in dq if now - t <= 60) for (k, m), dq in self._usage.items()},
            "cooldowns": {f"k{k}:{m}": round(v - now) for (k, m), v in self._cooldown.items() if v > now},
            "skipped_for_budget": self.skipped_for_budget,
        }


rate_guard = ModelRateGuard()
