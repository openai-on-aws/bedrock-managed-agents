# Lab 01 — Run on an AgentCore Runtime

**What you learn:** how to create a BMA session that runs on an AgentCore
Runtime, submit the first prompt, and watch the agent **execute commands** in
a managed microVM — not just reason.

## Concept

A **session** is the durable, server-side container for one conversation with
an agent. You create it once, then submit **turns** (one user prompt + the
agent's response) against it. BMA keeps the full history on the server, so
later turns — even from a different terminal — see everything that came before.

The difference from Module 1 is the **environment**. Module 1 used
`environment={"type": "none"}`: the agent reasoned and produced text but could
not run anything. Here the environment is an **AgentCore Runtime**, a managed
microVM, so the agent can actually RUN commands against a workspace. That is
the whole payoff of this module: the agent does real work, and you can see
exactly what it ran.

Three things are required to create the session:

- **`agent`** — the `model` to run and the `instructions` that shape its
  behavior.
- **`environment`** — the `aws_bedrock_agentcore` dict naming your Runtime ARN
  (built by `bma_common.agentcore_environment(...)`).
- **`role_arn`** — the IAM session role (`BmaSessionRole-<region>`) BMA assumes
  to run inference AND to invoke the Runtime. The typed SDK does not expose
  this field yet, so it is passed through `extra_body`.

The first prompt goes in `input` as a list of messages, each with `content`
parts of type `input_text`. The default prompt is a neutral shell task: create
`hello.txt`, list the workspace, and show the file's contents.

## What you'll run

`run_on_runtime.py` builds the bearer-token client, resolves the session role
ARN, calls `sessions.create(...)` with the AgentCore environment, and then
**drains the turn** — opens the event stream and reads lifecycle events until
the turn completes:

- prints the final text on `agent.session.turn.output_text.done`,
- stops on `agent.session.turn.completed` / `agent.session.completed`,
- raises on any failure event (`turn.failed`, `turn.cancelled`,
  `session.failed`, `environment.failed`, `error`).

As the agent runs the shell task, each command it executes becomes a
`command_execution` item (the `$` command plus its captured output) — inspect
them in [Lab 02](../lab-02-interpret-results/README.md).

## The command

Finish the [prerequisites](../../00-prerequisites.md) first (Runtime, venv,
session role), then from the module root with the venv active and
`$BMA_RUNTIME_ARN` / `$AWS_PROFILE` set, run the read-only preflight and fix
anything it marks FAILED:

```bash
python preflight.py
```

Then run the lab:

```bash
python labs/lab-01-run-on-runtime/run_on_runtime.py
```

Override the prompt, model, instructions, profile, or role as needed (the
Region comes from the Runtime ARN, so you do not pass `--region`):

```bash
python labs/lab-01-run-on-runtime/run_on_runtime.py \
    --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id \
    --model openai.gpt-5.6-luna \
    --instructions "You are a terse assistant." \
    --aws-profile <your-aws-profile> \
    "Create data.txt with three numbers, then sum them with a shell command."
```

Pass `--help` for the full flag list.

> **Note:** a non-default `--model` only works if the session role's inference
> policy allows that model (see
> [prerequisites](../../00-prerequisites.md#4-create-the-session-role));
> otherwise inference is denied.

## Expected output

The new session id prints to stdout; progress and the final turn event print
to stderr. You will see something like:

```text
Agent config: {
  "model": "openai.gpt-5.6-luna",
  "instructions": "You are a concise assistant. Answer the user's questions directly and accurately."
}
Created session: sess_abc123
--- prompt: "Create a file named hello.txt containing 'hello from the runtime', then list the files in the workspace and show the file's contents."
{
  "type": "agent.session.turn.output_text.done",
  "text": "Done. I created hello.txt, listed the workspace (hello.txt), and its contents are: hello from the runtime"
}

Session kept open: sess_abc123
  * inspect its items:   python labs/lab-02-interpret-results/interpret_results.py --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id --session-id sess_abc123
  * continue the session: python labs/lab-03-continue-session/continue_session.py --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id --session-id sess_abc123
  * delete it when done:  python labs/lab-04-delete-session/delete_session.py --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id sess_abc123
```

Copy the `sess_...` id — the next labs reuse it.

## What to notice

- **The agent executed real commands.** With the Runtime environment the agent
  ran shell commands in a microVM; those become `command_execution` items you
  can read in Lab 02. This is impossible with `environment=none`.
- **`role_arn` rides in `extra_body`**, not as a typed parameter — the BMA
  preview API requires it even though the SDK types do not list it.
- **The Region comes from the Runtime ARN.** A session runs in its Runtime's
  Region, so the script derives the Region from `--runtime` and ignores a
  conflicting `--region`.
- **The session is not deleted.** It stays open so you can keep working with
  it. Clean it up in [Lab 04](../lab-04-delete-session/README.md).

## Cleanup

When you are done, delete the session with
[Lab 04](../lab-04-delete-session/README.md) (reads `$BMA_RUNTIME_ARN`):

```bash
python labs/lab-04-delete-session/delete_session.py sess_abc123
```

Deleting the session does **not** remove the Runtime itself (use the AgentCore
CLI's `agentcore remove` for that).

## Next

Continue to [Lab 02 — Interpret results](../lab-02-interpret-results/README.md)
to read the `command_execution` items the agent produced.
