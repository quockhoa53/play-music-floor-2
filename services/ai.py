"""AI Assistant ("@Em bé floor 2"), AI DJ, Room Vibe Analyzer, Smart Lyric Cleaner & Expressive Female Radio MC TTS.

Uses Groq OpenAI-compatible API with strict round-robin API key alternation + automatic failover
so rate limits are distributed evenly across all configured keys.
"""
import asyncio
import json
import os
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

from services.cache import TTLCache

AI_BOT_ID = "ai_em_be_floor_2"
AI_BOT_NAME = "Em bé floor 2"
AI_BOT_AVATAR = "🎀"
AI_BOT_COLOR = "#ec4899"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
PRIMARY_MODEL = "qwen/qwen3.8-27b"
FALLBACK_MODEL = "openai/gpt-oss-120b"

# Mention matcher for "@Em bé floor 2" (case/accent-insensitive + handy shortcuts)
AI_MENTION_RE = re.compile(
    r"@\s*(?:em\s*b[eé]\s*floor\s*2|embefloor2|em\s*b[eé]|ai\b)",
    re.IGNORECASE,
)
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
CJK_CHARS_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

_groq_client: Optional[httpx.AsyncClient] = None
_tts_client: Optional[httpx.AsyncClient] = None

vibe_cache: TTLCache[Dict[str, Any]] = TTLCache("ai_vibe", max_size=120, default_ttl=300)
mc_intro_cache: TTLCache[Dict[str, Any]] = TTLCache("ai_mc_intro", max_size=200, default_ttl=1800)
mc_audio_cache: TTLCache[bytes] = TTLCache("ai_mc_audio", max_size=150, default_ttl=1800)
lyric_norm_cache: TTLCache[Tuple[str, str]] = TTLCache("ai_lyric_norm", max_size=300, default_ttl=86400)
_last_mc_style: Dict[str, str] = {}


class GroqKeyRotator:
    """Thread/async-safe round-robin rotator with per-key cooldown on 429 rate-limits."""

    def __init__(self):
        self._keys: List[str] = []
        self._idx: int = 0
        self._cooldown_until: Dict[int, float] = {}
        self.total_calls: int = 0
        self.failover_count: int = 0
        self.key_usage: Dict[int, int] = {}
        self._loaded = False

    def reload_keys(self) -> List[str]:
        raw_parts: List[str] = []
        for env_name in ("GROQ_API_KEYS", "GROD", "GROQ_API_KEY", "GROQ_API_KEY_1", "GROQ_API_KEY_2"):
            val = os.getenv(env_name, "").strip()
            if val:
                raw_parts.extend(re.split(r"[\s,;]+", val))
        seen = set()
        keys = []
        for k in raw_parts:
            ck = k.strip()
            if ck and ck.startswith("gsk_") and ck not in seen:
                seen.add(ck)
                keys.append(ck)
        self._keys = keys
        self._loaded = True
        return keys

    def get_keys(self) -> List[str]:
        if not self._loaded or not self._keys:
            self.reload_keys()
        return self._keys

    def is_configured(self) -> bool:
        return len(self.get_keys()) > 0

    def next_order(self) -> List[Tuple[int, str]]:
        """Return keys in alternating round-robin order, deferring any key currently on 429 cooldown."""
        keys = self.get_keys()
        n = len(keys)
        if n == 0:
            return []
        start = self._idx % n
        self._idx = (self._idx + 1) % n
        now = time.time()
        ordered = [((start + i) % n, keys[(start + i) % n]) for i in range(n)]
        ready = [(i, k) for i, k in ordered if self._cooldown_until.get(i, 0.0) <= now]
        cooling = [(i, k) for i, k in ordered if self._cooldown_until.get(i, 0.0) > now]
        return ready + cooling

    def mark_used(self, key_idx: int) -> None:
        self.total_calls += 1
        self.key_usage[key_idx] = self.key_usage.get(key_idx, 0) + 1

    def mark_rate_limited(self, key_idx: int, retry_after: float = 25.0) -> None:
        self.failover_count += 1
        self._cooldown_until[key_idx] = time.time() + max(5.0, min(120.0, retry_after))

    def stats(self) -> Dict[str, Any]:
        keys = self.get_keys()
        return {
            "configured": len(keys) > 0,
            "key_count": len(keys),
            "total_calls": self.total_calls,
            "failovers": self.failover_count,
            "usage_per_key": dict(self.key_usage),
            "vibe_cache": vibe_cache.stats(),
            "mc_cache": mc_intro_cache.stats(),
        }


key_rotator = GroqKeyRotator()


def get_ai_stats() -> Dict[str, Any]:
    return key_rotator.stats()


def _get_groq_client() -> httpx.AsyncClient:
    global _groq_client
    if _groq_client is None or _groq_client.is_closed:
        _groq_client = httpx.AsyncClient(
            timeout=httpx.Timeout(14.0, connect=4.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=8),
        )
    return _groq_client


def _get_tts_client() -> httpx.AsyncClient:
    global _tts_client
    if _tts_client is None or _tts_client.is_closed:
        _tts_client = httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=httpx.Timeout(8.0, connect=3.0),
            follow_redirects=True,
        )
    return _tts_client


def is_ai_mention(text: str) -> bool:
    return bool(text and AI_MENTION_RE.search(text))


def strip_ai_mention(text: str) -> str:
    return re.sub(r"\s+", " ", AI_MENTION_RE.sub("", text or "")).strip(" ,:;-")


def _strip_stray_cjk(text: str) -> str:
    """Remove accidental CJK characters that Qwen may slip into Vietnamese prose."""
    if not text:
        return ""
    out = CJK_CHARS_RE.sub("", text)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"  +", " ", out)
    return out.strip()


def _clean_model_text(raw: str) -> str:
    cleaned = THINK_BLOCK_RE.sub("", raw or "").strip()
    return _strip_stray_cjk(cleaned)


def _parse_json_lenient(raw: str) -> Optional[Dict[str, Any]]:
    text = _clean_model_text(raw)
    if not text:
        return None
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
    return None


