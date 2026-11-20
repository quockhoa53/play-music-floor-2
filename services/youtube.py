"""Ultra-fast YouTube metadata extraction and realtime Innertube search service."""
import asyncio
import os
import re
import time
import urllib.parse
from typing import Optional, List, Dict, Any, Tuple
import httpx
import yt_dlp

from services.cache import TTLCache

YDL_OPTS = {
    "extract_flat": True,
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
    "no_color": True,
}

MWEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Content-Type": "application/json",
    "Origin": "https://m.youtube.com",
    "Referer": "https://m.youtube.com/",
}

WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Content-Type": "application/json",
    "Origin": "https://www.youtube.com",
    "Referer": "https://www.youtube.com/",
}

# Persistent HTTP client for connection pooling (eliminates repeated TLS handshakes)
_http_client: Optional[httpx.AsyncClient] = None

# Unified LRU + TTL caches
yt_search_cache: TTLCache[List[Dict[str, Any]]] = TTLCache("yt_search", max_size=300, default_ttl=900)
yt_info_cache: TTLCache[Dict[str, Any]] = TTLCache("yt_info", max_size=500, default_ttl=3600)
yt_audio_cache: TTLCache[Tuple[str, str]] = TTLCache("yt_audio", max_size=200, default_ttl=1800)
yt_related_cache: TTLCache[List[Dict[str, Any]]] = TTLCache("yt_related", max_size=300, default_ttl=1800)

# In-flight request deduplication: cache_key -> asyncio.Task
_inflight_searches: Dict[str, asyncio.Task] = {}
_inflight_related: Dict[str, asyncio.Task] = {}


def get_youtube_cache_stats() -> Dict[str, Any]:
    return {
        "yt_search": yt_search_cache.stats(),
        "yt_info": yt_info_cache.stats(),
        "yt_audio": yt_audio_cache.stats(),
        "yt_related": yt_related_cache.stats(),
    }


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(4.0, connect=2.5),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15, keepalive_expiry=120),
        )
    return _http_client


async def warmup_youtube_connection():
    """Pre-warm TLS keep-alive connection to YouTube Innertube on server startup."""
    try:
        client = _get_http_client()
        await client.get("https://m.youtube.com/generate_204", timeout=3.0)
    except Exception:
        pass


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


def _extract_text(obj: Any) -> str:
    """Safely extract text from YouTube Innertube simpleText or runs list."""
    if not isinstance(obj, dict):
        return ""
    if "simpleText" in obj:
        return str(obj["simpleText"])
    runs = obj.get("runs")
    if isinstance(runs, list):
        return "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
    return ""


