import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select, distinct
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.database.db import get_session
from bot.database.models import DailyWinner, Score

logger = logging.getLogger(__name__)


async def calculate_daily_winners() -> None:
    """
    APScheduler job: runs every day at 00:00 UTC.

    Finds the highest-scoring player per chat for the previous UTC day and
    stores them in DailyWinner. The display_name is taken from the score
    row that achieved the maximum points.
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
            # Use DISTINCT ON to get one row per (chat_id, user_id) with the highest score
            # This allows us to retrieve display_name for that exact score.
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

            # For each chat, find the single highest score
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

    except Exception as e:
        logger.error(f"❌ Failed to calculate daily winners: {e}")


def setup_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Registers the daily winner job on the provided AsyncIOScheduler instance."""
    scheduler.add_job(
        calculate_daily_winners,
        trigger="cron",
        hour=0,
        minute=0,
        timezone="UTC",
        id="daily_winner_job",
        replace_existing=True,
    )
    logger.info("⏰ APScheduler: daily winner job registered (00:00 UTC).")