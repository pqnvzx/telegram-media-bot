import asyncio
import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yt_dlp

from config import (
    TEMP_DIR,
    YTDLP_MAX_WORKERS,
    YTDLP_CONCURRENT_FRAGMENTS,
    FFMPEG_THREADS,
    LONG_VIDEO_MINUTES,
)

logger = logging.getLogger(__name__)

YTDLP_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=YTDLP_MAX_WORKERS)


def cleanup_temp_dir(path: Optional[str]):
    if not path:
        return

    # On Windows, active file handles can prevent directory removal.
    # Retry a few times to give handles a chance to close.
    for attempt in range(1, 6):
        try:
            shutil.rmtree(path)
            logger.info("Cleaned up temp dir: %s", path)
            return
        except Exception as e:
            if attempt < 5:
                logger.debug("Retrying cleanup of %s (attempt %s): %s", path, attempt, e)
                time.sleep(0.1)
                continue
            logger.error("Error cleaning temp dir %s: %s", path, e)


def normalize_media_url(url: str) -> str:
    url = (url or "").strip()

    if url.startswith("ttps://"):
        url = "h" + url
    elif url.startswith("ttp://"):
        url = "h" + url
    elif url.startswith("tps://"):
        url = "ht" + url
    elif url.startswith("ps://"):
        url = "htt" + url
    elif url.startswith("://"):
        url = "https" + url
    elif url.startswith("www."):
        url = "https://" + url
    elif not url.startswith(("http://", "https://")):
        if any(x in url for x in ("youtube.com", "youtu.be", "soundcloud.com")):
            url = "https://" + url

    return url


def _extract_quality_label(info: Dict[str, Any]) -> str:
    heights: List[int] = []

    direct_height = info.get("height")
    if isinstance(direct_height, (int, float)) and direct_height > 0:
        heights.append(int(direct_height))

    for key in ("requested_formats", "formats", "requested_downloads"):
        value = info.get(key)
        if isinstance(value, list):
            for item in value:
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


def _max_height(info: Dict[str, Any]) -> int:
    heights = []

    direct_height = info.get("height")
    if isinstance(direct_height, (int, float)) and direct_height > 0:
        heights.append(int(direct_height))

    for key in ("requested_formats", "formats", "requested_downloads"):
        value = info.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    h = item.get("height")
                    if isinstance(h, (int, float)) and h > 0:
                        heights.append(int(h))

    return max(heights) if heights else 0


def _safe_progress_callback(progress_callback, data: Dict[str, Any]):
    if not progress_callback:
        return
    try:
        progress_callback(data)
    except Exception:
        pass


def _yt_headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }


def _base_youtube_opts(
    output_template: str,
    progress_callback=None,
    use_external_downloader: bool = True,
) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "windowsfilenames": True,
        "nopart": False,
        "continuedl": False,
        "concurrent_fragment_downloads": max(1, YTDLP_CONCURRENT_FRAGMENTS),
        "buffersize": 16 * 1024,
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "file_access_retries": 3,
        "skip_unavailable_fragments": True,
        "source_address": "0.0.0.0",
        "force_ipv4": True,
        "http_headers": _yt_headers(),
        "writethumbnail": False,
        "writeinfojson": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "prefer_ffmpeg": True,
        "merge_output_format": "mp4",
        "extractor_args": {
            "youtube": {
                # Don't force a specific player client; some clients can limit available resolutions.
                # Let yt-dlp pick the best available unless we need special overrides.
                "skip": ["translated_subs"],
                "player_skip": [],
            }
        },
        "youtube_include_dash_manifest": True,
        "youtube_include_hls_manifest": True,
        "format_sort": [
            "codec:h264",
            "acodec:m4a",
            "res",
            "fps",
            "size",
        ],
        "postprocessor_args": ["-threads", str(max(1, FFMPEG_THREADS))],
        "progress_hooks": [lambda d: _safe_progress_callback(progress_callback, d)],
    }

    deno_path = os.getenv("YT_DENO_PATH", "").strip()
    if deno_path:
        opts["js_runtimes"] = {
            "deno": {
                "path": deno_path,
            }
        }

    if use_external_downloader:
        external_dl = os.getenv("YTDLP_EXTERNAL_DOWNLOADER", "").strip()
        if external_dl:
            opts["external_downloader"] = external_dl
            env_args = os.getenv("YTDLP_EXTERNAL_DOWNLOADER_ARGS", "").strip()
            if env_args:
                opts["external_downloader_args"] = env_args.split()
            elif external_dl == "aria2c":
                opts["external_downloader_args"] = [
                    "-x",
                    "16",
                    "-s",
                    "16",
                    "--max-connection-per-server=16",
                    "--file-allocation=none",
                ]
            logger.info(
                "Enabled external downloader %s %s",
                external_dl,
                opts.get("external_downloader_args"),
            )

    return opts


