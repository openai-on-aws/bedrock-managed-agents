# Module 2 prerequisites

Work through this page once before the labs: AWS provisions the Runtime, then
you set up a venv and a session role here.

> **Public preview.** BMA (powered by OpenAI) and AgentCore Runtime are in
> preview. Default models, Region availability, the AgentCore CLI surface, and
> the exact IAM actions can change. Treat the policies here as a least-privilege
> **starting point** and confirm the current requirements in the official docs:
>
> - [AgentCore Runtime get-started (BMA)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-bma.html)
> - [BMA prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-prerequisites.html)
> - [BMA security](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-security.html)

> **Supported platforms.** Our Python setup runs natively on macOS, Linux, and
> Windows. Commands are shown in macOS/Linux form; Windows
> differs only in venv activation and backslash paths (see
> [Running on Windows](#running-on-windows-powershell--windows-only)). The
> AgentCore CLI in §2 is separate AWS tooling (needs Node.js).

> **Handling output.** The labs print prompts, session ids, the role ARN, and
> (with `--json`) full items including the commands the agent ran — so
> redirecting output to a shared log or committing it can leak prompt/command
> content. The scripts never print AWS credentials or tokens. **Treat a session
> id like a credential:** anyone who can call BMA in your account and has the id
> can continue, inspect, or delete that session — don't paste session ids into
> shared/public places. Use a disposable workspace and don't mount secrets into
> the Runtime.

## 1. What AgentCore Runtime is (and why the environment changes)

An **AgentCore Runtime** is a managed, isolated microVM that AWS runs for you.
When a session uses it as its execution environment, the agent can actually
**execute commands** against a workspace — write files, run shell commands,
read results back — not just reason and emit text.

That is the bridge from Module 1. There, `environment={"type": "none"}` meant
no sandbox: the agent reasoned and produced text but ran nothing. Here the
environment becomes `aws_bedrock_agentcore`, naming a specific Runtime ARN, and
the agent's work shows up as `command_execution` items (the command plus its
output). Everything else about sessions — turns, items, events, cleanup — is
the same as Module 1; only the execution environment (and, as a consequence,
the auth and the session role) changes.

## 2. Provision the AgentCore Runtime 

The Runtime itself is created with the **AgentCore CLI**, following the
official
[AgentCore Runtime get-started (BMA)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-bma.html)
tutorial. This repo does **not** reimplement the CLI or the deploy — you run
that tutorial once, then bring back the **Runtime ARN** for the labs here. The
flow is roughly (confirm exact commands and flags in the linked tutorial, as
they can change in preview):

1. Install Node.js 20+ and the AgentCore CLI, then confirm it:

   ```bash
   npm install -g @aws/agentcore
   agentcore --version
   ```

2. Create and deploy a BMA-framework Runtime:

   ```bash
   agentcore create --name MyManagedAgent --framework BedrockManagedAgents
   agentcore deploy
   ```

3. Read back the Runtime ARN:

   ```bash
   agentcore status
   # -> arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<runtime-id>
   ```

4. **Set the Runtime ARN once per terminal.** Every lab, `preflight.py`, and
   `create_session_role.py` read `$BMA_RUNTIME_ARN`, so export it once and you
   can drop the `--runtime` flag from every command below:

   ```bash
   export BMA_RUNTIME_ARN=arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<runtime-id>
   ```

   (Windows PowerShell: `$env:BMA_RUNTIME_ARN =
   "arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<runtime-id>"`.)
   It is per-terminal, like `AWS_PROFILE` — re-export it in each new shell, or
   pass `--runtime <arn>` explicitly instead. The ARN also sets the Region the
   session runs in, so you do not pass `--region` separately for the labs.

Deploying typically takes **~15 minutes** and provisions AWS resources that
**incur charges** while they exist, so remember to remove the Runtime
(`agentcore remove`) when you are done with the module.

> **This repo starts after the Runtime exists.** We do not script the CLI,
> the deploy, or the Runtime's execution role. You only need
> the Runtime ARN to continue.

## 3. Our setup (venv + session role)

With a Runtime ARN in hand, set up the local tooling for the labs.

- **Python 3.12 or newer** — check with `python3 --version`.
- **AWS CLI v2** (optional) — handy for configuring credentials; the scripts
  themselves use boto3, not the CLI.

Build the module virtual environment and install dependencies
(`openai[bedrock]`, `boto3`, and `aws-bedrock-token-generator`), then
**activate** it. These are two steps: `setup.py` creates the venv, and you
activate it in your own shell (no script can do that for you).

```bash
cd modules/02-bma-agentcore-runtime
python3 setup.py                 # 1. create the venv + install deps
source .venv/bin/activate        # 2. activate it in THIS terminal
```

`setup.py` creates the venv; you activate it yourself (no script can activate
your shell). Once active, plain `python` points at the venv. Re-activate in
each new terminal — only re-run `setup.py` when `requirements.txt` changes (or
`python3 setup.py --force` to rebuild). On macOS/Linux use `python3` for this
one setup command (there is often no bare `python`); after activation `python`
works everywhere. Windows differs only in the activation command — see the box
below.

> **Coming from Module 1?** Still run `setup.py` here. Module 2 has its **own**
> venv and one extra dependency (`aws-bedrock-token-generator`, for bearer-token
> auth), so Module 1's venv won't work. If Module 1's venv is active,
> `deactivate` first (or just activate Module 2's).

Then set your AWS profile — the labs sign STS/IAM calls and mint the bearer
token with it:

```bash
export AWS_PROFILE=<your-aws-profile>
aws sts get-caller-identity --profile "$AWS_PROFILE"   # confirm it resolves
```

It is per-terminal, like the venv — re-export in each new shell, or pass
`--aws-profile <profile>` per command. Then create the session role
([Step 4](#4-create-the-session-role), next).

---

### Running on Windows (PowerShell) — Windows only

*This box is for Windows users and is still part of Step 3. On macOS/Linux you
can skip it entirely and go to [Step 4](#4-create-the-session-role).*

Our setup is all Python, so it runs natively on Windows — no Bash, WSL, or Git
Bash. Setup, role creation, the preflight, and all four labs each run with
`python ...`. Windows differs from macOS/Linux in just two mechanical ways: the
venv **activation command** and **backslash paths**.

Run setup exactly as on macOS/Linux:

```powershell
cd modules\02-bma-agentcore-runtime
python setup.py
```

Then, wherever this guide shows a macOS/Linux command, apply these two
substitutions:

- **Activate the venv** with `.\.venv\Scripts\Activate.ps1` instead of
  `source .venv/bin/activate`.
- **Use backslash paths** when calling a lab script, e.g.
  `python labs\lab-01-run-on-runtime\run_on_runtime.py --runtime <arn>`.

Creating the session role is native too:

```powershell
python scripts\create_session_role.py --runtime $env:BMA_RUNTIME_ARN
```

The preflight is likewise `python preflight.py --runtime <arn>`.

> If a PowerShell detail differs on your setup (for example an
> `Activate.ps1` execution-policy prompt), the underlying Python is the same —
> activate the venv however your system requires, then call the scripts
> directly. The AgentCore CLI in §2 is separate Node.js tooling and follows its
> own install instructions.

*End of the Windows-only box. The rest of Step 3 is done — continue to Step 4.*

---

> ## Auth and role differ from Module 1 — on purpose
>
> **If you did Module 1, do not reuse its auth or its role here.** Two things
> are intentionally different in Module 2:
>
> - **Auth is a bearer token, not SigV4.** Module 1 signed each request with
>   the `bedrock` provider (SigV4). Module 2 mints a short-lived **bearer
>   token** with `aws-bedrock-token-generator` and sends it as the API key
>   (still derived from your AWS credential chain — no OpenAI API key). This is
>   handled in `bma_common.make_client`; you do not call it directly.
> - **The session role is `BmaSessionRole-<region>`, not Module 1's role.**
>   Module 1 used `BedrockManagedAgentsPreviewInferenceServiceRole`. Module 2
>   uses a region-scoped `BmaSessionRole-<region>` whose inline `BmaSession`
>   policy additionally allows the Runtime invoke actions. You do **not** need
>   Module 1's `create_session_role.py` or its role here — create the Module 2
>   role with the script in §4.

## 4. Create the session role

The session role is the role BMA **assumes** to do inference AND to invoke the
AgentCore Runtime. You can create it with the provided script, or by hand.
Either way, review the policies against least privilege before using them
beyond a sandbox.

Preview the exact policies without touching AWS (no credentials needed). The
`--dry-run` falls back to placeholder values where real ones are absent:

```bash
cd modules/02-bma-agentcore-runtime
python scripts/create_session_role.py --dry-run --runtime <runtime-arn>
```

`create_session_role.py` uses boto3, so a real run needs the venv active (i.e.
after `python3 setup.py` and activating). Create it for real (prompts for
confirmation; pass `--yes` to skip, `--model` to override the model):

```bash
python scripts/create_session_role.py
```

The role name, Region, and the Runtime invoke permissions all come from the
Runtime ARN (`$BMA_RUNTIME_ARN`): the default name is `BmaSessionRole-<region>`,
derived from the Runtime's Region. **Recommended: keep the default.** To use a
different name, set it once (same `BMA_ROLE_NAME` convention as Module 1) and
pass it:

```bash
export BMA_ROLE_NAME=MyBmaRole        # optional; default is BmaSessionRole-<region>
python scripts/create_session_role.py --role-name "$BMA_ROLE_NAME"
```

**Pointing the labs at the role you just created.** If you used the **default**
role name, the labs and `python preflight.py` resolve it automatically from the
Runtime ARN's Region and your account id — nothing more to do. If you used a
**custom** `--role-name`, the script prints a ready-to-paste
`export BMA_INFERENCE_ROLE_ARN=...` line (and the Windows `$env:` form); run it
in each terminal (or pass `--role-arn <arn>` per command).

The script creates the role plus its inline `BmaSession` policy. It also
**prints** — without creating — two policies for your own calling identity: an
`iam:PassRole` policy (so you may pass the session role to BMA) and a BMA-client
policy (the session actions **plus `bedrock-mantle:CallWithBearerToken`**, which
Module 2's bearer-token auth requires). Attach those to your identity only if it
does not already allow them (an admin/broad role usually does; a
least-privilege identity usually does not).

### Create it by hand (least privilege)

Replace `123456789012` with your account id, `my-runtime-id` with your Runtime
id, and the Region/model as needed.

**Trust policy** on the session role (lets BMA assume it, scoped to your
account):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "bedrock-mantle.amazonaws.com" },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": { "aws:SourceAccount": "123456789012" },
        "ArnLike": { "aws:SourceArn": "arn:aws:bedrock-mantle:*:123456789012:*" }
      }
    }
  ]
}
```

**Inline `BmaSession` policy** on the session role (one model's inference, plus
invoking the one Runtime):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock-mantle:CreateInference",
      "Resource": "*",
      "Condition": {
        "StringEquals": { "bedrock-mantle:Model": "openai.gpt-5.6-luna" }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:InvokeAgentRuntime",
        "bedrock-agentcore:StopRuntimeSession"
      ],
      "Resource": [
        "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id",
        "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id/runtime-endpoint/*"
      ]
    }
  ]
}
```

**On your own calling identity**, an `iam:PassRole` policy so you may pass the
role to BMA:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::123456789012:role/BmaSessionRole-us-east-1",
      "Condition": {
        "StringEquals": { "iam:PassedToService": "bedrock-mantle.amazonaws.com" }
      }
    }
  ]
}
```

…and a BMA-client policy granting the session actions plus the bearer-token
call:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-mantle:CallWithBearerToken",
        "bedrock-mantle:CreateAgentSession",
        "bedrock-mantle:GetAgentSession",
        "bedrock-mantle:ListAgentSessions",
        "bedrock-mantle:CreateAgentSessionEvent",
        "bedrock-mantle:ListAgentSessionEvents",
        "bedrock-mantle:ListAgentSessionItems",
        "bedrock-mantle:DeleteAgentSession"
      ],
      "Resource": "*"
    }
  ]
}
```

