---
name: bma-sessions
description: >-
  Work with Amazon Bedrock Managed Agents (BMA, powered by OpenAI) sessions
  in this repo: create a session, submit prompts/turns, interpret a session's
  items/results, list sessions, delete sessions, attach an AgentCore Gateway
  MCP tool (Module 1, environment=none), and run/continue a session on an
  AgentCore Runtime where the agent executes commands in a microVM (Module 2).
  Trigger when the user mentions BMA, Bedrock Managed Agents, bedrock-mantle, a
  BMA session (sess_...), an AgentCore Runtime, or asks to
  run/continue/inspect/clean up an OpenAI-on-Bedrock agent session. Not for
  self-hosted execution environments.
---

# BMA sessions

This skill drives the lab scripts in this repository to work with Amazon
Bedrock Managed Agents (BMA, powered by OpenAI) sessions. BMA is a public
preview; APIs and default models can change.

The repo has two modules, and this skill covers both:

- **Module 1 — `environment=none`** (`modules/01-bma-environment-none/`): the
  agent reasons and emits text but runs nothing. SigV4 auth. Default session
  role `BedrockManagedAgentsPreviewInferenceServiceRole`.
- **Module 2 — AgentCore Runtime** (`modules/02-bma-agentcore-runtime/`): the
  session runs on a managed microVM, so the agent **executes commands**
  (results show up as `command_execution` items). Bearer-token auth and a
  region-scoped `BmaSessionRole-<region>` with Runtime invoke permissions.

**Pick the module from the user's intent (see [Choosing a module](#choosing-a-module)).**
The two modules have **separate venvs, separate auth, and separate session
roles** — do not mix them. Each module is self-contained under its own
directory; always run a module's scripts from that module's root.

**Do not reimplement the BMA API.** Prefer running the existing, verified lab
scripts under the relevant module's `labs/`. Only write ad-hoc API calls if the
user asks for something the scripts do not cover — in that case read
`references/api-cheatsheet.md` (next to this file) for the exact call shapes,
and reuse that module's `bma_common.py`.

## Choosing a module

- **No execution / reason-and-text, Gateway MCP tool, shared to-do demo,
  SigV4** → **Module 1**. This is the default when the user just says "a BMA
  session" with no mention of a Runtime.
- **"run on a Runtime", "execute commands", "microVM", a Runtime ARN
  (`arn:aws:bedrock-agentcore:...:runtime/...`), `command_execution` items,
  bearer token, `BmaSessionRole`** → **Module 2**.
- If it is genuinely ambiguous, ask which execution environment they want
  (environment=none vs AgentCore Runtime) before running anything — the venv,
  auth, and role differ.

## Safety and cost (read first)

- These scripts make **real AWS calls** signed with the user's credentials and
  can **incur charges** (model inference). Treat creating sessions and
  submitting turns as side-effecting actions.
- **Sessions are never auto-deleted.** Whenever you create sessions during a
  task, tell the user the session id(s) and offer to delete them with the
  module's delete lab when done (Module 1: Lab 05; Module 2: Lab 04). Do not
  leave orphaned sessions silently.
- Never print or paste the user's AWS credentials, tokens, or full role ARNs
  with account ids into chat beyond what a command strictly needs.
- Deleting a BMA session removes its server-side history but does NOT delete
  customer-owned resources — e.g. a Module 1 AgentCore Gateway or its Lambda,
  or a Module 2 AgentCore Runtime (which keeps incurring charges until the user
  runs `agentcore remove`).

## Module 1 prerequisites (verify before running anything)

1. **Working directory & venv.** Run scripts from the module root
   `modules/01-bma-environment-none`. The Python venv must exist and be
   active. If it does not exist, create it:
   `cd modules/01-bma-environment-none && python3 setup.py` (use `python` on
   Windows), then `source .venv/bin/activate`.
2. **AWS profile.** BMA calls are SigV4-signed with AWS credentials (no OpenAI
   API key). Credentials are per-shell. If the user hits a
   `401 AuthenticationError` or an expired/invalid token, the profile is
   wrong or stale — have them set `export AWS_PROFILE=<their-profile>` (or pass
   `--aws-profile <profile>` to each script) and confirm with
   `aws sts get-caller-identity --profile <profile>`.
3. **Session role.** BMA assumes an IAM session role for inference, resolved
   via `--role-arn` / `$BMA_INFERENCE_ROLE_ARN`, else the default
   `BedrockManagedAgentsPreviewInferenceServiceRole` in the caller's account.
   If the role is missing, point the user at
   `modules/01-bma-environment-none/00-prerequisites.md` and
   `scripts/create_session_role.py`.
