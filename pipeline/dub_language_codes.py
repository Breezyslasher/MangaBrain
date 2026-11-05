"""AniList's Staff.languageV2 values, mapped to language codes.

languageV2 is a free-ish string rather than an enum, which is the reason to
read it instead of filtering: the StaffLanguage enum carries ten values, but
one unfiltered request over five titles returned thirteen languages, three of
which - Chinese, Hindi and Indonesian - are not in the enum at all. A design
built on the enum would have missed them silently.

So the mapping cannot be assumed complete, and an unknown value must not be
dropped. It is slugged and kept, and the caller reports it, so a language
AniList adds shows up as a slug to be mapped rather than as absent data.
"""

import re

# Codes follow what the rest of the dub columns already use: ISO-639-1 where
# one exists, and a language-region tag only where it separates real dub
# markets, as ANN's zh-hk/zh-cn/zh-tw and pt-br do.
LANGUAGE_V2_CODES: dict[str, str] = {
    "JAPANESE": "ja",
    "ENGLISH": "en",
    "KOREAN": "ko",
    "ITALIAN": "it",
    "SPANISH": "es",
    "PORTUGUESE": "pt",
    "FRENCH": "fr",
    "GERMAN": "de",
    "HEBREW": "he",
    "HUNGARIAN": "hu",
    # Observed in one five-title request and absent from StaffLanguage.
    "CHINESE": "zh",
    "HINDI": "hi",
    "INDONESIAN": "id",
    # Not yet observed here, but carried by ANN for this catalog, so they are
    # mapped in advance rather than arriving as slugs.
    "ARABIC": "ar",
    "POLISH": "pl",
    "DUTCH": "nl",
    "SWEDISH": "sv",
    "DANISH": "da",
    "NORWEGIAN": "no",
    "FINNISH": "fi",
    "RUSSIAN": "ru",
    "TURKISH": "tr",
    "THAI": "th",
    "VIETNAMESE": "vi",
    "TAGALOG": "tl",
    "FILIPINO": "tl",
    "GREEK": "el",
    "CZECH": "cs",
    "ROMANIAN": "ro",
    "UKRAINIAN": "uk",
    "CATALAN": "ca",
    "BASQUE": "eu",
    "GALICIAN": "gl",
    "VALENCIAN": "ca",
    "PORTUGUESE (BRAZIL)": "pt-br",
    "BRAZILIAN PORTUGUESE": "pt-br",
    "SPANISH (LATIN AMERICA)": "es-419",
    "MANDARIN": "zh",
    "CANTONESE": "zh-hk",
}

# A region written as a two-letter code in brackets, which is how the MAL
# cast API spells what AniList spells out: "Portuguese (BR)" against
# "Portuguese (Brazil)". Derived rather than enumerated, because the cost of
# a missing pairing is not a visible error. Measured: "Portuguese (BR)" was
# slugged to portuguese-(br) and written to over a thousand rows, where the
# dub filter queries pt-br and matched none of them, so a Brazilian dub
# filter returned nothing while the data was sitting in the column.
REGION_RE = re.compile(r"^(.+?)\s*\(([A-Z]{2})\)$")

# What a stored value looks like once it IS a code: two letters, optionally
# a region tag. Anything else in the column is a slug of a name the mapping
# did not know, which remains remappable because slug() is reversible.
CODE_RE = re.compile(r"^[a-z]{2}(-[a-z0-9]{2,3})?$")


def slug(language: str) -> str:
    """A stable code for an unmapped languageV2 value.

    Lowercased and hyphenated rather than discarded: an unknown language is
    missing from the mapping, not missing from the data, and silently
    dropping it would understate coverage in exactly the quiet way this
    investigation kept running into.
    """
    cleaned = "-".join(language.strip().lower().split())
    return cleaned or "unknown"


def is_code(value: str) -> bool:
    """Whether a value already in the column is a code or an unmapped slug."""
    return bool(CODE_RE.match(value))


def unslug(code: str) -> str:
    """Reverse slug(), so a name stored unmapped can be mapped afterwards.

    The point of slugging rather than dropping was that the name survives.
    This is what cashes that in: a row written before a mapping existed can
    be corrected without asking the source again, which matters because the
    backfill stamps what it has asked about and so will not ask twice.
    """
    return code.replace("-", " ").upper()


def code_for(language: str | None) -> tuple[str, bool]:
    """Returns (code, was_known). An empty language is reported, not dropped."""
    if not language or not language.strip():
        return "unknown", False
    key = language.strip().upper()
    mapped = LANGUAGE_V2_CODES.get(key)
    if mapped:
        return mapped, True
    region = REGION_RE.match(key)
    if region:
        base = LANGUAGE_V2_CODES.get(region.group(1).strip())
        if base:
            return f"{base}-{region.group(2).lower()}", True
    return slug(language), False
