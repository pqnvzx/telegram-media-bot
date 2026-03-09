import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import (
    user_languages,
    user_states,
    SOUNDCLOUD_CLIENT_ID,
    SOUNDCLOUD_CLIENT_SECRET,
)
from texts import get_text, TEXTS
from soundcloud_api import get_oauth_token, search_soundcloud
from keyboards import (
    create_track_keyboard,
    get_main_menu_keyboard,
    get_back_button,
    get_language_keyboard,
    get_video_format_keyboard,
)
from utils import handle_track_download, youtube_queue
from downloader import get_youtube_video_info, normalize_media_url

logger = logging.getLogger(__name__)


def is_youtube_url(url: str) -> bool:
    if not url:
        return False

    url = url.lower().strip()
    youtube_domains = [
        "youtube.com/watch",
        "www.youtube.com/watch",
        "m.youtube.com/watch",
        "youtu.be/",
        "www.youtu.be/",
        "youtube.com/shorts/",
        "www.youtube.com/shorts/",
    ]
    return any(domain in url for domain in youtube_domains)


async def safe_delete_message(message):
    if not message:
        return
    try:
        await message.delete()
    except Exception as e:
        logger.warning(f"Could not delete message: {e}")


async def send_main_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    await context.bot.send_message(
        chat_id=chat_id,
        text=get_text(user_id, "language_selected"),
        reply_markup=get_main_menu_keyboard(user_id),
        parse_mode="Markdown",
    )


async def send_action_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int, action: str, prompt_text: str):
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{get_text(user_id, action)}\n\n{prompt_text}",
        reply_markup=get_back_button(user_id),
        parse_mode="Markdown",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_states[user_id] = "language_selection"

    await update.message.reply_text(
        TEXTS["ru"]["welcome"],
        reply_markup=get_language_keyboard(),
        parse_mode="Markdown",
    )


async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    lang = query.data.split("_")[1]

    user_languages[user_id] = lang
    user_states[user_id] = "main"

    await safe_delete_message(query.message)
    await send_main_menu(chat_id, context, user_id)


async def handle_music_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, message_text: str):
    token = await get_oauth_token(SOUNDCLOUD_CLIENT_ID, SOUNDCLOUD_CLIENT_SECRET)
    if not token:
        await update.message.reply_text(
            get_text(user_id, "error", error="Failed to get OAuth token")
        )
        return

    if " - " in message_text:
        artist, title = message_text.split(" - ", 1)
        artist = artist.strip()
        title = title.strip()
    else:
        artist = message_text.strip()
        title = ""

    searching_msg = await update.message.reply_text(get_text(user_id, "searching"))

    try:
        if title:
            await searching_msg.edit_text(
                get_text(user_id, "search_song", artist=artist, title=title)
            )
            tracks = await search_soundcloud(f"{artist} {title}", token)
        else:
            await searching_msg.edit_text(
                get_text(user_id, "search_artist", artist=artist)
            )
            tracks = await search_soundcloud(artist, token)

        if not tracks:
            await searching_msg.edit_text(get_text(user_id, "no_results"))
            return

        context.user_data["search_results"] = tracks
        context.user_data["current_page"] = 0

        keyboard, total_pages = await create_track_keyboard(tracks, 0, user_id=user_id)

        await searching_msg.edit_text(
            text=get_text(
                user_id,
                "tracks_found",
                total=len(tracks),
                current=1,
                total_pages=total_pages,
            ),
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"Error in handle_music_message: {e}", exc_info=True)
        await searching_msg.edit_text(
            get_text(user_id, "error", error=str(e))
        )


