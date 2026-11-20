"""Synced Lyrics (LRC) service using LRCLIB + YouTube Caption Auto-Sync Alignment.

Solves YouTube MV intro/outro drift by running LRCLIB lookup and YouTube ANDROID
TimedText captions fetch concurrently, then word-level RANSAC-aligning LRCLIB
timestamps onto the exact YouTube video audio (or using official manual YouTube
captions directly when LRCLIB has no synced version).
"""
import asyncio
import html
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
import httpx

from services.cache import TTLCache

LRC_TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
LRC_OFFSET_RE = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)
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
SOUND_EFFECT_RE = re.compile(
    r"^[\(\[\{]?\s*(?:âm nhạc|nhạc|vỗ tay|tiếng vỗ tay|cười|music|applause|laughter|cheering|ringing(?:\s+sound)?|instrumental|intro|outro|[♪♫♬\s]+)\s*[\)\]\}]?$",
    re.IGNORECASE,
)
VIET_DIACRITIC_RE = re.compile(
    r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]",
    re.IGNORECASE,
)
VERSION_MODIFIERS = (
    "remix", "live", "cover", "karaoke", "instrumental",
    "sped up", "slowed", "acoustic", "mashup", "nightcore", "piano", "lofi",
)
VALID_YT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

_lyrics_client: Optional[httpx.AsyncClient] = None
_yt_cap_client: Optional[httpx.AsyncClient] = None
lyrics_cache: TTLCache[Dict[str, Any]] = TTLCache("lyrics", max_size=400, default_ttl=86400)
_inflight_lyrics: Dict[str, asyncio.Task] = {}


def get_lyrics_cache_stats() -> Dict[str, Any]:
    return lyrics_cache.stats()


def _get_lyrics_client() -> httpx.AsyncClient:
    global _lyrics_client
    if _lyrics_client is None or _lyrics_client.is_closed:
        _lyrics_client = httpx.AsyncClient(
            headers={"User-Agent": "Floor2MusicRoom/2.2 (+https://play-music-floor-2.onrender.com)"},
            timeout=httpx.Timeout(6.0, connect=3.0),
            follow_redirects=True,
        )
    return _lyrics_client


def _get_yt_caption_client() -> httpx.AsyncClient:
    global _yt_cap_client
    if _yt_cap_client is None or _yt_cap_client.is_closed:
        _yt_cap_client = httpx.AsyncClient(
            headers={"User-Agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip"},
            timeout=httpx.Timeout(4.0, connect=2.5),
            follow_redirects=True,
        )
    return _yt_cap_client


