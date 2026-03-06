from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host import evidence_campaign


def run(args: Namespace, *, root: Path) -> int:
    return int(evidence_campaign.run(args, root=root))
