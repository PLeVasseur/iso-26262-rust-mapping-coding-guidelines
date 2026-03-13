from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host import evidence  # noqa: E402


def _arg_value(extra_args: list[str], key: str) -> str:
    idx = extra_args.index(key)
    return str(extra_args[idx + 1])


def _write_targets_manifest(path: Path) -> None:
    payload = {
        "targets": [
            {
                "prompt_id": "RET-ISSUE-001",
                "query_text": "unsafe pointer dereference",
                "expected_row_markers": ["memory"],
            }
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_writer_evidence_generates_multi_corpus_manifest(tmp_path: Path) -> None:
    targets_manifest = tmp_path / "targets.json"
    _write_targets_manifest(targets_manifest)

    def fake_query_run(args: Namespace, *, root: Path) -> int:
        mode = _arg_value(list(args.extra_args), "--mode")
        prompt_id = _arg_value(list(args.extra_args), "--prompt-id")
        save_dir = Path(_arg_value(list(args.extra_args), "--save-response-dir"))
        save_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "response": {
                "rows": [
                    {
                        "statement_id": f"{mode}-s1",
                        "source_anchor": f"https://example.test/{args.corpus}/{mode}",
                        "doc_id": f"{args.corpus}-doc",
                        "statement_text": f"{args.corpus} {mode} evidence",
                        "final_score": 0.9,
                    }
                ]
            }
        }
        out = save_dir / f"{prompt_id.replace('::', '-')}-{mode}.json"
        out.write_text(json.dumps(payload), encoding="utf-8")
        return 0

    args = Namespace(
        corpora="rust_reference,core_docs",
        profile_path="",
        targets_manifest=str(targets_manifest),
        run_id="writer_evidence_test",
        report_root=str(tmp_path / "report"),
        output="",
        modes="lexical,semantic,hybrid",
        top_k=10,
        top_n=12,
        rrf_k=60,
        rank_window=100,
        allow_degraded=False,
    )

    with patch("retrieval.writer_host.evidence.query_service.run", side_effect=fake_query_run):
        code = evidence.run(args, root=ROOT)

    assert code == 0
    manifest_path = tmp_path / "report" / "writer_evidence_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["corpora"] == ["rust_reference", "core_docs"]
    assert payload["targets_manifest"] == str(targets_manifest.resolve())
    assert payload["degraded"] is False
    target_row = payload["targets"][0]
    assert "rust_reference" in target_row["per_corpus"]
    assert "core_docs" in target_row["per_corpus"]
    selected = list(target_row["selected_evidence"])
    assert all("raw_statement_id" in row for row in selected)
    assert all(row["raw_statement_id"] for row in selected)
    assert all(row["corpus"] in {"rust_reference", "core_docs"} for row in selected)
    assert all(row["source_anchor"] for row in selected)
    selected_ids = list(target_row["selected_evidence_ids"])
    assert any(value.startswith("rust_reference::") for value in selected_ids)
    assert any(value.startswith("core_docs::") for value in selected_ids)


def test_writer_evidence_allows_partial_mode_failure_when_degraded(tmp_path: Path) -> None:
    targets_manifest = tmp_path / "targets.json"
    _write_targets_manifest(targets_manifest)

    def fake_query_run(args: Namespace, *, root: Path) -> int:
        mode = _arg_value(list(args.extra_args), "--mode")
        prompt_id = _arg_value(list(args.extra_args), "--prompt-id")
        if args.corpus == "core_docs" and mode == "semantic":
            return 2
        save_dir = Path(_arg_value(list(args.extra_args), "--save-response-dir"))
        save_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "response": {
                "rows": [
                    {
                        "statement_id": f"{mode}-s1",
                        "source_anchor": f"https://example.test/{args.corpus}/{mode}",
                        "doc_id": f"{args.corpus}-doc",
                        "statement_text": f"{args.corpus} {mode} evidence",
                        "final_score": 0.8,
                    }
                ]
            }
        }
        out = save_dir / f"{prompt_id.replace('::', '-')}-{mode}.json"
        out.write_text(json.dumps(payload), encoding="utf-8")
        return 0

    args = Namespace(
        corpora="rust_reference,core_docs",
        profile_path="",
        targets_manifest=str(targets_manifest),
        run_id="writer_evidence_degraded",
        report_root=str(tmp_path / "report"),
        output="",
        modes="lexical,semantic",
        top_k=10,
        top_n=12,
        rrf_k=60,
        rank_window=100,
        allow_degraded=True,
    )

    with patch("retrieval.writer_host.evidence.query_service.run", side_effect=fake_query_run):
        code = evidence.run(args, root=ROOT)

    assert code == 0
    manifest_path = tmp_path / "report" / "writer_evidence_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["degraded"] is True
    assert payload["degraded_mode_failures"]
