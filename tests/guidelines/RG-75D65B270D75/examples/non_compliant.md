# Non Compliant Example: RG-75D65B270D75

This example intentionally violates unsafe blocks and invariants constraints and should be treated as negative evidence.

Expected outcome: `compile_fail`.

Verification notes: Compile with `rustc` or `cargo check`; build should fail because `unsafe` usage is denied.

```compile_fail
fn main() {
    // Intentional compile failure for negative evidence.
    let must_be_u32: u32 = "invalid";
    let _ = must_be_u32;
}
```
