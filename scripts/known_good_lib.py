from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from _common import read_yaml, write_json, write_yaml

GUIDELINE_PATH_RE = re.compile(r"^src/coding-guidelines/.+/gui_[^/]+\.rst(?:\.inc)?$")
GUIDELINE_ID_RE = re.compile(r"gui_[A-Za-z0-9]+")

SECTION_DIRECTIVES = {
    "rationale",
    "non_compliant_example",
    "compliant_example",
    "bibliography",
}

INLINE_CODE_RE = re.compile(r"``([^`]+)``")
STD_REF_RE = re.compile(r":std:`([^`]+)`")
CITE_REF_RE = re.compile(r":cite:`([^`]+)`")
BIB_REF_RE = re.compile(r":bibentry:`([^`]+)`")

TODO_SIGNAL_RE = re.compile(r"\bTODO\b|:release:\s*(todo|unknown)\b", re.IGNORECASE)

RUST_TERMS = {
    "unsafe",
    "result",
    "option",
    "trait",
    "enum",
    "struct",
    "union",
    "macro",
    "match",
    "impl",
    "borrow",
    "lifetime",
    "panic",
    "overflow",
    "checked",
    "wrapping",
    "saturating",
    "ffi",
    "extern",
    "pointer",
    "miri",
    "clippy",
    "usize",
    "isize",
    "u8",
    "u16",
    "u32",
    "u64",
    "u128",
    "i8",
    "i16",
    "i32",
    "i64",
    "i128",
}

ACTION_TERMS = {
    "must",
    "shall",
    "require",
    "avoid",
    "ensure",
    "never",
    "only",
    "forbid",
    "disallow",
}

CONDITION_TERMS = {
    "if",
    "when",
    "unless",
    "while",
    "where",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "iso26262-known-good-harvester",
        },
    )
    with urllib.request.urlopen(request) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> dict[str, Any]:
    return json.loads(fetch_text(url))


def fetch_main_sha(source_repo: str) -> str:
    payload = fetch_json(f"https://api.github.com/repos/{source_repo}/commits/main")
    return str(payload.get("sha") or "").strip()


def list_guideline_paths(source_repo: str, source_sha: str) -> list[str]:
    tree_url = f"https://api.github.com/repos/{source_repo}/git/trees/{source_sha}?recursive=1"
    payload = fetch_json(tree_url)
    entries = payload.get("tree") or []
    paths = []
    for entry in entries:
        path = str(entry.get("path") or "")
        if entry.get("type") != "blob":
            continue
        if not GUIDELINE_PATH_RE.match(path):
            continue
        paths.append(path)
    return sorted(paths)


def guideline_id_from_path(path: str) -> str:
    match = GUIDELINE_ID_RE.search(path)
    if match:
        return match.group(0)
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
    return f"gui_{digest}"


def extract_title(lines: list[str]) -> str:
    for index in range(len(lines) - 1):
        current = lines[index].rstrip()
        next_line = lines[index + 1].rstrip()
        if not current or len(current) < 3:
            continue
        if set(next_line) <= {"=", "-", "~", "^"} and len(next_line) >= len(current):
            return current.strip()
    return ""


def count_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def parse_metadata_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped.startswith(":"):
        return None
    match = re.match(r"^:([A-Za-z0-9_-]+):\s*(.*)$", stripped)
    if not match:
        return None
    return match.group(1), match.group(2)


def is_section_directive(line: str) -> tuple[str, int] | None:
    match = re.match(r"^(\s*)\.\.\s+([a-zA-Z0-9_]+)::\s*(.*)$", line)
    if not match:
        return None
    name = match.group(2)
    if name not in SECTION_DIRECTIVES:
        return None
    return name, len(match.group(1))


def dedent_lines(lines: list[str]) -> list[str]:
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return [line.rstrip() for line in lines]
    min_indent = min(count_indent(line) for line in non_empty)
    output = []
    for line in lines:
        if line.strip():
            output.append(line[min_indent:].rstrip())
        else:
            output.append("")
    return output


def normalize_inline(text: str) -> str:
    text = INLINE_CODE_RE.sub(r"`\1`", text)
    text = STD_REF_RE.sub(r"`\1`", text)
    text = CITE_REF_RE.sub(r"[cite:\1]", text)
    text = BIB_REF_RE.sub(r"\1", text)
    return text


def normalize_block_text(lines: list[str]) -> str:
    normalized = []
    for line in dedent_lines(lines):
        stripped = line.strip()
        if stripped.startswith(".. note::"):
            normalized.append("Note:")
            continue
        if stripped.startswith(".. warning::"):
            normalized.append("Warning:")
            continue
        if stripped.startswith(".. "):
            continue
        normalized.append(normalize_inline(line.rstrip()))

    collapsed: list[str] = []
    blank_count = 0
    for line in normalized:
        if not line.strip():
            blank_count += 1
            if blank_count > 1:
                continue
        else:
            blank_count = 0
        collapsed.append(line.rstrip())

    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    return "\n".join(collapsed).strip()


