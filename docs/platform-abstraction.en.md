# Platform Abstraction Design and Migration SOP

> This document captures the reusable playbook distilled from Issue #129 Week 1. Use it when onboarding a new platform such as Lagou, Liepin, or any future recruitment source, or when repeating a similar refactor.

## Why the platform abstraction exists

**Core problem**: the original implementation had 30+ commands directly calling `with BossClient(...) as client:`. That implicit dependency made the cost of adding a second recruitment platform explode, because every new platform would have to re-implement request wiring across the full command surface.

**Chosen abstraction boundary**: promote the idea of a "platform" to a first-class abstraction, instead of abstracting by API endpoint or auth flow. That keeps protocol differences such as response envelopes, error codes, and encryption fully encapsulated inside each platform implementation.

## Platform ABC contract

### 1. Basic metadata

```python
name: str            # "zhipin" / "zhilian" / ...
display_name: str    # "BOSS Zhipin" / "Zhilian" / ...
base_url: str
```

### 2. Response-envelope adapters

Every platform must implement:

```python
def is_success(self, response: dict) -> bool
def unwrap_data(self, response: dict) -> Any
def parse_error(self, response: dict) -> tuple[str, str]  # (normalized error code, raw message)
```

Examples of platform-specific differences:
- BOSS Zhipin: `code == 0` means success and the payload lives under `zpData`
- Zhilian: `code == 200` means success and the payload lives under `data`
- Raw platform errors are normalized into shared enums such as `AUTH_EXPIRED`, `RATE_LIMITED`, `ACCOUNT_RISK`, `TOKEN_REFRESH_FAILED`, and `UNKNOWN`

### 3. P0 read-only capabilities

These abstract methods are mandatory:

```python
@abstractmethod
def search_jobs(self, query: str, **filters: Any) -> dict
@abstractmethod
def job_detail(self, job_id: str) -> dict
@abstractmethod
def recommend_jobs(self, page: int = 1) -> dict
@abstractmethod
def user_info(self) -> dict
```

### 4. P0+ / P1 / P2 capability tiers

These default to `NotImplementedError` in the base class:

```python
# P0+: resume, application, history, interview, and chat read APIs
def resume_baseinfo(self) -> dict
def resume_expect(self) -> dict
def deliver_list(self, page: int = 1) -> dict
def job_card(self, security_id: str, lid: str = "") -> dict
def interview_data(self) -> dict
def job_history(self, page: int = 1) -> dict
def chat_history(self, gid: str, security_id: str, page: int = 1, count: int = 20) -> dict
def friend_label(self, friend_id: str, label_id: int, friend_source: int = 0, remove: bool = False) -> dict
def exchange_contact(self, security_id: str, uid: str, friend_name: str, exchange_type: int = 1) -> dict

# P1: write operations
def greet(self, security_id: str, job_id: str, message: str = "") -> dict
def apply(self, security_id: str, job_id: str, lid: str = "") -> dict

# P2: conversation workflows
def friend_list(self, page: int = 1) -> dict
```

### 5. Resource lifecycle

```python
def close(self) -> None                      # delegate to the underlying client.close()
def __enter__(self) -> "Platform": ...
def __exit__(self, ...) -> None: ...
```

## Command migration template

**Key idea**: move the command layer from "use BossClient directly" to "call through the Platform abstraction", so command code no longer needs to know which platform it is talking to.

### Before

```python
from boss_agent_cli.api.client import BossClient

def cmd(ctx):
    data_dir = ctx.obj["data_dir"]
    logger = ctx.obj["logger"]
    delay = ctx.obj["delay"]
    cdp_url = ctx.obj.get("cdp_url")

    auth = AuthManager(data_dir, logger=logger)
    with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
        result = client.search_jobs("Python", city="Guangzhou")
```

### After

