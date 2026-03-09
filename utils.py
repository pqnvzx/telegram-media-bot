import asyncio
import concurrent.futures
import logging
import os
import subprocess
import time
from pathlib import Path

from telegram import Bot, InputFile
from telegram.error import RetryAfter
from telegram.ext import ContextTypes

from texts import get_text
from downloader import (
    download_track,
    cleanup_files,
    download_youtube_media,
    cleanup_temp_dir,
)
from audio_processor import compress_audio_file
from config import MAX_FILE_SIZE, TARGET_SIZE_MB, user_states
from keyboards import get_back_button

logger = logging.getLogger(__name__)

PROCESS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def _format_bytes_per_sec(value: float) -> str:
    if not value:
        return "0 B/s"
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    size = float(value)
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{size:.1f} {units[idx]}"


def _build_progress_bar(percent: float, length: int = 10) -> str:
    percent = max(0.0, min(100.0, percent))
    filled = int(round((percent / 100.0) * length))
    return "█" * filled + "░" * (length - filled)


def format_progress_text(user_id: int, media_format: str, data: dict) -> str:
    status = data.get("status")
    if status == "finished":
        return get_text(user_id, "progress_processing", format=media_format)

    total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
    downloaded = data.get("downloaded_bytes") or 0
    percent = (downloaded / total * 100.0) if total else 0.0
    speed = _format_bytes_per_sec(data.get("speed") or 0)
    eta = data.get("eta")
    eta_text = f"{int(eta // 60):02}:{int(eta % 60):02}" if isinstance(eta, (int, float)) else "--:--"
    return get_text(
        user_id,
        "progress_template",
        format=media_format.upper(),
        bar=_build_progress_bar(percent),
        percent=f"{percent:.1f}",
        speed=speed,
        eta=eta_text,
    )


async def _safe_edit_message(message, text: str):
    try:
        await message.edit_text(text)
    except RetryAfter as e:
        await asyncio.sleep(float(getattr(e, "retry_after", 1)) + 0.5)
        try:
            await message.edit_text(text)
        except Exception:
            pass
    except Exception:
        pass


async def run_with_progress(bot, chat_id: int, user_id: int, media_format: str, coro_factory):
    progress_message = await bot.send_message(
        chat_id=chat_id,
        text=get_text(user_id, "queue_started", format=media_format),
    )

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def progress_callback(data: dict):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, data)
        except Exception:
            pass

    async def updater():
        last_text = None
        last_edit = 0.0
        last_percent_bucket = -1
        pending_item = None
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                item = None

            if item is None and pending_item is None:
                continue

            if item is None:
                item = pending_item
            elif item is not None:
                pending_item = item

            if item is None:
                continue
            if item is False:
                break

            now = time.monotonic()
            text = format_progress_text(user_id, media_format, item)
            percent = int(float(item.get("downloaded_bytes") or 0) * 100 / max(1, (item.get("total_bytes") or item.get("total_bytes_estimate") or 1))) if item.get("status") != "finished" else 100
            if text == last_text:
                continue
            if item.get("status") != "finished" and percent == last_percent_bucket and now - last_edit < 10.0:
                continue
            # throttle edits to avoid Flood control.
            if now - last_edit < 5.0 and item.get("status") != "finished":
                pending_item = item
                continue
            pending_item = None
            last_text = text
            last_percent_bucket = percent
            last_edit = now
            await _safe_edit_message(progress_message, text)
            if item.get("status") == "finished":
                break

    updater_task = asyncio.create_task(updater())
    try:
        result = await coro_factory(progress_callback)
        return result, progress_message
    finally:
        await queue.put(False)
        await updater_task


async def send_done_with_back(bot, chat_id: int, user_id: int):
    await bot.send_message(chat_id=chat_id, text="✅", reply_markup=get_back_button(user_id))
    user_states[user_id] = "done_wait_back"



def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        logger.info("✅ ffmpeg is installed")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False



def check_dependencies() -> bool:
    try:
        import yt_dlp  # noqa: F401
        logger.info("✅ yt-dlp is installed")
        return True
    except ImportError as e:
        logger.error("❌ Missing required package: %s", e)
        logger.error("Please install: pip install yt-dlp")
        return False


def _make_input_file(file_path: str) -> InputFile:
    fp = open(file_path, "rb")
    return InputFile(fp, filename=os.path.basename(file_path))


async def _send_document_fallback(bot, chat_id, file_path, caption, parse_mode, read_timeout, write_timeout, pool_timeout):
    document = _make_input_file(file_path)
    try:
        await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=caption,
            parse_mode=parse_mode,
            disable_content_type_detection=True,
            read_timeout=read_timeout,
            write_timeout=write_timeout,
            pool_timeout=pool_timeout,
        )
    finally:
        try:
            document.obj.close()
        except Exception:
            pass




