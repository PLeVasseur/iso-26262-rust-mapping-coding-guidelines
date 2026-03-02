from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.import_utils import GUIDELINES_REPO_ROOT
from scripts.validation_v2.conformance import (
    validate_batch_conformance,
    validate_rst_conformance,
)
from scripts.validation_v2.run_conformance import run_conformance_on_rerendered


def _valid_rst(guideline_id: str = "gui_AbC123xYz890") -> str:
    return f""".. SPDX-License-Identifier: MIT OR Apache-2.0
   SPDX-FileCopyrightText: The Coding Guidelines Subcommittee Contributors

.. default-domain:: coding-guidelines

Use :std:`Option` when handling partial values
===============================================

.. guideline:: Use :std:`Option` when handling partial values
   :id: {guideline_id}
   :category: advisory
   :status: draft
   :release: latest
   :fls: fls:core::option::Option
   :decidability: decidable
   :scope: module
   :tags: option, safety

   Prefer :std:`Option` in APIs :cite:`{guideline_id}:R1`.

   .. rationale::
      :id: rat_AbC123xYz891
      :status: draft

      Explicit optionality improves auditability :cite:`{guideline_id}:R1`.

   .. non_compliant_example::
      :id: non_compl_ex_AbC123xYz890
      :status: draft

      Uses sentinel values.

      .. rust-example::
         :edition: 2021
         :miri:

         unsafe {{ fn bad() {{}} }}

   .. compliant_example::
      :id: compl_ex_AbC123xYz890
      :status: draft

      Returns :std:`Option`.

      .. rust-example::
         :edition: 2021

         fn good() -> Option<u8> {{ Some(1) }}

   .. bibliography::
      :status: draft

      .. list-table::
         :header-rows: 0
         :widths: auto

         * - :bibentry:`{guideline_id}:R1`
           - `Rust Option docs <https://doc.rust-lang.org/std/option/>`__
"""


def test_validate_rst_conformance_catches_known_bad_signals(tmp_path: Path) -> None:
    bad = """.. SPDX-License-Identifier: MIT OR Apache-2.0

Guideline for CORE-CONC-003
===========================

.. guideline:: Guideline for CORE-CONC-003
   :id: gui_b1a1c4a4ee36
   :category: mandatory
   :status: draft
   :release: latest
   :fls: fls_b1a1c4a4ee36
   :decidability: decidable
   :scope: module
   :tags: table1-1a, core_docs

   Use `Option` values :cite:`gui_b1a1c4a4ee36:R1`.

   .. rationale::
      :id: rat_b1a1c4a4ee36
      :status: draft

      rationale

   .. non_compliant_example::
      :id: non_gui_b1a1c4a4ee36
      :status: draft

      bad

      .. rust-example::
         unsafe {{ fn bad() {{}} }}

   .. compliant_example::
      :id: com_gui_b1a1c4a4ee36
      :status: draft

      good

      .. rust-example::
         :edition: 2021

         fn good() {{}}

   .. bibliography::
      :id: bib_gui_b1a1c4a4ee36
      :status: draft

      .. list-table::
         :header-rows: 0

         * - :bibentry:`other:R1`
           - `Local cache <evidence_bundle/path.txt>`__
"""
    path = tmp_path / "core-conc-003.rst"
    path.write_text(bad, encoding="utf-8")

    is_valid, violations = validate_rst_conformance(path, guideline_id="gui_b1a1c4a4ee36")

    checks = {v.get("check") for v in violations}
    assert not is_valid
    assert "id_format_hex_hash" in checks
    assert "sub_element_prefix" in checks
    assert "title_format" in checks
    assert "std_role_missing" in checks
    assert "missing_miri_option" in checks
    assert "missing_edition_option" in checks
    assert "bibliography_internal_path" in checks
    assert "citation_key_prefix_mismatch" in checks
    assert "fls_id_looks_like_hash" in checks or "fls_id_mirrors_gui_id" in checks
    assert "tag_iso_derived" in checks
    assert "tag_corpus_name" in checks


