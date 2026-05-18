---
name: boss-resume-downloader
description: Use this skill when the user wants to install, configure, download, or synchronize online resumes for candidates who applied to currently open BOSS/Zhipin recruiter jobs. Provides a cross-platform boss-agent-cli setup, standard CDP/Chrome conventions, persistent resume root, job/candidate JSON indexes, full and per-job incremental sync, and randomized delay during bulk downloads.
---

# Boss Resume Downloader

## Purpose

Install and operate a deterministic BOSS/Zhipin recruiter resume synchronization workflow. Standardize the boss-agent-cli command path, CDP endpoint, Chrome launch convention, auth data directory, resume storage root, and resume download fallback logic so that the workflow is reproducible across machines and across agent harnesses (Claude Code, Claude Desktop, Cursor, etc.).

Use this skill for two related goals:

1. Speed up installation and first successful run on a new machine.
2. Let other sessions operate the same BOSS resume synchronization flow without rediscovering CDP paths, Chrome executable locations, boss-agent-cli storage directories, or `securityId` fallback behavior.

## Golden Configuration

Prefer these defaults unless the user explicitly requests otherwise. Paths shown with `~` are POSIX (macOS/Linux); the Windows equivalent uses `%USERPROFILE%`.

| Item | Value (POSIX) | Value (Windows) |
|---|---|---|
| Platform | `zhipin` | `zhipin` |
| Role | `recruiter` | `recruiter` |
| CDP URL | `http://localhost:9222` | `http://localhost:9222` |
| boss command | `boss` (on PATH) | `boss` (on PATH) or `%USERPROFILE%\bin\boss.cmd` |
| boss-agent-cli auth data dir | `~/.boss-agent` | `%USERPROFILE%\.boss-agent` |
| Resume output root | `~/WorkBuddy/boss-resumes` | `%USERPROFILE%\WorkBuddy\boss-resumes` |
| Sync script | `scripts/sync_boss_resumes.py` (relative to this SKILL.md) | same |

Invoke the CLI like this (assumes `boss` is on PATH):

```bash
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 <command>
```

Important known facts:

- Use platform `zhipin`, not `zhipin-recruiter`.
- Use CDP URL `http://localhost:9222` when recruiter authentication is needed.
- Use auth data directory `~/.boss-agent` (POSIX) / `%USERPROFILE%\.boss-agent` (Windows). Do not guess `.boss-agent-cli`.
- On Windows, if `boss` is not resolvable from subprocess, fall back to `%USERPROFILE%\bin\boss.cmd`.
- Resume viewing requires encrypted geek ID, encrypted job ID, and `securityId`.
- `hr applications` can list applied candidates but may not expose `securityId` in all outputs.
- `hr chat` can expose candidate names but may still omit `securityId`.
- The bundled script can use boss-agent-cli internal `friend_detail(friendIds)` as a fallback to resolve `securityId` from numeric `friendId`. This fallback requires the `boss_agent_cli` Python package to be importable from the active environment.
- `hr candidates` can be used as an additional fallback source.
- Do not assume `encryptFriendId` equals `securityId`.

## Recommended Usage Pattern: Hourly Incremental Sync + Local Analysis

**Strongly recommended over one-shot bulk downloads.**

