import asyncio
import logging
import time
from datetime import timezone
from typing import Optional

from aiogram import Bot, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from bot.database.db import get_session
from bot.database.models import DailyWinner, Score
from bot.game.engine import SnakeGame
from bot.game.renderer import render_board
from bot.utils import get_title
from bot.multipliers import is_auto_play_user
from bot.ai_controller import find_safe_direction

router = Router()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state: key format is "chat_id:user_id"
# ---------------------------------------------------------------------------
active_games: dict[str, SnakeGame] = {}
active_tasks: dict[str, asyncio.Task] = {}

# Inline direction-control keyboard (left/right swapped)
controls_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⬆️", callback_data="u")],
    [InlineKeyboardButton(text="➡️", callback_data="r"),
     InlineKeyboardButton(text="⬅️", callback_data="l")],
    [InlineKeyboardButton(text="⬇️", callback_data="d")],
])

# ---------------------------------------------------------------------------
# Rate limiting dictionaries (global, must be cleaned periodically)
# ---------------------------------------------------------------------------
user_request_times: dict[str, list[float]] = {}     # general throttle for all commands
start_request_times: dict[str, list[float]] = {}    # dedicated counter for /start (snake bite)
start_cooldowns: dict[str, float] = {}              # /start cooldown end timestamps

MAX_REQUESTS = 6
THROTTLE_WINDOW = 30.0
START_LIMIT = 3
START_WINDOW = 30.0
SNAKE_BITE_DURATION = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Periodic cleanup of rate limiting dictionaries to prevent memory leaks
# ---------------------------------------------------------------------------
async def cleanup_dictionaries():
    """Cleans up expired entries from all rate-limiting dicts every hour."""
    while True:
        await asyncio.sleep(3600)  # Run every hour
        now = time.monotonic()

        # Clean up start_cooldowns
        expired_cooldowns = [k for k, v in start_cooldowns.items() if v < now]
        for k in expired_cooldowns:
            del start_cooldowns[k]

        # Clean up general throttle dict
        inactive_throttle = [k for k, v in user_request_times.items()
                             if not v or (now - v[-1]) > THROTTLE_WINDOW]
        for k in inactive_throttle:
            del user_request_times[k]

        # Clean up start-request tracking dict
        inactive_start = [k for k, v in start_request_times.items()
                          if not v or (now - v[-1]) > START_WINDOW]
        for k in inactive_start:
            del start_request_times[k]


# ---------------------------------------------------------------------------
# Internal throttling helper (general spam protection, no message sent)
# ---------------------------------------------------------------------------
async def _throttle_check(chat_id: int, user_id: int) -> bool:
    """
    Returns True if the request should be blocked (i.e., user is throttled).
    Updates the general user_request_times dict.
    """
    key = f"{chat_id}:{user_id}"
    now = time.monotonic()

    if key not in user_request_times:
        user_request_times[key] = [now]
        return False

    # Remove timestamps older than the window
    user_request_times[key] = [
        t for t in user_request_times[key] if now - t <= THROTTLE_WINDOW
    ]
    user_request_times[key].append(now)

    if len(user_request_times[key]) > MAX_REQUESTS:
        # Trim list to avoid unbounded growth between cleanups
        user_request_times[key] = user_request_times[key][-MAX_REQUESTS:]
        return True

    return False


# ---------------------------------------------------------------------------
# Game loop
# ---------------------------------------------------------------------------
INACTIVITY_TIMEOUT = 30.0

