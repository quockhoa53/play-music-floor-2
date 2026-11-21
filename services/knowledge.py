"""Factual music knowledge lookup (Wikipedia) so the AI settles debates with sources, not guesses."""
import asyncio
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

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
                "note": "Chỉ là thông tin công khai đã được ghi nhận, có thể chưa đầy đủ hoặc chưa cập nhật.",
            }
            break
    wiki_cache.set(cache_key, result, ttl=86400 if result["found"] else 3600)
    return result


# ---------------------------------------------------------------------------------------------------
# Encyclopedia lookup: find the RIGHT page for an entity (artist, song, album, show, genre), read the
# whole article and return the intro + the paragraphs most relevant to the question ('focus').
# Intro-only extracts were too thin (no discography, awards, winners) and the model filled gaps by guessing.
# ---------------------------------------------------------------------------------------------------
def _fold(text: str) -> str:
    s = unicodedata.normalize("NFD", (text or "").lower()).replace("đ", "d")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def _tokens(text: str) -> List[str]:
    return [w for w in _fold(text).split() if len(w) >= 2 or w.isdigit()]


# Words that describe WHAT is asked, not WHO/WHAT it is about: never used to pick the page
_GENERIC_WORDS = set(
    "ca si sy nhac rapper rap ban nhom album bai hat ca khuc mv the loai dong cua va voi la ai gi nao co khong "
    "thong tin ve nam sinh bao nhieu tuoi dau tay moi nhat quan he ten that que o vo dich giai thuong sang tac "
    "singer musician band song genre who what when".split()
)
# Placeholder-only queries ('bài hát hiện tại', 'ca sĩ này'). Kept tiny on purpose: name syllables such as
# 'Bảo', 'Nam', 'Linh' must never be treated as vague.
_VAGUE_WORDS = set(
    "ca si sy nhac rapper ban nhom bai hat khuc mv album hien tai dang phat mo nghe nay do kia tren phong "
    "current playing this song singer artist".split()
)
_STOP_FOCUS = set("la ai gi nao co khong cua va voi ve the nhung cac mot nay do kia thi ma bao nhieu".split())
_DISAMBIG_RE = re.compile(r"(có thể (là|đề cập|chỉ)|may refer to|can refer to|trang định hướng)", re.IGNORECASE)
_SUFFIXES = ("ca sĩ", "rapper", "nhạc sĩ", "ban nhạc", "nhóm nhạc", "singer", "rapper", "musician", "band")


def _title_score(title: str, entity: str) -> float:
    """How well a page title matches the asked entity (core name + qualifiers like 'mùa 1')."""
    t_tokens = _tokens(title)
    e_tokens = [w for w in _tokens(entity) if w not in _GENERIC_WORDS] or _tokens(entity)
    if not t_tokens or not e_tokens:
        return 0.0
    core_title = _fold(re.sub(r"\(.*?\)", " ", title)).split()
    hit = sum(1 for w in e_tokens if w in t_tokens)
    score = hit / len(e_tokens)
    # Exact core name match beats pages that merely contain the words ('Phương Ly' vs 'Ngô Phương Ly')
    if set(core_title) == set(e_tokens):
        score += 0.5
    extra = [w for w in core_title if w not in e_tokens]
    score -= 0.08 * len(extra)
    # One-word / very short names ('NHA', 'Đen', 'Bray') match far too much ('Tây Ban Nha'):
    # accept only an exact title or one qualified as a musician / song
    if len(entity.split()) == 1 and len(e_tokens[0]) <= 5 and set(core_title) != set(e_tokens):
        score -= 0.6
    if re.search(r"\((ca sĩ|rapper|nhạc sĩ|ban nhạc|nhóm nhạc|singer|musician|band|album|bài hát|song)", title, re.IGNORECASE):
        score += 0.15
    return score


_MUSIC_LINE_RE = re.compile(
    r"(ca sĩ|nhạc sĩ|rapper|ban nhạc|nhóm nhạc|nghệ sĩ|album|bài hát|ca khúc|đĩa đơn|singer|musician|band|song|album)",
    re.IGNORECASE,
)


_MUSIC_INTRO_RE = re.compile(
    r"(ca sĩ|nhạc sĩ|rapper|ban nhạc|nhóm nhạc|nghệ sĩ|album|bài hát|ca khúc|đĩa đơn|âm nhạc|thể loại|"
    r"chương trình|nhạc|singer|musician|band|song|music|genre|record)",
    re.IGNORECASE,
)


