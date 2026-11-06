"""TV from TVmaze: what is kept, how it maps, and what is never read."""

import inspect

from pipeline import sync_tvmaze

SUMMARY = "<p>A long enough <b>summary</b> &amp; more. " + "Plot goes on. " * 15 + "</p>"


def show(**over):
    data = {
        "id": 82,
        "name": "Game of Thrones",
        "type": "Scripted",
        "language": "English",
        "genres": ["Drama", "Adventure", "Fantasy"],
        "status": "Ended",
        "premiered": "2011-04-17",
        "network": {"country": {"code": "US"}},
        "image": {"medium": "m.jpg", "original": "o.jpg"},
        "summary": SUMMARY,
        "externals": {"imdb": "tt0944947", "thetvdb": 121361, "tvrage": 24493},
        "rating": {"average": 8.9},
        "weight": 99,
    }
    data.update(over)
    return data


def test_a_show_maps_onto_the_media_row():
    row = sync_tvmaze.row_for(show())
    assert row["source_id"] == "82" and row["source"] == "tvmaze"
    assert row["format"] == "SCRIPTED" and row["status"] == "FINISHED"
    assert row["genres"] == ["Drama", "Adventure", "Fantasy"]
    assert row["start_year"] == 2011 and row["country"] == "US"
    assert "<" not in row["description"] and "&amp;" not in row["description"]
    assert row["cover_large"] == "o.jpg"
    assert row["external_ids"] == {"imdb": "tt0944947", "tvdb": "121361"}


def test_web_channel_country_counts_when_there_is_no_network():
    row = sync_tvmaze.row_for(show(network=None, webChannel={"country": {"code": "GB"}}))
    assert row["country"] == "GB"


def test_short_summaries_and_japanese_animation_are_kept_out():
    assert sync_tvmaze.row_for(show(summary="<p>Short.</p>")) is None
    anime = show(type="Animation", language="Japanese", network={"country": {"code": "JP"}})
    assert sync_tvmaze.row_for(anime) is None
    western = show(type="Animation", network={"country": {"code": "US"}})
    assert sync_tvmaze.row_for(western) is not None


def test_rating_and_weight_are_never_read():
    row = sync_tvmaze.row_for(show())
    assert not {"rating", "weight", "average_score", "popularity"} & set(row)
    code = inspect.getsource(sync_tvmaze.row_for)
    assert '"rating"' not in code and '"weight"' not in code


def test_a_short_index_never_removes_shows():
    """An outage that ends the index early must not empty the TV tab."""
    source = inspect.getsource(sync_tvmaze.sync)
    assert 'if tally["read"] >= MIN_INDEX:' in source
    assert sync_tvmaze.MIN_INDEX >= 10_000


def test_tv_is_an_installable_tvmaze_dataset_credited_to_tvmaze():
    from api.datasets import BY_KEY, dataset_for_medium
    from pipeline.dataset_install import INSTALLERS

    assert dataset_for_medium("tv").key == "tvmaze"
    assert "TVmaze" in BY_KEY["tvmaze"].description and "CC BY-SA" in BY_KEY["tvmaze"].description
    assert "tvmaze" in INSTALLERS


def test_the_nightly_pass_reads_tvmaze():
    from pathlib import Path

    nightly = (Path(__file__).resolve().parent.parent / "pipeline/nightly.py").read_text()
    assert "tvmaze_nightly()" in nightly


def test_tv_and_films_are_embedded_before_books():
    """In plain id order the ~965,000 books, which take ids before TV and
    films, held both up for days on a NAS."""
    from pipeline import embed

    order = embed.SOURCE_ORDER
    assert order.index("tvmaze") < order.index("openlibrary")
    assert order.index("tmdb") < order.index("openlibrary")
    assert order[0] == "anilist"


class EmbedConn:
    """Answers SELECT_MISSING_SQL per source from a fixed backlog."""

    def __init__(self, backlog: dict[str, list[int]]):
        self.backlog = backlog
        self.asked: list[str] = []

    def execute(self, sql, params):
        conn = self

        class Result:
            def fetchall(self):
                if "NOT (m.source = ANY" in sql:
                    conn.asked.append("other")
                    return [(i,) for i in conn.backlog.get("other", [])]
                source = params[1]
                conn.asked.append(source)
                return [(i,) for i in conn.backlog.get(source, [])]

        return Result()


def test_the_next_batch_comes_from_the_first_source_with_work_left():
    from pipeline import embed

    conn = EmbedConn({"tvmaze": [7], "openlibrary": [1]})
    assert embed.next_batch(conn, "m", 256, None) == [(7,)]
    assert conn.asked == ["anilist", "tvmaze"]
    conn = EmbedConn({"other": [9]})
    assert embed.next_batch(conn, "m", 256, None) == [(9,)]


def test_a_source_option_limits_the_pass():
    from pipeline import embed

    conn = EmbedConn({"tvmaze": [7], "openlibrary": [1]})
    assert embed.next_batch(conn, "m", 256, ("openlibrary",)) == [(1,)]
    assert "tvmaze" not in conn.asked


def test_both_clients_show_fetch_progress():
    """TV and films report phase "fetching"; a client that only knew the
    books' phases showed "Starting" for the whole install."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    web = (root / "web/app.js").read_text()
    android = (
        root / "android/app/src/main/java/com/mangabrain/app/ui/settings/SettingsFragment.kt"
    ).read_text()
    assert 'install.phase === "fetching"' in web
    assert '"fetching" -> if (total > 0)' in android
    assert '"fetching")' in android and 'setOf("downloading", "fetching")' in android
    assert '"phase": "fetching"' in (root / "pipeline/sync_tvmaze.py").read_text()
    assert (
        '"phase": "fetching", "bytes": 0, "total": total'
        in (root / "pipeline/sync_tmdb.py").read_text()
    )


def test_installing_again_is_labelled_for_what_it_does():
    """TV and films have no published release, so their cards must not
    offer "Update from the newest release"."""
    import inspect

    from api.datasets import BY_KEY
    from pipeline.dataset_install import INSTALLERS

    assert "release" in BY_KEY["books"].update_label
    assert "release" not in BY_KEY["tvmaze"].update_label
    assert "release" not in BY_KEY["tmdb"].update_label
    # A completed TMDB install asked again runs the changes feed.
    assert "sync_tmdb.changes(conn, client)" in inspect.getsource(INSTALLERS["tmdb"])
