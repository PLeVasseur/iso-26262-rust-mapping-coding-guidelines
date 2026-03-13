from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

from retrieval.writer_host.targets import build_manifest, load_prompts, write_manifest


def run(args: Namespace, *, root: Path) -> int:
    query_testset_path = (
        root
        / str(
            getattr(
                args,
                "query_testset_path",
                "data/query_testsets/rust_reference_table1_retrieval_eval.yaml",
            )
        )
    ).resolve()
    prompts = load_prompts(query_testset_path)
    profile = str(getattr(args, "profile", "fast") or "fast")
    explicit_targets = [
        value.strip()
        for value in str(getattr(args, "targets", "") or "").split(",")
        if value.strip()
    ]
    manifest = build_manifest(prompts=prompts, profile=profile, explicit_targets=explicit_targets)
    output_path_raw = str(getattr(args, "output", "") or "").strip()
    if output_path_raw:
        output_path = Path(output_path_raw).resolve()
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_path = root / ".cache" / "sqlite_kb" / "reports" / f"writer_targets_{stamp}.json"
    write_manifest(output_path, manifest)
    print(output_path)
    return 0
