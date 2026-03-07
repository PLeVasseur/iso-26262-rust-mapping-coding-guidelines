from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.conformance import (  # noqa: E402
    _extract_example_failures,
    _offline_build_failures,
)


def test_extract_example_failures_buckets_compile_and_warning_failures() -> None:
    stdout_tail = """
📍 src/coding-guidelines/expressions/gui_demo.rst:33
   Parent: non_compl_ex_guidemo
   ❌ Compilation failed unexpectedly
   Compiler output:
      error: the range pattern here has ambiguous interpretation

📍 src/coding-guidelines/unsafety/gui_other.rst:54
   Parent: compl_ex_guiother
   ❌ Compilation succeeded but produced warnings
   Compiler output:
      warning: use of deprecated method `core::str::<impl str>::slice_unchecked`
"""

    failures = _extract_example_failures(stdout_tail)

    assert failures == [
        {
            "location": "src/coding-guidelines/expressions/gui_demo.rst:33",
            "parent": "non_compl_ex_guidemo",
            "kind": "compile_failed",
            "reason": "ambiguous_reference_range_pattern",
        },
        {
            "location": "src/coding-guidelines/unsafety/gui_other.rst:54",
            "parent": "compl_ex_guiother",
            "kind": "warnings_as_failures",
            "reason": "deprecated_api",
        },
    ]


def test_offline_build_failures_buckets_fls_and_bibliography_issues() -> None:
    stderr_tail = """
Need gui_demo references 'fls_UNRESOLVED'
duplicate bibliography URL detected in guideline batch
Need gui_other references non-existent FLS ID: 'fls_missing123'
"""

    failures = _offline_build_failures(stderr_tail)

    assert failures == [
        {"kind": "fls_unresolved", "detail": "Need gui_demo references 'fls_UNRESOLVED'"},
        {
            "kind": "bibliography_validation",
            "detail": "duplicate bibliography URL detected in guideline batch",
        },
        {
            "kind": "fls_missing",
            "detail": "Need gui_other references non-existent FLS ID: 'fls_missing123'",
        },
    ]
