# Non Compliant Example: RG-10299F77BE61

This example intentionally violates error propagation and failure handling constraints and should be treated as negative evidence.

Expected outcome: `runtime_panic`.

Verification notes: Executing the test should panic at runtime due to `unwrap()` on `None`, demonstrating non-compliant failure handling.

```should_panic
fn main() {
    // Intentional runtime panic for negative evidence.
    let values = [10_u32, 20_u32];
    let idx = values.len();
    let _ = values[idx];
}
```
