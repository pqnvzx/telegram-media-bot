import os
from pathlib import Path
from typing import Dict
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
SOUNDCLOUD_CLIENT_ID = os.getenv("SOUNDCLOUD_CLIENT_ID")
SOUNDCLOUD_CLIENT_SECRET = os.getenv("SOUNDCLOUD_CLIENT_SECRET")

TELEGRAM_LOCAL_API_URL = os.getenv("TELEGRAM_LOCAL_API_URL", "http://127.0.0.1:8081")
TELEGRAM_LOCAL_MODE = os.getenv("TELEGRAM_LOCAL_MODE", "true").lower() == "true"

MAX_FILE_SIZE = 1900 * 1024 * 1024
TARGET_SIZE_MB = 1800

user_languages: Dict[int, str] = {}
user_states: Dict[int, str] = {}

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set")
if not SOUNDCLOUD_CLIENT_ID:
    raise ValueError("SOUNDCLOUD_CLIENT_ID is not set")
if not SOUNDCLOUD_CLIENT_SECRET:
    raise ValueError("SOUNDCLOUD_CLIENT_SECRET is not set")


# 'main' - главное меню
# 'music' - режим поиска музыки
# 'playlist' - режим плейлиста
# 'video' - режим видео
# 'social' - режим соц сетей
# 'lyrics' - режим текста
TEMP_DIR = Path(os.getenv("TEMP_DIR", str(BASE_DIR / "temp"))).resolve()
TEMP_DIR.mkdir(parents=True, exist_ok=True)

YTDLP_MAX_WORKERS = int(os.getenv("YTDLP_MAX_WORKERS", "1"))
YTDLP_CONCURRENT_FRAGMENTS = int(os.getenv("YTDLP_CONCURRENT_FRAGMENTS", "1"))
FFMPEG_THREADS = int(os.getenv("FFMPEG_THREADS", "1"))
SAFE_MODE_2GB = os.getenv("SAFE_MODE_2GB", "true").lower() == "true"
LONG_VIDEO_MINUTES = int(os.getenv("LONG_VIDEO_MINUTES", "20"))
SAFE_LONG_VIDEO_MAX_HEIGHT = int(os.getenv("SAFE_LONG_VIDEO_MAX_HEIGHT", "720"))
