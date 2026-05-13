"""
Utility functions used across the bot.
"""

# Title definitions (score thresholds and corresponding titles)
TITLES = [
    (144, "🐉 تایتان ابدی"),
    (130, "🌪 شاه‌مار باستانی"),
    (110, "💢 اژدهای زهرآگین"),
    (90,  "🔥 افعی آتشین"),
    (70,  "⚔ کبرای سلطنتی"),
    (50,  "💀 شکارچی بی‌رحم"),
    (30,  "🐍 مار تیزدندان"),
    (15,  "🥚 مار جوان"),
    (0,   "🍼 بچه‌مار"),
]


def get_title(score: int) -> str:
    """
    Returns the title corresponding to the given score.
    The first matching threshold (largest first) is used.
    """
    for min_score, title in TITLES:
        if score >= min_score:
            return title
    # Fallback (should not happen because last threshold is 0)
    return TITLES[-1][1]