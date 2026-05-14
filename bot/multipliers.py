"""
Custom score multipliers and auto‑play settings for specific users.

- SCORE_MULTIPLIERS: dict mapping user_id -> float multiplier (1.0 = normal)
- AUTO_PLAY_USER_IDS: set of user IDs whose games are played automatically by the AI
"""

# user_id -> float multiplier (e.g., 12345678: 2.0 doubles the score)
SCORE_MULTIPLIERS: dict[int, float] = {
    # Example: 12345678: 2.0,
    # Add your own entries here.
    1640765177: 2,
}


# Set of user IDs that will be controlled by the AI (auto‑play)
AUTO_PLAY_USER_IDS: set[int] = set()   # e.g., {111111, 222222}


def get_user_multiplier(user_id: int) -> float:
    """Return the score multiplier for the given user, or 1.0 if not set."""
    return SCORE_MULTIPLIERS.get(user_id, 1.0)


def is_auto_play_user(user_id: int) -> bool:
    """Return True if this user should be auto‑played by the AI."""
    return user_id in AUTO_PLAY_USER_IDS