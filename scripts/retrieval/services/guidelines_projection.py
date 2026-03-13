from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


def run_m15_projection(repo_root: Path, report_dir: Path) -> tuple[int, str, str]:
    output_json = repo_root / "build" / "examples" / "m1_5_results.json"
    output_log = repo_root / "build" / "examples" / "m1_5_test_output.log"
    output_json.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "uv",
        "run",
        "python",
        "scripts/extract_rust_examples.py",
        "--test",
        "--src-dir",
        "src/coding-guidelines",
        "--prelude",
        "src/examples_prelude.rs",
        "--json",
        str(output_json),
        "--fail-on-error",
        "--verbose",
    ]
    completed = subprocess.run(
        command,
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=False,
    )
    output_log.write_text(
        (completed.stdout or "") + "\n" + (completed.stderr or ""), encoding="utf-8"
    )
    (report_dir / "m1_5_command.log").write_text(
        " ".join(str(token) for token in command) + "\n",
        encoding="utf-8",
    )
    return int(completed.returncode), completed.stdout, completed.stderr


def export_projection_summary(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        payload = {
            "guidelines": conn.execute(
                "SELECT guideline_id, source_file_path, quality_label, export_topic FROM guideline_records ORDER BY guideline_id"
            ).fetchall(),
            "blocks": conn.execute(
                "SELECT guideline_id, block_type, order_index, content FROM guideline_blocks ORDER BY guideline_id, order_index"
            ).fetchall(),
            "bib_links": conn.execute(
                "SELECT guideline_id, bib_key FROM guideline_bib_links ORDER BY guideline_id, bib_key"
            ).fetchall(),
            "citations": conn.execute(
                "SELECT guideline_id, block_id, ref_target, order_index FROM guideline_citations ORDER BY guideline_id, block_id, order_index"
            ).fetchall(),
        }
    finally:
        conn.close()
    encoded = json.dumps(payload, sort_keys=True)
    return {
        "hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "counts": {
            "guidelines": len(payload["guidelines"]),
            "blocks": len(payload["blocks"]),
            "bib_links": len(payload["bib_links"]),
            "citations": len(payload["citations"]),
        },
    }
