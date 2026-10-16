# Lab 04 — Delete session

**What you learn:** how to delete sessions, why cleanup is on you, and what
deleting a session does **not** remove (the Runtime and the session role stay).

## Concept

Every session you create in the earlier labs stays around until you remove it.
BMA does **not** auto-delete idle sessions, so cleaning up is an explicit step.
Deleting is a single call:

```python
client.beta.agents.sessions.delete(session_id)
```

A few things worth knowing — especially on a Runtime:

- **Deleting a session removes the session and its conversation history.** It
  does **not** delete any customer-owned files or resources the agent touched.
- **It does not delete the AgentCore Runtime.** The Runtime is a separate
  resource managed with the AgentCore CLI; remove it with `agentcore remove`
  when you no longer need it (and remember it may incur AWS charges while it
  exists).
- **It does not delete `BmaSessionRole-<region>`.** The IAM session role lives
  in your account and is reused by every session; remove it yourself if you
  are done with the module.

## What you'll run

`delete_session.py` takes one or more session ids and deletes each. It reports
success or failure **per id** and keeps going if one fails (already gone, wrong
id, missing permissions), so a single bad id does not stop the rest. The
process exits non-zero if any delete failed.

## The command

From the module root with the venv active and `$BMA_RUNTIME_ARN` / `$AWS_PROFILE`
set (see [prerequisites](../../00-prerequisites.md)):

```bash
# delete one session
python labs/lab-04-delete-session/delete_session.py sess_abc123

# delete several at once
python labs/lab-04-delete-session/delete_session.py sess_abc123 sess_def456

# match how the session was created — pass the same Runtime (or --region)
python labs/lab-04-delete-session/delete_session.py \
    --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id \
    sess_abc123
```

> **Delete in the same Region the session was created in.** Sessions are
> region-scoped, so pass the same `--runtime` (its ARN carries the Region) or
> `--region`, and the same profile, used to create them. Deleting with the
> wrong Region returns a `404 no managed agent session found` even though the
> session exists elsewhere.

## Expected output

```text
Deleted session: sess_abc123
Deleted session: sess_def456
```

If one id cannot be deleted, the others still succeed and the run reports the
failure to stderr, exiting non-zero:

```text
Deleted session: sess_abc123
Failed to delete session sess_def456: <error detail>
```

## What to notice

- **Cleanup is manual.** Nothing deletes sessions for you; make it a habit to
  delete the ones you create while learning.
- **Per-id, best-effort.** One failing id does not block the others, and the
  non-zero exit lets scripts detect that something failed.
- **The Runtime and session role outlive the session.** Deleting a session is
  irreversible — its history is gone — but the AgentCore Runtime, the
  `BmaSessionRole-<region>` role, and your own files are left untouched.

## Cleanup

This lab *is* the session cleanup step. When you are also finished with the
module, remove the AgentCore Runtime with the AgentCore CLI (`agentcore
remove`) and delete the `BmaSessionRole-<region>` role if you no longer need
it.

## Next

You have finished Module 2's core labs. See the
[module overview](../../README.md) for the lab index and a recap of what you built.
