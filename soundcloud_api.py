import logging
import aiohttp
from typing import List, Dict, Optional

from config import SOUNDCLOUD_CLIENT_ID, SOUNDCLOUD_CLIENT_SECRET

logger = logging.getLogger(__name__)

async def get_oauth_token(client_id: str = SOUNDCLOUD_CLIENT_ID, 
                          client_secret: str = SOUNDCLOUD_CLIENT_SECRET) -> Optional[str]:
    """oath токен саунд йопани клауд."""
    token_url = "https://api.soundcloud.com/oauth2/token"
    params = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'client_credentials'
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(token_url, data=params) as response:
            if response.status == 200:
                data = await response.json()
                return data['access_token']
            else:
                logger.error(f"Failed to retrieve token: {response.status}")
                return None

async def search_soundcloud(query: str, token: str) -> List[Dict]:
    tracks = []
    try:
        search_url = "https://api.soundcloud.com/tracks"
        params = {"q": query, "limit": 50}
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
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
                            "genre": track.get("genre", None)
                        })
                else:
                    logger.error(f"Search failed: {resp.status}")
    except Exception as e:
        logger.error(f"Error during search: {e}")
    return tracks