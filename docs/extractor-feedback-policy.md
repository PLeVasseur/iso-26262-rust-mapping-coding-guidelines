# Extractor Feedback Policy

## Severity scale

- `S0`: blocking correctness or safety mapping defect
- `S1`: high-impact quality regression with material traceability risk
- `S2`: moderate issue with workaround
- `S3`: minor issue or documentation gap

## Triage states

`new -> triaged -> upstream-open -> fixed-upstream -> verified`

## Promotion gating

- Any open `S0` or `S1` finding blocks promotion of affected rules to `ENFORCED`.
- `S2` and `S3` findings require triage and owner assignment but do not automatically block promotion.

## Required finding metadata

Each finding in `feedback/extractor_findings.yaml` includes:

- ownership (`owner`)
- due date (`due_by`)
- before/after run IDs
- extractor tool version + commit SHA
- expected vs observed behavior
- reproducible command
- upstream issue URL when opened
