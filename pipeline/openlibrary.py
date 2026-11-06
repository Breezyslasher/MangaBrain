"""Reading Open Library's works dump.

Shared by pipeline.survey_openlibrary, which counts what is there, and
pipeline.sync_openlibrary, which imports it. One reader for both, so
the importer walks exactly what the survey measured.

Measured while building the survey: the dump is 4.08 GB compressed and
arrives at roughly 388 KB/s, and Open Library closed the connection
31% of the way through a full pass. Hence the resume.
"""

import gzip
import io
import json
import os
from pathlib import Path

import httpx

DUMP_URL = "https://openlibrary.org/data/ol_dump_works_latest.txt.gz"

# Where the dump is kept between runs. Caching it is the difference
# between a restart costing minutes and costing hours, and the reason is
# not the parsing - it is that the dump is one gzip stream, so there is
# no seeking into it. A row-number resume would still have to pull every
# byte before the resume point at 388 KB/s. Decompressing the same bytes
# off local disk runs at over 100 MB/s, which is what makes re-reaching
# row 25,000,000 a minute rather than an hour and three quarters.
#
# It also makes the download itself resumable across restarts, which it
# was not: a dropped container meant starting the 4 GB fetch again.
#
# Default under /root/.cache/mangabrain, which the compose files mount
# as a named volume so it survives a container being recreated. An
# override is here because this path is wrong anywhere but a container.
DUMP_CACHE = Path(
    os.environ.get("OPENLIBRARY_DUMP_CACHE") or "/root/.cache/mangabrain/openlibrary-works.txt.gz"
)

# The same floor the source probe used, and for the same reason: below
# this a description is a tagline, not something to embed.
MIN_DESCRIPTION = 200

# 200 characters was chosen to exclude taglines, not to select good
# records, and the difference matters: at 2.32% of a ~40M-work dump the
# catalogue is around 930,000 books, which is 5.6x the anime and manga
# catalogue combined and more vector than a single release asset can
# hold. Where to put the floor is a judgement about result quality, and
# it should be made against numbers rather than re-run for hours per
# guess. Each pass now counts every combination.
THRESHOLDS = [(200, 1), (200, 3), (400, 1), (400, 3), (600, 1), (600, 3), (1000, 3)]


class ChunkReader(io.RawIOBase):
    """A file-like view over httpx's chunk iterator.

    gzip.GzipFile wants an object with .read(); iter_raw() yields bytes.
    Passing the generator straight in raised "'generator' object has no
    attribute 'read'" on the first byte of a multi-gigabyte download,
    which is a cheap failure but only at runtime - hence the test that
    drives real gzip bytes through this in chunks.
    """

    def __init__(self, chunks) -> None:
        self._chunks = iter(chunks)
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def readinto(self, target) -> int:
        while not self._buffer:
            try:
                self._buffer = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(target), len(self._buffer))
        target[:size] = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return size


def description_of(work: dict) -> str:
    """Open Library stores a description as a string or as {value: ...}."""
    value = work.get("description")
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def download_chunks(url: str, max_attempts: int = 8, start: int = 0, totals: dict | None = None):
    """Raw bytes of the dump, continuing across dropped connections.

    Measured: Open Library closed the connection 1,258,174,288 bytes
    into a 4,078,855,962-byte file, 54 minutes in, and the whole survey
    was lost with it. A Range request resumes from the last byte
    received, and because the caller keeps one decompressor across the
    whole generator the gzip stream never notices the seam.

    If the server ignores Range and answers 200, resuming would feed
    the file twice into a decompressor expecting the middle of it, so
    that is refused rather than silently corrupting the count.
    """
    # start > 0 resumes a file already on disk, so the very first request
    # carries a Range header and the 206 check below applies to it.
    offset = start
    attempts = 0
    while True:
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with httpx.stream(
                "GET", url, headers=headers, timeout=120.0, follow_redirects=True
            ) as resp:
                resp.raise_for_status()
                if offset and resp.status_code != 206:
                    print(f"  server ignored Range (HTTP {resp.status_code}); cannot resume")
                    return
                if totals is not None and "total" not in totals:
                    # A 206 states the whole size after the slash in
                    # Content-Range; a 200 states it in Content-Length.
                    # Without it there is no way to tell a finished
                    # download from a truncated one.
                    span = resp.headers.get("content-range", "")
                    length = resp.headers.get("content-length", "")
                    if "/" in span and span.rsplit("/", 1)[1].isdigit():
                        totals["total"] = int(span.rsplit("/", 1)[1])
                    elif length.isdigit():
                        totals["total"] = offset + int(length)
                for chunk in resp.iter_raw():
                    offset += len(chunk)
                    yield chunk
                return
        except httpx.HTTPError as exc:
            attempts += 1
            if attempts > max_attempts:
                print(f"  giving up at {offset:,} bytes after {attempts} attempts: {exc}")
                return
            print(
                f"  connection lost at {offset:,} bytes; resuming (attempt {attempts})",
                flush=True,
            )


