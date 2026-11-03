"""Offline matching against ANN's bulk anime index.

ANN's title search needs the query to be a prefix of a real ANN title, so it
returned zero entries for every name the cross-check sent it - 'BEASTARS Final
Season Part 2', 'Drifters: Battle in a Brand-new World War', anything in
Japanese script. Worse, the failures clustered on sequels, seasons and OVAs,
which are exactly the likeliest false negatives, so any rate computed from the
titles that happened to match was biased.

Matching therefore happens locally against reports.xml. A wrong match asserts
another show's dub, so these tests pin the rules that keep it honest: an exact
name wins over any base form, a base form is only accepted once year and
format single it out, and anything still plural is reported ambiguous rather
than guessed.
"""

from pipeline.ann import AnnEntry, base_form, build_index, match, narrow, normalize

BEASTARS_TV = AnnEntry("1", "Beastars", "TV", 2019)
BEASTARS_ONA = AnnEntry("2", "Beastars", "ONA", 2026)
CAPETA = AnnEntry("3", "Capeta", "TV", 2005)
SHIROBAKO_TV = AnnEntry("4", "Shirobako", "TV", 2014)
SHIROBAKO_MOVIE = AnnEntry("5", "Shirobako the Movie", "movie", 2020)
INDEX = build_index([BEASTARS_TV, BEASTARS_ONA, CAPETA, SHIROBAKO_TV, SHIROBAKO_MOVIE])


def test_base_form_strips_season_and_part_markers():
    assert base_form("BEASTARS Final Season Part 2") == "BEASTARS"
    assert base_form("Hibike! Euphonium 2") == "Hibike! Euphonium"
    assert base_form("DRIFTERS OVA") == "DRIFTERS"
    assert base_form("Shingeki no Kyojin Season 3 Part 2") == "Shingeki no Kyojin"
    assert base_form("Yu-Gi-Oh! ZEXAL II") == "Yu-Gi-Oh! ZEXAL"


def test_base_form_leaves_a_plain_title_alone():
    assert base_form("Cowboy Bebop") == "Cowboy Bebop"
    assert base_form("capeta") == "capeta"
    # Stripping must never empty the title out.
    assert base_form("2") == "2"
    assert base_form("OVA") == "OVA"


def test_an_exact_name_resolves():
    verdict, entry = match(INDEX, ["Capeta"], year=2005, fmt="TV")
    assert (verdict, entry) == ("exact", CAPETA)


def test_a_shared_name_is_split_by_year_and_format():
    # Beastars exists twice; only the pair of year and format separates them.
    verdict, entry = match(INDEX, ["Beastars"], year=2019, fmt="TV")
    assert (verdict, entry) == ("exact", BEASTARS_TV)
    verdict, entry = match(INDEX, ["Beastars"], year=2026, fmt="ONA")
    assert (verdict, entry) == ("exact", BEASTARS_ONA)


def test_a_shared_name_with_no_year_stays_ambiguous():
    verdict, entry = match(INDEX, ["Beastars"], year=None, fmt=None)
    assert verdict == "ambiguous_exact"
    assert entry is None


def test_a_sequel_falls_back_to_the_base_name_and_is_dated():
    # 'Beastars Final Season Part 2' is not an ANN title; its base is, and the
    # year picks the 2026 entry out.
    verdict, entry = match(INDEX, ["Beastars Final Season Part 2"], year=2026, fmt="ONA")
    assert (verdict, entry) == ("base", BEASTARS_ONA)


def test_an_exact_match_beats_a_base_match_on_another_candidate():
    """The movie must not resolve to the series just because a base form hits.

    'Shirobako the Movie' is an exact ANN title, and its base form 'Shirobako'
    is a different entry. Trying every candidate's exact form before any base
    form is what keeps them apart.
    """
    verdict, entry = match(INDEX, ["Shirobako the Movie", "Shirobako"], year=2020, fmt="MOVIE")
    assert (verdict, entry) == ("exact", SHIROBAKO_MOVIE)


def test_an_unknown_title_is_unmatched_not_approximated():
    verdict, entry = match(INDEX, ["Some Show That Does Not Exist"], year=2020, fmt="TV")
    assert (verdict, entry) == ("unmatched", None)
    assert match(INDEX, [], year=None, fmt=None) == ("unmatched", None)


