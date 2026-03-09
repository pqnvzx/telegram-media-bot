from typing import List, Dict, Tuple
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from texts import get_text


async def create_track_keyboard(
    tracks: List[Dict],
    page: int = 0,
    items_per_page: int = 8,
    user_id: int = None
) -> Tuple[InlineKeyboardMarkup, int]:
    total_pages = (len(tracks) + items_per_page - 1) // items_per_page
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, len(tracks))
    keyboard = []

    for i in range(start_idx, end_idx):
        track = tracks[i]
        display_name = f"{track['artist']} - {track['title']}"
        if len(display_name) > 45:
            display_name = display_name[:42] + "..."
        duration = f"{track['duration'] // 60:02}:{track['duration'] % 60:02}"
        button_text = f"{display_name} [{duration}]"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"track_{i}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    return InlineKeyboardMarkup(keyboard), total_pages


def get_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(get_text(user_id, 'music'), callback_data="action_music"),
            InlineKeyboardButton(get_text(user_id, 'playlist'), callback_data="action_playlist"),
            InlineKeyboardButton(get_text(user_id, 'video'), callback_data="action_video")
        ],
        [
            InlineKeyboardButton(get_text(user_id, 'social'), callback_data="action_social"),
            InlineKeyboardButton(get_text(user_id, 'lyrics'), callback_data="action_lyrics")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_back_button(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(get_text(user_id, 'back_to_search'), callback_data="back_to_main")
    ]])


def get_language_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_video_format_keyboard() -> InlineKeyboardMarkup:
    keyboard = [[
        InlineKeyboardButton("MP3", callback_data="video_mp3"),
        InlineKeyboardButton("MP4", callback_data="video_mp4"),
    ]]
    return InlineKeyboardMarkup(keyboard)