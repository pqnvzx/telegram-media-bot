import asyncio
import concurrent.futures
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yt_dlp

from config import (
    TEMP_DIR,
    YTDLP_MAX_WORKERS,
    YTDLP_CONCURRENT_FRAGMENTS,
    FFMPEG_THREADS,
    SAFE_MODE_2GB,
    LONG_VIDEO_MINUTES,
    SAFE_LONG_VIDEO_MAX_HEIGHT,
)

logger = logging.getLogger(__name__)

YTDLP_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=YTDLP_MAX_WORKERS)


def normalize_media_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("ttps://"):
        url = "h" + url
    elif url.startswith("ttp://"):
        url = "h" + url
    elif url.startswith("www."):
        url = "https://" + url
    return url





def _extract_quality_label(info: Dict[str, Any]) -> str:
    heights = []
    direct_height = info.get("height")
    if isinstance(direct_height, (int, float)) and direct_height > 0:
        heights.append(int(direct_height))

    for key in ("requested_formats", "formats"):
        value = info.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    h = item.get("height")
                    if isinstance(h, (int, float)) and h > 0:
                        heights.append(int(h))

    requested_downloads = info.get("requested_downloads")
    if isinstance(requested_downloads, list):
        for item in requested_downloads:
            if isinstance(item, dict):
                h = item.get("height")
                if isinstance(h, (int, float)) and h > 0:
                    heights.append(int(h))

    if heights:
        return f"{max(heights)}p"

    format_note = info.get("format_note")
    if isinstance(format_note, str) and format_note.strip():
        return format_note.strip()

    resolution = info.get("resolution")
    if isinstance(resolution, str) and resolution.strip():
        return resolution.strip()

    return "unknown"

def _safe_progress_callback(progress_callback, data: Dict[str, Any]):
    if not progress_callback:
        return
    try:
        progress_callback(data)
    except Exception:
        pass


async def download_track(track: Dict, progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> Optional[Dict[str, Any]]:
    track["url"] = normalize_media_url(track.get("url", ""))
    if not track.get("url"):
        logger.error("No URL found for track: %s", track.get("id"))
        return None

    work_dir = tempfile.mkdtemp(prefix="sc_", dir=str(TEMP_DIR))
    output_template = os.path.join(work_dir, f"{track['id']}.%(ext)s")

    def download_with_ytdlp():
        ydl_opts = {
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "noplaylist": True,
            "concurrent_fragment_downloads": max(1, YTDLP_CONCURRENT_FRAGMENTS),
            "postprocessor_args": ["-threads", str(max(1, FFMPEG_THREADS))],
            "progress_hooks": [lambda d: _safe_progress_callback(progress_callback, d)],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([track["url"]])

        files = sorted(Path(work_dir).glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        return str(files[0]) if files else None

    try:
        loop = asyncio.get_running_loop()
        downloaded_file = await loop.run_in_executor(YTDLP_EXECUTOR, download_with_ytdlp)
        if not downloaded_file or not os.path.exists(downloaded_file):
            cleanup_temp_dir(work_dir)
            logger.error("SoundCloud download failed: file was not created")
            return None
        return {"file_path": downloaded_file, "work_dir": work_dir}
    except Exception as e:
        cleanup_temp_dir(work_dir)
        logger.error("SoundCloud download error: %s", e, exc_info=True)
        return None


async def get_youtube_video_info(url: str) -> Dict[str, Any]:
    url = normalize_media_url(url)
    def extract_info():
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            channel = info.get("channel") or info.get("uploader") or info.get("uploader_id") or "unknown channel"
            title = info.get("title") or "unknown title"
            duration = info.get("duration") or 0
            return {
                "channel": str(channel).strip().lower(),
                "title": str(title).strip().lower(),
                "duration": duration,
                "webpage_url": info.get("webpage_url") or url,
            }
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(YTDLP_EXECUTOR, extract_info)


async def download_youtube_media(url: str, media_format: str, progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> Optional[Dict[str, Any]]:
    url = normalize_media_url(url)
    work_dir = tempfile.mkdtemp(prefix="yt_", dir=str(TEMP_DIR))
    output_template = os.path.join(work_dir, "%(title).180B [%(id)s].%(ext)s")

    def run_download():
        try:
            info_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            duration = int(info.get("duration") or 0)
            long_video = SAFE_MODE_2GB and duration >= LONG_VIDEO_MINUTES * 60

            base_opts = {
                "outtmpl": output_template,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "windowsfilenames": True,
                "concurrent_fragment_downloads": max(1, YTDLP_CONCURRENT_FRAGMENTS),
                "nopart": True,
                "writethumbnail": False,
                "writeinfojson": False,
                "writesubtitles": False,
                "writeautomaticsub": False,
                "progress_hooks": [lambda d: _safe_progress_callback(progress_callback, d)],
                "postprocessor_args": ["-threads", str(max(1, FFMPEG_THREADS))],
                "buffersize": 1024,
                "http_chunk_size": 1048576,
            }

            if media_format == "mp3":
                ydl_opts = {
                    **base_opts,
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                }
            elif media_format == "mp4":
                if long_video:
                    ydl_opts = {
                        **base_opts,
                        "format": (
                            f"bv*[height<={SAFE_LONG_VIDEO_MAX_HEIGHT}][ext=mp4]+ba[ext=m4a]/"
                            f"bv*[height<={SAFE_LONG_VIDEO_MAX_HEIGHT}]+ba/"
                            f"best[height<={SAFE_LONG_VIDEO_MAX_HEIGHT}][ext=mp4]/"
                            f"best[height<={SAFE_LONG_VIDEO_MAX_HEIGHT}]"
                        ),
                        "merge_output_format": "mp4",
                    }
                else:
                    ydl_opts = {
                        **base_opts,
                        "format": (
                            "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
                            "bv*[height<=1080]+ba/"
                            "best[height<=1080][ext=mp4]/"
                            "best[height<=1080]"
                        ),
                        "merge_output_format": "mp4",
                    }
            else:
                raise ValueError(f"Unsupported media format: {media_format}")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            files = [
                os.path.join(work_dir, name)
                for name in os.listdir(work_dir)
                if os.path.isfile(os.path.join(work_dir, name))
            ]
            if not files:
                return None
            files.sort(key=os.path.getmtime, reverse=True)
            final_file = files[0]

            channel = info.get("channel") or info.get("uploader") or info.get("uploader_id") or "unknown channel"
            title = info.get("title") or "unknown title"
            duration = info.get("duration") or 0
            return {
                "file_path": final_file,
                "work_dir": work_dir,
                "channel": str(channel).strip().lower(),
                "title": str(title).strip().lower(),
                "duration": duration,
                "webpage_url": info.get("webpage_url") or url,
                "quality": _extract_quality_label(info) if media_format == "mp4" else None,
            }
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(YTDLP_EXECUTOR, run_download)
    except Exception as e:
        logger.error("YouTube %s download error: %s", media_format, e, exc_info=True)
        return None


def cleanup_files(track_id: str, compressed_file: Optional[str] = None):
    for file_path in filter(None, [compressed_file]):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info("Cleaned up temp file: %s", file_path)
        except Exception as cleanup_error:
            logger.error("Error cleaning up temp file: %s", cleanup_error)


def cleanup_temp_dir(path: Optional[str]):
    if not path:
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
        logger.info("Cleaned up temp dir: %s", path)
    except Exception as e:
        logger.error("Error cleaning temp dir %s: %s", path, e)
