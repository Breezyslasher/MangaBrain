"""Are well-known books in the catalogue, and if not, why not?

The books catalogue is whatever Open Library's works dump carries with
enough content to rank (pipeline.sync_openlibrary). Open Library often
keeps a famous book's blurb on its editions rather than on the work,
and the import reads only works - which is how "A Dog's Purpose" went
missing. This checks a fixed list of widely read books across genres
and, for each one the catalogue lacks, asks Open Library why:

  not on Open Library      no work matched the title and author
  no description           the work record holds none
  description N chars      too short for the import's rule
  M real subjects          too few subjects for a short description
  qualifies now            passes today's rule: re-run the import

It is a coverage check, never an input to ranking: the list says which
books to look for, not which to favour.

    docker exec mangabrain-worker-1 python -m pipeline.check_books
    docker exec mangabrain-worker-1 python -m pipeline.check_books --no-lookup
"""

import argparse
import time
import unicodedata

from pipeline.openlibrary import MIN_DESCRIPTION, description_of
from pipeline.sync_openlibrary import NOISE_SUBJECTS, clean_description, row_for

API = "https://openlibrary.org"
# Open Library asks API users to stay around one request a second.
INTERVAL = 1.0

# (title, author surname): widely read books across genres and decades.
# Alternative titles are separated by "|"; the first is the one looked up.
BOOKS: list[tuple[str, str]] = [
    # Classics
    ("Pride and Prejudice", "Austen"),
    ("Jane Eyre", "Bronte"),
    ("Wuthering Heights", "Bronte"),
    ("Moby Dick", "Melville"),
    ("Great Expectations", "Dickens"),
    ("Frankenstein", "Shelley"),
    ("Dracula", "Stoker"),
    ("The Picture of Dorian Gray", "Wilde"),
    ("Crime and Punishment", "Dostoyevsky"),
    ("Anna Karenina", "Tolstoy"),
    ("Les Miserables", "Hugo"),
    ("The Count of Monte Cristo|Le Comte de Monte-Cristo", "Dumas"),
    ("Little Women", "Alcott"),
    ("The Adventures of Huckleberry Finn", "Twain"),
    ("Don Quixote", "Cervantes"),
    # Twentieth-century fiction
    ("The Great Gatsby", "Fitzgerald"),
    ("To Kill a Mockingbird", "Lee"),
    ("Nineteen Eighty-Four|1984", "Orwell"),
    ("Animal Farm", "Orwell"),
    ("Brave New World", "Huxley"),
    ("The Catcher in the Rye", "Salinger"),
    ("Of Mice and Men", "Steinbeck"),
    ("The Grapes of Wrath", "Steinbeck"),
    ("Lord of the Flies", "Golding"),
    ("Fahrenheit 451", "Bradbury"),
    ("Slaughterhouse-Five", "Vonnegut"),
    ("One Hundred Years of Solitude", "Garcia Marquez"),
    ("Beloved", "Morrison"),
    ("The Bell Jar", "Plath"),
    ("Catch-22", "Heller"),
    ("Lolita", "Nabokov"),
    ("On the Road", "Kerouac"),
    ("Things Fall Apart", "Achebe"),
    ("The Color Purple", "Walker"),
    ("Rebecca", "du Maurier"),
    # Contemporary fiction
    ("The Kite Runner", "Hosseini"),
    ("Life of Pi", "Martel"),
    ("The Road", "McCarthy"),
    ("The Book Thief", "Zusak"),
    ("Never Let Me Go", "Ishiguro"),
    ("The Remains of the Day", "Ishiguro"),
    ("Atonement", "McEwan"),
    ("The Help", "Stockett"),
    ("Where the Crawdads Sing", "Owens"),
    ("The Midnight Library", "Haig"),
    ("Normal People", "Rooney"),
    ("A Man Called Ove", "Backman"),
    ("A Dog's Purpose", "Cameron"),
    ("Water for Elephants", "Gruen"),
    ("The Lovely Bones", "Sebold"),
    # Fantasy
    ("The Hobbit", "Tolkien"),
    ("The Fellowship of the Ring|The Lord of the Rings", "Tolkien"),
    ("A Game of Thrones", "Martin"),
    ("The Name of the Wind", "Rothfuss"),
    ("Mistborn|The Final Empire", "Sanderson"),
    ("The Way of Kings", "Sanderson"),
    ("The Eye of the World", "Jordan"),
    ("American Gods", "Gaiman"),
    ("Good Omens", "Pratchett"),
    ("The Colour of Magic|The Color of Magic", "Pratchett"),
    ("A Wizard of Earthsea", "Le Guin"),
    ("The Lion, the Witch and the Wardrobe", "Lewis"),
    ("Harry Potter and the Philosopher's Stone|Harry Potter and the Sorcerer's Stone", "Rowling"),
    ("The Night Circus", "Morgenstern"),
    ("Circe", "Miller"),
    # Science fiction
    ("Dune", "Herbert"),
    ("Foundation", "Asimov"),
    ("Ender's Game", "Card"),
    ("Neuromancer", "Gibson"),
    ("The Left Hand of Darkness", "Le Guin"),
    ("Hyperion", "Simmons"),
    ("The Hitchhiker's Guide to the Galaxy", "Adams"),
    ("Do Androids Dream of Electric Sheep?", "Dick"),
    ("The Martian", "Weir"),
    ("Project Hail Mary", "Weir"),
    ("Ready Player One", "Cline"),
    ("The Three-Body Problem", "Liu"),
    ("Snow Crash", "Stephenson"),
    # Young adult and children's
    ("The Hunger Games", "Collins"),
    ("Divergent", "Roth"),
    ("Twilight", "Meyer"),
    ("The Fault in Our Stars", "Green"),
    ("The Lightning Thief|Percy Jackson and the Lightning Thief", "Riordan"),
    ("Charlotte's Web", "White"),
    ("Matilda", "Dahl"),
    ("Wonder", "Palacio"),
    ("The Giver", "Lowry"),
    ("Holes", "Sachar"),
    # Mystery, thriller and horror
    ("The Da Vinci Code", "Brown"),
    ("Gone Girl", "Flynn"),
    ("The Girl with the Dragon Tattoo", "Larsson"),
    ("And Then There Were None", "Christie"),
    ("The Hound of the Baskervilles", "Doyle"),
    ("The Silence of the Lambs", "Harris"),
    ("The Shining", "King"),
    ("It", "King"),
    ("The Girl on the Train", "Hawkins"),
    # Non-fiction
    ("Sapiens|Sapiens: A Brief History of Humankind", "Harari"),
    ("Educated", "Westover"),
    ("Becoming", "Obama"),
    ("The Diary of a Young Girl|Het Achterhuis|Diary of a Young Girl", "Frank"),
    ("A Brief History of Time", "Hawking"),
    ("Thinking, Fast and Slow", "Kahneman"),
    ("In Cold Blood", "Capote"),
    ("Atomic Habits", "Clear"),
]

