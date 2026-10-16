# Module 1 prerequisites

Before running any lab in this module, work through this page once. It covers
the tooling you need, how to configure AWS credentials, the IAM identities BMA
involves, and two ways to create the session role.

> **Public preview.** BMA (powered by OpenAI) is in preview. Default models,
> Region availability, and the exact IAM actions can change. Treat the policies
> here as a least-privilege **starting point** and confirm the current
> requirements in the official docs:
>
> - [Prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-prerequisites.html)
> - [Security](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-security.html)

> **Supported platforms.** The helper scripts in this module (`setup.py`,
> `scripts/create_session_role.py`) and the Python lab scripts are all
> cross-platform, so everything runs natively on **macOS, Linux and Windows**
> — no Bash or WSL required. The commands below are shown in macOS/Linux form;
> **Windows** differs only in the venv activation command and backslash paths.
> See [Running on Windows](#running-on-windows-powershell--windows-only) for the details.

> **Handling output.** The labs print what you send and receive — your prompts,
> the session id, the resolved role ARN, and (with `--json`) full session items
> including messages and tool arguments. That is intentional for learning, but
> it means **redirecting lab output to a file or CI log can capture sensitive
> prompt content**. Don't pipe lab output into shared logs, and don't commit it.
> The scripts never print AWS credentials or session tokens.

## 1. Install the tools

- **Python 3.12 or newer** — check with `python3 --version`.
- **AWS CLI v2** — check with `aws --version`. Used to configure credentials and
  (optionally) to create the session role.

Then build the module virtual environment and install dependencies
(`openai[bedrock]` and `boto3`), then **activate** it. These are two steps:
`setup.py` creates the venv, and you activate it in your own shell (no script
can do that for you).

```bash
cd modules/01-bma-environment-none
python3 setup.py                 # 1. create the venv + install deps
source .venv/bin/activate        # 2. activate it in THIS terminal
```

Once activated, your prompt usually shows a `(.venv)` prefix, and plain
`python` now points at the venv — so every command in this guide and the labs
is just `python ...`.

> On macOS/Linux the setup interpreter is usually `python3` (there is often no
> bare `python`); use whichever name your system has, and `python3 --version`
> should report 3.12+. On Windows, run `python setup.py` and activate with
> `.\.venv\Scripts\Activate.ps1` (see the Windows box below). The interpreter
> name only matters for this one-time setup step — after activation, `python`
> works everywhere.

**Activate the venv in every new terminal.** Activation lasts only for the
shell you ran it in. Each time you open a new terminal to run a lab, re-run
`source .venv/bin/activate` (Windows: `.\.venv\Scripts\Activate.ps1`) from the
module root first. You do **not** need to re-run `setup.py` — only re-run it
when `requirements.txt` changes, or `python3 setup.py --force` to rebuild the
venv from scratch.

---

### Running on Windows (PowerShell) — Windows only

*This box is for Windows users and is still part of Step 1. On macOS/Linux you
can skip it entirely and go to [Step 2](#2-configure-aws-credentials-and-a-region).*

Everything in this module is Python, so it runs natively on Windows — no
Bash, WSL, or Git Bash. Setup, role creation, the preflight, and all six labs
each run with `python ...`. Windows differs from macOS/Linux in just two
mechanical ways: the venv **activation command** and **backslash paths**.

Run setup exactly as on macOS/Linux:

```powershell
cd modules\01-bma-environment-none
python setup.py
```

Then, wherever this guide shows a macOS/Linux command, apply these two
substitutions:

- **Activate the venv** with `.\.venv\Scripts\Activate.ps1` instead of
  `source .venv/bin/activate`.
- **Use backslash paths** when calling a lab script, e.g.
  `python labs\lab-01-create-session\create_session.py "..."`.

Creating the session role is native too:

```powershell
python scripts\create_session_role.py --role-name "$env:BMA_ROLE_NAME"
```

The preflight is likewise `python preflight.py`.

> If a PowerShell detail differs on your setup (for example an
> `Activate.ps1` execution-policy prompt), the underlying Python is the same —
> activate the venv however your system requires, then call the scripts
> directly.

*End of the Windows-only box. The rest of Step 1 is done — continue to Step 2.*

---

## 2. Configure AWS credentials and a Region

BMA calls are SigV4-signed with your AWS credentials — there is no OpenAI API
key. The labs and scripts sign with the profile named in the standard
`AWS_PROFILE` environment variable, so set it once per terminal to the profile
whose credentials you'll use:

```bash
# The standard AWS variable; every AWS tool and these labs read it.
export AWS_PROFILE=<your-aws-profile>
```

Configure that profile (first time only) and confirm it resolves:

```bash
aws configure --profile "$AWS_PROFILE"
aws sts get-caller-identity --profile "$AWS_PROFILE"
```

AWS credentials are **per terminal**: `AWS_PROFILE` only applies to the shell
you set it in, so run `export AWS_PROFILE=...` again in each new terminal (or
add it to your shell startup file). For a one-off, pass `--aws-profile
<your-aws-profile>` to a lab script instead of exporting. A stale or wrong
profile shows up as a `401 AuthenticationError` when you run a lab.

**Region and model.** This module defaults to Region `us-east-1` (choices:
`us-east-1`, `us-west-2`, `us-east-2`) and model `openai.gpt-5.6-luna`. Model and
Region availability are account-dependent. Use `--region` / `--model` to match
what your account exposes.

> **Keep `--model` in sync with the session role.** The role's inference policy
> only permits the model it was created with (default `openai.gpt-5.6-luna`). If
> you pass a different `--model`, re-create the role for that model
> (`python scripts/create_session_role.py --model <id>`) or add it to the
> policy's `bedrock-mantle:Model` condition — otherwise inference is denied.

## 3. The three IAM identities

> **These IAM policies are learning-sandbox examples, not production
> configurations.** Everything below (the trust policy, the inference policy,
> and the printed caller policies) is scoped for a tutorial and uses broad
> patterns — e.g. the BMA-client policy's `Resource "*"`, and reusing an
> existing role leaves its current trust and permissions in place. Before using
> any of this beyond a sandbox: review and narrow each policy, prefer an
> isolated role/account, confirm an existing role's trust and attached
> permissions are not broader than you intend, and verify with the IAM policy
> simulator. See [§5 Least privilege](#5-least-privilege--review-before-deploying).

BMA involves up to three distinct identities. Keeping them separate — and being
clear about **who does what** and **which permissions persist** — is what the
IAM setup below is about:

1. **Provisioning identity** — whoever creates the session role and attaches
   policies during setup. This needs **administrative IAM permissions**
   (`iam:CreateRole`, `iam:PutRolePolicy`, and `iam:PutUserPolicy` /
   `iam:AttachUserPolicy` / `iam:AttachRolePolicy` to attach the caller
   policies). These permissions are **transient**: they are only needed for
   setup. A learner does **not** need to keep them afterward, and in many orgs
   an administrator does this part.
2. **Caller / BMA-client identity** — the user or role *you* authenticate as
   day-to-day to invoke the BMA session APIs (create/get/list/delete sessions,
   submit events, list items). It needs the BMA-client session actions **and**
   permission to `iam:PassRole` the session role to BMA. These permissions are
   **persistent** — your calling identity keeps them to run the labs.
3. **Session role** — the role BMA *assumes* (via `bedrock-mantle.amazonaws.com`)
   to do customer-authorized work, chiefly model inference. You never call AWS
   *as* this role directly; BMA assumes it on your behalf. Default name:
   `BedrockManagedAgentsPreviewInferenceServiceRole`.

(There is also a fourth, **execution-environment identity** — the identity under
which an execution environment runs tools/code — but it is **not applicable for
`environment=none`**, which is this entire module. It becomes relevant only for
the self-hosted and AgentCore Runtime environments covered in later modules.)

## 4. Create the session role

You can create the session role with the provided script, or by hand. Either
way, review the policies against least privilege before using them beyond a
sandbox.

```bash
# Any name you like; this is just the example's default.
export BMA_ROLE_NAME=BedrockManagedAgentsPreviewInferenceServiceRole
```

Point the lab scripts at your role with `--role-arn` or
`export BMA_INFERENCE_ROLE_ARN=arn:aws:iam::<account-id>:role/$BMA_ROLE_NAME`.
If you skip both, the labs fall back to the default name above in your own
account.

### Option A — use the script

Preview the exact policies without touching AWS (no credentials needed):

```bash
cd modules/01-bma-environment-none
python scripts/create_session_role.py --dry-run
```

`create_session_role.py` uses boto3, so run it with the venv active (i.e.
after `python3 setup.py` and activating the venv). The `--dry-run` above needs
no credentials.

Then create it for real (prompts for confirmation; pass `--yes` to skip, and
`--model` to override the model). Pass `--role-name` to control the name:

```bash
python scripts/create_session_role.py --role-name "$BMA_ROLE_NAME"
```


**Pointing the labs at the role you just created.** If you used the **default**
role name, the labs and `python preflight.py` find it automatically from your
account id — nothing more to do. If you used a **custom** `--role-name`, the
script prints a ready-to-paste `export BMA_INFERENCE_ROLE_ARN=...` line at the
end; run it in each terminal (or pass `--role-arn <arn>` per command) so every
lab and the preflight target your role rather than the default-named one.

The script creates the role plus its inline inference policy. It also **prints**
— without creating — two policies for your own calling identity: an
`iam:PassRole` policy (so you may pass the session role to BMA) and a BMA-client
policy (the session API actions). Whether you need to attach them depends on
your identity.

#### Do you need to attach the caller policies?

The script only creates the session role; it does **not** change your calling
identity. Whether that matters depends on what your identity already allows:

- **If you authenticate as a broad / administrator role or user** (e.g. an
  `Admin` role whose policy already allows the BMA session actions and
  `iam:PassRole`), you can **skip** attaching these — Lab 01 just works. The
  printed policies are already covered by your existing permissions.
- **If you authenticate as a least-privilege identity** that does *not* already
  grant those actions, you **must** attach them, or session calls in Lab 01
  fail with access-denied.

Not sure which you are? The simplest test is to try: run `python preflight.py` and
then Lab 01. If a session call fails with an access-denied / not-authorized
error (typically on `CreateAgentSession` or `iam:PassRole`), come back and
attach the policies below. (The preflight confirms identity and role, but it
cannot by itself prove every session operation is authorized — only an actual
session call does.)

#### Attaching the caller policies (only if needed)

If you authenticate as an **IAM user** (replace the names and account id with
your own; the policy JSON is what the script printed):

```bash
# 1) iam:PassRole policy — lets you pass the session role to BMA.
aws iam put-user-policy \
  --user-name <your-iam-user> \
  --policy-name BmaPassRole \
  --policy-document file://passrole-policy.json

# 2) BMA-client policy — the session actions your identity needs.
aws iam put-user-policy \
  --user-name <your-iam-user> \
  --policy-name BmaClient \
  --policy-document file://bma-client-policy.json
```

If you authenticate as an **IAM role** (for example an assumed role), use
`aws iam put-role-policy --role-name <role> ...` with the same two documents.

**IAM Identity Center / AWS SSO path.** If you sign in through IAM Identity
Center (AWS SSO), you authenticate as a short-lived role tied to a **permission
set**, and you usually **cannot** attach policies to your own identity. In that
case an **administrator** must add these two policies (the `iam:PassRole` on the
session role, and the BMA-client session actions) to the relevant **permission
set** so they flow to everyone who assumes it. Point your administrator at the
exact role/permission-set you assume (visible in `aws sts get-caller-identity`)
and the two policy documents the script printed.

### Option B — create it by hand (least privilege)

Replace `123456789012` with your own account id, `BMA_ROLE_NAME` with the role
name you chose above, and adjust the model as needed. The trust and inference
policies do not reference the role name; only the `iam:PassRole` resource ARN
below does, so keep that in sync with the name you actually create.

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

**Inline inference policy** on the session role (allows inference for one
model):

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
    }
  ]
}
```

**On your own calling identity**, attach an `iam:PassRole` policy so you may pass
the role to BMA:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::123456789012:role/BMA_ROLE_NAME",
      "Condition": {
        "StringEquals": { "iam:PassedToService": "bedrock-mantle.amazonaws.com" }
      }
    }
  ]
}
```

