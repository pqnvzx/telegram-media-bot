import logging

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import BOT_TOKEN, TELEGRAM_LOCAL_API_URL, TELEGRAM_LOCAL_MODE
from handlers import start, handle_message, button_callback, error_handler
from utils import check_ffmpeg, check_dependencies, youtube_queue
from commands import set_commands

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def post_init(application: Application):
    await set_commands(application)
    await youtube_queue.start()
    logger.info("Bot commands set successfully")
    logger.info("YouTube queue started successfully")


def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN" or not BOT_TOKEN:
        logger.error("Bot token is not set!")
        return

    if not check_ffmpeg():
        logger.error("❌ ffmpeg is not installed! Please install ffmpeg first.")
        return

    if not check_dependencies():
        return

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(60)
        .read_timeout(600)
        .write_timeout(600)
        .media_write_timeout(1800)
        .pool_timeout(60)
        .base_url(f"{TELEGRAM_LOCAL_API_URL}/bot")
        .base_file_url(f"{TELEGRAM_LOCAL_API_URL}/file/bot")
        .local_mode(False)
        .post_init(post_init)
    )

    application = builder.build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Bot started successfully!")
    application.run_polling()


if __name__ == "__main__":
    main()