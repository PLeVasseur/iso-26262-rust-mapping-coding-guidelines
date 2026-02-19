---
guideline_id: gui_6JSM7YE7a1KR
source_path: src/coding-guidelines/types-and-traits/gui_6JSM7YE7a1KR.rst.inc
source_sha: 1c7d6bb9448a25401db3b411d1783cd4e78fc718
tier: strict
title: gui_6JSM7YE7a1KR
metadata:
  id: gui_6JSM7YE7a1KR
  category: required
  status: draft
  release: 1.85.0
  fls: fls_6lg0oaaopc26
  decidability: undecidable
  scope: expression
  tags:
    - unions
    - initialization
    - undefined-behavior
---

# gui_6JSM7YE7a1KR

## Rule

Do not read from a union field unless all bytes of that field have been explicitly initialized.
Partial initialization of a union's composite field leaves some bytes in an uninitialized state,
and reading those bytes is undefined behavior.

When working with unions:

* Initialize all bytes of a field before reading from it
* Do not assume that initializing one variant preserves the initialized state of another
* Do not rely on prior initialization of a union before reassignment
* Use `MaybeUninit` with proper initialization patterns rather than custom unions for
  managing uninitialized memory

You can access a field of a union even when the backing bytes of that field are uninitialized provided that:

- The resulting value has an unspecified but well-defined bit pattern.
- Interpreting that value must still comply with the requirements of the accessed type
  (e.g., no invalid enum discriminants, no invalid pointer values, etc.).

For example, reading an uninitialized `u32` field of a union is allowed;
reading an uninitialized bool field is disallowed because not all bit patterns are valid.

## Rationale

Unions in Rust allow multiple fields to share the same memory.
   When a union field is a composite type (tuple, struct, array),
   writing to only some components leaves the remaining bytes in an indeterminate state.
   Reading these uninitialized bytes is undefined behavior [cite:gui_6JSM7YE7a1KR:RUST-REF-UB].

   This issue is particularly insidious because:

   * **Silent data corruption**: The program may appear to work, reading stale or
     garbage values that happen to be *reasonable* in testing.

   * **Optimization interactions**: The compiler may merge, inline, or deduplicate
     functions in ways that change which code paths execute.
     A function that fully initializes a union may be merged with one that partially initializes it,
     causing UB to appear in previously-safe code paths [cite:gui_6JSM7YE7a1KR:LLVM-MERGE].

   * **Function pointer comparisons**: Relying on function pointer equality to
     select code paths is unreliable.
     Combined with partial initialization,
     this can lead to UB being introduced through seemingly unrelated optimizations.

   * **Reassignment resets initialization**: Assigning a new value to a union
     (e.g., `*u = MyUnion { uninit: () }`) does not preserve the initialized state of other fields.
     All fields must be considered uninitialized after such an assignment.

   * **Nested partial initialization**: When a union variant contains a
     `struct`, initializing only one field of that `struct` leaves the remaining
     fields uninitialized.
     The compiler does not warn about the uninitialized fields within the nested `struct`.

   Fields of a struct can be individually accessed using a raw pointer.
   Reading the entire struct, or forming a reference to that struct,
   requires that all fields be initialized before a typed read occurs.

The sole exception is that unions work like C unions:
any union field may be read, even if it was never written.
The resulting bytes must, however, form a valid representation for the field's type,
which is not guaranteed if the union contains arbitrary data.

## Non-Compliant Example 1 (non_compl_ex_kJEoz8oh6Fig)

This noncompliant example partially initializes a tuple field, leaving the second element uninitialized.

- options: miri=expect_ub, warn=allow

```rust
union MyMaybeUninit {
    uninit: (),
    init: (u8, u8),
}

fn foo() {
    let mut a = MyMaybeUninit { uninit: () };
    a.init.0 = 1; // Only initializes the first byte

    // Undefined behavior reading uninitialized value
    println!("{}", unsafe { a.init.1 }); // noncompliant
}

fn main() {
    foo();
}
```

## Non-Compliant Example 2 (non_compl_ex_gE095eyVJizR)

This noncompliant example assumes prior initialization is preserved after reassignment.

- options: miri=expect_ub

```rust
union Data {
    uninit: (),
    init: (u8, u8),
}

fn reassign(d: &mut Data) {
    // Reassignment invalidates all prior initialization
    *d = Data { uninit: () };
}

fn foo() {
    let mut d = Data { init: (0, 0) };
    reassign(&mut d);

    // 'init' is uninitialized after reassignment
    println!("{}", unsafe { d.init.1 });  // noncompliant
}

fn main() {
    foo();
}
```

## Non-Compliant Example 3 (non_compl_ex_BAHKbKIgDFnY)

This noncompliant example combines function pointer comparison with partial initialization,
creating subtle undefined behavior that may only manifest after optimization.

Note: this example relies on optimizer behavior (function merging can make
pointer equality succeed). Miri runs without those optimizations, so the
UB path is not deterministic there.

- options: miri=skip

```rust
union MyMaybeUninit {
    uninit: (),
    init: (u8, u8),
}

fn write_first(a: &mut MyMaybeUninit) {
    *a = MyMaybeUninit { uninit: () };
    a.init.0 = 1;
}

fn write_both(a: &mut MyMaybeUninit) {
    *a = MyMaybeUninit { uninit: () };
    a.init.0 = 1;
    a.init.1 = 2;
}

fn main() {
    let mut a = MyMaybeUninit { init: (0, 0) };

    if write_first as usize == write_both as usize {
        write_first(&mut a);
    }

    // UB if the branch was taken (functions may be merged by optimizer)
    println!("{}", unsafe { a.init.1 });  // noncompliant
}
```

