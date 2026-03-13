from __future__ import annotations

from retrieval.operations.chapter_index_policy import ensure_glob_toctree


def test_noop_when_glob_exists(tmp_path) -> None:
    index = tmp_path / "expressions" / "index.rst"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        "Expressions\n===========\n\n.. toctree::\n   :maxdepth: 1\n   :glob:\n\n   gui_*\n",
        encoding="utf-8",
    )
    before = index.read_text(encoding="utf-8")
    changed = ensure_glob_toctree(index)
    after = index.read_text(encoding="utf-8")
    assert changed is False
    assert after == before


def test_inserts_glob_when_missing(tmp_path) -> None:
    index = tmp_path / "expressions" / "index.rst"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("Expressions\n===========\n", encoding="utf-8")
    changed = ensure_glob_toctree(index)
    content = index.read_text(encoding="utf-8")
    assert changed is True
    assert "gui_*" in content