> **Keep `--model` in sync with the session role.** The role's inference policy
> only permits the model it was created with (default `openai.gpt-5.6-luna`). If
> you pass a different `--model` to a lab, re-create the role for that model or
> add it to the policy's `bedrock-mantle:Model` condition — otherwise inference
> is denied.

## 5. The four IAM identities

> **These IAM policies are learning-sandbox examples, not production
> configurations.** Everything above and below (the trust policy, the inline
> `BmaSession` policy, and the printed caller policies) is scoped for a tutorial
> and uses broad patterns — e.g. the BMA-client policy's `Resource "*"`, and
> reusing an existing role leaves its current trust and permissions in place.
> Before using any of this beyond a sandbox: review and narrow each policy,
> prefer an isolated role/account, confirm an existing role's trust and attached
> permissions are not broader than you intend, and verify with the IAM policy
> simulator. See [§6 Least privilege](#6-least-privilege--review-before-deploying).

Module 1 had three identities. Module 2 adds a **fourth** — the Runtime's
execution role — because the agent now runs code somewhere:

1. **Provisioning identity** — whoever creates the session role and attaches
   policies during setup. Needs **administrative IAM permissions**
   (`iam:CreateRole`, `iam:PutRolePolicy`, and the `put/attach` actions for the
   caller policies). These are **transient**: needed only for setup, often done
   by an administrator.
2. **Caller / BMA-client identity** — the user or role *you* authenticate as to
   invoke the BMA session APIs. It needs the BMA-client session actions, the
   **`bedrock-mantle:CallWithBearerToken`** permission (Module 2's bearer-token
   auth), **and** `iam:PassRole` on the session role. These are **persistent** —
   your calling identity keeps them to run the labs.
3. **Session role** — the role BMA *assumes* (via `bedrock-mantle.amazonaws.com`)
   to do customer-authorized work: model inference AND invoking the Runtime
   (`bedrock-agentcore:InvokeAgentRuntime` / `StopRuntimeSession`). Default name:
   `BmaSessionRole-<region>`.
4. **Runtime execution role** — the role the AgentCore Runtime runs **under**
   when it executes the agent's tools/code in the microVM. Created by
   `agentcore deploy` ([§2](#2-provision-the-agentcore-runtime-aws-owns-this))
   and **separate from the session role** — do not conflate them. (In Module 1,
   `environment=none` had no execution environment, so this did not exist.)

## 6. Least privilege — review before deploying

Before using these policies beyond a sandbox:

- Keep the inference statement's `Resource "*"` as-is.
  `bedrock-mantle:CreateInference` does not take a model-specific resource ARN,
  so you cannot narrow it that way. The model restriction comes from the
  `bedrock-mantle:Model` condition, not from scoping `Resource`.
- Confirm the `bedrock-mantle:Model` condition pins exactly the model(s) you
  intend to allow.
- Narrow the `bedrock-agentcore` statement. It is already scoped to the one
  Runtime (and its endpoints) you passed via `--runtime`; for production, pin
  it further to a specific `runtime-endpoint` qualifier instead of the `/*`
  wildcard, and grant only the actions you actually use.
- Narrow the BMA-client policy's `Resource "*"` where your account supports
  resource-level scoping.
- Keep the trust policy scoped to your account (`aws:SourceAccount` /
  `aws:SourceArn`); do not broaden the principal.
- **Verify** the effective permissions (for example with the IAM policy
  simulator) before deploying.

Full guidance is in the official
[prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-prerequisites.html)
and
[security](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-security.html)
docs.

## 7. Verify you're ready

Before Lab 01, run the read-only preflight check. It inspects — and only
inspects — the pieces the labs depend on: your Python/SDK versions (including
`aws-bedrock-token-generator`), your AWS identity, the Region, the Runtime ARN
(presence/shape only — it makes no runtime API call), the exact session-role
ARN, and (best-effort) the role's IAM config. It creates **no** session and
changes **nothing**.

```bash
cd modules/02-bma-agentcore-runtime
python preflight.py                               # uses $BMA_RUNTIME_ARN / $AWS_PROFILE
python preflight.py --aws-profile <your-aws-profile>
```

Each check is reported as one of three labels:

- **CONFIRMED** — verified just now.
- **UNVERIFIED** — could **not** be checked (for example, you lack IAM read
  permission like `iam:GetRole`, or the Runtime's existence was not probed).
  This is **not** the same as broken; it means "unknown". Follow the printed
  NEXT ACTION only if you want to close the gap.
- **FAILED** — actively wrong and will block the labs. Fix it using the printed
  NEXT ACTION before continuing.

The check exits non-zero only when something is **FAILED**. A clean run means no
blockers were found — it does **not** guarantee that every session operation is
authorized, only that the listed checks passed.

---

Once your Runtime, credentials, and session role are in place and
`python preflight.py` reports no FAILED checks, start with the
[module overview and lab index](README.md).
