# Labels And Triage

Use labels to make outside contribution easier.

## Type labels

- `bug`: Confirmed defect or reproducible regression.
- `enhancement`: New capability or behavior improvement.
- `documentation`: Documentation-only change.
- `test`: Test coverage or test infrastructure.
- `ci`: GitHub Actions, packaging, or release automation.
- `security`: Security-sensitive report that is safe to discuss publicly.

## Workflow labels

- `triage`: Needs maintainer classification.
- `needs-repro`: Needs a minimal command, JSON envelope, or environment detail.
- `good first issue`: Small, well-scoped issue suitable for a first contributor.
- `help wanted`: Maintainers agree on the direction and welcome outside implementation.
- `blocked`: Cannot proceed until external information or platform behavior is known.

## Domain labels

- `contract`: JSON envelope, schema, exit code, stdout/stderr, or error-code contract.
- `platform-drift`: Live platform behavior changed while local contracts still pass.
- `auth`: Cookie, token, CDP, QR login, browser login, or logout.
- `candidate`: Candidate-side workflow.
- `recruiter`: Recruiter-side workflow.
- `docs-parity`: Chinese and English docs need synchronization.

## Triage rules

- Add `triage` to every new issue.
- Remove `triage` once the issue has type, domain, and workflow labels.
- Use `platform-drift` only when mock tests can pass while live commands fail.
- Use `contract` when stdout, stderr, exit code, schema, or error envelopes are involved.
- Use `good first issue` only when the expected files, tests, and acceptance criteria are written in the issue.
