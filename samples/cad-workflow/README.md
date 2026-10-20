---
sample_id: cad-workflow-freecad
machine_manifest: sample.json
example: examples/support-bracket.json
example_schema: schemas/example.schema.json
run_report_schema: schemas/e2e-report.schema.json
reference_manifest: examples/support-bracket/reference/verified-example.json
---

# CAD workflows with Bedrock Managed Agents

Use **Amazon Bedrock Managed Agents (BMA), powered by OpenAI**, with FreeCAD
on AWS to create editable parts and check their geometry. A separate native
checker verifies the saved CAD files before they can be accepted.

Start with the verified support-bracket example below, or run the
[resumable design study](docs/design-study.md). In the study, BMA chooses
candidates, interprets measurements, reuses verified designs, and revises
its recommendation after engineer feedback in the same session.

![BMA plans CAD studies, calls FreeCAD tools and resumes review with EC2 stopped.](docs/architecture.png)

[Architecture notes](docs/architecture.md) · [Numbered walkthrough](docs/architecture-steps.md) ·
[Editable diagram](docs/architecture.drawio) · [Scalable SVG](docs/architecture.svg)

## Run the verified example

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), and an authenticated AWS
profile with BMA/model access in `us-east-1` and the
[deployment permissions](docs/walkthrough.md#prerequisites).
FreeCAD runs on EC2; you do not need to install it locally.

From the repository root:

```bash
cd samples/cad-workflow
uv sync --frozen
uv run --frozen python install_guard.py
uv run --frozen python deploy.py validate

uv run --frozen python e2e.py --deploy \
  --profile YOUR_PROFILE --region us-east-1 \
  --account YOUR_12_DIGIT_ACCOUNT --stack bma-cad-freecad --hours 4
```

Use a new stack name. This creates billable AWS resources, invokes BMA,
checks the result, audits the grader, exports logs, deletes the test session,
and stops the worker. EBS, S3 and log storage remain provisioned. For an
existing subnet or worker, see the [walkthrough](docs/walkthrough.md#run-the-complete-example).

The example is a **160 × 42 × 14 mm bracket** with three mounting bores, a
12 mm blind pocket and a 2 mm floor. A live AWS run on 19 October 2026 passed
**all 11 CAD checks**; the grader also rejected four deliberately defective
parts. Success is exit code `0` and `status: passed` in the saved report.

[FreeCAD model](examples/support-bracket/reference/support.FCStd) ·
[STEP file](examples/support-bracket/reference/support.step) ·
[Preview](examples/support-bracket/reference/preview.png) ·
[Verified result](examples/support-bracket/reference/verified-example.json)

Verify those checked-in files without AWS calls:

```bash
uv run --frozen python evidence.py verify-reference
```

## What BMA does

BMA runs the managed Codex harness: it keeps the conversation and performs
reasoning and tool calls. The baseline uses `environment=none`; two MCP tools
submit fixed dimensions and retrieve the result. FreeCAD builds the part,
and the independent checker decides whether its geometry passes.

This is a useful integration test, but a deterministic script could submit
those same dimensions. The [richer workflow](docs/design-study.md) gives BMA
an engineering brief, candidate results and review feedback. An AgentCore
Runtime workspace additionally lets the harness use scripts, skills and
working files throughout the study.

The [live two-round study](docs/design-study.md#observed-aws-result) achieved
13.52% volume reduction, then reused a 10.85% lighter design after a stricter
floor requirement. BMA completed that review with EC2 stopped. The optional
Runtime workspace path is implemented but has not been live-tested.

## Guides and automation

| Need | Read |
| --- | --- |
| Detailed setup, commands, results and troubleshooting | [Walkthrough](docs/walkthrough.md) |
| A richer, resumable BMA workflow | [Design study](docs/design-study.md) |
| AWS components and the numbered flow | [Architecture](docs/architecture.md) |
| Geometric checks and grader audit | [Verification](docs/verification.md) |
| Machine-readable commands and contracts | [sample.json](sample.json) |
| Stop compute or remove the stack | [Cleanup](docs/walkthrough.md#operate-and-clean-up) |
| Add a future CAD backend | [Backend interface](docs/adding-a-backend.md) |

**Scope:** FreeCAD is implemented; CATIA V6 is an extension path. The current
part family is bounded. Geometric acceptance does not establish structural
strength, manufacturability or engineering approval. BMA is in public preview.
