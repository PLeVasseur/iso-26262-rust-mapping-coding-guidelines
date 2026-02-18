# Decision Log

## Defaults

- Default corpus pack: `iso-core-part6`
- Default mode for PR/new-session checks: `change`
- Default profile for PR/new-session checks: `quick`
- Full profile usage: nightly/release validation
- Promotion policy: no auto-promotion from `.cache/` to tracked `data/`
- Required promotion inputs: explicit reviewer sign-off + `data/run_registry.yaml` update
- Query retrieval defaults:
  - Exact anchors -> lexical mode
  - Concept anchors -> hybrid mode
  - Seed queries always include `--with-pinpoint`, `--with-ancestors`, `--with-descendants`
- Exit code policy:
  - `0` success
  - `2` policy/gate failure
  - `3` runtime/tooling failure
