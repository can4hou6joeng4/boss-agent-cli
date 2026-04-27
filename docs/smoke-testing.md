# Smoke Testing

`scripts/smoke_p0.py` provides a minimal structured smoke harness for the current P0 golden path.

By default it targets `zhipin`. Set `BOSS_SMOKE_PLATFORM=zhilian` to run the same P0 shape against the Zhilian candidate path and have each step automatically prepend `--platform zhilian`.

## Covered Steps

- `doctor`
- `status`
- `search`
- `detail`

## Output Shape

The script prints JSON describing each step:

- `name`
- `purpose`
- `platform`
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