## Non-Compliant Example 4 (non_compl_ex_PMmuoYeT7HsG)

Even though unions allow reads of any field, not all bit patterns are valid for a `bool`.
Unions do not relax type validity requirements.
Only the read itself is allowed;
the resulting bytes must still be a valid bool.

- options: miri=expect_ub

```rust
union U {
    b: bool,
    x: u8,
}

fn main() {
    let u = U { x: 255 };  // 255 is not a valid bool representation
    println!("{}", unsafe { u.b });  // UB — invalid bool
}
```

## Compliant Example 1 (compl_ex_JAR0OI9S07kf)

This compliant examples initializes all bytes of the field before reading.

- options: miri=

```rust
union MyMaybeUninit {
    uninit: (),
    init: (u8, u8),
}

fn write_both(a: &mut MyMaybeUninit) {
    *a = MyMaybeUninit { uninit: () };
    a.init.0 = 1;
    a.init.1 = 2;  // Initialize all bytes
}

fn main() {
    let mut a = MyMaybeUninit { init: (0, 0) };
    write_both(&mut a);

    // Both bytes are initialized
    println!("{}", unsafe { a.init.1 }); // compliant
}
```

## Compliant Example 2 (compl_ex_ko80pT9aS8Ge)

This compliant example uses `MaybeUninit` with proper initialization patterns.

- options: miri=

```rust
use std::mem::MaybeUninit;

fn init_tuple() -> (u8, u8) {
    let mut data: MaybeUninit<(u8, u8)> = MaybeUninit::uninit();

    unsafe {
        let ptr = data.as_mut_ptr();
        (*ptr).0 = 1;
        (*ptr).1 = 2;  // Initialize all fields
        // data is fully initialized before call to 'assume_init'
        data.assume_init()
    }
}

fn main() {
    let result = init_tuple();
    println!("{}, {}", result.0, result.1); // compliant
}
```

## Compliant Example 3 (compl_ex_xnanwe9eU5p5)

This compliant example initializes through the composite field directly.

- options: miri=

```rust
union Data {
    raw: [u8; 4],
    value: u32,
}

fn full_init(d: &mut Data) {
    //  Initialize entire field at once
    *d = Data { raw: [0xAB, 0xCD, 0xEF, 0x12] };
}

fn main() {
    let mut d = Data { value: 0 };
    full_init(&mut d);

    // All bytes in 'd' are initialized
    println!("{:?}", unsafe { d.raw });  // compliant
}
```

## Compliant Example 4 (compl_ex_gdh48eGNdS7e)

This compliant example avoids relying on function pointer comparisons.

- options: miri=

```rust
union MyMaybeUninit {
    uninit: (),
    init: (u8, u8),
}

#[allow(dead_code)]
enum InitLevel {
    Partial,
    Full,
}

fn write_first(a: &mut MyMaybeUninit) {
    *a = MyMaybeUninit { uninit: () };
    a.init.0 = 1;
}

fn write_both(a: &mut MyMaybeUninit) {
    *a = MyMaybeUninit { uninit: () };
    a.init.0 = 1;
    a.init.1 = 2;
}

fn main() {
    let mut a = MyMaybeUninit { init: (0, 0) };
    let level = InitLevel::Full;  // Explicit tracking, not pointer comparison

    match level {
        InitLevel::Full => {
            write_both(&mut a);
            // Compliant: safe to read both fields
            println!("{}", unsafe { a.init.1 });
        }
        InitLevel::Partial => {
            write_first(&mut a);
            // Only read the initialized field
            println!("{}", unsafe { a.init.0 });
        }
    }
}
```

## Compliant Example 5 (compl_ex_EU7kO0DtkJxs)

Types such as `u8`, `u16`, `u32`, and `i128` allow all possible bit patterns.
Provided the memory is initialized, there is no undefined behavior.

- options: miri=

```rust
union U {
    n: u32,
    bytes: [u8; 4],
}

fn main() {
    let u = U { bytes: [0xFF, 0xEE, 0xDD, 0xCC] };
    println!("{}", unsafe { u.n });   // OK — all bit patterns valid for u32
}
```

## Compliant Example 6 (compl_ex_V73XRTccrWky)

The following code reads a union field:

- options: miri=

```rust
union U {
   x: u32,
   y: f32,
}

fn main() {
   let u = U { x: 123 }; // write to one field
   println!("{}", unsafe { u.y }); // reading the other field is allowed
}
```

## References

- gui_6JSM7YE7a1KR:RUST-REF-UB :: The Rust Project Developers. "Behavior Considered Undefined." *The Rust Reference*, n.d. https://doc.rust-lang.org/reference/behavior-considered-undefined.html.
- gui_6JSM7YE7a1KR:RUST-REF-UNION :: The Rust Reference. "Unions." https://doc.rust-lang.org/reference/items/unions.html.
- gui_6JSM7YE7a1KR:LLVM-MERGE :: LLVM Project. "MergeFunctions Pass." *LLVM Documentation*, n.d. https://llvm.org/docs/MergeFunctions.html.
- gui_6JSM7YE7a1KR:UCG-VALIDITY :: Rust Unsafe Code Guidelines. "Validity and Safety Invariant." https://rust-lang.github.io/unsafe-code-guidelines/glossary.html#validity-and-safety-invariant.

## Citation Signals

- citations: gui_6JSM7YE7a1KR:LLVM-MERGE, gui_6JSM7YE7a1KR:RUST-REF-UB
- std_refs: (none)
