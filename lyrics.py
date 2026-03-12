import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
from difflib import SequenceMatcher
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from config import SOUNDCLOUD_CLIENT_ID, SOUNDCLOUD_CLIENT_SECRET
from soundcloud_api import get_oauth_token, search_soundcloud

logger = logging.getLogger(__name__)

GENIUS_PROXY = os.getenv("GENIUS_PROXY", "").strip()
GENIUS_TOKEN = os.getenv("GENIUS_ACCESS_TOKEN", "").strip()
GENIUS_TIMEOUT = int(os.getenv("GENIUS_TIMEOUT", "20"))
LYRICS_CACHE_TTL = int(os.getenv("LYRICS_CACHE_TTL", "21600"))
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}

_LYRICS_CACHE: dict[str, dict] = {}
_PROXY_DISABLED_UNTIL = 0.0
_PROXY_DISABLE_SECONDS = int(os.getenv("GENIUS_PROXY_DISABLE_SECONDS", "300"))


def _cache_key(artist: str, title: str) -> str:
    return f"{_normalize_text(artist)}::{_normalize_text(title)}"


def _get_cached_lyrics(artist: str, title: str) -> Optional[str]:
    key = _cache_key(artist, title)
    entry = _LYRICS_CACHE.get(key)
    if not entry:
        return None
    if entry["expires_at"] < time.time():
        _LYRICS_CACHE.pop(key, None)
        return None
    return entry["lyrics"]


def _set_cached_lyrics(artist: str, title: str, lyrics: str) -> None:
    if not artist or not title or not lyrics:
        return
    _LYRICS_CACHE[_cache_key(artist, title)] = {
        "lyrics": lyrics,
        "expires_at": time.time() + LYRICS_CACHE_TTL,
    }


def _disable_proxy_temporarily(seconds: Optional[int] = None) -> None:
    global _PROXY_DISABLED_UNTIL
    if not GENIUS_PROXY:
        return
    cooldown = seconds or _PROXY_DISABLE_SECONDS
    _PROXY_DISABLED_UNTIL = time.time() + max(30, cooldown)


def _proxy_attempts() -> list[Optional[str]]:
    attempts: list[Optional[str]] = [None]
    if GENIUS_PROXY and time.time() >= _PROXY_DISABLED_UNTIL:
        attempts.append(GENIUS_PROXY)
    return attempts


async def _request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    proxy: Optional[str] = None,
) -> Optional[aiohttp.ClientResponse]:
    timeout = aiohttp.ClientTimeout(total=GENIUS_TIMEOUT)
    session_headers = dict(DEFAULT_HEADERS)
    if headers:
        session_headers.update(headers)

    session = aiohttp.ClientSession(timeout=timeout, headers=session_headers, trust_env=False)
    try:
        resp = await session.request(method, url, params=params, proxy=proxy)
        if resp.status != 200:
            logger.debug("%s %s returned %s via proxy=%s", method, url, resp.status, proxy or "direct")
            resp.release()
            await session.close()
            return None
        resp._managed_session = session  # type: ignore[attr-defined]
        return resp
    except Exception as e:
        await session.close()
        message = str(e).lower()
        if proxy and ("server disconnected" in message or "remoteprotocolerror" in message or "clientconnectorerror" in message or "cannot connect" in message or "timeout" in message):
            _disable_proxy_temporarily()
        raise


async def _get_json(url: str, *, headers: Optional[dict] = None, params: Optional[dict] = None, proxy: Optional[str] = None):
    resp = await _request(url, headers=headers, params=params, proxy=proxy)
    if not resp:
        return None
    session = getattr(resp, "_managed_session", None)
    try:
        return await resp.json(content_type=None)
    finally:
        resp.release()
        if session:
            await session.close()


async def _get_text(url: str, *, headers: Optional[dict] = None, params: Optional[dict] = None, proxy: Optional[str] = None) -> Optional[str]:
    resp = await _request(url, headers=headers, params=params, proxy=proxy)
    if not resp:
        return None
    session = getattr(resp, "_managed_session", None)
    try:
        return await resp.text()
    finally:
        resp.release()
        if session:
            await session.close()


