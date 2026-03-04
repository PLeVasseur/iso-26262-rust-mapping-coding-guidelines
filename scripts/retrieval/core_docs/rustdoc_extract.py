from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

TARGET_MATRIX = (
    "aarch64-unknown-linux-gnu",
    "aarch64-unknown-nto-qnx710",
    "aarch64-unknown-nto-qnx800",
)
CANONICAL_TARGET = "aarch64-unknown-linux-gnu"
OVERLAY_ITEM_CAP = 1200

CODE_BLOCK_RE = re.compile(r"```(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class TargetCfg:
    target_triple: str
    target_os: str
    target_arch: str
    target_env: str
    cfg_signature: str
    cfg_signature_sha256: str


@dataclass(frozen=True)
class ParsedItem:
    item_id: str
    target: TargetCfg
    item_path: str
    item_kind: str
    signature: str
    stability: str
    safety_notes: str
    panic_behavior: str
    example_snippets: str
    docs_text: str
    source_anchor: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def token_len(value: str) -> int:
    return max(1, len([token for token in value.split() if token.strip()]))


def run(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def toolchain_sysroot(toolchain: str) -> Path:
    return Path(run(["rustc", f"+{toolchain}", "--print", "sysroot"]))


def toolchain_version(toolchain: str) -> str:
    return run(["rustc", f"+{toolchain}", "--version", "--verbose"])


def cfg_map(toolchain: str, target: str) -> dict[str, str]:
    raw = run(["rustc", f"+{toolchain}", "--print", "cfg", "--target", target])
    cfg_values: dict[str, str] = {}
    for line in raw.splitlines():
        token = line.strip()
        if not token:
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            cfg_values[key.strip()] = value.strip().strip('"')
        else:
            cfg_values[token] = "1"
    return cfg_values


def cfg_signature_from_map(cfg_values: dict[str, str]) -> tuple[str, str]:
    normalized = {key: cfg_values[key] for key in sorted(cfg_values.keys())}
    signature_payload = json.dumps(normalized, sort_keys=True)
    return signature_payload, sha256_text(signature_payload)


def target_cfg(toolchain: str, target: str) -> TargetCfg:
    cfg_values = cfg_map(toolchain, target)
    signature_payload, signature_hash = cfg_signature_from_map(cfg_values)
    return TargetCfg(
        target_triple=target,
        target_os=cfg_values.get("target_os", "unknown"),
        target_arch=cfg_values.get("target_arch", "unknown"),
        target_env=cfg_values.get("target_env", "unknown"),
        cfg_signature=signature_payload,
        cfg_signature_sha256=signature_hash,
    )


def split_sections(docs: str, title: str) -> dict[str, str]:
    _ = title
    lower = docs.lower()
    safety = ""
    panics = ""
    if "# safety" in lower:
        safety = docs[lower.index("# safety") :]
    if "# panics" in lower:
        panics = docs[lower.index("# panics") :]
    code_blocks = list(CODE_BLOCK_RE.finditer(docs))[:2]
    examples = "\n\n".join(match.group(1).strip() for match in code_blocks)
    return {
        "summary": docs.strip(),
        "safety": safety.strip(),
        "panics": panics.strip(),
        "examples": examples.strip(),
        "title": title,
    }


def detect_stability(attrs: list[str]) -> str:
    joined = "\n".join(attrs).lower()
    if "deprecated" in joined:
        return "deprecated"
    if "unstable" in joined:
        return "unstable"
    if "stable" in joined:
        return "stable"
    return "unspecified"


def stringify_signature(inner_payload: object) -> str:
    if isinstance(inner_payload, dict):
        sig = inner_payload.get("sig")
        if sig is not None:
            return json.dumps(sig, sort_keys=True)
    return ""


def item_path(paths: dict[str, object], item_id: str, name: str) -> str:
    payload = paths.get(item_id)
    if isinstance(payload, dict):
        parts = payload.get("path")
        if isinstance(parts, list) and parts:
            return "::".join(str(part) for part in parts)
    return name or f"core::item::{item_id}"


def build_anchor(item_path_value: str, target: str) -> str:
    return f"https://doc.rust-lang.org/core/?search={item_path_value}&target={target}"


def target_output_path(sysroot: Path, target: str) -> Path:
    return (
        sysroot
        / "lib"
        / "rustlib"
        / "src"
        / "rust"
        / "library"
        / "target"
        / target
        / "doc"
        / "core.json"
    )


def split_chunks(
    raw_text: str, *, min_tokens: int, target_tokens: int, max_tokens: int
) -> list[str]:
    paragraphs = [segment.strip() for segment in raw_text.split("\n\n") if segment.strip()]
    if not paragraphs:
        return [raw_text.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for paragraph in paragraphs:
        para_tokens = token_len(paragraph)
        if current and current_tokens + para_tokens > max_tokens:
            chunks.append("\n\n".join(current).strip())
            current = [paragraph]
            current_tokens = para_tokens
            continue
        current.append(paragraph)
        current_tokens += para_tokens
        if current_tokens >= target_tokens:
            chunks.append("\n\n".join(current).strip())
            current = []
            current_tokens = 0

    if current:
        chunks.append("\n\n".join(current).strip())

    if len(chunks) > 1 and token_len(chunks[-1]) < min_tokens:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}".strip()
        chunks.pop()
    return chunks


def generate_rustdoc_json(toolchain: str, target: str) -> Path:
    sysroot = toolchain_sysroot(toolchain)
    manifest = sysroot / "lib" / "rustlib" / "src" / "rust" / "library" / "core" / "Cargo.toml"
    if not manifest.exists():
        raise RuntimeError(f"rust-src core manifest not found: {manifest}")

    run(
        [
            "cargo",
            f"+{toolchain}",
            "rustdoc",
            "--manifest-path",
            str(manifest),
            "-Z",
            "unstable-options",
            "--output-format",
            "json",
            "--target",
            target,
            "--lib",
        ]
    )
    output = target_output_path(sysroot, target)
    if not output.exists():
        raise RuntimeError(f"rustdoc JSON not found for target {target}: {output}")
    return output


def load_parsed_items(json_path: Path, target_cfg_value: TargetCfg) -> list[ParsedItem]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    index = payload.get("index") or {}
    paths = payload.get("paths") or {}

    allowed_kinds = {
        "module",
        "struct",
        "enum",
        "trait",
        "impl",
        "function",
        "assoc_type",
        "assoc_const",
        "constant",
        "type_alias",
    }
    parsed: list[ParsedItem] = []
    for item_id, item in index.items():
        if not isinstance(item, dict):
            continue
        docs = str(item.get("docs") or "").strip()
        if not docs:
            continue
        inner = item.get("inner") or {}
        if not isinstance(inner, dict) or not inner:
            continue
        item_kind = next(iter(inner.keys()))
        if item_kind not in allowed_kinds:
            continue

        name = str(item.get("name") or "")
        item_path_value = item_path(paths, str(item_id), name)
        signature = stringify_signature(inner.get(item_kind))
        attrs = [str(value) for value in (item.get("attrs") or [])]
        sections = split_sections(docs, item_path_value)
        parsed.append(
            ParsedItem(
                item_id=str(item_id),
                target=target_cfg_value,
                item_path=item_path_value,
                item_kind=item_kind,
                signature=signature,
                stability=detect_stability(attrs),
                safety_notes=sections["safety"],
                panic_behavior=sections["panics"],
                example_snippets=sections["examples"],
                docs_text=sections["summary"],
                source_anchor=build_anchor(item_path_value, target_cfg_value.target_triple),
            )
        )

    return parsed


def write_manifest(
    path: Path, *, toolchain_version_value: str, target: str, source_revision: str
) -> None:
    payload = {
        "toolchain_version": toolchain_version_value,
        "target_triple": target,
        "source_revision": source_revision,
        "generated_at": utc_now(),
    }
    encoded = json.dumps(payload, sort_keys=True)
    path.write_text(f"{sha256_text(encoded)}  metadata.json\n", encoding="utf-8")
