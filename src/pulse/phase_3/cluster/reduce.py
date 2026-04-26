"""UMAP dimensionality reduction wrapper."""
from __future__ import annotations

import numpy as np


def umap_reduce(
    X: np.ndarray,
    n_components: int = 15,
    n_neighbors: int = 15,
    random_state: int = 42,
) -> np.ndarray:
    """Reduce (N, D) embeddings to (N, n_components) via UMAP.

    n_jobs=1 is required for cross-platform determinism (UMAP's own guidance).
    When the data is already smaller than the target dimensionality (e.g. tiny
    synthetic test corpora), the original array is returned unchanged so
    downstream code never sees a shape surprise.
    """
    n_samples, n_feats = X.shape
    if n_samples <= n_components or n_feats <= n_components:
        return X

    from umap import UMAP  # lazy import — avoid paying startup cost in non-cluster paths

    reducer = UMAP(
        n_components=n_components,
        n_neighbors=min(n_neighbors, n_samples - 1),
        random_state=random_state,
        n_jobs=1,
    )
    return reducer.fit_transform(X).astype(np.float32)
