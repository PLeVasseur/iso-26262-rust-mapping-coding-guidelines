from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retrieval.build.reference_parsing import SectionRecord, StatementRecord


@dataclass(frozen=True)
class MechanismDefinition:
    mechanism_id: str
    canonical_symbol: str
    mechanism_family: str
    enforcement_kind: str
    stability: str
    patterns: tuple[re.Pattern[str], ...]


MECHANISM_TAXONOMY: tuple[MechanismDefinition, ...] = (
    MechanismDefinition(
        mechanism_id="rust.typing.static",
        canonical_symbol="type-system",
        mechanism_family="typing",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\btype(s|d)?\b", re.IGNORECASE),
            re.compile(r"\bstruct\b", re.IGNORECASE),
            re.compile(r"\benum\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.typing.newtype",
        canonical_symbol="newtype",
        mechanism_family="typing",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bnewtype\b", re.IGNORECASE),
            re.compile(r"\btuple struct\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.abstraction.traits",
        canonical_symbol="traits",
        mechanism_family="abstraction",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\btrait(s)?\b", re.IGNORECASE),
            re.compile(r"\bimpl\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.control_flow.match",
        canonical_symbol="match",
        mechanism_family="control-flow",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bmatch\b", re.IGNORECASE),
            re.compile(r"\bpattern\b", re.IGNORECASE),
            re.compile(r"\bexhaustive\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.defensive.result_option",
        canonical_symbol="result-option",
        mechanism_family="defensive",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bresult\b", re.IGNORECASE),
            re.compile(r"\boption\b", re.IGNORECASE),
            re.compile(r"\berror\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.memory.borrowing",
        canonical_symbol="ownership-borrowing",
        mechanism_family="memory-safety",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bborrow(ing)?\b", re.IGNORECASE),
            re.compile(r"\blifetime\b", re.IGNORECASE),
            re.compile(r"\bownership\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.concurrency.send_sync",
        canonical_symbol="send-sync",
        mechanism_family="concurrency",
        enforcement_kind="hard",
        stability="stable",
        patterns=(
            re.compile(r"\bsend\b", re.IGNORECASE),
            re.compile(r"\bsync\b", re.IGNORECASE),
            re.compile(r"\bthread\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.unsafe.boundary",
        canonical_symbol="unsafe-boundary",
        mechanism_family="unsafe",
        enforcement_kind="process",
        stability="stable",
        patterns=(
            re.compile(r"\bunsafe\b", re.IGNORECASE),
            re.compile(r"\binvariant\b", re.IGNORECASE),
            re.compile(r"\bsafety\b", re.IGNORECASE),
        ),
    ),
    MechanismDefinition(
        mechanism_id="rust.diagnostics.attributes",
        canonical_symbol="diagnostic-attributes",
        mechanism_family="diagnostics",
        enforcement_kind="lint",
        stability="stable",
        patterns=(
            re.compile(r"\blint\b", re.IGNORECASE),
            re.compile(r"\battribute\b", re.IGNORECASE),
        ),
    ),
)


def _anchor_url_for_section(section: SectionRecord, *, source_url: str) -> str:
    html_path = Path(section.rel_path).with_suffix(".html")
    return f"{source_url}{html_path.as_posix()}#{section.anchor}"


def extract_mechanisms_and_evidence(
    *,
    sections: list[SectionRecord],
    statements: list[StatementRecord],
    source_fetched_at: str,
    source_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
    statement_by_section: dict[str, list[StatementRecord]] = {}
    for statement in statements:
        statement_by_section.setdefault(statement.section_id, []).append(statement)

    mechanisms: dict[str, dict[str, Any]] = {}
    evidence_rows: list[dict[str, Any]] = []
    evidence_counter = 0
    evidence_count_by_mechanism: dict[str, int] = {}
    best_anchor_by_mechanism: dict[str, dict[str, Any]] = {}
    seen_evidence_keys: set[tuple[str, str, str | None]] = set()

    for section in sections:
        section_statements = statement_by_section.get(section.section_id, [])
        section_text = f"{section.heading}\n{section.text}"
        lower_section_text = section_text.lower()
        section_anchor = _anchor_url_for_section(section, source_url=source_url)

        for mechanism in MECHANISM_TAXONOMY:
            if not any(pattern.search(lower_section_text) for pattern in mechanism.patterns):
                continue

            mechanisms.setdefault(
                mechanism.mechanism_id,
                {
                    "mechanism_id": mechanism.mechanism_id,
                    "canonical_symbol": mechanism.canonical_symbol,
                    "mechanism_family": mechanism.mechanism_family,
                    "enforcement_kind": mechanism.enforcement_kind,
                    "stability": mechanism.stability,
                },
            )

            best_statement: StatementRecord | None = None
            for statement in section_statements:
                if any(pattern.search(statement.text.lower()) for pattern in mechanism.patterns):
                    best_statement = statement
                    break

            statement_id = best_statement.statement_id if best_statement is not None else None
            key = (mechanism.mechanism_id, section.section_id, statement_id)
            if key in seen_evidence_keys:
                continue
            seen_evidence_keys.add(key)

            evidence_counter += 1
            excerpt = best_statement.text if best_statement else section.heading
            confidence = best_statement.confidence if best_statement else 0.64
            evidence_rows.append(
                {
                    "evidence_id": f"evidence::{evidence_counter:06d}",
                    "mechanism_id": mechanism.mechanism_id,
                    "section_id": section.section_id,
                    "statement_id": statement_id,
                    "source_anchor": section_anchor,
                    "evidence_kind": "normative",
                    "text_excerpt": excerpt,
                    "confidence": confidence,
                    "source_fetched_at": source_fetched_at,
                }
            )

            evidence_count_by_mechanism[mechanism.mechanism_id] = (
                evidence_count_by_mechanism.get(mechanism.mechanism_id, 0) + 1
            )
            current_best = best_anchor_by_mechanism.get(mechanism.mechanism_id)
            if current_best is None or confidence > current_best["confidence"]:
                best_anchor_by_mechanism[mechanism.mechanism_id] = {
                    "source_anchor": section_anchor,
                    "section_id": section.section_id,
                    "statement_id": statement_id,
                    "confidence": confidence,
                }

    if not mechanisms:
        raise RuntimeError(
            "Mechanism extraction failed: no mechanisms detected from Rust Reference"
        )

    return (
        sorted(mechanisms.values(), key=lambda value: value["mechanism_id"]),
        evidence_rows,
        evidence_count_by_mechanism,
        best_anchor_by_mechanism,
    )
