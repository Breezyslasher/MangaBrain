"""Read Kitsu's OpenAPI spec and report exactly how casts are addressed.

The spec is not served as a file. Every obvious path - /openapi.json,
/openapi.yaml, /swagger.json - returns 404, and the openapi3 branch of
hummingbird-me/api-docs is still a stub (its base.yml is 25 bytes). But the
Redoc page at hummingbird-me.github.io/api-docs/ embeds the whole document
inline as `const __redoc_state = {...}`, so it is extracted from there.

Beware the name collision that cost this investigation a detour:
api-docs.kitsu.cloud documents CGWire's Kitsu, an animation production
tracker whose "casting" means assigning assets to shots. The anime site is
kitsu.io, documented by hummingbird-me.

Outcome: the spec cannot settle the cast questions. It documents 8 paths -
anime, manga, episodes, search and little else - and none of the cast
resources, exactly as the repo README warns while the conversion from API
Blueprint is unfinished. The legacy Apiary docs that did cover them no
longer resolve at any host. So the live API is the only authority, and
pipeline.probe_kitsu_shape asks it.

What the spec does give, and it is worth keeping:
  - page[limit] is capped at 20, "some routes may increase the limit"
  - `include=[relationship]`, successive relationships joined by `.`, and a
    comma list for several - so include=voices.person is valid
  - the server is https://kitsu.io/api/edge
"""

import argparse
import json
import re

import httpx

DOCS = "https://hummingbird-me.github.io/api-docs/"
STATE_RE = re.compile(r"const __redoc_state\s*=\s*", re.S)

CAST_HINTS = ("casting", "character", "voice", "mapping", "person")


def extract_spec(html: str) -> dict | None:
    """Pull the inline __redoc_state JSON out of the Redoc page."""
    match = STATE_RE.search(html)
    if not match:
        return None
    start = html.find("{", match.end())
    if start < 0:
        return None
    # Brace-count rather than regex: the document is ~1.9 MB of nested JSON
    # and contains braces inside strings.
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : i + 1])
                except ValueError as exc:
                    print(f"  could not parse extracted JSON: {exc}", flush=True)
                    return None
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Read Kitsu's OpenAPI spec")
    parser.add_argument("--all-paths", action="store_true", help="list every path")
    args = parser.parse_args()

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        resp = client.get(DOCS)
        resp.raise_for_status()
        print(f"fetched {len(resp.text)} bytes from {DOCS}", flush=True)
        state = extract_spec(resp.text)

    if not state:
        raise SystemExit("could not find __redoc_state in the page")
    spec = ((state.get("spec") or {}).get("data")) or {}
    info = spec.get("info") or {}
    print(f"spec: {info.get('title')} version {info.get('version')}", flush=True)
    servers = [s.get("url") for s in spec.get("servers") or []]
    print(f"servers: {servers}", flush=True)

    desc = info.get("description") or ""
    for line in desc.splitlines():
        if any(w in line.lower() for w in ("paginat", "rate limit", "maximum", "limit")):
            print(f"  doc: {line.strip()[:220]}", flush=True)

    paths = spec.get("paths") or {}
    print(f"{len(paths)} documented paths", flush=True)

    cast_paths = sorted(p for p in paths if any(h in p.lower() for h in CAST_HINTS))
    print("=" * 72)
    print(f"cast-related paths ({len(cast_paths)})")
    print("=" * 72)
    for path in cast_paths:
        for method, op in (paths[path] or {}).items():
            if not isinstance(op, dict):
                continue
            flag = "  [DEPRECATED]" if op.get("deprecated") else ""
            print(f"{method.upper():<6} {path}{flag}", flush=True)
            summary = op.get("summary") or op.get("operationId")
            if summary:
                print(f"       {summary}", flush=True)
            names = []
            for param in op.get("parameters") or []:
                name = param.get("name")
                if not name:
                    ref = param.get("$ref")
                    if ref:
                        names.append(f"$ref:{ref.rsplit('/', 1)[-1]}")
                    continue
                names.append(name)
            filters = [n for n in names if "filter" in n.lower()]
            others = [n for n in names if "filter" not in n.lower()]
            if filters:
                print(f"       filters: {filters}", flush=True)
            if others:
                print(f"       other params: {others[:14]}", flush=True)

    if args.all_paths:
        print("=" * 72)
        print("every path")
        print("=" * 72)
        for path in sorted(paths):
            methods = ",".join(
                m.upper() for m, o in (paths[path] or {}).items() if isinstance(o, dict)
            )
            print(f"  {methods:<12} {path}", flush=True)


if __name__ == "__main__":
    main()