async def game_loop(
    bot: Bot,
    chat_id: int,
    user_id: int,
    message_id: int,
    username: Optional[str],
    display_name: str,
) -> None:
    """
    Asynchronous game loop that runs in a dedicated asyncio.Task.
    Uses a fast refresh cycle (0.15 s) to make controls feel responsive,
    while the snake movement follows the engine's current_delay.

    If the user is flagged for auto‑play, the AI controller provides
    direction input automatically every tick until the game ends.

    Inactivity timeout: if the user does not press any direction button for
    INACTIVITY_TIMEOUT seconds, the game ends automatically.

    Cancellation handling:
        CancelledError is allowed to propagate naturally; asyncio.shield()
        in finally protects the final score save and game‑over message.
    """
    key = f"{chat_id}:{user_id}"
    game = active_games.get(key)
    if not game:
        return

    last_step = 0.0
    refresh_interval = 0.15   # Bale does not restrict edit rate
    previous_title = get_title(game.score)
    promotion_text = ""

    # Check if this user should be auto‑played
    auto_play = is_auto_play_user(user_id)

    try:
        while not game.game_over:
            now = time.monotonic()

            # ---------- Inactivity check ----------
            if game.is_timed_out():
                game.game_over = True
                break

            # ---------- Auto‑play direction (if applicable) ----------
            if auto_play:
                direction = find_safe_direction(game)
                if direction:
                    game.change_direction(*direction)
                # If no safe direction is returned, the snake will hit itself
                # and game.game_over will become True in the next step.

            # Only step if enough time has passed for the snake's current speed
            if now - last_step >= game.current_delay:
                game.step()
                last_step = now

            # ---------- Title promotion check ----------
            current_title = get_title(game.score)
            if current_title != previous_title:
                promotion_text = f"✨ تبریک! حالا تو **{current_title}** هستی! ✨\n"
                previous_title = current_title

            # ---------- Build output ----------
            # Get raw rendered board from engine (without title)
            rendered_raw = game.render()
            # Inject title into status line
            lines = rendered_raw.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("🏆"):
                    lines[i] = f"🏅 {current_title}\n{line}"
                    break
            # Combine with promotion text (if any)
            final_text = promotion_text + "\n".join(lines)

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=final_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=controls_kb,
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in e.message.lower():
                    logger.warning(f"Edit message failed for {key}: {e}")
            except Exception as e:
                logger.error(f"Unexpected edit error for {key}: {e}")

            promotion_text = ""  # Reset to show only once
            await asyncio.sleep(refresh_interval)

    finally:
        if game:
            try:
                await asyncio.shield(
                    _save_score(chat_id, user_id, username, display_name, game.score)
                )
            except (asyncio.CancelledError, Exception) as e:
                logger.error(f"Failed to save score for {key}: {e}")

            # ---------- Build final game‑over message ----------
            death_pos = game.death_pos

            # Render final board with death marker if applicable
            if game.game_over and death_pos:
                final_board = render_board(
                    game.size, game.snake, game.items,
                    game.score, game.fps,
                    death_pos=death_pos,
                )
            else:
                final_board = game.render()

            mention = f"[{display_name}](uid:{user_id})"
            final_title = get_title(game.score)

            # Determine cause of death
            cause = ""
            if game.is_timed_out():
                cause = "\n🕒 علت: ۳۰ ثانیه هیچ حرکتی نکردی!"
            elif death_pos:
                cause = f"\n💀 مار به خودش خورد در خانهٔ {death_pos}!"
            else:
                cause = "\n⚡ بازی به پایان رسید."

            final_text = (
                f"{final_board}\n\n"
                f"💀 *بازی تمام شد!*\n"
                f"بازیکن: {mention}\n"
                f"🏅 لقب نهایی: *{final_title}*\n"
                f"🏆 امتیاز نهایی: *{game.score}*{cause}\n"
                f"برای شروع دوباره /start را بزنید."
            )
            try:
                await asyncio.shield(
                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=final_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=None,
                    )
                )
            except (asyncio.CancelledError, Exception) as e:
                if not (isinstance(e, TelegramBadRequest) and "bot was blocked" in str(e).lower()):
                    logger.warning(f"Failed to send game-over message for {key}: {e}")

        # --- Race‑condition‑safe cleanup ---
        current_task = asyncio.current_task()
        if active_tasks.get(key) is current_task:
            active_tasks.pop(key, None)
        if active_games.get(key) is game:
            active_games.pop(key, None)


