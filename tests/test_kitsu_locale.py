"""Normalising Kitsu's cast locales.

Kitsu exposes cast language twice and differently. `castings.language` is
free text and returned "Brazilian" where every other source says Portuguese;
`characterVoices.locale` is a code, but a mixture of bare languages and
locales - ja_jp, en, it, es observed in one twenty-row page.

Collapsing to the language is right for most, and wrong for exactly the
cases a dub filter cares about: Portuguese and Brazilian Portuguese are
separate dub markets, as are the Chinese variants, and ANN tags them apart
(zh-hk, zh-cn, zh-tw) so merging them here would lose a distinction the
other source keeps.
"""

from pipeline.probe_kitsu import normalise_locale


def test_a_locale_collapses_to_its_language():
    assert normalise_locale("ja_jp") == "ja"
    assert normalise_locale("en_us") == "en"


def test_a_bare_language_is_left_alone():
    assert normalise_locale("en") == "en"
    assert normalise_locale("it") == "it"


def test_portuguese_keeps_its_region():
    """pt and pt-br are different dubs, and ANN already distinguishes them."""
    assert normalise_locale("pt_br") == "pt-br"
    assert normalise_locale("pt") == "pt"


def test_chinese_keeps_its_region():
    """ANN carries zh-hk, zh-cn and zh-tw separately; do not merge them."""
    assert normalise_locale("zh_cn") == "zh-cn"
    assert normalise_locale("zh_hk") == "zh-hk"
    assert normalise_locale("zh_tw") == "zh-tw"
    assert normalise_locale("zh") == "zh"


def test_case_and_separator_are_normalised():
    assert normalise_locale("EN") == "en"
    assert normalise_locale("JA_JP") == "ja"
    assert normalise_locale("pt-BR") == "pt-br"


def test_empty_is_named_rather_than_silently_dropped():
    """A missing locale is a fact about the data, not something to discard."""
    assert normalise_locale("") == "(none)"
    assert normalise_locale("   ") == "(none)"
