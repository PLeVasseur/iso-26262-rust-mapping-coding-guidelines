#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from _common import (
    EXIT_RUNTIME_FAIL,
    EXIT_SUCCESS,
    find_registry_baseline,
    read_json,
    read_yaml,
    repo_root,
    utc_now,
    write_json,
)

ARTIFACT_PATHS = {
    "category": "data/guideline_categories.yaml",
    "guideline": "data/todo_guidelines.yaml",
    "coverage": "data/coverage_matrix.csv",
    "scope": "data/target_scope.yaml",
}

ENTITY_ORDER = ["gate", "category", "guideline", "coverage", "scope"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a diffset bundle between runs")
    parser.add_argument("--after-run", required=True)
    parser.add_argument("--before-run")
    parser.add_argument("--output-root", type=Path, default=Path(".cache/reviews/diffsets"))
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def stable_item_id(entity_type: str, key: str) -> str:
    digest = hashlib.sha1(f"{entity_type}|{key}".encode()).hexdigest()[:12]
    return f"{entity_type}:{digest}"


def classify_change(before_value: Any, after_value: Any) -> str:
    if before_value is None and after_value is None:
        return "unchanged-context"
    if before_value is None:
        return "added"
    if after_value is None:
        return "removed"
    if before_value == after_value:
        return "unchanged-context"
    return "modified"


def severity_hint(entity_type: str, change_type: str, before_value: Any, after_value: Any) -> str:
    if entity_type == "gate":
        value = after_value if after_value is not None else before_value
        if isinstance(value, dict):
            has_errors = value.get("kind") in {"runtime_errors", "policy_errors"}
            if has_errors and value.get("count", 0) > 0:
                return "high"
            if value.get("return_code", 0) != 0:
                return "high"
        if change_type == "modified":
            return "warn"
        return "info"

    if change_type == "removed":
        return "high"
    if change_type == "modified":
        return "warn"
    return "info"


def run_dir(root: Path, run_id: str) -> Path:
    return root / ".cache" / "ops" / "runs" / run_id


def load_run_manifest(root: Path, run_id: str) -> dict[str, Any]:
    path = run_dir(root, run_id) / "run_manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"run manifest missing: {path.relative_to(root)}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"run manifest is not an object: {path.relative_to(root)}")
    return payload


def infer_before_run(
    root: Path,
    after_manifest: dict[str, Any],
    explicit_before: str | None,
) -> str | None:
    if explicit_before:
        return explicit_before

    base_run = after_manifest.get("base_run")
    if isinstance(base_run, str) and base_run.strip():
        return base_run.strip()

    run_registry = read_yaml(root / "data" / "run_registry.yaml") or {}
    baseline = find_registry_baseline(
        run_registry,
        str(after_manifest.get("corpus_pack") or ""),
        str(after_manifest.get("mode") or ""),
    )
    if baseline:
        accepted = str(baseline.get("accepted_run_id") or "").strip()
        if accepted:
            return accepted
    return None


def resolve_artifact_source(
    root: Path,
    run_id: str | None,
    rel_path: str,
    *,
    allow_repo_fallback: bool,
) -> Path | None:
    if run_id is None:
        return None

    snapshot_path = run_dir(root, run_id) / "snapshots" / rel_path
    if snapshot_path.exists():
        return snapshot_path

    legacy_path = run_dir(root, run_id) / Path(rel_path).name
    if legacy_path.exists():
        return legacy_path

    if allow_repo_fallback:
        repo_path = root / rel_path
        if repo_path.exists():
            return repo_path

    return None


def load_categories(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = read_yaml(path) or {}
    categories = payload.get("categories") or []
    mapped: dict[str, dict[str, Any]] = {}
    for item in categories:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or item.get("name") or "").strip()
        if not key:
            continue
        mapped[key] = item
    return mapped


