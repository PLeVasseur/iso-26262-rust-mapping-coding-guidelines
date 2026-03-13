# Core Docs Chunking Strategy

## Purpose

Define an implementation-ready chunking strategy for `core_docs` retrieval so queries return
real Rust core API evidence (not synthetic row summaries), with platform-aware behavior for
safety-critical targets.

## Scope and Non-Goals

- Scope:
  - rustdoc JSON item-level extraction for `core`
  - target-aware metadata and retrieval ranking
  - chunking templates for core item kinds used by safety-focused queries
- Non-goals:
  - replacing `rust_reference` ingestion strategy
  - introducing target-specific top-level dispatchers

## Source and Target Matrix

- Stable content baseline: Rust `1.83.1` semantics
- Extraction toolchain: latest green nightly that passes rustdoc-json preflight
- Canonical base target: `x86_64-unknown-linux-gnu`
- Required safety overlays:
  - `x86_64-pc-nto-qnx710`
  - `aarch64-unknown-nto-qnx710`
  - `x86_64-wrs-vxworks`
  - `aarch64-wrs-vxworks`
  - `thumbv7em-none-eabihf`

Cross-host steering rule:

- Extraction host may differ from target.
- Output semantics are controlled by pinned source revision plus explicit `--target`.

## Chunk Unit and Budgets

- Primary chunk unit: rustdoc item boundary.
- Item kinds covered:
  - module
  - struct / enum / union
  - trait
  - impl
  - function / method
  - associated const / associated type
- Token budgets:
  - target: 220-320 tokens
  - min: 120 tokens
  - max: 520 tokens

Overflow split policy:

- Keep metadata header and signature in every split chunk.
- Split body by semantic sections in priority order:
  - Safety
  - Panics
  - Errors/Returns
  - Memory ordering/Concurrency notes
  - Examples
  - Misc description

## Required Fields Per Chunk

Each chunk MUST include:

- `item_path`: fully qualified path
- `item_kind`
- `signature`
- `stability`: stable/unstable/deprecated info
- `safety_notes`: explicit safety preconditions if present
- `panic_behavior`: explicit panic conditions if present
- `example_snippets`: normalized examples where present
- `target_triple`
- `target_os`
- `target_arch`
- `target_env`
- `cfg_signature`: normalized sorted cfg kv payload
- `cfg_signature_sha256`
- `source_anchor`

## Canonical Chunk Templates

### Template: function/method

Header:

- Item: `<item_path>`
- Kind: `fn` or `method`
- Signature: `<signature>`
- Stability: `<stability>`
- Target: `<target_triple>`

Body sections in order:

1. Summary docs paragraph
2. Safety section (if present)
3. Panics section (if present)
4. Errors/returns behavior section (if present)
5. Memory ordering / thread-safety notes (if present)
6. Example snippet(s)

### Template: trait

Header:

- Item: `<item_path>`
- Kind: `trait`
- Stability
- Target

Body:

1. Trait intent/contract summary
2. Safety or unsafety requirements
3. Required methods key points
4. Auto-trait interactions when documented

### Template: impl

Header:

- Item: `<impl target path>`
- Kind: `impl`
- Signature: `<impl signature>`
- Target

Body:

1. Impl applicability summary
2. Method behavior deltas from trait/base docs
3. Safety/panic/memory notes

### Template: struct/enum

Header:

- Item: `<item_path>`
- Kind: `struct` or `enum`
- Signature
- Stability
- Target

Body:

1. Type contract summary
2. Invariants/ownership constraints
3. Related constructor/accessor behavior
4. Safety/panic notes

## Pseudocode: End-to-End Build

```text
for target in TARGET_MATRIX:
  rustdoc_json = generate_rustdoc_json(source_rev=CORE_DOCS_REVISION, target=target)
  validate_schema(rustdoc_json)
  write_manifest(target, rustc_vv, sha256s)

  items = load_items(rustdoc_json)
  for item in items:
    if not should_index(item):
      continue

    normalized = normalize_item(item)
    metadata = build_target_metadata(target, rustdoc_json.cfg)
    sections = collect_sections(normalized)
    chunks = split_with_budget(sections, min=120, target=260, max=520)

    for chunk in chunks:
      row = assemble_chunk_record(
        item_path=normalized.path,
        item_kind=normalized.kind,
        signature=normalized.signature,
        stability=normalized.stability,
        safety_notes=normalized.safety,
        panic_behavior=normalized.panics,
        example_snippets=normalized.examples,
        target_metadata=metadata,
        source_anchor=normalized.anchor,
      )
      write_docs_sections_chunks(row)

dedupe_within_target(exact_text=True, same_item_path=True)
populate_fts()
validate_realism_gates()
record_provenance()
```