> ⚠️ **Risk notice — recruiter resume access is sensitive.**
> Recruiter-side resume access is a monitored, high-sensitivity action. Bulk resume screening has been observed to trigger account-level risk controls (rate limiting, CAPTCHA challenges, temporary account restrictions; see upstream issue #232). The patterns and limits in this skill **reduce** that risk but do not eliminate it. Treat any safeguards here — incremental scheduling, randomized delay, per-run/per-job download caps — as **risk-reduction measures, not safety guarantees**. Recruiter accounts may still be limited, challenged, or restricted at any time; monitor for `ACCOUNT_RISK` / `RATE_LIMITED` errors and back off.

### Schedule hourly incremental sync via agent

Use your agent harness's scheduled task capability (e.g., Claude Code `/loop`, cron, or any recurring job) to run `sync-all` every hour:

```bash
# Run once per hour, keep Chrome open in the background
python "${SKILL_DIR}/scripts/sync_boss_resumes.py" sync-all
```

In Claude Code you can set this up with:

```
/loop 1h python "${SKILL_DIR}/scripts/sync_boss_resumes.py" sync-all
```

Each run only downloads candidates not yet in `candidate_index.json`, so an hourly job typically only needs to download the few new applicants from the past hour.

**Why this is preferred over a one-shot bulk sync:**

1. **Reduces (does not eliminate) risk-control exposure.** Downloading dozens or hundreds of resumes in one session resembles scraping and is more likely to trigger account risk controls. Spreading work across many small hourly runs lowers — but does not remove — that likelihood. The script also enforces hard per-run/per-job download caps (`--max-downloads-per-run`, `--max-downloads-per-job`) so a single run cannot accidentally produce a bulk-access pattern even if many new candidates accumulated.

2. **Local analysis is fast and free.** Once resumes are on disk as `resume.md`, any analysis (filtering by keyword, comparing candidates, summarizing, ranking) runs entirely locally — no network round-trips, no rate limits, no extra API calls to BOSS.

### Analyze from local Markdown files

After sync, all resumes are at:

```
~/WorkBuddy/boss-resumes/jobs/<jobId>_<jobName>/resumes/<name>_<id>/resume.md
```

To analyze them, read the local files directly. Example prompts that work well once files are synced:

- "Read all resume.md files under ~/WorkBuddy/boss-resumes and shortlist candidates with Python experience."
- "Compare the work experience sections across all downloaded resumes for job <job_name>."
- "Find candidates who mentioned crawler/scrapy/selenium in their resumes."

This is faster and more reliable than querying BOSS live for each analysis question.

---

## First-Time Setup Checklist

### 1. Verify the boss CLI is available

```bash
boss --help
```

If `boss` is not on PATH, locate the installed entrypoint and either add it to PATH or pass it via `--boss-bin` to the bundled script. On Windows the entrypoint is typically `%USERPROFILE%\bin\boss.cmd` (a wrapper around the env's `boss.exe`).

### 2. Start Chrome with a stable CDP endpoint

Prefer an already-running Chrome session that exposes `http://localhost:9222`. If status checks fail because CDP is unavailable, launch Chrome manually with remote debugging enabled and a dedicated profile. Common executable locations:

- macOS: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
- Linux: `google-chrome` or `chromium` on PATH
- Windows: `C:\Program Files\Google\Chrome\Application\chrome.exe`, `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe`, or `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`

Recommended launch shapes:

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.workbuddy/chrome-profiles/boss-cdp"

# Linux
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.workbuddy/chrome-profiles/boss-cdp"
```

```bat
:: Windows
"<chrome.exe>" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\.workbuddy\chrome-profiles\boss-cdp"
```

Do not randomize the CDP port or profile directory unless port 9222 is occupied.

### 3. Verify recruiter login

```bash
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 status
```

If not logged in, run CDP login and let the user complete any browser-side QR/login step:

```bash
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 login --cdp
```

### 4. Verify online jobs

```bash
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 hr jobs list
```

Proceed only after the command returns the expected recruiter jobs.

## Default Storage

Use this default resume root unless the user specifies another directory:

- POSIX: `~/WorkBuddy/boss-resumes`
- Windows: `%USERPROFILE%\WorkBuddy\boss-resumes`

Expected structure:

```text
boss-resumes/
  config.json
  job_index.json
  jobs/
    <jobId>_<safeJobName>/
      job.json
      candidate_index.json
      resumes/
        <safeCandidateName>_<candidateId>/
          resume.json
          resume.md
          raw_response.json
```

## Main Workflow

### 1. Check login and open jobs

Run status first. If authentication is missing or expired, use the CDP login flow.

```bash
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 status
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 hr jobs list
```

Persist all currently online jobs into `job_index.json`, keyed by stable job ID and encrypted job ID.

### 2. Create per-job directories

For every online job, create a directory named:

```text
<jobId>_<safeJobName>
```

Store raw job metadata in `job.json`.

### 3. List applied candidates

For each job, call applications with the encrypted job ID when available:

```bash
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 hr applications --job-id <encryptJobId>
```

Paginate if the CLI output indicates more pages.

### 4. Resolve resume parameters

For each applied candidate, resolve:

- `encryptGeekId` / encrypted candidate ID
- display name
- `securityId`
- applicable encrypted job ID

Use `applications` first. If `securityId` is absent, resolve it by numeric `friendId` through the internal boss-agent-cli `friend_detail(friendIds)` API. This endpoint returns candidate name, `securityId`, `encryptUid`, `jobId`, and `encryptJobId`. Use `hr candidates` or `hr chat` only as additional fallback sources. Do not assume `encryptFriendId` equals `securityId`.

If `securityId` cannot be resolved, record the candidate as `pending_security_id` in `candidate_index.json` and skip resume download for that candidate. Do not mark as permanently failed.

### 5. Download online resume

When all parameters are present, call:

```bash
boss --role recruiter --platform zhipin --cdp-url http://localhost:9222 hr resume <encryptGeekId> --job-id <encryptJobId> --security-id <securityId>
```

Save:

- raw JSON response as `raw_response.json`
- normalized JSON as `resume.json`
- readable Markdown resume as `resume.md`

### 6. Incremental behavior

Before downloading, inspect `candidate_index.json`.

Skip a candidate when:

- candidate ID exists in the index,
- resume status is `downloaded`, and
- no `--force` option was requested.

Update candidates when:

- candidate is new,
- previous status was `pending_security_id`, `failed`, or `partial`,
- `--force` is set.

### 7. Random delay

During bulk downloads, apply a random delay between successful/attempted candidate downloads:

```text
3-6 seconds
```

Do not delay for dry-run/list-only operations.

## Bundled Script

The script lives next to this SKILL.md at `scripts/sync_boss_resumes.py`. Resolve `${SKILL_DIR}` to the directory containing this SKILL.md, then call:

```bash
# Sync all online jobs incrementally
python "${SKILL_DIR}/scripts/sync_boss_resumes.py" sync-all

# Sync all online jobs with command echoing; --verbose is a global option and must be placed before the subcommand
python "${SKILL_DIR}/scripts/sync_boss_resumes.py" --verbose sync-all

# Sync one job by numeric jobId or encrypted jobId
python "${SKILL_DIR}/scripts/sync_boss_resumes.py" sync-job --job-id <jobId>

# Refresh job index only
python "${SKILL_DIR}/scripts/sync_boss_resumes.py" refresh-jobs

# Dry run without downloading resumes
python "${SKILL_DIR}/scripts/sync_boss_resumes.py" sync-all --dry-run

# Override per-run / per-job download caps (defaults: 20 per run, 10 per job)
python "${SKILL_DIR}/scripts/sync_boss_resumes.py" sync-all \
  --max-downloads-per-run 50 --max-downloads-per-job 20
```

The script enforces conservative download caps **by default** so a single run cannot perform a bulk-access pattern even when many new candidates have accumulated:

| Flag | Default | Meaning |
|---|---|---|
| `--max-downloads-per-run` | 20 | Hard cap on resume downloads across the entire run. When reached, the run stops cleanly (current job is finalized then no more jobs are processed). |
| `--max-downloads-per-job` | 10 | Hard cap on resume downloads within a single job. When reached, that job's candidate loop ends cleanly and the run moves to the next job. |

When a cap is hit, the run still exits with `ok: true`; the summary's `stopped_due_to_limit` field is set (`"run"`, `"job"`, or `null`) so the operator knows work remains and the next scheduled run will pick up where this one stopped.

Use the Python interpreter that has `boss_agent_cli` importable, otherwise the `friend_detail` fallback will be disabled. The script prints a warning to stderr (with `--verbose`) when this happens.

If `boss` is not on PATH, override with `--boss-bin <path>` or set `BOSS_BIN=<path>` in the environment.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `unrecognized arguments: --verbose` | Global option placed after the subcommand | Put `--verbose` before `sync-all` or `sync-job` |
| `[WinError 2] 系统找不到指定的文件` / `FileNotFoundError: boss` | Subprocess cannot find `boss` on PATH | Pass `--boss-bin <path>` or set `BOSS_BIN`; on Windows use `%USERPROFILE%\bin\boss.cmd` |
| Not logged in despite browser login | Wrong auth data dir | Use `--data-dir ~/.boss-agent` (Windows: `%USERPROFILE%\.boss-agent`) |
| CDP connection refused | Chrome was not started with remote debugging | Relaunch Chrome with `--remote-debugging-port=9222` |
| All candidates are `pending_security_id` | `applications` output lacks `securityId` and internal fallback failed | Run the script with a Python that has `boss_agent_cli` importable, or pass `--verbose` to see the import error |
| `zhipin-recruiter` platform error | Wrong platform name | Use `--platform zhipin` |
| Applications pagination stopped at page 100 | Hard cap reached | Re-run on the specific job, or split by other filters; the script warns to stderr when the cap is hit |

## Output Expectations

Report the following after each sync:

- resume root directory
- jobs discovered
- jobs updated
- candidates discovered
- resumes downloaded
- skipped existing resumes
- candidates pending `securityId`
- failures with exact error messages
- whether the run stopped early because a download cap was reached

A successful sync envelope shape:

```json
{
  "ok": true,
  "resume_root": "<resolved path>",
  "mode": "sync-all",
  "stopped_due_to_limit": null,
  "totals": {
    "candidates_discovered": 0,
    "downloaded": 0,
    "skipped_existing": 0,
    "pending_security_id": 0,
    "failed": 0
  }
}
```

`stopped_due_to_limit` takes one of `null` (ran to completion), `"run"` (per-run cap hit), or `"job"` (per-job cap hit on at least one job).

## Safety and Etiquette

- Do not send messages to candidates.
- Do not change job online/offline status.
- Do not delete previous resumes or indexes during sync.
- Preserve raw API responses for debugging.
- Treat BOSS data as private recruiting data.
- Do not include downloaded candidate resumes in a distributable skill package.
