from __future__ import annotations

import os
from pathlib import Path


def get_pulse_dir() -> Path:
    """Return the .pulse data directory, honouring PULSE_ROOT env var.

    On Render/Railway, set PULSE_ROOT to the persistent volume mount path
    (e.g. /data/pulse). Locally it falls back to .pulse/ in the CWD.
    """
    root = os.environ.get("PULSE_ROOT")
    return Path(root) / ".pulse" if root else Path(".pulse")
