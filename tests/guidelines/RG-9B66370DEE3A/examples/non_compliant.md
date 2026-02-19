# Non Compliant Example: RG-9B66370DEE3A

This example intentionally violates unsafe blocks and invariants constraints and should be treated as negative evidence.

Expected outcome: `runtime_panic`.

Verification notes: Run with `cargo run`; execution should panic at the approval assertion, confirming the evidence set is non-compliant.

```should_panic
fn main() {
    // Intentional runtime panic for negative evidence.
    let values = [10_u32, 20_u32];
    let idx = values.len();
    let _ = values[idx];
}
```
