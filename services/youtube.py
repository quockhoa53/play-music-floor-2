"""Ultra-fast YouTube metadata extraction and realtime search service."""
import asyncio
import json
import os
import re
import time
import urllib.parse
from typing import Optional, List, Dict, Any, Tuple
import httpx
import yt_dlp

YDL_OPTS = {
    "extract_flat": True,
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
    "no_color": True,
}

SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Persistent HTTP client for connection pooling (faster realtime search)
_http_client: Optional[httpx.AsyncClient] = None

# Simple TTL cache: query_lower -> (timestamp, results)
_search_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
CACHE_TTL_SEC = 300
CACHE_MAX_SIZE = 120

# Audio stream URL cache: video_id -> (timestamp, stream_url, content_type)
_audio_url_cache: Dict[str, Tuple[float, str, str]] = {}
AUDIO_CACHE_TTL_SEC = 1800


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            headers=SEARCH_HEADERS,
            timeout=6.0,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


def extract_video_id(url_or_id: str) -> Optional[str]:
    """Extract 11-character YouTube video ID from URL or bare ID."""
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()

    patterns = [
        r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    return None


def parse_duration_str(duration_str: str) -> int:
    """Convert '3:55' or '1:02:15' to total seconds."""
    if not duration_str:
        return 0
    parts = duration_str.strip().split(":")
    try:
        nums = [int(p) for p in parts]
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        if len(nums) == 1:
            return nums[0]
    except ValueError:
        pass
    return 0


def format_duration(seconds: Optional[int | float]) -> str:
    """Format duration in seconds to mm:ss or hh:mm:ss."""
    if not seconds or seconds < 0:
        return ""
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


async def get_video_oembed(video_id: str) -> Optional[Dict[str, Any]]:
    """Fast metadata fetch using YouTube oEmbed API (<150ms, no IP block)."""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
        client = _get_http_client()
        resp = await client.get(oembed_url, timeout=4.0)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "id": video_id,
                "title": data.get("title", f"YouTube Video ({video_id})"),
                "author": data.get("author_name", "YouTube"),
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "duration": 0,
                "duration_str": "",
                "views": "",
            }
    except Exception as e:
        print(f"[oembed error] {e}")
    return None