def ensure_dump(path: Path, url: str = DUMP_URL) -> tuple[Path, bool]:
    """The dump on local disk, downloading or resuming it as needed.

    Returns (path, complete). An incomplete file is still returned and
    still readable - a partial pass is a partial result, as it always
    was - but the caller is told, because a truncated count that prints
    as a complete one is the quiet kind of wrong this project keeps
    paying for.

    Completion is decided by size against the length the server states,
    and the finished file is only moved into place once it matches. A
    partial download therefore lives under .part, where the next run
    resumes it rather than starting the 4 GB fetch again.
    """
    if path.exists():
        print(f"  using the cached dump at {path} ({path.stat().st_size:,} bytes)")
        return path, True

    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if offset:
        print(f"  resuming the cached dump at {offset:,} bytes", flush=True)
    else:
        print(f"  caching the dump to {path} (about 4 GB, once)", flush=True)

    totals: dict = {}
    with part.open("ab") as fh:
        for chunk in download_chunks(url, start=offset, totals=totals):
            fh.write(chunk)

    size = part.stat().st_size
    total = totals.get("total")
    if total and size < total:
        print(f"  dump incomplete ({size:,}/{total:,} bytes); the next run resumes it")
        return part, False
    if not total:
        # Nothing to check against. Treated as incomplete rather than
        # renamed, so an unverifiable download is never cached as good.
        print(f"  dump size unconfirmed at {size:,} bytes; not caching it as complete")
        return part, False
    part.rename(path)
    print(f"  cached the dump ({size:,} bytes)")
    return path, True


def iter_works(
    limit: int | None = None,
    status: dict | None = None,
    cache: Path | None = DUMP_CACHE,
):
    """Parsed work records from the dump, in dump order.

    Yields (row_number, work). A stream that ends early stops the
    generator rather than raising: a partial pass is a partial result,
    not a failure.

    The caller passes `status` to learn that it happened. Swallowing the
    error without a way to report it would be worse than raising - a
    truncated count would print as a complete one, which is the quiet
    kind of wrong this project keeps paying for.
    """
    reader = None
    if cache is not None:
        # A cache that cannot be written is not a failure, it is just no
        # cache. The default path only exists inside the container; on a
        # CI runner /root is not writable, and a survey that died on
        # that would be a worse outcome than one that streams.
        try:
            path, complete = ensure_dump(Path(cache))
            if not complete and status is not None:
                status["truncated"] = f"cached dump incomplete at {path.stat().st_size:,} bytes"
            reader = path.open("rb")
        except OSError as exc:
            print(f"  no dump cache ({type(exc).__name__}: {exc}); streaming instead")
    if reader is None:
        reader = io.BufferedReader(ChunkReader(download_chunks(DUMP_URL)), buffer_size=1 << 20)
    rows = 0
    try:
        with gzip.GzipFile(fileobj=reader, mode="rb") as stream:
            for raw in stream:
                rows += 1
                if limit and rows > limit:
                    return
                # Five tab-separated columns; the JSON is the last.
                parts = raw.decode("utf-8", "replace").rstrip("\n").split("\t")
                if len(parts) < 5:
                    continue
                try:
                    yield rows, json.loads(parts[4])
                except ValueError:
                    continue
    except (OSError, EOFError, httpx.HTTPError) as exc:
        if status is not None:
            status["truncated"] = f"{type(exc).__name__}: {exc}"
        print(f"\n  stream ended early ({type(exc).__name__}); stopping at row {rows:,}")
    finally:
        # The streaming reader wrapped a generator and needed no closing.
        # A cached run holds an open file, and this is a generator, so an
        # early return from the caller lands here too.
        reader.close()