4. **Region & model.** Supported regions: us-east-1 (default), us-west-2,
   us-east-2. Default model `openai.gpt-5.6-luna`; availability is
   region/account dependent. Pass `--region` / `--model` to override.

Always invoke scripts with the venv's Python, e.g.
`python labs/lab-01-create-session/create_session.py ...` after activating the
venv (or `.venv/bin/python labs/...` without activating).

## Module 1 operations (map the user's intent to a lab script)

All paths are relative to `modules/01-bma-environment-none`.

### Create a session and submit the first prompt
Use `labs/lab-01-create-session/create_session.py`.
```
python labs/lab-01-create-session/create_session.py "What is the capital of France?"
```
Optional: `--model`, `--region`, `--instructions`, `--aws-profile`,
`--role-arn`. It prints `Created session: sess_...` to stdout — capture that
id and report it to the user; later operations reuse it.

### Interpret a session's results (list its items)
Use `labs/lab-02-interpret-results/interpret_results.py`.
```
python labs/lab-02-interpret-results/interpret_results.py --session-id sess_abc123
python labs/lab-02-interpret-results/interpret_results.py --session-id sess_abc123 --json
```
Items are a tagged union (`message` with role, `reasoning`, `mcp_call`,
`command_execution`). Use `--json` to inspect tool-call detail, especially an
`mcp_call` item's `server_label`, `name`, and `error`. With no `--session-id`
it creates a throwaway session, asks one prompt, then lists.

### Demonstrate durable/shared session state (to-do list)
Use `labs/lab-03-session-state/session_state.py`. First run (no
`--session-id`) creates a shared list and prints the id; later runs join it.
```
python labs/lab-03-session-state/session_state.py --user Alice "buy milk"
python labs/lab-03-session-state/session_state.py --session-id sess_abc123 --user Bob "book flights"
```

### List sessions
Use `labs/lab-04-list-sessions/list_sessions.py`.
```
python labs/lab-04-list-sessions/list_sessions.py
python labs/lab-04-list-sessions/list_sessions.py --limit 50 --order asc --json
```
This endpoint does not auto-paginate; raise `--limit` (max 100) if more exist.

### Delete session(s) — cleanup
Use `labs/lab-05-delete-session/delete_session.py`.
```
python labs/lab-05-delete-session/delete_session.py sess_abc123 sess_def456
```
Offer this whenever sessions created during the task are no longer needed.

### Attach an AgentCore Gateway MCP tool (advanced)
Use `labs/lab-06-agentcore-gateway/gateway_session.py`. Requires a
pre-provisioned Gateway ARN (not created here). The tool is fixed at session
creation, so `--gateway-arn` is required on create and omitted when joining.
```
python labs/lab-06-agentcore-gateway/gateway_session.py \
  --gateway-arn arn:aws:bedrock-agentcore:us-east-1:<acct>:gateway/<id> \
  --user Alice "buy milk"
python labs/lab-06-agentcore-gateway/gateway_session.py --session-id sess_abc123 --user Bob "book flights"
```
Gateway requirements and debugging are documented in that lab's README; if a
tool call fails, inspect the `mcp_call` item via Lab 02 `--json`.

## Module 2 prerequisites (AgentCore Runtime)

Module 2 is **not** a superset of Module 1 — it has its own venv, auth, and
role. Verify these before running anything in it. Full detail is in
`modules/02-bma-agentcore-runtime/00-prerequisites.md`.

1. **A Runtime already exists.** AWS provisions the AgentCore Runtime with the
   AgentCore CLI (`agentcore create/deploy`, ~15 min, incurs charges) — this
   repo does **not** script that. You need its **Runtime ARN**
   (`arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>`). If the user
   does not have one, point them at §2 of the prerequisites; do not try to
   create a Runtime from this skill.
2. **Runtime ARN set once.** Every lab, `preflight.py`, and
   `create_session_role.py` read `$BMA_RUNTIME_ARN`. Have the user
   `export BMA_RUNTIME_ARN=<arn>` (or pass `--runtime <arn>` per command). The
   ARN also fixes the Region, so do **not** pass `--region` for Module 2.
3. **Module 2's own venv.** Run scripts from `modules/02-bma-agentcore-runtime`.
   It has a separate venv with one extra dependency
   (`aws-bedrock-token-generator`, for bearer-token auth) — Module 1's venv
   will **not** work. Create it with
   `cd modules/02-bma-agentcore-runtime && python3 setup.py` (use `python` on
   Windows), then `source .venv/bin/activate`. If Module 1's venv is active,
   `deactivate` first.
4. **AWS profile.** As in Module 1, calls are made with AWS credentials (no
   OpenAI API key) — here they also mint the bearer token. Set
   `export AWS_PROFILE=<profile>` (or pass `--aws-profile`) and confirm with
   `aws sts get-caller-identity --profile <profile>`.
