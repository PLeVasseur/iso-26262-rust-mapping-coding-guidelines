from __future__ import annotations

import json
from pathlib import Path

from context.convention_extractor import _extract_exemplar_conventions
from context.convention_spec import _build_convention_spec, validate_convention_spec
from context.exemplars import get_exemplar_paths
from context.stdlib_lookup import KNOWN_STD_TYPES, load_stdlib_index, validate_std_path
from scripts.validate_fls_matching import extract_fls_ids_from_rst, extract_topic_from_rst


def test_get_exemplar_paths_from_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "guidelines"
    exemplar = repo / "src" / "coding-guidelines" / "expressions" / "gui_demo.rst"
    exemplar.parent.mkdir(parents=True)
    exemplar.write_text("Demo\n====\n", encoding="utf-8")

    manifest = tmp_path / "exemplar_manifest.json"
    manifest.write_text(
        json.dumps({"exemplars": [{"path": "src/coding-guidelines/expressions/gui_demo.rst"}]}),
        encoding="utf-8",
    )

    paths = get_exemplar_paths(manifest_path=manifest, guidelines_repo_root=repo)
    assert paths == [exemplar]


def test_convention_spec_required_keys(tmp_path: Path) -> None:
    rst = tmp_path / "sample.rst"
    rst.write_text(
        """
Use atomics safely
==================

   .. guideline:: Use atomics safely
   :id: gui_Abc123Def456
   :category: advisory
   :tags: atomics, concurrency
   :decidability: decidable
   :scope: module

       Use :std:`core::sync::atomic::AtomicBool` and cite claims :cite:`K1`.

   .. bibliography::
      :id: bib_gui_Abc123Def456

      .. list-table::
         :header-rows: 0

         * - :bibentry:`K1`
           - Rust reference `https://doc.rust-lang.org/std/sync/atomic/struct.AtomicBool.html`
""".strip()
        + "\n",
        encoding="utf-8",
    )
    conventions = [_extract_exemplar_conventions(rst), _extract_exemplar_conventions(rst)]
    spec = _build_convention_spec(conventions, guidelines_repo_root=tmp_path)

    for key in (
        "spec_version",
        "guidelines_repo_commit_sha",
        "conventions",
        "known_types",
        "title_examples",
        "category_distribution",
        "tag_examples",
    ):
        assert key in spec

    report = validate_convention_spec(spec)
    assert report["status"] == "pass"


def test_stdlib_lookup_fallback_and_validate() -> None:
    lookup = load_stdlib_index(db_path=Path("/definitely/missing/core_docs.db"))
    assert lookup["AtomicBool"] == KNOWN_STD_TYPES["AtomicBool"]
    assert validate_std_path(
        "core::sync::atomic::AtomicBool",
        db_path=Path("/definitely/missing/core_docs.db"),
    )


def test_extract_fls_ids_and_topic(tmp_path: Path) -> None:
    rst = tmp_path / "sample.rst"
    rst.write_text(
        """
Concurrency rule
================

.. guideline:: Demo
   :fls: fls_ABC123

See also :fls:`fls_DEF456`.
""".strip()
        + "\n",
        encoding="utf-8",
    )
    ids = extract_fls_ids_from_rst(rst)
    assert ids
    assert extract_topic_from_rst(rst) == "Concurrency rule"
