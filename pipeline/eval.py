"""Batch-run recommendations against a running MangaBrain API and print
compact ranked tables, for eyeballing quality against reference engines
(e.g. AniBrain pairings like Jujutsu Kaisen -> Blue Exorcist ~93%).

Inside the compose network the API is http://api:8000 (the default). From
the host, pass the published port, e.g. --base-url http://localhost:8009.

Usage:
    python -m pipeline.eval
    python -m pipeline.eval --seed anime:"Hunter x Hunter" --seed manga:Berserk
    python -m pipeline.eval --include-franchise --top 15
"""

import argparse

import httpx

DEFAULT_SEEDS = [
    ("anime", "Jujutsu Kaisen"),
    ("anime", "Death Note"),
    ("anime", "Steins;Gate"),
    ("anime", "Cowboy Bebop"),
    ("anime", "Violet Evergarden"),
    ("anime", "Made in Abyss"),
    ("manga", "Berserk"),
    ("manga", "Monster"),
    ("manga", "Solo Leveling"),
    ("manga", "Tower of God"),
    ("light_novel", "Mushoku Tensei"),
]


def show_seed(
    client: httpx.Client, medium: str, name: str, top: int, exclude_franchise: bool
) -> None:
    search = client.get("/search", params={"q": name, "medium": medium, "limit": 1}).json()
    results = search.get("results") or []
    if not results:
        print(f"== {name} ({medium}): NOT FOUND in catalog ==\n")
        return
    seed = results[0]
    rec = client.get(
        f"/recommend/{seed['id']}",
        params={
            "limit": top,
            "exclude_franchise": str(exclude_franchise).lower(),
        },
    )
    if rec.status_code != 200:
        detail = rec.json().get("detail", rec.text)
        print(f"== {seed.get('title') or name} ({medium}): ERROR {rec.status_code}: {detail} ==\n")
        return
    data = rec.json()
    seed_media = data["seed"]
    title = seed_media.get("title") or name
    print(f"== {title} ({seed_media.get('start_year')}, {medium}) ==")
    for i, item in enumerate(data["results"], start=1):
        m = item["media"]
        comp = item["components"]
        name_out = m.get("title") or m.get("title_english") or f"#{m['id']}"
        extra = f"{m.get('start_year') or '????'} {m.get('medium')}"
        print(
            f"  {i:2d}. {item['similarity']:5.1f}%  {name_out}  ({extra})"
            f"  [s {comp['semantic']:.2f} t {comp['tags']:.2f} g {comp['genres']:.2f}]"
        )
    related = ", ".join(
        r["media"].get("title") or str(r["media"]["id"]) for r in data.get("related", [])
    )
    if related:
        print(f"  related (excluded): {related}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-eval recommendations")
    parser.add_argument("--base-url", default="http://api:8000")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument(
        "--include-franchise",
        action="store_true",
        help="keep sequels/spin-offs in results (hidden by default for eval clarity)",
    )
    parser.add_argument(
        "--seed",
        action="append",
        default=None,
        metavar="MEDIUM:TITLE",
        help="seed as medium:title (repeatable); default is a built-in list",
    )
    args = parser.parse_args()

    seeds = DEFAULT_SEEDS
    if args.seed:
        seeds = []
        for raw in args.seed:
            medium, _, name = raw.partition(":")
            if not name:
                parser.error(f"--seed must be MEDIUM:TITLE, got {raw!r}")
            seeds.append((medium.strip(), name.strip()))

    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        for medium, name in seeds:
            show_seed(client, medium, name, args.top, not args.include_franchise)


if __name__ == "__main__":
    main()
