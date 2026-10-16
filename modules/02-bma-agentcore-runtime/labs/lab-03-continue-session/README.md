# Lab 03 — Continue session

**What you learn:** how to submit a follow-up turn to an existing session, and
that the Runtime **workspace persists within the session** — the microVM's
files survive from one turn to the next.

## Concept

Lab 01 created a session on a Runtime and had the agent write `hello.txt`. A
session keeps its full history server-side, so a follow-up turn sees the whole
conversation. On a Runtime there is more: the follow-up turn reconnects to the
**same microVM workspace**, so files the agent wrote earlier are still there.

This lab proves it by asking the agent to **append a line to `hello.txt` and
show it**. If the appended line is there, the workspace carried over between
turns — not just the chat history.

A follow-up turn is submitted through the **events** endpoint:

```python
client.beta.agents.sessions.events.create(
    session_id,
    events=[{
        "type": "agent.session.input.message",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}],
    }],
)
```

…then you drain the turn's events exactly like Lab 01.

> **The workspace is ephemeral across an idle-stop.** Within a live session the
> workspace persists (what this lab shows). But if the Runtime is stopped on an
> idle timeout and later resumed, files from an earlier turn are not guaranteed
> to survive unless **persistent session storage** was configured when the
> Runtime was provisioned.

## What you'll run

`continue_session.py` takes a required `--session-id` (the session from Lab 01)
and a required `--runtime` (the SAME Runtime ARN, so the Region is
unambiguous), submits one follow-up turn via the events endpoint, and drains
it. The default prompt is `append a line to hello.txt and show it`; pass your
own as a positional argument.

## The command

From the module root with the venv active and `$BMA_RUNTIME_ARN` / `$AWS_PROFILE`
set (see [prerequisites](../../00-prerequisites.md)):

```bash
# continue the session from Lab 01 (default prompt touches hello.txt)
# reads $BMA_RUNTIME_ARN (or pass --runtime <arn>)
python labs/lab-03-continue-session/continue_session.py --session-id sess_abc123

# or submit your own follow-up prompt
python labs/lab-03-continue-session/continue_session.py \
    --session-id sess_abc123 \
    "count the lines in hello.txt"
```

Pass `--help` for the full flag list.

## Expected output

```text
Continuing session: sess_abc123
--- prompt: 'append a line to hello.txt and show it'
{
  "type": "agent.session.turn.output_text.done",
  "text": "Appended a line. hello.txt now reads:\nhello from the runtime\nappended on this turn"
}

Session kept open: sess_abc123
  * delete it when done: python labs/lab-04-delete-session/delete_session.py --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id sess_abc123
```

The reply shows the original line AND the appended one — evidence the file the
agent wrote in Lab 01 was still in the workspace this turn. Inspect the new
`command_execution` items with
[Lab 02](../lab-02-interpret-results/README.md) if you want the exact commands.

## What to notice

- **The workspace persisted within the session.** The agent appended to the
  file it created in an earlier turn, in the same microVM — not a fresh one.
- **The follow-up turn goes through the events endpoint**, not another
  `sessions.create()`. There is one session; you add turns to it.
- **Pass the same `--runtime`** the session was created on. The Region is
  derived from it; a mismatch returns a `404 no managed agent session found`.

## Cleanup

Delete the session when you are done with
[Lab 04](../lab-04-delete-session/README.md):

```bash
python labs/lab-04-delete-session/delete_session.py \
    --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id \
    sess_abc123
```

## Next

Continue to [Lab 04 — Delete session](../lab-04-delete-session/README.md) to
clean up the sessions you created.