async def groq_chat(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.75,
    max_tokens: int = 420,
    json_mode: bool = False,
    model: Optional[str] = None,
) -> Optional[str]:
    """Call Groq Chat Completions with alternating 2-key rotation & automatic model/key failover."""
    order = key_rotator.next_order()
    if not order:
        return None

    models_to_try = [model] if model else [PRIMARY_MODEL, FALLBACK_MODEL]
    client = _get_groq_client()

    for m_name in models_to_try:
        for key_idx, api_key in order:
            payload: Dict[str, Any] = {
                "model": m_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens if m_name == PRIMARY_MODEL else max(max_tokens, 600),
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

            try:
                resp = await client.post(
                    GROQ_API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                key_rotator.mark_used(key_idx)

                if resp.status_code == 200:
                    data = resp.json()
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                    cleaned = _clean_model_text(content)
                    if cleaned:
                        return cleaned
                elif resp.status_code == 429:
                    retry_after = float(resp.headers.get("retry-after", "25") or 25)
                    key_rotator.mark_rate_limited(key_idx, retry_after)
                    continue
                elif resp.status_code in (401, 403):
                    key_rotator.mark_rate_limited(key_idx, 120.0)
                    continue
                else:
                    # Try next key/model on 5xx or 404
                    continue
            except Exception as exc:
                print(f"[ai groq err key#{key_idx} model={m_name}] {exc}")
                continue
    return None


def build_room_ai_context(
    room: Any,
    current_lyrics_excerpt: str = "",
) -> str:
    """Build rich live room context: room info, playing song + lyrics, queue, member tastes & chat."""
    lines: List[str] = []
    lines.append(
        f"Phòng hiện tại: \"{room.name}\" (#{room.id}) | "
        f"Thành viên online: {len(room.users)} | Chế độ DJ: {'BẬT' if room.dj_mode else 'TẮT'}"
    )

    # Current track + lyric content
    ct = room.current_track
    if ct:
        lines.append(
            f"Đang phát: \"{ct.title}\" — {ct.author} "
            f"(thời lượng {ct.duration_str or '?'}, thêm bởi {ct.added_by_name})"
        )
        if current_lyrics_excerpt:
            lines.append(f"Nội dung / lời bài đang phát (trích đoạn):\n{current_lyrics_excerpt}")
    else:
        lines.append("Đang phát: (Chưa có bài hát nào đang phát)")

    # Upcoming queue
    if room.queue:
        q_items = [
            f"  #{i + 1}. \"{t.title}\" — {t.author} (bởi {t.added_by_name})"
            for i, t in enumerate(room.queue[:6])
        ]
        lines.append("Sắp phát trong hàng đợi:\n" + "\n".join(q_items))
    else:
        lines.append("Sắp phát trong hàng đợi: (Đang trống)")

    # Listening history
    if room.history:
        h_items = [
            f"  - \"{t.title}\" — {t.author} (bởi {t.added_by_name})"
            for t in reversed(room.history[-10:])
        ]
        lines.append("Lịch sử nghe gần đây của phòng:\n" + "\n".join(h_items))

    # Per-member list & inferred music taste from tracks they added
    member_tracks: Dict[str, List[str]] = {}
    all_tracks = []
    if ct:
        all_tracks.append(ct)
    all_tracks.extend(room.queue)
    all_tracks.extend(room.history[-20:])
    for t in all_tracks:
        who = (t.added_by_name or "").strip()
        if who:
            member_tracks.setdefault(who, [])
            entry = f"{t.title} ({t.author})"
            if entry not in member_tracks[who]:
                member_tracks[who].append(entry)

    if room.users:
        m_lines = []
        for u in list(room.users.values())[:15]:
            role = "Chủ phòng" if u.user_id == room.host_id else "Thành viên"
            tastes = member_tracks.get(u.name, [])[:4]
            taste_str = f" — Gu nhạc đã thêm: {', '.join(tastes)}" if tastes else ""
            m_lines.append(f"  - {u.name} ({role}){taste_str}")
        lines.append("Thành viên đang trong phòng & gu âm nhạc:\n" + "\n".join(m_lines))

    # Recent chat history (text only, last 12 non-system messages)
    recent_chat = [
        m for m in room.messages[-20:]
        if m.get("type") == "chat" and (m.get("text") or "").strip()
    ][-12:]
    if recent_chat:
        c_lines = [
            f"  {m.get('user_name', 'Khách')}: {(m.get('text') or '')[:160]}"
            for m in recent_chat
        ]
        lines.append("Cuộc trò chuyện gần đây:\n" + "\n".join(c_lines))

    return "\n\n".join(lines)


def extract_lyrics_excerpt(lyrics_data: Optional[Dict[str, Any]], max_lines: int = 12) -> str:
    """Compress synced/plain lyrics into a concise representative excerpt for LLM context."""
    if not lyrics_data or not lyrics_data.get("found"):
        return ""
    raw_lines: List[str] = []
    if lyrics_data.get("lines"):
        raw_lines = [ln.get("text", "").strip() for ln in lyrics_data["lines"] if ln.get("text")]
    elif lyrics_data.get("plain_lines"):
        raw_lines = [str(ln).strip() for ln in lyrics_data["plain_lines"] if str(ln).strip()]
    if not raw_lines:
        return ""
    if len(raw_lines) <= max_lines:
        chosen = raw_lines
    else:
        # Take opening verse + middle chorus so AI understands both intro & hook
        mid = len(raw_lines) // 2
        chosen = raw_lines[:6] + ["..."] + raw_lines[mid : mid + 6]
    return "\n".join(chosen)[:900]


# Curated, real trending / once-trending hits grouped by mood. Grounds the LLM so it suggests songs
# that truly fit the user's feeling instead of falling back to the same 1-2 mega hits it remembers.
MOOD_HIT_CATALOG: Dict[str, Dict[str, Any]] = {
    "buon": {
        "label": "Buồn / thất tình / tổn thương",
        "songs": [
            ("Một Ngàn Nỗi Đau", "Văn Mai Hương"), ("Ai Chung Tình Được Mãi", "Đinh Tùng Huy"),
            ("Sau Tất Cả", "ERIK"), ("Em Không Sai Chúng Ta Sai", "ERIK"),
            ("Chúng Ta Không Thuộc Về Nhau", "Sơn Tùng M-TP"), ("Đừng Làm Trái Tim Anh Đau", "Sơn Tùng M-TP"),
            ("Muộn Rồi Mà Sao Còn", "Sơn Tùng M-TP"), ("Buông Đôi Tay Nhau Ra", "Sơn Tùng M-TP"),
            ("Có Chàng Trai Viết Lên Cây", "Phan Mạnh Quỳnh"), ("Hoa Nở Không Màu", "Hoài Lâm"),
            ("Từng Là", "Vũ Cát Tường"), ("Mất Kết Nối", "Dương Domic"), ("Tràn Bộ Nhớ", "Dương Domic"),
            ("Dù Cho Mai Về Sau", "buitruonglinh"), ("Đúng Cũng Thành Sai", "Mỹ Tâm"),
            ("Thằng Điên", "JustaTee, Phương Ly"), ("Người Lạ Thoáng Qua", "Đinh Tùng Huy"),
            ("Ngày Mai Người Ta Lấy Chồng", "Thành Đạt"), ("Sóng Gió", "Jack, K-ICM"), ("Hồng Nhan", "Jack"),
            ("Một Bước Yêu Vạn Dặm Đau", "Mr. Siro"), ("Tháng Tư Là Lời Nói Dối Của Em", "Hà Anh Tuấn"),
            ("Nếu Lúc Đó", "tlinh"), ("Sài Gòn Đau Lòng Quá", "Hứa Kim Tuyền, Hoàng Duyên"),
            ("Chạy Về Khóc Với Anh", "ERIK"), ("Đế Vương", "Đình Dũng"),
            ("Someone You Loved", "Lewis Capaldi"), ("drivers license", "Olivia Rodrigo"), ("lovely", "Billie Eilish, Khalid"),
        ],
    },
    "co_don": {
        "label": "Cô đơn / đêm khuya / trầm tư",
        "songs": [
            ("Anh Đã Quen Với Cô Đơn", "Soobin Hoàng Sơn"), ("Bước Qua Mùa Cô Đơn", "Vũ."), ("Lạ Lùng", "Vũ."),
            ("Đông Kiếm Em", "Vũ."), ("Hôm Nay Tôi Buồn", "Phùng Khánh Linh"), ("Ngày Chưa Giông Bão", "Bùi Lan Hương"),
            ("Nàng Thơ", "Hoàng Dũng"), ("Ngủ Một Mình", "HIEUTHUHAI, Negav, Kewtiie"), ("Chuyện Rằng", "Thịnh Suy"),
            ("Một Đêm Say", "Thịnh Suy"), ("Vì Anh Đâu Có Biết", "Madihu, Vũ."), ("Waiting For You", "MONO"),
            ("Let Her Go", "Passenger"),
        ],
    },
    "mua": {
        "label": "Mưa / hoài niệm",
        "songs": [
            ("Mưa Tháng Sáu", "Văn Mai Hương, GREY D, Trung Quân"), ("Dấu Mưa", "Trung Quân"),
            ("Em Gái Mưa", "Hương Tràm"), ("Dự Báo Thời Tiết Hôm Nay Mưa", "GREY D"),
            ("Tình Đầu", "Tăng Duy Tân"), ("Chúng Ta Của Hiện Tại", "Sơn Tùng M-TP"),
            ("Em Của Ngày Hôm Qua", "Sơn Tùng M-TP"), ("Có Hẹn Với Thanh Xuân", "MONSTAR"),
            ("Nắng Ấm Xa Dần", "Sơn Tùng M-TP"), ("3107 3", "W/n, Nâu, Duongg"),
        ],
    },
    "chill": {
        "label": "Chill / thư giãn / làm việc",
        "songs": [
            ("Bài Này Chill Phết", "Đen, MIN"), ("Lối Nhỏ", "Đen, Phương Anh Đào"), ("Chuyện Đôi Ta", "Emcee L, Muộii"),
            ("Ánh Sao Và Bầu Trời", "T.R.I"), ("Có Em", "Madihu, Low G"), ("Ghé Qua", "Dick, Tofu, PC"),
            ("Cứ Chill Thôi", "Chillies, Suni Hạ Linh, Rhymastic"), ("Từ Chối Nhẹ Nhàng Thôi", "Bích Phương, Phúc Du"),
            ("Thích Em Hơi Nhiều", "Wren Evans"), ("Không Thể Say", "HIEUTHUHAI"), ("Exit Sign", "HIEUTHUHAI"),
            ("Birds of a Feather", "Billie Eilish"),
        ],
    },
    "yeu": {
        "label": "Yêu đương / lãng mạn / thả thính",
        "songs": [
            ("Nơi Này Có Anh", "Sơn Tùng M-TP"), ("Có Chắc Yêu Là Đây", "Sơn Tùng M-TP"), ("Hơn Cả Yêu", "Đức Phúc"),
            ("Ngày Đầu Tiên", "Đức Phúc"), ("Ánh Nắng Của Anh", "Đức Phúc"), ("Em Đồng Ý", "Đức Phúc, 911"),
            ("Tình Yêu Màu Hồng", "Hồ Văn Quý, Xám"), ("Ngày Em Đẹp Nhất", "Tama"), ("Từng Ngày Yêu Em", "buitruonglinh"),
            ("Trốn Tìm", "Đen, MTV"), ("Có Em Chờ", "MIN, Mr. A"), ("Die With A Smile", "Lady Gaga, Bruno Mars"),
            ("Perfect", "Ed Sheeran"), ("Until I Found You", "Stephen Sanchez"),
        ],
    },
    "vui": {
        "label": "Vui vẻ / sôi động / quẩy",
        "songs": [
            ("Hãy Trao Cho Anh", "Sơn Tùng M-TP, Snoop Dogg"), ("See Tình", "Hoàng Thùy Linh"),
            ("Để Mị Nói Cho Mà Nghe", "Hoàng Thùy Linh"), ("Gieo Quẻ", "Hoàng Thùy Linh, Đen"),
            ("Bắc Bling", "Hòa Minzy"), ("Thị Mầu", "Hòa Minzy"), ("Bật Tình Yêu Lên", "Hòa Minzy, Tăng Duy Tân"),
            ("Cắt Đôi Nỗi Sầu", "Tăng Duy Tân"), ("Bánh Mì Không", "Đạt G, DuUyên"), ("Răng Khôn", "Phí Phương Anh, RIN9"),
            ("Đi Đu Đưa Đi", "Bích Phương"), ("Bùa Yêu", "Bích Phương"), ("Chúng Ta Của Tương Lai", "Sơn Tùng M-TP"),
            ("APT.", "ROSÉ, Bruno Mars"), ("Espresso", "Sabrina Carpenter"),
        ],
    },
    "dong_luc": {
        "label": "Động lực / gia đình / nhớ nhà / tự hào",
        "songs": [
            ("Đi Về Nhà", "Đen, JustaTee"), ("Mang Tiền Về Cho Mẹ", "Đen, Nguyên Thảo"), ("Hai Triệu Năm", "Đen, Biên"),
            ("Đi Để Trở Về", "Soobin Hoàng Sơn"), ("Ai Cũng Phải Bắt Đầu Từ Đâu Đó", "HIEUTHUHAI"),
            ("Còn Gì Đẹp Hơn", "Nguyễn Hùng"), ("Một Vòng Việt Nam", "Tùng Dương"),
            ("Không Phải Dạng Vừa Đâu", "Sơn Tùng M-TP"), ("Một Nhà", "Da LAB"),
        ],
    },
}


def format_mood_catalog() -> str:
    lines = []
    for key, grp in MOOD_HIT_CATALOG.items():
        songs = "; ".join(f"{t} - {a}" for t, a in grp["songs"])
        lines.append(f"[{key}] {grp['label']}: {songs}")
    return "\n".join(lines)


def catalog_songs_for_mood(mood: str) -> List[Dict[str, str]]:
    grp = MOOD_HIT_CATALOG.get((mood or "").strip().lower())
    if not grp:
        return []
    return [{"title": t, "artist": a, "reason": f"Bản hit hợp tâm trạng {grp['label'].split(' / ')[0].lower()}"}
            for t, a in grp["songs"]]


async def chat_with_em_be_floor_2(
    room_context: str,
    sender_name: str,
    user_message: str,
    youtube_candidates: Optional[List[Dict[str, Any]]] = None,
    avoid_titles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Conversational AI assistant '@Em bé floor 2' that knows full room context & can suggest trending songs."""
    clean_q = strip_ai_mention(user_message) or "Chào Em bé floor 2!"
    avoid_block = ""
    if avoid_titles:
        avoid_block = "\n\n=== BÀI ĐÃ CÓ TRONG PHÒNG / VỪA GỢI Ý (KHÔNG GỢI Ý LẠI) ===\n" + "; ".join(avoid_titles[:30])

    cand_block = ""
    if youtube_candidates:
        c_lines = [
            f"  - \"{c.get('title', '')}\" — {c.get('author', '')} ({c.get('duration_str', '')})"
            for c in youtube_candidates[:10]
            if c.get("title")
        ]
        if c_lines:
            cand_block = "\n\n=== ỨNG VIÊN BÀI HÁT THẬT & ĐANG TRENDING TRÊN YOUTUBE ===\n" + "\n".join(c_lines)

    system_prompt = (
        f"Bạn là '{AI_BOT_NAME}' — trợ lý AI dễ thương, thông minh, hoạt bát, thân thiện của phòng nghe nhạc Floor 2 Studio.\n"
        "Phong cách & Quy tắc:\n"
        "- Trả lời 100% bằng TIẾNG VIỆT tự nhiên, gần gũi, dí dỏm (TUYỆT ĐỐI KHÔNG chèn chữ Hán / tiếng Trung vào câu trả lời), xưng 'mình' hoặc 'Em bé' và gọi tên thành viên khi phù hợp.\n"
        "- Có thể tán gẫu MỌI CHỦ ĐỀ với thành viên (âm nhạc, đời sống, công việc, code, tâm sự, đùa vui, phim ảnh...), KHÔNG bị giới hạn chỉ hỏi về nhạc.\n"
        "- Luôn tận dụng ngữ cảnh phòng (tên phòng, bài đang phát & nội dung lời bài hát, bài sắp phát, gu nhạc từng thành viên, mạch chat gần đây) để trả lời sâu sắc và đúng trọng tâm.\n"
        "- Giữ câu trả lời súc tích, dễ đọc trong khung chat, dùng 1–3 emoji vừa phải.\n"
        "- TRÌNH BÀY `reply` CÓ BỐ CỤC, KHÔNG viết dồn 1 đoạn dài (dùng ký tự xuống dòng \\n trong chuỗi JSON):\n"
        "  • Tán gẫu thường: 1–2 đoạn ngắn (mỗi đoạn 1–2 câu), các đoạn cách nhau bằng 1 dòng trống (\\n\\n).\n"
        "  • Khi gợi ý nhạc: dòng 1 là câu đồng cảm / mở đầu ngắn; dòng trống; danh sách mỗi bài 1 dòng dạng "
        "'- **Tên bài** – Ca sĩ: lý do ngắn (tối đa 12 từ)'; dòng trống; 1 câu chốt ấm áp.\n"
        "  • Khi liệt kê ý / hướng dẫn: dùng gạch đầu dòng '- ' hoặc '1. '. Dùng **in đậm** cho tên bài hoặc ý chính, không lạm dụng.\n"
        "  • KHÔNG dùng tiêu đề markdown (#), bảng hay code block.\n"
        "- Khi người dùng nhờ gợi ý bài hát / tìm nhạc (kể cả nói gián tiếp như 'tôi đang buồn quá', 'có bài nào hợp lúc này không'):\n"
        "  1. XÁC ĐỊNH ĐÚNG TÂM TRẠNG trong tin nhắn mới nhất và mạch chat gần đây (buồn, cô đơn, mưa/hoài niệm, chill, yêu, vui, động lực) rồi đặt vào trường `mood` bằng đúng 1 mã trong ngoặc vuông của KHO HIT bên dưới.\n"
        "  2. Gợi ý đúng 3 bài KHỚP ĐÚNG TÂM TRẠNG ĐÓ. Người buồn thì gợi ý bài buồn, an ủi; TUYỆT ĐỐI KHÔNG đưa bài tình yêu vui tươi cho người đang buồn.\n"
        "  3. Ưu tiên chọn từ KHO HIT TRENDING THEO TÂM TRẠNG bên dưới (đều là bài có thật, đang hoặc từng rất trending). Có thể thêm bản hit có thật khác nếu chắc chắn đúng tên bài và ca sĩ. Đa dạng ca sĩ, mỗi ca sĩ tối đa 1 bài.\n"
        "  4. Nếu người dùng nêu tên một bài hoặc ca sĩ cụ thể, bài đầu tiên phải là đúng bản gốc chính chủ đó.\n"
        "  5. Gu nhạc của thành viên và lịch sử phòng CHỈ để tham khảo; KHÔNG gợi ý lại các bài trong mục 'KHÔNG GỢI Ý LẠI'.\n"
        "  6. KHÔNG chọn bản Interlude/Intro/Outro, remix, cover; KHÔNG bịa tên bài.\n"
        "  7. Trong `reply`, đồng cảm đúng tâm trạng người dùng và liệt kê CHÍNH XÁC các bài trong `suggested_songs` theo đúng thứ tự, mỗi bài 1 dòng như quy tắc trình bày ở trên.\n"
        "- Nếu người dùng chỉ tán gẫu bình thường (không xin gợi ý nhạc), để `suggested_songs` là mảng rỗng `[]` và `mood` là chuỗi rỗng.\n\n"
        f"=== KHO HIT TRENDING THEO TÂM TRẠNG ===\n{format_mood_catalog()}\n\n"
        "LUÔN trả về đúng định dạng JSON:\n"
        '{"reply": "Câu mở đầu đồng cảm 🥺\\n\\n- **Tên bài 1** – Ca sĩ: lý do ngắn\\n- **Tên bài 2** – Ca sĩ: lý do ngắn\\n\\nCâu chốt ấm áp 💕", '
        '"mood": "buon", "suggested_songs": [{"title": "Tên bài hát chuẩn", "artist": "Tên ca sĩ chính xác"}]}'
    )

    user_prompt = (
        f"=== NGỮ CẢNH PHÒNG HIỆN TẠI ===\n{room_context}"
        f"{avoid_block}"
        f"{cand_block}\n\n"
        f"=== TIN NHẮN MỚI TỪ {sender_name} ===\n{clean_q}"
    )

    raw = await groq_chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.6,
        max_tokens=650,
        json_mode=True,
    )

    parsed = _parse_json_lenient(raw or "")
    if parsed and isinstance(parsed.get("reply"), str) and parsed["reply"].strip():
        sugs: List[Dict[str, str]] = []
        raw_sugs = parsed.get("suggested_songs")
        mood = str(parsed.get("mood") or "").strip().lower()
        if isinstance(raw_sugs, list):
            for s in raw_sugs[:5]:
                if isinstance(s, dict) and s.get("title"):
                    sugs.append({
                        "title": _strip_stray_cjk(str(s.get("title", "")).strip())[:80],
                        "artist": _strip_stray_cjk(str(s.get("artist", "")).strip())[:60],
                    })
        return {
            "reply": _strip_stray_cjk(parsed["reply"].strip())[:900],
            "suggested_songs": sugs,
            "mood": mood if mood in MOOD_HIT_CATALOG else "",
        }

    # Graceful fallback if JSON parsing failed but plain text exists
    if raw and not raw.startswith("{"):
        return {"reply": _strip_stray_cjk(raw.strip())[:900], "suggested_songs": []}

    return {
        "reply": f"Ủa {sender_name} ơi, tín hiệu của Em bé floor 2 vừa hơi chập chờn xíu 🥺 Bạn gọi lại mình sau vài giây nhé!",
        "suggested_songs": [],
    }


async def generate_ai_dj_playlist(
    room_context: str,
    vibe_prompt: str,
    count: int = 6,
    exclude_titles: Optional[List[str]] = None,
    youtube_candidates: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Generate a curated trending-first playlist matching a natural-language vibe, song query & room taste."""
    count = max(1, min(8, int(count)))
    ask_count = min(10, count + 2)
    excl = ", ".join(exclude_titles[:15]) if exclude_titles else "(không có)"

    cand_block = ""
    if youtube_candidates:
        c_lines = [
            f"  - \"{c.get('title', '')}\" — {c.get('author', '')} ({c.get('duration_str', '')})"
            for c in youtube_candidates[:14]
            if c.get("title")
        ]
        if c_lines:
            cand_block = (
                "\n\n=== ỨNG VIÊN BÀI HÁT THẬT & ĐANG TRENDING TRÊN YOUTUBE (ĐÃ XÁC THỰC) ===\n"
                + "\n".join(c_lines)
            )

    system_prompt = (
        f"Bạn là '{AI_BOT_NAME}' trong vai trò Giám đốc Âm nhạc & AI DJ chuyên nghiệp của Floor 2 Studio.\n"
        "Nhiệm vụ: Tạo danh sách bài hát ĐANG TRENDING hoặc ĐÃ TỪNG RẤT TRENDING (Hit Quốc Dân hàng chục/trăm triệu view) khớp chính xác nhất với yêu cầu của người dùng và gu của phòng.\n\n"
        "QUY TẮC BẮT BUỘC:\n"
        "1. KHỚP CHÍNH XÁC TÊN BÀI HÁT / CA SĨ NGƯỜI DÙNG NHẬP:\n"
        "   - Nếu `YÊU CẦU VIBE` là tên một bài hát, một cụm từ trong tên bài hát hoặc tên ca sĩ (ví dụ: 'mưa tháng 6' -> 'Mưa Tháng Sáu - Văn Mai Hương, GREY D, Trung Quân'; 'nàng thơ' -> 'Nàng Thơ - Hoàng Dũng'; 'cắt đôi nỗi sầu' -> 'Cắt Đôi Nỗi Sầu - Tăng Duy Tân'...), thì BÀI ĐẦU TIÊN (`songs[0]`) BẮT BUỘC PHẢI LÀ BẢN HIT GỐC CHÍNH CHỦ ĐÓ!\n"
        "   - Các bài tiếp theo (`songs[1..]`) phải là những SIÊU HIT ĐANG TRENDING hoặc ĐÃ TỪNG RẤT TRENDING có cùng chủ đề/tâm trạng với bài #1 (ví dụ người dùng gõ 'mưa tháng 6' thì #1 là 'Mưa Tháng Sáu - Văn Mai Hương, GREY D, Trung Quân', theo sau là các siêu hit mưa/buồn cực nổi tiếng như 'Dự Báo Thời Tiết Hôm Nay Mưa - GREY D', 'Dấu Mưa - Trung Quân', 'Em Gái Mưa - Hương Tràm', 'Chuyện Đôi Ta - Emcee L (Da LAB) ft. Muộii', 'Để Tôi Ôm Em Bằng Giai Điệu Này - Kai Đinh, MIN, GREY D', 'Một Ngàn Nỗi Đau - Văn Mai Hương', 'Tháng Tư Là Lời Nói Dối Của Em - Hà Anh Tuấn', 'Nàng Thơ - Hoàng Dũng', 'Bước Qua Mùa Cô Đơn - Vũ.'...).\n"
        "2. ƯU TIÊN TUYỆT ĐỐI NHẠC ĐANG TRENDING & ĐÃ TỪNG RẤT TRENDING (MEGA-HITS):\n"
        f"   - Chọn {ask_count} bài hát CÓ THẬT 100%, là các bản HIT LỚN (Top Trending YouTube Việt Nam, Làn Sóng Xanh, Spotify Top 50, ZingChart, Billboard).\n"
        "   - TUYỆT ĐỐI CẤM chọn các bản Interlude, Intro, Outro, Skit, Demo, nhạc nền ngắn dưới 2 phút (ví dụ CẤM chọn 'mưa chưa tạnh (Interlude)'), hoặc video chia sẻ/tin tức/giảng pháp không phải bài hát!\n"
        "   - TUYỆT ĐỐI KHÔNG tự bịa tên bài hát hoặc ghép sai tên bài hát với ca sĩ.\n"
        "3. TẬN DỤNG DANH SÁCH ỨNG VIÊN YOUTUBE ĐÃ XÁC THỰC:\n"
        "   - Nếu trong `=== ỨNG VIÊN BÀI HÁT THẬT & ĐANG TRENDING TRÊN YOUTUBE ===` có bản hit chính thức khớp với yêu cầu hoặc hợp vibe, hãy ưu tiên đưa vào danh sách và kết hợp thêm các siêu hit quốc dân chuẩn xác nhất.\n"
        "4. ĐA DẠNG & NGÔN NGỮ CHUẨN:\n"
        "   - Đa dạng ca sĩ (mỗi ca sĩ tối đa 1-2 bài trừ khi người dùng yêu cầu riêng 1 ca sĩ). Không chọn trùng danh sách `BÀI CẦN TRÁNH TRÙNG`.\n"
        "   - `vibe_note` và `reason` viết 100% bằng TIẾNG VIỆT tự nhiên, truyền cảm (TUYỆT ĐỐI KHÔNG dùng chữ Hán / tiếng Trung như 钢琴).\n\n"
        "LUÔN trả về JSON hợp lệ:\n"
        '{"vibe_note": "1 câu giới thiệu vibe cuốn hút", "songs": [{"title": "Tên bài hát chính xác", "artist": "Tên ca sĩ chính xác", "reason": "Lý do ngắn gọn vì sao bản hit này hợp vibe"}]}'
    )

    user_prompt = (
        f"=== NGỮ CẢNH PHÒNG ===\n{room_context}\n\n"
        f"=== BÀI CẦN TRÁNH TRÙNG ===\n{excl}"
        f"{cand_block}\n\n"
        f"=== YÊU CẦU VIBE / TỪ KHÓA CỦA NGƯỜI DÙNG ===\n{vibe_prompt.strip() or 'Chọn những bản hit đang trending và hợp gu hiện tại của phòng, nghe thật chill và cuốn'}"
    )

    raw = await groq_chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.45,
        max_tokens=750,
        json_mode=True,
    )

    parsed = _parse_json_lenient(raw or "")
    songs: List[Dict[str, str]] = []
    vibe_note = "Set nhạc hit đang trending được Em bé floor 2 tuyển chọn riêng cho phòng mình nè ✨"
    if parsed:
        if isinstance(parsed.get("vibe_note"), str) and parsed["vibe_note"].strip():
            vibe_note = _strip_stray_cjk(parsed["vibe_note"].strip())[:180]
        if isinstance(parsed.get("songs"), list):
            for s in parsed["songs"][:ask_count]:
                if isinstance(s, dict) and s.get("title"):
                    songs.append({
                        "title": _strip_stray_cjk(str(s.get("title", "")).strip())[:80],
                        "artist": _strip_stray_cjk(str(s.get("artist", "")).strip())[:60],
                        "reason": _strip_stray_cjk(str(s.get("reason", "")).strip())[:110],
                    })
    return {"vibe_note": vibe_note, "songs": songs}


async def analyze_room_vibe(room_context: str, cache_key: str) -> Dict[str, Any]:
    """Analyze the room's listening history & active members into a compact Room Vibe Card."""
    cached = vibe_cache.get(cache_key)
    if cached is not None:
        return cached

    system_prompt = (
        f"Bạn là '{AI_BOT_NAME}', chuyên gia đọc vị gu âm nhạc của phòng Floor 2.\n"
        "Dựa vào bài đang phát, hàng đợi, lịch sử nghe và từng thành viên trong phòng, hãy phân tích tâm trạng & gu nhạc chung.\n"
        "LUÔN trả về JSON:\n"
        '{"emoji": "🌙", "title": "Tên vibe 3-5 từ", "genres": [{"name": "Thể loại", "pct": 60}, {"name": "Thể loại 2", "pct": 40}], "summary": "1-2 câu nhận xét dí dỏm, tinh tế bằng tiếng Việt về gu nhạc phòng và nhắc khéo bài/thành viên nếu có."}'
    )

    raw = await groq_chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": room_context},
        ],
        temperature=0.72,
        max_tokens=320,
        json_mode=True,
    )

    parsed = _parse_json_lenient(raw or "")
    if parsed and parsed.get("title"):
        raw_genres = parsed.get("genres") if isinstance(parsed.get("genres"), list) else []
        genres = []
        for g in raw_genres[:3]:
            if isinstance(g, dict) and g.get("name"):
                try:
                    pct = max(5, min(100, int(g.get("pct", 50))))
                except (TypeError, ValueError):
                    pct = 50
                genres.append({"name": str(g["name"]).strip()[:24], "pct": pct})
        result = {
            "emoji": str(parsed.get("emoji") or "✨").strip()[:4],
            "title": str(parsed.get("title")).strip()[:48],
            "genres": genres or [{"name": "Đa thể loại", "pct": 100}],
            "summary": str(parsed.get("summary") or "Phòng đang có một vibe nghe nhạc rất riêng!").strip()[:240],
        }
        vibe_cache.set(cache_key, result, ttl=300)
        return result

    return {
        "emoji": "🎧",
        "title": "Đang khám phá gu nhạc",
        "genres": [{"name": "Chill Mix", "pct": 100}],
        "summary": "Thêm vài bài hát yêu thích để Em bé floor 2 đọc vị chuẩn gu âm nhạc của phòng mình nhé!",
    }


