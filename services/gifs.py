"""Fast Giphy GIF & Animated Sticker search service (no API key required) with rich curated defaults & TTL cache."""
import re
import urllib.parse
from typing import Dict, List, Optional
import httpx

from services.cache import TTLCache
from services.security import sanitize_giphy_id

GIPHY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
}

GIPHY_ID_EXTRACT_RE = re.compile(
    r"media[0-9]?\.giphy\.com/media/(?:v1\.[^/]+/)?([A-Za-z0-9]{10,22})/"
)

# 30 verified diverse curated GIFs for instant 0ms open
CURATED_GIFS = [
    ("GK7dGndK4kyOozR9Xk", "Mèo nhảy chill"),
    ("4oMoIbIQrvCjm", "Nghe nhạc chill"),
    ("l3q2zVr6cu95nF6O4", "Quẩy cực sung"),
    ("lHi4RqYaKlisaZBRmB", "Thả tim"),
    ("l0ExayQDzrI2xOb8A", "Cười xỉu"),
    ("RMwgs5kZqkRyhF24KK", "Anime chill"),
    ("D3umcr3d38kLfCpfAL", "Meme phản ứng"),
    ("Qf1glkIPvRMlXOO1Pv", "Mèo nhún nhảy"),
    ("dW5quSUTG1Q3v7XKkb", "Lắc lư theo nhạc"),
    ("7o6oVRTLFI2GY", "Party sôi động"),
    ("IhyGL914g9ShHdHVFy", "Haha cực mạnh"),
    ("sTzAXLbAnZ5NvpHd6m", "Anime thư giãn"),
    ("llJX1hxWL8IWDotJPd", "Yêu thương"),
    ("11hVniWaqh17Q4", "Meme bất ngờ"),
    ("93mS3A87wfmOySC29W", "Mèo vui vẻ"),
    ("GpyS1lJXJYupG", "Cười không nhặt được mồm"),
    ("tqfS3mgQU28ko", "Vibe cực chill"),
    ("XbJYBCi69nyVOffLIU", "Anime nghe nhạc"),
    ("IRzDdEx5tRCMY1feSX", "Cháy hết mình"),
    ("hg0Cl6fQzWv6NGTYP3", "Meme hài hước"),
    ("MKuEHA4bHXUhwXhovI", "Gửi trái tim"),
    ("Fn7q3cMgPZmqk", "Cười sảng khoái"),
    ("CW16nFVXLSQxSMUEMd", "Anime đêm khuya"),
    ("9ARfgisbZLrnggjgq0", "Meme chất lừ"),
    ("LBb735fuQwRKAVzN23", "Vui muốn xỉu"),
    ("MU56lYT1Ov07fVTsnM", "Anime lofi"),
    ("3fdGJimYYnehPjggrc", "Meme chuẩn bài"),
    ("XHeLeuirRbwptHhSWd", "Cười vỡ bụng"),
    ("fwtYgX4buYMJw0hJeA", "Anime dễ thương"),
    ("XHBQuYX3PiTRNaHADq", "Meme đỉnh cao"),
]

# 18 verified transparent animated Giphy Stickers (Nhãn dán động) for instant 0ms open
CURATED_STICKERS = [
    ("nBRm2zMTYALaREiLcJ", "Cute dễ thương"),
    ("HMZiZo1iz9tHvLFUKG", "Nhạc chill"),
    ("sThlS7PKkYKXfZkeDE", "Thả tim"),
    ("lHTJgttLitH3BKpEXr", "Dễ thương quá"),
    ("f6Pn3p24tQc3Cv3oe5", "Nốt nhạc vui"),
    ("qx3d4enif4rw3t5eng", "Yêu thương"),
    ("10a8AOSeP6Rqfu", "Nhãn dán cute"),
    ("FuIERg8ovKp9aXjIcN", "Quẩy nhạc"),
    ("ocXWdoEQdXbeEOaCPE", "Tim bay"),
    ("UMon0fuimoAN9ueUNP", "Mèo đáng yêu"),
    ("4hskq67xMqnRpYwBXe", "DJ lên nhạc"),
    ("fvN5KrNcKKUyX7hNIA", "Gửi yêu thương"),
    ("UlPKkzW1NebqnhVgFZ", "Vui vẻ"),
    ("FwDvNgZRhIGTu6xwl3", "Vibe âm nhạc"),
    ("c35VZ2wTAiZeZsUf8h", "Trái tim lấp lánh"),
    ("2w4p6knfL2m92cQ8eN", "Chào cả phòng"),
    ("jSfIrLP6dvzMQiaaw7", "Hát cùng nhau"),
    ("gkFMKf6KtUlerKf0jg", "Love vibe"),
]

