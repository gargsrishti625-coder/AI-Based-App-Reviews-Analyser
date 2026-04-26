"""HDBSCAN clustering wrapper."""
from __future__ import annotations

import numpy as np


def cluster(X: np.ndarray, min_cluster_size: int | None = None) -> np.ndarray:
    """Fit HDBSCAN on (N, D) array; return integer label array (-1 = noise).

    min_cluster_size defaults to max(5, N // 20) when not supplied.
    core_dist_n_jobs=1 keeps results deterministic across machines.
    """
    import hdbscan  # lazy import

    n = X.shape[0]
    mcs = min_cluster_size if min_cluster_size is not None else max(5, n // 20)
    # HDBSCAN requires at least min_cluster_size samples; return all-noise for tiny inputs.
    if n < mcs:
        return np.full(n, -1, dtype=np.int32)
    clusterer = hdbscan.HDBSCAN(min_cluster_size=mcs, core_dist_n_jobs=1)
    clusterer.fit(X)
    return clusterer.labels_.astype(np.int32)
