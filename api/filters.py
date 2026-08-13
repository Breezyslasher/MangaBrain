"""Shared SQL filter builder for recommend, random, and search."""

from typing import Any


def build_filters(
    *,
    adult: bool = False,
    formats: list[str] | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    min_score: int | None = None,
    countries: list[str] | None = None,
    statuses: list[str] | None = None,
    mal_user: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (sql_fragment, params). The fragment is a chain of ' AND ...'
    clauses safe to append to a WHERE clause that already has a condition.
    Adult content is excluded at the SQL level unless the toggle is on."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if not adult:
        clauses.append("m.is_adult = FALSE")
    if formats:
        clauses.append("m.format = ANY(%(f_formats)s)")
        params["f_formats"] = [f.upper() for f in formats]
    if year_min is not None:
        clauses.append("m.start_year >= %(f_year_min)s")
        params["f_year_min"] = year_min
    if year_max is not None:
        clauses.append("m.start_year <= %(f_year_max)s")
        params["f_year_max"] = year_max
    if min_score is not None:
        clauses.append("m.average_score >= %(f_min_score)s")
        params["f_min_score"] = min_score
    if countries:
        clauses.append("m.country_of_origin = ANY(%(f_countries)s)")
        params["f_countries"] = [c.upper() for c in countries]
    if statuses:
        clauses.append("m.status = ANY(%(f_statuses)s)")
        params["f_statuses"] = [s.upper() for s in statuses]
    if mal_user:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM mal_list_entries l"
            " WHERE l.username = %(f_mal_user)s AND l.id_mal = m.id_mal)"
        )
        params["f_mal_user"] = mal_user.strip().lower()

    sql = "".join(f" AND {clause}" for clause in clauses)
    return sql, params
