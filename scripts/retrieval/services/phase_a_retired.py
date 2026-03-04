from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.services.capability import emit_unsupported


def _emit(operation: str) -> int:
    return emit_unsupported(
        corpus="s0",
        operation=operation,
        reason="phase_a_soft_retired_use_guidelines_repo_or_sqlite_kb_corpus_ops",
    )


def run_scaffold_s0_config(args: Namespace, *, root: Path) -> int:
    _ = (args, root)
    return _emit("scaffold-s0-config")


def run_doctor(args: Namespace, *, root: Path) -> int:
    _ = (args, root)
    return _emit("doctor")


def run_enumerate_targets(args: Namespace, *, root: Path) -> int:
    _ = (args, root)
    return _emit("enumerate-targets")


def run_calibration_run(args: Namespace, *, root: Path) -> int:
    _ = (args, root)
    return _emit("calibration-run")


def run_enforce_calibration_quality(args: Namespace, *, root: Path) -> int:
    _ = (args, root)
    return _emit("enforce-calibration-quality")


def run_pack_reviewer_packet(args: Namespace, *, root: Path) -> int:
    _ = (args, root)
    return _emit("pack-reviewer-packet")
