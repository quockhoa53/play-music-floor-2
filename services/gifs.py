"""Fast Giphy GIF & Animated Sticker search service (no API key required) with rich curated defaults, local Bé Floor 2 pack & TTL cache."""
import os
import re
import unicodedata
import urllib.parse
from typing import Any, Dict, List, Optional
import httpx

from services.cache import TTLCache
from services.security import sanitize_giphy_id

LOCAL_STICKERS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "images",
    "stickers",
)
LOCAL_STICKER_FILENAME_RE = re.compile(r"^[a-z0-9_]{1,64}\.gif$")

FLOOR2_STICKER_TITLES: Dict[str, str] = {
    "01_dance_music.gif": "Bé Floor 2 nhảy theo nhạc vui vẻ",
    "02_sleepy_zzz.gif": "Bé Floor 2 buồn ngủ Zzz",
    "03_thumbs_up_heart.gif": "Bé Floor 2 giơ ngón cái thả tim",
    "04_dj_youtube_queue.gif": "Bé Floor 2 lên nhạc DJ",
    "05_yeah_cheer.gif": "Bé Floor 2 reo hò Yeah!",
    "06_heart_eyes.gif": "Bé Floor 2 mắt hình trái tim mê mẩn",
    "07_coding_coffee.gif": "Bé Floor 2 vừa code vừa uống cà phê chill",
    "08_peeking_sign.gif": "Bé Floor 2 ló đầu giơ bảng đáng yêu",
    "09_thinking_question.gif": "Bé Floor 2 đang suy nghĩ thắc mắc",
    "10_superhero_go.gif": "Bé Floor 2 siêu anh hùng tiến lên",
    "11_sad_bai_buon_roi.gif": "Bé Floor 2 khóc nức nở: Bài buồn rồi...",
    "12_sad_sao_lai_nhac_buon.gif": "Bé Floor 2 nằm bẹp khóc: Sao lại là nhạc buồn...",
    "13_sad_huhu_cry.gif": "Bé Floor 2 khóc như thác nước: Huhu",
    "14_sad_that_la_buon.gif": "Bé Floor 2 ôm gối khóc: Thật là buồn...",
    "15_sad_gloom_hug.gif": "Bé Floor 2 ngồi ôm gối ủ rũ buồn bã",
    "16_sad_khong_chiu_duoc_nua.gif": "Bé Floor 2 nằm ăn vạ khóc: Không chịu được nữa!!!",
    "17_sad_bai_nay_la_sao_vay.gif": "Bé Floor 2 ôm điện thoại khóc: Bài này là sao vậy...",
    "18_sad_buon_qua.gif": "Bé Floor 2 mắt rưng rưng: Buồn quá...",
    "19_sad_khoc_roi_do.gif": "Bé Floor 2 trùm chăn xì mũi: Khóc rồi đó...",
    "20_sad_nhac_buon_thi_khoc_thoi.gif": "Bé Floor 2 nằm khóc dưới mây đen: Nhạc buồn thì khóc thôi!",
    "21_dj_lets_dj.gif": "Bé Floor 2 bàn DJ cực cháy: LET'S DJ!",
    "22_dj_mode_on.gif": "Bé Floor 2 đeo kính pixel cực ngầu: DJ Mode ON",
    "23_dj_drop_the_beat.gif": "Bé Floor 2 đánh nhạc: Drop the Beat!",
    "24_dj_quay_thoi.gif": "Bé Floor 2 nhảy tưng bừng: Quẩy thôi!",
    "25_dj_feel_the_beat.gif": "Bé Floor 2 phiêu theo nhịp bass: Feel the Beat",
    "26_dj_party_time.gif": "Bé Floor 2 tiệc quẩy disco: Party Time!",
    "27_dj_all_day.gif": "Bé Floor 2 giơ tay hô vang: DJ! All Day!",
    "28_dj_mix_it_up.gif": "Bé Floor 2 chà đĩa phối nhạc: Mix It Up!",
    "29_dj_music_forever.gif": "Bé Floor 2 giơ đĩa than: Music Forever",
    "30_dj_chill_dj.gif": "Bé Floor 2 nằm ghế lười cực chill: Chill DJ",
}

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


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    return "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")


def is_floor2_query(query: str) -> bool:
    norm = re.sub(r"\s+", " ", _strip_accents(query)).strip()
    return norm in {
        "be floor 2",
        "be floor2",
        "em be floor 2",
        "em be floor2",
        "floor 2",
        "floor2",
        "em be",
        "be-floor-2",
    } or "floor 2" in norm or "floor2" in norm


def get_local_sticker_caption(filename: str) -> str:
    """Return instant Vietnamese description for a local Bé Floor 2 sticker GIF."""
    if not filename:
        return ""
    return FLOOR2_STICKER_TITLES.get(
        filename,
        f"Sticker động Bé Floor 2 ({filename.rsplit('.', 1)[0].replace('_', ' ')})",
    )


def get_local_floor2_gifs() -> List[Dict[str, Any]]:
    """Return all local animated Bé Floor 2 GIFs from images/stickers/ in sorted order."""
    items: List[Dict[str, Any]] = []
    if not os.path.isdir(LOCAL_STICKERS_DIR):
        return items
    try:
        names = sorted(os.listdir(LOCAL_STICKERS_DIR))
    except OSError:
        return items
    for fname in names:
        if not LOCAL_STICKER_FILENAME_RE.match(fname):
            continue
        full = os.path.join(LOCAL_STICKERS_DIR, fname)
        if not os.path.isfile(full):
            continue
        url = f"/images/stickers/{fname}"
        items.append({
            "id": f"local:{fname}",
            "title": get_local_sticker_caption(fname),
            "preview": url,
            "url": url,
            "local": True,
        })
    return items


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


async def search_gifs(query: str, limit: int = 24) -> List[Dict[str, Any]]:
    """Search animated GIFs on Giphy or return local Bé Floor 2 GIF pack instantaneously."""
    clean_q = re.sub(r"\s+", " ", (query or "").strip())
    if not clean_q:
        return get_curated_gifs()

    if is_floor2_query(clean_q):
        return get_local_floor2_gifs()

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


async def search_stickers(query: str, limit: int = 24) -> List[Dict[str, Any]]:
    """Search transparent animated Stickers on Giphy or return local Bé Floor 2 pack instantaneously."""
    clean_q = re.sub(r"\s+", " ", (query or "").strip())
    if not clean_q:
        return get_curated_stickers()

    if is_floor2_query(clean_q):
        return get_local_floor2_gifs()

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
