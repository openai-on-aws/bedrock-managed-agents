# Getting started with Amazon Bedrock Managed Agents (powered by OpenAI)

A hands-on, beginner-first learning repo for **Amazon Bedrock Managed Agents
(BMA), powered by OpenAI**. It starts from the basics and builds up, one module
at a time. It covers Module 1 (running agents with **no execution
environment**, `environment: {"type": "none"}`) and Module 2 (running agents on
an **AgentCore Runtime**, where the agent executes commands in a managed
microVM).

> **Public preview.** BMA is in preview. APIs, request/response shapes, default
> models, and Region availability can change, and some endpoints behave
> differently from the generally available Bedrock APIs. Treat everything here
> as a starting point, pin to the version you tested against, and check the
> official docs (linked below) for the current behavior.

## What is BMA?

Bedrock Managed Agents lets you run OpenAI-style agents on AWS without managing
the orchestration loop yourself. You describe the agent and send input; AWS runs
the turn, calls the model, optionally runs tools, and stores the conversation
for you. A few terms you will see throughout this repo:

- **Session** — the durable, server-side container for one agent conversation.
  It holds the agent definition (model + instructions) and the full history.
  Sessions are addressed by a session id (e.g. `sess_abc123`) and are never
  deleted automatically — you delete them when you are done.
- **Turn** — one request/response cycle within a session. You submit input, BMA
  runs the model (and any tools), and produces output.
- **Items** — the stored, structured record of what happened: user messages,
  assistant messages, reasoning, tool/MCP calls, command executions, and so on.
  You read items to interpret what an agent actually did.
- **Events** — the streaming signals emitted while a turn runs (for example,
  `agent.session.turn.output_text.done` and `agent.session.turn.completed`).
  You drain events to show output as it is produced.
- **Session role** — an IAM role that BMA *assumes* to perform
  customer-authorized work (chiefly model inference). The calling identity must
  be allowed to `iam:PassRole` this role to BMA. See
  [Module 1 prerequisites](modules/01-bma-environment-none/00-prerequisites.md).
- **Execution environment** — where the agent's tools/code run. BMA offers three
  options:
  - `none` — no execution environment; the agent reasons and calls model
    inference (and remote MCP tools) only. **This repo focuses on `none` for
    now.**
  - `self-hosted` — you host the execution environment.
  - AgentCore Runtime — AWS runs the execution environment for you.

See [BMA documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai.html) for details
## Repo map

```text
.
├── README.md                     # you are here
├── LICENSE                       # MIT-0 (MIT No Attribution)
├── .agents/
│   └── skills/
│       └── bma-sessions/         # Codex skill: drive the labs conversationally
├── modules/
    ├── 01-bma-environment-none/  # Module 1: agents with environment=none
    │   ├── README.md             # module overview + lab index
    │   ├── 00-prerequisites.md   # install, credentials, IAM setup
    │   ├── setup.py              # create the module's Python virtualenv
    │   ├── preflight.py          # read-only "verify you're ready" check (identity/region/role/IAM)
    │   ├── requirements.txt      # openai[bedrock], boto3
    │   ├── bma_common.py         # shared client/role helpers used by every lab
    │   ├── scripts/
    │   │   └── create_session_role.py  # create the BMA session role (least privilege)
    │   └── labs/
    │       ├── lab-01-create-session/
    │       ├── lab-02-interpret-results/
    │       ├── lab-03-session-state/
    │       ├── lab-04-list-sessions/
    │       ├── lab-05-delete-session/
    │       └── lab-06-agentcore-gateway/
    └── 02-bma-agentcore-runtime/ # Module 2: agents on an AgentCore Runtime
        ├── README.md             # module overview + lab index
        ├── 00-prerequisites.md   # provision Runtime, venv, bearer auth, IAM
        ├── setup.py              # create the module's Python virtualenv
        ├── preflight.py          # read-only "verify you're ready" check (identity/region/runtime/role/IAM)
        ├── requirements.txt      # openai[bedrock], boto3, aws-bedrock-token-generator
        ├── bma_common.py         # shared client/role/runtime helpers used by every lab
        ├── scripts/
        │   └── create_session_role.py  # create BmaSessionRole-<region> (least privilege)
        └── labs/
            ├── lab-01-run-on-runtime/
            ├── lab-02-interpret-results/
            ├── lab-03-continue-session/
            └── lab-04-delete-session/
└── samples/
    └── cad-workflow/             # BMA + native FreeCAD tools, verification and AWS deployment
```

## Modules

| Module | Focus / Key learning | Level | Est. time |
|---|---|---|---|
| [01 — BMA with `environment=none`](modules/01-bma-environment-none/README.md) | Create a session, interpret results and items, prove durable server-side session state, list and delete sessions, and (stretch) wire in AgentCore Gateway MCP tools — all without an execution environment. | Beginner | ~30–60 min |
| [02 — BMA with AgentCore Runtime](modules/02-bma-agentcore-runtime/README.md) | Run agents in a real execution environment where the agent executes commands in a managed microVM: run on a Runtime, read `command_execution` items, continue a session as the workspace persists, and clean up. | Intermediate | ~30–60 min |

## Samples

| Sample | What it demonstrates |
| --- | --- |
| [CAD workflow with FreeCAD](samples/cad-workflow/README.md) | BMA calls IAM-authenticated MCP tools through AgentCore Gateway. An EC2 worker builds editable native CAD, independently checks the frozen geometry, and returns versioned S3 artifacts. Includes CloudFormation deployment, offline tests, a native grader audit, an AWS diagram and a documented extension boundary for a future CATIA V6 backend. |

## Use it from Codex (optional)

This repo ships a [Codex](https://developers.openai.com/codex/) **skill**,
`bma-sessions`, that lets you drive the Module 1 labs conversationally instead
of remembering each script's flags — create, continue, inspect, list, and
delete BMA sessions, and attach an AgentCore Gateway tool. It lives at
[`.agents/skills/bma-sessions/`](.agents/skills/bma-sessions/README.md), so
Codex discovers it automatically when launched anywhere in this repo.

Ask naturally (for example, "create a BMA session and ask it the capital of
France", or "list my BMA sessions and delete the idle ones"), or invoke it
explicitly with `$bma-sessions`. It still relies on the same
[prerequisites](modules/01-bma-environment-none/00-prerequisites.md) (venv, AWS
profile, session role), makes real AWS calls, and never deletes sessions on its
own. See its [README](.agents/skills/bma-sessions/README.md) for setup and
example prompts.

## Official documentation

- [Bedrock Managed Agents, powered by OpenAI — overview](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai.html)
- [Prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-prerequisites.html)
- [Security](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-security.html)
- [Sessions](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-sessions.html)
- [API reference](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-managed-agents-openai-api-reference.html)

## License

This repository is licensed under the MIT-0 (MIT No Attribution) license. See
[LICENSE](LICENSE).
