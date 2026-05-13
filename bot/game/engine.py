import random
import time
from typing import Dict, List, Optional, Tuple

from bot.game.renderer import (
    EMOJI_APPLE,
    EMOJI_GOLD,
    EMOJI_SLOW,
    render_board,
)
from bot.multipliers import get_user_multiplier


class SnakeGame:
    """
    Core game engine: state, movement, collision detection, items, and difficulty scaling.
    Each active user session runs one instance of this class inside an asyncio.Task.

    Features:
      - Board size 12 (was 10).
      - Walls wrap around (toroidal topology).
      - Base speed = 0.50 s/tick (2 FPS), gradually increases to 0.20 s (5 FPS).
      - Slow‑mo power‑up increases delay to 0.50 s.
      - Inactivity timeout: game ends if no direction change for 30 seconds.
      - Direction input uses a queue to handle rapid presses without losing moves.
      - Fair golden apple spawning: guaranteed after every 7 non‑gold items eaten,
        plus a 5% random chance for an early bonus.
      - Quick‑eat bonus: 3 items eaten within 2 seconds rewards a free golden apple.
      - Custom per‑user score multipliers (configurable in bot/multipliers.py).
      - Death position stored for rendering.
      - Item spawning uses set difference for guaranteed placement.
    """

    # Fairness constant for golden apple spawning
    GOLD_SPAWN_INTERVAL = 7

    # Quick‑eat reward window (seconds) and required item count
    QUICK_EAT_WINDOW = 2.0
    QUICK_EAT_COUNT = 3

    def __init__(self, chat_id: int, user_id: int, size: int = 12) -> None:
        self.chat_id = chat_id
        self.user_id = user_id
        self.size = size

        # Initial snake (center of the board) moving upward
        self.snake: List[Tuple[int, int]] = [
            (size // 2, size // 2),
            (size // 2, size // 2 + 1),
        ]
        self.dx, self.dy = 0, -1
        self.last_step_dx, self.last_step_dy = 0, -1  # Direction that was actually used in the last step

        self.score: int = 0
        self.game_over: bool = False
        self.base_delay: float = 0.50          # Original slow start (2 FPS)

        # Active items on the board: {(x, y): emoji_type}
        self.items: Dict[Tuple[int, int], str] = {}

        # Power-up expiration timestamps (Unix epoch seconds)
        self.gold_expire: float = 0.0
        self.slow_expire: float = 0.0

        # Inactivity timer (reset by every direction change)
        self.last_input_time: float = time.monotonic()

        # Direction input queue (prevents loss of rapid key presses)
        self.direction_queue: List[Tuple[int, int]] = []

        # Fairness counter: non‑gold items consumed since last golden apple
        self.items_since_gold: int = 0

        # Quick‑eat tracker: list of recent item consumption timestamps
        self.recent_eat_times: List[float] = []

        # Score multiplier for this user (from multipliers.py)
        self.score_multiplier: float = get_user_multiplier(user_id)

        # Death position (set when snake collides with itself)
        self.death_pos: Optional[Tuple[int, int]] = None

        self._spawn_item()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_delay(self) -> float:
        """Tick delay in seconds, accounting for score‑based difficulty and slow‑mo."""
        if time.time() < self.slow_expire:
            return 0.5                      # Slow‑mo active: ~2 FPS
        # Delay drops by 0.05 s per 5 points, floor at 0.20 s
        return max(0.20, self.base_delay - (self.score // 5) * 0.05)

    @property
    def fps(self) -> int:
        """Approximate frames per second shown in the status bar."""
        return round(1 / self.current_delay)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _spawn_golden_apple(self) -> None:
        """
        Force‑spawns a golden apple on a random empty cell.
        Resets the fairness counter because a golden apple is now available.
        """
        all_cells = {(x, y) for x in range(self.size) for y in range(self.size)}
        snake_cells = set(self.snake)
        empty_cells = list(all_cells - snake_cells)
        if not empty_cells:
            return

        pos = random.choice(empty_cells)
        self.items[pos] = EMOJI_GOLD
        self.gold_expire = time.time() + 7.0
        self.items_since_gold = 0

    def _check_quick_eat_bonus(self) -> None:
        """
        Awards a bonus golden apple if the player ate
        QUICK_EAT_COUNT items within QUICK_EAT_WINDOW seconds.
        Clears the recent eat times afterwards to prevent infinite bonuses.
        """
        if len(self.recent_eat_times) >= self.QUICK_EAT_COUNT:
            # Check if the oldest required eat is still within the window
            if time.monotonic() - self.recent_eat_times[-self.QUICK_EAT_COUNT] <= self.QUICK_EAT_WINDOW:
                self._spawn_golden_apple()
                # Reset tracker to avoid repeated rewards
                self.recent_eat_times.clear()

    def _spawn_item(self) -> None:
        """
        Spawns a new item on a random empty cell.
        Uses set difference of all grid cells minus snake body to guarantee
        an empty cell even when the snake fills most of the board.

        Fairness: if the snake has eaten enough non‑gold items (7 by default),
        a golden apple is guaranteed. Otherwise, a small 5% random bonus
        golden apple may still appear; otherwise it's a regular apple or a slow
        power‑up.
        """
        all_cells = {(x, y) for x in range(self.size) for y in range(self.size)}
        snake_cells = set(self.snake)
        empty_cells = list(all_cells - snake_cells)
        if not empty_cells:
            # Player has filled the entire board – would be a win condition
            return

        pos = random.choice(empty_cells)

        # Guaranteed golden apple if fairness threshold is reached
        if self.items_since_gold >= self.GOLD_SPAWN_INTERVAL:
            self.items[pos] = EMOJI_GOLD
            self.gold_expire = time.time() + 7.0
            self.items_since_gold = 0
            return

        # Random spawn (5% gold, 5% slow, 90% normal)
        rand = random.random()
        if rand < 0.05:                     # 5% chance for golden apple (bonus)
            self.items[pos] = EMOJI_GOLD
            self.gold_expire = time.time() + 7.0
            self.items_since_gold = 0       # reset counter because gold spawned
        elif rand < 0.10:                   # 5% chance for slow power-up
            self.items[pos] = EMOJI_SLOW
            self.items_since_gold += 1      # slow doesn't reset gold counter
        else:                               # 90% normal apple
            self.items[pos] = EMOJI_APPLE
            self.items_since_gold += 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def change_direction(self, ndx: int, ndy: int) -> None:
        """
        Enqueues a direction request. The actual direction change happens
        during the next step() call, respecting the input queue.
        This prevents rapid key presses from being lost.
        Resets the inactivity timer.
        """
        if len(self.direction_queue) < 3:   # Limit queue size to avoid memory overflow
            self.direction_queue.append((ndx, ndy))
            self.last_input_time = time.monotonic()

    def step(self) -> None:
        """Executes one game tick: processes input queue, moves the snake, checks collisions, handles items."""
        if self.game_over:
            return

        # Process direction queue (apply only one valid direction per tick)
        while self.direction_queue:
            ndx, ndy = self.direction_queue.pop(0)
            # Reject 180-degree reversal based on the last actual move direction
            if (ndx, ndy) != (-self.last_step_dx, -self.last_step_dy):
                self.dx, self.dy = ndx, ndy
                break  # consume only one valid direction

        # Record the direction that is actually used for movement this tick
        self.last_step_dx, self.last_step_dy = self.dx, self.dy

        # Remove expired golden apples and spawn replacements
        now = time.time()
        expired_gold = [
            pos for pos, item in self.items.items()
            if item == EMOJI_GOLD and now > self.gold_expire
        ]
        for pos in expired_gold:
            del self.items[pos]
            # Spawn replacement – does NOT reset items_since_gold because gold wasn't eaten
            self._spawn_item()

        # Compute new head position, wrapping around the board edges
        hx, hy = self.snake[0]
        new_head = (
            (hx + self.dx) % self.size,
            (hy + self.dy) % self.size,
        )

        # Self collision – store death position then end
        if new_head in self.snake:
            self.death_pos = new_head
            self.game_over = True
            return

        self.snake.insert(0, new_head)

        # Item consumption
        if new_head in self.items:
            item_type = self.items.pop(new_head)
            # Record eat time for quick‑eat bonus (before applying multiplier)
            self.recent_eat_times.append(time.monotonic())

            if item_type == EMOJI_APPLE:
                self.score += round(1 * self.score_multiplier)
                self.items_since_gold += 1
            elif item_type == EMOJI_GOLD:
                self.score += round(3 * self.score_multiplier)
                self.items_since_gold = 0      # reset fairness counter
            elif item_type == EMOJI_SLOW:
                self.slow_expire = time.time() + 5.0
                self.items_since_gold += 1     # slow still increments counter

            # Check if quick‑eat bonus should trigger
            self._check_quick_eat_bonus()

            # Spawn a replacement item
            self._spawn_item()
        else:
            self.snake.pop()  # Remove tail only when no item was eaten

    def is_timed_out(self) -> bool:
        """Returns True if the game should end due to inactivity."""
        return (time.monotonic() - self.last_input_time) > 30.0

    def render(self) -> str:
        """Returns the current board as a Markdown code block for Bale."""
        return render_board(self.size, self.snake, self.items, self.score, self.fps)