```python
from boss_agent_cli.commands._platform import get_platform_instance

def cmd(ctx):
    data_dir = ctx.obj["data_dir"]
    logger = ctx.obj["logger"]

    auth = AuthManager(data_dir, logger=logger)
    with get_platform_instance(ctx, auth) as platform:
        result = platform.search_jobs("Python", city="Guangzhou")
```

**Net gain**: remove 3 lines of boilerplate (`delay`, `cdp_url`, and direct `BossClient` construction) while keeping the command call site stable.

## Test mocking rule

### Before

```python
@patch("boss_agent_cli.commands.X.BossClient")
def test_something(mock_client_cls):
    mock_client = _ctx_mock(mock_client_cls)
    mock_client.search_jobs.return_value = {...}
```

### After

```python
@patch("boss_agent_cli.commands.X.get_platform_instance")
def test_something(mock_get_platform):
    mock_platform = _ctx_mock(mock_get_platform)
    mock_platform.search_jobs.return_value = {...}
```

**Why**: patch the imported alias inside the command module, not the original source module. The patch target follows the command module's import path.

### Batch replacement helper

```bash
for cmd in greet apply detail me recommend ...; do
  sed -i '' "s/boss_agent_cli.commands.$cmd.BossClient/boss_agent_cli.commands.$cmd.get_platform_instance/g" tests/*.py
done
```

## SOP for onboarding a new platform

### Step 1: research note

Create `docs/research/platforms/<name>.md` and cover the same seven areas used in [lagou.md](research/platforms/lagou.md):

1. login flow
2. core API endpoint inventory
3. request signing or anti-bot mechanisms
4. response structure
5. CDP / browser-channel feasibility
6. risk-control thresholds
7. protocol-difference matrix compared to BOSS Zhipin

### Step 2: register a stub

```python
# src/boss_agent_cli/platforms/<name>.py
class MyPlatform(Platform):
    name = "myname"
    display_name = "My Platform"
    base_url = "https://..."

    def is_success(self, r): ...
    def unwrap_data(self, r): ...
    def parse_error(self, r): ...

    # P0 / P1 / P2 methods all raise NotImplementedError("Planned for Week 2")
```

### Step 3: add it to `platforms/__init__.py`

```python
_REGISTRY: dict[str, type[Platform]] = {
    "zhipin": BossPlatform,
    "zhilian": ZhilianPlatform,
    "myname": MyPlatform,
}
```

### Step 4: contract tests in `tests/test_<name>_stub.py`

Cover:
- platform registry validation
- basic metadata
- response-envelope adaptation
- stub behavior for each abstract method
- CLI integration, such as `boss --platform <name> schema`

### Step 5: real implementation rollout

- Start with P0: `search`, `detail`, `recommend`, `user_info`
- Then P1: `greet`, `apply`, if the platform needs them
- Finally P2: conversation workflows as an optional later stage

## Contract invariants

These are breaking-change red lines for the platform abstraction:

1. Keep the base-class signature `__init__(client: Any)` unchanged. Subclasses may narrow types but cannot add required parameters.
2. Keep abstract-method signatures stable. Add new methods if needed, but do not mutate existing signatures.
3. Error-code normalization must stay aligned with the shared error enums documented in `CLAUDE.md`.
4. Preserve `with`-context semantics: `__exit__` must call `close()`.
5. Python embedding exports must remain available through `from boss_agent_cli import ...` for `Platform`, `BossPlatform`, `ZhilianPlatform`, `get_platform`, and `list_platforms`.

## References

- [Issue #129 - Week 1 design and implementation](https://github.com/can4hou6joeng4/boss-agent-cli/issues/129)
- The Zhilian candidate-side implementation is already merged into mainline through PR #157, PR #158, and follow-up fixes; recruiter-side support is still intentionally unavailable
- [Issue #90 - Multi-platform API research](https://github.com/can4hou6joeng4/boss-agent-cli/issues/90)
- PR #131 / #132 / #133 / #134 / #135 / #136 / #137 / #138 / #139 / #141 - the full Week 1 PR set