def _normalize_text(value: str) -> str:
    value = (value or "").casefold().strip()
    value = value.replace("ё", "е")
    value = re.sub(r"feat\.?|ft\.?", " ", value)
    value = re.sub(r"[\[\](){}'\"`´’]", " ", value)
    value = re.sub(r"[^\w\s-]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = (value or "").strip()
        if not value:
            continue
        key = _normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _title_variants(title: str) -> list[str]:
    title = (title or "").strip()
    if not title:
        return []
    variants = [title]
    cleaned = re.sub(r"\s*[-–—]\s*(official|lyrics?|audio|video|prod\.?|remix|live|slowed.*|sped.*)$", "", title, flags=re.I).strip()
    cleaned = re.sub(r"\((feat\.?|ft\.?)[^)]+\)", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"\[(feat\.?|ft\.?)[^\]]+\]", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
    if cleaned:
        variants.append(cleaned)
    if " - " in cleaned:
        left, right = cleaned.split(" - ", 1)
        variants.extend([left.strip(), right.strip()])
    return _unique_keep_order(variants)


def _artist_variants(artist: str) -> list[str]:
    artist = (artist or "").strip()
    if not artist:
        return []
    variants = [artist]
    cleaned = re.sub(r"\s+", " ", artist).strip()
    if cleaned:
        variants.append(cleaned)
    return _unique_keep_order(variants)




def _sanitize_lookup_title(title: str) -> str:
    title = (title or "").strip()
    if not title:
        return ""
    title = re.sub(r"\s*[\[(](official|lyrics?|audio|video|visualizer|prod\.?|remix|live|slowed.*|sped.*|reverb|clip)[^\])]*[\])]\s*", " ", title, flags=re.I)
    title = re.sub(r"\s*[-–—]\s*(official|lyrics?|audio|video|visualizer|prod\.?|remix|live|slowed.*|sped.*|reverb)$", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" -–—")
    return title


def _split_compound_title(title: str) -> tuple[Optional[str], str]:
    raw = (title or "").strip()
    if not raw:
        return None, ""
    clean = _sanitize_lookup_title(raw)
    parts = re.split(r"\s*[-–—:]\s*", clean, maxsplit=1)
    if len(parts) == 2 and all(parts):
        left, right = parts[0].strip(), parts[1].strip()
        if len(left.split()) <= 6 and len(right.split()) >= 1:
            return left, right
    return None, clean


def _build_lookup_pairs(artist: str, title: str, candidates: Optional[list[dict]] = None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    def add_pair(a: str, t: str) -> None:
        a = (a or "").strip()
        t = _sanitize_lookup_title(t or "")
        if not a or not t:
            return
        key = (_normalize_text(a), _normalize_text(t))
        if not key[0] or not key[1]:
            return
        if key not in seen:
            seen.add(key)
            pairs.append((a, t))

    seen: set[tuple[str, str]] = set()
    for a in _artist_variants(artist):
        for t in _title_variants(title):
            add_pair(a, t)
            split_artist, split_title = _split_compound_title(t)
            if split_artist and split_title:
                add_pair(split_artist, split_title)
                if _normalize_text(a) != _normalize_text(split_artist):
                    add_pair(f"{a} {split_artist}", split_title)

    for item in candidates or []:
        cand_artist = (item.get("artist") or "").strip()
        cand_title = (item.get("title") or "").strip()
        if cand_artist and cand_title:
            add_pair(cand_artist, cand_title)
            split_artist, split_title = _split_compound_title(cand_title)
            if split_artist and split_title:
                add_pair(split_artist, split_title)
                add_pair(cand_artist, split_title)
                add_pair(f"{cand_artist} {split_artist}", split_title)

    return pairs
def _looks_like_matching_candidate(candidate: dict, artist: str, title: str) -> bool:
    cand_artist = _normalize_text(candidate.get("artist", ""))
    cand_title = _normalize_text(candidate.get("title", ""))
    artist_n = _normalize_text(artist)
    title_n = _normalize_text(title)
    if title_n and _similarity(cand_title, title_n) < 0.5 and title_n not in cand_title and cand_title not in title_n:
        return False
    if artist_n and _similarity(cand_artist, artist_n) < 0.35 and artist_n not in cand_artist and cand_artist not in artist_n:
        return False
    return True


_ROMANIZED_RE = re.compile(r"(romanized|translation|translated|traducao|traducción)", re.I)
_INSTRUMENTAL_RE = re.compile(r"(instrumental|karaoke|sped up|slowed|remix|live)", re.I)


def _should_drop_result(artist: str, title: str) -> bool:
    haystack = f"{artist} {title}"
    return bool(_ROMANIZED_RE.search(haystack) or _INSTRUMENTAL_RE.search(haystack))


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize_text(a), _normalize_text(b)).ratio()


async def _search_genius_api(query: str, max_results: int = 10) -> list[dict]:
    if not GENIUS_TOKEN or not query.strip():
        return []

    url = "https://api.genius.com/search"
    headers = {"Authorization": f"Bearer {GENIUS_TOKEN}"}
    last_error = None
    for proxy in _proxy_attempts():
        try:
            data = await _get_json(url, headers=headers, params={"q": query}, proxy=proxy)
            hits = (((data or {}).get("response") or {}).get("hits") or [])[: max_results * 4]
            results: list[dict] = []
            for hit in hits:
                result = hit.get("result") or {}
                artist = ((result.get("primary_artist") or {}).get("name") or "").strip()
                title = (result.get("title") or "").strip()
                url_val = (result.get("url") or "").strip()
                if not artist or not title or _should_drop_result(artist, title):
                    continue
                results.append({
                    "artist": artist,
                    "title": title,
                    "url": url_val,
                    "source": "genius_api",
                })
                if len(results) >= max_results:
                    break
            if results:
                return results
        except Exception as e:
            last_error = e
            logger.warning("Genius API search failed for '%s' via %s: %s", query, proxy or "direct", e)
    if last_error:
        logger.warning("All Genius API attempts failed for '%s': %s", query, last_error)
    return []


async def _search_genius_web(query: str, max_results: int = 10) -> list[dict]:
    if not query.strip():
        return []

    endpoints = [
        ("https://genius.com/api/search/song", {"q": query}),
        ("https://genius.com/api/search/multi", {"q": query}),
    ]

    for endpoint, params in endpoints:
        last_error = None
        for proxy in _proxy_attempts():
            try:
                data = await _get_json(endpoint, params=params, proxy=proxy)
                if not data:
                    continue

                response = data.get("response") or {}
                sections = []
                if "sections" in response:
                    sections = response.get("sections") or []
                elif "songs" in response:
                    sections = [{"hits": [{"result": x} for x in response.get("songs") or []]}]

                results: list[dict] = []
                seen: set[tuple[str, str]] = set()
                for section in sections:
                    section_type = (section.get("type") or "").lower()
                    if section_type and section_type not in {"song", "songs", "top_hit"}:
                        continue
                    for hit in section.get("hits") or []:
                        result = hit.get("result") or hit
                        artist = ((result.get("primary_artist") or {}).get("name") or "").strip()
                        title = (result.get("title") or result.get("full_title") or "").strip()
                        url_val = (result.get("url") or "").strip()
                        if not artist or not title or _should_drop_result(artist, title):
                            continue
                        key = (_normalize_text(artist), _normalize_text(title))
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append({
                            "artist": artist,
                            "title": title,
                            "url": url_val,
                            "source": "genius_web",
                        })
                        if len(results) >= max_results:
                            return results
                if results:
                    return results
            except Exception as e:
                last_error = e
                logger.warning("Genius web search failed for '%s' via %s: %s", query, proxy or "direct", e)
        if last_error:
            logger.warning("All Genius web attempts failed for '%s' on %s: %s", query, endpoint, last_error)
    return []


async def _search_lrclib(artist: str, title: str, max_results: int = 10) -> list[dict]:
    queries: list[dict] = []
    artist = (artist or "").strip()
    title = (title or "").strip()
    if artist and title:
        queries.extend([
            {"artist_name": artist, "track_name": title},
            {"q": f"{artist} {title}"},
            {"q": title},
        ])
    elif artist:
        queries.extend([
            {"q": artist},
        ])

    seen_params: set[tuple[tuple[str, str], ...]] = set()
    deduped_queries: list[dict] = []
    for params in queries:
        key = tuple(sorted((k, str(v)) for k, v in params.items() if v))
        if key and key not in seen_params:
            seen_params.add(key)
            deduped_queries.append(params)

    for params in deduped_queries:
        last_error = None
        for proxy in _proxy_attempts():
            try:
                data = await _get_json("https://lrclib.net/api/search", params=params, proxy=proxy)
                if not isinstance(data, list):
                    continue
                results: list[dict] = []
                seen: set[tuple[str, str]] = set()
                for item in data:
                    item_artist = (item.get("artistName") or "").strip()
                    item_title = (item.get("trackName") or "").strip()
                    if not item_artist or not item_title or _should_drop_result(item_artist, item_title):
                        continue
                    key = (_normalize_text(item_artist), _normalize_text(item_title))
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append({
                        "artist": item_artist,
                        "title": item_title,
                        "lyrics": (item.get("plainLyrics") or item.get("syncedLyrics") or "").strip(),
                        "source": "lrclib",
                    })
                    if len(results) >= max_results:
                        return results
                if results:
                    return results
            except Exception as e:
                last_error = e
                logger.warning("LRCLIB search failed for artist=%s title=%s via %s: %s", artist, title or "(empty)", proxy or "direct", e)
        if last_error:
            logger.warning("All LRCLIB attempts failed for artist=%s title=%s: %s", artist, title or "(empty)", last_error)
    return []


async def _search_itunes(query: str, max_results: int = 10) -> list[dict]:
    if not query.strip():
        return []
    last_error = None
    for proxy in _proxy_attempts():
        try:
            data = await _get_json(
                "https://itunes.apple.com/search",
                params={"entity": "song", "limit": max_results * 3, "term": query},
                proxy=proxy,
            )
            results = []
            seen: set[tuple[str, str]] = set()
            for item in (data or {}).get("results") or []:
                artist = (item.get("artistName") or "").strip()
                title = (item.get("trackName") or "").strip()
                if not artist or not title or _should_drop_result(artist, title):
                    continue
                key = (_normalize_text(artist), _normalize_text(title))
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "artist": artist,
                    "title": title,
                    "url": (item.get("trackViewUrl") or "").strip(),
                    "source": "itunes",
                })
                if len(results) >= max_results:
                    return results
            if results:
                return results
        except Exception as e:
            last_error = e
            logger.warning("iTunes search failed for '%s' via %s: %s", query, proxy or "direct", e)
    if last_error:
        logger.warning("All iTunes attempts failed for '%s': %s", query, last_error)
    return []


