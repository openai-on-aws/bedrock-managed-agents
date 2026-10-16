# Module 2 tests

Fast, offline regression tests for the security-sensitive helpers in this
module. They use only the Python standard library (`unittest`) — no AWS
credentials, no network, and **no third-party packages** — so they run in a
bare Python interpreter even without this module's dependencies installed.
(The production scripts import `boto3`/`openai` at the top level; the test
file registers lightweight stubs for those only when they are not already
installed, so the command below works either way.)

```bash
cd modules/02-bma-agentcore-runtime
python -m unittest discover -s tests -v
```

What they lock down:

- **Runtime ARN validator** (`scripts/create_session_role.py`) — rejects
  wildcards, wrong-service and malformed ARNs before they can reach an IAM
  policy's `Resource`.
- **IAM scope matcher** (`preflight.py`) — an Allow must be scoped to *your*
  exact Runtime (so `runtime/rt-1` never passes for `runtime/rt-10`), and a
  `Deny` wins over an `Allow`.
- **Profile-bound credential provider** (`bma_common.py`) — the bearer token
  is minted under the AWS profile you selected, not the ambient default chain.
