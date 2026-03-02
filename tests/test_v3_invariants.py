"""v3 non-negotiable regression tests.

Run after each step:
    uv run pytest tests/test_v3_invariants.py -x --tb=short
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PIPELINE_ROOT = Path(__file__).resolve().parent.parent


class TestEvidenceGateOrdering:
    def test_ordering_contract_in_code(self) -> None:
        orchestrator = (
            PIPELINE_ROOT / "scripts" / "retrieval" / "services" / "s0_phase_a_service.py"
        )
        if not orchestrator.exists():
            pytest.skip("orchestrator not present")
        content = orchestrator.read_text(encoding="utf-8")
        normalize_pos = content.find("normalize_claim")
        validate_pos = content.find("validate_evidence") if normalize_pos >= 0 else -1
        if normalize_pos >= 0 and validate_pos >= 0:
            assert normalize_pos < validate_pos


class TestCitationStaging:
    def test_staged_validation_exists(self) -> None:
        services_dir = PIPELINE_ROOT / "scripts" / "retrieval" / "services"
        if not services_dir.exists():
            pytest.skip("services not present")
        all_py = "".join(path.read_text(encoding="utf-8") for path in services_dir.rglob("*.py"))
        assert "citation_resolution" in all_py.lower()


class TestPromptIdCanonical:
    def test_report_files_use_prompt_id(self) -> None:
        reports_dir = PIPELINE_ROOT / ".cache" / "sqlite_kb" / "reports"
        if not reports_dir.exists():
            pytest.skip("no reports directory")
        for report in reports_dir.rglob("*.json"):
            try:
                data = json.loads(report.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict) and isinstance(data.get("per_file"), list):
                for entry in data["per_file"]:
                    if isinstance(entry, dict) and "target_id" in entry:
                        assert "prompt_id" in entry, f"{report.name} missing prompt_id"


class TestAdditiveConstructMapping:
    def test_synonyms_config_exists(self) -> None:
        synonyms = PIPELINE_ROOT / "config" / "s0" / "construct_synonyms.yaml"
        if not synonyms.exists():
            pytest.skip("construct_synonyms.yaml not present")
        import yaml

        data = yaml.safe_load(synonyms.read_text(encoding="utf-8"))
        assert isinstance(data, dict)


class TestEvidenceGateBlocking:
    def test_go_no_go_checks_evidence_gate(self) -> None:
        gates_dir = PIPELINE_ROOT / "scripts" / "retrieval" / "gates"
        orchestrator = (
            PIPELINE_ROOT / "scripts" / "retrieval" / "services" / "s0_phase_a_service.py"
        )
        target = None
        if gates_dir.exists():
            for path in gates_dir.rglob("*.py"):
                if "go_no_go" in path.name:
                    target = path
                    break
        if target is None and orchestrator.exists():
            target = orchestrator
        if target is None:
            pytest.skip("gate code not available")
        content = target.read_text(encoding="utf-8").lower()
        assert "evidence_gate" in content or "evidence_synthesizer" in content


class TestNoReverseImports:
    def test_no_extracted_module_imports_orchestrator(self) -> None:
        module_dirs = [
            PIPELINE_ROOT / "scripts" / "retrieval" / "rendering",
            PIPELINE_ROOT / "scripts" / "retrieval" / "context",
            PIPELINE_ROOT / "scripts" / "retrieval" / "validation",
            PIPELINE_ROOT / "scripts" / "retrieval" / "judges",
            PIPELINE_ROOT / "scripts" / "retrieval" / "gates",
        ]
        for module_dir in module_dirs:
            if not module_dir.exists():
                continue
            for py_file in module_dir.rglob("*.py"):
                if py_file.name == "__init__.py":
                    continue
                content = py_file.read_text(encoding="utf-8")
                assert "from retrieval.services.s0_phase_a_service import" not in content
                assert "import retrieval.services.s0_phase_a_service" not in content
