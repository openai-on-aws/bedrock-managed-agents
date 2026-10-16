# Lab 02 — Interpret results

**What you learn:** how to list a session's items and read the tagged-union
result shape — with the spotlight on `command_execution`, the item type a
Runtime session produces.

## Concept

Streaming a turn (Lab 01) shows the live reply, but a session also keeps a
durable, ordered log of everything that happened. Each entry is an **item**,
and listing items is how you inspect a session after the fact — and the best
way to debug what an agent actually did.

Items are a **tagged union**: every item has a `type` and an `id`, but the
rest of its fields depend on the type.

| `type` | Key fields | Meaning |
|---|---|---|
| `command_execution` | `command`, `output` | A command the agent **ran** in the Runtime microVM — the `$` command plus its captured output. **This is the item Module 2 produces** and `environment=none` could not. |
| `message` (role `user`) | `role`, `content[]` | A prompt you submitted. |
| `message` (role `assistant`) | `role`, `content[]` | The agent's reply. Each `content` part has its own `text` / `type`. |
| `reasoning` | summary content | The model's internal reasoning for the turn. |
| `mcp_call` | `server_label`, `name`, `arguments`, `error` | A remote MCP tool call. `error` is `null` on success. |

Because the fields vary, read each item by its `type`. For raw inspection,
use `model_dump(mode="json")` (the `--json` mode here) — it avoids noisy
pydantic warnings on this preview API's union-typed fields, and it is the best
way to read the full `command` and `output` of each `command_execution`.

## What you'll run

`interpret_results.py` either reuses a `--session-id` you pass or creates a
throwaway Runtime session (which requires `--runtime`), runs the neutral shell
task once, and then lists. It reads **one page** (`page.data`) with
`items.list(session_id, limit=, order=)` and renders a table, or raw JSON with
`--json`.


## The command

From the module root with the venv active and `$BMA_RUNTIME_ARN` / `$AWS_PROFILE`
set (see [prerequisites](../../00-prerequisites.md)):

```bash
# inspect the session you created in Lab 01
python labs/lab-02-interpret-results/interpret_results.py --session-id sess_abc123

# or let it create a throwaway Runtime session, run the task once, then list
# (reads $BMA_RUNTIME_ARN; or pass --runtime <arn>)
python labs/lab-02-interpret-results/interpret_results.py

# raw JSON (full field detail per item — read command + output here)
python labs/lab-02-interpret-results/interpret_results.py --session-id sess_abc123 --json
```

## Expected output

Table mode renders one row per item, oldest first. Note the
`command_execution` rows — the agent actually ran these:

```text
ITEM_ID       TYPE               STATUS      PREVIEW
---------------------------------------------------------------------------
item_000001   message:user                   Create a file named hello.txt containing 'hello from the runtime'…
item_000002   reasoning          completed    Writing the file, then listing and reading it back.
item_000003   command_execution  completed    $ echo 'hello from the runtime' > hello.txt
item_000004   command_execution  completed    $ ls  -> hello.txt
item_000005   command_execution  completed    $ cat hello.txt  -> hello from the runtime
item_000006   message:assistant  completed    Done. hello.txt contains: hello from the runtime

6 item(s). Use --json for full details.
```

`--json` prints the full items array, including each `command_execution`'s
`command` and `output`:

```json
[
  {
    "id": "item_000005",
    "type": "command_execution",
    "status": "completed",
    "command": "cat hello.txt",
    "output": "hello from the runtime\n"
  }
]
```

## What to notice

- **`command_execution` is the new item type.** It appears only because the
  agent runs on a Runtime; its `command` and `output` are the record of what
  the agent actually did in the microVM.
- The `TYPE` column shows `message:user` vs `message:assistant` by joining
  `type` and `role` — bare `message` would hide who said what.
- `--order asc` reads the conversation top to bottom; `--json` is the go-to
  for debugging (inspect each `command_execution`'s full `output`, or an
  `mcp_call`'s `error`).
- **`--json` prints everything, including command output** that may contain
  secrets or personal data. Don't redirect it to a shared log or commit it
  (see [Handling output](../../00-prerequisites.md)).

## Cleanup

If you created a throwaway session here, delete it with
[Lab 04](../lab-04-delete-session/README.md).

## Next

Continue to [Lab 03 — Continue session](../lab-03-continue-session/README.md)
to submit a follow-up turn and see the microVM workspace persist within the
session.