def parse_rust_example(
    content_lines: list[str], start_index: int
) -> tuple[dict[str, str], str, int]:
    directive_line = content_lines[start_index]
    directive_indent = count_indent(directive_line)
    options: dict[str, str] = {}
    index = start_index + 1

    while index < len(content_lines):
        current = content_lines[index]
        if not current.strip():
            index += 1
            continue
        option = parse_metadata_line(current)
        if option is None or count_indent(current) <= directive_indent:
            break
        options[option[0]] = option[1]
        index += 1

    code_lines: list[str] = []
    code_started = False
    while index < len(content_lines):
        current = content_lines[index]
        if not current.strip():
            if code_started:
                code_lines.append("")
            index += 1
            continue

        current_indent = count_indent(current)
        if current_indent <= directive_indent and current.lstrip().startswith(".. "):
            break
        if current_indent <= directive_indent and code_started:
            break

        code_started = True
        code_lines.append(current)
        index += 1

    rust_code = "\n".join(dedent_lines(code_lines)).rstrip()
    return options, rust_code, index


def parse_example_block(content_lines: list[str]) -> dict[str, Any]:
    description_lines: list[str] = []
    rust_code = ""
    rust_options: dict[str, str] = {}

    index = 0
    while index < len(content_lines):
        line = content_lines[index]
        if re.match(r"^\s*\.\.\s+rust-example::\s*$", line):
            rust_options, rust_code, index = parse_rust_example(content_lines, index)
            continue
        description_lines.append(line)
        index += 1

    return {
        "description": normalize_block_text(description_lines),
        "rust_code": rust_code,
        "options": {key: value.strip() for key, value in rust_options.items()},
    }


def parse_bibliography(content_lines: list[str]) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for index, line in enumerate(content_lines):
        match = BIB_REF_RE.search(line)
        if not match:
            continue
        key = match.group(1)
        description = ""
        lookahead = index + 1
        while lookahead < len(content_lines):
            candidate = content_lines[lookahead].strip()
            if not candidate:
                lookahead += 1
                continue
            if candidate.startswith("-"):
                description = re.sub(r"^-\s*", "", candidate)
            break
        if not description:
            description = key
        references.append({"key": key, "description": normalize_inline(description)})
    return references


def parse_directive_block(lines: list[str], start_index: int) -> tuple[dict[str, Any], int]:
    match = re.match(r"^(\s*)\.\.\s+([a-zA-Z0-9_]+)::\s*(.*)$", lines[start_index])
    if not match:
        raise ValueError(f"Expected directive at line {start_index + 1}")

    directive_indent = len(match.group(1))
    directive_name = match.group(2)
    directive_title = match.group(3).strip()
    metadata: dict[str, str] = {}

    index = start_index + 1
    while index < len(lines):
        current = lines[index]
        if not current.strip():
            index += 1
            continue
        parsed = parse_metadata_line(current)
        if parsed is None or count_indent(current) <= directive_indent:
            break
        metadata[parsed[0]] = parsed[1].strip()
        index += 1

    content_lines: list[str] = []
    while index < len(lines):
        section = is_section_directive(lines[index])
        if section is not None and section[1] <= directive_indent:
            break
        content_lines.append(lines[index])
        index += 1

    block = {
        "directive": directive_name,
        "directive_title": directive_title,
        "metadata": metadata,
        "content_lines": content_lines,
    }
    return block, index


