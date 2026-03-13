from __future__ import annotations

import pytest

from retrieval.writer_host.manifest import load_manifest, target_index, write_manifest


def test_manifest_round_trip(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    payload = {
        "manifest_id": "m1",
        "targets": [
            {
                "target_id": "RET-ISSUE-001",
                "selected_evidence": [{"statement_id": "chunk::1"}],
            }
        ],
    }
    write_manifest(path, payload)
    loaded = load_manifest(path)
    assert loaded["manifest_id"] == "m1"
    assert "RET-ISSUE-001" in target_index(loaded)


def test_manifest_requires_targets(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    write_manifest(path, {"manifest_id": "m2", "targets": []})
    with pytest.raises(RuntimeError):
        load_manifest(path)
