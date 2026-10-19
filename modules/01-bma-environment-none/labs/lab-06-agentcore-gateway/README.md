# Lab 06 — Gate the shared to-do list with an AgentCore Gateway tool



**What you learn:** how to give a BMA agent a remote tool by attaching an
**Amazon Bedrock AgentCore Gateway** as an MCP tool at session creation, and
how to make that tool actually *decide* something in your workflow.

## The idea

Lab 03 built a shared to-do list that lives in the session's server-side
history. This lab adds a gate: **before the agent adds an item, it calls a
Gateway tool, `GetRandomBool`, and only adds the item if the tool returns
`true`.** On `false`, the item is rejected and the list is unchanged.

So the remote tool call is not decoration — it decides whether the turn
mutates the shared list. Everything else from Lab 03 still holds: teammates
join the same session id from their own terminals, and the list persists
server-side.

`GetRandomBool` returns a random boolean, so some adds succeed and some are
rejected — which makes the gate easy to see across a few turns.

## Prerequisite: a Gateway that exposes `GetRandomBool`

This lab does **not** create AWS Lambda functions or AgentCore Gateways —
that is outside its scope. But the tool behind this lab is tiny, so here is
a brief copy-paste setup. Do this once in your AWS account, then bring
back the AgentCore Gateway's ARN.

For a complete deployment with Lambda tools, Gateway, a native worker and
verification, see the [FreeCAD sample](../../../../samples/cad-workflow/README.md).

**1. Create a Lambda function** that returns a random boolean:

```python
import random

def lambda_handler(event, context):
    return {"result": random.choice([True, False])}
```

Note its ARN after creating it.

**2. Create an AgentCore Gateway** with these settings:

- Authorization / inbound auth: **use IAM permissions**.
- Target type: **MCP**, backed by a **Lambda** target.
- Target ARN: the **Lambda ARN** from step 1.
- Supply an **inline tool schema** so the Gateway knows how to present the
  Lambda as an MCP tool:

  ```json
  [
    {
      "name": "GetRandomBool",
      "description": "Randomly returns true or false with a 50/50 probability. Takes no input arguments.",
      "inputSchema": {
        "type": "object",
        "properties": {},
        "required": []
      },
      "outputSchema": {
        "type": "object",
        "properties": {
          "result": {
            "type": "boolean",
            "description": "The randomly generated boolean value, either true or false."
          }
        },
        "required": ["result"]
      }
    }
  ]
  ```

For the full, supported walkthrough of creating a Gateway, Lambda target,
and schema, see the AgentCore docs:

- [Amazon Bedrock AgentCore Gateway — overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Amazon Bedrock AgentCore documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)

Throughout this lab the Gateway ARN is written as the placeholder
`arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/my-gateway-id` —
substitute your own.

## Concept: attaching the Gateway

You attach a Gateway to a BMA session through the agent's `tools` list:

```python
agent["tools"] = [
    {
        "type": "mcp",
        "server_label": "gateway_tools",
        "connector_id": "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/my-gateway-id",
    }
]
```

The Gateway is referenced **by its ARN, through `connector_id`** 

**Tools are fixed at session creation.** You cannot add or change tools by
submitting more turns, so `--gateway-arn` is only used when creating a new
list. Teammates who join with `--session-id` inherit the same tool
automatically.

## Two hard requirements

Both live on the AWS side, not in this script. If either is wrong, the tool
config still *accepts* and the session still *creates* — the call only fails
later, on the `mcp_call` item, which is why the debugging section matters.

1. **IAM: the session role needs `bedrock-agentcore:InvokeGateway`.**
   BMA calls the Gateway as the session role (default
   `BedrockManagedAgentsPreviewInferenceServiceRole`, from
   [`00-prerequisites.md`](../../00-prerequisites.md) /
   `scripts/create_session_role.py`). That role's identity policy must allow
   `bedrock-agentcore:InvokeGateway` on the Gateway ARN. Add it with
   least-privilege (scope the `Resource` to the specific Gateway ARN, not
   `*`), and verify before using beyond a sandbox:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": "bedrock-agentcore:InvokeGateway",
         "Resource": "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/my-gateway-id"
       }
     ]
   }
   ```

2. **MCP protocol version: the Gateway must advertise `2025-03-26`.**
   BMA's MCP client requests protocol version `2025-03-26`. The Gateway's
   `protocolConfiguration.mcp.supportedVersions` **must include**
   `2025-03-26` (alongside any others it supports). If it does not, the
   Gateway rejects the connection with `Unsupported protocol version:
   2025-03-26`, which BMA relays as a generic
   `-32603: tools/list failed at the AgentCore gateway` on the `mcp_call`
   item — not in the chat reply. 

## What you'll run

`gateway_session.py` is Lab 03's shared-list script with the Gateway tool
attached and gate-aware instructions. Two modes:

- **no `--session-id`** — creates a new gated list and attaches the Gateway;
  `--gateway-arn` is **required** here.
- **with `--session-id`** — joins that existing gated list and tries to add
  another item; it inherits the already-attached tool, so no `--gateway-arn`.

Use `--user <name>` to attribute an item to a person (optional).

## The command

Before your first run, complete the one-time
[prerequisites](../../00-prerequisites.md) (create the venv and configure your
AWS profile) and the Gateway setup above. Then, in each new terminal, from the
module root (`modules/01-bma-environment-none`), activate the venv and make
sure your AWS profile is set (`export AWS_PROFILE=<your-aws-profile>` or pass
`--aws-profile`):

```bash
# Set your Gateway ARN once, then reuse it below.
export GATEWAY_ARN=arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/my-gateway-id

