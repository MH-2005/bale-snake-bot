import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select, distinct
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.database.db import get_session
from bot.database.models import DailyWinner, Score

logger = logging.getLogger(__name__)


async def calculate_daily_winners(bot: Bot) -> None:
    """
    APScheduler job: runs every day at 00:00 UTC.

    Finds the highest-scoring player per chat for the previous UTC day,
    stores them in DailyWinner, and sends a congratulatory message to the
    corresponding group.
    """
    now_utc = datetime.now(tz=timezone.utc)
    yesterday_start = (now_utc - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    yesterday_end = yesterday_start + timedelta(days=1)
    yesterday_str = yesterday_start.strftime("%Y-%m-%d")

    logger.info(f"📊 Calculating daily winners for {yesterday_str} (UTC)...")

    try:
        async with get_session() as session:
            # Subquery: all scores from yesterday
            subq = (
                select(
                    Score.chat_id,
                    Score.user_id,
                    Score.username,
                    Score.display_name,
                    Score.score,
                )
                .where(
                    Score.created_at >= yesterday_start,
                    Score.created_at < yesterday_end,
                )
                .subquery()
            )

            # For each chat, pick the row with the highest score
            stmt_max_per_chat = (
                select(
                    subq.c.chat_id,
                    subq.c.user_id,
                    subq.c.username,
                    subq.c.display_name,
                    subq.c.score,
                )
                .distinct(subq.c.chat_id)  # PostgreSQL DISTINCT ON
                .order_by(subq.c.chat_id, subq.c.score.desc())
            )

            result = await session.execute(stmt_max_per_chat)
            winners = result.all()

            # Upsert winners — ON CONFLICT DO NOTHING is atomic
            for chat_id, user_id, username, display_name, score in winners:
                upsert_stmt = (
                    pg_insert(DailyWinner)
                    .values(
                        chat_id=chat_id,
                        user_id=user_id,
                        username=username,
                        display_name=display_name,
                        win_date=yesterday_str,
                        score=score,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_daily_winner_chat_date"
                    )
                )
                await session.execute(upsert_stmt)

            # Commit happens automatically at the end of the context manager
            logger.info(f"✅ Daily winners saved for {len(winners)} chat(s).")

            # Send a congratulatory message to each group
            for chat_id, user_id, username, display_name, score in winners:
                friendly_name = display_name or username or str(user_id)
                mention = f"[{friendly_name}](uid:{user_id})"
                text = (
                    f"🏆 *برندهٔ روزانهٔ بازی مار*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"👑 {mention}\n"
                    f"⭐ امتیاز: *{score}*\n"
                    f"تبریک می‌گیم! 🎉"
                )
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        parse_mode="Markdown",
                    )
                except (TelegramForbiddenError, TelegramBadRequest) as e:
                    logger.warning(f"Could not announce winner in chat {chat_id}: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error announcing in chat {chat_id}: {e}")

    except Exception as e:
        logger.error(f"❌ Failed to calculate daily winners: {e}")


def setup_scheduler(scheduler: AsyncIOScheduler, bot: Bot) -> None:
    """Registers the daily winner job on the provided AsyncIOScheduler instance,
    passing the bot instance so that the job can send group messages."""
    scheduler.add_job(
        calculate_daily_winners,
        trigger="cron",
        hour=0,
        minute=0,
        timezone="UTC",
        id="daily_winner_job",
        replace_existing=True,
        kwargs={"bot": bot},   # bot is passed to calculate_daily_winners
    )
    logger.info("⏰ APScheduler: daily winner job registered (00:00 UTC).")