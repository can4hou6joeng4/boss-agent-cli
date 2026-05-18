#!/usr/bin/env python3
"""
Synchronize online resumes for candidates who applied to BOSS/Zhipin recruiter jobs.

This script wraps boss-agent-cli commands and stores cross-session indexes/resume files.
It is intentionally conservative: it does not send messages, mutate jobs, or delete files.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path.home()
DEFAULT_ROOT = HOME / "WorkBuddy" / "boss-resumes"
DEFAULT_CDP_URL = "http://localhost:9222"
DEFAULT_PLATFORM = "zhipin"
DEFAULT_ROLE = "recruiter"
DEFAULT_BOSS_DATA_DIR = HOME / ".boss-agent"


def _default_boss_bin() -> str:
    """Pick a sensible default for the boss CLI based on the host OS.

    Prefers `boss` on PATH. On Windows, falls back to %USERPROFILE%\bin\boss.cmd
    when present, since subprocess sometimes fails to resolve a bare `boss` shim.
    """
    if sys.platform.startswith("win"):
        win_wrapper = HOME / "bin" / "boss.cmd"
        if win_wrapper.exists():
            return str(win_wrapper)
    return "boss"


DEFAULT_BOSS_BIN = _default_boss_bin()


@dataclass
class BossCommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    json_data: dict[str, Any] | None


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_name(value: Any, fallback: str = "unknown") -> str:
    text = str(value or fallback).strip() or fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._ ")
    return text[:80] or fallback


def ensure_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = path.with_suffix(path.suffix + f".broken-{int(time.time())}")
        path.rename(backup)
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_boss(extra_args: list[str], cdp_url: str, boss_bin: str, verbose: bool = False) -> BossCommandResult:
    args = [
        boss_bin,
        "--role",
        DEFAULT_ROLE,
        "--platform",
        DEFAULT_PLATFORM,
        "--cdp-url",
        cdp_url,
        *extra_args,
    ]
    if verbose:
        print(f"$ {' '.join(args)}", file=sys.stderr)
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    parsed: dict[str, Any] | None = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = None
    return BossCommandResult(args=args, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, json_data=parsed)


def try_friend_detail(friend_ids: list[int], cdp_url: str, data_dir: Path, verbose: bool = False) -> dict[int, dict[str, Any]]:
    """Call boss-agent-cli internal friend_detail API to obtain securityId by numeric friendId.

    The public `hr chat`/`hr applications` output omits securityId, but the internal
    friend_detail endpoint returns it. Keep this as an optional best-effort path so
    the script still works even if boss-agent-cli internals change later.
    """
    if not friend_ids:
        return {}
    try:
        from boss_agent_cli.auth.manager import AuthManager
        from boss_agent_cli.api.recruiter_client import BossRecruiterClient
    except Exception as exc:
        if verbose:
            print(
                f"[friend_detail] boss_agent_cli import failed ({exc!r}); "
                "securityId fallback via internal API is unavailable.",
                file=sys.stderr,
            )
        return {}

    auth = AuthManager(data_dir, platform=DEFAULT_PLATFORM)
    client = BossRecruiterClient(auth, cdp_url=cdp_url)
    try:
        response = client.friend_detail(friend_ids)
    except Exception:
        return {}
    finally:
        try:
            client.close()
        except Exception:
            pass

    data = response.get("zpData") if isinstance(response, dict) else None
    friends = data.get("friendList") if isinstance(data, dict) else None
    if not isinstance(friends, list):
        return {}
    resolved: dict[int, dict[str, Any]] = {}
    for friend in friends:
        if not isinstance(friend, dict):
            continue
        fid = friend.get("friendId") or friend.get("uid")
        if isinstance(fid, int):
            resolved[fid] = friend
    return resolved


def require_ok(result: BossCommandResult, context: str) -> dict[str, Any]:
    if result.returncode != 0 or not result.json_data or result.json_data.get("ok") is False:
        details = {
            "context": context,
            "args": result.args,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "json": result.json_data,
        }
        raise RuntimeError(json.dumps(details, ensure_ascii=False, indent=2))
    return result.json_data


def envelope_data(envelope: dict[str, Any]) -> Any:
    return envelope.get("data", [])


def list_jobs(cdp_url: str, boss_bin: str, verbose: bool) -> list[dict[str, Any]]:
    result = run_boss(["hr", "jobs", "list"], cdp_url=cdp_url, boss_bin=boss_bin, verbose=verbose)
    data = envelope_data(require_ok(result, "list jobs"))
    if isinstance(data, dict):
        for key in ("jobs", "list", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def is_online_job(job: dict[str, Any]) -> bool:
    status = job.get("jobOnlineStatus", job.get("onlineStatus", job.get("status")))
    if status in (1, "1", True, "online", "ONLINE", "在招", "招聘中"):
        return True
    if status in (0, "0", False, "offline", "OFFLINE", "关闭"):
        return False
    return True


def job_plain_id(job: dict[str, Any]) -> str:
    return str(job.get("jobId") or job.get("id") or job.get("job_id") or job.get("encryptJobId") or job.get("encrypt_job_id") or "unknown")


def job_encrypt_id(job: dict[str, Any]) -> str:
    return str(job.get("encryptJobId") or job.get("encryptJobIdStr") or job.get("encJobId") or job.get("jobId") or job.get("id") or "")


def job_name(job: dict[str, Any]) -> str:
    return str(job.get("jobName") or job.get("name") or job.get("title") or "unknown-job")


def refresh_jobs(root: Path, cdp_url: str, boss_bin: str, verbose: bool) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    config = ensure_json(root / "config.json", {})
    config.update({
        "resume_root": str(root),
        "cdp_url": cdp_url,
        "boss_bin": boss_bin,
        "updated_at": now_iso(),
    })
    write_json(root / "config.json", config)

    all_jobs = list_jobs(cdp_url, boss_bin, verbose)
    online_jobs = [job for job in all_jobs if is_online_job(job)]
    index = {
        "updated_at": now_iso(),
        "count": len(online_jobs),
        "jobs": {},
    }
    for job in online_jobs:
        plain = job_plain_id(job)
        enc = job_encrypt_id(job)
        dirname = f"{safe_name(plain)}_{safe_name(job_name(job))}"
        job_dir = root / "jobs" / dirname
        job_dir.mkdir(parents=True, exist_ok=True)
        write_json(job_dir / "job.json", job)
        index["jobs"][plain] = {
            "jobId": plain,
            "encryptJobId": enc,
            "jobName": job_name(job),
            "dir": str(job_dir),
            "raw": job,
        }
        if enc and enc != plain:
            index["jobs"][enc] = index["jobs"][plain]
    write_json(root / "job_index.json", index)
    return online_jobs


def candidate_id(candidate: dict[str, Any]) -> str:
    keys = [
        "encryptGeekId", "geekId", "geek_id", "encryptUserId", "encryptUid",
        "uid", "id", "candidateId", "encryptFriendId", "friendId",
    ]
    for key in keys:
        value = candidate.get(key)
        if value:
            return str(value)
    return safe_name(candidate_name(candidate), "unknown-candidate")


def candidate_name(candidate: dict[str, Any]) -> str:
    for key in ("name", "geekName", "candidateName", "userName", "nickName", "encryptName"):
        value = candidate.get(key)
        if value:
            return str(value)
    return "unknown-candidate"


def candidate_security_id(candidate: dict[str, Any]) -> str | None:
    for key in ("securityId", "securityID", "security_id", "sid"):
        value = candidate.get(key)
        if value:
            return str(value)
    return None


def flatten_candidates(data: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            found.extend(flatten_candidates(item))
    elif isinstance(data, dict):
        candidate_like = any(k in data for k in ("geekName", "encryptGeekId", "geekId", "candidateName", "securityId", "encryptFriendId"))
        if candidate_like:
            found.append(data)
        for value in data.values():
            if isinstance(value, (dict, list)):
                found.extend(flatten_candidates(value))
    return found


MAX_APPLICATION_PAGES = 100


def list_applications(job_enc_id: str, cdp_url: str, boss_bin: str, verbose: bool) -> list[dict[str, Any]]:
    all_candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    last_page = 0
    exhausted = False
    for page in range(1, MAX_APPLICATION_PAGES + 1):
        last_page = page
        args = ["hr", "applications", "--job-id", job_enc_id, "--page", str(page)]
        result = run_boss(args, cdp_url=cdp_url, boss_bin=boss_bin, verbose=verbose)
        envelope = require_ok(result, f"list applications page {page}")
        candidates = flatten_candidates(envelope_data(envelope))
        new_count = 0
        for cand in candidates:
            cid = candidate_id(cand)
            if cid not in seen_ids:
                seen_ids.add(cid)
                all_candidates.append(cand)
                new_count += 1
        pagination = envelope.get("pagination") or {}
        has_more = pagination.get("has_more") or pagination.get("hasMore")
        if has_more is True:
            continue
        if has_more is False:
            exhausted = True
            break
        if new_count == 0 or len(candidates) == 0:
            exhausted = True
            break
    if not exhausted and last_page == MAX_APPLICATION_PAGES:
        print(
            f"[list_applications] reached page cap of {MAX_APPLICATION_PAGES} for job {job_enc_id}; "
            "results may be truncated. Raise MAX_APPLICATION_PAGES if needed.",
            file=sys.stderr,
        )
    return all_candidates


def resolve_security_id(candidate: dict[str, Any], job_enc_id: str, cdp_url: str, boss_bin: str, verbose: bool, data_dir: Path) -> str | None:
    existing = candidate_security_id(candidate)
    if existing:
        return existing

    friend_id = candidate.get("friendId")
    if isinstance(friend_id, int):
        details = try_friend_detail([friend_id], cdp_url=cdp_url, data_dir=data_dir, verbose=verbose)
        detail = details.get(friend_id)
        if detail:
            candidate.update(detail)
            sid = candidate_security_id(detail)
            if sid:
                return sid

    # Conservative fallback: query candidates by display name and candidate id, then match likely records.
    search_terms = []
    name = candidate_name(candidate)
    cid = candidate_id(candidate)
    if name and name != "unknown-candidate":
        search_terms.append(name)
    if cid and cid != name:
        search_terms.append(cid)

    for term in search_terms[:2]:
        result = run_boss(["hr", "candidates", term], cdp_url=cdp_url, boss_bin=boss_bin, verbose=verbose)
        if result.returncode != 0 or not result.json_data or result.json_data.get("ok") is False:
            continue
        for item in flatten_candidates(envelope_data(result.json_data)):
            item_id = candidate_id(item)
            item_name = candidate_name(item)
            sid = candidate_security_id(item)
            if sid and (item_id == cid or item_name == name or cid in json.dumps(item, ensure_ascii=False)):
                candidate.update(item)
                return sid

    return None


def markdown_escape(value: Any) -> str:
    text = str(value).strip()
    return text.replace("\r\n", "\n").replace("\r", "\n")


def non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def join_present(parts: list[Any], sep: str = " / ") -> str:
    return sep.join(str(part).strip() for part in parts if non_empty(part))


def section(lines: list[str], title: str) -> None:
    lines.append("")
    lines.append(f"## {title}")
    lines.append("")


def bullet(lines: list[str], label: str, value: Any) -> None:
    if non_empty(value):
        lines.append(f"- **{label}**：{markdown_escape(value)}")


def normalize_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if non_empty(value):
        return [value]
    return []


def render_mapping(lines: list[str], data: dict[str, Any], skip: set[str] | None = None) -> None:
    skip = skip or set()
    for key, value in data.items():
        if key in skip or not non_empty(value):
            continue
        label = str(key).replace("_", " ")
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, indent=2)
            lines.append(f"- **{label}**：")
            lines.append("")
            lines.append("```json")
            lines.append(rendered)
            lines.append("```")
        else:
            bullet(lines, label, value)


def render_experience_item(lines: list[str], item: Any, default_title: str) -> None:
    if not isinstance(item, dict):
        if non_empty(item):
            lines.append(f"- {markdown_escape(item)}")
        return

    title = join_present([
        item.get("company") or item.get("school") or item.get("project") or item.get("project_name") or item.get("name"),
        item.get("position") or item.get("major") or item.get("role"),
    ]) or default_title
    time_range = join_present([item.get("start"), item.get("end")], " - ")
    duration = item.get("duration")
    header_bits = [title]
    if time_range:
        header_bits.append(time_range)
    if non_empty(duration):
        header_bits.append(str(duration))
    lines.append(f"### {markdown_escape(' | '.join(header_bits))}")
    lines.append("")

    primary_keys = {
        "company", "school", "project", "project_name", "name", "position", "major", "role",
        "start", "end", "duration", "responsibility", "performance", "description", "content", "keywords",
    }
    for label, key in [
        ("职责", "responsibility"),
        ("业绩", "performance"),
        ("描述", "description"),
        ("内容", "content"),
    ]:
        if non_empty(item.get(key)):
            lines.append(f"- **{label}**：{markdown_escape(item.get(key))}")
    keywords = item.get("keywords")
    if isinstance(keywords, list) and keywords:
        lines.append(f"- **关键词**：{', '.join(str(x) for x in keywords if non_empty(x))}")
    render_mapping(lines, item, skip=primary_keys)
    lines.append("")


def extract_markdown(value: Any) -> str:
    data = value if isinstance(value, dict) else {}
    basic = data.get("basic") if isinstance(data.get("basic"), dict) else {}
    expectation = data.get("expectation") if isinstance(data.get("expectation"), dict) else {}
    name = basic.get("name") or data.get("name") or "未知候选人"

    lines: list[str] = [f"# {markdown_escape(name)}", ""]

    summary_parts = [
        basic.get("gender"),
        basic.get("age"),
        basic.get("degree"),
        basic.get("work_years"),
        basic.get("active_status"),
    ]
    summary = join_present(summary_parts)
    if summary:
        lines.append(f"> {markdown_escape(summary)}")
        lines.append("")

    section(lines, "基本信息")
    for label, key in [
        ("姓名", "name"),
        ("性别", "gender"),
        ("年龄", "age"),
        ("学历", "degree"),
        ("工作年限", "work_years"),
        ("活跃状态", "active_status"),
    ]:
        bullet(lines, label, basic.get(key))
    render_mapping(lines, basic, skip={"name", "gender", "age", "degree", "work_years", "active_status", "avatar"})

    if any(non_empty(v) for v in expectation.values()):
        section(lines, "求职意向")
        for label, key in [("职位", "position"), ("薪资", "salary"), ("城市", "city")]:
            bullet(lines, label, expectation.get(key))
        render_mapping(lines, expectation, skip={"position", "salary", "city"})

    experience_sections = [
        ("工作经历", "work_experience", "经历"),
        ("项目经历", "project_experience", "项目"),
        ("教育经历", "education", "教育"),
        ("竞争力分析", "competitive_analysis", "分析"),
        ("证书", "certifications", "证书"),
    ]
    for title, key, default_title in experience_sections:
        items = normalize_items(data.get(key))
        if not items:
            continue
        section(lines, title)
        for item in items:
            render_experience_item(lines, item, default_title)

    remaining_skip = {"basic", "expectation", *(key for _, key, _ in experience_sections)}
    remaining = {k: v for k, v in data.items() if k not in remaining_skip and non_empty(v)}
    if remaining:
        section(lines, "其他信息")
        render_mapping(lines, remaining)

    return "\n".join(lines).strip() + "\n"


def download_resume(candidate: dict[str, Any], job_enc_id: str, security_id: str, cdp_url: str, boss_bin: str, verbose: bool) -> dict[str, Any]:
    geek_id = candidate_id(candidate)
    result = run_boss(
        ["hr", "resume", geek_id, "--job-id", job_enc_id, "--security-id", security_id],
        cdp_url=cdp_url,
        boss_bin=boss_bin,
        verbose=verbose,
    )
    return require_ok(result, f"download resume for {candidate_name(candidate)}")


def sync_job(
    job: dict[str, Any],
    root: Path,
    cdp_url: str,
    boss_bin: str,
    data_dir: Path,
    force: bool,
    dry_run: bool,
    verbose: bool,
    max_per_job: int,
    run_budget: dict[str, int],
) -> dict[str, Any]:
    plain = job_plain_id(job)
    enc = job_encrypt_id(job)
    dirname = f"{safe_name(plain)}_{safe_name(job_name(job))}"
    job_dir = root / "jobs" / dirname
    resumes_dir = job_dir / "resumes"
    job_dir.mkdir(parents=True, exist_ok=True)
    resumes_dir.mkdir(parents=True, exist_ok=True)
    write_json(job_dir / "job.json", job)

    candidate_index_path = job_dir / "candidate_index.json"
    candidate_index = ensure_json(candidate_index_path, {"updated_at": None, "job": {"jobId": plain, "encryptJobId": enc, "jobName": job_name(job)}, "candidates": {}})
    candidate_index.setdefault("candidates", {})

    stats = {
        "jobId": plain,
        "encryptJobId": enc,
        "jobName": job_name(job),
        "candidates_discovered": 0,
        "downloaded": 0,
        "skipped_existing": 0,
        "pending_security_id": 0,
        "failed": 0,
        "failures": [],
        "stopped_due_to_limit": None,
    }

    candidates = list_applications(enc, cdp_url=cdp_url, boss_bin=boss_bin, verbose=verbose)
    stats["candidates_discovered"] = len(candidates)
    job_downloads = 0

    for candidate in candidates:
        cid = candidate_id(candidate)
        cname = candidate_name(candidate)
        existing = candidate_index["candidates"].get(cid, {})
        candidate_dir = resumes_dir / f"{safe_name(cname)}_{safe_name(cid)}"
        current = {
            **existing,
            "candidateId": cid,
            "name": cname,
            "last_seen_at": now_iso(),
            "source_application": candidate,
        }

        if existing.get("status") == "downloaded" and not force:
            stats["skipped_existing"] += 1
            candidate_index["candidates"][cid] = current
            continue

        # Enforce caps before issuing a new resume request. Already-downloaded
        # / pending / dry-run paths above don't count against the budget.
        if not dry_run and run_budget["remaining"] <= 0:
            stats["stopped_due_to_limit"] = "run"
            break
        if not dry_run and job_downloads >= max_per_job:
            stats["stopped_due_to_limit"] = "job"
            break

        sid = resolve_security_id(candidate, enc, cdp_url=cdp_url, boss_bin=boss_bin, verbose=verbose, data_dir=data_dir)
        cname = candidate_name(candidate)
        candidate_dir = resumes_dir / f"{safe_name(cname)}_{safe_name(cid)}"
        current.update({"name": cname, "source_application": candidate})
        if not sid:
            current.update({"status": "pending_security_id", "updated_at": now_iso(), "reason": "securityId not found in applications/friend_detail/candidates fallback"})
            candidate_index["candidates"][cid] = current
            stats["pending_security_id"] += 1
            write_json(candidate_index_path, candidate_index)
            continue

        current["securityId"] = sid
        if dry_run:
            current.update({"status": "dry_run_ready", "updated_at": now_iso()})
            candidate_index["candidates"][cid] = current
            continue

        try:
            resume_envelope = download_resume(candidate, enc, sid, cdp_url=cdp_url, boss_bin=boss_bin, verbose=verbose)
            candidate_dir.mkdir(parents=True, exist_ok=True)
            write_json(candidate_dir / "raw_response.json", resume_envelope)
            write_json(candidate_dir / "resume.json", envelope_data(resume_envelope))
            markdown_resume = extract_markdown(envelope_data(resume_envelope))
            (candidate_dir / "resume.md").write_text(markdown_resume, encoding="utf-8")
            current.update({
                "status": "downloaded",
                "downloaded_at": now_iso(),
                "updated_at": now_iso(),
                "resume_dir": str(candidate_dir),
            })
            stats["downloaded"] += 1
        except Exception as exc:  # Keep going during bulk sync.
            message = str(exc)
            current.update({"status": "failed", "updated_at": now_iso(), "error": message})
            stats["failed"] += 1
            stats["failures"].append({"candidateId": cid, "name": cname, "error": message})
        finally:
            candidate_index["candidates"][cid] = current
            candidate_index["updated_at"] = now_iso()
            write_json(candidate_index_path, candidate_index)
            # Attempted a real resume request — count it against both budgets
            # whether it succeeded or failed, since either pattern shows up to
            # the server.
            job_downloads += 1
            run_budget["remaining"] -= 1

        if not dry_run:
            time.sleep(random.uniform(3, 6))

    candidate_index["updated_at"] = now_iso()
    write_json(candidate_index_path, candidate_index)
    return stats


def load_job_index(root: Path) -> dict[str, Any]:
    return ensure_json(root / "job_index.json", {"jobs": {}})


def command_refresh_jobs(args: argparse.Namespace) -> int:
    jobs = refresh_jobs(args.root, args.cdp_url, args.boss_bin, args.verbose)
    print(json.dumps({"ok": True, "resume_root": str(args.root), "online_jobs": len(jobs), "jobs": jobs}, ensure_ascii=False, indent=2))
    return 0


def command_sync_all(args: argparse.Namespace) -> int:
    jobs = refresh_jobs(args.root, args.cdp_url, args.boss_bin, args.verbose)
    run_budget = {"remaining": args.max_downloads_per_run}
    summary = {
        "ok": True,
        "resume_root": str(args.root),
        "mode": "sync-all",
        "dry_run": args.dry_run,
        "jobs_discovered": len(jobs),
        "limits": {
            "max_downloads_per_run": args.max_downloads_per_run,
            "max_downloads_per_job": args.max_downloads_per_job,
        },
        "stopped_due_to_limit": None,
        "jobs": [],
        "totals": {"candidates_discovered": 0, "downloaded": 0, "skipped_existing": 0, "pending_security_id": 0, "failed": 0},
    }
    for job in jobs:
        if not args.dry_run and run_budget["remaining"] <= 0:
            summary["stopped_due_to_limit"] = "run"
            break
        stats = sync_job(
            job, args.root, args.cdp_url, args.boss_bin, args.data_dir,
            args.force, args.dry_run, args.verbose,
            args.max_downloads_per_job, run_budget,
        )
        summary["jobs"].append(stats)
        for key in summary["totals"]:
            summary["totals"][key] += int(stats.get(key, 0))
        if stats.get("stopped_due_to_limit") == "run":
            summary["stopped_due_to_limit"] = "run"
            break
        if stats.get("stopped_due_to_limit") == "job" and summary["stopped_due_to_limit"] is None:
            summary["stopped_due_to_limit"] = "job"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def command_sync_job(args: argparse.Namespace) -> int:
    refresh_jobs(args.root, args.cdp_url, args.boss_bin, args.verbose)
    index = load_job_index(args.root)
    job_entry = index.get("jobs", {}).get(str(args.job_id))
    if not job_entry:
        print(json.dumps({"ok": False, "error": f"job not found in job_index: {args.job_id}"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    raw_job = job_entry.get("raw") or job_entry
    run_budget = {"remaining": args.max_downloads_per_run}
    stats = sync_job(
        raw_job, args.root, args.cdp_url, args.boss_bin, args.data_dir,
        args.force, args.dry_run, args.verbose,
        args.max_downloads_per_job, run_budget,
    )
    print(json.dumps({
        "ok": True,
        "resume_root": str(args.root),
        "mode": "sync-job",
        "dry_run": args.dry_run,
        "limits": {
            "max_downloads_per_run": args.max_downloads_per_run,
            "max_downloads_per_job": args.max_downloads_per_job,
        },
        "stopped_due_to_limit": stats.get("stopped_due_to_limit"),
        "job": stats,
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync BOSS/Zhipin recruiter application online resumes.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"Resume root directory. Default: {DEFAULT_ROOT}")
    parser.add_argument("--cdp-url", default=os.environ.get("BOSS_CDP_URL", DEFAULT_CDP_URL), help=f"CDP URL. Default: {DEFAULT_CDP_URL}")
    parser.add_argument("--boss-bin", default=os.environ.get("BOSS_BIN", DEFAULT_BOSS_BIN), help=f"boss executable path/name. Default: {DEFAULT_BOSS_BIN}")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("BOSS_DATA_DIR", str(DEFAULT_BOSS_DATA_DIR))), help=f"boss-agent-cli data directory. Default: {DEFAULT_BOSS_DATA_DIR}")
    parser.add_argument("--verbose", action="store_true", help="Print executed commands to stderr.")

    sub = parser.add_subparsers(dest="command", required=True)

    sub_refresh = sub.add_parser("refresh-jobs", help="Refresh online job index only.")
    sub_refresh.set_defaults(func=command_refresh_jobs)

    sub_all = sub.add_parser("sync-all", help="Incrementally sync resumes for all online jobs.")
    sub_all.add_argument("--force", action="store_true", help="Re-download candidates already marked downloaded.")
    sub_all.add_argument("--dry-run", action="store_true", help="Resolve candidates without downloading resumes.")
    sub_all.add_argument("--max-downloads-per-run", type=int, default=20, help="Hard cap on resume downloads across the entire run. Default: 20.")
    sub_all.add_argument("--max-downloads-per-job", type=int, default=10, help="Hard cap on resume downloads within a single job. Default: 10.")
    sub_all.set_defaults(func=command_sync_all)

    sub_job = sub.add_parser("sync-job", help="Incrementally sync one job by numeric or encrypted job ID.")
    sub_job.add_argument("--job-id", required=True, help="Numeric jobId or encryptJobId from job_index.json.")
    sub_job.add_argument("--force", action="store_true", help="Re-download candidates already marked downloaded.")
    sub_job.add_argument("--dry-run", action="store_true", help="Resolve candidates without downloading resumes.")
    sub_job.add_argument("--max-downloads-per-run", type=int, default=20, help="Hard cap on resume downloads for this run. Default: 20.")
    sub_job.add_argument("--max-downloads-per-job", type=int, default=10, help="Hard cap on resume downloads within the job. Default: 10.")
    sub_job.set_defaults(func=command_sync_job)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "interrupted"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
