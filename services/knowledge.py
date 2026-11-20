"""Factual music knowledge lookup (Wikipedia) so the AI settles debates with sources, not guesses."""
import re
from typing import Any, Dict, List, Optional

import httpx

from services.cache import TTLCache

_wiki_client: Optional[httpx.AsyncClient] = None
wiki_cache: TTLCache[Dict[str, Any]] = TTLCache("wiki_lookup", max_size=300, default_ttl=86400)

WIKI_HEADERS = {
    # Wikimedia requires a descriptive User-Agent with a contact URL; use the app's public site (same as lyrics client)
    "User-Agent": "Floor2MusicRoom/2.2 (+https://play-music-floor-2.onrender.com) httpx",
    "Accept": "application/json",
}


def _get_wiki_client() -> httpx.AsyncClient:
    global _wiki_client
    if _wiki_client is None or _wiki_client.is_closed:
        _wiki_client = httpx.AsyncClient(headers=WIKI_HEADERS, timeout=httpx.Timeout(6.0, connect=3.0), follow_redirects=True)
    return _wiki_client


async def _search_titles(lang: str, query: str, limit: int) -> List[str]:
    r = await _get_wiki_client().get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query, "srlimit": limit, "format": "json", "utf8": 1},
    )
    if r.status_code != 200:
        return []
    return [h.get("title", "") for h in (r.json().get("query", {}).get("search") or []) if h.get("title")]


async def _extracts(lang: str, titles: List[str], max_chars: int) -> List[Dict[str, str]]:
    if not titles:
        return []
    r = await _get_wiki_client().get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "redirects": 1,
            "titles": "|".join(titles),
            "format": "json",
            "utf8": 1,
        },
    )
    if r.status_code != 200:
        return []
    pages = (r.json().get("query", {}).get("pages") or {}).values()
    by_title = {p.get("title", ""): p for p in pages}
    out: List[Dict[str, str]] = []
    for t in titles:
        p = by_title.get(t)
        if not p:
            # redirected titles: fall back to any page not yet used
            continue
        text = re.sub(r"\s+", " ", p.get("extract") or "").strip()
        if not text:
            continue
        out.append({
            "title": p.get("title", t),
            "summary": text[:max_chars] + ("…" if len(text) > max_chars else ""),
            "url": f"https://{lang}.wikipedia.org/wiki/{p.get('title', t).replace(' ', '_')}",
            "lang": lang,
        })
    return out


DRAMA_SECTION_RE = re.compile(
    r"(tranh cãi|tranh luận|scandal|bê bối|ồn ào|lùm xùm|thị phi|đời tư|đời sống cá nhân|vấn đề pháp lý|kiện|vướng|"
    r"chỉ trích|phát ngôn|đạo nhái|nghi vấn|mâu thuẫn|hợp đồng|controvers|legal|lawsuit|personal life|criticism|"
    r"plagiarism|feud|dispute|allegation|public image)",
    re.IGNORECASE,
)
SECTION_SPLIT_RE = re.compile(r"\n(={2,4})\s*(.+?)\s*\1\n")


async def _full_extract(lang: str, title: str) -> str:
    r = await _get_wiki_client().get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki",
            "redirects": 1, "titles": title, "format": "json", "utf8": 1,
        },
    )
    if r.status_code != 200:
        return ""
    pages = list((r.json().get("query", {}).get("pages") or {}).values())
    return (pages[0].get("extract") or "") if pages else ""


async def lookup_artist_drama(artist: str, lang: str = "vi", max_chars: int = 2200) -> Dict[str, Any]:
    """Publicly documented controversies / drama of an artist, from Wikipedia sections only (sourced, no rumours)."""
    q = re.sub(r"\s+", " ", (artist or "").strip())[:80]
    if not q:
        return {"artist": q, "found": False, "sections": []}
    cache_key = f"drama:{lang}:{q.lower()}"
    cached = wiki_cache.get(cache_key)
    if cached is not None:
        return cached

    result: Dict[str, Any] = {"artist": q, "found": False, "sections": []}
    for lg in [lang] + [x for x in ("vi", "en") if x != lang]:
        try:
            titles = await _search_titles(lg, q, 1)
            if not titles:
                continue
            text = await _full_extract(lg, titles[0])
        except Exception as exc:
            print(f"[wiki drama] {lg}: {type(exc).__name__} {exc}")
            continue
        if not text:
            continue
        parts = SECTION_SPLIT_RE.split("\n" + text)
        # parts = [intro, '==', heading, body, '==', heading, body, ...]
        sections = []
        for i in range(1, len(parts) - 2, 3):
            heading, body = parts[i + 1].strip(), re.sub(r"\s+", " ", parts[i + 2]).strip()
            if body and DRAMA_SECTION_RE.search(heading):
                sections.append({"heading": heading, "text": body})
        if sections:
            budget = max_chars
            trimmed = []
            for s in sections:
                if budget <= 200:
                    break
                chunk = s["text"][:budget]
                trimmed.append({"heading": s["heading"], "text": chunk + ("…" if len(s["text"]) > len(chunk) else "")})
                budget -= len(chunk)
            result = {
                "artist": q,
                "found": True,
                "page": titles[0],
                "url": f"https://{lg}.wikipedia.org/wiki/{titles[0].replace(' ', '_')}",
                "lang": lg,
                "sections": trimmed,
                "note": "Chỉ là thông tin đã được Wikipedia ghi nhận, có thể chưa đầy đủ hoặc chưa cập nhật.",
            }
            break
    wiki_cache.set(cache_key, result, ttl=86400 if result["found"] else 3600)
    return result


async def lookup_music_facts(query: str, lang: str = "vi", max_results: int = 2, max_chars: int = 1300) -> Dict[str, Any]:
    """Search Wikipedia (preferred language first, English as fallback) and return short intro extracts."""
    q = re.sub(r"\s+", " ", (query or "").strip())[:120]
    if not q:
        return {"query": q, "results": []}
    langs = [lang] + [x for x in ("vi", "en") if x != lang]
    cache_key = f"{'-'.join(langs)}:{q.lower()}:{max_results}"
    cached = wiki_cache.get(cache_key)
    if cached is not None:
        return cached

    results: List[Dict[str, str]] = []
    for lg in langs:
        try:
            titles = await _search_titles(lg, q, max_results)
            results = await _extracts(lg, titles, max_chars)
        except Exception as exc:
            print(f"[wiki lookup] {lg}: {type(exc).__name__} {exc}")
            results = []
        if results:
            break
    payload = {"query": q, "results": results}
    wiki_cache.set(cache_key, payload, ttl=86400 if results else 1800)
    return payload
