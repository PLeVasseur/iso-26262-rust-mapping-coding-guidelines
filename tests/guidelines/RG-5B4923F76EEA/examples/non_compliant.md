# Non Compliant Example: RG-5B4923F76EEA

This example intentionally violates unsafe blocks and invariants constraints and should be treated as negative evidence.

Expected outcome: `runtime_panic`.

Verification notes: Execute and confirm panic occurs at runtime due to out-of-bounds access; this is negative evidence for rule validation.

```should_panic
fn main() {
    // Intentional runtime panic for negative evidence.
    let values = [10_u32, 20_u32];
    let idx = values.len();
    let _ = values[idx];
}
```
