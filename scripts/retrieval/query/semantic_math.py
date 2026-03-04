from __future__ import annotations

import math


def min_max_normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lower = min(values)
    upper = max(values)
    if abs(upper - lower) < 1e-12:
        return [1.0 for _ in values]
    return [(value - lower) / (upper - lower) for value in values]


def dot(left: list[float], right: list[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right, strict=False)))


def l2_norm(values: list[float]) -> float:
    return math.sqrt(sum(value * value for value in values))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    left_norm = l2_norm(left)
    right_norm = l2_norm(right)
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot(left, right) / (left_norm * right_norm)