async def send_audio_safely(bot, chat_id, file_path, title, performer, duration, caption, parse_mode="HTML"):
    audio = _make_input_file(file_path)
    try:
        try:
            await bot.send_audio(
                chat_id=chat_id,
                audio=audio,
                title=title,
                performer=performer,
                duration=duration,
                caption=caption,
                parse_mode=parse_mode,
                read_timeout=3600,
                write_timeout=3600,
                pool_timeout=120,
            )
            return
        except RetryAfter as e:
            await asyncio.sleep(float(getattr(e, "retry_after", 1)) + 0.5)
            audio_retry = _make_input_file(file_path)
            try:
                await bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_retry,
                    title=title,
                    performer=performer,
                    duration=duration,
                    caption=caption,
                    parse_mode=parse_mode,
                    read_timeout=3600,
                    write_timeout=3600,
                    pool_timeout=120,
                )
                return
            finally:
                try:
                    audio_retry.obj.close()
                except Exception:
                    pass
        except Exception:
            pass

        await _send_document_fallback(bot, chat_id, file_path, caption, parse_mode, 3600, 3600, 120)
    finally:
        try:
            audio.obj.close()
        except Exception:
            pass


async def send_video_safely(bot, chat_id, file_path, duration, caption, parse_mode="HTML"):
    video = _make_input_file(file_path)
    try:
        try:
            await bot.send_video(
                chat_id=chat_id,
                video=video,
                duration=duration,
                caption=caption,
                parse_mode=parse_mode,
                supports_streaming=True,
                read_timeout=7200,
                write_timeout=7200,
                pool_timeout=120,
            )
            return
        except RetryAfter as e:
            await asyncio.sleep(float(getattr(e, "retry_after", 1)) + 0.5)
            video_retry = _make_input_file(file_path)
            try:
                await bot.send_video(
                    chat_id=chat_id,
                    video=video_retry,
                    duration=duration,
                    caption=caption,
                    parse_mode=parse_mode,
                    supports_streaming=True,
                    read_timeout=7200,
                    write_timeout=7200,
                    pool_timeout=120,
                )
                return
            finally:
                try:
                    video_retry.obj.close()
                except Exception:
                    pass
        except Exception:
            pass

        await _send_document_fallback(bot, chat_id, file_path, caption, parse_mode, 7200, 7200, 120)
    finally:
        try:
            video.obj.close()
        except Exception:
            pass


