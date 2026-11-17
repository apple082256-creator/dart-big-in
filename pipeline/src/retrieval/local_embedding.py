from __future__ import annotations

import hashlib
import math
import re

import numpy as np


MODEL_NAME = "local-ko-en-finance-hashing-v1"
DIMENSIONS = 1536
TOKEN_PATTERN = re.compile(r"[가-힣]+|[A-Za-z]+|[0-9]+(?:\.[0-9]+)?")


def _features(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    words = TOKEN_PATTERN.findall(normalized)
    features = ["w:" + word for word in words]
    features.extend("b:" + words[i] + "_" + words[i + 1] for i in range(len(words) - 1))
    compact = re.sub(r"[^가-힣a-z0-9]", "", normalized)
    for size in (2, 3, 4):
        features.extend(f"c{size}:" + compact[i:i + size] for i in range(max(0, len(compact) - size + 1)))
    return features


def embed_text(text: str, dimensions: int = DIMENSIONS) -> np.ndarray:
    vector = np.zeros(dimensions, dtype=np.float32)
    counts: dict[tuple[int, int], int] = {}
    for feature in _features(text):
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        key = (value % dimensions, 1 if value & (1 << 63) else -1)
        counts[key] = counts.get(key, 0) + 1
    for (index, sign), count in counts.items():
        vector[index] += sign * (1.0 + math.log(count))
    norm = float(np.linalg.norm(vector))
    if norm:
        vector /= norm
    return vector
