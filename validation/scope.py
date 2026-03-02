from __future__ import annotations

from typing import Any

CONSTRUCT_FAMILIES: dict[str, set[str]] = {
    "atomics": {
        "AtomicBool",
        "AtomicUsize",
        "AtomicI8",
        "AtomicI16",
        "AtomicI32",
        "AtomicI64",
        "AtomicU8",
        "AtomicU16",
        "AtomicU32",
        "AtomicU64",
        "AtomicPtr",
        "Ordering",
        "fence",
        "compiler_fence",
        "atomic",
    },
    "concurrency_sync": {"Mutex", "RwLock", "Condvar", "Barrier", "Once", "OnceLock", "LazyLock"},
    "interior_mutability": {"Cell", "RefCell", "UnsafeCell", "OnceCell"},
    "smart_pointers": {"Box", "Rc", "Arc", "Weak", "Cow"},
    "unsafe_primitives": {
        "unsafe",
        "raw_pointer",
        "NonNull",
        "MaybeUninit",
        "ManuallyDrop",
        "transmute",
        "transmute_copy",
    },
    "pinning": {"Pin", "Unpin", "pin!"},
    "error_handling": {
        "Result",
        "Option",
        "panic",
        "unwrap",
        "expect",
        "unwrap_unchecked",
        "unwrap_or",
        "unwrap_or_else",
    },
    "memory_layout": {
        "repr",
        "align",
        "size_of",
        "align_of",
        "Layout",
        "alloc",
        "GlobalAlloc",
        "Allocator",
    },
    "traits_generics": {"Send", "Sync", "Copy", "Clone", "Drop", "impl", "dyn", "trait", "where"},
    "lifetime_borrowing": {"lifetime", "borrow", "reference", "NLL", "reborrow", "elision"},
    "iterators": {
        "Iterator",
        "IntoIterator",
        "iter",
        "iter_mut",
        "into_iter",
        "collect",
        "map",
        "filter",
        "fold",
    },
    "closures": {"Fn", "FnMut", "FnOnce", "closure", "move"},
    "collections": {
        "Vec",
        "HashMap",
        "BTreeMap",
        "HashSet",
        "BTreeSet",
        "VecDeque",
        "LinkedList",
        "BinaryHeap",
    },
    "string_types": {"String", "str", "CStr", "CString", "OsStr", "OsString", "Path", "PathBuf"},
    "io": {
        "Read",
        "Write",
        "BufRead",
        "BufReader",
        "BufWriter",
        "File",
        "stdin",
        "stdout",
        "stderr",
    },
    "async_runtime": {
        "async",
        "await",
        "Future",
        "Poll",
        "Waker",
        "Pin<Future>",
        "tokio",
        "async-std",
    },
}

MAX_CONSTRUCT_FAMILIES = 2


def _normalize(term: str) -> str:
    return term.strip().split("::")[-1]


def _match_membership(term: str, members: set[str]) -> bool:
    lower = term.lower()
    return any(term == member or lower == member.lower() for member in members)


def _classify_construct_scope(
    construct_terms: list[str],
    *,
    families_config: dict[str, set[str]] | None = None,
) -> tuple[set[str], list[str]]:
    active_families = families_config if families_config else CONSTRUCT_FAMILIES
    families_touched: set[str] = set()
    unknown_terms: list[str] = []
    for term in construct_terms:
        normalized = _normalize(term)
        matched = False
        for family_name, members in active_families.items():
            if _match_membership(normalized, members):
                families_touched.add(family_name)
                matched = True
                break
        if not matched:
            unknown_terms.append(term)
    return families_touched, unknown_terms


def _term_in_family(
    term: str, family_name: str, families: dict[str, set[str]] | None = None
) -> bool:
    active_families = families if families else CONSTRUCT_FAMILIES
    normalized = _normalize(term)
    members = active_families.get(family_name, set())
    return _match_membership(normalized, members)


def check_scope_cardinality(
    construct_terms: list[str],
    prompt_id: str,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    families_config = CONSTRUCT_FAMILIES
    max_families = MAX_CONSTRUCT_FAMILIES

    if config:
        if isinstance(config.get("families"), dict):
            families_config = {
                str(k): {str(item) for item in v}
                for k, v in config["families"].items()
                if isinstance(v, list)
            }
        max_families = int(config.get("max_families", MAX_CONSTRUCT_FAMILIES))
        overrides = config.get("per_prompt_overrides", {})
        if isinstance(overrides, dict) and prompt_id in overrides:
            max_families = int(overrides[prompt_id])

    families, unknown_terms = _classify_construct_scope(
        construct_terms,
        families_config=families_config,
    )

    result: dict[str, Any] = {
        "prompt_id": prompt_id,
        "construct_terms": construct_terms,
        "families_touched": sorted(families),
        "family_count": len(families),
        "max_allowed": max_families,
        "passed": len(families) <= max_families,
        "config_source": "yaml_override" if config else "hardcoded_default",
    }

    if unknown_terms:
        result["unknown_terms"] = unknown_terms
        if len(unknown_terms) > len(construct_terms) / 2:
            result["unclassified_dominant"] = True
            result["unclassified_warning"] = (
                f"{len(unknown_terms)}/{len(construct_terms)} terms are unclassified. "
                "Consider updating scope taxonomy before next run."
            )

    if not result["passed"]:
        result["recommendation"] = (
            f"Scope spans {len(families)} construct families "
            f"({', '.join(sorted(families))}). Consider splitting into focused guidelines."
        )
        result["suggested_splits"] = [
            {
                "family": family,
                "terms": [
                    term
                    for term in construct_terms
                    if _term_in_family(term, family, families_config)
                ],
            }
            for family in sorted(families)
        ]

    return bool(result["passed"]), result
