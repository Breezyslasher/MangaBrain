from pipeline.medium import derive_medium


def test_anime():
    assert derive_medium("ANIME", "TV", "JP") == "anime"
    assert derive_medium("ANIME", "MOVIE", "KR") == "anime"


def test_light_novel():
    assert derive_medium("MANGA", "NOVEL", "JP") == "light_novel"


def test_one_shot():
    assert derive_medium("MANGA", "ONE_SHOT", "JP") == "one_shot"


def test_manhwa():
    assert derive_medium("MANGA", "MANGA", "KR") == "manhwa"


def test_manhua():
    assert derive_medium("MANGA", "MANGA", "CN") == "manhua"
    assert derive_medium("MANGA", "MANGA", "TW") == "manhua"


def test_manga_default():
    assert derive_medium("MANGA", "MANGA", "JP") == "manga"
    assert derive_medium("MANGA", None, None) == "manga"
