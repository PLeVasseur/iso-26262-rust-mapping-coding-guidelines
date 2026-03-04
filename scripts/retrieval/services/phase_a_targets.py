from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.services.phase_a_retired import run_enumerate_targets


def _run_eval_for_corpus(*args: object, **kwargs: object):
    _ = (args, kwargs)
    raise RuntimeError("Phase-A target evaluation helper is soft-retired")


__all__ = ["run_enumerate_targets", "_run_eval_for_corpus", "Namespace", "Path"]