def _sync_ytdlp_extract(url_or_query: str, is_search: bool = False, limit: int = 8) -> Any:
    """Run yt-dlp in synchronous mode (fallback)."""
    opts = dict(YDL_OPTS)
    proxy = (os.getenv("YT_PROXY") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        if is_search:
            return ydl.extract_info(f"ytsearch{limit}:{url_or_query}", download=False)
        return ydl.extract_info(url_or_query, download=False)


async def _fast_scrape_search(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Direct async YouTube search via ytInitialData (~400-700ms)."""
    client = _get_http_client()
    resp = await client.get(
        "https://www.youtube.com/results",
        params={"search_query": query},
        timeout=5.0,
    )
    if resp.status_code != 200:
        return []

    m = re.search(r"var ytInitialData = (\{.*?\});</script>", resp.text)
    if not m:
        return []

    data = json.loads(m.group(1))
    sections = (
        data.get("contents", {})
        .get("twoColumnSearchResultsRenderer", {})
        .get("primaryContents", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
    )

    items: List[Dict[str, Any]] = []
    seen_ids = set()

    for sec in sections:
        contents = sec.get("itemSectionRenderer", {}).get("contents", [])
        for entry in contents:
            vr = entry.get("videoRenderer")
            if not vr:
                continue
            vid = vr.get("videoId")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)

            title = "".join(r.get("text", "") for r in vr.get("title", {}).get("runs", []))
            author = "".join(r.get("text", "") for r in vr.get("ownerText", {}).get("runs", []))
            dur_str = vr.get("lengthText", {}).get("simpleText", "")
            is_live = not dur_str and any(
                b.get("metadataBadgeRenderer", {}).get("style") == "BADGE_STYLE_TYPE_LIVE_NOW"
                for b in vr.get("badges", [])
            )
            if is_live:
                dur_str = "LIVE"

            views = vr.get("shortViewCountText", {}).get("simpleText", "")
            duration_sec = parse_duration_str(dur_str) if dur_str and dur_str != "LIVE" else 0

            items.append({
                "id": vid,
                "title": title or f"Video {vid}",
                "author": author or "YouTube",
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "duration": duration_sec,
                "duration_str": dur_str,
                "views": views,
            })

            if len(items) >= limit:
                return items

    return items


async def get_video_info(url_or_id: str, fast: bool = False) -> Optional[Dict[str, Any]]:
    """Get video metadata from URL or ID. If fast=True, returns oEmbed immediately."""
    video_id = extract_video_id(url_or_id)
    if not video_id:
        return None

    oembed_info = await get_video_oembed(video_id)
    if fast and oembed_info:
        return oembed_info

    try:
        loop = asyncio.get_running_loop()
        url = f"https://www.youtube.com/watch?v={video_id}"
        data = await loop.run_in_executor(None, _sync_ytdlp_extract, url, False, 1)
        if data:
            duration = data.get("duration") or 0
            title = data.get("title") or (oembed_info.get("title") if oembed_info else f"Video {video_id}")
            author = data.get("uploader") or data.get("channel") or (oembed_info.get("author") if oembed_info else "YouTube")
            return {
                "id": video_id,
                "title": title,
                "author": author,
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "duration": duration,
                "duration_str": format_duration(duration),
                "views": "",
            }
    except Exception as e:
        print(f"[yt-dlp extract error] {e}")

    if oembed_info:
        return oembed_info

    return {
        "id": video_id,
        "title": f"YouTube Video ({video_id})",
        "author": "YouTube",
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "duration": 0,
        "duration_str": "",
        "views": "",
    }


async def search_youtube(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Realtime YouTube search with fast HTML scraping, TTL cache, and yt-dlp fallback."""
    query = query.strip()
    if not query:
        return []

    # Direct YouTube URL / Video ID -> ultra-fast oEmbed path (<150ms)
    video_id = extract_video_id(query)
    if video_id:
        info = await get_video_info(video_id, fast=True)
        return [info] if info else []

    # Check TTL cache
    cache_key = f"{query.lower()}::{limit}"
    now = time.time()
    cached = _search_cache.get(cache_key)
    if cached and (now - cached[0]) < CACHE_TTL_SEC:
        return cached[1]

    items: List[Dict[str, Any]] = []

    # Primary: Fast direct async scrape
    try:
        items = await _fast_scrape_search(query, limit=limit)
    except Exception as e:
        print(f"[fast search error] {e}")

    # Fallback: yt-dlp if fast scrape failed or returned nothing
    if not items:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _sync_ytdlp_extract, query, True, limit)
            for entry in (result or {}).get("entries", []) or []:
                if not entry or not entry.get("id"):
                    continue
                v_id = entry.get("id")
                if entry.get("_type") == "url" and "playlist" in str(entry.get("url", "")):
                    continue
                duration = entry.get("duration") or 0
                items.append({
                    "id": v_id,
                    "title": entry.get("title", "Untitled"),
                    "author": entry.get("uploader") or entry.get("channel") or "YouTube",
                    "thumbnail": f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg",
                    "duration": duration,
                    "duration_str": format_duration(duration),
                    "views": "",
                })
        except Exception as e:
            print(f"[yt-dlp search fallback error] {e}")

    if items:
        if len(_search_cache) >= CACHE_MAX_SIZE:
            # Evict oldest entry
            oldest_key = min(_search_cache, key=lambda k: _search_cache[k][0])
            _search_cache.pop(oldest_key, None)
        _search_cache[cache_key] = (now, items)

    return items


def _sync_extract_audio_url(video_id: str) -> Optional[Tuple[str, str]]:
    """Extract direct audio stream URL using yt-dlp."""
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
    }
    proxy = (os.getenv("YT_PROXY") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        if not info or not info.get("url"):
            return None
        ext = (info.get("ext") or "m4a").lower()
        mime_map = {
            "m4a": "audio/mp4",
            "mp4": "audio/mp4",
            "webm": "audio/webm",
            "opus": "audio/webm",
            "mp3": "audio/mpeg",
        }
        return info["url"], mime_map.get(ext, "audio/mp4")


async def get_audio_stream_url(video_id: str) -> Optional[Tuple[str, str]]:
    """Get cached direct audio stream URL and MIME type for Radio/Fallback playback."""
    clean_id = extract_video_id(video_id)
    if not clean_id:
        return None

    now = time.time()
    cached = _audio_url_cache.get(clean_id)
    if cached and (now - cached[0]) < AUDIO_CACHE_TTL_SEC:
        return cached[1], cached[2]

    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, _sync_extract_audio_url, clean_id)
        if res:
            stream_url, content_type = res
            _audio_url_cache[clean_id] = (now, stream_url, content_type)
            return stream_url, content_type
    except Exception as e:
        print(f"[audio stream extract error] {e}")
    return None

