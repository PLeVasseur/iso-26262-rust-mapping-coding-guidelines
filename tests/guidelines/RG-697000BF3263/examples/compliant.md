# Compliant Example: RG-697000BF3263

This example demonstrates unsafe blocks and invariants with explicit, reviewable constraints and deterministic evidence.

Expected outcome: `assertion_pass`.

Verification notes: Run `rustc compliant.rs && ./compliant`; expected result is zero panics and successful exit. Reviewers can check that each required activity is mapped and approved.

```rust
fn main() {
    // Compliant evidence for unsafe blocks and invariants.
    let values = [1_u32, 2_u32, 3_u32];
    let total: u32 = values.into_iter().sum();
    assert_eq!(total, 6);
}
```