async def _search_soundcloud_candidates(query: str, max_results: int = 10) -> list[dict]:
    if not query.strip():
        return []
    try:
        token = await get_oauth_token(SOUNDCLOUD_CLIENT_ID, SOUNDCLOUD_CLIENT_SECRET)
        if not token:
            return []
        tracks = await search_soundcloud(query, token)
        results = []
        seen: set[tuple[str, str]] = set()
        for item in tracks:
            artist = (item.get("artist") or "").strip()
            title = (item.get("title") or "").strip()
            if not artist or not title or _should_drop_result(artist, title):
                continue
            key = (_normalize_text(artist), _normalize_text(title))
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "artist": artist,
                "title": title,
                "url": (item.get("url") or "").strip(),
                "source": "soundcloud",
            })
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        logger.warning("SoundCloud search failed for '%s': %s", query, e)
        return []


async def _lyrics_ovh_get(artist: str, title: str) -> Optional[str]:
    artist_q = urllib.parse.quote(artist.strip())
    title_q = urllib.parse.quote(title.strip())
    url = f"https://api.lyrics.ovh/v1/{artist_q}/{title_q}"

    last_error = None
    for proxy in _proxy_attempts():
        try:
            data = await _get_json(url, proxy=proxy)
            lyrics = (data or {}).get("lyrics")
            if lyrics:
                return lyrics.strip()
        except Exception as e:
            last_error = e
            logger.warning("lyrics.ovh request failed for %s - %s via %s: %s", artist, title, proxy or "direct", e)
    if last_error:
        logger.warning("All lyrics.ovh attempts failed for %s - %s: %s", artist, title, last_error)
    return None


