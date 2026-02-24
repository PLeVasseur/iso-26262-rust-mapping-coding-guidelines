from __future__ import annotations


def init_retrieval_timing() -> dict[str, float]:
    return {
        "preflight_ms": 0.0,
        "lexical_ms": 0.0,
        "semantic_embed_ms": 0.0,
        "semantic_score_ms": 0.0,
        "rerank_ms": 0.0,
        "projection_ms": 0.0,
    }


def init_retrieval_workload() -> dict[str, int]:
    return {
        "lexical_pool_size": 0,
        "semantic_pool_size": 0,
        "union_pool_size": 0,
        "rerank_pool_size": 0,
        "rerank_doc_count": 0,
    }


def timing_payload(timing: dict[str, float], total_case_ms: float) -> dict[str, float]:
    payload = {name: round(float(value), 3) for name, value in timing.items()}
    payload["total_case_ms"] = round(float(total_case_ms), 3)
    return payload
