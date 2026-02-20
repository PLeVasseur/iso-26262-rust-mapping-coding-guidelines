# Non Compliant Example: RG-DCC941884F0D

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
