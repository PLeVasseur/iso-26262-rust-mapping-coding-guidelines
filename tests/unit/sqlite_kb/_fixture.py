from __future__ import annotations

from pathlib import Path


def create_reference_fixture(root: Path) -> Path:
    source_root = root / "rust-reference-fixture"
    src = source_root / "src"
    src.mkdir(parents=True, exist_ok=True)

    (src / "SUMMARY.md").write_text(
        "\n".join(
            [
                "# Summary",
                "",
                "- [Types](types.md)",
                "- [Traits](traits.md)",
                "- [Control Flow](control-flow.md)",
                "- [Unsafe](unsafe.md)",
                "- [Concurrency](concurrency.md)",
                "- [Diagnostics](diagnostics.md)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (src / "types.md").write_text(
        """
# Types

Rust uses strong typing with structs and enums so values are checked before execution.
Type definitions must be explicit and remain consistent across operations.
Newtype patterns can be used to separate domain concepts.
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    (src / "traits.md").write_text(
        """
# Traits

Traits define interfaces and behavioral contracts.
Implementations are checked against trait requirements.
Traits can be used to model software architecture boundaries.
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    (src / "control-flow.md").write_text(
        """
# Control Flow

Match expressions are exhaustive and every pattern must be handled.
Option and Result values should be handled with explicit branches.
Defensive programming techniques require explicit handling of error paths.
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    (src / "unsafe.md").write_text(
        """
# Unsafe

Unsafe blocks mark operations that require additional invariant checks.
Unsafe code must be reviewed with clear rationale for each boundary.
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    (src / "concurrency.md").write_text(
        """
# Concurrency

Send and Sync define cross-thread safety constraints.
Concurrency aspects should use ownership and borrowing rules to avoid races.
Atomic operations can be used for lock-free synchronization.
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    (src / "diagnostics.md").write_text(
        """
# Diagnostics

Lint attributes configure diagnostics and warnings.
The language subset used for safety code should be enforced with diagnostics.
Verification and testing support require deterministic diagnostics outputs.
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    return source_root