def parse_guideline_rst(text: str, fallback_title: str = "") -> dict[str, Any]:
    lines = text.splitlines()
    title = extract_title(lines)
    if not title:
        title = fallback_title

    guideline_index = -1
    guideline_title = ""
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\.\.\s+guideline::\s*(.*)$", line)
        if match:
            guideline_index = index
            guideline_title = match.group(1).strip()
            break

    metadata: dict[str, Any] = {}
    rule_lines: list[str] = []
    rationale_text = ""
    compliant_examples: list[dict[str, Any]] = []
    non_compliant_examples: list[dict[str, Any]] = []
    references: list[dict[str, str]] = []

    if guideline_index != -1:
        guideline_indent = count_indent(lines[guideline_index])
        index = guideline_index + 1

        while index < len(lines):
            current = lines[index]
            if not current.strip():
                index += 1
                continue
            parsed = parse_metadata_line(current)
            if parsed is None or count_indent(current) <= guideline_indent:
                break
            key, value = parsed
            if key == "tags":
                tags = [item.strip() for item in value.split(",") if item.strip()]
                metadata[key] = tags
            else:
                metadata[key] = value.strip()
            index += 1

        while index < len(lines):
            section = is_section_directive(lines[index])
            if section is not None:
                break
            rule_lines.append(lines[index])
            index += 1

        while index < len(lines):
            section = is_section_directive(lines[index])
            if section is None:
                index += 1
                continue
            block, index = parse_directive_block(lines, index)
            directive = str(block["directive"])

            if directive == "rationale":
                rationale_text = normalize_block_text(block["content_lines"])
                continue

            if directive in {"compliant_example", "non_compliant_example"}:
                parsed_example = parse_example_block(block["content_lines"])
                example = {
                    "example_id": str(
                        block["metadata"].get("id") or block["directive_title"] or "example"
                    ).strip(),
                    "status": str(block["metadata"].get("status") or "").strip(),
                    "description": parsed_example["description"],
                    "rust_code": parsed_example["rust_code"],
                    "options": parsed_example["options"],
                }
                if directive == "compliant_example":
                    compliant_examples.append(example)
                else:
                    non_compliant_examples.append(example)
                continue

            if directive == "bibliography":
                references.extend(parse_bibliography(block["content_lines"]))

    rule_text = normalize_block_text(rule_lines)
    citations = sorted(set(CITE_REF_RE.findall(text)))
    std_refs = sorted(set(STD_REF_RE.findall(text)))
    if not title:
        title = guideline_title or "Untitled Guideline"

    return {
        "title": title,
        "guideline_title": guideline_title,
        "metadata": metadata,
        "rule_text": rule_text,
        "rationale_text": rationale_text,
        "non_compliant_examples": non_compliant_examples,
        "compliant_examples": compliant_examples,
        "references": references,
        "citations": citations,
        "std_refs": std_refs,
    }


def compute_signals(text: str) -> dict[str, bool]:
    return {
        "has_rationale": ".. rationale::" in text,
        "has_compliant_example": ".. compliant_example::" in text,
        "has_non_compliant_example": ".. non_compliant_example::" in text,
        "has_citation_signal": (":cite:`" in text) or (":std:`" in text),
        "has_bibliography_signal": ".. bibliography::" in text,
        "has_todo_signal": bool(TODO_SIGNAL_RE.search(text)),
    }


def signals_match_rule(signals: dict[str, bool], rule: dict[str, Any]) -> bool:
    checks = {
        "require_rationale": "has_rationale",
        "require_compliant_example": "has_compliant_example",
        "require_non_compliant_example": "has_non_compliant_example",
        "require_citation_signal": "has_citation_signal",
        "require_bibliography_signal": "has_bibliography_signal",
    }
    for rule_key, signal_key in checks.items():
        required = bool(rule.get(rule_key, False))
        if required and not signals.get(signal_key, False):
            return False

    if bool(rule.get("disallow_todo_signal", False)) and signals.get("has_todo_signal", False):
        return False
    return True


def markdown_front_matter(payload: dict[str, Any]) -> str:
    lines = ["---"]
    lines.extend(write_yaml_to_lines(payload))
    lines.append("---")
    return "\n".join(lines)