async def _save_score(
    chat_id: int,
    user_id: int,
    username: Optional[str],
    display_name: str,
    score: int,
) -> None:
    """Persist a game score to PostgreSQL using the safe context manager."""
    try:
        async with get_session() as session:
            session.add(Score(
                chat_id=chat_id,
                user_id=user_id,
                username=username,
                display_name=display_name,
                score=score,
            ))
    except Exception as e:
        logger.error(f"DB save_score error: {e}")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot) -> None:
    """Starts or restarts a Snake game session for the calling user."""
    if message.from_user is None:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    username: Optional[str] = message.from_user.username
    # Build display name from first_name and last_name
    first = message.from_user.first_name or ""
    last = message.from_user.last_name or ""
    display_name = f"{first} {last}".strip() or "کاربر"

    key = f"{chat_id}:{user_id}"

    # --- Snake bite cooldown for /start ---
    now = time.monotonic()
    if key in start_cooldowns:
        remaining = start_cooldowns[key] - now
        if remaining > 0:
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            await message.answer(
                f"🐍 *اومدیم نیشت زدیم!*\n"
                f"مار گزیده شدی و باید {minutes} دقیقه و {seconds} ثانیه صبر کنی تا زهرش پاک بشه.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        else:
            # Cooldown expired, remove it
            del start_cooldowns[key]

    # --- Track /start requests using a dedicated dictionary (snake bite rule) ---
    if key not in start_request_times:
        start_request_times[key] = []
    # Remove old timestamps (>START_WINDOW)
    start_request_times[key] = [t for t in start_request_times[key] if now - t <= START_WINDOW]

    if len(start_request_times[key]) >= START_LIMIT:
        # Snake bite! Set cooldown and clean list
        start_cooldowns[key] = now + SNAKE_BITE_DURATION
        await message.answer(
            "🐍 *اومدیم نیشت زدیم!*\n"
            "زیادی استارت زدی! مار گزیده شدی، ۱۰ دقیقه استراحت کن تا حالت خوب بشه.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Record this /start request
    start_request_times[key].append(now)

    # --- General throttle check (for overall spam protection) ---
    if await _throttle_check(chat_id, user_id):
        await message.answer(
            "🐢 هوووی! چقدر عجله داری؟ یه نفس بکش، چند لحظه دیگه دوباره تلاش کن."
        )
        return

    # Cancel any running game task for this user and wait for its cleanup
    existing_task = active_tasks.get(key)
    if existing_task and not existing_task.done():
        existing_task.cancel()
        try:
            await existing_task
        except asyncio.CancelledError:
            pass

    game = SnakeGame(chat_id, user_id)
    try:
        msg = await message.answer(
            game.render(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=controls_kb,
        )
    except Exception as e:
        logger.error(f"Failed to send initial board for {key}: {e}")
        await message.answer("❌ شروع بازی با خطا مواجه شد. لطفا دوباره تلاش کنید.")
        return

    # Atomic replacement: set the new task and game instance AFTER message is sent
    active_games[key] = game
    task = asyncio.create_task(
        game_loop(bot, chat_id, user_id, msg.message_id, username, display_name)
    )
    active_tasks[key] = task


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Sends a Persian help message explaining the game rules and commands."""
    if message.from_user is None:
        return

    help_text = (
        "🐍 *به قلمروی مارها خوش اومدی!*\n\n"
        "🎮 *چطوری بازی کنیم؟*\n"
        "با دکمه‌های جهتی که زیر صفحه هست، مار رو هدایت کن.\n"
        "هر چی بیشتر بخوری، بزرگ‌تر و سریع‌تر می‌شی!\n"
        "اما حواست باشه – برخورد با خودت بازی رو تموم می‌کنه.\n"
        "خبر خوب: دیوار تموم شد! مار از هر طرف بره بیرون، از طرف مقابل میاد تو.\n\n"
        "🍎 سیب: ۱ امتیاز\n"
        "🟡 سیب طلایی: ۳ امتیاز (فقط ۷ ثانیه مونده، عجله کن!)\n"
        "🌀 حلزون جادویی: سرعت رو ۵ ثانیه کم می‌کنه.\n\n"
        "⏱️ *اتمام خودکار:* اگر ۳۰ ثانیه هیچ دکمه‌ای نزنی، بازی تموم می‌شه.\n\n"
        "🔒 *کنترل:* فقط کسی که بازی رو شروع کرده می‌تونه دکمه‌ها رو بزنه.\n\n"
        "📊 *جدول افتخارات:*\n"
        "/leaderboard – ۱۰ شکارچی برتر این گروه رو نشون می‌ده.\n"
        "برنده روزانه (🥇) هر نیمه‌شب اعلام می‌شه.\n\n"
        "🏅 *لقب‌ها:* با هر امتیاز یه لقب باحال می‌گیری.\n"
        "ببین می‌تونی به «🐉 تایتان ابدی» برسی؟\n\n"
        "⚙️ *دستورات:*\n"
        "/start – شروع ماجراجویی جدید\n"
        "/leaderboard – جدول شکارچیان\n"
        "/help – همین راهنما"
    )

    await message.answer(help_text, parse_mode=ParseMode.MARKDOWN)


@router.message(Command("leaderboard"))
async def cmd_leaderboard(message: Message) -> None:
    """Displays the top-10 leaderboard for the current chat with titles."""
    if message.from_user is None:
        return

    chat_id = message.chat.id
    try:
        async with get_session() as session:
            # Fetch top 10 personal bests for this chat
            stmt = (
                select(
                    Score.user_id,
                    Score.username,
                    Score.display_name,
                    func.max(Score.score).label("max_score"),
                )
                .where(Score.chat_id == chat_id)
                .group_by(Score.user_id, Score.username, Score.display_name)
                .order_by(func.max(Score.score).desc())
                .limit(10)
            )
            result = await session.execute(stmt)
            rows = result.all()

            # Get today's date in UTC for daily winner badge
            today = message.date.astimezone(timezone.utc).strftime("%Y-%m-%d")

            # Fetch daily winner for today
            dw_stmt = select(DailyWinner.user_id).where(
                DailyWinner.chat_id == chat_id,
                DailyWinner.win_date == today,
            )
            dw_result = await session.execute(dw_stmt)
            daily_winner_id = dw_result.scalar_one_or_none()

        if not rows:
            await message.answer("📭 هنوز هیچ شکارچی‌ای تو این گروه امتیازی ثبت نکرده.")
            return

        lines = ["🏆 *جدول شکارچیان برتر* 🏆\n"]
        for i, (uid, uname, dname, score) in enumerate(rows, 1):
            medal = "🥇 " if uid == daily_winner_id else ""
            display = dname or uname or "کاربر"
            title = get_title(score)
            mention = f"[{display}](uid:{uid})"
            lines.append(f"{i}. {medal}{mention} — {score} امتیاز | {title}")

        await message.answer("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Leaderboard error for chat {chat_id}: {e}")
        await message.answer("❌ خطا در بارگذاری جدول امتیازات.")