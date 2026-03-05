from __future__ import annotations

from retrieval.writer_host.fusion import fuse_ranked_lists


def test_rrf_fusion_prefers_cross_mode_overlap() -> None:
    lexical = [
        {"statement_id": "s1", "source_anchor": "a1", "score": 0.9},
        {"statement_id": "s2", "source_anchor": "a2", "score": 0.8},
    ]
    semantic = [
        {"statement_id": "s3", "source_anchor": "a3", "score": 0.99},
        {"statement_id": "s1", "source_anchor": "a1", "score": 0.6},
    ]
    hybrid = [
        {"statement_id": "s1", "source_anchor": "a1", "score": 0.7},
        {"statement_id": "s4", "source_anchor": "a4", "score": 0.65},
    ]

    selected, decision = fuse_ranked_lists(
        ranked_rows_by_mode={"lexical": lexical, "semantic": semantic, "hybrid": hybrid},
        rrf_k=60,
        rank_window=50,
        top_n=3,
    )

    assert decision["selected_count"] == 3
    assert selected[0]["statement_id"] == "s1"
    assert selected[0]["coverage"] == 3


def test_rrf_fusion_respects_anchor_cap() -> None:
    lexical = [
        {"statement_id": "s1", "source_anchor": "same", "score": 0.9},
        {"statement_id": "s2", "source_anchor": "same", "score": 0.8},
        {"statement_id": "s3", "source_anchor": "same", "score": 0.7},
    ]
    selected, _ = fuse_ranked_lists(
        ranked_rows_by_mode={"lexical": lexical},
        rrf_k=60,
        rank_window=10,
        top_n=5,
    )
    assert len([row for row in selected if row.get("source_anchor") == "same"]) == 2
