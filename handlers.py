import logging
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    user_languages,
    user_states,
    SOUNDCLOUD_CLIENT_ID,
    SOUNDCLOUD_CLIENT_SECRET,
    LYRICS_STATE,
)
from texts import get_text, TEXTS
from soundcloud_api import get_oauth_token, search_soundcloud
from keyboards import (
    create_track_keyboard,
    get_main_menu_keyboard,
    get_back_button,
    get_language_keyboard,
    get_video_format_keyboard,
    get_retry_button,
)
from lyrics import get_lyrics, search_lyrics_candidates
from utils import handle_track_download, youtube_queue
from downloader import get_youtube_video_info, normalize_media_url

logger = logging.getLogger(__name__)

def parse_artist_title_query(message_text: str) -> tuple[str, str]:
    text = (message_text or "").strip()
    separators = [" - ", " — ", " – ", "-", "—", "–"]
    for sep in separators:
        if sep in text:
            left, right = text.split(sep, 1)
            artist = left.strip()
            title = right.strip()
            if artist and title:
                return artist, title
    return text, ""


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




def build_lyrics_candidates_keyboard(candidates, page: int, user_id: int, items_per_page: int = 10) -> InlineKeyboardMarkup:
    total = len(candidates)
    total_pages = max(1, (total + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, total)

    keyboard = []
    for idx in range(start_idx, end_idx):
        candidate = candidates[idx]
        display = f"{candidate.get('artist', '')} - {candidate.get('title', '')}".strip(' -')
        if len(display) > 55:
            display = display[:52] + '...'
        keyboard.append([InlineKeyboardButton(display, callback_data=f"lyrics_choice_{idx}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton('◀️', callback_data=f'lyrics_page_{page-1}'))
    nav_buttons.append(InlineKeyboardButton(f'{page + 1}/{total_pages}', callback_data='lyrics_page_current'))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton('▶️', callback_data=f'lyrics_page_{page+1}'))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton(get_text(user_id, "back_to_search"), callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)


def build_lyrics_done_keyboard(user_id: int, artist: str | None = None) -> InlineKeyboardMarkup:
    keyboard = []
    if artist:
        keyboard.append([InlineKeyboardButton(get_text(user_id, "lyrics_more_artist"), callback_data="lyrics_more_artist")])
    keyboard.append([InlineKeyboardButton(get_text(user_id, "back_to_search"), callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

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




async def send_retry_prompt(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    current_state = user_states.get(user_id, "main")

    if current_state == "music":
        await send_action_menu(chat_id, context, user_id, "music", get_text(user_id, "music_prompt"))
        return
    if current_state == "playlist":
        await send_action_menu(chat_id, context, user_id, "playlist", get_text(user_id, "playlist_prompt"))
        return
    if current_state == "video":
        await send_action_menu(chat_id, context, user_id, "video", get_text(user_id, "video_prompt"))
        return
    if current_state == "social":
        await send_action_menu(chat_id, context, user_id, "social", get_text(user_id, "social_prompt"))
        return
    if current_state == LYRICS_STATE:
        await send_action_menu(chat_id, context, user_id, "lyrics", get_text(user_id, "lyrics_prompt"))
        return

    await send_main_menu(chat_id, context, user_id)


async def send_error_message(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int, error_text: str):
    await context.bot.send_message(
        chat_id=chat_id,
        text=get_text(user_id, "error", error=error_text),
        reply_markup=get_retry_button(user_id),
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
        await send_error_message(update.effective_chat.id, context, user_id, "Failed to get OAuth token")
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
            get_text(user_id, "error", error=str(e)),
            reply_markup=get_retry_button(user_id),
        )


async def handle_lyrics_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, message_text: str):
    logger.info("handle_lyrics_message: user_id=%s message=%s", user_id, message_text)

    artist, title = parse_artist_title_query(message_text)
    free_query = (message_text or "").strip()

    if title:
        logger.info("  Detected artist - title format")
        logger.info("  Parsed: artist='%s' title='%s'", artist, title)

        candidates = await search_lyrics_candidates(artist, title, max_results=80)
        if candidates:
            logger.info("  ✓ Found %s candidates, showing selection", len(candidates))
            context.user_data["lyrics_candidates"] = candidates
            context.user_data["lyrics_candidates_page"] = 0
            context.user_data["lyrics_last_query"] = {"mode": "artist_title", "artist": artist, "title": title, "query": free_query}

            await update.message.reply_text(
                get_text(user_id, "lyrics_select_match", current=1, total_pages=max(1, (len(candidates) + 9) // 10)),
                reply_markup=build_lyrics_candidates_keyboard(candidates, 0, user_id),
            )
            return

        lyrics = await get_lyrics(artist, title)
        if not lyrics:
            await update.message.reply_text(get_text(user_id, "lyrics_not_found"), reply_markup=get_retry_button(user_id))
            return

        context.user_data["lyrics_last_artist"] = artist
        await send_lyrics_and_done(update.effective_chat.id, user_id, context, lyrics, artist=artist)
        return

    query = artist.strip()
    logger.info("  Detected free-query lyrics search: '%s'", query)

    candidates = await search_lyrics_candidates(query, "", max_results=100)
    if candidates:
        context.user_data["lyrics_candidates"] = candidates
        context.user_data["lyrics_candidates_page"] = 0
        context.user_data["lyrics_last_query"] = {"mode": "free", "artist": query, "title": "", "query": free_query}

        await update.message.reply_text(
            get_text(user_id, "lyrics_select_match", current=1, total_pages=max(1, (len(candidates) + 9) // 10)),
            reply_markup=build_lyrics_candidates_keyboard(candidates, 0, user_id),
        )
        return

    logger.warning("  ❌ No lyrics candidates found for query '%s'", query)
    await update.message.reply_text(get_text(user_id, "no_results"), reply_markup=get_retry_button(user_id))
    return


async def send_lyrics_and_done(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, lyrics: str, artist: str | None = None):
    max_len = 3900
    if not lyrics:
        await context.bot.send_message(chat_id=chat_id, text=get_text(user_id, "lyrics_not_found"))
        return

    if artist:
        context.user_data["lyrics_last_artist"] = artist

    final_lyrics = lyrics.rstrip() + "\n\n@musicshithead_bot"

    if len(final_lyrics) <= max_len:
        await context.bot.send_message(chat_id=chat_id, text=final_lyrics)
    else:
        remaining = final_lyrics
        while remaining:
            if len(remaining) <= max_len:
                chunk = remaining
                remaining = ""
            else:
                split_at = remaining.rfind("\n", 0, max_len)
                if split_at < max_len // 2:
                    split_at = max_len
                chunk = remaining[:split_at].rstrip()
                remaining = remaining[split_at:].lstrip("\n")
            await context.bot.send_message(chat_id=chat_id, text=chunk)

    reply_markup = build_lyrics_done_keyboard(user_id, artist=artist or context.user_data.get("lyrics_last_artist"))
    try:
        await context.bot.send_animation(
            chat_id=chat_id,
            animation="https://media.giphy.com/media/111ebonMs90YLu/giphy.gif",
            reply_markup=reply_markup,
        )
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id,
            text="👍",
            reply_markup=reply_markup,
        )


async def handle_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, message_text: str):
    if not is_youtube_url(message_text):
        await update.message.reply_text(get_text(user_id, "invalid_youtube_link"), reply_markup=get_retry_button(user_id))
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
            reply_markup=InlineKeyboardMarkup([*get_video_format_keyboard().inline_keyboard, *get_retry_button(user_id).inline_keyboard]),
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

    if current_state == LYRICS_STATE:
        await handle_lyrics_message(update, context, user_id, message_text)
        return

    if current_state in ("playlist", "social"):
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

        if data.startswith("lyrics_track_"):
            if user_states.get(user_id) != LYRICS_STATE:
                return

            track_idx = int(data.split("_")[2])
            tracks = context.user_data.get("lyrics_search_results", [])

            if 0 <= track_idx < len(tracks):
                track = tracks[track_idx]
                await safe_delete_message(query.message)

                artist = track.get("artist") or ""
                title = track.get("title") or ""
                context.user_data["lyrics_last_artist"] = artist
                lyrics = await get_lyrics(artist, title)
                if not lyrics:
                    await context.bot.send_message(chat_id=user_id, text=get_text(user_id, "lyrics_not_found"), reply_markup=get_retry_button(user_id))
                else:
                    await send_lyrics_and_done(update.effective_chat.id, user_id, context, lyrics, artist=artist)
            return


        if data == "lyrics_more_artist":
            if user_states.get(user_id) != LYRICS_STATE:
                return

            artist = (context.user_data.get("lyrics_last_artist") or "").strip()
            if not artist:
                await context.bot.send_message(chat_id=user_id, text=get_text(user_id, "lyrics_not_found"), reply_markup=get_retry_button(user_id))
                return

            candidates = await search_lyrics_candidates(artist, "", max_results=100)
            if not candidates:
                await context.bot.send_message(chat_id=user_id, text=get_text(user_id, "no_results"), reply_markup=get_retry_button(user_id))
                return

            context.user_data["lyrics_candidates"] = candidates
            context.user_data["lyrics_candidates_page"] = 0
            context.user_data["lyrics_last_query"] = {"mode": "artist_only", "artist": artist, "title": "", "query": artist}

            await context.bot.send_message(
                chat_id=user_id,
                text=get_text(user_id, "lyrics_select_match", current=1, total_pages=max(1, (len(candidates) + 9) // 10)),
                reply_markup=build_lyrics_candidates_keyboard(candidates, 0, user_id),
            )
            return

        if data == "lyrics_page_current":
            return

        if data.startswith("lyrics_page_"):
            if user_states.get(user_id) != LYRICS_STATE:
                return

            try:
                page = int(data.split("_")[2])
            except (IndexError, ValueError):
                return

            candidates = context.user_data.get("lyrics_candidates", [])
            if not candidates:
                return

            context.user_data["lyrics_candidates_page"] = page
            total_pages = max(1, (len(candidates) + 9) // 10)
            await query.edit_message_text(
                text=get_text(user_id, "lyrics_select_match", current=page + 1, total_pages=total_pages),
                reply_markup=build_lyrics_candidates_keyboard(candidates, page, user_id),
            )
            return

        if data.startswith("lyrics_choice_"):
            logger.info("lyrics_choice callback: user_id=%s data=%s", user_id, data)
            if user_states.get(user_id) != LYRICS_STATE:
                logger.warning("  User not in LYRICS_STATE, ignoring")
                return

            try:
                choice_idx = int(data.split("_")[2])
            except (IndexError, ValueError) as e:
                logger.warning("  Failed to parse choice index: %s", e)
                return

            candidates = context.user_data.get("lyrics_candidates", [])
            logger.debug("  choice_idx=%s total_candidates=%s", choice_idx, len(candidates))
            
            if 0 <= choice_idx < len(candidates):
                candidate = candidates[choice_idx]
                logger.info("  Selected candidate %s: %s - %s", choice_idx, candidate.get("artist"), candidate.get("title"))
                await safe_delete_message(query.message)

                artist = candidate.get("artist") or ""
                title = candidate.get("title") or ""
                logger.debug("  Fetching lyrics for: %s - %s", artist, title)
                context.user_data["lyrics_last_artist"] = artist
                lyrics = (candidate.get("lyrics") or "").strip() or await get_lyrics(artist, title)
                if not lyrics:
                    logger.warning("  ❌ No lyrics found for %s - %s", artist, title)
                    await context.bot.send_message(chat_id=user_id, text=get_text(user_id, "lyrics_not_found"), reply_markup=get_retry_button(user_id))
                else:
                    logger.info("  ✓ Sending lyrics for %s - %s", artist, title)
                    await send_lyrics_and_done(update.effective_chat.id, user_id, context, lyrics, artist=artist)
            else:
                logger.warning("  Invalid choice index %s for %s candidates", choice_idx, len(candidates))
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
                user_states[user_id] = LYRICS_STATE
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
                    reply_markup=get_retry_button(user_id),
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
                    reply_markup=get_retry_button(user_id),
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

        if data == "retry_state":
            await safe_delete_message(query.message)
            await send_retry_prompt(chat_id, context, user_id)
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
            reply_markup=get_retry_button(user_id),
        )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)

    if update and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Произошла внутренняя ошибка. Попробуйте позже.",
        )
