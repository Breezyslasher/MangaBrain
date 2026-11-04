"""Title name variants, and near-miss search over ANN's index.

Both exist because "unmatched" is not an answer. The Amazing 3 (1965) came
back unmatched against all five names AniList holds - The Amazing 3,
Wonder 3, the native title, W3, Wondering Three - which leaves two very
different possibilities indistinguishable: ANN has no entry (nothing to do)
or ANN files it under a name none of those five is (a fixable matcher miss).
A near-miss search separates them by naming the entry ANN actually has.

The variant families are the generalisation of that, measured in bulk by
audit_dub stage D. They are kept here rather than in either caller because
the explainer needs the same token canonicalisation the variants imply - if
'3' and 'three' are interchangeable for matching, they have to be
interchangeable for searching too, or the search will miss exactly the
titles the variants would fix.
"""

import re

ARTICLES = ("The", "A", "An")

NUMBER_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
    "11": "eleven",
    "12": "twelve",
    "13": "thirteen",
    "14": "fourteen",
    "15": "fifteen",
    "16": "sixteen",
    "17": "seventeen",
    "18": "eighteen",
    "19": "nineteen",
    "20": "twenty",
}
WORD_NUMBERS = {word: digit for digit, word in NUMBER_WORDS.items()}
DIGIT_RE = re.compile(r"\b(\d{1,2})\b")
WORD_RE = re.compile(r"\b(" + "|".join(WORD_NUMBERS) + r")\b", re.I)

# Dropped from token sets: they carry no identity and would make every
# English title look slightly similar to every other.
STOPWORDS = {"the", "a", "an", "of", "and", "to", "in", "no", "wa", "ga", "ni"}

TOKEN_RE = re.compile(r"[a-z0-9]+")

# Numbers are never identity on their own, however long their spelled form
# is. 'three' is five characters, so without this it counted as a strong
# shared token and 'Apollo 3' came back as a near miss for 'The Amazing 3' -
# which is how a looser search buries the one real entry under noise.
NUMBER_TOKENS = frozenset(NUMBER_WORDS.values())


def variant_article(title: str) -> list[str]:
    """'The Amazing 3' <-> 'Amazing 3, The'. ANN files some titles sorted."""
    out = []
    for article in ARTICLES:
        prefix = f"{article} "
        if title.lower().startswith(prefix.lower()):
            out.append(f"{title[len(prefix) :]}, {article}")
        suffix = f", {article}"
        if title.lower().endswith(suffix.lower()):
            out.append(f"{article} {title[: -len(suffix)]}")
    return out


def variant_numbers(title: str) -> list[str]:
    """'The Amazing 3' <-> 'The Amazing Three'.

    Both directions, because which form a source picked is not predictable:
    AniList holds 'The Amazing 3' while ANN may hold the spelled form, and
    nothing says it is not the other way round for the next title.
    """
    out = []
    # Title case, because the result is printed as well as matched: matching
    # normalises case away, a human reading 'The Amazing three' does not.
    spelled = DIGIT_RE.sub(lambda m: NUMBER_WORDS.get(m.group(1), m.group(1)).capitalize(), title)
    if spelled != title:
        out.append(spelled)
    digits = WORD_RE.sub(lambda m: WORD_NUMBERS[m.group(1).lower()], title)
    if digits != title:
        out.append(digits)
    return out


def variant_subtitle(title: str) -> list[str]:
    """Drop a trailing subtitle: sources disagree on whether to carry it."""
    for sep in (": ", " - ", " ~ "):
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if len(head) >= 4:
                return [head]
    return []


def variant_both(title: str) -> list[str]:
    """Article swap AND number spelling, composed.

    Not redundant with the two families above. ANN's sorted form of
    'The Amazing 3' would be 'Amazing Three, The', which neither single
    transform produces - the article swap gives 'Amazing 3, The' and the
    number spelling gives 'The Amazing Three'. A title needing both was the
    whole reason the first unmatched case stayed unexplained, so composition
    is measured as its own family rather than assumed to be covered.
    """
    out: list[str] = []
    for first, second in ((variant_article, variant_numbers), (variant_numbers, variant_article)):
        for middle in first(title):
            for name in second(middle):
                # Both orders reach the same string, so dedupe: a repeat
                # would be counted twice by the audit's hit totals.
                if name not in out:
                    out.append(name)
    return out


VARIANTS = {
    "article_swap": variant_article,
    "number_words": variant_numbers,
    "drop_subtitle": variant_subtitle,
    "article_and_number": variant_both,
}


def variant_names(family: str, known_names: list[str]) -> list[str]:
    """Extra candidate names from one family, none the matcher already tries.

    A variant equal to a name already in the candidate list would report a
    hit the normal pass already had, crediting the family with recovering a
    title it did not.
    """
    known = {n.strip().lower() for n in known_names}
    out: list[str] = []
    for name in known_names:
        for extra in VARIANTS[family](name):
            key = extra.strip().lower()
            if key and key not in known:
                known.add(key)
                out.append(extra)
    return out


def tokens(title: str | None) -> set[str]:
    """Identity-carrying tokens, with digits and number words unified.

    '3' and 'three' collapse to one token so that a title whose sources
    disagree on the form still scores as similar. Without that, the near-miss
    search would fail on precisely the titles the number_words variant
    exists to fix.
    """
    out: set[str] = set()
    for token in TOKEN_RE.findall((title or "").lower()):
        token = NUMBER_WORDS.get(token, token)
        if token in STOPWORDS or len(token) < 2:
            continue
        out.add(token)
    return out


def near_misses(entries, names: list[str], limit: int = 8) -> list[tuple[float, object]]:
    """ANN entries sharing identity tokens with any candidate name.

    Reporting only, and deliberately not a matcher: it returns things that
    merely look related, with no year or format corroboration, which is
    exactly what must never be written. Its job is to turn "unmatched" into
    "ANN calls it X", so the next step is a decision rather than a guess.
    """
    wanted: set[str] = set()
    for name in names:
        wanted |= tokens(name)
    # A single short token in common is noise; require something substantial
    # to be shared, or every title with "battle" in it comes back. Numbers do
    # not count as substantial at any length.
    strong = {t for t in wanted if len(t) >= 4 and t not in NUMBER_TOKENS}
    if not wanted:
        return []
    scored: list[tuple[float, object]] = []
    for entry in entries:
        have = tokens(entry.name)
        if not have:
            continue
        shared = wanted & have
        named = shared - NUMBER_TOKENS
        if not (shared & strong or len(named) >= 2):
            continue
        jaccard = len(shared) / len(wanted | have)
        scored.append((jaccard, entry))
    scored.sort(key=lambda pair: (-pair[0], getattr(pair[1], "name", "")))
    return scored[:limit]
