from typing import Dict, List, Optional, Tuple

# Emoji constants for board rendering
EMOJI_HEAD = "🟩"       # Green square – distinguishable from body
EMOJI_BODY = "🟢"
EMOJI_APPLE = "🍎"
EMOJI_GOLD = "🟡"
EMOJI_SLOW = "🌀"
EMOJI_BG = "⬛"
EMOJI_DEATH = "💀"      # Shown at the collision spot when snake dies


def render_board(
    size: int,
    snake: List[Tuple[int, int]],
    items: Dict[Tuple[int, int], str],
    score: int,
    fps: int,
    title: str = None,
    death_pos: Optional[Tuple[int, int]] = None,
) -> str:
    """
    Renders the game board as a Markdown code block so it can be sent
    with ParseMode.MARKDOWN (the only format officially documented by Bale).

    The board is wrapped in triple backticks to preserve monospace layout
    on clients that support it. The status line is placed outside the code
    block for readability.

    If title is provided, it will be shown above the score/fps line.
    If death_pos is provided, that cell is marked with 💀.
    """
    # Initialize grid with background tiles
    grid = [[EMOJI_BG for _ in range(size)] for _ in range(size)]

    # Place items on the grid
    for (x, y), item_type in items.items():
        if 0 <= x < size and 0 <= y < size:
            grid[y][x] = item_type

    # Render snake: head gets special emoji, body gets standard body emoji,
    # but death position overrides with skull.
    for i, (x, y) in enumerate(snake):
        if 0 <= x < size and 0 <= y < size:
            if death_pos and (x, y) == death_pos:
                grid[y][x] = EMOJI_DEATH
            elif i == 0:
                grid[y][x] = EMOJI_HEAD
            else:
                grid[y][x] = EMOJI_BODY

    # If death position is not on the snake (should not happen), draw it anyway
    if death_pos and death_pos not in snake:
        x, y = death_pos
        if 0 <= x < size and 0 <= y < size:
            grid[y][x] = EMOJI_DEATH

    board_str = "\n".join("".join(row) for row in grid)

    # Status line with corrected "delay" label
    delay = 1 / fps if fps > 0 else 0.0
    status = f"🏆 Score: {score} | ⏱️ Delay: {delay:.2f}s"

    if title:
        status = f"🏅 {title}\n{status}"

    # Return a Markdown code block + status line.
    return f"\n{board_str}\n\n{status}"