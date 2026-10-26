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


def claim_identity_forms(text: str) -> list[str]:
    """Conservative exact forms for stored editorial claim matching.

    Unlike match_key, this keeps Mongolian ө/о and ү/у distinct. It adds only
    a direct galig-to-Cyrillic spelling when the input looks Mongolian; it does
    not use fuzzy spelling rewrites for journalist assessments.
    """
    original = unicodedata.normalize("NFKC", text).casefold()
    original = re.sub(r"[^\w]+", " ", original, flags=re.UNICODE)
    original = " ".join(original.split())
    forms = [original] if original else []
    if detect_language(text) == "mn_latn":
        converted = _latin_to_cyrillic(text)
        converted = re.sub(r"[^\w]+", " ", converted.casefold(), flags=re.UNICODE)
        converted = " ".join(converted.split())
        if converted and converted not in forms:
            forms.append(converted)
    return forms


_LATIN_TO_CYRILLIC = {
    "yo": "ё", "yu": "ю", "ya": "я", "ts": "ц", "ch": "ч",
    "sh": "ш", "zh": "ж", "kh": "х",
}


def _latin_to_cyrillic(text: str) -> str:
    """Best-effort galig rendering used only to broaden retrieval queries."""
    value = text.lower().replace("ö", "ө").replace("ü", "ү")
    # Common Mongolian galig endings and diphthongs are not recoverable from
    # character-by-character transliteration (e.g. havtgai -> хавтгай,
    # ...gui -> ...гүй). Keep this as a retrieval variant, never a translation.
    value = re.sub(r"\buus", "үүс", value)
    value = re.sub(r"g[uү]i\b", "гүй", value)
    value = re.sub(r"ai\b", "ай", value)
    value = re.sub(r"oi\b", "ой", value)
    value = re.sub(r"ks", "kts", value)
    value = re.sub(r"lh", "лх", value)
    for latin, cyrillic in (("ii", "ий"), ("aa", "аа"), ("ee", "ээ"),
                             ("uu", "уу"), ("oo", "оо")):
        value = re.sub(rf"{latin}\b", cyrillic, value)
    value = re.sub(
        r"(yo|yu|ya|ts|ch|sh|zh|kh)",
        lambda match: _LATIN_TO_CYRILLIC[match.group(0)],
        value,
    )
    reverse = {
        "a":"а", "b":"б", "v":"в", "g":"г", "d":"д", "e":"э",
        "j":"ж", "z":"з", "i":"и", "k":"к", "l":"л", "m":"м",
        "n":"н", "o":"о", "p":"п", "r":"р", "s":"с", "t":"т",
        "u":"у", "f":"ф", "h":"х", "c":"ц", "w":"в", "y":"ы",
    }
    return "".join(reverse.get(char, char) for char in value)


def search_variants(
    text: str, limit: int = 12, force_mongolian: bool = False
) -> list[str]:
    """Bounded common galig/Cyrillic spellings; not a translator or verdict."""
    original = " ".join(unicodedata.normalize("NFKC", text).split())
    folded = original.lower()
    variants = [original]
    is_cyrillic = bool(re.search(r"[а-яёөү]", folded))
    if not is_cyrillic and not force_mongolian and detect_language(original) != "mn_latn":
        return variants
    if is_cyrillic:
        base = folded.translate(_CYRILLIC_TO_LATIN)
        variants.append(base)
        replacements = (("ts", "s"), ("ch", "c"), ("sh", "s"),
                        ("o", "ö"), ("u", "ü"), ("h", "kh"), ("j", "zh"))
        for before, after in replacements:
            if before in base:
                variants.append(base.replace(before, after))
    else:
        variants.append(_latin_to_cyrillic(folded))
        # Galig commonly drops or changes digraphs (ts/s, ch/c, sh/s) and
        # Mongolian vowels (o/ö, u/ü). Each query gets one bounded rewrite.
        replacements = (("ts", "s"), ("s", "ts"), ("ch", "c"), ("c", "ch"),
                        ("sh", "s"), ("o", "ö"), ("u", "ü"),
                        ("kh", "h"), ("h", "kh"), ("x", "h"))
        for before, after in replacements:
            if before in folded:
                variants.append(folded.replace(before, after))

    result: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        # Some common spelling rewrites (notably ш -> с) can erase the
        # spelling of биш. Never emit a rewrite that changes detected polarity.
        if has_negation(original) != has_negation(variant):
            continue
        # Keep script-distinct spellings even when match_key folds them to
        # the same value. Search APIs often need the literal Cyrillic query
        # to find Mongolian reviews; only exact duplicate strings are noise.
        key = " ".join(variant.casefold().split())
        if key and key not in seen:
            seen.add(key)
            result.append(variant)
        if len(result) >= limit:
            break
    return result


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