async def _find_page(lang: str, entity: str) -> Optional[Tuple[str, str, List[str]]]:
    """Best-matching article (title, full text, same-name notes) for an entity.

    Disambiguation pages are skipped, but their music-related lines ('Ca sĩ Phương Linh (sinh 1984)') are kept:
    they tell the model the page it got may be a different person with the same name."""
    core = " ".join(w for w in entity.split() if _fold(w).strip() not in _GENERIC_WORDS) or entity
    queries = list(dict.fromkeys([entity, core] + [f"{core} ({s})" for s in _SUFFIXES[:2 if lang == "vi" else 0]]))
    candidates: Dict[str, float] = {}
    for q in queries[:3]:
        for rank, title in enumerate(await _search_titles(lang, q, 5)):
            s = _title_score(title, entity) - 0.03 * rank
            candidates[title] = max(candidates.get(title, -9), s)
        if candidates and max(candidates.values()) >= 1.2:
            break
    same_name: List[str] = []
    non_music: Optional[Tuple[str, str]] = None
    for title, score in sorted(candidates.items(), key=lambda kv: -kv[1])[:4]:
        if score < 0.5:
            break
        text = await _full_extract(lang, title)
        if not text:
            continue
        if _DISAMBIG_RE.search(text[:400]):
            same_name += [ln.strip() for ln in text.splitlines()[1:] if _MUSIC_LINE_RE.search(ln) and len(ln.strip()) < 160]
            continue
        # 'Exit Sign' (the song) vs 'Exit sign' (the emergency sign): prefer an article that is about music
        if not _MUSIC_INTRO_RE.search(text[:700]):
            non_music = non_music or (title, text)
            continue
        return title, text, same_name[:6]
    if same_name:
        return "", "", same_name[:6]
    if non_music and _MUSIC_INTRO_RE.search(non_music[1]):
        return non_music[0], non_music[1], []
    return None


# The model often writes `focus` in English or with synonyms: expand to the words Vietnamese articles use
_FOCUS_SYNONYMS = {
    "family": "gia dinh chi em anh bo me cha vo chong con em gai chi gai anh trai",
    "gia dinh": "chi em anh bo me cha vo chong con em gai chi gai anh trai",
    "quan he": "chi em anh em gia dinh vo chong hen ho hop tac ket hop song ca",
    "relationship": "chi em anh em gia dinh vo chong hen ho hop tac",
    "birth": "sinh ngay sinh nam", "born": "sinh", "nam sinh": "sinh", "tuoi": "sinh",
    "album": "album dia nhac phong thu", "debut": "dau tay ra mat dau tien",
    "dau tay": "dau tien ra mat", "winner": "quan quan vo dich chien thang",
    "vo dich": "quan quan chien thang", "quan quan": "vo dich chien thang",
    "award": "giai thuong giai cong hien de cu", "giai": "giai thuong de cu",
    "genre": "the loai dong nhac phong cach", "the loai": "dong nhac phong cach",
    "songwriter": "sang tac nhac si", "sang tac": "nhac si viet", "what is it": "",
    "career": "su nghiep", "real name": "ten that ten khai sinh", "hometown": "que sinh ra",
}


def _expand_focus(focus: str) -> List[str]:
    f = " ".join(_fold(focus).split())
    extra = " ".join(v for k, v in _FOCUS_SYNONYMS.items() if k in f)
    return list(dict.fromkeys(w for w in _tokens(f"{focus} {extra}") if w not in _STOP_FOCUS))


