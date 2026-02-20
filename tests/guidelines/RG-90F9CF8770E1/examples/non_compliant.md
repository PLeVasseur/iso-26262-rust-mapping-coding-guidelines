# Non Compliant Example: RG-90F9CF8770E1

This example intentionally violates unsafe blocks and invariants constraints and should be treated as negative evidence.

Expected outcome: `runtime_panic`.

```should_panic
fn main() {
    // Intentional runtime panic for negative evidence.
    let values = [10_u32, 20_u32];
    let idx = values.len();
    let _ = values[idx];
}
```