async def download_track(
    track: Dict,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Optional[Dict[str, Any]]:
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
            "prefer_ffmpeg": True,
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
            **_base_youtube_opts("%(title)s.%(ext)s", use_external_downloader=False),
            "skip_download": True,
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


def _is_video_only(fmt: Dict[str, Any]) -> bool:
    return fmt.get("vcodec") not in (None, "", "none") and fmt.get("acodec") == "none"


def _is_audio_only(fmt: Dict[str, Any]) -> bool:
    return fmt.get("acodec") not in (None, "", "none") and fmt.get("vcodec") == "none"


def _is_progressive(fmt: Dict[str, Any]) -> bool:
    return fmt.get("vcodec") not in (None, "", "none") and fmt.get("acodec") not in (None, "", "none")


def _video_sort_key(fmt: Dict[str, Any]) -> Tuple:
    height = int(fmt.get("height") or 0)
    fps = float(fmt.get("fps") or 0)
    tbr = float(fmt.get("tbr") or 0)
    ext = str(fmt.get("ext") or "")
    vcodec = str(fmt.get("vcodec") or "")
    is_h264 = 1 if vcodec.startswith("avc1") or "h264" in vcodec else 0
    is_mp4 = 1 if ext == "mp4" else 0
    return (height, is_h264, is_mp4, fps, tbr)


def _audio_sort_key(fmt: Dict[str, Any]) -> Tuple:
    abr = float(fmt.get("abr") or 0)
    tbr = float(fmt.get("tbr") or 0)
    ext = str(fmt.get("ext") or "")
    acodec = str(fmt.get("acodec") or "")
    is_m4a = 1 if ext in ("m4a", "mp4") else 0
    is_aac = 1 if ("mp4a" in acodec or "aac" in acodec) else 0
    return (is_m4a, is_aac, abr, tbr)


def _build_video_profiles(formats: List[Dict[str, Any]], max_height: int) -> List[Tuple[str, str]]:
    audio_only = [fmt for fmt in formats if _is_audio_only(fmt)]
    video_only = [
        fmt for fmt in formats
        if _is_video_only(fmt) and int(fmt.get("height") or 0) > 0 and int(fmt.get("height") or 0) <= max_height
    ]
    progressive = [
        fmt for fmt in formats
        if _is_progressive(fmt) and int(fmt.get("height") or 0) > 0 and int(fmt.get("height") or 0) <= max_height
    ]

    audio_only.sort(key=_audio_sort_key, reverse=True)
    video_only.sort(key=_video_sort_key, reverse=True)
    progressive.sort(key=_video_sort_key, reverse=True)

    profiles: List[Tuple[str, str]] = []
    seen: set[str] = set()

    top_audios = audio_only[:2]
    top_videos = video_only[:8]
    top_progressive = progressive[:4]

    for vfmt in top_videos:
        for afmt in top_audios:
            fmt = f"{vfmt['format_id']}+{afmt['format_id']}"
            if fmt not in seen:
                profiles.append((f"dash_{vfmt.get('height')}_{vfmt['format_id']}_{afmt['format_id']}", fmt))
                seen.add(fmt)

    for pfmt in top_progressive:
        fmt = str(pfmt["format_id"])
        if fmt not in seen:
            profiles.append((f"progressive_{pfmt.get('height')}_{pfmt['format_id']}", fmt))
            seen.add(fmt)

    return profiles


def _ffprobe_streams(path: str) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except Exception as e:
        logger.warning("ffprobe failed for %s: %s", path, e)
        return {}


def _needs_mobile_safe_transcode(path: str) -> bool:
    meta = _ffprobe_streams(path)
    streams = meta.get("streams", []) if isinstance(meta, dict) else []
    fmt = meta.get("format", {}) if isinstance(meta, dict) else {}
    container = str(fmt.get("format_name") or "")

    video_codec = None
    audio_codec = None
    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and not video_codec:
            video_codec = str(stream.get("codec_name") or "")
        elif codec_type == "audio" and not audio_codec:
            audio_codec = str(stream.get("codec_name") or "")

    if "mp4" not in container and "mov" not in container:
        return True
    if video_codec not in ("h264",):
        return True
    if audio_codec not in ("aac", "mp3", "mp4a"):
        return True
    return False


def _normalized_output_path(input_path: str) -> str:
    base, _ = os.path.splitext(input_path)
    return base + ".mobile.mp4"


def _normalize_for_telegram(input_path: str, ffmpeg_threads: int) -> str:
    output_path = _normalized_output_path(input_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-threads",
        str(max(1, ffmpeg_threads)),
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


async def download_youtube_media(
    url: str,
    media_format: str,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Optional[Dict[str, Any]]:
    url = normalize_media_url(url)
    work_dir = tempfile.mkdtemp(prefix="yt_", dir=str(TEMP_DIR))
    output_template = os.path.join(work_dir, "%(title).180B [%(id)s].%(ext)s")

    def run_download():
        try:
            logger.info("Starting YouTube download: url=%s format=%s", url, media_format)
            info_opts = {
                **_base_youtube_opts(output_template),
                "skip_download": True,
            }

            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            duration = int(info.get("duration") or 0)
            formats = info.get("formats") or []
            title = info.get("title") or "unknown title"
            channel = info.get("channel") or info.get("uploader") or info.get("uploader_id") or "unknown channel"
            webpage_url = info.get("webpage_url") or url

            logger.info(
                "YouTube info: title=%s channel=%s duration=%s formats=%s",
                title,
                channel,
                duration,
                len(formats),
            )

            # Determine whether to use an external downloader (aria2c/etc).
            # This is enabled via YTDLP_EXTERNAL_DOWNLOADER and can be tuned with
            # YTDLP_EXTERNAL_DOWNLOADER_MODE:
            #   - always: always use external downloader
            #   - auto: use only for large downloads (default)
            #   - never: never use external downloader
            external_mode = os.getenv("YTDLP_EXTERNAL_DOWNLOADER_MODE", "auto").strip().lower()
            use_external_downloader = False
            if os.getenv("YTDLP_EXTERNAL_DOWNLOADER", "").strip():
                if external_mode == "always":
                    use_external_downloader = True
                elif external_mode == "auto":
                    size = int(info.get("filesize") or info.get("filesize_approx") or 0)
                    use_external_downloader = size >= 80 * 1024 * 1024

            if media_format == "mp3":
                ydl_opts = {
                    **_base_youtube_opts(
                        output_template,
                        progress_callback=progress_callback,
                        use_external_downloader=use_external_downloader,
                    ),
                    "format": "bestaudio/best",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_result = ydl.extract_info(url, download=True)

            elif media_format == "mp4":
                # Download best available MP4-compatible stream, prioritizing >=720p.
                # If >=720p isn't available, fall back to the best available stream.
                fmt = (
                    "bestvideo[height>=720]+bestaudio/best[height>=720]/"
                    "bestvideo+bestaudio/best"
                )
                ydl_opts = {
                    **_base_youtube_opts(
                        output_template,
                        progress_callback=progress_callback,
                        use_external_downloader=use_external_downloader,
                    ),
                    "format": fmt,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_result = ydl.extract_info(url, download=True)

                logger.info(
                    "YouTube download selected format: %s height=%s resolution=%s",
                    info_result.get("format"),
                    info_result.get("height"),
                    info_result.get("resolution"),
                )
            else:
                raise ValueError(f"Unsupported media format: {media_format}")

            files = [
                os.path.join(work_dir, name)
                for name in os.listdir(work_dir)
                if os.path.isfile(os.path.join(work_dir, name))
            ]
            if not files:
                return None

            files.sort(key=os.path.getmtime, reverse=True)
            final_file = files[0]
            width = info_result.get("width")
            height = info_result.get("height")

            # Fallback to ffprobe if dimensions aren't provided by yt-dlp.
            if width is None or height is None:
                file_meta = _ffprobe_streams(final_file)
                width = None
                height = None
                for stream in file_meta.get("streams", []) if isinstance(file_meta, dict) else []:
                    if stream.get("codec_type") == "video":
                        width = stream.get("width")
                        height = stream.get("height")
                        break

            logger.info(
                "YouTube download finished: file=%s size=%s width=%s height=%s",
                final_file,
                os.path.getsize(final_file),
                width,
                height,
            )

            return {
                "file_path": final_file,
                "work_dir": work_dir,
                "channel": str(channel).strip().lower(),
                "title": str(title).strip().lower(),
                "duration": int(info_result.get("duration") or duration),
                "webpage_url": info_result.get("webpage_url") or webpage_url,
                "quality": _extract_quality_label(info_result) if media_format == "mp4" else None,
                "width": width,
                "height": height,
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


def cleanup_files(track_id: str = "", compressed_file: Optional[str] = None, extra_file: Optional[str] = None):
    for file_path in filter(None, [compressed_file, extra_file]):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info("Cleaned up temp file: %s", file_path)
        except Exception as cleanup_error:
            logger.error("Error cleaning up temp file: %s", cleanup_error)