async def normalize_track_for_lyrics(title: str, author: str = "") -> Optional[Tuple[str, str]]:
    """AI Smart Lyric Title Cleaner fallback for noisy YouTube titles (mashups, covers, vlog MVs)."""
    if not key_rotator.is_configured():
        return None
    cache_key = f"{(title or '').strip().lower()}::{(author or '').strip().lower()}"
    cached = lyric_norm_cache.get(cache_key)
    if cached is not None:
        return cached

    raw = await groq_chat(
        [
            {
                "role": "system",
                "content": (
                    "Extract the clean official song title and main artist name from a noisy YouTube video title "
                    "so it can be matched on LRCLIB lyrics database. Remove MV/Official/Cover/Remix/Lyrics/Vietsub/4K/Live tags.\n"
                    'Return JSON ONLY: {"title": "clean song title", "artist": "main artist name"}'
                ),
            },
            {
                "role": "user",
                "content": f"YouTube title: {title}\nChannel: {author}",
            },
        ],
        temperature=0.1,
        max_tokens=100,
        json_mode=True,
    )
    parsed = _parse_json_lenient(raw or "")
    if parsed and isinstance(parsed.get("title"), str) and parsed["title"].strip():
        res = (parsed["title"].strip()[:100], str(parsed.get("artist") or "").strip()[:80])
        lyric_norm_cache.set(cache_key, res, ttl=86400)
        return res
    return None


