# Module 1 — BMA with `environment=none`

This module teaches the core BMA (Bedrock Managed Agents, powered by OpenAI)
session workflow using **no execution environment**
(`environment: {"type": "none"}`). With `none`, the agent reasons and calls
model inference (and, in the stretch lab, remote MCP tools) — there is no
sandbox running code or local tools. That makes it the simplest place to learn
the session APIs, so it is where we start.

> **Public preview.** APIs, default models, and Region availability can change.
> See the [prerequisites doc](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-prerequisites.html).

## What this module covers

Your code (or a lab script) calls the **BMA session API** with a
**SigV4-signed** request — no OpenAI API key. BMA keeps the conversation in a
**durable, server-side session** (`sess_...`) and, by assuming the **session
role**, runs **model inference** on your behalf. With `environment = none`
there is **no sandbox**: the agent reasons and calls inference but runs no code
or local tools. You read the result back as **items** — `message`, `reasoning`,
and (in the stretch Gateway lab) `mcp_call`. Lab 06 adds the optional dashed
path: when the agent decides to use a tool, **BMA** invokes the **AgentCore
Gateway**'s MCP tool service-side (`bedrock-agentcore:InvokeGateway` on the
session role) and records the result as an `mcp_call` item.

![BMA with environment=none: your agent client code starts or continues a session via the SigV4-signed BMA session API; BMA assumes the session role and runs model inference via Amazon Bedrock, optionally invoking an AgentCore Gateway MCP tool; BMA records items (message, reasoning, mcp_call) on the durable server-side session, which the client reads back.](images/architecture.png)

## Prerequisites

Do the one-time setup first: install Python 3.12+ and the AWS CLI v2, configure
AWS credentials, and create the session role. The full walkthrough — including
the three IAM identities and least-privilege guidance — is in
[00-prerequisites.md](00-prerequisites.md).

### 1. Build the virtual environment

Everything in this module runs inside a per-module Python virtualenv. Create it
once:

```bash
cd modules/01-bma-environment-none
python3 setup.py   # macOS/Linux; on Windows use `python setup.py`
```

### 2. Set your AWS profile in every terminal

AWS credentials are **per terminal**. In each new shell, either export the
profile or pass it per command:

```bash
export AWS_PROFILE=<your-aws-profile>
# ...or add --aws-profile <your-aws-profile> to each lab command
```

A stale or wrong profile shows up as a `401 AuthenticationError`. Fix it by
exporting the correct `AWS_PROFILE` (or passing `--aws-profile`) for that
terminal.

### 3. Verify you're ready

After the venv is built and your profile is configured, run the read-only
preflight check to confirm your Python/SDK, AWS identity, Region, session role,
and IAM config before Lab 01:

```bash
python preflight.py
```

It reports each check as CONFIRMED, UNVERIFIED, or FAILED and prints a next
action for anything that needs attention. See
[Verify you're ready](00-prerequisites.md#6-verify-youre-ready) for what the
labels mean.

## Run convention

All labs use the same convention: with the venv **active** (set up once in the
[prerequisites](00-prerequisites.md#1-install-the-tools); re-activate in each
new terminal with `source .venv/bin/activate`, or `.\.venv\Scripts\Activate.ps1`
on Windows), run the lab script directly. There are no per-lab wrapper scripts.

```bash
python labs/lab-01-create-session/create_session.py "Hello, BMA!"
```

Each lab script imports the shared `bma_common.py` from the module root
automatically, so you can run it from any working directory. Pass `--help` to
any lab script for its full flag list (region, model, instructions, role ARN,
and so on).

## Lab index

Work through the labs in order; each one builds on the ideas before it.

| # | Lab | What you learn |
|---|---|---|
| 01 | [lab-01-create-session](labs/lab-01-create-session/README.md) — `create_session.py` | Create a session, submit the first prompt, and stream the agent's output. |
| 02 | [lab-02-interpret-results](labs/lab-02-interpret-results/README.md) — `interpret_results.py` | List a session's items and read the tagged-union result shape (messages, reasoning, tool calls). |
| 03 | [lab-03-session-state](labs/lab-03-session-state/README.md) — `session_state.py` | Build a shared to-do list where several terminals add to the same session, showing durable, server-side state shared across users. |
| 04 | [lab-04-list-sessions](labs/lab-04-list-sessions/README.md) — `list_sessions.py` | List sessions in the account/Region and read their status and metadata. |
| 05 | [lab-05-delete-session](labs/lab-05-delete-session/README.md) — `delete_session.py` | Delete sessions and understand cleanup (sessions are never auto-deleted). |
| 06 | [lab-06-agentcore-gateway](labs/lab-06-agentcore-gateway/README.md) — `gateway_session.py` | **Stretch/advanced:** attach an AgentCore Gateway MCP tool and gate each add to the Lab 03 to-do list on the tool's result. |

> The lab files are added in the labs' own commits; the directory and script
> names above are the stable paths every lab uses.

## Use these labs from Codex (optional)

This repo ships a [Codex](https://developers.openai.com/codex/) **skill** that
lets you drive these labs conversationally instead of remembering each script's
flags. It lives at `.agents/skills/bma-sessions/` in the repo root, so Codex
discovers it automatically when you launch Codex anywhere inside this
repository.

The `bma-sessions` skill knows how to create, continue, inspect, list, and
delete BMA sessions, and how to attach an AgentCore Gateway tool — by running
the same lab scripts documented above. Ask Codex naturally (for example,
"create a BMA session and ask it the capital of France", or "list my BMA
sessions and delete the idle ones"), or invoke it explicitly with
`$bma-sessions`.

The skill still relies on the prerequisites on this page: the venv must be set
up and your AWS profile and session role must be configured. It makes real AWS
calls and never deletes sessions on its own — it will surface session ids and
offer cleanup.
