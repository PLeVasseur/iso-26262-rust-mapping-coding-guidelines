#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import EXIT_SUCCESS, repo_root, utc_now, write_yaml
from _fls_proxy import slug_ascii

CHAPTER_DEFS = [
    {
        "title": "Associated Items",
        "keywords": ["associated", "trait", "impl", "method", "item"],
    },
    {
        "title": "Attributes",
        "keywords": ["attribute", "cfg", "derive", "lint", "feature"],
    },
    {
        "title": "Concurrency",
        "keywords": ["thread", "sync", "atomic", "mutex", "race"],
    },
    {
        "title": "Entities and Resolution",
        "keywords": ["name", "path", "module", "resolve", "namespace"],
    },
    {
        "title": "Exceptions and Errors",
        "keywords": ["error", "result", "panic", "unwrap", "handling"],
    },
    {
        "title": "Expressions",
        "keywords": ["expression", "operator", "match", "if", "loop"],
    },
    {
        "title": "FFI",
        "keywords": ["ffi", "extern", "abi", "c", "boundary"],
    },
    {
        "title": "Functions",
        "keywords": ["function", "fn", "argument", "return", "signature"],
    },
    {
        "title": "Generics",
        "keywords": ["generic", "type parameter", "lifetime", "bound", "where"],
    },
    {
        "title": "Implementations",
        "keywords": ["impl", "trait", "coherence", "orphan", "method"],
    },
    {
        "title": "Inline Assembly",
        "keywords": ["asm", "register", "unsafe", "architecture", "constraint"],
    },
    {
        "title": "Macros",
        "keywords": ["macro", "token", "expansion", "hygiene", "syntax"],
    },
    {
        "title": "Ownership and Destruction",
        "keywords": ["ownership", "borrow", "drop", "move", "lifetime"],
    },
    {
        "title": "Patterns",
        "keywords": ["pattern", "destructure", "match", "binding", "wildcard"],
    },
    {
        "title": "Program Structure and Compilation",
        "keywords": ["crate", "module", "compilation", "feature", "build"],
    },
    {
        "title": "Statements",
        "keywords": ["statement", "let", "assignment", "item", "semicolon"],
    },
    {
        "title": "Types and Traits",
        "keywords": ["type", "trait", "conversion", "cast", "bound"],
    },
    {
        "title": "Unsafety",
        "keywords": ["unsafe", "ub", "raw pointer", "invariant", "soundness"],
    },
    {
        "title": "Values",
        "keywords": ["value", "literal", "const", "static", "initialization"],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build synthetic FLS proxy inventory")
    parser.add_argument("--output", type=Path, default=Path("data/fls_inventory.yaml"))
    parser.add_argument("--source", default="synthetic-proxy-v1")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()

    chapters = []
    paragraphs = []
    for chapter in CHAPTER_DEFS:
        slug = slug_ascii(chapter["title"])
        chapter_id = f"fls_ch_{slug}"
        keywords = list(dict.fromkeys(chapter["keywords"]))
        chapters.append(
            {
                "chapter_id": chapter_id,
                "title": chapter["title"],
                "keywords": keywords,
            }
        )
        paragraphs.append(
            {
                "fls_ref": f"fls_{slug}_core",
                "chapter_id": chapter_id,
                "label": f"{chapter['title']} core semantics",
                "keywords": keywords,
            }
        )
        paragraphs.append(
            {
                "fls_ref": f"fls_{slug}_safety",
                "chapter_id": chapter_id,
                "label": f"{chapter['title']} safety constraints",
                "keywords": list(dict.fromkeys(keywords + ["safety", "deterministic", "review"])),
            }
        )

    payload = {
        "version": 1,
        "generated_at": utc_now(),
        "source": args.source,
        "chapters": chapters,
        "paragraphs": paragraphs,
    }
    output_path = root / args.output
    write_yaml(output_path, payload)
    print(
        "[fls-inventory] wrote "
        f"chapters={len(chapters)} paragraphs={len(paragraphs)} -> {output_path.relative_to(root)}"
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
