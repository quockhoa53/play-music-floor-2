"""Prompt-injection defenses for '@Em bé floor 2' (defense in depth).

1. Detection: Vietnamese/English attack patterns + Meta Llama Prompt Guard 2 (catches English well,
   misses Vietnamese, hence the patterns).
2. Neutralization: untrusted text (chat, captions, tool results, memories) cannot forge our prompt
   structure (<room_context>, <new_message>, [Hệ thống] system notes).
3. Server-side action gating: tools with side effects only run when the CURRENT message actually asks
   for that action, regardless of what the model decided.
"""
import asyncio
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from services.cache import TTLCache

PROMPT_GUARD_MODEL = "meta-llama/llama-prompt-guard-2-86m"
GUARD_THRESHOLD = 0.85
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_guard_cache: TTLCache[float] = TTLCache("prompt_guard", max_size=500, default_ttl=3600)
_guard_client: Optional[httpx.AsyncClient] = None

INJECTION_PATTERNS = [
    # Vietnamese
    r"bỏ qua (hết |mọi |tất cả |toàn bộ |các )?(quy tắc|luật|hướng dẫn|chỉ dẫn|chỉ thị|lệnh|cài đặt)",
    r"quên (hết |mọi |tất cả |đi )?(quy tắc|luật|hướng dẫn|chỉ dẫn|vai trò|những gì được dặn)",
    r"(từ (giờ|bây giờ|nay)|kể từ giờ),? (em|bạn|mày|cậu|ai) (là|sẽ là|phải)",
    r"(đóng vai|nhập vai|giả làm|hãy làm) (một )?(ai|bot|trợ lý|hệ thống|admin|quản trị|người khác)",
    r"(tiết lộ|cho (tui|tôi|mình|tao) (xem|biết)|in ra|đọc ra).{0,20}(prompt|lời nhắc hệ thống|hướng dẫn hệ thống|chỉ dẫn gốc|system)",
    r"\b(prompt hệ thống|system prompt|lời nhắc hệ thống)\b",
    r"\[\s*(hệ thống|he thong|system|admin|developer|dev)\s*\]",
    r"(admin|quản trị viên|nhà phát triển|developer|dev) (yêu cầu|ra lệnh|cho phép|bảo)",
    r"chế độ (nhà phát triển|developer|dan|không giới hạn|jailbreak)",
    r"\bjailbreak\b",
    r"(làm|thực hiện) (theo|y) (lệnh|chỉ thị) (sau|này) (của tui|của tôi)? ?(mà )?không (cần )?(hỏi|kiểm tra)",
    # English
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |the )?(previous|prior|above) (instructions|rules)",
    r"you are now\b",
    r"developer mode",
    r"reveal (your )?(system )?prompt",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)
_FAKE_SYSTEM_TAG_RE = re.compile(r"\[\s*(hệ thống|he thong|system|admin|developer|dev)\s*\]", re.IGNORECASE)

# Actions with side effects -> words the CURRENT message must contain for the tool to run
ACTION_INTENT_PATTERNS: Dict[str, re.Pattern] = {
    "add_song_to_queue": re.compile(
        r"(\bmở\b|\bmo\b|\bthêm\b|\bthem\b|\bphát\b|\bphat\b|\bbật\b|\bbat\b|cho (tui|tôi|mình|anh|em|cả phòng)? ?nghe|"
        r"xếp (vào|hàng)|cho vào (hàng đợi|queue)|\bqueue\b|\badd\b|\bplay\b|nghe (bài|thử) )",
        re.IGNORECASE,
    ),
    # Must be about the SENDER themself ("quên tui đi", "xóa dữ liệu của mình")
    "forget_my_memory": re.compile(
        r"(quên|xóa|xoá|reset|forget|delete).{0,40}\b(tui|tôi|mình|tao|tớ|me|my|myself)\b", re.IGNORECASE
    ),
    "schedule_reminder": re.compile(r"(nhắc|hẹn|remind|báo (giùm|giúp|cho)|đặt lịch|alarm)", re.IGNORECASE),
    "remember_fact": re.compile(r"(nhớ|ghi (lại|nhớ)|remember|note|lưu)", re.IGNORECASE),
}


def _get_guard_client() -> httpx.AsyncClient:
    global _guard_client
    if _guard_client is None or _guard_client.is_closed:
        _guard_client = httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=2.0))
    return _guard_client


def neutralize_untrusted(text: str) -> str:
    """Make untrusted text unable to forge prompt structure: no tags, no fake [Hệ thống] notes."""
    if not text:
        return ""
    t = str(text).replace("<", "‹").replace(">", "›")
    t = _FAKE_SYSTEM_TAG_RE.sub(lambda m: f"(giả mạo {m.group(1)})", t)
    return t


def injection_patterns_hit(text: str) -> List[str]:
    return [m.group(0)[:60] for m in _INJECTION_RE.finditer(text or "")][:3]


async def _prompt_guard_score(text: str, api_key: str) -> Optional[float]:
    key = (text or "")[:500]
    cached = _guard_cache.get(key)
    if cached is not None:
        return cached
    try:
        r = await _get_guard_client().post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": PROMPT_GUARD_MODEL, "messages": [{"role": "user", "content": key}]},
        )
        if r.status_code != 200:
            return None
        score = float(r.json()["choices"][0]["message"]["content"].strip())
    except Exception:
        return None
    _guard_cache.set(key, score)
    return score


async def assess_injection(text: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """{'flagged': bool, 'score': float, 'hits': [...], 'source': 'pattern'|'guard'|'', 'ms': int}"""
    started = time.time()
    hits = injection_patterns_hit(text)
    score: Optional[float] = None
    if api_key:
        try:
            score = await asyncio.wait_for(_prompt_guard_score(text, api_key), timeout=2.5)
        except Exception:
            score = None
    flagged_guard = score is not None and score >= GUARD_THRESHOLD
    source = "pattern" if hits else ("guard" if flagged_guard else "")
    return {
        "flagged": bool(hits) or flagged_guard,
        "score": round(score, 4) if score is not None else None,
        "hits": hits,
        "source": source,
        "ms": int((time.time() - started) * 1000),
    }


def action_allowed(tool_name: str, current_message: str) -> bool:
    """Server-side gate: side-effect tools only when the current message actually asks for them."""
    pat = ACTION_INTENT_PATTERNS.get(tool_name)
    return True if pat is None else bool(pat.search(current_message or ""))
