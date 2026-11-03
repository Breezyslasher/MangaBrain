"""ANN encyclopedia client: bulk index, offline title matching, batched cast.

Why this exists rather than ANN's title search: the search needs the query to
be a prefix of a real ANN title, so it returns nothing at all for the names
AniList hands over. Measured, every failed lookup in the cross-check was
"ANN returned 0 entries", never "returned entries that did not match" -
including 'BEASTARS Final Season Part 2', 'Drifters: Battle in a Brand-new
World War' and every title in Japanese script. The failures were not random
either: they clustered on the sequels, seasons and OVAs that are the likeliest
false negatives, which biases any rate computed from the survivors.

So the catalog is matched against a local index instead. reports.xml hands
over every anime entry with id, name, type and vintage under nskip/nlist
paging, which makes matching offline, auditable, and free of ANN's search
semantics. type and vintage also disambiguate where a name cannot: BEASTARS
the 2019 TV series and the 2026 ONA share a base name and separate cleanly on
both.

ANN asks for roughly one request a second and for responses to be cached, so
the index is fetched once per run and reused, and cast lookups are batched.
"""

import re
import time
from dataclasses import dataclass
from xml.etree import ElementTree

import httpx

ANN_API = "https://cdn.animenewsnetwork.com/encyclopedia/api.xml"
ANN_REPORTS = "https://cdn.animenewsnetwork.com/encyclopedia/reports.xml"

# Report 155 is the anime list; 'args' echoes the query and is not an entry.
ANIME_REPORT_ID = "155"
REPORT_PAGE = 1000
MAX_REPORT_PAGES = 80
# api.xml accepts repeated anime= parameters, so casts come back in batches
# rather than one request per title.
CAST_BATCH = 20
MIN_INTERVAL = 2.5
UA = "MangaBrain/0.1 (self-hosted recommendation engine; dub coverage probe)"

NORMALIZE_RE = re.compile(r"[^a-z0-9\u0080-\U0010ffff]+")

# Trailing markers that distinguish an entry from its base title. Stripped
# only to build a fallback key: a base-form match still has to be
# disambiguated by year and format before it is accepted.
SEASON_TAIL = re.compile(
    r"(?:"
    r"\b(?:season|series|part|cour|chapter)\s*\d+|"
    r"\b\d+(?:st|nd|rd|th)\s+(?:season|series|part)|"
    r"\bfinal\s+(?:season|chapter)|"
    r"\b(?:oav|ova|ona|movie|film|specials?|picture\s+drama|recap)\b|"
    r"\bs\d+\b|"
    r"\b(?:ii|iii|iv|v|vi|vii|viii|ix|x)\b|"
    r"\b\d{1,2}\b"
    r")\s*$",
    re.I,
)

# AniList format -> the ANN types that are plausibly the same thing. ANN uses
# OAV where AniList uses OVA, and lowercases movie.
FORMAT_TO_ANN: dict[str, set[str]] = {
    "TV": {"TV", "TV special"},
    "TV_SHORT": {"TV", "TV special", "ONA"},
    "MOVIE": {"movie", "OAV"},
    "SPECIAL": {"TV special", "OAV", "special", "TV"},
    "OVA": {"OAV", "OVA", "special"},
    "ONA": {"ONA", "OAV"},
    "MUSIC": {"music video", "OAV"},
}


@dataclass(frozen=True)
class AnnEntry:
    ann_id: str
    name: str
    kind: str | None
    year: int | None


def normalize(title: str | None) -> str:
    return NORMALIZE_RE.sub("", (title or "").lower())


def base_form(title: str) -> str:
    """Strip trailing season, part and format markers, repeatedly.

    'BEASTARS Final Season Part 2' reduces to 'BEASTARS', which exists on ANN
    where the full string does not.
    """
    text = title
    for _ in range(4):
        stripped = SEASON_TAIL.sub("", text).strip(" :-–—·,")
        if stripped == text or not stripped:
            break
        text = stripped
    return text


def _year(value: str | None) -> int | None:
    match = re.search(r"\d{4}", value or "")
    return int(match.group()) if match else None


def fetch_anime_index(
    client: httpx.Client, page_size: int = REPORT_PAGE, max_pages: int = MAX_REPORT_PAGES
) -> list[AnnEntry]:
    """Page through ANN's anime report once, oldest offset first."""
    entries: list[AnnEntry] = []
    for page in range(max_pages):
        params = {
            "id": ANIME_REPORT_ID,
            "type": "anime",
            "nskip": str(page * page_size),
            "nlist": str(page_size),
        }
        resp = client.get(ANN_REPORTS, params=params)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.text)
        items = list(root.iter("item"))
        for item in items:
            fields = {child.tag: (child.text or "").strip() for child in item}
            ann_id = fields.get("id")
            if not ann_id:
                continue
            entries.append(
                AnnEntry(
                    ann_id=ann_id,
                    name=fields.get("name", ""),
                    kind=fields.get("type") or None,
                    year=_year(fields.get("vintage")),
                )
            )
        if len(items) < page_size:
            break
        time.sleep(MIN_INTERVAL)
    return entries