_gif_client: Optional[httpx.AsyncClient] = None
gif_search_cache: TTLCache[List[Dict[str, str]]] = TTLCache("gif_search", max_size=150, default_ttl=1200)
sticker_search_cache: TTLCache[List[Dict[str, str]]] = TTLCache("sticker_search", max_size=150, default_ttl=1200)


def get_gif_cache_stats() -> Dict[str, object]:
    return gif_search_cache.stats()


def get_sticker_cache_stats() -> Dict[str, object]:
    return sticker_search_cache.stats()


def _get_gif_client() -> httpx.AsyncClient:
    global _gif_client
    if _gif_client is None or _gif_client.is_closed:
        _gif_client = httpx.AsyncClient(
            headers=GIPHY_HEADERS,
            timeout=httpx.Timeout(4.5, connect=2.5),
            follow_redirects=True,
        )
    return _gif_client


def _format_gif_item(gif_id: str, title: str = "GIF") -> Dict[str, str]:
    return {
        "id": gif_id,
        "title": title,
        "preview": f"https://media.giphy.com/media/{gif_id}/200w.gif",
        "url": f"https://media.giphy.com/media/{gif_id}/giphy.gif",
    }


def get_curated_gifs() -> List[Dict[str, str]]:
    return [_format_gif_item(gid, title) for gid, title in CURATED_GIFS]


def get_curated_stickers() -> List[Dict[str, str]]:
    return [_format_gif_item(gid, title) for gid, title in CURATED_STICKERS]


async def _scrape_giphy(slug: str, label: str, limit: int) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    try:
        client = _get_gif_client()
        resp = await client.get(f"https://giphy.com/search/{slug}")
        if resp.status_code == 200:
            raw_ids = GIPHY_ID_EXTRACT_RE.findall(resp.text)
            seen = set()
            for raw_id in raw_ids:
                gid = sanitize_giphy_id(raw_id)
                if not gid or gid in seen or gid == "XJtM2nNFCzT3etvzOB":
                    continue
                seen.add(gid)
                items.append(_format_gif_item(gid, label))
                if len(items) >= limit:
                    break
    except Exception as e:
        print(f"[giphy search error] {e}")
    return items


async def search_gifs(query: str, limit: int = 24) -> List[Dict[str, str]]:
    """Search animated GIFs on Giphy with strict ID sanitization and LRU+TTL cache."""
    clean_q = re.sub(r"\s+", " ", (query or "").strip())
    if not clean_q:
        return get_curated_gifs()

    cache_key = f"{clean_q.lower()}::{limit}"
    cached = gif_search_cache.get(cache_key)
    if cached is not None:
        return cached

    slug = urllib.parse.quote(re.sub(r"[^a-zA-Z0-9\u00C0-\u1EF9]+", "-", clean_q).strip("-"))
    if not slug:
        return get_curated_gifs()

    items = await _scrape_giphy(slug, clean_q, limit)
    if items:
        gif_search_cache.set(cache_key, items)
        return items

    return get_curated_gifs()


async def search_stickers(query: str, limit: int = 24) -> List[Dict[str, str]]:
    """Search transparent animated Stickers on Giphy with strict ID sanitization and LRU+TTL cache."""
    clean_q = re.sub(r"\s+", " ", (query or "").strip())
    if not clean_q:
        return get_curated_stickers()

    cache_key = f"{clean_q.lower()}::{limit}"
    cached = sticker_search_cache.get(cache_key)
    if cached is not None:
        return cached

    base_slug = re.sub(r"[^a-zA-Z0-9\u00C0-\u1EF9]+", "-", clean_q).strip("-")
    if not base_slug:
        return get_curated_stickers()
    slug = urllib.parse.quote(base_slug if base_slug.lower().endswith("stickers") else f"{base_slug}-stickers")

    items = await _scrape_giphy(slug, clean_q, limit)
    if items:
        sticker_search_cache.set(cache_key, items)
        return items

    return get_curated_stickers()