# person A — create the gated shared list (prints the id)
python labs/lab-06-agentcore-gateway/gateway_session.py \
    --gateway-arn "$GATEWAY_ARN" \
    --user Alice "buy milk"

# person B — join the SAME list from another terminal (no --gateway-arn)
python labs/lab-06-agentcore-gateway/gateway_session.py \
    --session-id sess_abc123 --user Bob "book flights"

# try a few more — some adds pass the gate, some are rejected
python labs/lab-06-agentcore-gateway/gateway_session.py \
    --session-id sess_abc123 --user Alice "water the plants"
```

Pass `--help` for the full flag list.

## Expected output

The gate makes the result vary turn to turn. A `true` adds the item; a
`false` rejects it and the list is unchanged:

```text
# person A creates the gated list; the gate returns true, so the item is added
Agent config: {
  "model": "openai.gpt-5.6-luna",
  "instructions": "You maintain a single shared to-do list ...",
  "tools": [
    {
      "type": "mcp",
      "server_label": "gateway_tools",
      "connector_id": "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/my-gateway-id"
    }
  ]
}
Created gated shared list: sess_abc123
--- trying to add: 'Alice: buy milk'
{"type": "agent.session.turn.output_text.done", "text": "Gate returned true — added. Current list:\n1. buy milk (Alice)"}

# person B's add is rejected by the gate; the list keeps Alice's item unchanged
Joining shared list: sess_abc123
--- trying to add: 'Bob: book flights'
{"type": "agent.session.turn.output_text.done", "text": "Gate returned false — 'book flights' was rejected this time. Current list:\n1. buy milk (Alice)"}
```

(The exact wording varies with the model; the behavior — a tool call gating
each add, over a list that persists across terminals — is the point.)

## Debugging a Gateway call

A failing Gateway call usually does **not** fail the turn — the agent still
replies, often by saying the tool did not work. The real error is on the
`mcp_call` item, so inspect the session's items with Lab 02 in JSON mode:

```bash
python labs/lab-02-interpret-results/interpret_results.py --json --session-id sess_abc123
```

For each `mcp_call` item check:

- **`server_label`** — which MCP server was hit (here, `gateway_tools`).
- **`name`** — which tool the agent called (here, `GetRandomBool`).
- **`error`** — `null` on success, or the failure detail. A generic
  `-32603: tools/list failed at the AgentCore gateway` here almost always
  means requirement 2 (protocol version) or requirement 1 (IAM).

When the client-side error is too generic, read the Gateway's own
server-side logs in CloudWatch Logs:

```text
/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/my-gateway-id
```

(Enable tracing/logging on the Gateway if that log group does not exist
yet.) The server-side message is more specific — e.g. the explicit
`Unsupported protocol version: 2025-03-26` that points straight at
requirement 2.

Suggested order when a call fails: confirm the tool config echoes back on
the session resource (`agent.tools[0]`), then read the `mcp_call` item's
`error` via the command above, then read the Gateway's CloudWatch
application log for the real server-side cause.

## What to notice

- **The tool decides the outcome.** Each add is gated on `GetRandomBool`;
  the server-side list only changes when the tool returns `true`.
- **This gate is a demonstration, not an authorization control.** The "only
  add when the tool returns true" rule lives entirely in the agent's
  natural-language instructions — nothing in code enforces it. A prompt
  injection or a model mistake can bypass it, and the agent could add an item
  without the tool returning `true`. That is acceptable here because the only
  effect is this sample's session state. **For any real side effect, enforce
  authorization deterministically in your own trusted code** (check the tool
  result and perform the action yourself), and treat model-selected tools and
  model output as untrusted input — never as an authorization decision.
- **Service-side IAM, no secrets in the client.** BMA calls the Gateway as
  the session role — you never pass a token or URL from here.
- **Fixed at creation.** The tool is attached when the list is created;
  teammates joining with `--session-id` inherit it.
- **Failures hide on the item, not the reply.** Always confirm with
  `interpret_results.py --json` before trusting a gated result.

## Cleanup

Delete the session when you are done, as in
[Lab 05](../lab-05-delete-session/README.md):

```bash
python labs/lab-05-delete-session/delete_session.py sess_abc123
```

Deleting the BMA session does not touch your Gateway, Lambda, or any
customer-owned resources behind it — those are yours to manage (and delete)
separately.

## Next

That is the end of Module 1. Head back to the
[module overview](../../README.md) for a recap and where to go next.
