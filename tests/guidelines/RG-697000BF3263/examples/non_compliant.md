# Non Compliant Example: RG-697000BF3263

This example intentionally violates unsafe blocks and invariants constraints and should be treated as negative evidence.

Expected outcome: `documented_only`.

Verification notes: Automated execution not required for documented-only negative evidence.

```no_run
fn main() {
    let unchecked = -1_i32;
    let _ = unchecked;
}
```
