# Non Compliant Example: RG-7359C842E4E4

This example intentionally violates unsafe blocks and invariants constraints and should be treated as negative evidence.

Expected outcome: `runtime_panic`.

Verification notes: Run `cargo check` and verify compiler diagnostics report use of unsafe code forbidden by crate policy.

```should_panic
fn main() {
    // Intentional runtime panic for negative evidence.
    let values = [10_u32, 20_u32];
    let idx = values.len();
    let _ = values[idx];
}
```
