# Core Docs Mapping Spec

This document defines deterministic mapping from core-docs source entities into retrieval compatibility tables.

## Primary Retrieval Unit

- Item-level chunks are the primary retrieval unit.
- One item can produce one or more chunks when docs exceed token targets.

## Mapping Rules

- `modules` -> `docs`
  - `modules.module_id` -> `docs.doc_uid`
  - `modules.path` -> `docs.source_path`
  - `modules.title` -> `docs.title`

- `items` -> `sections`
  - `items.item_id` -> `sections.section_id`
  - `items.module_id` -> `sections.document_id`
  - `items.fq_path` -> `sections.heading`
  - generated anchor slug from `items.fq_path` -> `sections.anchor`

- `items` + `contracts` + `examples` -> `chunks`
  - `chunks.raw_text` and `chunks.clean_text` are deterministic concatenations in this order:
    1. item signature
    2. item docs summary
    3. safety contract text
    4. panic contract text
    5. examples
  - each chunk UID is `sha256(section_id::order_index::clean_text_lower)`.

- `chunks` -> `chunk_spans`
  - `chunk_spans.source_anchor` derives from rustdoc canonical item URL.
  - offsets span the selected raw_text segment.

## Table 1 Compatibility

- `table1_rows` and `table1_row_profile_terms` remain canonical join points for query/eval contract resolution.
- Item-level extracted evidence influences queryability via term projection in retrieval logic, not by changing row identity.
