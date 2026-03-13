from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retrieval.ingest.contracts import ChunkInput, ChunkResult, CleanInput, CleanResult

BLOCK_RE = re.compile(r"\.\.\s+guideline_(rationale|compliant|non_compliant)::", re.IGNORECASE)
REF_RE = re.compile(r":ref:`([^`]+)`")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_topic(docname: str, category: str) -> str:
    normalized = str(docname or "").strip().lower().replace("\\", "/")
    if normalized.startswith("coding-guidelines/"):
        normalized = normalized[len("coding-guidelines/") :]
    normalized = normalized.strip("/")
    if normalized.endswith("/index"):
        normalized = normalized[: -len("/index")]
    normalized = normalized.strip("/")
    if not normalized:
        fallback = str(category or "general").strip().lower() or "general"
        return re.sub(r"[^a-z0-9/_-]+", "-", fallback).strip("-/") or "general"
    return "/".join(
        re.sub(r"[^a-z0-9_-]+", "-", segment).strip("-") or "misc"
        for segment in normalized.split("/")
    )


def _template_location(
    *, link: str, guideline_id: str, docname: str, category: str
) -> tuple[str, str]:
    normalized_link = str(link or "").strip().replace("\\", "/")
    if ".html" in normalized_link:
        normalized_link = normalized_link.split(".html", 1)[0]
    normalized_link = normalized_link.strip("/")
    if normalized_link.startswith("coding-guidelines/"):
        normalized_link = normalized_link[len("coding-guidelines/") :]
    if normalized_link:
        parts = [part for part in normalized_link.split("/") if part]
        if len(parts) >= 2:
            chapter = "/".join(parts[:-1])
            filename = f"{parts[-1]}.rst"
            return chapter, filename

    normalized_doc = str(docname or "").strip().replace("\\", "/")
    if normalized_doc.startswith("coding-guidelines/"):
        normalized_doc = normalized_doc[len("coding-guidelines/") :]
    normalized_doc = normalized_doc.strip("/")
    if normalized_doc.endswith("/index"):
        chapter = normalized_doc[: -len("/index")].strip("/")
    else:
        chapter = "/".join(normalized_doc.split("/")[:-1]).strip("/")
    if not chapter:
        chapter = _normalize_topic(docname, category)
    safe_guideline = re.sub(r"[^A-Za-z0-9_-]+", "", str(guideline_id)) or "guideline"
    return chapter, f"{safe_guideline}.rst"


@dataclass(frozen=True)
class ParsedGuideline:
    guideline_id: str
    title: str
    source_file_path: str
    export_topic: str
    metadata_json: str
    source_hash: str
    blocks: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    bibliography: dict[str, str]
    checksums: dict[str, str]


@dataclass(frozen=True)
class ArtifactBundle:
    source_revision: str
    source_hash: str
    guidelines: list[ParsedGuideline]
    warnings: list[str]


