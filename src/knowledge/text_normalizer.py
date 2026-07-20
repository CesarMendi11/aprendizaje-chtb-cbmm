from __future__ import annotations

import re
import unicodedata

STOP_WORDS = {
    "a",
    "al",
    "como",
    "con",
    "cual",
    "de",
    "del",
    "donde",
    "el",
    "en",
    "esta",
    "este",
    "hay",
    "la",
    "las",
    "lo",
    "los",
    "me",
    "mi",
    "para",
    "por",
    "puedo",
    "que",
    "se",
    "su",
    "un",
    "una",
    "y",
}

EQUIVALENCES = {
    "modulo": "pantalla",
    "modulos": "pantalla",
}


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/]+", " ", without_accents)).strip()


def tokens(value: str, *, remove_stop_words: bool = True) -> set[str]:
    words = normalize_text(value).replace("/", " ").split()
    return {
        EQUIVALENCES.get(word, _singularize(word))
        for word in words
        if not remove_stop_words or word not in STOP_WORDS
    }


def _singularize(word: str) -> str:
    """Apply conservative Spanish plural rules without business vocabulary."""
    if len(word) > 6 and word.endswith("ciones"):
        return f"{word[:-6]}cion"
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and word[-2] in "aeiou":
        return word[:-1]
    return word
