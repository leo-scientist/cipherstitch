"""Password strength: the app's meter shows it, and Standard and Research modes require a
minimum."""

import itertools
import re
import unicodedata

from zxcvbn import zxcvbn

STRENGTH_LABELS = ("No password", "Weak", "Fair", "Strong", "Very strong")
_MAX_LENGTH = 72  # zxcvbn refuses longer passwords; the characters after only add strength
# Han ideographs, kana, Hangul syllables and Thai, often written without spaces between words.
# zxcvbn rates their letters as it rates random characters, about three to a common word, and
# finds a part repeated in a row itself, so they are left as they are.
_UNSPACED_RANGES = (
    (0x0E00, 0x0E7F),  # Thai
    (0x3040, 0x30FF),  # Hiragana, Katakana
    (0x31F0, 0x31FF),  # Katakana phonetic extensions
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF66, 0xFF9F),  # Halfwidth Katakana
    (0x20000, 0x323AF),  # CJK Unified Ideographs Extensions B to H, compatibility supplement
)
_UNSPACED = "".join(f"{chr(first)}-{chr(last)}" for first, last in _UNSPACED_RANGES)
_OTHER_SCRIPT = re.compile(f"[^\\W\\d_A-Za-z{_UNSPACED}]{{2,}}")
# English words zxcvbn ranks about 1000th. Its dictionaries are English only, so it would
# rate a word in another script (пароль, привіт) as random letters; each such word counts as
# one of these common words instead. No two end in the same letter: zxcvbn takes the first
# repeat it finds, which would otherwise start inside the word before (widely, then activity
# three times, read as "yactivit" three times).
_STAND_INS = (
    "widely",
    "advanced",
    "relations",
    "founder",
    "amount",
    "scottish",
    "memorial",
    "direction",
)


def password_strength(password: str) -> int:
    """0 without a password, else 1–4 from zxcvbn's estimate of the guesses it takes:
    Fair from 10⁶, Strong from 10⁸, Very strong from 10¹⁰."""
    if not password:
        return 0

    # The key is derived from the NFC form (kdf.py): a decomposed password must not rate
    # higher than the same one composed.
    normalized = unicodedata.normalize("NFC", password)
    score: int = zxcvbn(_in_english_words(normalized)[:_MAX_LENGTH])["score"]

    return max(1, score)


def _in_english_words(password: str) -> str:
    """Each word in another script as a common English word. The same word always gets the
    same one, so zxcvbn sees a repeat (пароль пароль пароль) for what it is."""
    runs = _OTHER_SCRIPT.findall(password)
    distinct = dict.fromkeys(word.casefold() for run in runs for word in _words(run))
    english = {word: _STAND_INS[i % len(_STAND_INS)] for i, word in enumerate(distinct)}

    def replace(run: re.Match[str]) -> str:
        return "".join(_stand_in(word, english[word.casefold()]) for word in _words(run.group()))

    return _OTHER_SCRIPT.sub(replace, password)


def _words(run: str) -> list[str]:
    """``run`` split where a lower-case letter meets an upper-case one (camelCase)."""
    cuts = [i for i in range(1, len(run)) if run[i - 1].islower() and run[i].isupper()]

    return [run[start:end] for start, end in itertools.pairwise([0, *cuts, len(run)])]


def _stand_in(word: str, english: str) -> str:
    """``english`` with the case of ``word``."""
    if len(word) > 1 and word.isupper():
        return english.upper()

    return english.capitalize() if word[0].isupper() else english