async def get_video_oembed(video_id: str) -> Optional[Dict[str, Any]]:
    """Fast metadata fetch using YouTube oEmbed API (~60-150ms, never blocked)."""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
        client = _get_http_client()
        resp = await client.get(oembed_url, timeout=3.5)
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
    """Run yt-dlp in synchronous mode (last-resort fallback)."""
    opts = dict(YDL_OPTS)
    proxy = (os.getenv("YT_PROXY") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        if is_search:
            return ydl.extract_info(f"ytsearch{limit}:{url_or_query}", download=False)
        return ydl.extract_info(url_or_query, download=False)


async def _innertube_mweb_search(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Ultra-lightweight Innertube MWEB JSON search (~120 KB payload, ~200-450ms, no HTML/regex)."""
    client = _get_http_client()
    resp = await client.post(
        "https://m.youtube.com/youtubei/v1/search?prettyPrint=false",
        headers=MWEB_HEADERS,
        json={
            "context": {
                "client": {
                    "clientName": "MWEB",
                    "clientVersion": "2.20240501.00.00",
                    "hl": "vi",
                    "gl": "VN",
                }
            },
            "query": query,
            "params": "EgIQAQ%3D%3D",  # Filter: Videos only
        },
        timeout=3.5,
    )
    if resp.status_code != 200:
        return []

    data = resp.json()
    sections = data.get("contents", {}).get("sectionListRenderer", {}).get("contents", [])
    items: List[Dict[str, Any]] = []
    seen_ids = set()

    for sec in sections:
        contents = sec.get("itemSectionRenderer", {}).get("contents", [])
        for entry in contents:
            vr = (
                entry.get("videoWithContextRenderer")
                or entry.get("compactVideoRenderer")
                or entry.get("videoRenderer")
            )
            if not vr:
                continue
            vid = vr.get("videoId")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)

            title = _extract_text(vr.get("headline")) or _extract_text(vr.get("title"))
            author = _extract_text(vr.get("shortBylineText")) or _extract_text(vr.get("ownerText"))
            dur_str = _extract_text(vr.get("lengthText"))
            if not dur_str:
                # Check if live badge is present
                badges = vr.get("badges") or []
                overlays = vr.get("thumbnailOverlays") or []
                is_live = any("LIVE" in str(b).upper() for b in badges) or any(
                    "LIVE" in str(o).upper() for o in overlays
                )
                if is_live:
                    dur_str = "LIVE"

            views = _extract_text(vr.get("shortViewCountText"))
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


async def _innertube_web_search(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Fallback Innertube WEB JSON search (~450 KB payload, pure JSON)."""
    client = _get_http_client()
    resp = await client.post(
        "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
        headers=WEB_HEADERS,
        json={
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240501.00.00",
                    "hl": "vi",
                    "gl": "VN",
                }
            },
            "query": query,
            "params": "EgIQAQ%3D%3D",
        },
        timeout=4.0,
    )
    if resp.status_code != 200:
        return []

    data = resp.json()
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

            title = _extract_text(vr.get("title"))
            author = _extract_text(vr.get("ownerText"))
            dur_str = _extract_text(vr.get("lengthText"))
            if not dur_str and any("LIVE" in str(b).upper() for b in vr.get("badges", [])):
                dur_str = "LIVE"
            views = _extract_text(vr.get("shortViewCountText"))
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
    """Get video metadata from URL or ID with 1-hour LRU+TTL caching."""
    video_id = extract_video_id(url_or_id)
    if not video_id:
        return None

    cached_info = yt_info_cache.get(video_id)
    if cached_info and (fast or cached_info.get("duration", 0) > 0):
        return cached_info

    oembed_info = await get_video_oembed(video_id)
    if fast and oembed_info:
        yt_info_cache.set(video_id, oembed_info)
        return oembed_info

    try:
        loop = asyncio.get_running_loop()
        url = f"https://www.youtube.com/watch?v={video_id}"
        data = await loop.run_in_executor(None, _sync_ytdlp_extract, url, False, 1)
        if data:
            duration = data.get("duration") or 0
            title = data.get("title") or (oembed_info.get("title") if oembed_info else f"Video {video_id}")
            author = data.get("uploader") or data.get("channel") or (oembed_info.get("author") if oembed_info else "YouTube")
            info = {
                "id": video_id,
                "title": title,
                "author": author,
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "duration": duration,
                "duration_str": format_duration(duration),
                "views": "",
            }
            yt_info_cache.set(video_id, info)
            return info
    except Exception as e:
        print(f"[yt-dlp extract error] {e}")

    if oembed_info:
        yt_info_cache.set(video_id, oembed_info)
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


async def _run_search_pipeline(query: str, limit: int) -> List[Dict[str, Any]]:
    """Execute tiered search: Innertube MWEB -> Innertube WEB -> yt-dlp."""
    items: List[Dict[str, Any]] = []

    # 1. Primary: Ultra-lightweight MWEB Innertube (~120 KB JSON)
    try:
        items = await _innertube_mweb_search(query, limit=limit)
    except Exception as e:
        print(f"[mweb innertube error] {e}")

    # 2. Fallback 1: WEB Innertube (~450 KB JSON)
    if not items:
        try:
            items = await _innertube_web_search(query, limit=limit)
        except Exception as e:
            print(f"[web innertube error] {e}")

    # 3. Fallback 2: yt-dlp (last resort)
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

    return items


