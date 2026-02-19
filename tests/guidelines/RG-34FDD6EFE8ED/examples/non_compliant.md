# Non Compliant Example: RG-34FDD6EFE8ED

This example intentionally violates error propagation and failure handling constraints and should be treated as negative evidence.

Expected outcome: `runtime_panic`.

Verification notes: Execute with invalid input (as shown) and check runtime panic evidence from `unwrap()` or assertion failure.

```should_panic
fn main() {
    // Intentional runtime panic for negative evidence.
    let values = [10_u32, 20_u32];
    let idx = values.len();
    let _ = values[idx];
}
```
