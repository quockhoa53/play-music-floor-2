"""Let '@Em bé floor 2' see images & GIFs posted in chat (Groq vision via qwen/qwen3.8-27b).

- Animated GIFs are not accepted by the API, so Giphy GIFs/stickers use their still frame (giphy_s.gif).
- Uploaded images live only on this server, so they are sent inline as base64 data URLs.
- Captions are cached per media item and rate-limited per room to protect the free-tier quota.
"""
import base64
import re
import time
from typing import Dict, List, Optional

from services.ai import PRIMARY_MODEL, _strip_stray_cjk, groq_chat, key_rotator
from services.cache import TTLCache

caption_cache: TTLCache[str] = TTLCache("vision_caption", max_size=500, default_ttl=86400)
_room_caption_times: Dict[str, List[float]] = {}
MAX_CAPTIONS_PER_ROOM_PER_MIN = 12
MAX_INLINE_IMAGE_BYTES = 3_500_000
GIPHY_ID_RE = re.compile(r"/media/([A-Za-z0-9]+)/")

VISION_PROMPT = (
    "Bạn mô tả ảnh / GIF được gửi trong khung chat của một phòng nghe nhạc, để một trợ lý AI hiểu người gửi muốn nói gì.\n"
    "Viết 1-2 câu tiếng Việt (tối đa 35 từ): nội dung chính, biểu cảm / cảm xúc, chữ xuất hiện trên ảnh (trích nguyên văn nếu ngắn). "
    "Nếu là meme / reaction thì nói luôn ý nghĩa hoặc cảm xúc nó thể hiện. "
    "Không đoán danh tính người thật trừ khi tên xuất hiện rõ trên ảnh. Không dùng chữ Hán. Chỉ trả về câu mô tả."
)


def _room_allows(room_id: str) -> bool:
    now = time.time()
    times = [t for t in _room_caption_times.get(room_id, []) if now - t < 60]
    if len(times) >= MAX_CAPTIONS_PER_ROOM_PER_MIN:
        _room_caption_times[room_id] = times
        return False
    times.append(now)
    _room_caption_times[room_id] = times
    if len(_room_caption_times) > 500:
        _room_caption_times.clear()
    return True


def giphy_still_url(media_url: str) -> Optional[str]:
    m = GIPHY_ID_RE.search(media_url or "")
    return f"https://media.giphy.com/media/{m.group(1)}/giphy_s.gif" if m else None


async def describe_chat_media(
    *,
    room_id: str,
    media_type: str,
    media_url: str,
    image_bytes: Optional[bytes] = None,
    mime: str = "",
    caption_hint: str = "",
) -> str:
    """Short Vietnamese description of a chat image / GIF / sticker ('' when unavailable)."""
    if not key_rotator.is_configured():
        return ""
    cache_key = f"{media_type}:{media_url}"
    cached = caption_cache.get(cache_key)
    if cached is not None:
        return cached
    if not _room_allows(room_id):
        return ""

    if media_type in ("gif", "sticker"):
        image_url = giphy_still_url(media_url)
    elif media_type == "image" and image_bytes:
        if len(image_bytes) > MAX_INLINE_IMAGE_BYTES:
            return ""
        safe_mime = mime if mime in ("image/jpeg", "image/png", "image/webp") else "image/jpeg"
        image_url = f"data:{safe_mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    else:
        image_url = None
    if not image_url:
        return ""

    text_part = VISION_PROMPT + (f"\nNgười gửi viết kèm: \"{caption_hint[:150]}\"" if caption_hint else "")
    raw = await groq_chat(
        [{"role": "user", "content": [{"type": "text", "text": text_part}, {"type": "image_url", "image_url": {"url": image_url}}]}],
        temperature=0.3,
        max_tokens=160,
        model=PRIMARY_MODEL,  # gpt-oss is text-only; never fall back to it for images
    )
    caption = re.sub(r"\s+", " ", _strip_stray_cjk(raw or "")).strip().strip('"')[:240]
    caption_cache.set(cache_key, caption, ttl=86400 if caption else 300)
    return caption