# Each intro randomly picks one persona so consecutive songs never sound the same
MC_STYLES: Tuple[Tuple[str, str], ...] = (
    ("hai_huoc", "HÀI HƯỚC: dí dỏm, tếu táo, có 1 câu đùa nhẹ hoặc chơi chữ liên quan tên bài / người yêu cầu, nghe là muốn bật cười."),
    ("chuyen_nghiep", "CHUYÊN NGHIỆP: giọng MC đài FM chuẩn mực, lịch sự, gãy gọn, giới thiệu rõ tên bài và nghệ sĩ như bản tin âm nhạc."),
    ("pha_tro", "PHA TRÒ / CÀ KHỊA: trêu yêu người yêu cầu bài một cách thân thiện (ví dụ đoán tâm trạng của họ), tinh nghịch nhưng không xúc phạm."),
    ("nhe_nhang", "NHẸ NHÀNG / THƯ GIÃN: thủ thỉ, êm dịu, chậm rãi, gợi khung cảnh hoặc cảm xúc thư giãn, như radio đêm khuya."),
    ("soi_dong", "SÔI ĐỘNG / BÙNG CHÁY: năng lượng cao như sàn nhảy, hô hào cả phòng quẩy lên, câu ngắn dồn dập."),
    ("tho_mong", "THƠ MỘNG / KỂ CHUYỆN: mở đầu bằng 1 hình ảnh hoặc câu chuyện nhỏ rồi dẫn vào bài, giàu hình ảnh."),
    ("tam_tinh", "TÂM TÌNH / GỬI GẮM: như đọc lời nhắn của thính giả, người yêu cầu gửi tặng bài này cho cả phòng kèm một lời chúc."),
)

