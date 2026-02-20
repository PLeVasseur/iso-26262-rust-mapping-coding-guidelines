# Compliant Example: RG-1E3A1235235C

This example demonstrates unsafe blocks and invariants with explicit, reviewable constraints and deterministic evidence.

Expected outcome: `assertion_pass`.

```rust
fn main() {
    // Compliant evidence for unsafe blocks and invariants.
    let values = [1_u32, 2_u32, 3_u32];
    let total: u32 = values.into_iter().sum();
    assert_eq!(total, 6);
}
```
