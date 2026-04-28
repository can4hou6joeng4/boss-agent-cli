# Shell Agent Integration Example

Applies to the current `boss-agent-cli` CLI contract as of April 28, 2026.

## Good fit when

- your agent framework only needs a shell or subprocess tool
- you want to expose `boss` as a reusable external command inside an existing orchestrator
- you want the most universal, lowest-dependency integration path

## Minimal integration

Core rule: let the host do only two things, "run the command" and "parse JSON". Do not reimplement the BOSS Zhipin protocol inside the host.

Recommended wrapper flow:

```text
1. Run `boss schema`
2. Run `boss status`
3. If not logged in, run `boss login`
4. Run `boss search`
5. Run `boss detail`
6. When the result is promising, run `boss greet`
```

Minimal command chain:

```bash
boss schema
boss status
boss search "Golang" --city 广州 --welfare "双休,五险一金"
boss detail <security_id>
boss greet <security_id> <job_id>
```

If the host supports a single wrapper function, use something like:

```python
def run_boss(*args: str) -> dict:
	result = subprocess.run(["boss", *args], check=False, capture_output=True, text=True)
	return json.loads(result.stdout)
```

Then let the upper layer depend only on the returned JSON envelope.

## Recovery flow

Standard order:

```bash
boss doctor
boss status
boss login
boss search "Golang" --city 广州
```

General advice:

- do not continue follow-up actions when `ok=false`
- on `AUTH_REQUIRED`, restore login first and only then retry `boss search`
- on `RATE_LIMITED`, pause instead of continuing with `boss greet`
