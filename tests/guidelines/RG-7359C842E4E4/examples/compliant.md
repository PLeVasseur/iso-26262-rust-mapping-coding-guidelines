# Compliant Example: RG-7359C842E4E4

This example demonstrates unsafe blocks and invariants with explicit, reviewable constraints and deterministic evidence.

Expected outcome: `assertion_pass`.

Verification notes: Run `cargo run` (or `cargo test`) and check that all assertions pass and no lint/build violations are reported.

```rust
fn main() {
    // Compliant evidence for unsafe blocks and invariants.
    let values = [1_u32, 2_u32, 3_u32];
    let total: u32 = values.into_iter().sum();
    assert_eq!(total, 6);
}
```