async def handle_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, message_text: str):
    if not is_youtube_url(message_text):
        await update.message.reply_text(get_text(user_id, "invalid_youtube_link"))
        return

    youtube_url = normalize_media_url(message_text.strip())
    context.user_data["youtube_url"] = youtube_url

    try:
        video_info = await get_youtube_video_info(youtube_url)
        channel_name = video_info["channel"]
        video_title = video_info["title"]

        await update.message.reply_text(
            f"{channel_name} - {video_title}\n\n{get_text(user_id, 'video_format_prompt')}",
            reply_markup=get_video_format_keyboard(),
        )

    except Exception as e:
        logger.error(f"Error getting YouTube video info: {e}", exc_info=True)
        await update.message.reply_text(
            f"{get_text(user_id, 'video_info_error')}\n\n{get_text(user_id, 'video_format_prompt')}",
            reply_markup=get_video_format_keyboard(),
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not update.message or not update.message.text:
        return

    message_text = update.message.text.strip()

    if user_id not in user_languages:
        await start(update, context)
        return

    current_state = user_states.get(user_id, "main")

    if current_state == "music":
        await handle_music_message(update, context, user_id, message_text)
        return

    if current_state == "video":
        await handle_video_message(update, context, user_id, message_text)
        return

    if current_state in ("playlist", "social", "lyrics"):
        await update.message.reply_text(get_text(user_id, "in_development"))
        return

    if current_state == "main":
        await update.message.reply_text(
            get_text(user_id, "language_selected"),
            reply_markup=get_main_menu_keyboard(user_id),
            parse_mode="Markdown",
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data

    try:
        if data.startswith("track_"):
            if user_states.get(user_id) != "music":
                return

            track_idx = int(data.split("_")[1])
            tracks = context.user_data.get("search_results", [])

            if 0 <= track_idx < len(tracks):
                track = tracks[track_idx]
                await safe_delete_message(query.message)
                await handle_track_download(track, user_id, context)
            return

        if data.startswith("page_"):
            if user_states.get(user_id) != "music":
                return

            page = int(data.split("_")[1])
            tracks = context.user_data.get("search_results", [])

            if tracks:
                context.user_data["current_page"] = page
                keyboard, total_pages = await create_track_keyboard(tracks, page, user_id=user_id)
                await query.edit_message_text(
                    text=get_text(
                        user_id,
                        "tracks_found",
                        total=len(tracks),
                        current=page + 1,
                        total_pages=total_pages,
                    ),
                    reply_markup=keyboard,
                )
            return

        if data.startswith("lang_"):
            await language_callback(update, context)
            return

        if data.startswith("action_"):
            action = data.split("_", 1)[1]

            if action == "music":
                user_states[user_id] = "music"
                prompt_text = get_text(user_id, "music_prompt")
            elif action == "playlist":
                user_states[user_id] = "playlist"
                prompt_text = get_text(user_id, "playlist_prompt")
            elif action == "video":
                user_states[user_id] = "video"
                prompt_text = get_text(user_id, "video_prompt")
            elif action == "social":
                user_states[user_id] = "social"
                prompt_text = get_text(user_id, "social_prompt")
            elif action == "lyrics":
                user_states[user_id] = "lyrics"
                prompt_text = get_text(user_id, "lyrics_prompt")
            else:
                user_states[user_id] = "main"
                prompt_text = get_text(user_id, "in_development")

            await safe_delete_message(query.message)
            await send_action_menu(chat_id, context, user_id, action, prompt_text)
            return

        if data == "video_mp3":
            if user_states.get(user_id) != "video":
                return

            youtube_url = context.user_data.get("youtube_url")
            if not youtube_url:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=get_text(user_id, "invalid_youtube_link"),
                )
                return

            await safe_delete_message(query.message)

            position = await youtube_queue.enqueue(
                bot=context.bot,
                user_id=user_id,
                url=youtube_url,
                media_format="mp3",
            )

            await context.bot.send_message(
                chat_id=user_id,
                text=get_text(user_id, "queue_added", position=position),
            )
            return

        if data == "video_mp4":
            if user_states.get(user_id) != "video":
                return

            youtube_url = context.user_data.get("youtube_url")
            if not youtube_url:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=get_text(user_id, "invalid_youtube_link"),
                )
                return

            await safe_delete_message(query.message)

            position = await youtube_queue.enqueue(
                bot=context.bot,
                user_id=user_id,
                url=youtube_url,
                media_format="mp4",
            )

            await context.bot.send_message(
                chat_id=user_id,
                text=get_text(user_id, "queue_added", position=position),
            )
            return

        if data == "back_to_main":
            user_states[user_id] = "main"
            await safe_delete_message(query.message)
            await send_main_menu(chat_id, context, user_id)
            return

    except Exception as e:
        logger.error(f"Error in button_callback: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=user_id,
            text=get_text(user_id, "error", error="Internal error"),
        )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)

    if update and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Произошла внутренняя ошибка. Попробуйте позже.",
        )