async def _scrape_genius_lyrics(song_url: str) -> Optional[str]:
    if not song_url:
        return None

    last_error = None
    for proxy in _proxy_attempts():
        try:
            html = await _get_text(song_url, proxy=proxy)
            if not html:
                continue

            soup = _make_soup(html)
            containers = soup.select('[data-lyrics-container="true"]')
            if containers:
                parts = [c.get_text("\n", strip=True) for c in containers]
                lyrics = "\n".join(part for part in parts if part).strip()
                if lyrics:
                    return lyrics

            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("lyrics"):
                    return str(data["lyrics"]).strip()
        except Exception as e:
            last_error = e
            logger.warning("Failed to scrape Genius lyrics from %s via %s: %s", song_url, proxy or "direct", e)
    if last_error:
        logger.warning("All Genius scrape attempts failed for %s: %s", song_url, last_error)
    return None


async def _scrape_azlyrics_search(artist: str, title: str) -> Optional[str]:
    if not artist or not title:
        return None

    query = urllib.parse.quote_plus(f"site:azlyrics.com {artist} {title}")
    search_url = f"https://duckduckgo.com/html/?q={query}"
    ddg_headers = {"Referer": "https://duckduckgo.com/"}

    for proxy in _proxy_attempts():
        try:
            html = await _get_text(search_url, headers=ddg_headers, proxy=proxy)
            if not html:
                continue
            soup = _make_soup(html)
            link = None
            for a in soup.select("a.result__a, a[data-testid='result-title-a']"):
                href = (a.get("href") or "").strip()
                if "azlyrics.com/lyrics/" in href:
                    link = href
                    break
            if not link:
                continue
            page = await _get_text(link, proxy=proxy)
            if not page:
                continue
            marker = "Usage of azlyrics.com content"
            idx = page.find(marker)
            if idx == -1:
                continue
            sub = page[idx:]
            div_match = re.search(r"</div>\s*<div[^>]*>(.*?)</div>", sub, flags=re.S | re.I)
            if not div_match:
                continue
            raw = re.sub(r"<br\s*/?>", "\n", div_match.group(1), flags=re.I)
            raw = re.sub(r"<.*?>", "", raw, flags=re.S)
            lyrics = _make_soup(raw).get_text("\n", strip=True).strip()
            if lyrics:
                return lyrics
        except Exception as e:
            logger.warning("AZLyrics scrape failed for %s - %s via %s: %s", artist, title, proxy or "direct", e)
    return None


