import unicodedata

import pytest

from cipherstitch.password import password_strength
from cipherstitch.standard import MIN_PASSWORD_STRENGTH


@pytest.mark.parametrize(
    ("password", "strength"),
    [
        ("", 0),
        ("abc", 1),
        ("Password1!", 1),
        ("kitten2024", 2),
        ("correct horse battery staple", 4),
        ("correct horse battery staple " * 4, 4),  # zxcvbn refuses more than 72 characters
    ],
)
def test_password_strength(password: str, strength: int) -> None:
    assert password_strength(password) == strength


@pytest.mark.parametrize(
    ("password", "strength"),
    [
        ("пароль123", 1),  # noqa: RUF001
        ("україна1991", 1),  # noqa: RUF001
        ("Пароль123!", 2),  # noqa: RUF001
        ("ПАРОЛЬ123", 1),  # noqa: RUF001
        ("гавань ліхтар фіалка океан", 4),
        ("мояСобакаРекс2019", 4),  # noqa: RUF001
        ("café-crème-42", 4),  # single accented letters stay as they are
    ],
)
def test_each_word_in_another_script_counts_as_a_common_word(password: str, strength: int) -> None:
    """zxcvbn has English dictionaries only: alone, it rates these words as random letters."""
    assert password_strength(password) == strength


def test_a_decomposed_password_rates_as_its_composed_form() -> None:
    """The key comes from the NFC form, so typing ї as two code points must not make a weak
    password pass as strong."""
    composed = "Київ1991"  # noqa: RUF001
    decomposed = unicodedata.normalize("NFD", composed)

    assert decomposed != composed
    assert password_strength(decomposed) == password_strength(composed) == 1


def _text(*codes: int) -> str:
    return "".join(map(chr, codes))


@pytest.mark.parametrize(
    ("password", "strength"),
    [
        # 我的猫喜欢在阳台上晒太阳听雨声看星星: Chinese, 18 ideographs, no spaces
        (
            _text(0x6211, 0x7684, 0x732B, 0x559C, 0x6B22, 0x5728, 0x9633, 0x53F0, 0x4E0A)
            + _text(0x6652, 0x592A, 0x9633, 0x542C, 0x96E8, 0x58F0, 0x770B, 0x661F, 0x661F),
            4,
        ),
        (_text(0x5BC6, 0x7801), 1),  # 密码, "password"
        # ねこがひるねをしているようだな: Japanese hiragana
        (
            _text(0x306D, 0x3053, 0x304C, 0x3072, 0x308B, 0x306D, 0x3092, 0x3057)
            + _text(0x3066, 0x3044, 0x308B, 0x3088, 0x3046, 0x3060, 0x306A),
            4,
        ),
        # 고양이가지붕위에서낮잠을자요: Korean written without spaces
        (
            _text(0xACE0, 0xC591, 0xC774, 0xAC00, 0xC9C0, 0xBD95, 0xC704)
            + _text(0xC5D0, 0xC11C, 0xB0AE, 0xC7A0, 0xC744, 0xC790, 0xC694),
            4,
        ),
    ],
    ids=["chinese", "chinese-word", "japanese", "korean"],
)
def test_scripts_without_spaces_are_rated_letter_by_letter(password: str, strength: int) -> None:
    """A whole run once counted as one common word, so no such passphrase reached Strong."""
    assert password_strength(password) == strength


@pytest.mark.parametrize(
    "password",
    [
        _text(*[0x54C8] * 6),  # 哈哈哈哈哈哈, "hahaha"
        _text(0x5BC6, 0x7801) * 3,  # 密码 ("password") three times
        _text(0x5BC6, 0x7801) * 4,
        _text(0xC0AC, 0xB791) * 5,  # 사랑 ("love") five times
        _text(0x4E00, 0x5E06, 0x98CE, 0x987A) * 2,  # 一帆风顺, a four-character idiom, twice
        _text(0x6211, 0x7231, 0x4F60) * 3,  # 我爱你 ("I love you") three times
        _text(0x8C22, 0x8C22, 0x4F60) * 3,  # 谢谢你 ("thank you"), a doubled letter inside
        _text(0x6211) + _text(0x5BC6, 0x7801) * 3,  # 我 then 密码 three times
        _text(0x306D, 0x306D, 0x3053) * 3,  # ねねこ
        _text(0x0E23, 0x0E31, 0x0E01) * 3,  # รัก ("love") three times, with its vowel sign
    ],
    ids=[
        "one-ideograph",
        "chinese-word",
        "chinese-word-4",
        "korean-word",
        "chinese-idiom",
        "chinese-phrase",
        "doubled-letter",
        "after-a-word",
        "kana",
        "thai",
    ],
)
def test_a_repeat_in_a_script_without_spaces_stays_below_strong(password: str) -> None:
    """Cut into words of a few letters, a repeat once read as different words."""
    assert password_strength(password) < MIN_PASSWORD_STRENGTH


@pytest.mark.parametrize(
    "password", [_text(0x0E31), _text(0x0301), _text(0xFE0F)], ids=["thai", "acute", "selector"]
)
def test_a_password_of_marks_alone_is_weak(password: str) -> None:
    assert password_strength(password) == 1


def test_a_repeated_word_rates_below_different_words() -> None:
    """With stand-ins that end in the same letter, zxcvbn read the repeat from inside the word
    before it (``мій пароль пароль пароль`` rated Very strong)."""
    mine, word = (
        _text(0x043C, 0x0456, 0x0439),
        _text(0x043F, 0x0430, 0x0440, 0x043E, 0x043B, 0x044C),
    )
    others = (
        _text(0x0441, 0x043E, 0x043D, 0x0446, 0x0435),
        _text(0x043C, 0x0456, 0x0441, 0x0442, 0x043E),
    )

    for lead in ([], [mine]):
        repeated = password_strength(" ".join([*lead, word, word, word]))

        assert repeated < password_strength(" ".join([*lead, word, *others]))
