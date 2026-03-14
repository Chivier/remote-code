"""
Name Generator - zellij-style random two-word session names.

Generates human-friendly names like "bright-falcon" or "calm-river"
for sessions, making them easier to reference than UUIDs.
"""

import random
import re
from typing import Optional

# Adjectives - common, easy to type, distinct
ADJECTIVES = [
    "able", "aged", "ancient", "apt", "bare", "best", "big", "bold",
    "brave", "brief", "bright", "broad", "busy", "calm", "chief",
    "clean", "clear", "clever", "close", "cold", "cool", "crisp",
    "dark", "dear", "deep", "dim", "dry", "dull", "eager", "early",
    "easy", "even", "extra", "faint", "fair", "far", "fast", "fine",
    "firm", "first", "fit", "flat", "fond", "free", "fresh", "full",
    "glad", "gold", "good", "grand", "grave", "gray", "great", "green",
    "grim", "half", "handy", "happy", "hard", "harsh", "heavy", "high",
    "holy", "hot", "huge", "humble", "idle", "inner", "iron", "ivory",
    "just", "keen", "kind", "known", "large", "last", "late", "lean",
    "light", "live", "lone", "long", "lost", "loud", "low", "lucky",
    "mad", "main", "major", "mild", "mint", "moist", "neat", "new",
    "next", "nice", "noble", "odd", "old", "open", "outer", "pale",
    "past", "plain", "polar", "prime", "proud", "pure", "quick", "quiet",
    "rare", "raw", "real", "red", "rich", "right", "rigid", "ripe",
    "rocky", "rough", "round", "royal", "ruby", "rural", "rusty", "safe",
    "sandy", "sharp", "shiny", "short", "shy", "silly", "slim", "slow",
    "small", "smart", "smooth", "snowy", "soft", "solar", "solid", "sour",
    "spare", "stark", "steep", "still", "stony", "stout", "sunny", "super",
    "sure", "sweet", "swift", "tall", "tame", "tart", "thick", "thin",
    "tidy", "tight", "tiny", "total", "tough", "trim", "true", "twin",
    "upper", "used", "vast", "vivid", "warm", "wavy", "weak", "weary",
    "wet", "whole", "wide", "wild", "wise", "worn", "young", "zesty",
]

# Nouns - animals, nature, objects - easy to type, distinct
NOUNS = [
    "ant", "arch", "aspen", "badge", "bass", "bay", "beach", "beam",
    "bear", "bell", "birch", "bird", "blade", "blaze", "bloom", "bolt",
    "bone", "book", "brook", "brush", "cedar", "chain", "charm", "claw",
    "cliff", "cloud", "coal", "coast", "cobra", "coral", "core", "crane",
    "creek", "cross", "crow", "crown", "crypt", "curve", "dawn", "deer",
    "delta", "depth", "dock", "dove", "drift", "drum", "dusk", "dust",
    "eagle", "earth", "edge", "elm", "ember", "fable", "fawn", "field",
    "finch", "fire", "flame", "flash", "flint", "float", "flood", "floor",
    "forge", "fort", "fox", "frost", "gate", "gaze", "gem", "ghost",
    "glade", "glass", "globe", "goat", "grain", "grape", "grass", "grove",
    "gulf", "gust", "hawk", "heart", "heath", "hedge", "heron", "hill",
    "horn", "horse", "hound", "index", "inlet", "isle", "ivy", "jade",
    "jay", "jewel", "keep", "kelp", "kite", "knoll", "lake", "lark",
    "latch", "leaf", "light", "lily", "lime", "lion", "lodge", "lynx",
    "maple", "marsh", "mesa", "mill", "mint", "mist", "moat", "moon",
    "moss", "moth", "mound", "night", "north", "oak", "ocean", "olive",
    "onyx", "orbit", "otter", "owl", "palm", "pansy", "path", "peak",
    "pearl", "petal", "pier", "pine", "plain", "plum", "pond", "poppy",
    "port", "pulse", "quail", "quartz", "rain", "ranch", "raven", "realm",
    "reef", "ridge", "ring", "river", "robin", "rock", "root", "rose",
    "sage", "sand", "seal", "seed", "shade", "shell", "shore", "silk",
    "sky", "slate", "sloth", "smoke", "snail", "snake", "snow", "south",
    "spark", "spire", "spray", "spring", "star", "steam", "steel", "stone",
    "stork", "storm", "sun", "swamp", "swan", "thorn", "tide", "tiger",
    "torch", "tower", "trail", "tree", "trout", "tulip", "tusk", "vale",
    "vault", "vine", "viper", "voice", "watch", "wave", "well", "west",
    "whale", "wheat", "willow", "wind", "wing", "wolf", "wood", "wren",
]


def generate_name(existing_names: Optional[set[str]] = None, max_attempts: int = 100) -> str:
    """
    Generate a random two-word name like "bright-falcon".

    Args:
        existing_names: Set of names already in use (to avoid collisions).
        max_attempts: Maximum attempts before falling back to numbered name.

    Returns:
        A unique hyphenated name string.
    """
    if existing_names is None:
        existing_names = set()

    for _ in range(max_attempts):
        adj = random.choice(ADJECTIVES)
        noun = random.choice(NOUNS)
        name = f"{adj}-{noun}"
        if name not in existing_names:
            return name

    # Fallback: append a number if we can't find a unique combination
    # (extremely unlikely with 192 * 224 = 43,008 combinations)
    base = f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}"
    counter = 1
    while f"{base}-{counter}" in existing_names:
        counter += 1
    return f"{base}-{counter}"


def is_valid_name(name: str) -> bool:
    """
    Check if a string looks like a valid session name.

    Valid names are lowercase, hyphen-separated words containing only
    letters, digits, and hyphens.
    """
    if not name or len(name) > 50:
        return False
    return re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", name) is not None
