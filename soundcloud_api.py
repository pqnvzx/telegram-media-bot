import logging
import aiohttp
import asyncio
import time
from typing import List, Dict, Optional

from config import SOUNDCLOUD_CLIENT_ID, SOUNDCLOUD_CLIENT_SECRET

logger = logging.getLogger(__name__)

# Кэш OAuth токена
_TOKEN_CACHE = {
    "access_token": None,
    "expires_at": 0,
}

# Кэш поисковых запросов
_SEARCH_CACHE: Dict[str, Dict] = {}

# Настройки защиты от rate limit
REQUEST_DELAY_SECONDS = 1.5
SEARCH_CACHE_TTL_SECONDS = 300  # 5 минут

_last_request_ts = 0.0


async def _rate_limit():
    global _last_request_ts
    now = time.time()
    diff = now - _last_request_ts

    if diff < REQUEST_DELAY_SECONDS:
        await asyncio.sleep(REQUEST_DELAY_SECONDS - diff)

    _last_request_ts = time.time()


async def get_oauth_token(
    client_id: str = SOUNDCLOUD_CLIENT_ID,
    client_secret: str = SOUNDCLOUD_CLIENT_SECRET
) -> Optional[str]:
    """OAuth токен SoundCloud с кэшированием."""
    now = time.time()

    # Если токен ещё жив — возвращаем его
    if _TOKEN_CACHE["access_token"] and _TOKEN_CACHE["expires_at"] > now:
        return _TOKEN_CACHE["access_token"]

    token_url = "https://api.soundcloud.com/oauth2/token"
    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        await _rate_limit()

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(token_url, data=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()

                    access_token = data.get("access_token")
                    expires_in = int(data.get("expires_in", 3600))

                    if not access_token:
                        logger.error("SoundCloud token response has no access_token")
                        return None

                    # кэшируем токен с запасом 60 секунд
                    _TOKEN_CACHE["access_token"] = access_token
                    _TOKEN_CACHE["expires_at"] = now + max(60, expires_in - 60)

                    logger.info("SoundCloud OAuth token received and cached")
                    return access_token
                else:
                    body = await response.text()
                    logger.error(f"Failed to retrieve token: {response.status} | {body[:300]}")
                    return None

    except Exception as e:
        logger.error(f"Error retrieving token: {e}", exc_info=True)
        return None


async def search_soundcloud(query: str, token: str) -> List[Dict]:
    tracks = []
    query = (query or "").strip()

    if not query:
        return tracks

    # Кэш поиска
    cache_key = query.lower()
    now = time.time()
    cached = _SEARCH_CACHE.get(cache_key)

    if cached and cached["expires_at"] > now:
        return cached["tracks"]

    try:
        await _rate_limit()

        search_url = "https://api.soundcloud.com/tracks"
        params = {"q": query, "limit": 20}
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(search_url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    for track in data:
                        tracks.append({
                            "id": track["id"],
                            "title": track["title"],
                            "artist": track["user"]["username"],
                            "duration": track.get("duration", 0) // 1000,
                            "url": track["permalink_url"],
                            "genre": track.get("genre", None),
                        })

                    # Кэшируем успешный результат
                    _SEARCH_CACHE[cache_key] = {
                        "tracks": tracks,
                        "expires_at": now + SEARCH_CACHE_TTL_SECONDS,
                    }

                elif resp.status == 429:
                    logger.error("SoundCloud rate limit hit (429)")
                else:
                    body = await resp.text()
                    logger.error(f"Search failed: {resp.status} | {body[:300]}")

    except Exception as e:
        logger.error(f"Error during search: {e}", exc_info=True)

    return tracks