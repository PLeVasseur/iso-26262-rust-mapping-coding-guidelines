# Non Compliant Example: RG-262BBEC1A2A9

This example intentionally violates error propagation and failure handling constraints and should be treated as negative evidence.

Expected outcome: `runtime_panic`.

Verification notes: Executing this program panics at runtime when `None` is provided, demonstrating an uncontrolled failure path.

```should_panic
fn main() {
    // Intentional runtime panic for negative evidence.
    let values = [10_u32, 20_u32];
    let idx = values.len();
    let _ = values[idx];
}
```
