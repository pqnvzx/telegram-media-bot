import logging
from telegram.ext import Application
from telegram import BotCommand

logger = logging.getLogger(__name__)


async def set_commands(application: Application):
    commands = [
        BotCommand("start", "запустить бота / start bot"),
    ]
    await application.bot.set_my_commands(commands)