async def handle_track_download(track: dict, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    compressed_file = None
    progress_message = None
    work_dir = None

    try:
        result, progress_message = await run_with_progress(
            context.bot,
            user_id,
            user_id,
            "mp3",
            lambda progress_callback: download_track(track, progress_callback=progress_callback),
        )

        if not result:
            await context.bot.send_message(chat_id=user_id, text=get_text(user_id, "download_error"))
            return

        downloaded_file = result["file_path"]
        work_dir = result.get("work_dir")
        file_size = os.path.getsize(downloaded_file)
        genre = track.get("genre") or get_text(user_id, "genre_unknown")
        send_path = downloaded_file

        if file_size > MAX_FILE_SIZE:
            await context.bot.send_message(chat_id=user_id, text=get_text(user_id, "compressing"))
            loop = asyncio.get_running_loop()
            compressed_file = await loop.run_in_executor(PROCESS_EXECUTOR, compress_audio_file, downloaded_file, TARGET_SIZE_MB)
            if not compressed_file or not os.path.exists(compressed_file):
                await context.bot.send_message(chat_id=user_id, text=get_text(user_id, "compression_failed"))
                return
            if os.path.getsize(compressed_file) > MAX_FILE_SIZE:
                await context.bot.send_message(chat_id=user_id, text=get_text(user_id, "compression_failed"))
                return
            send_path = compressed_file

        caption = (
            f"жанр: {genre}\n"
            f'<a href="{track["url"]}">{get_text(user_id, "soundcloud_link")}</a>\n'
            f"@musicshithead_bot"
        )
        await send_audio_safely(
            bot=context.bot,
            chat_id=user_id,
            file_path=send_path,
            title=track["title"],
            performer=track["artist"],
            duration=track["duration"],
            caption=caption,
        )
        try:
            await progress_message.delete()
        except Exception:
            pass
        await send_done_with_back(context.bot, user_id, user_id)

    except Exception as e:
        logger.error("Download error: %s", e, exc_info=True)
        await context.bot.send_message(chat_id=user_id, text=get_text(user_id, "download_error"))
    finally:
        cleanup_files(track.get("id", ""), compressed_file)
        cleanup_temp_dir(work_dir)


class YouTubeDownloadQueue:
    def __init__(self, workers_count: int = 1):
        self.queue = asyncio.Queue()
        self.workers_count = workers_count
        self.workers = []
        self.started = False

    async def start(self):
        if self.started:
            return
        self.started = True
        for index in range(self.workers_count):
            task = asyncio.create_task(self.worker(index + 1))
            self.workers.append(task)
        logger.info("YouTube queue started with %s worker(s)", self.workers_count)

    async def enqueue(self, bot: Bot, user_id: int, url: str, media_format: str) -> int:
        await self.start()
        position = self.queue.qsize() + 1
        await self.queue.put({"bot": bot, "user_id": user_id, "url": url, "media_format": media_format})
        return position

    async def worker(self, worker_id: int):
        while True:
            job = await self.queue.get()
            try:
                await self.process_job(job, worker_id)
            except Exception as e:
                logger.error("Queue worker %s error: %s", worker_id, e, exc_info=True)
            finally:
                self.queue.task_done()

    async def process_job(self, job: dict, worker_id: int):
        bot: Bot = job["bot"]
        user_id: int = job["user_id"]
        url: str = job["url"]
        media_format: str = job["media_format"]
        work_dir = None
        compressed_file = None
        progress_message = None
        try:
            result, progress_message = await run_with_progress(
                bot,
                user_id,
                user_id,
                media_format,
                lambda progress_callback: download_youtube_media(url, media_format, progress_callback=progress_callback),
            )
            if not result:
                await bot.send_message(chat_id=user_id, text=get_text(user_id, "youtube_download_error"))
                return

            file_path = result["file_path"]
            work_dir = result["work_dir"]
            channel = result["channel"]
            title = result["title"]
            duration = result["duration"]
            webpage_url = result["webpage_url"]
            quality = result.get("quality")
            file_size = os.path.getsize(file_path)
            send_path = file_path

            if media_format == "mp3" and file_size > MAX_FILE_SIZE:
                await bot.send_message(chat_id=user_id, text=get_text(user_id, "queue_mp3_compressing"))
                loop = asyncio.get_running_loop()
                compressed_file = await loop.run_in_executor(PROCESS_EXECUTOR, compress_audio_file, file_path, TARGET_SIZE_MB)
                if not compressed_file or not os.path.exists(compressed_file):
                    await bot.send_message(chat_id=user_id, text=get_text(user_id, "queue_mp3_compress_failed"))
                    return
                if os.path.getsize(compressed_file) > MAX_FILE_SIZE:
                    await bot.send_message(chat_id=user_id, text=get_text(user_id, "queue_mp3_too_large"))
                    return
                send_path = compressed_file

            if media_format == "mp4" and file_size > MAX_FILE_SIZE:
                await bot.send_message(chat_id=user_id, text=get_text(user_id, "queue_mp4_too_large"))
                return

            quality_line = ""
            if quality:
                lang = get_text(user_id, "youtube_link")  # просто чтобы получить язык
                if "youtube" in lang.lower():
                    # EN
                    quality_line = f"\nquality: {quality}"
                else:
                    # RU
                    quality_line = f"\nкачество: {quality}"

            if media_format == "mp3":
                caption = (
                f'{channel} - {title}\n'
                f'<a href="{webpage_url}">{get_text(user_id, "youtube_link")}</a>\n'
                '@musicshithead_bot'
            )
                
                await send_audio_safely(
                    bot=bot,
                    chat_id=user_id,
                    file_path=send_path,
                    title=title,
                    performer=channel,
                    duration=duration,
                    caption=caption,
                )
            else:
                quality_text = ""
                if quality:
                    if str(get_text(user_id, "back_button")).lower().strip() == "go back":
                        quality_text = f"\nquality: {quality}"
                    else:
                        quality_text = f"\nкачество: {quality}"

                caption = (
                    f"{channel} - {title}"
                    f"{quality_text}\n"
                    f'<a href="{webpage_url}">{get_text(user_id, "youtube_link")}</a>\n'
                    "@musicshithead_bot"
                )
                await send_video_safely(
                    bot=bot,
                    chat_id=user_id,
                    file_path=send_path,
                    duration=duration,
                    caption=caption,
                )

            try:
                if progress_message:
                    await progress_message.delete()
            except Exception:
                pass
            await send_done_with_back(bot, user_id, user_id)
        except Exception as e:
            logger.error("YouTube processing error for user %s: %s", user_id, e, exc_info=True)
            await bot.send_message(chat_id=user_id, text=get_text(user_id, "youtube_download_error"))
        finally:
            cleanup_files("", compressed_file)
            cleanup_temp_dir(work_dir)


youtube_queue = YouTubeDownloadQueue(workers_count=1)
