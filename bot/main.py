import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import MessageEntityType          # Correct import
from aiogram.types import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import settings
from bot.database.db import init_db
from bot.handlers import callbacks, commands
from bot.scheduler.daily_winner import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Throttling Middleware (only for command messages)
# ---------------------------------------------------------------------------
MAX_GENERAL_REQUESTS = 6
THROTTLE_WINDOW = 30.0

class ThrottleMiddleware(BaseMiddleware):
    """
    Rate-limiting middleware that counts **only command messages** (e.g., /start).
    Non-command text messages are completely ignored and passed through without
    any throttling or warning.

    If a user exceeds the allowed number of commands within the window,
    the request is discarded and a fun warning message is sent.
    """
    def __init__(self):
        self.user_requests: Dict[str, list[float]] = {}
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        # Only apply to messages that contain a bot command
        if event.message and event.message.from_user:
            # Check if the message has a bot command entity
            has_command = any(
                entity.type == MessageEntityType.BOT_COMMAND
                for entity in (event.message.entities or [])
            )
            if not has_command:
                # Not a command – pass through without throttling
                return await handler(event, data)

            chat_id = event.message.chat.id
            user_id = event.message.from_user.id
            key = f"{chat_id}:{user_id}"
            now = time.monotonic()

            if key not in self.user_requests:
                self.user_requests[key] = [now]
            else:
                # Remove timestamps older than the window
                self.user_requests[key] = [
                    t for t in self.user_requests[key] if now - t <= THROTTLE_WINDOW
                ]
                self.user_requests[key].append(now)

                if len(self.user_requests[key]) > MAX_GENERAL_REQUESTS:
                    # Keep the list from growing indefinitely
                    self.user_requests[key] = self.user_requests[key][-MAX_GENERAL_REQUESTS:]
                    # Send a fun throttling message and stop the event
                    bot: Bot = data["bot"]
                    await bot.send_message(
                        chat_id=chat_id,
                        text="🐢 ووی! انقدر تند نزن! یه نفس بکش، چند لحظه دیگه امتحان کن."
                    )
                    return  # Discard the update

        return await handler(event, data)

    async def cleanup(self):
        """Periodically remove inactive entries to prevent memory leaks."""
        while True:
            await asyncio.sleep(3600)  # every hour
            now = time.monotonic()
            inactive_keys = [k for k, v in self.user_requests.items()
                             if not v or (now - v[-1]) > THROTTLE_WINDOW]
            for k in inactive_keys:
                del self.user_requests[k]


async def main() -> None:
    """Application entry point."""
    logger.info("🔄 Initializing database...")
    await init_db()

    # Route all API calls to Bale's server instead of api.telegram.org
    bale_server = TelegramAPIServer(
        base=f"{settings.BALE_API_URL}/bot{{token}}/{{method}}",
        file=f"{settings.BALE_API_URL}/file/bot{{token}}/{{path}}",
    )
    session = AiohttpSession(api=bale_server)
    bot = Bot(token=settings.BOT_TOKEN, session=session)

    dp = Dispatcher()

    # Throttling middleware with periodic cleanup (only for commands)
    throttle_middleware = ThrottleMiddleware()
    dp.update.middleware(throttle_middleware)
    asyncio.create_task(throttle_middleware.cleanup())

    dp.include_router(commands.router)
    dp.include_router(callbacks.router)

    # Start periodic cleanup of command-level rate limiting dicts
    asyncio.create_task(commands.cleanup_dictionaries())

    scheduler = AsyncIOScheduler()
    # Pass the bot instance so the daily winner job can send messages to groups
    setup_scheduler(scheduler, bot)
    scheduler.start()
    logger.info("⚙️ APScheduler started.")

    logger.info("🚀 Starting Bale Snake Bot (long polling)...")
    try:
        await dp.start_polling(bot)
    finally:
        logger.info("🛑 Shutting down gracefully...")
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user.")