def write_yaml_to_lines(payload: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(payload, dict):
        lines: list[str] = []
        for key, value in payload.items():
            if isinstance(value, dict | list):
                lines.append(f"{prefix}{key}:")
                lines.extend(write_yaml_to_lines(value, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {json_scalar(value)}")
        return lines

    if isinstance(payload, list):
        lines = []
        for item in payload:
            if isinstance(item, dict | list):
                lines.append(f"{prefix}-")
                lines.extend(write_yaml_to_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {json_scalar(item)}")
        return lines

    return [f"{prefix}{json_scalar(payload)}"]


def json_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    if not text:
        return "''"
    if re.match(r"^[A-Za-z0-9_.:/-]+$", text):
        return text
    escaped = text.replace("'", "''")
    return f"'{escaped}'"


def parse_markdown_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    marker = "\n---\n"
    end = text.find(marker, 4)
    if end == -1:
        return {}, text

    raw_front_matter = text[4:end]
    body = text[end + len(marker) :]
    payload = read_yaml_from_text(raw_front_matter)
    if not isinstance(payload, dict):
        payload = {}
    return payload, body


def read_yaml_from_text(text: str) -> Any:
    return yaml.safe_load(text)


def tokenize_words(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9_]+", text.lower()) if token]


def cosine_similarity(text_a: str, text_b: str) -> float:
    tokens_a = tokenize_words(text_a)
    tokens_b = tokenize_words(text_b)
    if not tokens_a or not tokens_b:
        return 0.0

    counts_a = Counter(tokens_a)
    counts_b = Counter(tokens_b)
    dot = 0.0
    for token, count in counts_a.items():
        dot += count * counts_b.get(token, 0)

    norm_a = math.sqrt(sum(value * value for value in counts_a.values()))
    norm_b = math.sqrt(sum(value * value for value in counts_b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def count_terms(tokens: list[str], terms: set[str]) -> int:
    return sum(1 for token in tokens if token in terms)


def extract_feature_vector(canonical: dict[str, Any]) -> dict[str, float]:
    rule_text = str(canonical.get("rule_text") or "")
    rationale_text = str(canonical.get("rationale_text") or "")
    rule_tokens = tokenize_words(rule_text)
    rationale_tokens = tokenize_words(rationale_text)

    compliant_examples = canonical.get("compliant_examples") or []
    non_compliant_examples = canonical.get("non_compliant_examples") or []
    all_examples = list(compliant_examples) + list(non_compliant_examples)

    explanation_word_counts = []
    code_token_counts = []
    code_hashes = set()
    code_block_count = 0
    for example in all_examples:
        description = str(example.get("description") or "")
        explanation_word_counts.append(len(tokenize_words(description)))

        code = str(example.get("rust_code") or "")
        if code.strip():
            code_block_count += 1
            tokens = tokenize_words(code)
            code_token_counts.append(len(tokens))
            code_hashes.add(hashlib.sha1(code.encode("utf-8")).hexdigest())

    combined_text = "\n".join([rule_text, rationale_text] + [
        str(example.get("description") or "") for example in all_examples
    ])
    combined_tokens = tokenize_words(combined_text)

    rust_term_count = count_terms(combined_tokens, RUST_TERMS)
    action_term_count = count_terms(rule_tokens + rationale_tokens, ACTION_TERMS)
    condition_term_count = count_terms(rule_tokens + rationale_tokens, CONDITION_TERMS)
    concept_terms_present = {token for token in combined_tokens if token in RUST_TERMS}

    references = canonical.get("references") or []
    citations = canonical.get("citations") or []
    std_refs = canonical.get("std_refs") or []

    word_count = max(1, len(combined_tokens))
    rule_word_count = max(1, len(rule_tokens))

    examples_total = len(all_examples)
    concept_count = max(1, len(concept_terms_present))

    feature_vector = {
        "section_count": float(
            (1 if rule_text.strip() else 0)
            + (1 if rationale_text.strip() else 0)
            + (1 if compliant_examples else 0)
            + (1 if non_compliant_examples else 0)
            + (1 if references else 0)
        ),
        "rationale_present": float(1 if rationale_text.strip() else 0),
        "compliant_examples_count": float(len(compliant_examples)),
        "non_compliant_examples_count": float(len(non_compliant_examples)),
        "examples_total": float(examples_total),
        "example_code_block_count": float(code_block_count),
        "example_avg_explanation_words": float(
            sum(explanation_word_counts) / len(explanation_word_counts)
            if explanation_word_counts
            else 0.0
        ),
        "code_token_total": float(sum(code_token_counts)),
        "example_diversity": float(len(code_hashes) / examples_total if examples_total else 0.0),
        "rust_term_density": float(rust_term_count / word_count),
        "constraint_phrase_density": float(action_term_count / rule_word_count),
        "citation_count": float(len(citations) + len(std_refs) + len(references)),
        "bibliography_present": float(1 if references else 0),
        "concept_count": float(concept_count),
        "conditions_per_100_words": float((condition_term_count * 100.0) / word_count),
        "concepts_per_100_words": float((len(concept_terms_present) * 100.0) / word_count),
        "examples_per_concept": float(examples_total / concept_count),
        "rule_word_count": float(len(rule_tokens)),
    }
    return feature_vector


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * p
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return float(ordered[low])
    low_value = ordered[low]
    high_value = ordered[high]
    fraction = rank - low
    return float((low_value * (1 - fraction)) + (high_value * fraction))


def summarize_feature(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "p10": 0.0,
            "p25": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "p90": 0.0,
        }

    return {
        "min": float(min(values)),
        "max": float(max(values)),
        "mean": float(sum(values) / len(values)),
        "median": percentile(values, 0.5),
        "p10": percentile(values, 0.10),
        "p25": percentile(values, 0.25),
        "p50": percentile(values, 0.50),
        "p75": percentile(values, 0.75),
        "p90": percentile(values, 0.90),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=False))
            handle.write("\n")


def load_manifest(path: Path) -> dict[str, Any]:
    payload = read_yaml(path) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid manifest payload at {path}")
    return payload


def save_manifest(path: Path, payload: dict[str, Any]) -> None:
    write_yaml(path, payload)


def save_report(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def ensure_clean_directory(path: Path) -> None:
    if path.exists():
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file():
                safe_unlink(child)
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_dir():
                try:
                    child.rmdir()
                except OSError:
                    pass
    path.mkdir(parents=True, exist_ok=True)
