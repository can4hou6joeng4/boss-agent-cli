# boss-agent-cli Context

boss-agent-cli is a local-assist context for helping users organize job-search information without turning platform-sensitive workflows into default automation.

## Language

**Low-Risk Assistance Mode**:
The default operating posture that keeps the tool local-assist, read-only first, user-triggered, and blocked from automated outreach, bulk actions, risk-control bypass, or candidate personal-data workflows.
_Avoid_: automation mode, bypass mode, full agent loop

**Restricted Capability**:
A command or tool surface that exists for compatibility but is blocked by default because it touches platform write actions, bulk outreach, contact exchange, or candidate personal information.
_Avoid_: disabled feature, broken command

**Official Platform Handoff**:
The point where the CLI stops and the user completes a sensitive action manually on the official recruitment platform.
_Avoid_: workaround, fallback automation

**Candidate Personal Data Workflow**:
Any recruiter-side flow that reads or acts on a candidate's application, resume, contact details, or chat content.
_Avoid_: HR convenience command, recruiter shortcut

## Relationships

- **Low-Risk Assistance Mode** blocks **Restricted Capabilities** by default.
- **Restricted Capabilities** end in an **Official Platform Handoff**.
- A **Candidate Personal Data Workflow** is always a **Restricted Capability** unless a future explicit policy changes the boundary.

## Example Dialogue

> **Dev:** "Can the agent call `boss batch-greet` after search?"
> **Domain expert:** "No. In **Low-Risk Assistance Mode**, `batch-greet` is a **Restricted Capability** and should become an **Official Platform Handoff**."

## Flagged Ambiguities

- "低风险" is resolved as **Low-Risk Assistance Mode**, not merely slower request delays or better throttling.
- "招聘者工作流" must distinguish ordinary job-management tasks from **Candidate Personal Data Workflow**.
