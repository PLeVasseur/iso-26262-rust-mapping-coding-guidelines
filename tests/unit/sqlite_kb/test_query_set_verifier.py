from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
THIS_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from _fixture import create_reference_fixture  # noqa: E402

from semantic_backend_client import SemanticBackendConfig  # noqa: E402
from sqlite_build_rust_reference import (  # noqa: E402
    DEFAULT_EXTRACTOR_DB,
    DEFAULT_TABLE_NODE_ID,
    build_rust_reference_db,
)
from sqlite_eval_rust_reference_retrieval import evaluate_retrieval_prompts  # noqa: E402


class RetrievalEvalVerifierTests(unittest.TestCase):
    def _build_fixture_db(self, temp_root: Path) -> Path:
        db_path = temp_root / "current" / "rust_reference.sqlite"
        snapshot_root = temp_root / "snapshots"
        manifest_path = temp_root / "manifest.yaml"
        reference_source_dir = create_reference_fixture(temp_root)

        build_rust_reference_db(
            db_path=db_path,
            snapshot_root=snapshot_root,
            manifest_path=manifest_path,
            extractor_db=DEFAULT_EXTRACTOR_DB,
            table_node_id=DEFAULT_TABLE_NODE_ID,
            reference_source_dir=reference_source_dir,
            reference_revision="fixture-001",
            min_sections=4,
            min_statements=8,
            min_mechanisms=4,
        )
        return db_path

    def test_retrieval_eval_runs_lexical_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            db_path = self._build_fixture_db(temp_root)
            contract_path = ROOT / "config" / "sqlite_query_contracts" / "rust_reference.yaml"
            query_log_root = temp_root / "query_logs"

            prompts = [
                {
                    "prompt_id": "P-DEFENSIVE",
                    "slice": "issue_identification",
                    "query_text": "defensive error result option",
                    "modes": ["lexical"],
                    "expected_row_markers": ["1d"],
                    "relevant_statement_ids": [],
                    "relevant_anchor_prefixes": [],
                    "relevant_terms": ["defensive", "error", "result", "option"],
                    "hard_negative_statement_ids": [],
                    "semantic_focus": False,
                },
                {
                    "prompt_id": "P-CONCURRENCY",
                    "slice": "resolution_identification",
                    "query_text": "send sync thread concurrency",
                    "modes": ["lexical"],
                    "expected_row_markers": ["1h"],
                    "relevant_statement_ids": [],
                    "relevant_anchor_prefixes": [],
                    "relevant_terms": ["send", "sync", "thread", "concurrency"],
                    "hard_negative_statement_ids": [],
                    "semantic_focus": False,
                },
            ]

            report = evaluate_retrieval_prompts(
                db_path=db_path,
                contract_path=contract_path,
                query_log_root=query_log_root,
                prompts=prompts,
                top_k=5,
                candidate_limit=200,
                allow_degraded=False,
                semantic_config=SemanticBackendConfig(
                    base_url="http://127.0.0.1:1",
                    embed_model_id="unused",
                    reranker_model_id="unused",
                    timeout_sec=0.5,
                ),
                semantic_retries=0,
                enforce_gates=False,
            )

            self.assertEqual(report["summary"]["failed_cases"], 0)
            self.assertEqual(report["summary"]["total_mode_cases"], 2)

    def test_degraded_semantic_does_not_fail_delta_gate(self) -> None:
        prompts = [
            {
                "prompt_id": "P-DEGRADED-DELTA",
                "slice": "issue_identification",
                "query_text": "defensive coding",
                "modes": ["lexical", "semantic", "hybrid"],
                "expected_row_markers": ["1d"],
                "relevant_statement_ids": ["stmt-1"],
                "relevant_anchor_prefixes": [],
                "relevant_terms": [],
                "hard_negative_statement_ids": [],
                "semantic_focus": True,
            }
        ]

        def _fake_execute_retrieval_query(*, mode: str, **_: object) -> dict[str, object]:
            degraded = mode in {"semantic", "hybrid"}
            return {
                "requested_mode": mode,
                "executed_mode": "lexical" if degraded else mode,
                "degraded": degraded,
                "rows": [
                    {
                        "statement_id": "stmt-1",
                        "statement_text": "defensive coding",
                        "row_markers": ["1d"],
                        "source_anchor": "doc::x",
                    }
                ],
            }

        with patch(
            "sqlite_eval_rust_reference_retrieval.execute_retrieval_query",
            side_effect=_fake_execute_retrieval_query,
        ):
            report = evaluate_retrieval_prompts(
                db_path=Path("unused.sqlite"),
                contract_path=Path("unused.yaml"),
                query_log_root=Path("unused"),
                prompts=prompts,
                top_k=1,
                candidate_limit=10,
                allow_degraded=True,
                semantic_config=SemanticBackendConfig(
                    base_url="http://127.0.0.1:1",
                    embed_model_id="unused",
                    reranker_model_id="unused",
                    timeout_sec=0.1,
                ),
                semantic_retries=0,
                enforce_gates=True,
            )

        self.assertEqual(report["summary"]["failed_cases"], 0)
        self.assertEqual(report["summary"]["degraded_mode_cases"]["semantic"], 1)
        self.assertNotIn("semantic_vs_lexical", " ".join(report["gate_failures"]))

    def test_hybrid_gate_detects_regression_vs_best_single(self) -> None:
        prompts = [
            {
                "prompt_id": "P-HYBRID-GATE",
                "slice": "issue_identification",
                "query_text": "defensive coding",
                "modes": ["lexical", "semantic", "hybrid"],
                "expected_row_markers": ["1d"],
                "relevant_statement_ids": ["stmt-good"],
                "relevant_anchor_prefixes": [],
                "relevant_terms": [],
                "hard_negative_statement_ids": [],
                "semantic_focus": False,
                "min_metrics": {},
            }
        ]

        def _fake_execute_retrieval_query(*, mode: str, **_: object) -> dict[str, object]:
            if mode in {"lexical", "semantic"}:
                rows = [
                    {
                        "statement_id": "stmt-good",
                        "statement_text": "defensive coding",
                        "row_markers": ["1d"],
                        "source_anchor": "doc::good",
                    }
                ]
            else:
                rows = [
                    {
                        "statement_id": "stmt-bad",
                        "statement_text": "unrelated",
                        "row_markers": ["1a"],
                        "source_anchor": "doc::bad",
                    }
                ]
            return {
                "requested_mode": mode,
                "executed_mode": mode,
                "degraded": False,
                "rows": rows,
                "semantic_retry_events": [],
            }

        with patch(
            "sqlite_eval_rust_reference_retrieval.execute_retrieval_query",
            side_effect=_fake_execute_retrieval_query,
        ):
            report = evaluate_retrieval_prompts(
                db_path=Path("unused.sqlite"),
                contract_path=Path("unused.yaml"),
                query_log_root=Path("unused"),
                prompts=prompts,
                top_k=1,
                candidate_limit=10,
                allow_degraded=False,
                semantic_config=SemanticBackendConfig(
                    base_url="http://127.0.0.1:1",
                    embed_model_id="unused",
                    reranker_model_id="unused",
                    timeout_sec=0.1,
                ),
                semantic_retries=0,
                enforce_gates=True,
            )

        gate_blob = " ".join(report["gate_failures"])
        self.assertIn("hybrid_vs_best_single", gate_blob)
        self.assertGreater(report["summary"]["failed_cases"], 0)

    def test_slice_level_gate_reports_resolution_shortfall(self) -> None:
        prompts = [
            {
                "prompt_id": "P-SLICE-ISSUE",
                "slice": "issue_identification",
                "query_text": "issue",
                "modes": ["lexical"],
                "expected_row_markers": ["1d"],
                "relevant_statement_ids": ["stmt-issue"],
                "relevant_anchor_prefixes": [],
                "relevant_terms": [],
                "hard_negative_statement_ids": [],
                "semantic_focus": False,
                "min_metrics": {},
            },
            {
                "prompt_id": "P-SLICE-RESOLUTION",
                "slice": "resolution_identification",
                "query_text": "resolution",
                "modes": ["lexical"],
                "expected_row_markers": ["1h"],
                "relevant_statement_ids": ["stmt-resolution"],
                "relevant_anchor_prefixes": [],
                "relevant_terms": [],
                "hard_negative_statement_ids": [],
                "semantic_focus": False,
                "min_metrics": {},
            },
        ]

        def _fake_execute_retrieval_query(*, query_text: str, **_: object) -> dict[str, object]:
            if query_text == "issue":
                rows = [
                    {
                        "statement_id": "stmt-issue",
                        "statement_text": "issue",
                        "row_markers": ["1d"],
                        "source_anchor": "doc::issue",
                    }
                ]
            else:
                rows = [
                    {
                        "statement_id": "stmt-wrong",
                        "statement_text": "wrong",
                        "row_markers": ["1a"],
                        "source_anchor": "doc::wrong",
                    }
                ]
            return {
                "requested_mode": "lexical",
                "executed_mode": "lexical",
                "degraded": False,
                "rows": rows,
                "semantic_retry_events": [],
            }

        with patch(
            "sqlite_eval_rust_reference_retrieval.execute_retrieval_query",
            side_effect=_fake_execute_retrieval_query,
        ):
            report = evaluate_retrieval_prompts(
                db_path=Path("unused.sqlite"),
                contract_path=Path("unused.yaml"),
                query_log_root=Path("unused"),
                prompts=prompts,
                top_k=1,
                candidate_limit=10,
                allow_degraded=False,
                semantic_config=SemanticBackendConfig(
                    base_url="http://127.0.0.1:1",
                    embed_model_id="unused",
                    reranker_model_id="unused",
                    timeout_sec=0.1,
                ),
                semantic_retries=0,
                enforce_gates=True,
            )

        gate_blob = " ".join(report["gate_failures"])
        self.assertIn("slice.resolution_identification.lexical.row_hit_rate", gate_blob)

    def test_prompt_metric_override_can_fail_gate(self) -> None:
        prompts = [
            {
                "prompt_id": "P-OVERRIDE",
                "slice": "issue_identification",
                "query_text": "override",
                "modes": ["lexical"],
                "expected_row_markers": ["1d"],
                "relevant_statement_ids": ["stmt-override"],
                "relevant_anchor_prefixes": [],
                "relevant_terms": [],
                "hard_negative_statement_ids": [],
                "semantic_focus": False,
                "min_metrics": {
                    "lexical": {
                        "precision_at_k": 1.1,
                    }
                },
            }
        ]

        def _fake_execute_retrieval_query(**_: object) -> dict[str, object]:
            return {
                "requested_mode": "lexical",
                "executed_mode": "lexical",
                "degraded": False,
                "rows": [
                    {
                        "statement_id": "stmt-override",
                        "statement_text": "override",
                        "row_markers": ["1d"],
                        "source_anchor": "doc::override",
                    }
                ],
                "semantic_retry_events": [],
            }

        with patch(
            "sqlite_eval_rust_reference_retrieval.execute_retrieval_query",
            side_effect=_fake_execute_retrieval_query,
        ):
            report = evaluate_retrieval_prompts(
                db_path=Path("unused.sqlite"),
                contract_path=Path("unused.yaml"),
                query_log_root=Path("unused"),
                prompts=prompts,
                top_k=1,
                candidate_limit=10,
                allow_degraded=False,
                semantic_config=SemanticBackendConfig(
                    base_url="http://127.0.0.1:1",
                    embed_model_id="unused",
                    reranker_model_id="unused",
                    timeout_sec=0.1,
                ),
                semantic_retries=0,
                enforce_gates=True,
            )

        gate_blob = " ".join(report["gate_failures"])
        self.assertIn("prompt_override.P-OVERRIDE.lexical.precision_at_k", gate_blob)


if __name__ == "__main__":
    unittest.main()