def _candidate_score(item: dict, artist_norm: str, title_norm: str, free_query_norm: str) -> tuple[float, int, int, int, float]:
    item_artist = _normalize_text(item.get("artist", ""))
    item_title = _normalize_text(item.get("title", ""))
    artist_exact = int(bool(artist_norm) and item_artist == artist_norm)
    artist_contains = int(bool(artist_norm) and (artist_norm in item_artist or item_artist in artist_norm))
    title_exact = int(bool(title_norm) and item_title == title_norm)
    title_contains = int(bool(title_norm) and (title_norm in item_title or item_title in title_norm))
    artist_similarity = _similarity(item_artist, artist_norm) if artist_norm else 0.0
    title_similarity = _similarity(item_title, title_norm) if title_norm else 0.0
    free_artist = _similarity(item_artist, free_query_norm) if free_query_norm else 0.0
    free_title = _similarity(item_title, free_query_norm) if free_query_norm else 0.0
    source_bonus = 3 if item.get("source") == "lrclib" and item.get("lyrics") else 0
    if item.get("source") == "soundcloud":
        source_bonus += 1
    total = (
        artist_exact * 10
        + artist_contains * 5
        + artist_similarity * 4
        + title_exact * 10
        + title_contains * 5
        + title_similarity * 4
        + max(free_artist, free_title) * 6
        + source_bonus
    )
    if free_query_norm and not title_norm:
        total += free_title * 8 + free_artist * 6
    return (total, title_exact + artist_exact, title_contains + artist_contains, source_bonus, max(free_title, free_artist))