@dataclass(frozen=True)
class GuidelinesArtifactsV1Strategy:
    strategy_id: str = "guidelines_artifacts_v1"
    strategy_version: str = "1"

    def clean_text(self, clean_input: CleanInput) -> CleanResult:
        cleaned = " ".join(str(clean_input.raw_text).split())
        return CleanResult(cleaned_text=cleaned, normalizer_version="clean-v1")

    def build_chunks(self, chunk_input: ChunkInput) -> ChunkResult:
        return ChunkResult(chunks=[], spans=[], strategy_version=self.strategy_version)

    def parse_artifacts(
        self,
        *,
        repo_root: Path,
        needs_contract: dict[str, Any],
        ids_contract: dict[str, Any],
    ) -> ArtifactBundle:
        needs_path = repo_root / "build" / "html" / "needs.json"
        ids_path = repo_root / "build" / "html" / "guidelines-ids.json"
        spec_lock_path = repo_root / "src" / "spec.lock"

        if not needs_path.exists():
            raise RuntimeError(f"missing_required_artifact::{needs_path}")
        if not ids_path.exists():
            raise RuntimeError(f"missing_required_artifact::{ids_path}")
        if not spec_lock_path.exists():
            raise RuntimeError(f"missing_required_artifact::{spec_lock_path}")

        needs_payload = json.loads(needs_path.read_text(encoding="utf-8"))
        ids_payload = json.loads(ids_path.read_text(encoding="utf-8"))
        spec_lock = spec_lock_path.read_text(encoding="utf-8")
        source_revision = _sha256_text(spec_lock)[:16]

        self._validate_contract(
            payload=needs_payload, contract=needs_contract, artifact="needs.json"
        )
        self._validate_contract(
            payload=ids_payload, contract=ids_contract, artifact="guidelines-ids.json"
        )

        needs_entries = needs_payload.get("needs") if isinstance(needs_payload, dict) else None
        if not isinstance(needs_entries, dict):
            versions_payload = (
                needs_payload.get("versions") if isinstance(needs_payload, dict) else None
            )
            current_version = (
                str(needs_payload.get("current_version", ""))
                if isinstance(needs_payload, dict)
                else ""
            )
            if isinstance(versions_payload, dict):
                version_payload = versions_payload.get(current_version)
                if not isinstance(version_payload, dict) and "" in versions_payload:
                    version_payload = versions_payload.get("")
                if isinstance(version_payload, dict):
                    needs_entries = version_payload.get("needs")
        if not isinstance(needs_entries, dict):
            raise RuntimeError("artifact_contract_failed::needs.json::needs")

        ids_entries: dict[str, dict[str, Any]] = {}
        if isinstance(ids_payload, dict):
            raw_guidelines = ids_payload.get("guidelines")
            if isinstance(raw_guidelines, dict):
                for key, value in raw_guidelines.items():
                    if isinstance(value, dict):
                        ids_entries[str(key)] = value
            documents = ids_payload.get("documents")
            if isinstance(documents, list):
                for document in documents:
                    if not isinstance(document, dict):
                        continue
                    for guideline in document.get("guidelines", []):
                        if not isinstance(guideline, dict):
                            continue
                        guideline_id = str(guideline.get("id", "")).strip()
                        if not guideline_id:
                            continue
                        ids_entries[guideline_id] = {
                            "link": str(guideline.get("link", "")).strip(),
                            "checksum": str(guideline.get("checksum", "")).strip(),
                            "rationale": str(
                                (guideline.get("rationale") or {}).get("id", "")
                            ).strip(),
                            "rationale_checksum": str(
                                (guideline.get("rationale") or {}).get("checksum", "")
                            ).strip(),
                            "compliant_example": str(
                                (guideline.get("compliant_example") or {}).get("id", "")
                            ).strip(),
                            "compliant_example_checksum": str(
                                (guideline.get("compliant_example") or {}).get("checksum", "")
                            ).strip(),
                            "non_compliant_example": str(
                                (guideline.get("non_compliant_example") or {}).get("id", "")
                            ).strip(),
                            "non_compliant_example_checksum": str(
                                (guideline.get("non_compliant_example") or {}).get("checksum", "")
                            ).strip(),
                            "bibliography": {
                                "id": str(
                                    (guideline.get("bibliography") or {}).get("id", "")
                                ).strip(),
                                "checksum": str(
                                    (guideline.get("bibliography") or {}).get("checksum", "")
                                ).strip(),
                            },
                        }

        warnings: list[str] = []
        parsed: list[ParsedGuideline] = []
        for guideline_id in sorted(needs_entries.keys()):
            item = needs_entries.get(guideline_id)
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).strip().lower() != "guideline":
                continue

            ids_item = ids_entries.get(guideline_id)
            if not isinstance(ids_item, dict):
                raise RuntimeError(
                    f"artifact_contract_failed::guidelines-ids.json::missing_guideline::{guideline_id}"
                )

            block_ids = {
                "rationale": str(ids_item.get("rationale", "")).strip(),
                "compliant_example": str(ids_item.get("compliant_example", "")).strip(),
                "non_compliant_example": str(ids_item.get("non_compliant_example", "")).strip(),
            }
            if any(not value for value in block_ids.values()):
                raise RuntimeError(
                    f"artifact_contract_failed::guidelines-ids.json::missing_child_id::{guideline_id}"
                )

            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            docname = str(item.get("docname", "")).strip()
            category = str(item.get("category", "")).strip()
            chapter_path, export_filename = _template_location(
                link=str(ids_item.get("link", "")).strip(),
                guideline_id=str(guideline_id),
                docname=docname,
                category=category,
            )

            checksums = {
                "guideline": str(ids_item.get("checksum", "")).strip(),
                "rationale": str(ids_item.get("rationale_checksum", "")).strip(),
                "compliant_example": str(ids_item.get("compliant_example_checksum", "")).strip(),
                "non_compliant_example": str(
                    ids_item.get("non_compliant_example_checksum", "")
                ).strip(),
            }

            detected_blocks: list[dict[str, Any]] = []
            fragments = BLOCK_RE.split(content)
            if len(fragments) <= 1:
                detected_blocks.append({"block_type": "body", "content": content})
            else:
                header = str(fragments[0]).strip()
                if header:
                    detected_blocks.append({"block_type": "body", "content": header})
                for idx in range(1, len(fragments), 2):
                    block_type = str(fragments[idx]).strip().lower()
                    block_content = (
                        str(fragments[idx + 1]).strip() if idx + 1 < len(fragments) else ""
                    )
                    detected_blocks.append({"block_type": block_type, "content": block_content})

            citations: list[dict[str, Any]] = []
            for order_index, block in enumerate(detected_blocks, start=1):
                block_content = str(block.get("content", ""))
                for ref_index, match in enumerate(REF_RE.findall(block_content), start=1):
                    citations.append(
                        {
                            "block_order_index": order_index,
                            "block_type": str(block.get("block_type", "body")).strip() or "body",
                            "order_index": ref_index,
                            "ref_target": str(match).strip(),
                        }
                    )

            bibliography_payload = ids_item.get("bibliography")
            bibliography: dict[str, str] = {}
            if isinstance(bibliography_payload, dict):
                bibliography = {
                    str(key).strip(): str(value).strip()
                    for key, value in bibliography_payload.items()
                    if str(key).strip() and str(value).strip()
                }
            else:
                warnings.append(f"optional_bibliography_absent::{guideline_id}")

            metadata = {
                "status": str(item.get("status", "")).strip(),
                "category": category,
                "release": str(item.get("release", "")).strip(),
                "fls": str(item.get("fls", "")).strip(),
                "decidability": str(item.get("decidability", "")).strip(),
                "scope": str(item.get("scope", "")).strip(),
                "tags": item.get("tags", []),
                "lineno": int(item.get("lineno", 0) or 0),
                "docname": docname,
                "export_filename": export_filename,
                "export_relative_path": f"{chapter_path}/{export_filename}"
                if chapter_path
                else export_filename,
                "parent_needs_back": item.get("parent_needs_back", []),
            }
            source_hash = _sha256_text(
                json.dumps({"id": guideline_id, "title": title, "content": content}, sort_keys=True)
            )
            parsed.append(
                ParsedGuideline(
                    guideline_id=str(guideline_id),
                    title=title,
                    source_file_path=docname,
                    export_topic=chapter_path,
                    metadata_json=json.dumps(metadata, sort_keys=True),
                    source_hash=source_hash,
                    blocks=detected_blocks,
                    citations=citations,
                    bibliography=bibliography,
                    checksums=checksums,
                )
            )

        bundle_hash = _sha256_text(
            "::".join(
                [
                    _sha256_text(needs_path.read_text(encoding="utf-8")),
                    _sha256_text(ids_path.read_text(encoding="utf-8")),
                    _sha256_text(spec_lock),
                ]
            )
        )
        return ArtifactBundle(
            source_revision=source_revision,
            source_hash=bundle_hash,
            guidelines=parsed,
            warnings=warnings,
        )

    def _validate_contract(
        self,
        *,
        payload: dict[str, Any],
        contract: dict[str, Any],
        artifact: str,
    ) -> None:
        if not isinstance(payload, dict):
            raise RuntimeError(f"artifact_contract_failed::{artifact}::root_not_object")
        required_top = contract.get("required_top_level_keys")
        if isinstance(required_top, list):
            for key in required_top:
                normalized = str(key).strip()
                if normalized and normalized not in payload:
                    raise RuntimeError(
                        f"artifact_contract_failed::{artifact}::missing_top_level::{normalized}"
                    )
