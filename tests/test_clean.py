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


def test_strips_spoiler_blocks_entirely():
    raw = "A hero rises. ~!He was the villain all along.!~ The journey begins."
    assert clean_description(raw) == "A hero rises. The journey begins."


def test_strips_multiline_spoilers():
    raw = "Intro.~!spoiler line one<br>spoiler line two!~Outro."
    assert clean_description(raw) == "Intro.Outro."
