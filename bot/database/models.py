from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, String, Integer, DateTime, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy 2.0 ORM models."""
    pass


class Score(Base):
    """
    Stores every game score per user per chat.
    Used for leaderboard ranking and daily winner calculations.

    display_name stores the user's first_name + last_name for friendly display,
    even when the username is not set.
    """
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)   # new field
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DailyWinner(Base):
    """
    Stores the daily winner for each chat.
    Uses a database-level UniqueConstraint to prevent duplicate winners
    per chat per day even under concurrent inserts.
    """
    __tablename__ = "daily_winners"
    __table_args__ = (
        UniqueConstraint("chat_id", "win_date", name="uq_daily_winner_chat_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)   # new field
    win_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    score: Mapped[int] = mapped_column(Integer, nullable=False)