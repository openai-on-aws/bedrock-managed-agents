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

## Architecture

The code in this sample runs in **`us-east-1`**. By default, CloudFormation
creates a dedicated VPC, public subnet, CAD tools and worker. You can also
use an existing VPC and compatible subnet. The local client creates BMA
sessions on demand.

![BMA plans CAD studies, calls FreeCAD tools and resumes review with EC2 stopped.](docs/architecture.png)

### Numbered walkthrough

Steps 1–8 describe the study flow. Step 9 is optional; step 10 operates independently.

1. **Submit the engineering brief.** The engineer uses the local Python client to
   start a design study or submit revised requirements. The client saves the
   brief, session ID and round markers so work can be collected or resumed later.

2. **Let BMA plan the study.** Amazon Bedrock Managed Agents runs the managed
   Codex harness with an OpenAI model on Amazon Bedrock. It chooses candidate
   dimensions, compares measured results and retains context across review
   rounds. The default model is GPT-6.1 Sol.

3. **Invoke authenticated CAD tools.** BMA uses its session IAM role to access
   Amazon Bedrock AgentCore Gateway. The MCP interface exposes two tools:
   `create_support` to request a candidate and `get_result` to inspect its outcome.

4. **Validate and reserve the job.** The Lambda broker checks the requested
   dimensions against the supported part family and reserves an immutable job
   ID in S3. The agent supplies design parameters; the deployed broker fixes the
   worker, command document and storage destination.

5. **Dispatch the native CAD command.** Lambda submits the fixed Systems Manager
   Run Command document to the EC2 worker. The worker receives it through an
   outbound connection, with no inbound SSH, desktop or MCP port exposed.

6. **Build, freeze and check the candidate.** On one EC2 instance, a trusted
   supervisor runs the FreeCAD author, freezes its output files, and starts a
   separate FreeCAD checker. The checker runs 11 checks on the frozen candidate.
   Rows a–c in the EC2 rectangle show these sequential stages.

7. **Store the verdict and evidence.** The supervisor publishes the native CAD
   files, check report and accepted or failed verdict to private, encrypted,
   versioned S3. Recorded versions and SHA-256 hashes bind the evidence to the
   files that were checked. This is row d in the EC2 rectangle.

8. **Compare results and return for review.** BMA retrieves the receipts and
   recommends a candidate against the latest brief. The client verifies the
   downloaded evidence and produces JSON and Markdown reports for the engineer.
   A later round can reuse an existing checked design while EC2 is stopped.
   The result remains `awaiting_review` for the engineer's decision.

9. **Optionally add a persistent execution workspace.** An existing BMA
   exec-server on AgentCore Runtime can provide planning scripts, reusable skills
   and working files. Persistent session storage supports retaining those files
   when configured. This integration requires separate provisioning. Workspace
   commands, file persistence across compute replacement and resumed review
   have been live-validated.

10. **Stop EC2 at the scheduled deadline.** EventBridge Scheduler independently
    calls `StopInstances` at a configured UTC time for cost control. It can
    interrupt a running CAD job. Stopping EC2 preserves the BMA session, EBS disk
    and S3 evidence; retained storage remains billable.

### Components

| Component | What it does |
| --- | --- |
| **Bedrock Managed Agents + Amazon Bedrock** | BMA runs the managed Codex harness, chooses candidate dimensions and retains the conversation across review rounds. The default model is GPT-6.1 Sol with `environment=none`. |
| **AgentCore Gateway + AWS Lambda** | Gateway exposes the IAM-authenticated MCP tools `create_support` and `get_result`. The Lambda broker validates inputs, reserves job IDs and dispatches CAD work. |
| **Amazon EC2 + EBS** | One `t3.medium` worker with a 30 GiB encrypted gp3 root disk runs four sequential stages: build in FreeCAD, freeze the files, run a separate checker, then publish evidence. |
| **AWS Systems Manager** | A fixed Run Command document starts the native job through the worker's outbound connection. |
| **Amazon S3** | A private, encrypted, versioned bucket stores editable `.FCStd` files, STEP exports, previews, check reports and receipts. BMA can retrieve completed results while EC2 is stopped. |
| **VPC networking + IAM** | The worker has outbound HTTPS through an Internet Gateway and no inbound security-group rules. Five IAM roles separate BMA, Gateway, broker, worker and scheduled-stop permissions. |
| **Amazon CloudWatch + AWS X-Ray** | Two log groups capture broker and worker diagnostics with 30-day retention. Lambda active X-Ray tracing is enabled. |
| **Amazon EventBridge Scheduler** | A one-time UTC deadline stops this EC2 instance for cost control. It operates independently of BMA and can interrupt a running CAD job. |

The dashed **AgentCore Runtime workspace** is an optional extension that
requires separate provisioning. [Live validation](docs/runtime-validation.md)
confirmed Python execution, saved workspace files across a pause and compute
replacement, and continued review in the same BMA session with EC2 stopped.