def build_index(entries: list[AnnEntry]) -> dict[str, list[AnnEntry]]:
    """Normalised name and base form both key into the same index."""
    index: dict[str, list[AnnEntry]] = {}
    for entry in entries:
        for key in {normalize(entry.name), normalize(base_form(entry.name))}:
            if key:
                index.setdefault(key, []).append(entry)
    return index


def narrow(hits: list[AnnEntry], year: int | None, fmt: str | None) -> list[AnnEntry]:
    """Prefer entries agreeing on year, then on format.

    Applied only when a name is not unique, and each filter is skipped when it
    would remove everything, so a missing vintage on ANN's side cannot turn a
    correct single match into no match.
    """
    if len(hits) < 2:
        return hits
    if year is not None:
        same_year = [h for h in hits if h.year is not None and abs(h.year - year) <= 1]
        if same_year:
            hits = same_year
    if fmt:
        allowed = FORMAT_TO_ANN.get(fmt.upper(), set())
        same_kind = [h for h in hits if h.kind in allowed]
        if same_kind:
            hits = same_kind
    return hits


# A vintage can legitimately differ by a year between the two databases
# (AniList dates the first episode, ANN sometimes the release or a festival
# screening), so agreement is checked with tolerance rather than equality.
YEAR_TOLERANCE = 1


def corroborates(entry: AnnEntry, year: int | None, fmt: str | None) -> str:
    """Whether a name match is backed up by year and format, or contradicted.

    A unique name hit used to be accepted outright, and that is how a Suntory
    mineral-water commercial matched ANN's entry for 'your name.' and was
    counted as a dubbed title: the commercial carries a tie-in synonym, the
    name matched exactly, and nothing ever checked that a 2-character advert
    and a feature film are not the same work. A single match needs
    corroborating just as much as an ambiguous one needs resolving.
    """
    if year is not None and entry.year is not None and abs(entry.year - year) > YEAR_TOLERANCE:
        return "year"
    if fmt and entry.kind:
        allowed = FORMAT_TO_ANN.get(fmt.upper(), set())
        if allowed and entry.kind not in allowed:
            return "format"
    return ""


def match(
    index: dict[str, list[AnnEntry]],
    candidates: list[str],
    year: int | None = None,
    fmt: str | None = None,
) -> tuple[str, AnnEntry | None]:
    """Resolve one title against the index.

    Exact names are tried across every candidate before any base form is
    considered, so a sequel never resolves to its parent while an exact match
    for it exists somewhere in the candidate list. A base match is reported
    under its own verdict because it is a different claim: it resolves a
    season or OVA to its parent series, which is useful for inferring a dub
    but is not the same entry.
    """
    for keyer, verdict in ((normalize, "exact"), (lambda t: normalize(base_form(t)), "base")):
        for name in candidates:
            key = keyer(name)
            if not key:
                continue
            hits = narrow(index.get(key, []), year, fmt)
            unique = {h.ann_id: h for h in hits}
            if len(unique) > 1:
                return f"ambiguous_{verdict}", None
            if len(unique) == 1:
                entry = next(iter(unique.values()))
                contradiction = corroborates(entry, year, fmt)
                if contradiction:
                    return f"rejected_{contradiction}", None
                return verdict, entry
    return "unmatched", None


def fetch_casts(client: httpx.Client, ann_ids: list[str]) -> dict[str, tuple[int, set[str]]]:
    """English cast size and all cast languages, per ANN id, in batches."""
    out: dict[str, tuple[int, set[str]]] = {}
    for start in range(0, len(ann_ids), CAST_BATCH):
        batch = ann_ids[start : start + CAST_BATCH]
        resp = client.get(ANN_API, params=[("anime", i) for i in batch])
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.text)
        for anime in root.iter("anime"):
            casts = list(anime.iter("cast"))
            english = sum(1 for c in casts if c.attrib.get("lang") == "EN")
            langs = {c.attrib.get("lang") for c in casts if c.attrib.get("lang")}
            out[anime.attrib.get("id", "")] = (english, langs)
        if start + CAST_BATCH < len(ann_ids):
            time.sleep(MIN_INTERVAL)
    return out
