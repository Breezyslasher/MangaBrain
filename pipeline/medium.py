"""Derive MangaBrain's medium discriminator from AniList type/format/country."""

MANHUA_COUNTRIES = ("CN", "TW")


def derive_medium(media_type: str, format_: str | None, country: str | None) -> str:
    if media_type == "ANIME":
        return "anime"
    if format_ == "NOVEL":
        return "light_novel"
    if format_ == "ONE_SHOT":
        return "one_shot"
    if country == "KR":
        return "manhwa"
    if country in MANHUA_COUNTRIES:
        return "manhua"
    return "manga"