5. **Session role `BmaSessionRole-<region>`.** Distinct from Module 1's role;
   its inline policy additionally allows the Runtime invoke actions. Create it
   with `python scripts/create_session_role.py` (resolves name/Region/invoke
   scope from `$BMA_RUNTIME_ARN`). Preview policies without touching AWS via
   `--dry-run`. If a custom `--role-name` was used, the labs need
   `BMA_INFERENCE_ROLE_ARN` set (the script prints the exact line) or
   `--role-arn` per command.

**Verify readiness first.** Before Module 2 operations, prefer running the
read-only preflight — it changes nothing and surfaces blockers:
```
cd modules/02-bma-agentcore-runtime
python preflight.py                               # uses $BMA_RUNTIME_ARN / $AWS_PROFILE
```
It reports each check as CONFIRMED / UNVERIFIED (unknown, not broken) / FAILED
(blocks the labs — fix via the printed NEXT ACTION). It exits non-zero only on
a FAILED check.

Always invoke scripts with the venv's Python after activating (or
`.venv/bin/python ...` without activating).

## Module 2 operations (AgentCore Runtime)

All paths are relative to `modules/02-bma-agentcore-runtime`. Every operation
needs a Runtime ARN (via `$BMA_RUNTIME_ARN` or `--runtime <arn>`). The agent
runs commands in the microVM, so results include `command_execution` items.

### Run a session on the Runtime (create + first turn)
Use `labs/lab-01-run-on-runtime/run_on_runtime.py`. With no prompt it submits
a neutral create-file/list/show shell task; pass a prompt to override.
```
python labs/lab-01-run-on-runtime/run_on_runtime.py
python labs/lab-01-run-on-runtime/run_on_runtime.py "List the files in /tmp and show the OS release"
```
Optional: `--model`, `--instructions`, `--aws-profile`, `--role-arn`,
`--runtime`. It prints `sess_...` — capture and report that id.

### Interpret a Runtime session's results (list its items)
Use `labs/lab-02-interpret-results/interpret_results.py`.
```
python labs/lab-02-interpret-results/interpret_results.py --session-id sess_abc123
python labs/lab-02-interpret-results/interpret_results.py --session-id sess_abc123 --limit 50 --order asc --json
```
Items are a tagged union (`message`, `reasoning`, `command_execution`). Use
`--json` to see each command and its output. With no `--session-id` it creates
a throwaway Runtime session (needs `--runtime`), runs once, then lists.

### Continue a Runtime session (another turn)
Use `labs/lab-03-continue-session/continue_session.py` with the session id and
a follow-up prompt.
```
python labs/lab-03-continue-session/continue_session.py --session-id sess_abc123 "Now show the contents of that file"
```
The session's state persists across turns even though the microVM is recycled
between periods of inactivity (a new turn gets a fresh environment, not the
previous turn's in-memory state).

### Delete Runtime session(s) — cleanup
Use `labs/lab-04-delete-session/delete_session.py`.
```
python labs/lab-04-delete-session/delete_session.py sess_abc123 sess_def456
```
Deleting a session removes its server-side history; it does **not** remove the
Runtime itself (the user removes that with `agentcore remove` when done with
the module, since it incurs charges while it exists).

## Workflow guidance

- First decide the module (see [Choosing a module](#choosing-a-module)), then
  pick the single script matching the user's intent; do not chain destructive
  operations without confirming. Never mix the two modules' venvs/scripts in
  one run.
- Before create/continue operations, confirm that module's prerequisites
  (Module 1: venv + profile; Module 2: venv + profile + `$BMA_RUNTIME_ARN` +
  the `BmaSessionRole-<region>`). For Module 2, running `python preflight.py`
  first is the fastest way to confirm readiness.
- After creating any session, surface the `sess_...` id and remind the user it
  persists until deleted.
- When a run errors, read the actual message:
  - `401`/expired/invalid token = AWS credentials (stale/wrong profile) in
    **both** modules — Module 2 mints a bearer token from the same AWS chain,
    so the fix is identical (refresh/select the profile).
  - A generic `-32603: tools/list failed at the AgentCore gateway` on an
    `mcp_call` item (Module 1 Lab 06) = Gateway IAM or MCP protocol-version
    issue (see that lab).
  - A Module 2 `AccessDenied` on `InvokeAgentRuntime` usually means the session
    role's invoke scope does not cover this Runtime — re-run
    `python preflight.py` and, if needed,
    `python scripts/create_session_role.py` with the correct `--runtime`.
- For anything the scripts do not cover, consult `references/api-cheatsheet.md`
  and build on that module's `bma_common.py` rather than hand-rolling
  client/auth logic.
