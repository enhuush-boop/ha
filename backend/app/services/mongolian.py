"""Small, dependency-free helpers for Mongolian Cyrillic and informal Latin galig.

Galig has no single spelling convention. The canonical form is only for
matching short, curated claims; it is not a general translator.
"""

from __future__ import annotations

import re
import unicodedata

_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "ө": "o", "п": "p",
        "р": "r", "с": "s", "т": "t", "у": "u", "ү": "u", "ф": "f",
        "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
        "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)

_LATIN_CUES = {
    "za", "bi", "chi", "ta", "bid", "ene", "enehuu", "ter", "bol",
    "yum", "yamar", "gej", "gedeg", "baina", "bna", "bn", "shuu",
    "uu", "ve", "yu", "mongol", "minii", "mini", "nadad", "hun",
    "humuus", "tegeed", "teheer", "hii", "hiih", "buten",
    "delhii", "havtgai", "havgai", "vaksin", "vaktsin", "autizm",
    "uusgedeg", "uusgedeggui", "hudlaa", "hudal", "medee", "unen",
}
_NEGATION_WORDS = {"bish", "ugui", "ugvi", "gui", "gvi", "not", "never", "no"}


def match_key(text: str) -> str:
    """Fold both scripts to a comparable, deliberately lossy Latin form."""
    value = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    value = value.translate(_CYRILLIC_TO_LATIN)
    value = value.replace("ö", "o").replace("ü", "u")
    value = value.replace("kh", "h").replace("zh", "j")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def has_negation(text: str) -> bool:
    words = match_key(text).split()
    return any(
        word in _NEGATION_WORDS or word.endswith(("gui", "gvi"))
        for word in words
    )


def detect_language(text: str) -> str:
    """Return mn_cyrl, mn_latn, or other; abstain on ambiguous Latin text."""
    value = unicodedata.normalize("NFKC", text).lower()
    cyrillic = len(re.findall(r"[а-яёөү]", value))
    latin = len(re.findall(r"[a-z]", value))
    if cyrillic >= 3 and cyrillic >= latin:
        return "mn_cyrl"
    words = set(match_key(value).split())
    cues = words & _LATIN_CUES
    if latin >= 6 and (len(cues) >= 2 or (len(cues) == 1 and len(words) >= 5)):
        return "mn_latn"
    return "other"