def _norm_words(text: str) -> List[str]:
    """Normalize text (lowercase, strip accents/punctuation) into a list of tokens."""
    s = unicodedata.normalize("NFD", (text or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return [w for w in s.split() if w]


def parse_lrc_lyrics(lrc_text: str) -> List[Dict[str, Any]]:
    """Parse LRC synced lyrics string into sorted [{time: float, text: str}] lines.

    Honors standard LRC [offset:±ms] tags as well as multi-timestamp lines.
    """
    if not lrc_text or not isinstance(lrc_text, str):
        return []

    offset_sec = 0.0
    m_off = LRC_OFFSET_RE.search(lrc_text)
    if m_off:
        try:
            # Standard LRC spec: positive [offset:+500] shifts lyrics earlier by 0.5s
            offset_sec = -int(m_off.group(1)) / 1000.0
        except ValueError:
            offset_sec = 0.0

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
            ts = max(0.0, round(mins * 60 + secs + frac + offset_sec, 2))
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

    # Step 1: Split pipe-separated YouTube titles (e.g. "NƠI NÀY CÓ ANH | SƠN TÙNG M-TP")
    if "|" in t:
        parts = [p.strip(" -|•–—") for p in t.split("|") if p.strip(" -|•–—")]
        if parts:
            non_artist = [p for p in parts if not clean_author or p.lower() != clean_author.lower()]
            if non_artist:
                t = non_artist[0]
                if not clean_author and len(parts) > 1:
                    clean_author = parts[-1]
            else:
                t = parts[0]

    # Step 2: Also split dash-separated "Song - Artist" or "Artist - Song" (even after "|")
    dash_parts = [p.strip(" -|•–—") for p in re.split(r"\s+[-–—]\s+", t) if p.strip(" -|•–—")]
    if len(dash_parts) >= 2:
        left, right = dash_parts[0], dash_parts[1]
        ca_low = clean_author.lower()
        if clean_author and (left.lower() in ca_low or ca_low in left.lower()):
            t = right
        elif clean_author and (right.lower() in ca_low or ca_low in right.lower() or any(ca_low in dp.lower() for dp in dash_parts[1:])):
            t = left
        else:
            # Heuristic: if left has Vietnamese title words or is longer, or right is a short artist name
            if VIET_DIACRITIC_RE.search(left) and not VIET_DIACRITIC_RE.search(right):
                t = left
                if not clean_author or clean_author.lower() in ("st.319", "youtube"):
                    clean_author = right
            elif len(dash_parts) >= 3:
                # e.g. "Yêu Một Người Có Lẽ - Lou Hoàng - Miu Lê"
                t = left
            else:
                if not clean_author:
                    clean_author = left
                t = right

    clean_title = re.sub(r"[\"'`•★☆🎵🎶]+", " ", t)
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" -|•–—:")
    return clean_title, clean_author


def _score_lrclib_candidate(
    item: Dict[str, Any],
    target_duration: int,
    clean_title: str = "",
    clean_author: str = "",
    raw_title: str = "",
) -> Tuple[int, float]:
    """Higher score is better: prefer synced LRC, matching title/artist/version & closest duration."""
    has_synced = 1 if (item.get("syncedLyrics") or "").strip() else 0
    has_plain = 1 if (item.get("plainLyrics") or "").strip() else 0
    if not has_synced and not has_plain:
        return (-1, -99999.0)

    score = 100 if has_synced else 10

    cand_title = (item.get("trackName") or "").strip()
    cand_artist = (item.get("artistName") or "").strip()
    cand_album = (item.get("albumName") or "").strip()
    cand_meta_low = f"{cand_title} {cand_album}".lower()
    raw_low = (raw_title or clean_title or "").lower()

    # Penalize unwanted remix/live/cover/instrumental versions unless requested in YT title
    for mod in VERSION_MODIFIERS:
        if mod in cand_meta_low and mod not in raw_low:
            score -= 60

    # Reward title token overlap
    want_t = set(_norm_words(clean_title))
    got_t = set(_norm_words(cand_title))
    if want_t and got_t:
        overlap = len(want_t & got_t) / max(1, len(want_t))
        if overlap >= 0.85:
            score += 30
        elif overlap >= 0.5:
            score += 15
        elif overlap < 0.25:
            score -= 40

    # Reward artist token overlap
    want_a = set(_norm_words(clean_author))
    got_a = set(_norm_words(cand_artist))
    if want_a and got_a:
        a_overlap = len(want_a & got_a) / max(1, min(len(want_a), len(got_a)))
        if a_overlap >= 0.5:
            score += 20

    dur = item.get("duration")
    dur_penalty = 0.0
    if target_duration > 0 and isinstance(dur, (int, float)) and dur > 0:
        diff = abs(float(dur) - float(target_duration))
        dur_penalty = -diff
        if diff <= 3:
            score += 35
        elif diff <= 12:
            score += 20
        elif diff <= 30:
            score += 10
    return (score, dur_penalty)


def _detect_preferred_lang(title: str, author: str) -> str:
    combined = f"{title} {author}"
    if VIET_DIACRITIC_RE.search(combined):
        return "vi"
    return "en"


def _clean_caption_text(raw: str) -> str:
    txt = html.unescape(raw or "").replace("\n", " ")
    txt = re.sub(r"[♪♫♬]+", "", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if not txt or SOUND_EFFECT_RE.match(txt):
        return ""
    return txt


async def _fetch_youtube_captions(video_id: str, prefer_lang: str = "vi") -> Optional[Dict[str, Any]]:
    """Fetch YouTube TimedText captions (manual or ASR) via ANDROID Innertube player.

    Returns {kind, lang, lines: [{time, text}], words: [(t_sec, norm_word)]} or None.
    """
    if not video_id or not VALID_YT_ID_RE.match(video_id):
        return None

    try:
        client = _get_yt_caption_client()
        resp = await client.post(
            "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
            json={
                "context": {
                    "client": {
                        "clientName": "ANDROID",
                        "clientVersion": "20.10.38",
                        "androidSdkVersion": 30,
                        "hl": "vi",
                        "gl": "VN",
                    }
                },
                "videoId": video_id,
            },
        )
        if resp.status_code != 200:
            return None
        pj = resp.json()
        # The real video length (needed to pick the right LRCLIB version) comes for free with this call
        try:
            video_duration = float((pj.get("videoDetails") or {}).get("lengthSeconds") or 0)
        except (TypeError, ValueError):
            video_duration = 0.0
        no_caps = {"kind": None, "lang": "", "lines": [], "words": [], "duration": video_duration}
        caps = (
            pj.get("captions", {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        )
        if not caps:
            return no_caps

        def _track_rank(c: Dict[str, Any]) -> Tuple[int, int]:
            lang = (c.get("languageCode") or "").lower()
            is_asr = c.get("kind") == "asr"
            lang_base = lang.split("-")[0]
            if lang_base == prefer_lang and not is_asr:
                return (0, 0)
            if lang_base == prefer_lang and is_asr:
                return (1, 0)
            if lang_base == "en" and not is_asr:
                return (2, 0)
            if lang_base == "en" and is_asr:
                return (3, 0)
            return (9, 1 if is_asr else 0)

        caps_sorted = sorted(caps, key=_track_rank)
        best_cap = caps_sorted[0]
        # Avoid picking an unrelated translated language (e.g. Arabic/Portuguese) for alignment
        if _track_rank(best_cap)[0] >= 9:
            return no_caps

        base_url = best_cap.get("baseUrl") or ""
        if not base_url:
            return no_caps
        json3_url = re.sub(r"&fmt=[^&]+", "", base_url) + "&fmt=json3"

        cap_resp = await client.get(json3_url)
        if cap_resp.status_code != 200 or not cap_resp.text.strip().startswith("{"):
            return no_caps
        cj = cap_resp.json()

        lines: List[Dict[str, Any]] = []
        words: List[Tuple[float, str]] = []
        for ev in cj.get("events", []):
            segs = ev.get("segs")
            if not segs:
                continue
            t_base = ev.get("tStartMs", 0)
            full_raw = "".join(s.get("utf8", "") for s in segs)
            cleaned = _clean_caption_text(full_raw)
            if cleaned:
                lines.append({"time": round(t_base / 1000.0, 2), "text": cleaned})
            for seg in segs:
                stxt = html.unescape(seg.get("utf8", ""))
                if not stxt or stxt.strip().startswith("["):
                    continue
                t_seg = (t_base + seg.get("tOffsetMs", 0)) / 1000.0
                for w in _norm_words(stxt):
                    words.append((t_seg, w))

        return {
            "kind": "asr" if best_cap.get("kind") == "asr" else "manual",
            "lang": (best_cap.get("languageCode") or "").lower(),
            "lines": lines,
            "words": words,
            "duration": video_duration,
        }
    except Exception:
        return None


def estimate_lrc_offset_from_captions(
    lrc_lines: List[Dict[str, Any]],
    yt_words: List[Tuple[float, str]],
    is_asr: bool = False,
) -> Optional[float]:
    """Compute the exact YouTube MV intro/sync shift (seconds) via word-level RANSAC consensus.

    Positive return value means YouTube audio plays later than LRC timestamps (e.g. MV intro),
    so each LRC timestamp should be shifted by `+delta`.
    """
    if not lrc_lines or not yt_words or len(yt_words) < 6:
        return None

    yt_only = [w for _, w in yt_words]
    n = len(yt_only)
    deltas: List[float] = []

    for item in lrc_lines[:55]:
        lw = _norm_words(item.get("text", ""))
        if len(lw) < 3:
            continue
        target = lw[:5]
        min_hits = min(4, len(target)) if len(target) >= 4 else len(target)
        line_deltas: List[float] = []
        for i in range(n - 2):
            if yt_only[i] != target[0]:
                continue
            window = yt_only[i : i + len(target) + 2]
            hits = sum(1 for w in target if w in window)
            if hits >= min_hits:
                d = yt_words[i][0] - float(item["time"])
                if -45.0 <= d <= 120.0:
                    line_deltas.append(d)
                    if len(line_deltas) >= 4:
                        break
        deltas.extend(line_deltas)

    if len(deltas) < 3:
        return None

    # RANSAC consensus: find densest cluster within +-1.2s
    best_cluster: List[float] = []
    for d in deltas:
        cl = [x for x in deltas if abs(x - d) <= 1.2]
        if len(cl) > len(best_cluster):
            best_cluster = cl

    if len(best_cluster) < 3:
        return None

    best_cluster.sort()
    mid = len(best_cluster) // 2
    median = (
        best_cluster[mid]
        if len(best_cluster) % 2 == 1
        else (best_cluster[mid - 1] + best_cluster[mid]) / 2.0
    )

    # YouTube ASR emits words slightly after vocal onset (~0.35s recognition lag)
    if is_asr:
        median -= 0.35

    return round(median, 2)


async def _search_lrclib(title: str, author: str) -> Tuple[List[Dict[str, Any]], str, str]:
    """Search LRCLIB (regex-cleaned title first, AI-normalized title as fallback). Returns all candidates."""
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
    seen_ids = set()

    def _add(items: Any) -> None:
        if isinstance(items, list):
            for x in items:
                key = x.get("id") or (x.get("trackName"), x.get("artistName"), x.get("duration"))
                if key not in seen_ids:
                    seen_ids.add(key)
                    candidates.append(x)

    for q in queries:
        try:
            resp = await client.get("https://lrclib.net/api/search", params={"q": q})
            if resp.status_code == 200:
                _add(resp.json())
                # Keep collecting across queries: more versions = better chance of an exact-duration match
                if sum(1 for x in candidates if (x.get("syncedLyrics") or "").strip()) >= 3:
                    break
        except Exception as e:
            print(f"[lyrics fetch error] {e}")

    # AI Smart Title Normalization Fallback: when regex cleaning finds no synced lyrics
    if not any((x.get("syncedLyrics") or "").strip() for x in candidates):
        try:
            from services.ai import normalize_track_for_lyrics

            ai_norm = await normalize_track_for_lyrics(title, author)
            if ai_norm:
                ai_t, ai_a = ai_norm
                ai_queries = []
                if ai_t and ai_a:
                    ai_queries.append(f"{ai_t} {ai_a}")
                if ai_t and ai_t not in ai_queries:
                    ai_queries.append(ai_t)
                for aq in ai_queries:
                    if aq in queries:
                        continue
                    resp = await client.get("https://lrclib.net/api/search", params={"q": aq})
                    if resp.status_code == 200:
                        before = len(candidates)
                        _add(resp.json())
                        if len(candidates) > before:
                            clean_title = ai_t or clean_title
                            clean_author = ai_a or clean_author
                        if any((x.get("syncedLyrics") or "").strip() for x in candidates):
                            break
        except Exception as e:
            print(f"[lyrics ai norm fallback error] {e}")

    return candidates, clean_title, clean_author


def _build_lrclib_result(
    candidates: List[Dict[str, Any]],
    duration: float,
    clean_title: str,
    clean_author: str,
    raw_title: str,
) -> Dict[str, Any]:
    """Pick the best LRCLIB candidate for the REAL video duration and parse it."""
    empty = {
        "found": False,
        "synced": False,
        "lines": [],
        "plain_lines": [],
        "source": None,
        "matched_title": clean_title or raw_title,
        "matched_artist": clean_author or "",
        "matched_duration": 0,
        "auto_offset": 0.0,
    }
    if not candidates:
        return empty

    best = max(
        candidates,
        key=lambda item: _score_lrclib_candidate(item, int(duration or 0), clean_title, clean_author, raw_title),
    )
    synced_raw = (best.get("syncedLyrics") or "").strip()
    plain_raw = (best.get("plainLyrics") or "").strip()
    synced_lines = parse_lrc_lyrics(synced_raw) if synced_raw else []
    plain_lines = [ln.strip() for ln in plain_raw.splitlines() if ln.strip()] if plain_raw else []
    if not synced_lines and not plain_lines:
        return empty
    if not plain_lines and synced_lines:
        plain_lines = [ln["text"] for ln in synced_lines]
    return {
        "found": True,
        "synced": bool(synced_lines),
        "lines": synced_lines,
        "plain_lines": plain_lines,
        "source": "LRCLIB",
        "matched_title": best.get("trackName") or clean_title or raw_title,
        "matched_artist": best.get("artistName") or clean_author or "",
        "matched_duration": float(best.get("duration") or 0),
        "auto_offset": 0.0,
    }


async def _fetch_lyrics_from_lrclib(title: str, author: str, duration: int) -> Dict[str, Any]:
    """Backwards-compatible wrapper: search + pick by the given duration."""
    candidates, clean_title, clean_author = await _search_lrclib(title, author)
    return _build_lrclib_result(candidates, float(duration or 0), clean_title, clean_author, title)


def _text_lang(lines: List[str]) -> str:
    """'vi' when the lyric text is mostly Vietnamese (diacritics), else 'en'-like (latin)."""
    sample = " ".join(lines[:40])
    words = re.findall(r"\w+", sample)
    if not words:
        return ""
    viet = sum(1 for w in words if VIET_DIACRITIC_RE.search(w))
    return "vi" if viet / len(words) >= 0.12 else "en"


def align_lrc_to_captions(
    lrc_lines: List[Dict[str, Any]],
    yt_words: List[Tuple[float, str]],
    is_asr: bool = False,
) -> Optional[Dict[str, Any]]:
    """Piecewise (per-line) alignment of LRC lyrics onto the YouTube video's caption timeline.

    A single global offset fails when the MV differs structurally from the studio track (story intro,
    extended bridge, cut chorus...). Here every LRC line is matched to candidate positions in the caption
    word stream, a dynamic program picks the best strictly-increasing chain of matches (favouring a smooth
    offset), and unmatched lines inherit the offset of their nearest anchor.

    Returns {"lines", "anchors", "coverage", "offset"} or None when there is not enough evidence.
    """
    if not lrc_lines or not yt_words or len(yt_words) < 6:
        return None

    yt_only = [w for _, w in yt_words]
    positions: Dict[str, List[int]] = {}
    for i, w in enumerate(yt_only):
        positions.setdefault(w, []).append(i)

    # 1. Candidate caption times for each LRC line
    line_cands: List[List[float]] = []
    eligible = 0
    for item in lrc_lines:
        lw = _norm_words(item.get("text", ""))
        if len(lw) < 3:
            line_cands.append([])
            continue
        eligible += 1
        target = lw[:6]
        need = len(target) if len(target) <= 3 else max(3, int(round(len(target) * 0.65)))
        starts = set()
        for k in range(min(2, len(target))):
            for p in positions.get(target[k], []):
                starts.add(max(0, p - k))
        cands: List[float] = []
        for s in sorted(starts):
            window = yt_only[s : s + len(target) + 3]
            pool = list(window)
            hits = 0
            for w in target:
                if w in pool:
                    pool.remove(w)
                    hits += 1
            if hits >= need:
                t = yt_words[s][0]
                if not cands or t - cands[-1] > 1.0:
                    cands.append(t)
        line_cands.append(cands[:8])

    # 2. DP over (line, candidate): longest strictly increasing chain, penalizing offset jumps
    states: List[Tuple[int, float, float]] = []  # (line_idx, cap_time, delta)
    for li, cands in enumerate(line_cands):
        lt = float(lrc_lines[li]["time"])
        for ct in cands:
            d = ct - lt
            if -60.0 <= d <= 180.0:
                states.append((li, ct, d))
    if len(states) < 3:
        return None

    n = len(states)
    score = [1.0] * n
    prev = [-1] * n
    for b in range(n):
        lb, tb, db = states[b]
        for a in range(b):
            la, ta, da = states[a]
            if la >= lb or tb <= ta + 0.2:
                continue
            jump = abs(db - da)
            gain = 1.0 if jump <= 1.5 else (0.35 if jump <= 30 else -1.0)
            if score[a] + gain > score[b]:
                score[b] = score[a] + gain
                prev[b] = a
    end = max(range(n), key=lambda i: score[i])
    chain: List[Tuple[int, float, float]] = []
    while end != -1:
        chain.append(states[end])
        end = prev[end]
    chain.reverse()

    # 3. Drop isolated anchors whose offset disagrees with both neighbours (accidental word matches)
    anchors: List[Tuple[int, float, float]] = []
    for i, st in enumerate(chain):
        neigh = [chain[j][2] for j in (i - 1, i + 1) if 0 <= j < len(chain)]
        if len(chain) <= 2 or any(abs(st[2] - d) <= 1.5 for d in neigh):
            anchors.append(st)
    if len(anchors) < 3:
        return None

    asr_lag = 0.35 if is_asr else 0.0
    anchor_by_line = {li: (ct - asr_lag, d - asr_lag) for li, ct, d in anchors}
    anchor_lines = sorted(anchor_by_line)

    out: List[Dict[str, Any]] = []
    for li, item in enumerate(lrc_lines):
        lt = float(item["time"])
        if li in anchor_by_line:
            t = anchor_by_line[li][0]
        else:
            before = [a for a in anchor_lines if a < li]
            after = [a for a in anchor_lines if a > li]
            if before and after:
                a, b = before[-1], after[0]
                da, db = anchor_by_line[a][1], anchor_by_line[b][1]
                la, lb = float(lrc_lines[a]["time"]), float(lrc_lines[b]["time"])
                if abs(da - db) <= 1.5 and lb > la:
                    d = da + (db - da) * (lt - la) / (lb - la)  # smooth drift: interpolate
                else:
                    d = da if (lt - la) <= (lb - lt) else db   # structural jump: nearest anchor wins
            elif before:
                d = anchor_by_line[before[-1]][1]
            else:
                d = anchor_by_line[after[0]][1]
            t = lt + d
        out.append({"time": max(0.0, round(t, 2)), "text": item["text"]})

    # Keep timestamps monotonic for the player highlight
    for i in range(1, len(out)):
        if out[i]["time"] < out[i - 1]["time"]:
            out[i]["time"] = out[i - 1]["time"]

    deltas = sorted(d for _, _, d in anchors)
    return {
        "lines": out,
        "anchors": len(anchors),
        "coverage": round(len(anchors) / max(1, eligible), 2),
        "offset": round(deltas[len(deltas) // 2] - asr_lag, 2),
    }


async def _fetch_synced_lyrics_combined(
    title: str,
    author: str,
    duration: int,
    video_id: str,
) -> Dict[str, Any]:
    """Find lyrics and sync them to THIS YouTube video.

    Priority (best sync first):
      1. LRCLIB lyrics piecewise-aligned onto the video's same-language captions (manual or ASR).
      2. The video's own manual same-language captions used directly as lyrics.
      3. LRCLIB version whose duration matches the real video length (studio timing == video timing).
      4. Best LRCLIB version, flagged low-confidence so the UI offers manual / room calibration.
    """
    prefer_lang = _detect_preferred_lang(title, author)
    cap_task = (
        _fetch_youtube_captions(video_id, prefer_lang=prefer_lang) if video_id else asyncio.sleep(0, result=None)
    )
    (candidates, clean_title, clean_author), yt_caps = await asyncio.gather(_search_lrclib(title, author), cap_task)

    video_duration = float((yt_caps or {}).get("duration") or 0) or float(duration or 0)
    lrc_res = _build_lrclib_result(candidates, video_duration, clean_title, clean_author, title)
    lrc_res["video_duration"] = video_duration
    lrc_res["sync_method"] = "none"
    lrc_res["sync_confidence"] = "none"

    lyric_lang = _text_lang([ln["text"] for ln in lrc_res.get("lines") or []]) or prefer_lang
    cap_lang = ((yt_caps or {}).get("lang") or "").split("-")[0]
    caps_usable = bool(yt_caps and yt_caps.get("words") and cap_lang == lyric_lang)

    # 1. Piecewise alignment onto same-language captions
    if lrc_res.get("synced") and caps_usable:
        aligned = align_lrc_to_captions(lrc_res["lines"], yt_caps["words"], is_asr=(yt_caps.get("kind") == "asr"))
        if aligned and aligned["coverage"] >= 0.3:
            lrc_res["lines"] = aligned["lines"]
            lrc_res["auto_offset"] = aligned["offset"]
            lrc_res["source"] = "LRCLIB + Auto-Sync"
            lrc_res["sync_method"] = "aligned"
            lrc_res["sync_confidence"] = "high" if aligned["coverage"] >= 0.5 else "medium"
            lrc_res["align_coverage"] = aligned["coverage"]
            return lrc_res

    # 2. Manual same-language captions ARE the video's own synced lyrics
    if (
        yt_caps
        and yt_caps.get("kind") == "manual"
        and cap_lang == (lyric_lang if lrc_res.get("found") else prefer_lang)
        and len(yt_caps.get("lines") or []) >= 10
    ):
        return {
            "found": True,
            "synced": True,
            "lines": yt_caps["lines"],
            "plain_lines": [ln["text"] for ln in yt_caps["lines"]],
            "source": "YouTube",
            "matched_title": clean_title or title,
            "matched_artist": clean_author or author,
            "matched_duration": video_duration,
            "video_duration": video_duration,
            "auto_offset": 0.0,
            "sync_method": "youtube_captions",
            "sync_confidence": "high",
        }

    if not lrc_res.get("synced"):
        return lrc_res

    # 3/4. No usable captions: trust LRC timing only when the matched version has the video's length
    matched = float(lrc_res.get("matched_duration") or 0)
    diff = abs(matched - video_duration) if (matched and video_duration) else None
    lrc_res["duration_diff"] = round(diff, 1) if diff is not None else None
    if diff is not None and diff <= 3.0:
        lrc_res["sync_method"] = "duration_match"
        lrc_res["sync_confidence"] = "high"
    elif diff is not None and diff <= 8.0:
        lrc_res["sync_method"] = "duration_close"
        lrc_res["sync_confidence"] = "medium"
    else:
        lrc_res["sync_method"] = "unaligned"
        lrc_res["sync_confidence"] = "low"
    return lrc_res


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

    task = asyncio.create_task(
        _fetch_synced_lyrics_combined(clean_title, author, int(duration or 0), (video_id or "").strip())
    )
    _inflight_lyrics[cache_key] = task
    try:
        result = await task
    finally:
        if _inflight_lyrics.get(cache_key) is task:
            _inflight_lyrics.pop(cache_key, None)

    # Cache found lyrics for 24h; cache misses briefly (10m) to avoid repeated lookups
    lyrics_cache.set(cache_key, result, ttl=86400 if result.get("found") else 600)
    return result