## Dedupe and Noise Control

- Exact dedupe within same target + same item path.
- Keep cross-target variants (do not dedupe across targets).
- Reduce blanket impl noise:
  - keep only impl chunks with distinct behavior/safety text
  - collapse pure boilerplate impl blocks

## Target-Aware Retrieval Behavior

- Maintain explicit alias map for target hints in queries.
  - QNX: `qnx`, `nto`, `nto71`, `qnx710`
  - VxWorks: `vxworks`, `wrs-vxworks`
  - Embedded: `embedded`, `no_std`, `thumbv7em`
- If target hint present: boost exact target family first.
- If no target hint: canonical target (`x86_64-unknown-linux-gnu`) prioritized, overlays still eligible.

## Realistic Query Taxonomy (API-first)

### Ergonomics

- When should I use `Option::ok_or_else` instead of `ok_or`?
- What is the idiomatic way to convert `Option<T>` into `Result<T, E>` without eager error construction?
- How do I chain `Result` transformations while preserving error context in `core`?
- When should I use `map_or` vs `map_or_else` on `Option`?
- Which `Result` combinators avoid nested `match` in safety-critical control flow?

### Safety and Panic Contracts

- What are the safety preconditions for `AtomicUsize::from_ptr`?
- Which operations on `RefCell` can panic at runtime?
- Which `core` APIs explicitly document panic conditions for bounds/indexing-like behavior?
- What does `unwrap_unchecked` require, and what is the safer alternative?
- How do panic guarantees differ between `get`/`get_unchecked` style operations?

### Concurrency and Ordering

- Which ordering values are valid for `AtomicUsize::load`?
- How should failure ordering be selected for `compare_exchange`?
- When is `SeqCst` necessary vs Acquire/Release in safety review context?
- What ordering constraints apply to `fetch_add` and related RMW operations?
- Which atomic APIs are available on this target and what are the caveats?

### Traits and Auto-Traits

- Why can `RefCell<T>` be `Send` but not `Sync`?
- What conditions make a type `Send` in `core` docs?
- What conditions make a type `Sync` in `core` docs?
- How do auto-trait implementations interact with interior mutability wrappers?
- Where do trait docs specify thread-safety assumptions for shared references?

### Platform-Specific Queries

- For QNX targets, what target env values (`nto70`, `nto71`, `nto71_iosock`) alter cfg applicability?
- For VxWorks targets, which core behavior notes are target-conditional?
- For embedded `thumbv7em-none-eabihf`, what no_std constraints affect API availability?
- Are there target-specific ABI/cfg notes that change safety assumptions?
- Which chunks apply to canonical Linux only versus QNX/VxWorks overlays?

### Hard Negatives / Abstain

- What is the best SQL isolation level for distributed transactions in PostgreSQL?
- How do I configure Linux cgroups v2 CPU quotas in Kubernetes?
- What is the recommended AUTOSAR CanNm timer for startup synchronization?
- How do I enable TLS session tickets in NGINX?
- Which MISRA-C directive covers dynamic memory deallocation timing?

## Prompt Pack Acceptance Criteria

- At least 25 prompts total.
- At least 5 prompts each in:
  - ergonomics
  - safety/panic
  - concurrency
  - trait behavior
- At least 20% hard negatives/abstain.
- Every prompt includes:
  - `expected_item_kinds`
  - `required_evidence_fields`
  - `target_scope`
  - `expect_abstain`

## Synthetic Regression Ban

The following are forbidden in active core_docs build path:

- synthetic chunk prefixes like `Core docs coverage for ISO 26262 Table 1 row`
- one-row-one-chunk generation based purely on Table 1 markers

Regression checks:

- unit denylist test
- grep gate in verification checklist