[Architecture notes](docs/architecture.md) · [Numbered walkthrough](#numbered-walkthrough) ·
[Editable diagram](docs/architecture.drawio) · [Scalable SVG](docs/architecture.svg)

## Run the verified example

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), and an authenticated AWS
profile with BMA/model access in `us-east-1` and the
[deployment permissions](docs/walkthrough.md#prerequisites).
FreeCAD runs on EC2; you do not need to install it locally.

From the repository root:

```bash
cd samples/cad-workflow
uv sync
uv run python install_guard.py
uv run python deploy.py validate

uv run python e2e.py --deploy \
  --profile YOUR_PROFILE --region us-east-1 \
  --account YOUR_12_DIGIT_ACCOUNT --stack bma-cad-freecad --hours 4
```

`uv` uses the checked-in `uv.lock`. The optional `--frozen` flag prevents
lockfile updates; CI and the machine-readable commands include it for
repeatable dependency installation.

Use a new stack name. This creates billable AWS resources, invokes BMA,
checks the result, audits the grader, exports logs, deletes the test session,
and stops the worker. EBS, S3 and log storage remain provisioned. For an
existing subnet or worker, see the [walkthrough](docs/walkthrough.md#run-the-complete-example).

The example is a **160 × 42 × 14 mm bracket** with three mounting bores, a
12 mm blind pocket and a 2 mm floor. The runner requires **all 11 CAD checks**
to pass and audits the grader with a valid control and four deliberately
defective parts. Success is exit code `0` and `status: passed` in the saved report.

[FreeCAD model](examples/support-bracket/reference/support.FCStd) ·
[STEP file](examples/support-bracket/reference/support.step) ·
[Preview](examples/support-bracket/reference/preview.png) ·
[Verified result](examples/support-bracket/reference/verified-example.json)

Verify those checked-in files without AWS calls:

```bash
uv run python evidence.py verify-reference
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

In the [two-round study](docs/design-study.md), BMA compares candidate volumes,
then revises its recommendation after a stricter floor requirement. A review
can reuse a verified design while EC2 is stopped. The checked-in
[study reference](examples/design-study/reference.json) illustrates the results.

## Cleanup

A successful `e2e.py` run deletes its test BMA session and confirms that EC2
has stopped. It leaves the stack and storage provisioned. `study.py` retains
its session and worker so you can continue the study.

Run these commands from `samples/cad-workflow`, using the same AWS profile,
account and stack as your deployment.

**Pause CAD compute:** stop the worker while preserving its disk and artifacts.
This stops EC2 only; BMA can continue, and EBS, S3 and log storage remain billable.

```bash
uv run python cleanup.py stop \
  --profile YOUR_PROFILE --region us-east-1 \
  --account YOUR_12_DIGIT_ACCOUNT --stack bma-cad-freecad
```

**Finish a study:** collect the results you need and preserve `.state/` and
`artifacts/` before deleting its BMA session. If a turn is active, first
[cancel it and wait for idle](docs/design-study.md#operating-limits-and-cleanup).
Session deletion prevents further continuation.

```bash
uv run python study.py delete-session \
  --study-id YOUR_STUDY_ID \
  --profile YOUR_PROFILE --region us-east-1 \
  --account YOUR_12_DIGIT_ACCOUNT --stack bma-cad-freecad
```

For a standalone `run.py` job, use the
[recorded-job session cleanup](docs/walkthrough.md#operate-and-clean-up) instead.
Repeat session cleanup for each retained session you no longer need.

**Remove the deployment:** export needed files and CloudWatch logs, then
delete the sample's stack. This terminates EC2 and deletes its root disk,
log groups and other stack-managed resources.

```bash
uv run python cleanup.py delete-stack \
  --profile YOUR_PROFILE --region us-east-1 \
  --account YOUR_12_DIGIT_ACCOUNT --stack bma-cad-freecad
```

The S3 bucket and all object versions are **retained and remain billable**.
Its name is saved in `.state/<stack>/before-delete.json`; remove the bucket,
including object versions and delete markers, separately when your retention
policy permits. Stack deletion also preserves BMA sessions, any supplied
existing VPC/subnet and any separately provisioned optional Runtime.

Stop and stack-delete commands return before AWS finishes. Verify completion
in EC2 or CloudFormation. See the [cleanup guide](docs/walkthrough.md#operate-and-clean-up)
for details, including setting a new stop deadline before restarting EC2.

## Guides and automation

| Need | Read |
| --- | --- |
| Detailed setup, commands, results and troubleshooting | [Walkthrough](docs/walkthrough.md) |
| A richer, resumable BMA workflow | [Design study](docs/design-study.md) |
| Live workspace persistence and resumed review | [Runtime validation](docs/runtime-validation.md) |
| AWS components and the numbered flow | [Architecture](docs/architecture.md) |
| Geometric checks and grader audit | [Verification](docs/verification.md) |
| Machine-readable commands and contracts | [sample.json](sample.json) |
| Stop compute, close sessions or remove the stack | [Cleanup](#cleanup) |
| Add a future CAD backend | [Backend interface](docs/adding-a-backend.md) |

**Scope:** FreeCAD is implemented; CATIA V6 is an extension path. The current
part family is bounded. Geometric acceptance does not establish structural
strength, manufacturability or engineering approval. BMA is in public preview.