# The same characters title search ignores, so "Ender's Game" matches
# however the apostrophe was stored.
_PUNCTUATION = "'’‘ʼ`´.,:;!?\"“”-‐–—()/"
_STRIP = str.maketrans("", "", _PUNCTUATION)


def normalise(title: str) -> str:
    """Lower case, no accents, no punctuation and no spaces, so "Moby-Dick",
    "Moby Dick" and "Les Mis\u00e9rables" / "Les Miserables" each meet."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(text.translate(_STRIP).lower().split())


def title_keys(title: str) -> set[str]:
    """The title, and its main title without a subtitle: "Sapiens: A Brief
    History...", "Moby-Dick; or, The Whale", "Dune (Dune Chronicles)"."""
    keys = {normalise(title)}
    for sep in (":", ";", "(", " - "):
        if sep in title:
            keys.add(normalise(title.split(sep, 1)[0]))
    return {k for k in keys if k}


def wanted_keys(titles: str) -> set[str]:
    """Every key a list entry accepts: each "|"-separated title."""
    keys: set[str] = set()
    for title in titles.split("|"):
        keys |= title_keys(title)
    return keys


def catalogue_titles(conn) -> set[str]:
    keys: set[str] = set()
    for (title,) in conn.execute(
        "SELECT title_romaji FROM media WHERE source = 'openlibrary'"
    ).fetchall():
        keys |= title_keys(title or "")
    return keys


def reason(work: dict | None, min_description: int = MIN_DESCRIPTION) -> str:
    """Why the import skips this work, or that it no longer does."""
    if work is None:
        return "not on Open Library"
    if row_for(work, min_description) is not None:
        return "qualifies now: re-run the import"
    description = clean_description(description_of(work))
    subjects = [str(s).strip() for s in (work.get("subjects") or []) if str(s).strip()]
    real = sum(1 for s in subjects if s.lower().strip(" .") not in NOISE_SUBJECTS)
    if not description:
        return f"no description on the work ({real} real subjects)"
    return f"description {len(description)} chars, {real} real subjects"


def find_work(client, title: str, author: str) -> dict | None:
    """The best-matching work on Open Library, or None."""
    resp = client.get(
        f"{API}/search.json",
        params={"title": title, "author": author, "fields": "key,title", "limit": 1},
    )
    resp.raise_for_status()
    docs = resp.json().get("docs") or []
    if not docs:
        return None
    time.sleep(INTERVAL)
    work = client.get(f"{API}{docs[0]['key']}.json")
    work.raise_for_status()
    return work.json()


def check(lookup: bool = True) -> dict:
    import httpx
    import psycopg

    from api.config import settings

    with psycopg.connect(settings.database_url) as conn:
        have = catalogue_titles(conn)
    missing = [(t, a) for t, a in BOOKS if not (wanted_keys(t) & have)]
    found = len(BOOKS) - len(missing)
    print(f"{found}/{len(BOOKS)} of the list are in the catalogue", flush=True)
    if not missing:
        return {"checked": len(BOOKS), "found": found, "missing": []}
    print(f"\nmissing ({len(missing)}):", flush=True)
    results = []
    headers = {"User-Agent": "MangaBrain/0.1 (self-hosted recommendation engine)"}
    with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
        for titles, author in missing:
            title = titles.split("|")[0]
            why = "-"
            if lookup:
                try:
                    why = reason(find_work(client, title, author))
                except httpx.HTTPError as exc:
                    why = f"lookup failed ({type(exc).__name__})"
                time.sleep(INTERVAL)
            print(f"  {title} ({author}): {why}", flush=True)
            results.append({"title": title, "author": author, "reason": why})
    return {"checked": len(BOOKS), "found": found, "missing": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check well-known books are in the catalogue")
    parser.add_argument(
        "--no-lookup", action="store_true", help="list what is missing without asking Open Library"
    )
    args = parser.parse_args()
    check(lookup=not args.no_lookup)


if __name__ == "__main__":
    main()
