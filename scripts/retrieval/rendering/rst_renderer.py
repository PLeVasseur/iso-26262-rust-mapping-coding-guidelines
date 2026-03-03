from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_step2_renderer_module():
    file_path = Path(__file__).resolve().parents[2] / "rendering_v2" / "rst_renderer.py"
    spec = importlib.util.spec_from_file_location("_step2_rst_renderer", file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load step2 renderer module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_step2_renderer_module()

CITATION_PLACEMENT_POLICY = _mod.CITATION_PLACEMENT_POLICY
RenderArtifacts = _mod.RenderArtifacts
RendererInput = _mod.RendererInput
_render_guideline_rst = _mod._render_guideline_rst
render_guideline_rst = _mod.render_guideline_rst
serialize_citation_key_map = _mod.serialize_citation_key_map

__all__ = [
    "CITATION_PLACEMENT_POLICY",
    "RenderArtifacts",
    "RendererInput",
    "_render_guideline_rst",
    "render_guideline_rst",
    "serialize_citation_key_map",
]
