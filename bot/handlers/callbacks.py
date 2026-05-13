from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.handlers.commands import active_games

router = Router()

# Maps callback_data to (dx, dy) direction vectors
DIR_MAP = {
    "u": (0, -1),
    "d": (0, 1),
    "l": (-1, 0),
    "r": (1, 0),
}


@router.callback_query(F.data.in_(DIR_MAP.keys()))
async def handle_direction(callback: CallbackQuery) -> None:
    """
    Handles inline keyboard direction presses.
    Updates the game direction in memory and answers the callback to clear
    the loading spinner on the Bale client.

    FIX: callback.message and callback.from_user are both guarded against None.
    callback.from_user is None for anonymous admins in group chats, which would
    cause AttributeError when accessing .id without this check.
    """
    # Guard against None message (e.g. inline keyboard on a deleted message)
    # AND None from_user (e.g. anonymous admin actions in group chats).
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return

    key = f"{callback.message.chat.id}:{callback.from_user.id}"
    game = active_games.get(key)

    if game and not game.game_over:
        dx, dy = DIR_MAP[callback.data]
        game.change_direction(dx, dy)

    # Always answer to prevent the client from showing a spinning indicator
    await callback.answer()