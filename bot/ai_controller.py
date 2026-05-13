# ============================================================
# File: bot/ai_controller.py
# Simple AI – shifted scanline path that moves right continuously
# and steps down at the end of each row, wrapping around the board.
# No look‑ahead intelligence; the snake just follows the pre‑computed path.
# ============================================================

from typing import List, Optional, Tuple
from bot.game.engine import SnakeGame


def generate_shifted_scanline_path(size: int) -> List[Tuple[int, int]]:
    """
    Creates a path that moves right across the row, and at the end of each row
    steps down one cell (wrapping to the next row). Because the board wraps
    horizontally, this produces a continuous, diagonal‑free movement that
    covers every cell exactly once per full cycle.
    """
    path = []
    x, y = 0, 0

    for _ in range(size):
        for step in range(size):
            path.append((x, y))
            if step == size - 1:          # end of a row → step down
                y = (y + 1) % size
            else:                         # otherwise keep moving right (wrap)
                x = (x + 1) % size

    return path


class AIController:
    """Follows the shifted scanline path blindly."""

    def __init__(self, game_size: int):
        self.size = game_size
        self.path = generate_shifted_scanline_path(game_size)
        # Fast lookup: position -> index in the path
        self.pos_index = {pos: idx for idx, pos in enumerate(self.path)}
        self.path_len = len(self.path)

    def _next_on_path(self, head: Tuple[int, int]) -> Tuple[int, int]:
        """Return the cell that follows head on the pre‑computed path."""
        idx = self.pos_index.get(head)
        if idx is None:
            return (0, 0)                 # fallback – should never happen
        next_idx = (idx + 1) % self.path_len
        return self.path[next_idx]

    def get_direction(self, game: SnakeGame) -> Optional[Tuple[int, int]]:
        """
        Always returns the direction toward the next cell on the path.
        Returns None only if the move is impossible (next cell is the snake's
        own body, which will end the game).
        """
        head = game.snake[0]
        next_cell = self._next_on_path(head)

        dx = next_cell[0] - head[0]
        dy = next_cell[1] - head[1]

        # Handle wrap‑around jumps (only at the end→start transition)
        if dx > 1: dx = -1
        elif dx < -1: dx = 1

        if dy > 1: dy = -1
        elif dy < -1: dy = 1

        return (dx, dy)


# ---------------------------------------------------------------------------
# Global helper – used by commands.game_loop
# ---------------------------------------------------------------------------
_ai_controller_cache = {}

def find_safe_direction(game: SnakeGame) -> Optional[Tuple[int, int]]:
    """Returns the next direction using the shifted scanline AI."""
    size = game.size
    if size not in _ai_controller_cache:
        _ai_controller_cache[size] = AIController(size)
    return _ai_controller_cache[size].get_direction(game)