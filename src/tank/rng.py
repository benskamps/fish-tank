"""Deterministic seeded RNG. Same inputs -> same outputs across runs."""
from __future__ import annotations

import hashlib
import random


def seeded(*parts) -> random.Random:
    """Build a Random seeded by a stable hash of the parts."""
    blob = "|".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    return random.Random(int(digest[:16], 16))