_TITLE_NOISE_RE = re.compile(
    r"\b(official|music\s*video|mv|m/v|lyrics?|lyric\s*video|audio|visualizer|vietsub|engsub|"
    r"4k|hd|hq|1080p|720p|full|karaoke)\b",
    re.IGNORECASE,
)


def clean_song_title(title: str) -> str:
    """Strip YouTube noise (Official MV, 4K, | channel, (Lyrics)...) for natural spoken intros."""
    t = (title or "").strip()
    t = re.sub(r"[\(\[【][^\)\]】]*[\)\]】]", " ", t)  # drop bracketed tags
    t = re.split(r"\s*[\|｜]\s*|\s#", t)[0] if t else t
    t = _TITLE_NOISE_RE.sub(" ", t)
    t = re.sub(r"[\"'“”‘’]+", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—:|,.")
    return t or (title or "").strip()


def _fold(text: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", (text or "").lower()).replace("đ", "d")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def _script_mentions_title(script: str, clean_title: str) -> bool:
    """Guard against the LLM introducing the wrong song (e.g. the previous track)."""
    tokens = [w for w in _fold(clean_title).split() if len(w) >= 2]
    if not tokens:
        return True
    folded = f" {' '.join(_fold(script).split())} "
    hits = sum(1 for w in tokens if f" {w} " in folded)
    return hits >= max(1, min(3, (len(tokens) + 1) // 2))


def build_mc_context(room: Any, track: Any) -> str:
    """Compact MC-only context focused on the target song (no 'now playing' ambiguity)."""
    lines = [f"Phòng: \"{room.name}\" | Online: {len(room.users)} người"]
    target_uid = getattr(track, "uid", None)
    prev = [t for t in room.history[-4:] if getattr(t, "uid", None) != target_uid]
    if prev:
        lines.append("Vài bài phòng vừa nghe xong (CHỈ để tham khảo vibe, KHÔNG được giới thiệu lại): "
                     + "; ".join(clean_song_title(t.title) for t in reversed(prev)))
    recent_chat = [
        m for m in room.messages[-15:]
        if m.get("type") == "chat" and (m.get("text") or "").strip()
    ][-4:]
    if recent_chat:
        lines.append("Chat gần đây: " + " | ".join(
            f"{m.get('user_name', 'Khách')}: {(m.get('text') or '')[:80]}" for m in recent_chat
        ))
    return "\n".join(lines)


def _fallback_mc_script(clean_t: str, author: str, adder: str) -> str:
    by = f" của {author}" if author else ""
    templates = (
        f"Chào cả phòng Floor 2, tiếp theo là ca khúc {clean_t}{by} theo yêu cầu của {adder}, mời mọi người cùng thả lỏng và lắng nghe nhé.",
        f"A lô a lô, {adder} vừa gọi một món đặc biệt, đó là {clean_t}{by}. Lên nhạc thôi cả nhà ơi.",
        f"Đài Floor 2 xin gửi tới quý thính giả ca khúc {clean_t}{by}, do bạn {adder} yêu cầu.",
        f"Nghe nói {adder} đang có tâm sự, nên đã chọn ngay {clean_t}{by}. Cả phòng cùng nghe xem có đúng không nhé.",
        f"Một chút nhẹ nhàng cho khoảnh khắc này, {clean_t}{by}, món quà nhỏ từ {adder} gửi tới mọi người.",
        f"Cả phòng ơi vặn to loa lên nào, {clean_t}{by} đến từ {adder}, quẩy thôi.",
    )
    return secrets.choice(templates)


async def generate_mc_radio_script(
    room_context: str,
    track: Dict[str, Any],
    style: Optional[Tuple[str, str]] = None,
) -> str:
    """Generate a short Vietnamese female Radio MC intro in a randomly chosen style."""
    title = track.get("title") or "bài hát tiếp theo"
    author = (track.get("author") or "").replace(" - Topic", "").replace("VEVO", "").strip()
    adder = track.get("added_by_name") or "một thành viên"
    clean_t = clean_song_title(title)
    style = style or secrets.choice(MC_STYLES)

    system_prompt = (
        f"Bạn là nữ MC Radio tên '{AI_BOT_NAME}' của đài FM 102.4 Floor 2, biến hóa nhiều phong cách dẫn.\n"
        f"PHONG CÁCH LẦN NÀY: {style[1]}\n\n"
        "Quy tắc:\n"
        "- Viết 1 đến 2 câu lời dẫn tiếng Việt (tối đa 40 từ) để giới thiệu ĐÚNG bài hát trong mục 'BÀI CẦN GIỚI THIỆU'.\n"
        "- BẮT BUỘC nhắc nguyên văn tên bài hát đã cho và tên người yêu cầu. Có thể nhắc nghệ sĩ nếu tên nghe tự nhiên.\n"
        "- TUYỆT ĐỐI KHÔNG giới thiệu hay nhắc tên bất kỳ bài hát nào khác.\n"
        "- Không bịa thông tin về bài hát nếu không chắc. Không mở đầu bằng 'Chào cả phòng' một cách rập khuôn.\n"
        "- Viết THUẦN TIẾNG VIỆT: KHÔNG dùng từ tiếng Anh (cấm các từ như hype, chill, vibe, DJ, let's go, okay, order, volume, playlist, party...), "
        "chỉ giữ nguyên tên bài hát và tên nghệ sĩ đúng như đã cho.\n"
        "- KHÔNG emoji, KHÔNG ngoặc kép, KHÔNG markdown, KHÔNG chữ Hán. Chỉ trả về lời dẫn, không giải thích."
    )

    user_prompt = (
        f"=== KHÔNG KHÍ PHÒNG ===\n{room_context[:900]}\n\n"
        f"=== BÀI CẦN GIỚI THIỆU ===\nTên bài: {clean_t}\nNghệ sĩ: {author or 'không rõ'}\nNgười yêu cầu: {adder}"
    )

    for _ in range(2):
        raw = await groq_chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.95,
            max_tokens=160,
            json_mode=False,
        )
        if not raw:
            break
        # Clean out quotes, markdown, and emojis so TTS reads smoothly with natural pauses
        script = re.sub(r"[*_`#\"“”\[\]\(\)]+", "", raw)
        script = re.sub(r"\s+", " ", script).strip()
        if len(script) >= 10 and _script_mentions_title(script, clean_t):
            return script[:260]

    return _fallback_mc_script(clean_t, author, adder)


def _split_tts_chunks(text: str, max_len: int = 165) -> List[str]:
    """Split Vietnamese text on natural sentence/comma boundaries for smooth TTS synthesis."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_len:
        return [text] if text else []
    parts = re.split(r"(?<=[.!?,;])\s+", text)
    chunks: List[str] = []
    buf = ""
    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) + 1 <= max_len:
            buf = f"{buf} {p}".strip()
        else:
            if buf:
                chunks.append(buf)
            buf = p[:max_len]
    if buf:
        chunks.append(buf)
    return chunks


NEURAL_VOICE = "vi-VN-HoaiMyNeural"
# Slightly different prosody variants: each is a fresh request, which dodges Edge TTS's intermittent "no audio" replies
_EDGE_VARIANTS: Tuple[Tuple[str, str], ...] = (("+0%", "+0Hz"), ("-2%", "+1Hz"), ("+3%", "+2Hz"), ("-4%", "-1Hz"))


async def _edge_tts_once(text: str, rate: str, pitch: str, timeout: float = 6.0) -> Optional[bytes]:
    try:
        import edge_tts  # type: ignore
    except Exception:
        return None

    async def _run() -> bytes:
        comm = edge_tts.Communicate(
            text, voice=NEURAL_VOICE, rate=rate, pitch=pitch, connect_timeout=4, receive_timeout=6
        )
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                buf.extend(chunk["data"])
        return bytes(buf)

    try:
        data = await asyncio.wait_for(_run(), timeout=timeout)
        return data if len(data) > 1024 else None
    except Exception:
        return None


async def _edge_tts_with_retries(text: str) -> Optional[bytes]:
    for rate, pitch in _EDGE_VARIANTS:
        data = await _edge_tts_once(text, rate, pitch)
        if data:
            return data
        await asyncio.sleep(0.15)
    return None


async def synthesize_neural_female_tts(text: str) -> Optional[bytes]:
    """Natural Microsoft neural female voice (HoaiMy) with retries + clause-level fallback. No robotic voices."""
    clean_text = re.sub(r"\s+", " ", (text or "").strip())
    if not clean_text:
        return None

    async def _robust() -> Optional[bytes]:
        whole = await _edge_tts_with_retries(clean_text)
        if whole:
            return whole
        # Edge occasionally rejects a specific full sentence; synthesize clause by clause and join the MP3 frames
        clauses = [c.strip() for c in re.split(r"(?<=[,.!?;:])\s+", clean_text) if c.strip()]
        if len(clauses) < 2:
            return None
        out = bytearray()
        for cl in clauses:
            part = await _edge_tts_with_retries(cl)
            if not part:
                return None
            out.extend(part)
        return bytes(out)

    try:
        return await asyncio.wait_for(_robust(), timeout=26.0)
    except Exception:
        return None


async def synthesize_google_fallback_tts(text: str) -> Optional[bytes]:
    """Last-resort Google Vietnamese TTS (robotic but always available)."""
    clean_text = re.sub(r"\s+", " ", (text or "").strip())
    if not clean_text:
        return None
    try:
        client = _get_tts_client()
        chunks = _split_tts_chunks(clean_text, max_len=165)
        combined = bytearray()
        for ch in chunks:
            resp = await client.get(
                "https://translate.google.com/translate_tts",
                params={"ie": "UTF-8", "tl": "vi", "client": "tw-ob", "q": ch},
            )
            if resp.status_code == 200 and len(resp.content) > 512:
                combined.extend(resp.content)
        if len(combined) > 1024:
            return bytes(combined)
    except Exception as exc:
        print(f"[ai tts err] {exc}")

    return None


EDGE_BUDGET_SEC = 4.5
EDGE_COOLDOWN_SEC = 120.0
_edge_down_until: float = 0.0
_mc_inflight: Dict[str, "asyncio.Task[Dict[str, Any]]"] = {}


async def synthesize_mc_audio(text: str) -> Tuple[Optional[bytes], str]:
    """Fast Vietnamese MC audio: race Edge HoaiMy (preferred, capped at a few seconds) against Google vi TTS.

    Edge TTS sometimes stalls or returns NoAudioReceived for minutes; waiting on its retries made the MC
    start ~10-25s late. A circuit breaker skips Edge entirely while it is known to be failing.
    """
    global _edge_down_until
    clean_text = re.sub(r"\s+", " ", (text or "").strip())
    if not clean_text:
        return None, "browser"

    google_task = asyncio.create_task(synthesize_google_fallback_tts(clean_text))
    if time.time() >= _edge_down_until:
        async def _edge() -> Optional[bytes]:
            for rate, pitch in _EDGE_VARIANTS[:2]:
                data = await _edge_tts_once(clean_text, rate, pitch, timeout=EDGE_BUDGET_SEC)
                if data:
                    return data
            return None

        try:
            edge_bytes = await asyncio.wait_for(_edge(), timeout=EDGE_BUDGET_SEC)
        except Exception:
            edge_bytes = None
        if edge_bytes:
            google_task.cancel()
            return edge_bytes, NEURAL_VOICE
        _edge_down_until = time.time() + EDGE_COOLDOWN_SEC

    try:
        g_bytes = await asyncio.wait_for(google_task, timeout=8.0)
    except Exception:
        g_bytes = None
    return (g_bytes, "google-vi") if g_bytes else (None, "browser")


async def get_or_create_mc_intro(
    room_id: str,
    track: Dict[str, Any],
    room_context: str,
) -> Dict[str, Any]:
    """Generate & cache expressive female Radio MC intro (script + MP3 audio_url).

    Concurrent callers for the same track (prewarm + client request) share one in-flight generation.
    """
    track_uid = str(track.get("uid") or track.get("id") or "")
    cache_key = f"{room_id}:{track_uid}"
    cached = mc_intro_cache.get(cache_key)
    if cached is not None:
        return cached
    task = _mc_inflight.get(cache_key)
    if task is None or task.done():
        task = asyncio.create_task(_build_mc_intro(room_id, track, room_context, track_uid, cache_key))
        _mc_inflight[cache_key] = task
        task.add_done_callback(lambda _t, k=cache_key: _mc_inflight.pop(k, None))
    return await asyncio.shield(task)


async def _build_mc_intro(
    room_id: str,
    track: Dict[str, Any],
    room_context: str,
    track_uid: str,
    cache_key: str,
) -> Dict[str, Any]:
    # Avoid repeating the previous intro's style back-to-back in this room
    last_style = _last_mc_style.get(room_id)
    style = secrets.choice([s for s in MC_STYLES if s[0] != last_style] or list(MC_STYLES))
    _last_mc_style[room_id] = style[0]

    script = await generate_mc_radio_script(room_context, track, style=style)
    # Always a Vietnamese voice (never let the browser fall back to an English voice)
    audio_bytes, voice = await synthesize_mc_audio(script)

    audio_url = None
    if audio_bytes:
        audio_id = secrets.token_hex(8)
        mc_audio_cache.set(audio_id, audio_bytes, ttl=1800)
        audio_url = f"/api/ai/mc-audio/{audio_id}"
    else:
        voice = "browser"

    result = {
        "track_uid": track_uid,
        "script": script,
        "audio_url": audio_url,
        "voice": voice,
        "style": style[0],
    }
    # Cache successful neural audio for 30 min; retry neural synthesis sooner if it failed
    # Keep any real Vietnamese audio long enough for a prewarmed Up-Next intro to still be ready at play time
    mc_intro_cache.set(cache_key, result, ttl=1800 if voice == NEURAL_VOICE else (900 if audio_url else 30))
    return result
