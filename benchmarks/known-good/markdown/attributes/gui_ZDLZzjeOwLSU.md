---
guideline_id: gui_ZDLZzjeOwLSU
source_path: src/coding-guidelines/attributes/gui_ZDLZzjeOwLSU.rst
source_sha: 1c7d6bb9448a25401db3b411d1783cd4e78fc718
tier: strict
title: 'Assure visibility of ``unsafe`` keyword in unsafe code'
metadata:
  id: gui_ZDLZzjeOwLSU
  category: required
  status: draft
  release: 1.85-latest
  fls: fls_8kqo952gjhaf
  decidability: decidable
  scope: crate
  tags:
    - readability
    - reduce-human-error
---

# Assure visibility of ``unsafe`` keyword in unsafe code

## Rule

Mark all code that may violate safety guarantees with a visible `unsafe` keyword
[cite:gui_ZDLZzjeOwLSU:RUST-REF-UNSAFE-KEYWORD].

The following constructs require explicit `unsafe` visibility:

* `extern` blocks must be declared as `unsafe extern`
* The `#[no_mangle]` attribute must be written as `#[unsafe(no_mangle)]`
* The `#[export_name]` attribute must be written as `#[unsafe(export_name)]`
* The `#[link_section]` attribute must be written as `#[unsafe(link_section)]`

Note:

   Starting with Rust Edition 2024, the use of `unsafe` is required in these contexts.
   The `#[link]` and `#[link_ordinal]` attributes are implicitly covered by the
   `unsafe extern` requirement, as they must appear on `extern` blocks.
   See rust-lang/rust#82499 for the tracking issue on unsafe attributes.

## Rationale

* Auditability and review

  * `unsafe` blocks create clear audit boundaries where reviewers can focus on code
    that may violate Rust's safety guarantees [cite:gui_ZDLZzjeOwLSU:RUSTNOMICON-MEET-SAFE]
  * Safety-critical standards like ISO 26262 [cite:gui_ZDLZzjeOwLSU:ISO-26262] and
    DO-178C [cite:gui_ZDLZzjeOwLSU:DO-178C] require traceability of hazardous operations
  * Helps enumerate and document all places where safety requirements must be manually upheld
  * Satisfies ISO 26262 Part 6, Table 1, objective 1c (use of language subsets)
  * Satisfies DO-178C Section 6.3.4.f (source code traceability)

* Explicit acknowledgment of responsibility

  * The `unsafe` keyword signals that the programmer is taking responsibility for
    upholding invariants the compiler cannot verify
  * Prevents accidental use of `unsafe` operations without conscious decision
  * Aligns with the principle of defense in depth in safety-critical systems

* Static analysis and tooling

  * Tools like `cargo-geiger` [cite:gui_ZDLZzjeOwLSU:CARGO-GEIGER], `unsafe-inspect`,
    and custom linters can automatically locate and count unsafe blocks
  * Enables metrics like "unsafe density" for safety assessments
  * Supports qualification evidence required by certification standards

* Traceability for certification

  * Safety-critical certifications require demonstrating that hazardous operations are
    identified and controlled
  * Visible `unsafe` tokens provide direct linkage to safety cases and hazard analyses
  * Facilitates the required documentation that each unsafe operation has been reviewed
    and justified

## Non-Compliant Example 1 (non_compl_ex_FdmuPXGZr4EP)

The `#[no_mangle]` attribute is unsafe because it can be used to declare a function
identifier that conflicts with an existing symbol. This noncompliant example declares an
unmangled function named `convert` that is missing the required unsafe wrapper in Rust 2024.

This noncompliant example requires Rust Edition 2021 or earlier to compile.
In Rust Edition 2024, missing `unsafe` wrappers cause compilation errors.

- options: compile_fail=, edition=2024

```rust
// Undefined behavior by the linker or loader is possible
// if another 'convert' function is defined.
#[no_mangle]
fn convert() {}

fn main() {
    convert();
}
```

## Non-Compliant Example 2 (non_compl_ex_FdmuPXGZr4EO)

This noncompliant example misdeclares the `malloc` function in an `extern "C"` block
by specifying the type of the `size` parameter as `f32` instead of `usize`.

