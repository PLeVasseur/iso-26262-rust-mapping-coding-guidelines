from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.feedback_comparison import (  # noqa: E402
    build_feedback_comparison,
    render_feedback_comparison_summary,
)


def test_feedback_comparison_counts_current_signals(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback.md"
    feedback.write_text("`T` `N` `B`\n", encoding="utf-8")
    export_root = tmp_path / "exported_guidelines"
    export_root.mkdir()
    (export_root / "gui_demo.rst").write_text(
        "* - :bibentry:`gui_demo:KEY`\n* - :bibentry:`gui_demo:KEY`\n",
        encoding="utf-8",
    )
    payload = build_feedback_comparison(
        feedback_path=feedback,
        export_root=export_root,
        editorial_review_report={
            "entries": [
                {
                    "editorial_violations": [
                        "title_process_note",
                        "chapter_too_generic_expressions",
                    ],
                    "evidence_quality": {"status": "fail"},
                }
            ],
            "overlap": {"pair_count": 2},
        },
        publishability_audit={"blocked_count": 23},
    )

    assert payload["feedback_category_mentions"]["T"] == 1
    assert payload["current_signals"]["title_flags"] == 1
    assert payload["current_signals"]["duplicate_bibliography_rows"] == 1
    assert "Strict publishability blocked: 23" in render_feedback_comparison_summary(payload)
