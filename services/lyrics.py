"""Synced Lyrics (LRC) service using LRCLIB (free, no API key) with YouTube title cleaning & TTL cache."""
import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple
import httpx

from services.cache import TTLCache

LRC_TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
NOISE_BRACKETS_RE = re.compile(
    r"[\(\[\{][^\)\]\}]*(?:official|music\s*video|mv|lyric|vietsub|audio|hd|4k|live|visualizer|prod\.|remix|cover|karaoke|ver\.|version)[^\)\]\}]*[\)\]\}]",
    re.IGNORECASE,
)
NOISE_WORDS_RE = re.compile(
    r"\b(?:official\s+music\s+video|official\s+video|official\s+mv|official\s+audio|music\s+video|lyric\s+video|lyrics?|vietsub|karaoke|full\s+hd|4k|mv)\b",
    re.IGNORECASE,
)
CHANNEL_NOISE_RE = re.compile(
    r"\b(?:official|vevo|topic|music|entertainment|records|channel|vietnam)\b",
    re.IGNORECASE,
)
FEAT_RE = re.compile(r"\b(?:ft\.?|feat\.?|featuring)\s+[^|\-\(\[\n]+", re.IGNORECASE)

_lyrics_client: Optional[httpx.AsyncClient] = None
lyrics_cache: TTLCache[Dict[str, Any]] = TTLCache("lyrics", max_size=400, default_ttl=86400)
_inflight_lyrics: Dict[str, asyncio.Task] = {}


def get_lyrics_cache_stats() -> Dict[str, Any]:
    return lyrics_cache.stats()


def _get_lyrics_client() -> httpx.AsyncClient:
    global _lyrics_client
    if _lyrics_client is None or _lyrics_client.is_closed:
        _lyrics_client = httpx.AsyncClient(
            headers={"User-Agent": "Floor2MusicRoom/2.1 (+https://play-music-floor-2.onrender.com)"},
            timeout=httpx.Timeout(6.0, connect=3.0),
            follow_redirects=True,
        )
    return _lyrics_client


def parse_lrc_lyrics(lrc_text: str) -> List[Dict[str, Any]]:
    """Parse LRC synced lyrics string into sorted [{time: float, text: str}] lines."""
    if not lrc_text or not isinstance(lrc_text, str):
        return []

    parsed: List[Dict[str, Any]] = []
    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matches = list(LRC_TIMESTAMP_RE.finditer(line))
        if not matches:
            continue
        lyric_text = LRC_TIMESTAMP_RE.sub("", line).strip()
        if not lyric_text:
            continue
        for m in matches:
            mins = int(m.group(1))
            secs = int(m.group(2))
            frac_raw = m.group(3) or "0"
            if len(frac_raw) == 1:
                frac = int(frac_raw) / 10.0
            elif len(frac_raw) == 2:
                frac = int(frac_raw) / 100.0
            else:
                frac = int(frac_raw[:3]) / 1000.0
            ts = round(mins * 60 + secs + frac, 2)
            parsed.append({"time": ts, "text": lyric_text})

    parsed.sort(key=lambda item: item["time"])
    return parsed


def clean_youtube_title_for_lyrics(title: str, author: str = "") -> Tuple[str, str]:
    """Extract clean (track_query, artist_query) from noisy YouTube MV titles."""
    t = (title or "").strip()
    a = (author or "").strip()

    # Clean channel name noise
    clean_author = CHANNEL_NOISE_RE.sub("", a)
    clean_author = re.sub(r"\s+", " ", clean_author).strip(" -|•")

    # Strip bracketed noise like (Official Music Video), [MV], etc.
    t = NOISE_BRACKETS_RE.sub(" ", t)
    t = NOISE_WORDS_RE.sub(" ", t)
    t = FEAT_RE.sub(" ", t)

    # Split pipe-separated YouTube titles (e.g. "NƠI NÀY CÓ ANH | SƠN TÙNG M-TP")
    if "|" in t:
        parts = [p.strip(" -|•") for p in t.split("|") if p.strip(" -|•")]
        if parts:
            # Drop parts that just repeat the artist name so track_query stays sharp
            non_artist = [p for p in parts if not clean_author or p.lower() != clean_author.lower()]
            if non_artist:
                t = non_artist[0]
                if not clean_author and len(parts) > 1:
                    clean_author = parts[-1]
            else:
                t = parts[0]
    elif " - " in t:
        left, right = [p.strip() for p in t.split(" - ", 1)]
        # Usually "Artist - Song" on YouTube (e.g. "Ed Sheeran - Shape of You")
        if clean_author and left.lower() in clean_author.lower():
            t = right
        elif clean_author and right.lower() in clean_author.lower():
            t = left
        else:
            if not clean_author:
                clean_author = left
            t = right

    clean_title = re.sub(r"[\"'`•★☆🎵🎶]+", " ", t)
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" -|•:")
    return clean_title, clean_author


