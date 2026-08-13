from pipeline.clean import clean_description


def test_strips_tags_and_entities():
    raw = "<i>Foo</i> fights &amp; wins.<br><br>Second paragraph."
    assert clean_description(raw) == "Foo fights & wins.\n\nSecond paragraph."


def test_collapses_whitespace():
    assert clean_description("a    b\t\tc") == "a b c"


def test_handles_empty():
    assert clean_description(None) == ""
    assert clean_description("") == ""


def test_collapses_blank_lines():
    assert clean_description("a<br><br><br><br>b") == "a\n\nb"