def test_narrow_never_filters_everything_away():
    """A missing vintage on ANN's side must not destroy a correct match."""
    undated = [AnnEntry("9", "Thing", "TV", None), AnnEntry("10", "Thing", "TV", None)]
    assert len(narrow(undated, year=2015, fmt="TV")) == 2
    # And a format ANN records differently still survives.
    ova = [AnnEntry("11", "Thing", "OAV", 2015)]
    assert narrow(ova, year=2015, fmt="OVA") == ova


def test_ova_maps_to_anns_oav_spelling():
    index = build_index([AnnEntry("12", "Denpa Teki na Kanojo", "OAV", 2009)])
    verdict, entry = match(index, ["Denpa Teki na Kanojo"], year=2009, fmt="OVA")
    assert verdict == "exact"
    assert entry is not None and entry.kind == "OAV"


def test_normalization_is_punctuation_and_case_insensitive():
    assert normalize("Yu-Gi-Oh! ZEXAL") == normalize("yugioh zexal")
    assert normalize("ビースターズ") == "ビースターズ"


def test_index_keys_both_the_full_name_and_its_base():
    index = build_index([AnnEntry("13", "Hibike! Euphonium 2", "TV", 2016)])
    assert normalize("Hibike! Euphonium 2") in index
    assert normalize("Hibike! Euphonium") in index


def test_a_unique_name_still_needs_corroborating():
    """The Suntory/your name. failure: same name, utterly different work.

    A Suntory mineral-water advert carries a tie-in synonym, matched ANN's
    entry for the film exactly, and was counted as a dubbed title because a
    single hit was accepted without ever checking that a two-character advert
    and a feature film are not the same thing.
    """
    index = build_index([AnnEntry("18171", "your name.", "movie", 2016)])
    candidates = ["Suntory Minami Alps no Tennen Mizu", "your name."]
    assert match(index, candidates, year=2016, fmt="SPECIAL") == ("rejected_format", None)
    # The film itself still resolves.
    verdict, entry = match(index, ["your name."], year=2016, fmt="MOVIE")
    assert verdict == "exact"
    assert entry is not None and entry.ann_id == "18171"


def test_a_contradicting_year_is_rejected_not_accepted():
    index = build_index([AnnEntry("18171", "your name.", "movie", 2016)])
    assert match(index, ["your name."], year=2021, fmt="MOVIE") == ("rejected_year", None)


def test_a_year_within_tolerance_still_matches():
    # AniList dates the first episode, ANN sometimes a festival screening.
    index = build_index([AnnEntry("1", "Thing", "TV", 2015)])
    assert match(index, ["Thing"], year=2016, fmt="TV")[0] == "exact"


def test_corroboration_is_skipped_when_either_side_is_unknown():
    from pipeline.ann import corroborates

    undated = AnnEntry("1", "Thing", None, None)
    assert corroborates(undated, year=2015, fmt="TV") == ""
    dated = AnnEntry("2", "Thing", "TV", 2015)
    assert corroborates(dated, year=None, fmt=None) == ""


def test_a_base_match_is_exempt_from_year_and_format_agreement():
    """A season resolving to its parent differs from it in both, by design.

    Requiring agreement rejected BEASTARS FINAL SEASON Part 2 (ONA, 2026)
    against ANN's BEASTARS (TV, 2019) as rejected_format, discarding the only
    genuine dub finding in the sample. An exact match claims the entries are
    the same work; a base match claims only that one belongs to the other.
    """
    index = build_index([AnnEntry("38167", "BEASTARS", "TV", 2019)])
    verdict, entry = match(index, ["BEASTARS Final Season Part 2"], year=2026, fmt="ONA")
    assert verdict == "base"
    assert entry is not None and entry.ann_id == "38167"


def test_an_exact_match_is_still_held_to_agreement():
    # The Suntory/your name. guard must survive the base-match exemption.
    index = build_index([AnnEntry("18171", "your name.", "movie", 2016)])
    candidates = ["Suntory Minami Alps no Tennen Mizu", "your name."]
    assert match(index, candidates, year=2016, fmt="SPECIAL") == ("rejected_format", None)