…and a BMA-client policy granting the session actions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
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

> **Reusing an existing session role?** If you point the labs at a role that
> already exists (whether created earlier or by someone else), its
> already-attached permissions remain fully in effect. Adding the narrow inline
> inference policy above does **not** remove or override broader permissions the
> role already carries. If least privilege matters, review what the role already
> grants (`aws iam list-role-policies` / `list-attached-role-policies`) rather
> than assuming the narrow policy is all it can do.

## 5. Least privilege — review before deploying

These policies are a **starting point** for a learning sandbox, not a finished
production configuration. Before using them beyond a sandbox:

- Keep the inference policy's `Resource "*"` as-is.
  `bedrock-mantle:CreateInference` does not take a model-specific resource ARN,
  so you cannot narrow it that way. The model restriction comes from the
  `bedrock-mantle:Model` condition below, not from scoping `Resource`.
- Confirm the `bedrock-mantle:Model` condition pins exactly the model(s) you
  intend to allow — this condition, not `Resource`, is what limits which model
  may be invoked.
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

## 6. Verify you're ready

Before Lab 01, run the read-only preflight check. It inspects — and only
inspects — the pieces the labs depend on: your Python/SDK versions, your AWS
identity, the Region, the exact session-role ARN, and (best-effort) the role's
IAM config. It creates **no** session and changes **nothing**.

```bash
cd modules/01-bma-environment-none
python preflight.py                                 # uses $AWS_PROFILE / defaults
python preflight.py --aws-profile <your-aws-profile> --region us-east-1
```

Each check is reported as one of three labels:

- **CONFIRMED** — verified just now.
- **UNVERIFIED** — could **not** be checked (for example, you lack IAM read
  permission like `iam:GetRole`). This is **not** the same as broken; it means
  "unknown". Follow the printed NEXT ACTION only if you want to close the gap.
- **FAILED** — actively wrong and will block the labs. Fix it using the printed
  NEXT ACTION before continuing.

The check exits non-zero only when something is **FAILED**. A clean run means no
blockers were found — it does **not** guarantee that every session operation is
authorized, only that the listed checks passed.

---

Once your credentials and session role are in place and `python preflight.py` reports
no FAILED checks, start with the
[module overview and lab index](README.md).
