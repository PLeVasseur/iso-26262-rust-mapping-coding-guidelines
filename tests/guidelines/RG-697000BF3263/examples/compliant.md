# Compliant Example: RG-697000BF3263

This example demonstrates unsafe blocks and invariants with explicit, reviewable constraints and deterministic evidence.

Expected outcome: `documented_only`.

Verification notes: Automated execution not required for documented-only compliant evidence.

```no_run
fn main() {
    let stable_value = 42_u32;
    let _ = stable_value;
}
```
