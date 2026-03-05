# Step 11 Deviations

## Deviations
- WS3 main/adversarial evaluation reports were generated with `--allow-degraded --no-enforce-gates` so Step 11 can produce active-path artifacts in the current environment even while semantic backend preflight is unstable.
- Human review artifact is emitted as structured JSON (`retrieval_human_review_s0.json`) built from active query artifacts under `.cache/sqlite_kb/reports/step11_human_review/` instead of markdown-only output.

## Known Issues
- Semantic and hybrid modes degraded to lexical for WS3 eval runs in this environment; WS3 promotion to co-primary should wait for non-degraded backend-stable runs.
- Off-domain abstain remains imperfect (`WS3-NEG-OUT-001` returned non-abstain with irrelevant Rust chunks), so abstain robustness still needs retrieval-layer improvement.
