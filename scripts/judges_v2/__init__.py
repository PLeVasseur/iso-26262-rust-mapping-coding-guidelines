"""Backward-compatible exports for Step 4 Stage-B judges."""

__all__ = ["STAGE_B_JUDGES", "run_stage_b_judges"]


def __getattr__(name: str):
    if name in {"STAGE_B_JUDGES", "run_stage_b_judges"}:
        from retrieval.judges.stage_b import STAGE_B_JUDGES, run_stage_b_judges

        return {
            "STAGE_B_JUDGES": STAGE_B_JUDGES,
            "run_stage_b_judges": run_stage_b_judges,
        }[name]
    raise AttributeError(name)
