"""Clean AniList descriptions before embedding and display."""

import html
import re

_SPOILER_RE = re.compile(r"~!.*?!~", re.DOTALL)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"[ \t]+")
_NEWLINES_RE = re.compile(r"\n{3,}")


def clean_description(text: str | None) -> str:
    """Strip AniList spoiler markup, HTML tags, and entities from a
    description. Spoiler blocks are removed entirely so spoiler text never
    reaches displayed descriptions or embeddings."""
    if not text:
        return ""
    text = _SPOILER_RE.sub("", text)
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _SPACES_RE.sub(" ", text)
    text = _NEWLINES_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()