async def search_lyrics_candidates(artist: str, title: str, max_results: int = 5) -> list[dict]:
    logger.info("Searching lyrics candidates: artist=%s title=%s max_results=%s", artist, title or "(empty)", max_results)

    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist and not title:
        return []

    free_query = f"{artist} {title}".strip() if title else artist
    free_query_norm = _normalize_text(free_query)
    artist_norm = _normalize_text(artist)
    title_norm = _normalize_text(title)

    def _merge_items(existing: list[dict], incoming: list[dict], seen_keys: set[tuple[str, str]]) -> None:
        for item in incoming:
            key = (_normalize_text(item["artist"]), _normalize_text(item["title"]))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            existing.append(item)

    async def _rank_and_slice(items: list[dict]) -> list[dict]:
        if not items:
            return []
        ranked = sorted(items, key=lambda item: _candidate_score(item, artist_norm, title_norm, free_query_norm), reverse=True)
        final = ranked[:max_results]
        logger.info(
            "Returning %s lyric candidates for artist=%s title=%s: %s",
            len(final),
            artist,
            title or "(empty)",
            [f"{item['artist']} - {item['title']} [{item.get('source')}]" for item in final],
        )
        return final

    combined: list[dict] = []
    seen_results: set[tuple[str, str]] = set()

    if artist and not title:
        fast_tasks = [
            _search_soundcloud_candidates(artist, max_results=max_results * 12),
            _search_itunes(artist, max_results=max_results * 12),
        ]
        fast_results = await asyncio.gather(*fast_tasks, return_exceptions=True)
        for result in fast_results:
            if isinstance(result, Exception):
                logger.warning("Fast artist-only candidate search failed for %s: %s", artist, result)
                continue
            _merge_items(combined, result, seen_results)

        if len(combined) >= max_results:
            return await _rank_and_slice(combined)

        slow_tasks = [
            _search_genius_web(artist, max_results=max_results * 8),
            _search_lrclib(artist, "", max_results=max_results * 8),
        ]
        if GENIUS_TOKEN:
            slow_tasks.append(_search_genius_api(artist, max_results=max_results * 6))

        slow_results = await asyncio.gather(*slow_tasks, return_exceptions=True)
        for result in slow_results:
            if isinstance(result, Exception):
                logger.warning("Slow artist-only candidate search failed for %s: %s", artist, result)
                continue
            _merge_items(combined, result, seen_results)

        return await _rank_and_slice(combined)

    queries: list[str] = []
    if artist and title:
        queries.extend([
            f"{artist} {title}",
            f"{artist} - {title}",
            title,
        ])
    else:
        queries.append(artist or title)

    dedup_queries: list[str] = []
    seen_queries: set[str] = set()
    for query in queries:
        key = _normalize_text(query)
        if key and key not in seen_queries:
            seen_queries.add(key)
            dedup_queries.append(query)

    primary_query = dedup_queries[0]
    fast_tasks = [
        _search_lrclib(artist, title, max_results=max_results * 6),
        _search_soundcloud_candidates(primary_query, max_results=max_results * 6),
        _search_itunes(primary_query, max_results=max_results * 6),
        _search_genius_web(primary_query, max_results=max_results * 6),
    ]
    if GENIUS_TOKEN:
        fast_tasks.append(_search_genius_api(primary_query, max_results=max_results * 4))

    fast_results = await asyncio.gather(*fast_tasks, return_exceptions=True)
    for result in fast_results:
        if isinstance(result, Exception):
            logger.warning("Track candidate search failed for %s - %s: %s", artist, title, result)
            continue
        _merge_items(combined, result, seen_results)

    if len(combined) >= max_results or len(dedup_queries) == 1:
        return await _rank_and_slice(combined)

    for query in dedup_queries[1:]:
        followup_results = await asyncio.gather(
            _search_soundcloud_candidates(query, max_results=max_results * 4),
            _search_itunes(query, max_results=max_results * 4),
            _search_genius_web(query, max_results=max_results * 4),
            return_exceptions=True,
        )
        for result in followup_results:
            if isinstance(result, Exception):
                logger.warning("Follow-up candidate search failed for query=%s: %s", query, result)
                continue
            _merge_items(combined, result, seen_results)
        if len(combined) >= max_results * 2:
            break

    return await _rank_and_slice(combined)