def test_validate_rst_conformance_exemplar_set_has_no_blocking_violations() -> None:
    manifest = json.loads(Path("data/exemplar_manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []

    for entry in manifest.get("exemplars", []):
        rel_path = str(entry["path"])
        rst_path = GUIDELINES_REPO_ROOT / rel_path
        text = rst_path.read_text(encoding="utf-8")
        guideline_id = ""
        for line in text.splitlines():
            if line.strip().startswith(":id:") and "gui_" in line:
                guideline_id = line.split(":id:", 1)[1].strip()
                break

        valid, violations = validate_rst_conformance(rst_path, guideline_id=guideline_id)
        blocking = [v for v in violations if v.get("severity", "error") == "error"]
        if not valid or blocking:
            failures.append(f"{rel_path}: {blocking}")

    assert not failures, "\n".join(failures)


def test_validate_batch_conformance_checks_distribution_and_duplicates() -> None:
    batch = [
        {"category": "mandatory", "ids_found": ["gui_1"]},
        {"category": "mandatory", "ids_found": ["gui_1", "rat_1"]},
    ]
    is_valid, violations = validate_batch_conformance(batch)
    checks = {v["check"] for v in violations}

    assert not is_valid
    assert "category_all_mandatory" in checks
    assert "duplicate_ids_across_files" in checks


def test_validate_rst_conformance_uses_convention_spec_known_types(tmp_path: Path) -> None:
    path = tmp_path / "spec-hook.rst"
    path.write_text(_valid_rst().replace(":std:`Option`", "`MyType`", 1), encoding="utf-8")

    valid_default, violations_default = validate_rst_conformance(
        path, guideline_id="gui_AbC123xYz890"
    )
    valid_spec, violations_spec = validate_rst_conformance(
        path,
        guideline_id="gui_AbC123xYz890",
        convention_spec={"std_role_convention": {"known_types": ["MyType"]}},
    )

    default_checks = {v.get("check") for v in violations_default}
    spec_checks = {v.get("check") for v in violations_spec}
    assert valid_default
    assert not valid_spec
    assert "std_role_missing" not in default_checks
    assert "std_role_missing" in spec_checks


def test_docutils_parse_validation_flags_malformed_directive(tmp_path: Path) -> None:
    malformed = _valid_rst() + "\n.. bibliography::\n:status: draft\n"
    path = tmp_path / "malformed.rst"
    path.write_text(malformed, encoding="utf-8")

    _valid, violations = validate_rst_conformance(path, guideline_id="gui_AbC123xYz890")
    assert any(v.get("check") == "rst_parse_error" for v in violations)


def test_run_conformance_writes_output_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    rst_dir = run_dir / "rerendered_rst"
    rst_dir.mkdir(parents=True)

    (rst_dir / "good.rst").write_text(_valid_rst("gui_AbC123xYz890"), encoding="utf-8")
    (rst_dir / "bad.rst").write_text(
        _valid_rst("gui_b1a1c4a4ee36").replace("gui_b1a1c4a4ee36", "gui_b1a1c4a4ee36"),
        encoding="utf-8",
    )

    report = run_conformance_on_rerendered(run_dir)
    output_path = run_dir / "output_conformance_report.json"

    assert output_path.exists()
    assert report["source"] == "rerendered_rst"
    assert len(report["per_file"]) == 2


def test_exec2_files_are_rejected_when_present() -> None:
    run_dir = Path(
        ".cache/sqlite_kb/reports/s0_phase_a_20260227_v8_execution/generated_guidelines_rst"
    )
    if not run_dir.exists():
        pytest.skip("exec2 generated_guidelines_rst directory is not available")

    filenames = [
        "core-conc-003.rst",
        "core-safe-003.rst",
        "ret-issue-005.rst",
        "ret-resolve-008.rst",
    ]
    missing = [name for name in filenames if not (run_dir / name).exists()]
    if missing:
        pytest.skip(f"exec2 files missing: {missing}")

    for name in filenames:
        path = run_dir / name
        text = path.read_text(encoding="utf-8")
        guideline_id = ""
        for line in text.splitlines():
            if line.strip().startswith(":id:") and "gui_" in line:
                guideline_id = line.split(":id:", 1)[1].strip()
                break
        valid, violations = validate_rst_conformance(path, guideline_id=guideline_id)
        assert not valid, f"expected conformance failure for {name}"
        assert violations, f"expected non-empty violations for {name}"