def _relevant_excerpt(text: str, focus: str, max_chars: int) -> Tuple[str, List[str]]:
    """Intro + the paragraphs that best answer `focus`, kept in article order, with their section heading."""
    parts = SECTION_SPLIT_RE.split("\n" + text)
    intro = re.sub(r"\s+", " ", parts[0]).strip()
    paras: List[Tuple[str, str]] = []
    for i in range(1, len(parts) - 2, 3):
        heading = parts[i + 1].strip()
        for p in re.split(r"\n+", parts[i + 2]):
            p = re.sub(r"\s+", " ", p).strip()
            if len(p) >= 25:
                paras.append((heading, p))
    focus_tokens = _expand_focus(focus)
    intro_part = intro[: min(len(intro), 500 if focus_tokens else max_chars)]
    budget = max_chars - len(intro_part)
    picked: List[int] = []
    phrase = " ".join(w for w in _tokens(focus) if w not in _STOP_FOCUS)
    anchors: Dict[int, int] = {}  # paragraph index -> char position of the best match (to centre the excerpt)
    if focus_tokens and paras:
        scored = []
        for idx, (h, p) in enumerate(paras):
            hp, fh = _fold(p), _fold(h)
            # The body must answer the question; a matching section heading only breaks ties
            hits = [m.start() for w in focus_tokens for m in [re.search(rf"\b{re.escape(w)}", hp)] if m]
            s = float(len(hits))
            pos = hp.find(phrase) if len(phrase) >= 4 else -1
            if pos != -1:
                s += 4
                anchors[idx] = pos
            elif hits:
                anchors[idx] = min(hits)
            if s and any(w in fh for w in focus_tokens):
                s += 0.5
            if s:
                scored.append((s, idx))
        if not scored:
            # Nothing matched the focus: give the article's opening sections rather than the intro alone
            scored = [(0, idx) for idx in range(len(paras))]
        for _, idx in sorted(scored, key=lambda x: (-x[0], x[1])):
            if budget < 150:
                break
            picked.append(idx)
            budget -= min(len(paras[idx][1]), 650) + len(paras[idx][0]) + 4
    else:
        for idx in range(len(paras)):
            if budget < 150:
                break
            picked.append(idx)
            budget -= min(len(paras[idx][1]), 650) + len(paras[idx][0]) + 4
    sections, chunks = [], []
    for idx in sorted(picked):
        h, p = paras[idx]
        # Long paragraph: a 650-char window around the match ('album đầu tay' is often deep inside it)
        start = 0
        if len(p) > 650 and idx in anchors:
            start = max(0, min(anchors[idx] - 200, len(p) - 650))
            start = p.rfind(" ", 0, start) + 1 if start else 0
        snippet = p[start:start + 650]
        chunks.append(f"[{h}] {'…' if start else ''}{snippet}{'…' if start + 650 < len(p) else ''}")
        if h not in sections:
            sections.append(h)
    body = intro_part + ("…" if len(intro_part) < len(intro) else "")
    if chunks:
        body += "\n" + "\n".join(chunks)
    return body, sections


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZĐÀ-Ỹ\"“(])")


async def _mentions(lang: str, entity: str, focus: str, skip: set, limit: int = 3) -> List[Dict[str, str]]:
    """Sentences from OTHER articles that name the entity exactly ('... là em gái của ca sĩ Phương Linh').

    Covers artists without their own page and relations between artists (each page mentions the other)."""
    name_re = re.compile(rf"(?<!\w){re.escape(entity)}(?!\w)", re.IGNORECASE)
    queries = list(dict.fromkeys([
        f'"{entity}" {focus}'.strip(), f'"{entity}" ca sĩ' if lang == "vi" else f'"{entity}" singer', f'"{entity}"',
    ]))
    hits = await asyncio.gather(*(_search_titles(lang, q, 8) for q in queries), return_exceptions=True)
    # Pages found by several queries first, then by best rank ('Phương Ly' is only #6 for "Phương Linh")
    rank: Dict[str, Tuple[int, int]] = {}
    for res in hits:
        if not isinstance(res, list):
            continue
        for i, t in enumerate(res):
            if t in skip or name_re.fullmatch(t):
                continue
            n, best = rank.get(t, (0, 99))
            rank[t] = (n + 1, min(best, i))
    titles = sorted(rank, key=lambda t: (-rank[t][0], rank[t][1]))[:5]
    texts = await asyncio.gather(*(_full_extract(lang, t) for t in titles), return_exceptions=True)
    focus_tokens = _expand_focus(focus)
    found: List[Tuple[int, Dict[str, str]]] = []
    for title, text in zip(titles, texts):
        if not isinstance(text, str) or not text:
            continue
        no_headings = re.sub(r"\n=+.*?=+\n", " ", text)
        for sent in _SENTENCE_SPLIT_RE.split(re.sub(r"\s+", " ", no_headings)):
            if not (25 <= len(sent) <= 400) or not _MUSIC_LINE_RE.search(sent):
                continue
            if any(_is_standalone_name(sent, m) for m in name_re.finditer(sent)):
                fs = _fold(sent)
                score = sum(1 for w in focus_tokens if re.search(rf"{re.escape(w)}", fs))
                found.append((score, {"page": title, "sentence": sent.strip()}))
                break  # one sentence per page keeps it short; the best one is usually the first mention
    found.sort(key=lambda x: -x[0])
    return [f for _, f in found[:limit]]


def _is_standalone_name(sent: str, m: "re.Match") -> bool:
    """'ca sĩ Phương Linh' yes; 'Nguyễn Hoàng Phương Linh' no (part of a longer, different name)."""
    before = sent[: m.start()].split()
    after = sent[m.end():].split()
    if before and before[-1][:1].isupper() and not before[-1].endswith((".", ":", ",", "“", '"')):
        return False
    if after and after[0][:1].isupper() and after[0][:1].isalpha():
        return False
    return True