def _score_lrclib_candidate(item: Dict[str, Any], target_duration: int) -> Tuple[int, float]:
    """Higher score is better: strongly prefer synced LRC + closest duration."""
    has_synced = 1 if (item.get("syncedLyrics") or "").strip() else 0
    has_plain = 1 if (item.get("plainLyrics") or "").strip() else 0
    if not has_synced and not has_plain:
        return (-1, -99999.0)

    score = 100 if has_synced else 10
    dur = item.get("duration")
    dur_penalty = 0.0
    if target_duration > 0 and isinstance(dur, (int, float)) and dur > 0:
        diff = abs(float(dur) - float(target_duration))
        dur_penalty = -diff
        if diff <= 12:
            score += 30
        elif diff <= 30:
            score += 15
    return (score, dur_penalty)


async def _fetch_lyrics_from_lrclib(title: str, author: str, duration: int) -> Dict[str, Any]:
    clean_title, clean_author = clean_youtube_title_for_lyrics(title, author)
    queries: List[str] = []
    if clean_title and clean_author:
        queries.append(f"{clean_title} {clean_author}")
    if clean_title and clean_title not in queries:
        queries.append(clean_title)
    if not queries and title:
        queries.append(title.strip())

    client = _get_lyrics_client()
    candidates: List[Dict[str, Any]] = []

    for q in queries:
        try:
            resp = await client.get("https://lrclib.net/api/search", params={"q": q})
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data:
                    candidates = data
                    if any((x.get("syncedLyrics") or "").strip() for x in candidates):
                        break
        except Exception as e:
            print(f"[lyrics fetch error] {e}")

    if not candidates:
        return {
            "found": False,
            "synced": False,
            "lines": [],
            "plain_lines": [],
            "source": None,
            "matched_title": clean_title or title,
            "matched_artist": clean_author or author,
        }

    best = max(candidates, key=lambda item: _score_lrclib_candidate(item, duration))
    synced_raw = (best.get("syncedLyrics") or "").strip()
    plain_raw = (best.get("plainLyrics") or "").strip()

    synced_lines = parse_lrc_lyrics(synced_raw) if synced_raw else []
    plain_lines = [ln.strip() for ln in plain_raw.splitlines() if ln.strip()] if plain_raw else []

    if synced_lines:
        return {
            "found": True,
            "synced": True,
            "lines": synced_lines,
            "plain_lines": plain_lines,
            "source": "LRCLIB",
            "matched_title": best.get("trackName") or clean_title or title,
            "matched_artist": best.get("artistName") or clean_author or author,
            "matched_duration": best.get("duration") or 0,
        }

    if plain_lines:
        return {
            "found": True,
            "synced": False,
            "lines": [],
            "plain_lines": plain_lines,
            "source": "LRCLIB",
            "matched_title": best.get("trackName") or clean_title or title,
            "matched_artist": best.get("artistName") or clean_author or author,
            "matched_duration": best.get("duration") or 0,
        }

    return {
        "found": False,
        "synced": False,
        "lines": [],
        "plain_lines": [],
        "source": None,
        "matched_title": clean_title or title,
        "matched_artist": clean_author or author,
    }


async def get_synced_lyrics(
    title: str,
    author: str = "",
    duration: int = 0,
    video_id: str = "",
) -> Dict[str, Any]:
    """Fetch synced LRC or plain lyrics with LRU+TTL caching and in-flight deduplication."""
    clean_title = (title or "").strip()
    if not clean_title:
        return {"found": False, "synced": False, "lines": [], "plain_lines": []}

    cache_key = (video_id or "").strip() or f"{clean_title.lower()}::{(author or '').strip().lower()}"
    cached = lyrics_cache.get(cache_key)
    if cached is not None:
        return cached

    existing = _inflight_lyrics.get(cache_key)
    if existing and not existing.done():
        try:
            return await existing
        except Exception:
            pass

    task = asyncio.create_task(_fetch_lyrics_from_lrclib(clean_title, author, int(duration or 0)))
    _inflight_lyrics[cache_key] = task
    try:
        result = await task
    finally:
        if _inflight_lyrics.get(cache_key) is task:
            _inflight_lyrics.pop(cache_key, None)

    # Cache found lyrics for 24h; cache misses briefly (10m) to avoid repeated lookups
    lyrics_cache.set(cache_key, result, ttl=86400 if result.get("found") else 600)
    return result
