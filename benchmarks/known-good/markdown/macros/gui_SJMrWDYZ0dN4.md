---
guideline_id: gui_SJMrWDYZ0dN4
source_path: src/coding-guidelines/macros/gui_SJMrWDYZ0dN4.rst
source_sha: 1c7d6bb9448a25401db3b411d1783cd4e78fc718
tier: extended
title: 'Names in a macro definition shall use a fully qualified path'
metadata:
  id: gui_SJMrWDYZ0dN4
  category: required
  status: draft
  release: '1.85.0;1.85.1'
  fls: fls_7kb6ltajgiou
  decidability: decidable
  scope: module
  tags:
    - reduce-human-error
---

# Names in a macro definition shall use a fully qualified path

## Rule

Each name inside of the definition of a macro shall either use a global path or path prefixed with $crate.

## Rationale

Using a path that refers to an entity relatively inside of a macro subjects it to path resolution
results which may change depending on where the macro is used. The intended path to refer to an entity
can be shadowed when using a macro leading to unexpected behaviors. This could lead to developer confusion
about why a macro behaves differently in diffenent locations, or confusion about where entity in a macro
will resolve to.

## Non-Compliant Example 1 (non_compl_ex_m2XR1ihTbCQS)

The following is a macro which shows referring to a vector entity using a non-global path. Depending on
where the macro is used a different `Vec` could be used than is intended. If scope where this is used
defines a struct `Vec` which is not preset at the macro definition, the macro user might be intending to
use that in the macro.

```rust
#[macro_export]
macro_rules! my_vec {
    ( $( $x:expr ),* ) => {
        {
            let mut temp_vec = Vec::new(); // non-global path
            $(
                temp_vec.push($x);
            )*
            temp_vec
        }
    };
}

fn main() {
    let _ = my_vec![1, 2, 3];
}
```

## Compliant Example 1 (compl_ex_xyaShvxL9JAM)

The following is a macro refers to Vec using a global path. Even if there is a different struct called
`Vec` defined in the scope of the macro usage, this macro will unambiguously use the `Vec` from the
Standard Library.

```rust
#[macro_export]
macro_rules! my_vec_global {
    ( $( $x:expr ),* ) => {
        {
            let mut temp_vec = ::std::vec::Vec::new(); // global path
            $(
                temp_vec.push($x);
            )*
            temp_vec
        }
    };
}

fn main() {
    let _ = my_vec_global![1, 2, 3];
}
```

## References

- (none)

## Citation Signals

- citations: (none)
- std_refs: (none)
