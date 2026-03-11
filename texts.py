TEXTS = {
    'ru': {
        'welcome': "выбери язык / choose your language:",
        'language_selected': "язык: Русский\n\n"
                            "**я могу:**\n\n"
                            "1. скачивание музыки\n"
                            "2. скачивание плейлиста с soundcloud\n"
                            "3. скачивание видео с youtube\n"
                            "4. поиск соц сетей артиста\n"
                            "5. поиск текста песни\n\n"
                            "выбери действие:",
        'music': "музыка",
        'playlist': "плейлист",
        'video': "видео",
        'social': "соц сети",
        'lyrics': "текст",

        'searching': "поиск...",
        'search_artist': "ищем все треки {artist}",
        'search_song': "ищем {artist} - {title}",
        'no_results': "не нашел, попробуй что-то другое",

        'error': "ошибка: {error}",
        'download_error': "ошибка при скачивании",
        'in_development': "функция в разработке",

        'tracks_found': "найдено {total} треков. страница {current}/{total_pages}.",
        'back_to_search': "вернуться назад",

        'music_prompt': " **рассказываю:**\n\n"
                        "пока что бот работает только с soundcloud\n"
                        "для поиска всех песен артиста: отправьте имя исполнителя\n"
                        "для поиска конкретной песни: артист - трек\n\n"
                        "  **показываю:**\n\n"
                        "`kennedyxoxo` - все песни артиста\n"
                        "`kennedyxoxo - statues` - конкретная песня",

        'playlist_prompt': "функция скачивания плейлистов в разработке",
        'video_prompt': "отправь мне ссылку на youtube/shorts видео"
                        "если будет плохое качество, попробуй скачать видео заново",
        'social_prompt': "функция поиска соц сетей в разработке",
        'lyrics_prompt': "функция поиска текстов в разработке",

        'invalid_youtube_link': "отправь корректную ссылку на youtube видео",
        'video_format_prompt': "выбери формат для скачивания",
        'video_info_error': "не удалось получить информацию о видео",
        'youtube_download_error': "не удалось скачать видео с youtube",

        'queue_added': "задача добавлена в очередь\nпозиция: {position}",
        'queue_started': "начинаю скачивание {format}...",
        'queue_mp3_compressing': "mp3 файл слишком большой даже для локального сервера, сжимаю...",
        'queue_mp3_compress_failed': "не удалось сжать mp3 файл",
        'queue_mp3_too_large': "даже после сжатия mp3 файл слишком большой",
        'queue_mp4_too_large': "mp4 файл слишком большой даже для локального сервера",

        'downloading': "нелегально скачиваю {artist} - {title}",
        'compressing': "сжимаю файл... это может занять некоторое время",
        'compression_failed': "не удалось сжать файл",
        'genre_unknown': "неизвестный жанр",
        'soundcloud_link': "🔗soundcloud",
        'youtube_link': "🔗youtube",
        'progress_template': "скачиваю {format}\n{bar} {percent}%\nскорость: {speed}\nосталось: {eta}",
        'progress_processing': "обрабатываю {format}...",
    },

    'en': {
        'welcome': "welcome! choose your language:",
        'language_selected': "language set: English\n\n"
                             "**i can:**\n\n"
                             "1. download music\n"
                             "2. download soundcloud playlist\n"
                             "3. download youtube video\n"
                             "4. find artist's social network\n"
                             "5. find song lyrics\n\n"
                             "choose action:",
        'music': "music",
        'playlist': "playlist",
        'video': "video",
        'social': "social",
        'lyrics': "lyrics",

        'searching': "searching...",
        'search_artist': "searching for all songs by {artist}",
        'search_song': "searching for {artist} - {title}",
        'no_results': "couldn't find anything, try something else",

        'error': "error: {error}",
        'download_error': "download error",
        'in_development': "feature in development",

        'tracks_found': "found {total} tracks. page {current}/{total_pages}.",
        'back_to_search': "go back",

        'music_prompt': " **q&a:**\n\n"
                        "right now the bot works only with soundcloud\n"
                        "to find all songs by an artist: send artist name\n"
                        "to find a specific song: artist - track\n\n"
                        "  **example:**\n\n"
                        "`kennedyxoxo` - all songs by the artist\n"
                        "`kennedyxoxo - statues` - specific song",

        'playlist_prompt': "playlist download feature is in development",
        'video_prompt': "send me a youtube/shorts video link\n"
                        "if you're video low quality, try downloading it again, should be good",
        'social_prompt': "social media search feature is in development",
        'lyrics_prompt': "lyrics search feature is in development",

        'invalid_youtube_link': "send a valid youtube video link",
        'video_format_prompt': "choose download format",
        'video_info_error': "could not get video information",
        'youtube_download_error': "could not download youtube video",

        'queue_added': "task added to queue\nposition: {position}",
        'queue_started': "starting {format} download...",
        'queue_mp3_compressing': "mp3 is still too large even for local server, compressing...",
        'queue_mp3_compress_failed': "could not compress mp3 file",
        'queue_mp3_too_large': "even after compression the mp3 file is too large",
        'queue_mp4_too_large': "mp4 file is too large even for local server",

        'downloading': "illegally gaining access to {artist} - {title}",
        'compressing': "compressing file... this may take a while",
        'compression_failed': "couldn't compress the file",
        'genre_unknown': "unknown genre",
        'soundcloud_link': "🔗soundcloud",
        'youtube_link': "🔗youtube",
        'progress_template': "downloading {format}\n{bar} {percent}%\nspeed: {speed}\neta: {eta}",
        'progress_processing': "processing {format}...",
    }
}


def get_text(user_id: int, key: str, **kwargs) -> str:
    from config import user_languages
    lang = user_languages.get(user_id, 'en')
    text = TEXTS.get(lang, TEXTS['en']).get(key, TEXTS['en'].get(key, key))
    return text.format(**kwargs) if kwargs else text