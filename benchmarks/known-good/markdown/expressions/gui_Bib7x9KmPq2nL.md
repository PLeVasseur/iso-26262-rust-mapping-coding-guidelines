---
guideline_id: gui_Bib7x9KmPq2nL
source_path: src/coding-guidelines/expressions/gui_Bib7x9KmPq2nL.rst
source_sha: 1c7d6bb9448a25401db3b411d1783cd4e78fc718
tier: strict
title: 'Example guideline with bibliography (gui_Bib7x9KmPq2nL)'
metadata:
  id: gui_Bib7x9KmPq2nL
  category: advisory
  status: draft
  release: 1.0.0-latest
  fls: fls_69zyas59o8ff
  decidability: decidable
  scope: module
  tags:
    - safety
    - undefined-behavior
---

# Example guideline with bibliography (gui_Bib7x9KmPq2nL)

## Rule

This is an example guideline demonstrating the bibliography feature.

As documented in [cite:gui_Bib7x9KmPq2nL:RUST-REF-UNION], union types in Rust have specific safety requirements.
The CERT C Coding Standard [cite:gui_Bib7x9KmPq2nL:CERT-C-INT34] provides guidance on avoiding undefined behavior
in shift operations, which applies to Rust as well.

## Rationale

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

This guideline demonstrates how to include bibliographic references in coding guidelines.
References help users find authoritative sources for the recommendations made.

Refer to [cite:gui_Bib7x9KmPq2nL:RUST-REF-UNION] for more details.

## Non-Compliant Example 1 (non_compl_ex_Bib7x9KmPq2nL)

This example shows code that violates the guideline.

```rust
fn main() {
    // Non-compliant code example
    let x = 42;
    println!("{}", x);
}
```

## Compliant Example 1 (compl_ex_Bib7x9KmPq2nL)

This example shows the correct way to follow the guideline.

```rust
fn main() {
    // Compliant code example
    let x: i32 = 42;
    println!("{}", x);
}
```

## References

- gui_Bib7x9KmPq2nL:RUST-REF-UNION :: The Rust Reference. "Unions." https://doc.rust-lang.org/reference/items/unions.html
- gui_Bib7x9KmPq2nL:CERT-C-INT34 :: SEI CERT C Coding Standard. "INT34-C. Do not shift an expression by a negative number of bits." https://wiki.sei.cmu.edu/confluence/x/ItcxBQ

## Citation Signals

- citations: gui_Bib7x9KmPq2nL:CERT-C-INT34, gui_Bib7x9KmPq2nL:RUST-REF-UNION
- std_refs: (none)
