"""Fast Giphy GIF search service (no API key required) with curated defaults & TTL cache."""
import re
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple
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

# Verified curated GIF IDs for instant 0ms open
CURATED_GIFS = [
    ("GK7dGndK4kyOozR9Xk", "Mèo nhảy chill"),
    ("4oMoIbIQrvCjm", "Nghe nhạc chill"),
    ("l3q2zVr6cu95nF6O4", "Quẩy cực sung"),
    ("lHi4RqYaKlisaZBRmB", "Thả tim"),
    ("Qf1glkIPvRMlXOO1Pv", "Mèo nhún nhảy"),
    ("dW5quSUTG1Q3v7XKkb", "Lắc lư theo nhạc"),
    ("7o6oVRTLFI2GY", "Party sôi động"),
    ("llJX1hxWL8IWDotJPd", "Yêu thương"),
    ("93mS3A87wfmOySC29W", "Mèo vui vẻ"),
    ("tqfS3mgQU28ko", "Vibe cực chill"),
    ("IRzDdEx5tRCMY1feSX", "Cháy hết mình"),
    ("MKuEHA4bHXUhwXhovI", "Gửi trái tim"),
]

_gif_client: Optional[httpx.AsyncClient] = None
gif_search_cache: TTLCache[List[Dict[str, str]]] = TTLCache("gif_search", max_size=150, default_ttl=1200)


def get_gif_cache_stats() -> Dict[str, object]:
    return gif_search_cache.stats()


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


async def search_gifs(query: str, limit: int = 16) -> List[Dict[str, str]]:
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
                items.append(_format_gif_item(gid, clean_q))
                if len(items) >= limit:
                    break
    except Exception as e:
        print(f"[gif search error] {e}")

    if items:
        gif_search_cache.set(cache_key, items)
        return items

    return get_curated_gifs()