def _first_sentence(text: str) -> str:
    intro = re.split(r"\n=+", text.strip(), maxsplit=1)[0]
    intro = re.sub(r"\s+", " ", re.sub(r"\.(?=[A-ZĐÀ-Ỹ])", ". ", intro))
    first = _SENTENCE_SPLIT_RE.split(intro, maxsplit=1)[0]
    return first[:220]


_PROPER_SEQ_RE = re.compile(
    r"[A-ZĐÂĂÊÔƠƯÁÀẢÃẠÉÈẺẼẸÍÌỈĨỊÓÒỎÕỌÚÙỦŨỤÝỲỶỸỴ][\w'.-]*(?:\s+[A-ZĐÂĂÊÔƠƯÁÀẢÃẠÉÈẺẼẸÍÌỈĨỊÓÒỎÕỌÚÙỦŨỤÝỲỶỸỴ0-9][\w'.-]*)*"
)
_ROLE_PREFIX_RE = re.compile(
    r"^(?:(?:ca sĩ|nhạc sĩ|rapper|ban nhạc|nhóm nhạc|album|bài hát|ca khúc|bài|nghệ sĩ|thể loại)\s+)+", re.IGNORECASE
)
_AUTHOR_NOISE_RE =re.compile(r"\b(official|music|channel|entertainment|vevo|topic|records?|studio)\b", re.IGNORECASE)


def extract_entities(message: str, ignore_names: Optional[List[str]] = None) -> List[str]:
    """Capitalised name sequences in a question ('Phương Ly', 'Rap Việt', 'Sơn Tùng M-TP'), minus member names."""
    text = message or ""
    for n in sorted(ignore_names or [], key=len, reverse=True):
        if n:
            text = re.sub(re.escape(n), " ", text, flags=re.IGNORECASE)
    entities: List[str] = []
    for m in _PROPER_SEQ_RE.finditer(text):
        # 'Ca sĩ Phương Ly' -> 'Phương Ly'; 'Rap Việt' stays whole (only leading role words are dropped)
        name = _ROLE_PREFIX_RE.sub("", m.group(0).strip(" .-")).strip()
        if not name or _fold(name).strip() in _GENERIC_WORDS or len(name) < 3:
            continue
        entities.append(name)
    return list(dict.fromkeys(entities))


def same_entity(a: str, b: str) -> bool:
    """'Phương Ly' vs 'Phương Ly (ca sĩ)' / 'phuong ly' -> True; 'Phương Linh' vs 'Phương Ly' -> False."""
    fa = " ".join(_fold(re.sub(r"\(.*?\)", " ", a)).split())
    fb = " ".join(_fold(re.sub(r"\(.*?\)", " ", b)).split())
    return bool(fa and fb) and (fa == fb or f" {fa} " in f" {fb} " or f" {fb} " in f" {fa} ")


def fold_text(text: str) -> str:
    return " ".join(_fold(text).split())


async def prefetch_knowledge(
    message: str, current_author: str = "", ignore_names: Optional[List[str]] = None, max_chars: int = 3200
) -> str:
    """For the fallback (no tool agent) path: guess the entities in a knowledge question and look them up in code.

    Entities = capitalised name sequences in the message ('Phương Ly', 'Rap Việt'), else the artist now playing
    ('ca sĩ bài này'). Returns compact JSON text for the prompt, or '' when nothing useful was found."""
    entities = extract_entities(message, ignore_names)
    if not entities and current_author:
        author = re.split(r"\s+(?:x|ft\.?|feat\.?|&|,|và)\s+", _AUTHOR_NOISE_RE.sub(" ", current_author), flags=re.IGNORECASE)[0]
        if author.strip():
            entities.append(author.strip())
    entities = list(dict.fromkeys(entities))[:2]
    if not entities:
        return ""
    focus_words = [w for w in _tokens(message) if w not in _STOP_FOCUS and not any(w in _tokens(e) for e in entities)]
    focus = " ".join(focus_words[:8])
    focus_for = {e: (" ".join(x for x in entities if x != e) or focus) for e in entities}
    try:
        res = await asyncio.wait_for(
            asyncio.gather(*(lookup_music_facts(e, focus=focus_for[e], max_chars=1100) for e in entities)),
            timeout=9.0,
        )
    except Exception as exc:
        print(f"[wiki prefetch] {type(exc).__name__} {exc}")
        return ""
    useful = [r for r in res if r.get("results") or r.get("mentions") or r.get("same_name")]
    if not useful:
        return ""
    import json

    def _strip(o: Any) -> Any:  # no source URLs: the bot says 'Internet', never pastes wikipedia links
        if isinstance(o, dict):
            return {k: _strip(v) for k, v in o.items() if k not in ("url", "lang")}
        return [_strip(x) for x in o] if isinstance(o, list) else o

    out = json.dumps(_strip(useful), ensure_ascii=False, separators=(",", ":"))
    return out[:max_chars]


