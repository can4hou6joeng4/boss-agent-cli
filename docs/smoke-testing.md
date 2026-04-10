# Smoke Testing

`scripts/smoke_p0.py` provides a minimal structured smoke harness for the current P0 golden path.

## Covered Steps

- `doctor`
- `status`
- `search`
- `detail`

## Output Shape

The script prints JSON describing each step:

- `name`
- `purpose`
- `preconditions`
- `failure_classification`
- `command`
- `status`

## Status Meanings

- `pass`: command ran successfully
- `env_error`: required local setup is missing
- `command_error`: command executed but the smoke path failed

## Intended Use

- local pre-release validation
- manual debugging checkpoints
- future CI-safe smoke expansion
