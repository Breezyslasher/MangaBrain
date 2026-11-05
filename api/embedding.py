"""Query-time embedding: turning a typed description into a vector.

The rest of the API never needs this. Catalog embeddings are produced by
the worker, and /recommend starts from a vector already in the database, so
the API container runs without torch at all - which is the point of keeping
this in one module, imported lazily, instead of at the top of the app.

The first /describe request on a cold process pays the model load (seconds,
and a few hundred megabytes resident for the life of the process). Every
request after it pays only the encode, which is milliseconds for a handful
of short strings. A server whose owner never uses description search never
pays either.
"""

import threading

from api.config import settings

_lock = threading.Lock()
_model = None


def encode_queries(texts: list[str]):
    """Embed query strings into the catalog's embedding space.

    normalize_embeddings mirrors pipeline.embed: stored vectors are unit
    length, and mixing normalized with unnormalized would make cosine
    distance disagree with the index the search runs against.
    """
    return _get_model().encode(texts, normalize_embeddings=True)


def _get_model():
    """Load once per process.

    Double-checked under a lock because FastAPI runs sync endpoints in a
    thread pool: two requests arriving together on a cold process would
    otherwise each load their own copy of the model, and the loser's copy
    would be garbage after paying full price for it.
    """
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                _model = _load()
    return _model


def _load():
    import os

    import torch
    from sentence_transformers import SentenceTransformer

    from pipeline.embed import torch_thread_count

    # Same cap the embed pass uses: all cores but one, so a description
    # search cannot make the rest of the box unresponsive.
    torch.set_num_threads(torch_thread_count(settings.embed_threads, os.cpu_count()))
    return SentenceTransformer(settings.embed_model)


def is_loaded() -> bool:
    """Whether the model is resident, for callers that report readiness."""
    return _model is not None
