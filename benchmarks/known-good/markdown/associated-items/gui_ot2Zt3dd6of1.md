---
guideline_id: gui_ot2Zt3dd6of1
source_path: src/coding-guidelines/associated-items/gui_ot2Zt3dd6of1.rst
source_sha: 1c7d6bb9448a25401db3b411d1783cd4e78fc718
tier: strict
title: 'Recursive function are not allowed'
metadata:
  id: gui_ot2Zt3dd6of1
  category: required
  status: draft
  release: 1.3.0-latest
  fls: fls_vjgkg8kfi93
  decidability: undecidable
  scope: system
  tags:
    - stack-overflow
---

# Recursive function are not allowed

## Rule

Any function shall not call itself directly or indirectly

## Rationale

Recursive functions can easily cause stack overflows, which may result in exceptions or, in some cases, undefined behavior (typically some embedded systems). Although the Rust compiler supports tail call optimization [cite:gui_ot2Zt3dd6of1:WIKI-TAIL-CALL], this optimization is not guaranteed and depends on the specific implementation and function structure. There is an open RFC to guarantee tail call optimization in the Rust compiler [cite:gui_ot2Zt3dd6of1:PROPOSED-RFC-EXPLICIT-TAIL-CALLS], but this feature has not yet been stabilized. Until tail call optimization is guaranteed and stabilized, developers should avoid using recursive functions to prevent potential stack overflows and ensure program reliability.

## Non-Compliant Example 1 (non_compl_ex_MxqhjfkStJJy)

The below function `concat_strings` is not complaint because it call itself and depending on depth of data provided as input it could generate an stack overflow exception or undefine behavior.

```rust
// Recursive enum to represent a string or a list of `MyEnum`
#[allow(dead_code)]
enum MyEnum {
    Str(String),
    List(Vec<MyEnum>),
}

// Concatenates strings from a nested structure of `MyEnum` using recursion.
#[allow(dead_code)]
fn concat_strings(input: &[MyEnum]) -> String {
    let mut result = String::new();
    for item in input {
        match item {
            MyEnum::Str(s) => result.push_str(s),
            MyEnum::List(list) => result.push_str(&concat_strings(list)),
        }
    }
    result
}
#
# fn main() {}
```

## Compliant Example 1 (compl_ex_9pK3h65rfceO)

The following code implements the same functionality using iteration instead of recursion. The `stack` variable is used to maintain the processing context at each step of the loop. This approach provides explicit control over memory usage. If the stack grows beyond a predefined limit due to the structure or size of the input, the function returns an error rather than risking a stack overflow or out-of-memory exception. This ensures more predictable and robust behavior in resource-constrained environments.

```rust
// Recursive enum to represent a string or a list of `MyEnum`
#[allow(dead_code)]
enum MyEnum {
    Str(String),
    List(Vec<MyEnum>),
}

/// Concatenates strings from a nested structure of `MyEnum` without using recursion.
/// Returns an error if the stack size exceeds `MAX_STACK_SIZE`.
#[allow(dead_code)]
fn concat_strings_non_recursive(input: &[MyEnum]) -> Result<String, &'static str> {
    const MAX_STACK_SIZE: usize = 1000;
    let mut result = String::new();
    let mut stack = Vec::new();

    // Add all items to the stack
    stack.extend(input.iter());

    while let Some(item) = stack.pop() {
        match item {
            MyEnum::Str(s) => result.insert_str(0, s),
            MyEnum::List(list) => {
                // Add list items to the stack
                for sub_item in list.iter() {
                    stack.push(sub_item);
                    if stack.len() > MAX_STACK_SIZE {
                        return Err("Too big structure");
                    }
                }
            }
        }
    }
    Ok(result)
}
#
# fn main() {}
```

## References

- gui_ot2Zt3dd6of1:WIKI-TAIL-CALL :: Wikipedia. "Tail call." https://en.wikipedia.org/wiki/Tail_call
- gui_ot2Zt3dd6of1:PROPOSED-RFC-EXPLICIT-TAIL-CALLS :: Philipp Görz. "Open RFC - explicit_tail_calls." https://github.com/phi-go/rfcs/blob/guaranteed-tco/text/0000-explicit-tail-calls.md

## Citation Signals

- citations: gui_ot2Zt3dd6of1:PROPOSED-RFC-EXPLICIT-TAIL-CALLS, gui_ot2Zt3dd6of1:WIKI-TAIL-CALL
- std_refs: (none)
