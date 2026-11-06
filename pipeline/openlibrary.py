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

import httpx

DUMP_URL = "https://openlibrary.org/data/ol_dump_works_latest.txt.gz"

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


def download_chunks(url: str, max_attempts: int = 8):
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
    offset = 0
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


def iter_works(limit: int | None = None, status: dict | None = None):
    """Parsed work records from the dump, in dump order.

    Yields (row_number, work). A stream that ends early stops the
    generator rather than raising: a partial pass is a partial result,
    not a failure.

    The caller passes `status` to learn that it happened. Swallowing the
    error without a way to report it would be worse than raising - a
    truncated count would print as a complete one, which is the quiet
    kind of wrong this project keeps paying for.
    """
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
