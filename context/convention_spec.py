"""Build and validate aggregate convention specifications."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from context.convention_extractor import ExemplarConventions


def _get_repo_commit_sha(guidelines_repo_root: Path | None) -> str:
    """Get the current commit SHA of the guidelines repo."""
    if guidelines_repo_root is None or not guidelines_repo_root.exists():
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=guidelines_repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def _build_convention_spec(
    exemplar_conventions: list[ExemplarConventions],
    guidelines_repo_root: Path | None = None,
    std_lookup: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate exemplar conventions into a convention spec."""
    repo_commit_sha = _get_repo_commit_sha(guidelines_repo_root)

    title_formats = Counter(entry.title_format for entry in exemplar_conventions)
    title_examples = [
        entry.title
        for entry in exemplar_conventions
        if entry.title and entry.title_format == "descriptive_sentence"
    ][:5]
    category_counts = Counter(entry.category for entry in exemplar_conventions if entry.category)
    tag_counter = Counter(tag for entry in exemplar_conventions for tag in entry.tags if tag)

    all_std_roles = [
        role for entry in exemplar_conventions for role in entry.std_roles_used if role
    ]
    unique_std_roles = list(dict.fromkeys(all_std_roles))

    known_types: dict[str, str] = {}
    lookup = std_lookup or {}
    for fq_path in unique_std_roles:
        short_name = fq_path.rsplit("::", 1)[-1]
        known_types[short_name] = lookup.get(short_name, fq_path)

    cite_placements = [
        placement for entry in exemplar_conventions for placement in entry.cite_placements
    ]
    miri_patterns = [entry.miri_usage for entry in exemplar_conventions if entry.miri_usage]
    bib_domain_counter = Counter(
        domain
        for entry in exemplar_conventions
        for domain in entry.bibliography_url_patterns
        if domain
    )

    prefix_votes: dict[str, Counter[str]] = {}
    for entry in exemplar_conventions:
        for role, prefix in entry.sub_element_prefixes.items():
            if role not in prefix_votes:
                prefix_votes[role] = Counter()
            prefix_votes[role][prefix] += 1

    conventions = {
        "title_convention": {
            "dominant_format": title_formats.most_common(1)[0][0] if title_formats else "unknown",
            "examples": title_examples,
            "rule": "Title must be a descriptive English sentence stating the requirement.",
        },
        "category_convention": {
            "distribution": dict(category_counts),
            "rule": "Most guidelines are advisory or required; mandatory is reserved.",
        },
        "tag_convention": {
            "values_seen": [tag for tag, _ in tag_counter.most_common(20)],
            "rule": "Tags should be descriptive Rust-domain terms.",
        },
        "std_role_convention": {
            "examples": unique_std_roles[:20],
            "known_types": sorted(known_types.keys()),
            "rule": "Use :std:`fully::qualified::path` for stdlib references.",
        },
        "cite_convention": {
            "placement_examples": cite_placements[:5],
            "rule": "Inline :cite: immediately after factual claims.",
        },
        "miri_convention": {
            "patterns": miri_patterns[:5],
            "rule": "Use :miri: expect_ub for UB demonstrations; :miri: check otherwise.",
        },
        "bibliography_convention": {
            "accepted_domains": [domain for domain, _ in bib_domain_counter.most_common(10)],
            "rule": "Bibliography URLs should be authoritative and externally resolvable.",
        },
        "prefix_convention": {
            role: votes.most_common(1)[0][0] for role, votes in prefix_votes.items() if votes
        },
    }

    return {
        "spec_version": "2.0",
        "source": "exemplar_extraction",
        "exemplar_count": len(exemplar_conventions),
        "guidelines_repo_commit_sha": repo_commit_sha,
        "conventions": conventions,
        "known_types": known_types,
        "title_examples": title_examples,
        "category_distribution": dict(category_counts),
        "tag_examples": [tag for tag, _ in tag_counter.most_common(20)],
        "std_role_convention": conventions["std_role_convention"],
        "title_convention": conventions["title_convention"],
        "category_convention": conventions["category_convention"],
        "tag_convention": conventions["tag_convention"],
        "cite_convention": conventions["cite_convention"],
        "miri_convention": conventions["miri_convention"],
        "bibliography_convention": conventions["bibliography_convention"],
        "prefix_convention": conventions["prefix_convention"],
    }


def validate_convention_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate required shape and core convention coverage."""
    required_keys = {
        "spec_version",
        "guidelines_repo_commit_sha",
        "conventions",
        "known_types",
        "title_examples",
        "category_distribution",
        "tag_examples",
    }
    missing = sorted(key for key in required_keys if key not in spec)

    conventions = spec.get("conventions") if isinstance(spec.get("conventions"), dict) else {}
    std_conv = conventions.get("std_role_convention")
    if not isinstance(std_conv, dict):
        std_conv = {}
    cite_conv = conventions.get("cite_convention")
    if not isinstance(cite_conv, dict):
        cite_conv = {}
    bib_conv = conventions.get("bibliography_convention")
    if not isinstance(bib_conv, dict):
        bib_conv = {}

    field_checks = {
        "title_examples": len(spec.get("title_examples", [])) >= 2,
        "category_distribution": bool(spec.get("category_distribution")),
        "tag_examples": len(spec.get("tag_examples", [])) >= 2,
        "std_role_examples": len(std_conv.get("examples", [])) >= 1,
        "cite_placements": len(cite_conv.get("placement_examples", [])) >= 1,
        "bibliography_domains": len(bib_conv.get("accepted_domains", [])) >= 1,
    }
    verified_fields = sorted(name for name, ok in field_checks.items() if ok)
    unverified_fields = sorted(name for name, ok in field_checks.items() if not ok)

    status = "pass" if not missing and len(verified_fields) == len(field_checks) else "fail"
    return {
        "status": status,
        "missing_required_keys": missing,
        "verified_fields": verified_fields,
        "unverified_fields": unverified_fields,
    }


def _diff_specs(old_spec: dict[str, Any], new_spec: dict[str, Any]) -> dict[str, Any]:
    """Return a compact JSON-serializable diff between two specs."""
    if old_spec == new_spec:
        return {"changed": False, "keys_changed": []}
    keys = sorted(set(old_spec.keys()) | set(new_spec.keys()))
    changed = [key for key in keys if old_spec.get(key) != new_spec.get(key)]
    return {
        "changed": True,
        "keys_changed": changed,
        "old_size_bytes": len(json.dumps(old_spec, sort_keys=True)),
        "new_size_bytes": len(json.dumps(new_spec, sort_keys=True)),
    }
