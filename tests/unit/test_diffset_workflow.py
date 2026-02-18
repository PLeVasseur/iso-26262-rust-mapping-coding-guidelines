from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_diffset  # noqa: E402
import review_diffset  # noqa: E402


def write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=False)


class DiffsetWorkflowTests(unittest.TestCase):
    def test_stable_item_id_deterministic(self) -> None:
        first = build_diffset.stable_item_id("guideline", "RG-123")
        second = build_diffset.stable_item_id("guideline", "RG-123")
        self.assertEqual(first, second)

    def test_classify_change(self) -> None:
        self.assertEqual(build_diffset.classify_change(None, {"x": 1}), "added")
        self.assertEqual(build_diffset.classify_change({"x": 1}, None), "removed")
        self.assertEqual(build_diffset.classify_change({"x": 1}, {"x": 2}), "modified")
        self.assertEqual(build_diffset.classify_change({"x": 1}, {"x": 1}), "unchanged-context")

    def test_review_payload_schema_compliance(self) -> None:
        payload = {
            "reviewer": "tester",
            "reviewed_at": "2026-01-01T00:00:00Z",
            "items": [
                {
                    "item_id": "guideline:abc",
                    "verdict": "block",
                    "comment": "Need stronger rationale",
                    "status": "open",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        normalized = review_diffset.normalize_review_payload(payload, "diffset-test")

        schema_path = ROOT / "schemas" / "diffset_review.schema.json"
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        validator = Draft202012Validator(schema)
        errors = list(validator.iter_errors(normalized))
        self.assertEqual(errors, [])

    def test_build_diffset_bundle_from_temp_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_id = "run-after-001"
            run_dir = root / ".cache" / "ops" / "runs" / run_id
            snapshot_root = run_dir / "snapshots" / "data"
            snapshot_root.mkdir(parents=True, exist_ok=True)

            schema_root = root / "schemas"
            schema_root.mkdir(parents=True, exist_ok=True)
            for schema_name in ["diffset_manifest.schema.json", "diffset_item.schema.json"]:
                shutil.copy2(ROOT / "schemas" / schema_name, schema_root / schema_name)

            write_yaml(
                snapshot_root / "guideline_categories.yaml",
                {
                    "version": 1,
                    "categories": [
                        {
                            "id": "CAT-A",
                            "name": "Category A",
                            "default_enforcement_mode": "AUTO",
                            "seed_ids": ["SEED-A"],
                        }
                    ],
                },
            )
            write_yaml(
                snapshot_root / "todo_guidelines.yaml",
                {
                    "version": 1,
                    "guidelines": [
                        {
                            "id": "RG-A",
                            "category": "Category A",
                            "rule_statement": "Do thing A",
                            "rationale": "Because A",
                            "iso_seeds": ["SEED-A"],
                            "scope": "scope",
                            "state": "DRAFT",
                            "enforcement_mode": "AUTO",
                            "enforcement_details": "details",
                            "evidence_artifacts": ["tests/guidelines/RG-A/metadata.yaml"],
                            "deviation_requirements": "document deviation",
                        }
                    ],
                },
            )
            (snapshot_root / "coverage_matrix.csv").write_text(
                "target_id,seed_id,guideline_id,evidence_path\n"
                "T-A,SEED-A,RG-A,tests/guidelines/RG-A/metadata.yaml\n",
                encoding="utf-8",
            )
            write_yaml(
                snapshot_root / "target_scope.yaml",
                {"version": 1, "in_scope_target_ids": ["T-A"]},
            )

            write_yaml(root / "data" / "run_registry.yaml", {"version": 1, "accepted_runs": []})

            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "run_id": run_id,
                        "mode": "change",
                        "corpus_pack": "iso-core-part6",
                        "step_results": {
                            "check_traceability": {
                                "return_code": 0,
                                "output": "ok",
                            }
                        },
                        "runtime_errors": [],
                        "policy_errors": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            bundle_dir, manifest, items = build_diffset.build_diffset_bundle(
                root,
                after_run_id=run_id,
                before_run_id=None,
                output_root=Path(".cache/reviews/diffsets"),
            )

            self.assertTrue((bundle_dir / "manifest.json").exists())
            self.assertTrue((bundle_dir / "items.jsonl").exists())
            self.assertTrue((bundle_dir / "summary.md").exists())
            self.assertTrue((bundle_dir / "review.html").exists())
            self.assertEqual(manifest["after_run_id"], run_id)
            self.assertGreater(len(items), 0)

    def test_review_diffset_once_smoke(self) -> None:
        run_id = "test-review-once-smoke"
        run_dir = ROOT / ".cache" / "ops" / "runs" / run_id
        diffset_dir = ROOT / ".cache" / "reviews" / "diffsets" / f"diffset-bootstrap__{run_id}"

        try:
            snapshot_root = run_dir / "snapshots" / "data"
            snapshot_root.mkdir(parents=True, exist_ok=True)

            write_yaml(
                snapshot_root / "guideline_categories.yaml",
                {
                    "version": 1,
                    "categories": [
                        {
                            "id": "CAT-SMOKE",
                            "name": "Smoke Category",
                            "default_enforcement_mode": "AUDIT",
                            "seed_ids": ["SEED-SMOKE"],
                        }
                    ],
                },
            )
            write_yaml(
                snapshot_root / "todo_guidelines.yaml",
                {
                    "version": 1,
                    "guidelines": [
                        {
                            "id": "RG-SMOKE",
                            "category": "Smoke Category",
                            "rule_statement": "Smoke statement",
                            "rationale": "Smoke rationale",
                            "iso_seeds": ["SEED-SMOKE"],
                            "scope": "scope",
                            "state": "DRAFT",
                            "enforcement_mode": "AUDIT",
                            "enforcement_details": "details",
                            "evidence_artifacts": ["tests/guidelines/RG-SMOKE/metadata.yaml"],
                            "deviation_requirements": "deviation",
                        }
                    ],
                },
            )
            (snapshot_root / "coverage_matrix.csv").write_text(
                "target_id,seed_id,guideline_id,evidence_path\n"
                "T-SMOKE,SEED-SMOKE,RG-SMOKE,tests/guidelines/RG-SMOKE/metadata.yaml\n",
                encoding="utf-8",
            )
            write_yaml(
                snapshot_root / "target_scope.yaml",
                {"version": 1, "in_scope_target_ids": ["T-SMOKE"]},
            )

            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "run_id": run_id,
                        "mode": "change",
                        "corpus_pack": "iso-core-part6",
                        "step_results": {
                            "check_traceability": {
                                "return_code": 0,
                                "output": "ok",
                            }
                        },
                        "runtime_errors": [],
                        "policy_errors": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                "scripts/review_diffset.py",
                "--after-run",
                run_id,
                "--once",
                "--no-open",
            ]
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            if result.returncode != 0:
                self.fail(result.stderr or result.stdout)

            self.assertTrue((diffset_dir / "review.html").exists())
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
            shutil.rmtree(diffset_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