async def get_lyrics(artist: str, title: str) -> Optional[str]:
    logger.info("Getting lyrics for: %s - %s", artist, title)
    if not artist or not title:
        return None

    cached = _get_cached_lyrics(artist, title)
    if cached:
        logger.info("Returning cached lyrics for %s - %s", artist, title)
        return cached

    base_candidates = await search_lyrics_candidates(artist, title, max_results=8)
    lookup_pairs = _build_lookup_pairs(artist, title, base_candidates)
    logger.info("Expanded lyric lookup pairs for %s - %s: %s", artist, title, lookup_pairs[:12])

    for lookup_artist, lookup_title in lookup_pairs[:10]:
        cached = _get_cached_lyrics(lookup_artist, lookup_title)
        if cached:
            _set_cached_lyrics(artist, title, cached)
            return cached

        lrclib_results = await _search_lrclib(lookup_artist, lookup_title, max_results=6)
        for item in lrclib_results:
            direct = (item.get("lyrics") or "").strip()
            if direct:
                _set_cached_lyrics(lookup_artist, lookup_title, direct)
                _set_cached_lyrics(artist, title, direct)
                return direct

        lyrics = await _lyrics_ovh_get(lookup_artist, lookup_title)
        if lyrics:
            _set_cached_lyrics(lookup_artist, lookup_title, lyrics)
            _set_cached_lyrics(artist, title, lyrics)
            return lyrics

    for candidate in base_candidates:
        direct_lyrics = (candidate.get("lyrics") or "").strip()
        if direct_lyrics:
            _set_cached_lyrics(artist, title, direct_lyrics)
            return direct_lyrics
        if candidate.get("url") and candidate.get("source") in {"genius_web", "genius_api"}:
            lyrics = await _scrape_genius_lyrics(candidate["url"])
            if lyrics:
                lyrics = lyrics.strip()
                _set_cached_lyrics(artist, title, lyrics)
                return lyrics

    genius_queries = _unique_keep_order([
        f"{a} {t}".strip() for a, t in lookup_pairs[:8]
    ] + [
        f"{a} - {t}".strip() for a, t in lookup_pairs[:8]
    ] + [
        t for _, t in lookup_pairs[:8]
    ])

    for query in genius_queries[:12]:
        genius_candidates = await _search_genius_web(query, max_results=5)
        for candidate in genius_candidates:
            if not _looks_like_matching_candidate(candidate, artist, title):
                cand_artist = candidate.get("artist", "")
                cand_title = candidate.get("title", "")
                if not any(_looks_like_matching_candidate(candidate, a, t) for a, t in lookup_pairs[:10]):
                    continue
            if candidate.get("url"):
                lyrics = await _scrape_genius_lyrics(candidate["url"])
                if lyrics:
                    lyrics = lyrics.strip()
                    _set_cached_lyrics(candidate.get("artist", artist), candidate.get("title", title), lyrics)
                    _set_cached_lyrics(artist, title, lyrics)
                    return lyrics

    for lookup_artist, lookup_title in lookup_pairs[:8]:
        lyrics = await _scrape_azlyrics_search(lookup_artist, lookup_title)
        if lyrics:
            _set_cached_lyrics(lookup_artist, lookup_title, lyrics)
            _set_cached_lyrics(artist, title, lyrics)
            return lyrics

    logger.warning("No lyrics found for %s - %s after trying all sources", artist, title)
    return None