An `extern` block is unsafe because undefined behavior can occur if types or functions are
misdeclared. This is true even if the declarations are not used. This noncompliant example
requires Rust Edition 2021 or earlier to compile.

- options: compile_fail=, edition=2024

```rust
use std::ffi;

extern "C" {
    // If 'malloc' is otherwise defined with a 'usize' argument, the compiler
    // may generate code for calls to this function using this incompatible declaration,
    // resulting in undefined behavior.
    fn malloc(size: f32) -> *mut ffi::c_void;
}

fn main() {}
```

## Non-Compliant Example 3 (non_compl_ex_Hk3mNp5qRs7t)

The `#[export_name]` and `#[link_section]` attributes can cause undefined behavior if
misused, as they affect symbol resolution and memory layout at link time. Without the
`unsafe` keyword, these hazards are not visible to reviewers or tools.

This noncompliant example has two separate problems. First, it uses an `#[export_name]`
attribute without an unsafe wrapper. This attribute controls the symbol name used during
linking. If another symbol with the same name exists, it causes undefined behavior.
Rust 2024 requires it to be marked `unsafe`.

The second problem is that this noncompliant example uses a `#[link_section]` attribute
without an unsafe wrapper. This attribute places the item in a specific linker section.
Incorrect section placement can cause undefined behavior (e.g., placing mutable data in
read-only sections, or interfering with special sections like `.init`).

This noncompliant example requires Rust Edition 2021 or earlier to compile.

- options: compile_fail=, edition=2024

```rust
// Collides with the C library 'printf' function
#[export_name = "printf"]  // noncompliant

// Missing unsafe marker - noncompliant in Rust 2024
#[link_section = ".init_array"] // noncompliant
static DATA: u32 = 42;  // Corrupts initialization table!

fn main() {
    println!("DATA = {DATA}");
}
```

## Compliant Example 1 (compl_ex_wR1FEyLRKmrr)

Rust Edition 2024 enforces that the `no_mangle` attribute requires an `unsafe` keyword,
as shown in this compliant example.

NOTE: This code can still have undefined behavior if the `convert` function symbol is
defined more than once.

- options: edition=2024, miri=skip

```rust
#[unsafe(no_mangle)] // compliant
fn convert() {}

fn main() {
    convert();
}
```

## Compliant Example 2 (compl_ex_wR1FEyLRKmrq)

Rust Edition 2024 enforces that `extern "C"` blocks require an `unsafe` keyword,
as shown in this compliant example.

NOTE: This code can still have undefined behavior if the declared `malloc` function is
incompatible with the actual definition. To eliminate this undefined behavior, the
declaration for `malloc` used in this compliant example accepts one argument of type `usize`.

- options: edition=2024, miri=skip

```rust
use std::ffi;

unsafe extern "C" {
    // Here the assumption is that malloc is the one defined by C's stdlib.h
    // and that size_of::<usize>() == size_of::<size_t>()
    fn malloc(size: usize) -> *mut ffi::c_void;
    fn free(ptr: *mut ffi::c_void);
}

fn main() {
    unsafe {
        let ptr = malloc(1024);
        if !ptr.is_null() {
            free(ptr);
        }
    }
}
```

## Compliant Example 3 (compl_ex_xY2zAb3cDe4f)

The `#[export_name]` and `#[link_section]` attributes must use the `unsafe()` wrapper
   to make their safety implications visible.

**Enforcement**
This guideline can be enforced through the following mechanisms:

* **Rust Edition 2024**: Migrating to Rust Edition 2024 makes violations of this guideline
  compilation errors for `extern` blocks and unsafe attributes.

* **Compiler Lints**: Enable the following lints:

  * `#![deny(unsafe_code)]` - Denies all unsafe code (use `#[allow(unsafe_code)]` for justified exceptions)
  * `#![deny(unsafe_op_in_unsafe_fn)]` - Requires explicit unsafe blocks within unsafe functions
  * `#![warn(unsafe_attr_outside_unsafe)]` - Warns about unsafe attributes without the `unsafe()` wrapper (pre-2024)

* **Static Analysis Tools**:

  * `cargo-geiger` - Counts and reports unsafe code usage
  * `cargo-audit` - Checks for known vulnerabilities in dependencies
  * Custom Clippy lints for project-specific requirements

