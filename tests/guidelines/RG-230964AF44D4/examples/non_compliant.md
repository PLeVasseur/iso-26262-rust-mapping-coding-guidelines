# Non Compliant Example: RG-230964AF44D4

This example intentionally violates error propagation and failure handling constraints and should be treated as negative evidence.

Expected outcome: `runtime_panic`.

Verification notes: Execute the program; it should panic on out-of-bounds indexing when given a negative value cast to `usize`.

```should_panic
fn main() {
    // Intentional runtime panic for negative evidence.
    let values = [10_u32, 20_u32];
    let idx = values.len();
    let _ = values[idx];
}
```
