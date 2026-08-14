import numpy as np

from api.routers.recommend import _embedding_array


class FakeVector:
    """Mimics pgvector's Vector object, which is not float-convertible."""

    def __init__(self, values):
        self._values = values

    def to_numpy(self):
        return np.array(self._values)


def test_handles_pgvector_vector_objects():
    arr = _embedding_array(FakeVector([1.0, 2.0, 3.0]))
    assert arr.dtype == np.float32
    assert arr.tolist() == [1.0, 2.0, 3.0]


def test_handles_plain_arrays_and_lists():
    assert _embedding_array(np.array([0.5, 0.25])).tolist() == [0.5, 0.25]
    assert _embedding_array([0.5, 0.25]).dtype == np.float32