async def lookup_music_facts(
    query: str, lang: str = "vi", focus: str = "", max_chars: int = 1500, **_: Any
) -> Dict[str, Any]:
    """Encyclopedia lookup on Wikipedia (vi first, en fallback): the page of `query` (an entity) with the
    paragraphs relevant to `focus` (what is asked: 'album đầu tay', 'năm sinh', 'quán quân').

    Same-name safety: if the best page is probably a DIFFERENT person ('Phương Linh' -> 'Đàm Phương Linh' while a
    disambiguation page lists 'Ca sĩ Phương Linh (sinh 1984)'), its body is withheld so the model cannot attribute
    it; instead it gets the same-name list and sentences from other articles that mention the exact name."""
    q = re.sub(r"\s+", " ", (query or "").strip())[:120]
    focus = re.sub(r"\s+", " ", (focus or "").strip())[:120]
    if not q:
        return {"query": q, "results": []}
    # 'bài hát hiện tại', 'ca sĩ này', 'bài đang phát': not a name -> searching it returns a random page
    if not [w for w in _tokens(q) if w not in _VAGUE_WORDS]:
        return {
            "query": q, "results": [],
            "note": "query không phải tên riêng. Bài đang phát / ca sĩ xem trong <room_context>; muốn tra thì query đúng TÊN ca sĩ / bài.",
        }
    langs = [lang] + [x for x in ("vi", "en") if x != lang]
    cache_key = f"v8:{'-'.join(langs)}:{q.lower()}|{focus.lower()}"
    cached = wiki_cache.get(cache_key)
    if cached is not None:
        return cached

    results: List[Dict[str, Any]] = []
    same_name_notes: List[str] = []
    other_person: Optional[Dict[str, str]] = None
    mention_lang = lang
    for lg in langs:
        try:
            found = await _find_page(lg, q)
        except Exception as exc:
            print(f"[wiki lookup] {lg}: {type(exc).__name__} {exc}")
            found = None
        if not found:
            continue
        title, text, same_name = found
        if same_name:
            same_name_notes = same_name
            mention_lang = lg
        if not title:
            break  # only a disambiguation page: the person has no article in this language
        core_q = {w for w in _tokens(q) if w not in _GENERIC_WORDS}
        core_t = set(_fold(re.sub(r"\(.*?\)", " ", title)).split())
        if same_name and core_q and core_t != core_q:
            # No dates / details of the other person: the model tended to reuse them for the asked one
            about = re.sub(r"\s*\([^)]*\)", "", _first_sentence(text)).strip()
            other_person = {"title": title, "about": about[:160]}
            break
        body, sections = _relevant_excerpt(text, focus or q, max_chars)
        results.append({
            "title": title,
            "url": f"https://{lg}.wikipedia.org/wiki/{title.replace(' ', '_')}",
            "lang": lg,
            "sections": sections,
            "text": body,
        })
        break

    payload: Dict[str, Any] = {"query": q, "focus": focus, "results": results}
    if not results and (same_name_notes or other_person):
        try:
            skip = {other_person["title"]} if other_person else set()
            mentions = await _mentions(mention_lang, q, focus, skip)
        except Exception as exc:
            print(f"[wiki mentions] {type(exc).__name__} {exc}")
            mentions = []
        if mentions:
            payload["mentions"] = mentions
        payload["warning"] = (
            f"Nguồn CHƯA có bài riêng chắc chắn cho '{q}' (trùng tên). Chỉ dùng 'same_name' và 'mentions' "
            "(câu từ trang khác nhắc đúng tên này)."
            + (f" Trang '{other_person['title']}' là NGƯỜI KHÁC cùng tên: không lấy dữ kiện của họ." if other_person else "")
        )
        if other_person:
            payload["other_person_same_name"] = other_person
    if same_name_notes:
        payload["same_name"] = same_name_notes
    if not results and "mentions" not in payload and not same_name_notes:
        payload["note"] = "Không có bài khớp: nói thật là chưa tìm thấy thông tin trên Internet, KHÔNG đoán."
    # Warnings first: the model reads them before the article text
    lead = {k: payload.pop(k) for k in ("warning", "note", "same_name", "mentions", "other_person_same_name") if k in payload}
    payload = {**lead, **payload}
    useful = bool(results or payload.get("mentions"))
    wiki_cache.set(cache_key, payload, ttl=86400 if useful else 1800)
    return payload
