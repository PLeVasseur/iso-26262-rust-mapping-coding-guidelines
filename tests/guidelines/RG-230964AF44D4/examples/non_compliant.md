# Non Compliant Example: RG-230964AF44D4

This example intentionally violates error propagation and failure handling constraints and should be treated as negative evidence.

Expected outcome: `runtime_panic`.

Verification notes: Execute with malformed input path shown below; verify panic occurs due to unchecked `unwrap` rather than controlled error return.

```should_panic
fn main() {
    // Intentional runtime panic for negative evidence.
    let values = [10_u32, 20_u32];
    let idx = values.len();
    let _ = values[idx];
}
```
