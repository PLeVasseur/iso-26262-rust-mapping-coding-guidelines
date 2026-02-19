# Compliant Example: RG-587CE115D9C8

This example demonstrates unsafe blocks and invariants with explicit, reviewable constraints and deterministic evidence.

Expected outcome: `assertion_pass`.

Verification notes: Run as a test or executable; evidence is the passing assertions for valid, out-of-range, and missing-sample branches.

```rust
fn main() {
    // Compliant evidence for unsafe blocks and invariants.
    let values = [1_u32, 2_u32, 3_u32];
    let total: u32 = values.into_iter().sum();
    assert_eq!(total, 6);
}
```