async def search_youtube(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Realtime YouTube search with Innertube JSON, LRU+TTL cache, and in-flight deduplication."""
    query = query.strip()
    if not query:
        return []

    # Direct YouTube URL / Video ID -> cached metadata / oEmbed (~0-150ms)
    video_id = extract_video_id(query)
    if video_id:
        info = await get_video_info(video_id, fast=True)
        return [info] if info else []

    cache_key = f"{query.lower()}::{limit}"
    cached = yt_search_cache.get(cache_key)
    if cached is not None:
        return cached

    # Coalesce concurrent identical searches
    task = _inflight_searches.get(cache_key)
    if task is None or task.done():
        task = asyncio.create_task(_run_search_pipeline(query, limit))
        _inflight_searches[cache_key] = task

    try:
        items = await task
    finally:
        if _inflight_searches.get(cache_key) is task:
            _inflight_searches.pop(cache_key, None)

    if items:
        yt_search_cache.set(cache_key, items)
        # Warm per-video metadata cache for instant future URL pastes
        for it in items:
            vid = it.get("id")
            if vid:
                yt_info_cache.set(vid, it)

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

    cached = yt_audio_cache.get(clean_id)
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, _sync_extract_audio_url, clean_id)
        if res:
            yt_audio_cache.set(clean_id, res)
            return res
    except Exception as e:
        print(f"[audio stream extract error] {e}")
    return None


def _extract_text_obj(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    if obj.get("simpleText"):
        return str(obj["simpleText"]).strip()
    if isinstance(obj.get("runs"), list):
        return "".join(str(r.get("text", "")) for r in obj["runs"] if isinstance(r, dict)).strip()
    if obj.get("content"):
        return str(obj["content"]).strip()
    return ""


def _extract_related_from_next_json(data: Any, source_video_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Parse related tracks from YouTube Innertube /next response.

    Prefers RD Mix playlist items (musically matched individual songs), then falls back
    to standard Up-Next / related renderers for non-music videos.
    """
    mix_items: List[Dict[str, Any]] = []
    fallback_items: List[Dict[str, Any]] = []
    seen: set = {source_video_id}

    def _add_item(target_list: List[Dict[str, Any]], vid: str, title: str, author: str, dur_str: str):
        if not vid or len(vid) != 11 or vid in seen or not title:
            return
        seen.add(vid)
        dur_sec = parse_duration_str(dur_str) if dur_str else 0
        target_list.append({
            "id": vid,
            "title": title,
            "author": author or "YouTube",
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "duration": dur_sec,
            "duration_str": dur_str or format_duration(dur_sec),
        })

    def _walk(obj: Any):
        if isinstance(obj, dict):
            # 1. RD Music Mix entries (best quality for music rooms!)
            if "playlistPanelVideoRenderer" in obj and isinstance(obj["playlistPanelVideoRenderer"], dict):
                v = obj["playlistPanelVideoRenderer"]
                vid = v.get("videoId", "")
                title = _extract_text_obj(v.get("title"))
                author = _extract_text_obj(v.get("shortBylineText")) or _extract_text_obj(v.get("longBylineText"))
                dur_str = _extract_text_obj(v.get("lengthText"))
                _add_item(mix_items, vid, title, author, dur_str)

            # 2. Classic related video renderers (fallback)
            for key in ("compactVideoRenderer", "videoWithContextRenderer"):
                if key in obj and isinstance(obj[key], dict):
                    v = obj[key]
                    vid = v.get("videoId", "")
                    title = _extract_text_obj(v.get("title")) or _extract_text_obj(v.get("headline"))
                    author = (
                        _extract_text_obj(v.get("shortBylineText"))
                        or _extract_text_obj(v.get("longBylineText"))
                        or _extract_text_obj(v.get("ownerText"))
                    )
                    dur_str = _extract_text_obj(v.get("lengthText"))
                    _add_item(fallback_items, vid, title, author, dur_str)

            # 3. Newer lockupViewModel related cards (fallback)
            if "lockupViewModel" in obj and isinstance(obj["lockupViewModel"], dict):
                lm = obj["lockupViewModel"]
                vid = str(lm.get("contentId") or "")
                meta = lm.get("metadata", {}).get("lockupMetadataViewModel", {})
                title = _extract_text_obj(meta.get("title"))
                author = ""
                rows = meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                if isinstance(rows, list) and rows:
                    parts = rows[0].get("metadataParts", [])
                    if isinstance(parts, list) and parts:
                        author = _extract_text_obj(parts[0].get("text"))
                _add_item(fallback_items, vid, title, author, "")

            for val in obj.values():
                _walk(val)
        elif isinstance(obj, list):
            for el in obj:
                _walk(el)

    _walk(data)
    combined = mix_items + fallback_items
    return combined[:limit]


async def _fetch_related_innertube(video_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    client = _get_http_client()
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20240726.00.00",
                "hl": "vi",
                "gl": "VN",
            }
        },
        "videoId": video_id,
        "playlistId": f"RD{video_id}",
    }
    try:
        resp = await client.post(
            "https://www.youtube.com/youtubei/v1/next?prettyPrint=false",
            json=payload,
            headers=WEB_HEADERS,
            timeout=6.0,
        )
        if resp.status_code == 200:
            return _extract_related_from_next_json(resp.json(), video_id, limit=limit)
    except Exception as e:
        print(f"[youtube related fetch error] {e}")
    return []


async def get_related_videos(video_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch musically-related YouTube tracks for a video ID (LRU+TTL cached & deduplicated)."""
    clean_id = extract_video_id(video_id)
    if not clean_id:
        return []

    cache_key = f"{clean_id}:{limit}"
    cached = yt_related_cache.get(cache_key)
    if cached is not None:
        return cached

    existing = _inflight_related.get(cache_key)
    if existing and not existing.done():
        try:
            return await existing
        except Exception:
            pass

    task = asyncio.create_task(_fetch_related_innertube(clean_id, limit=limit))
    _inflight_related[cache_key] = task
    try:
        items = await task
    finally:
        if _inflight_related.get(cache_key) is task:
            _inflight_related.pop(cache_key, None)

    if items:
        yt_related_cache.set(cache_key, items)
        for it in items:
            vid = it.get("id")
            if vid:
                yt_info_cache.set(vid, it)
    return items