def load_guidelines(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = read_yaml(path) or {}
    guidelines = payload.get("guidelines") or []
    mapped: dict[str, dict[str, Any]] = {}
    for item in guidelines:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()
        if not key:
            continue
        mapped[key] = item
    return mapped


def load_coverage(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.exists():
        return {}
    mapped: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            target = str(row.get("target_id") or "").strip()
            seed = str(row.get("seed_id") or "").strip()
            guideline = str(row.get("guideline_id") or "").strip()
            if not target or not seed or not guideline:
                continue
            key = "|".join([target, seed, guideline])
            mapped[key] = {
                "target_id": target,
                "seed_id": seed,
                "guideline_id": guideline,
                "evidence_path": str(row.get("evidence_path") or "").strip(),
            }
    return mapped


def load_scope(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.exists():
        return {}
    payload = read_yaml(path) or {}
    targets = payload.get("in_scope_target_ids") or []
    mapped: dict[str, dict[str, str]] = {}
    for value in targets:
        target_id = str(value).strip()
        if not target_id:
            continue
        mapped[target_id] = {"target_id": target_id}
    return mapped


def load_gate_map(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if manifest is None:
        return {}

    mapped: dict[str, dict[str, Any]] = {}
    step_results = manifest.get("step_results") or {}
    for step_name in sorted(step_results):
        value = step_results.get(step_name) or {}
        mapped[f"step:{step_name}"] = {
            "kind": "step",
            "name": step_name,
            "return_code": int(value.get("return_code", 0)),
            "ok": int(value.get("return_code", 0)) == 0,
        }

    mapped["runtime_errors"] = {
        "kind": "runtime_errors",
        "count": len(manifest.get("runtime_errors") or []),
    }
    mapped["policy_errors"] = {
        "kind": "policy_errors",
        "count": len(manifest.get("policy_errors") or []),
    }
    return mapped


def build_context(
    entity_type: str,
    key: str,
    before_value: Any,
    after_value: Any,
    before_run_id: str | None,
    after_run_id: str,
) -> dict[str, Any]:
    active = after_value if after_value is not None else before_value
    context: dict[str, Any] = {
        "entity_key": key,
        "before_run_id": before_run_id,
        "after_run_id": after_run_id,
    }

    if isinstance(active, dict):
        if entity_type == "guideline":
            context.update(
                {
                    "guideline_id": active.get("id"),
                    "category": active.get("category"),
                    "state": active.get("state"),
                    "enforcement_mode": active.get("enforcement_mode"),
                }
            )
        elif entity_type == "category":
            context.update(
                {
                    "category_id": active.get("id"),
                    "name": active.get("name"),
                    "default_enforcement_mode": active.get("default_enforcement_mode"),
                }
            )
        elif entity_type == "coverage":
            context.update(
                {
                    "target_id": active.get("target_id"),
                    "seed_id": active.get("seed_id"),
                    "guideline_id": active.get("guideline_id"),
                }
            )
        elif entity_type == "scope":
            context.update({"target_id": active.get("target_id")})
        elif entity_type == "gate":
            context.update(
                {
                    "gate_kind": active.get("kind"),
                    "gate_name": active.get("name"),
                }
            )

    return context


def diff_entity_records(
    *,
    entity_type: str,
    path_hint: str,
    before_map: dict[str, Any],
    after_map: dict[str, Any],
    before_run_id: str | None,
    after_run_id: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    all_keys = sorted(set(before_map) | set(after_map))
    for key in all_keys:
        before_value = before_map.get(key)
        after_value = after_map.get(key)
        change_type = classify_change(before_value, after_value)
        if change_type == "unchanged-context":
            continue

        items.append(
            {
                "item_id": stable_item_id(entity_type, key),
                "entity_type": entity_type,
                "change_type": change_type,
                "path_hint": path_hint,
                "before_value": before_value,
                "after_value": after_value,
                "severity_hint": severity_hint(entity_type, change_type, before_value, after_value),
                "context": build_context(
                    entity_type,
                    key,
                    before_value,
                    after_value,
                    before_run_id,
                    after_run_id,
                ),
            }
        )

    return items


def write_items_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, sort_keys=False))
            handle.write("\n")


def load_items_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def count_items(items: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    by_entity: dict[str, int] = {}
    by_change_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}

    for item in items:
        by_entity[item["entity_type"]] = by_entity.get(item["entity_type"], 0) + 1
        by_change_type[item["change_type"]] = by_change_type.get(item["change_type"], 0) + 1
        by_severity[item["severity_hint"]] = by_severity.get(item["severity_hint"], 0) + 1

    return {
        "by_entity": dict(sorted(by_entity.items())),
        "by_change_type": dict(sorted(by_change_type.items())),
        "by_severity": dict(sorted(by_severity.items())),
    }


def write_summary(path: Path, manifest: dict[str, Any], items: list[dict[str, Any]]) -> None:
    counts = manifest["counts"]
    lines = [
        f"# Diffset Summary: {manifest['diffset_id']}",
        "",
        "## Runs",
        f"- before_run: {manifest['before_run_id'] or 'none'}",
        f"- after_run: {manifest['after_run_id']}",
        "",
        "## Item counts",
        f"- total_items: {manifest['item_count']}",
    ]

    for key, value in counts["by_entity"].items():
        lines.append(f"- entity.{key}: {value}")
    for key, value in counts["by_change_type"].items():
        lines.append(f"- change_type.{key}: {value}")
    for key, value in counts["by_severity"].items():
        lines.append(f"- severity.{key}: {value}")

    lines.extend(["", "## High severity items"])
    high_items = [item for item in items if item.get("severity_hint") == "high"]
    if high_items:
        for item in high_items[:30]:
            lines.append(
                f"- {item['item_id']} ({item['entity_type']}:{item['change_type']}) "
                f"-> {item['path_hint']}"
            )
    else:
        lines.append("- none")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_payload(schema: dict[str, Any], payload: Any) -> list[str]:
    validator = Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(payload)]


def validate_diffset_contract(
    root: Path,
    manifest: dict[str, Any],
    items: list[dict[str, Any]],
) -> None:
    manifest_schema_path = root / "schemas" / "diffset_manifest.schema.json"
    item_schema_path = root / "schemas" / "diffset_item.schema.json"

    if not manifest_schema_path.exists():
        raise FileNotFoundError(f"missing schema: {manifest_schema_path.relative_to(root)}")
    if not item_schema_path.exists():
        raise FileNotFoundError(f"missing schema: {item_schema_path.relative_to(root)}")

    manifest_schema = read_json(manifest_schema_path)
    item_schema = read_json(item_schema_path)

    manifest_errors = validate_payload(manifest_schema, manifest)
    if manifest_errors:
        raise ValueError(f"manifest schema validation failed: {manifest_errors}")

    item_errors: list[str] = []
    for index, item in enumerate(items):
        errors = validate_payload(item_schema, item)
        if errors:
            item_errors.append(f"item[{index}] {item.get('item_id', 'unknown')}: {errors}")
    if item_errors:
        raise ValueError(f"diffset item schema validation failed: {item_errors[:20]}")


def review_html() -> str:
    return """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Diffset Review</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --line: #d9e1ea;
      --text: #1f2933;
      --muted: #52606d;
      --accent: #0f4c81;
      --warn: #a16207;
      --high: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #eaf1f8, var(--bg));
      color: var(--text);
    }
    .page {
      max-width: 1400px;
      margin: 0 auto;
      padding: 20px;
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 16px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      box-shadow: 0 8px 18px rgba(15, 23, 42, 0.06);
    }
    h1 {
      margin: 0;
      font-size: 1.1rem;
      letter-spacing: 0.01em;
    }
    h2 {
      margin: 0 0 8px 0;
      font-size: 0.95rem;
      color: var(--muted);
      letter-spacing: 0.01em;
      text-transform: uppercase;
    }
    .stack { display: grid; gap: 10px; }
    .field { display: grid; gap: 4px; }
    label { font-size: 0.82rem; color: var(--muted); }
    input, select, textarea, button {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      background: #fff;
      color: var(--text);
    }
    textarea { min-height: 80px; resize: vertical; }
    button {
      cursor: pointer;
      background: #f8fafc;
    }
    button.primary {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      font-size: 0.86rem;
    }
    .stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      background: #fcfdff;
    }
    .list {
      max-height: calc(100vh - 220px);
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
    }
    th, td {
      text-align: left;
      padding: 6px;
      border-bottom: 1px solid #edf2f7;
      vertical-align: top;
    }
    tbody tr { cursor: pointer; }
    tbody tr:hover { background: #f7fafc; }
    tbody tr.active { background: #e6f0fb; }
    .pill {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 999px;
      font-size: 0.72rem;
      border: 1px solid var(--line);
      background: #fff;
    }
    .pill.warn { color: var(--warn); border-color: #f5d083; }
    .pill.high { color: var(--high); border-color: #f0a7a0; }
    .detail-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    pre {
      margin: 0;
      max-height: 360px;
      overflow: auto;
      padding: 8px;
      background: #0b1220;
      color: #d5e2ff;
      border-radius: 8px;
      font-family: "IBM Plex Mono", "SFMono-Regular", monospace;
      font-size: 0.76rem;
      line-height: 1.35;
    }
    .message {
      font-size: 0.82rem;
      color: var(--muted);
      min-height: 1.2em;
    }
    @media (max-width: 980px) {
      .page { grid-template-columns: 1fr; }
      .list { max-height: 320px; }
      .detail-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class=\"page\">
    <section class=\"panel stack\">
      <h1 id=\"title\">Diffset Review</h1>
      <div class=\"stats\" id=\"stats\"></div>

      <div class=\"field\">
        <label for=\"reviewer\">Reviewer</label>
        <input id=\"reviewer\" placeholder=\"name or handle\" />
      </div>

      <div class=\"field\">
        <label for=\"filterEntity\">Entity type</label>
        <select id=\"filterEntity\">
          <option value=\"\">All</option>
          <option value=\"gate\">gate</option>
          <option value=\"category\">category</option>
          <option value=\"guideline\">guideline</option>
          <option value=\"coverage\">coverage</option>
          <option value=\"scope\">scope</option>
        </select>
      </div>

      <div class=\"field\">
        <label for=\"filterChange\">Change type</label>
        <select id=\"filterChange\">
          <option value=\"\">All</option>
          <option value=\"added\">added</option>
          <option value=\"modified\">modified</option>
          <option value=\"removed\">removed</option>
        </select>
      </div>

      <div class=\"field\">
        <label for=\"filterSeverity\">Severity</label>
        <select id=\"filterSeverity\">
          <option value=\"\">All</option>
          <option value=\"info\">info</option>
          <option value=\"warn\">warn</option>
          <option value=\"high\">high</option>
        </select>
      </div>

      <div class=\"field\">
        <label for=\"search\">Search</label>
        <input id=\"search\" placeholder=\"item id, text, context\" />
      </div>

      <div class=\"stack\" style=\"grid-template-columns: 1fr 1fr; display:grid;\">
        <button id=\"saveDraft\">Save Draft</button>
        <button id=\"exportFeedback\" class=\"primary\">Export Feedback</button>
      </div>

      <div class=\"message\" id=\"message\"></div>

      <div class=\"list\">
        <table>
          <thead>
            <tr><th>Verdict</th><th>Severity</th><th>Entity</th><th>Change</th><th>Item</th></tr>
          </thead>
          <tbody id=\"rows\"></tbody>
        </table>
      </div>
    </section>

    <section class=\"panel stack\">
      <h2>Selected Item</h2>
      <div id=\"selectedMeta\" class=\"message\">Select a row to inspect details.</div>
      <div class=\"detail-grid\">
        <div>
          <h2>Before</h2>
          <pre id=\"beforeView\">null</pre>
        </div>
        <div>
          <h2>After</h2>
          <pre id=\"afterView\">null</pre>
        </div>
      </div>
      <div>
        <h2>Context</h2>
        <pre id=\"contextView\">null</pre>
      </div>

      <div class=\"field\">
        <label for=\"verdict\">Verdict</label>
        <select id=\"verdict\">
          <option value=\"\">Not set</option>
          <option value=\"accept\">accept</option>
          <option value=\"needs_change\">needs_change</option>
          <option value=\"block\">block</option>
        </select>
      </div>

      <div class=\"field\">
        <label for=\"status\">Status</label>
        <select id=\"status\">
          <option value=\"open\">open</option>
          <option value=\"resolved\">resolved</option>
        </select>
      </div>

      <div class=\"field\">
        <label for=\"comment\">Comment</label>
        <textarea id=\"comment\" placeholder=\"review notes\"></textarea>
      </div>

      <button id=\"applyItem\" class=\"primary\">Apply To Selected Item</button>
    </section>
  </div>

  <script>
    const state = {
      manifest: null,
      items: [],
      selectedItemId: null,
      reviewItems: {},
      reviewedAt: null,
    };

    function setMessage(text) {
      document.getElementById('message').textContent = text || '';
    }

    async function fetchJSON(path) {
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Failed to fetch ${path}: ${response.status}`);
      }
      return await response.json();
    }

    async function fetchItemsJSONL(path) {
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Failed to fetch ${path}: ${response.status}`);
      }
      const text = await response.text();
      return text
        .split('\\n')
        .map((line) => line.trim())
        .filter((line) => line.length > 0)
        .map((line) => JSON.parse(line));
    }

    async function fetchReviewState() {
      try {
        return await fetchJSON('/api/review_state');
      } catch (_error) {
        try {
          return await fetchJSON('review_state.json');
        } catch (_nestedError) {
          return null;
        }
      }
    }

    function activeReviewItems() {
      return Object.values(state.reviewItems).filter((item) => item && item.verdict);
    }

    function reviewStats() {
      let accept = 0;
      let needsChange = 0;
      let block = 0;
      for (const item of activeReviewItems()) {
        if (item.verdict === 'accept') accept += 1;
        if (item.verdict === 'needs_change') needsChange += 1;
        if (item.verdict === 'block') block += 1;
      }
      return { accept, needsChange, block };
    }

    function renderStats(filteredCount) {
      const statsNode = document.getElementById('stats');
      const manifest = state.manifest || {};
      const r = reviewStats();
      const total = state.items.length;
      statsNode.innerHTML = '';

      const entries = [
        ['Total', String(total)],
        ['Filtered', String(filteredCount)],
        ['Accept', String(r.accept)],
        ['Needs Change', String(r.needsChange)],
        ['Block', String(r.block)],
        ['After Run', manifest.after_run_id || 'unknown'],
      ];

      for (const [label, value] of entries) {
        const box = document.createElement('div');
        box.className = 'stat';
        box.innerHTML = `<strong>${label}</strong><div>${value}</div>`;
        statsNode.appendChild(box);
      }
    }

    function filteredItems() {
      const filterEntity = document.getElementById('filterEntity').value.trim();
      const filterChange = document.getElementById('filterChange').value.trim();
      const filterSeverity = document.getElementById('filterSeverity').value.trim();
      const search = document.getElementById('search').value.trim().toLowerCase();

      return state.items.filter((item) => {
        if (filterEntity && item.entity_type !== filterEntity) return false;
        if (filterChange && item.change_type !== filterChange) return false;
        if (filterSeverity && item.severity_hint !== filterSeverity) return false;
        if (!search) return true;

        const haystack = [
          item.item_id,
          item.path_hint,
          JSON.stringify(item.context || {}),
          JSON.stringify(item.before_value),
          JSON.stringify(item.after_value),
        ]
          .join(' ')
          .toLowerCase();
        return haystack.includes(search);
      });
    }

    function severityPill(severity) {
      const klass = severity === 'high' ? 'pill high' : severity === 'warn' ? 'pill warn' : 'pill';
      return `<span class=\"${klass}\">${severity}</span>`;
    }

    function renderRows() {
      const rowsNode = document.getElementById('rows');
      rowsNode.innerHTML = '';
      const rows = filteredItems();
      renderStats(rows.length);

      for (const item of rows) {
        const review = state.reviewItems[item.item_id] || {};
        const row = document.createElement('tr');
        if (item.item_id === state.selectedItemId) {
          row.classList.add('active');
        }

        row.innerHTML = `
          <td>${review.verdict || ''}</td>
          <td>${severityPill(item.severity_hint)}</td>
          <td>${item.entity_type}</td>
          <td>${item.change_type}</td>
          <td>${item.item_id}</td>
        `;
        row.onclick = () => {
          state.selectedItemId = item.item_id;
          renderRows();
          renderSelected();
        };
        rowsNode.appendChild(row);
      }
    }

    function renderSelected() {
      const meta = document.getElementById('selectedMeta');
      const beforeView = document.getElementById('beforeView');
      const afterView = document.getElementById('afterView');
      const contextView = document.getElementById('contextView');
      const verdict = document.getElementById('verdict');
      const status = document.getElementById('status');
      const comment = document.getElementById('comment');

      const selected = state.items.find((item) => item.item_id === state.selectedItemId);
      if (!selected) {
        meta.textContent = 'Select a row to inspect details.';
        beforeView.textContent = 'null';
        afterView.textContent = 'null';
        contextView.textContent = 'null';
        verdict.value = '';
        status.value = 'open';
        comment.value = '';
        return;
      }

      const review = state.reviewItems[selected.item_id] || {};
      meta.textContent =
        `${selected.item_id} | ${selected.entity_type} | ` +
        `${selected.change_type} | ${selected.path_hint}`;
      beforeView.textContent = JSON.stringify(selected.before_value, null, 2);
      afterView.textContent = JSON.stringify(selected.after_value, null, 2);
      contextView.textContent = JSON.stringify(selected.context || {}, null, 2);
      verdict.value = review.verdict || '';
      status.value = review.status || 'open';
      comment.value = review.comment || '';
    }

    function buildReviewPayload() {
      const reviewer = document.getElementById('reviewer').value.trim() || 'unknown';
      const reviewedAt = new Date().toISOString();
      state.reviewedAt = reviewedAt;
      return {
        version: 1,
        diffset_id: state.manifest.diffset_id,
        reviewer,
        reviewed_at: reviewedAt,
        items: activeReviewItems().sort((a, b) => a.item_id.localeCompare(b.item_id)),
      };
    }

    async function postJSON(path, payload) {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body.error || `POST ${path} failed with ${response.status}`);
      }
      return body;
    }

    function applySelectedItem() {
      const selected = state.items.find((item) => item.item_id === state.selectedItemId);
      if (!selected) {
        setMessage('Select an item before applying review values.');
        return;
      }

      const verdict = document.getElementById('verdict').value.trim();
      const statusNode = document.getElementById('status');
      const comment = document.getElementById('comment').value;
      let status = statusNode.value.trim() || 'open';

      if (!verdict) {
        delete state.reviewItems[selected.item_id];
        setMessage(`Cleared review for ${selected.item_id}`);
      } else {
        if (status !== 'open' && status !== 'resolved') {
          status = verdict === 'accept' ? 'resolved' : 'open';
        }
        state.reviewItems[selected.item_id] = {
          item_id: selected.item_id,
          verdict,
          comment,
          status,
          updated_at: new Date().toISOString(),
        };
        setMessage(`Applied review values for ${selected.item_id}`);
      }

      renderRows();
      renderSelected();
    }

    async function saveDraft() {
      const payload = buildReviewPayload();
      try {
        await postJSON('/api/review_state', payload);
        setMessage('Saved draft review_state.json');
      } catch (error) {
        setMessage(`Save draft failed: ${error.message}`);
      }
    }

    async function exportFeedback() {
      const payload = buildReviewPayload();
      try {
        const result = await postJSON('/api/export_feedback', payload);
        setMessage(`Exported tracked feedback -> ${result.feedback_path}`);
      } catch (error) {
        setMessage(`Export feedback failed: ${error.message}`);
      }
    }

    async function init() {
      try {
        state.manifest = await fetchJSON('manifest.json');
        state.items = await fetchItemsJSONL('items.jsonl');
      } catch (error) {
        setMessage(`Failed to load diffset: ${error.message}`);
        return;
      }

      document.getElementById('title').textContent = `Diffset Review: ${state.manifest.diffset_id}`;

      const reviewState = await fetchReviewState();
      if (reviewState && Array.isArray(reviewState.items)) {
        for (const item of reviewState.items) {
          if (item && item.item_id) {
            state.reviewItems[item.item_id] = item;
          }
        }
        if (reviewState.reviewer) {
          document.getElementById('reviewer').value = reviewState.reviewer;
        }
      }

      if (state.items.length > 0) {
        state.selectedItemId = state.items[0].item_id;
      }

      document.getElementById('applyItem').onclick = applySelectedItem;
      document.getElementById('saveDraft').onclick = saveDraft;
      document.getElementById('exportFeedback').onclick = exportFeedback;

      for (const id of ['filterEntity', 'filterChange', 'filterSeverity', 'search']) {
        document.getElementById(id).addEventListener('input', () => {
          renderRows();
          renderSelected();
        });
      }

      renderRows();
      renderSelected();
    }

    init();
  </script>
</body>
</html>
"""


def build_diffset_bundle(
    root: Path,
    *,
    after_run_id: str,
    before_run_id: str | None,
    output_root: Path,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    after_manifest = load_run_manifest(root, after_run_id)
    resolved_before_run = infer_before_run(root, after_manifest, before_run_id)

    diffset_id = f"diffset-{resolved_before_run or 'bootstrap'}__{after_run_id}"
    bundle_dir = root / output_root / diffset_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    before_manifest: dict[str, Any] | None = None
    if resolved_before_run:
        try:
            before_manifest = load_run_manifest(root, resolved_before_run)
        except FileNotFoundError:
            before_manifest = None

    source_before: dict[str, str | None] = {}
    source_after: dict[str, str | None] = {}

    before_categories_path = resolve_artifact_source(
        root,
        resolved_before_run,
        ARTIFACT_PATHS["category"],
        allow_repo_fallback=False,
    )
    after_categories_path = resolve_artifact_source(
        root,
        after_run_id,
        ARTIFACT_PATHS["category"],
        allow_repo_fallback=True,
    )
    source_before[ARTIFACT_PATHS["category"]] = (
        str(before_categories_path.relative_to(root)) if before_categories_path else None
    )
    source_after[ARTIFACT_PATHS["category"]] = (
        str(after_categories_path.relative_to(root)) if after_categories_path else None
    )

    before_guidelines_path = resolve_artifact_source(
        root,
        resolved_before_run,
        ARTIFACT_PATHS["guideline"],
        allow_repo_fallback=False,
    )
    after_guidelines_path = resolve_artifact_source(
        root,
        after_run_id,
        ARTIFACT_PATHS["guideline"],
        allow_repo_fallback=True,
    )
    source_before[ARTIFACT_PATHS["guideline"]] = (
        str(before_guidelines_path.relative_to(root)) if before_guidelines_path else None
    )
    source_after[ARTIFACT_PATHS["guideline"]] = (
        str(after_guidelines_path.relative_to(root)) if after_guidelines_path else None
    )

    before_coverage_path = resolve_artifact_source(
        root,
        resolved_before_run,
        ARTIFACT_PATHS["coverage"],
        allow_repo_fallback=False,
    )
    after_coverage_path = resolve_artifact_source(
        root,
        after_run_id,
        ARTIFACT_PATHS["coverage"],
        allow_repo_fallback=True,
    )
    source_before[ARTIFACT_PATHS["coverage"]] = (
        str(before_coverage_path.relative_to(root)) if before_coverage_path else None
    )
    source_after[ARTIFACT_PATHS["coverage"]] = (
        str(after_coverage_path.relative_to(root)) if after_coverage_path else None
    )

    before_scope_path = resolve_artifact_source(
        root,
        resolved_before_run,
        ARTIFACT_PATHS["scope"],
        allow_repo_fallback=False,
    )
    after_scope_path = resolve_artifact_source(
        root,
        after_run_id,
        ARTIFACT_PATHS["scope"],
        allow_repo_fallback=True,
    )
    source_before[ARTIFACT_PATHS["scope"]] = (
        str(before_scope_path.relative_to(root)) if before_scope_path else None
    )
    source_after[ARTIFACT_PATHS["scope"]] = (
        str(after_scope_path.relative_to(root)) if after_scope_path else None
    )

    if after_categories_path is None:
        raise FileNotFoundError(f"after-run category artifact missing for {after_run_id}")
    if after_guidelines_path is None:
        raise FileNotFoundError(f"after-run guideline artifact missing for {after_run_id}")
    if after_coverage_path is None:
        raise FileNotFoundError(f"after-run coverage artifact missing for {after_run_id}")
    if after_scope_path is None:
        raise FileNotFoundError(f"after-run scope artifact missing for {after_run_id}")

    before_categories = load_categories(before_categories_path)
    after_categories = load_categories(after_categories_path)
    before_guidelines = load_guidelines(before_guidelines_path)
    after_guidelines = load_guidelines(after_guidelines_path)
    before_coverage = load_coverage(before_coverage_path)
    after_coverage = load_coverage(after_coverage_path)
    before_scope = load_scope(before_scope_path)
    after_scope = load_scope(after_scope_path)
    before_gates = load_gate_map(before_manifest)
    after_gates = load_gate_map(after_manifest)

    items: list[dict[str, Any]] = []
    items.extend(
        diff_entity_records(
            entity_type="gate",
            path_hint=".cache/ops/runs/<run_id>/run_manifest.json",
            before_map=before_gates,
            after_map=after_gates,
            before_run_id=resolved_before_run,
            after_run_id=after_run_id,
        )
    )
    items.extend(
        diff_entity_records(
            entity_type="category",
            path_hint=ARTIFACT_PATHS["category"],
            before_map=before_categories,
            after_map=after_categories,
            before_run_id=resolved_before_run,
            after_run_id=after_run_id,
        )
    )
    items.extend(
        diff_entity_records(
            entity_type="guideline",
            path_hint=ARTIFACT_PATHS["guideline"],
            before_map=before_guidelines,
            after_map=after_guidelines,
            before_run_id=resolved_before_run,
            after_run_id=after_run_id,
        )
    )
    items.extend(
        diff_entity_records(
            entity_type="coverage",
            path_hint=ARTIFACT_PATHS["coverage"],
            before_map=before_coverage,
            after_map=after_coverage,
            before_run_id=resolved_before_run,
            after_run_id=after_run_id,
        )
    )
    items.extend(
        diff_entity_records(
            entity_type="scope",
            path_hint=ARTIFACT_PATHS["scope"],
            before_map=before_scope,
            after_map=after_scope,
            before_run_id=resolved_before_run,
            after_run_id=after_run_id,
        )
    )

    items.sort(
        key=lambda item: (
            ENTITY_ORDER.index(item["entity_type"]),
            item["change_type"],
            item["item_id"],
        )
    )

    counts = count_items(items)
    manifest = {
        "version": 1,
        "diffset_id": diffset_id,
        "generated_at": utc_now(),
        "before_run_id": resolved_before_run,
        "after_run_id": after_run_id,
        "bundle_rel_path": str(bundle_dir.relative_to(root)),
        "item_count": len(items),
        "counts": counts,
        "artifact_sources": {
            "before": source_before,
            "after": source_after,
        },
    }

    validate_diffset_contract(root, manifest, items)

    write_json(bundle_dir / "manifest.json", manifest)
    write_items_jsonl(bundle_dir / "items.jsonl", items)
    write_summary(bundle_dir / "summary.md", manifest, items)

    review_state_path = bundle_dir / "review_state.json"
    if not review_state_path.exists():
        write_json(
            review_state_path,
            {
                "version": 1,
                "diffset_id": diffset_id,
                "reviewer": "",
                "reviewed_at": None,
                "items": [],
            },
        )

    (bundle_dir / "review.html").write_text(review_html(), encoding="utf-8")
    return bundle_dir, manifest, items


def main() -> int:
    args = parse_args()
    root = repo_root()

    try:
        bundle_dir, manifest, _items = build_diffset_bundle(
            root,
            after_run_id=args.after_run,
            before_run_id=args.before_run,
            output_root=args.output_root,
        )
    except Exception as exc:
        print(f"[diffset][error] {exc}")
        return EXIT_RUNTIME_FAIL

    output_payload = {
        "diffset_id": manifest["diffset_id"],
        "bundle_rel_path": str(bundle_dir.relative_to(root)),
        "before_run_id": manifest["before_run_id"],
        "after_run_id": manifest["after_run_id"],
        "item_count": manifest["item_count"],
    }
    if args.json_output:
        write_json(root / args.json_output, output_payload)

    print(f"[diffset] built diffset_id={manifest['diffset_id']}")
    print(f"[diffset] bundle -> {bundle_dir.relative_to(root)}")
    print(f"[diffset] items={manifest['item_count']}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