* **Code Review**: Manual review of all code containing `unsafe` tokens should be
  part of the development process, with documented justification for each usage.

**Related guidelines**
* Minimize the scope of unsafe blocks
* Document safety invariants for all unsafe code with `// SAFETY:` comments
* Prefer safe abstractions over raw unsafe code
* Use `#![forbid(unsafe_code)]` at crate level where possible, with explicit exceptions

- options: edition=2024, miri=skip

```rust
// SAFETY: 'custom_symbol' does not conflict with any other symbol
#[unsafe(export_name = "custom_symbol")]
pub fn my_function() {}

// SAFETY: Placing data in a specific section for embedded systems
#[unsafe(link_section = ".noinit")]
static mut PERSISTENT_DATA: [u8; 256] = [0; 256];

// SAFETY: Custom section for shared memory
#[unsafe(link_section = ".shared")]
static SHARED_BUFFER: [u8; 4096] = [0; 4096];

fn main() {
    my_function();
    println!("shared buffer size = {}", SHARED_BUFFER.len());
    unsafe { let _ = PERSISTENT_DATA[0]; }
}
```

## References

- gui_ZDLZzjeOwLSU:RUST-EDITION-GUIDE :: The Rust Edition Guide. "Rust 2024." https://doc.rust-lang.org/edition-guide/rust-2024/index.html.
- gui_ZDLZzjeOwLSU:RUST-REF-UNSAFE-KEYWORD :: The Rust Reference. "Unsafe Keyword." https://doc.rust-lang.org/reference/unsafe-keyword.html.
- gui_ZDLZzjeOwLSU:RUST-LINT-UNSAFE :: Rust Compiler Lint Documentation. "unsafe_code." https://doc.rust-lang.org/rustc/lints/listing/allowed-by-default.html#unsafe-code.
- gui_ZDLZzjeOwLSU:RUSTNOMICON-MEET-SAFE :: The Rustonomicon. "Meet Safe and Unsafe." https://doc.rust-lang.org/nomicon/meet-safe-and-unsafe.html.
- gui_ZDLZzjeOwLSU:ISO-26262 :: International Organization for Standardization. "ISO 26262 - Road vehicles - Functional safety." https://www.iso.org/standard/68383.html.
- gui_ZDLZzjeOwLSU:DO-178C :: RTCA, Inc. "DO-178C: Software Considerations in Airborne Systems and Equipment Certification." https://store.accuristech.com/standards/rtca-do-178c.
- gui_ZDLZzjeOwLSU:CARGO-GEIGER :: cargo-geiger contributors. "cargo-geiger: Detects usage of unsafe Rust." https://github.com/geiger-rs/cargo-geiger.
- gui_ZDLZzjeOwLSU:RUST-REF-EXTERN :: The Rust Reference. "External blocks." https://doc.rust-lang.org/reference/items/external-blocks.html.
- gui_ZDLZzjeOwLSU:RUST-REF-UNSAFE-ATTR :: The Rust Reference. "Unsafe attributes." https://doc.rust-lang.org/reference/attributes.html#unsafe-attributes.
- gui_ZDLZzjeOwLSU:FERROCENE-SPEC :: Ferrocene GmbH. "Ferrocene Language Specification." https://spec.ferrocene.dev/.
- gui_ZDLZzjeOwLSU:RUST-REF-UNION :: The Rust Reference. "Unions." https://doc.rust-lang.org/reference/items/unions.html.
- gui_ZDLZzjeOwLSU:UCG-VALIDITY :: Rust Unsafe Code Guidelines. "Validity and Safety Invariant." https://rust-lang.github.io/unsafe-code-guidelines/glossary.html#validity-and-safety-invariant.

## Citation Signals

- citations: gui_ZDLZzjeOwLSU:CARGO-GEIGER, gui_ZDLZzjeOwLSU:DO-178C, gui_ZDLZzjeOwLSU:ISO-26262, gui_ZDLZzjeOwLSU:RUST-REF-UNSAFE-KEYWORD, gui_ZDLZzjeOwLSU:RUSTNOMICON-MEET-SAFE
- std_refs: (none)
