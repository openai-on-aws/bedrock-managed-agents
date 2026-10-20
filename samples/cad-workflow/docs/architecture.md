# Resumable CAD studies with Bedrock Managed Agents

**Amazon Bedrock Managed Agents (BMA), powered by OpenAI, owns the design-study
loop.** One managed session chooses candidates, reads native CAD evidence and
revises its recommendation after engineer feedback. FreeCAD on EC2 builds and
checks new candidates. A later review can reuse their S3 evidence with EC2
stopped.

[Editable AWS diagram](architecture.drawio) · [Full-resolution PNG](architecture.png) ·
[Scalable SVG](architecture.svg) · [Numbered walkthrough](../README.md#numbered-walkthrough) ·
[Runnable design study](design-study.md)

![BMA plans CAD studies, calls FreeCAD tools and resumes review with EC2 stopped.](architecture.png)

The solid paths represent the implemented FreeCAD/MCP setup; the build-and-review
flow has been live-qualified.
The dashed purple path is an **optional AgentCore Runtime execution workspace**:
workspace commands, persistent files and resumed review have also been
[live-validated](runtime-validation.md). The Runtime requires separate
provisioning. The diagram uses standalone AWS icons, white space and colored
infrastructure boundaries. Solid arrows show logical calls and evidence flow,
not inbound network connections to EC2. The
[README's numbered walkthrough](../README.md#numbered-walkthrough) explains each
stage as text outside the image.

## The numbered flow

The walkthrough is numbered in the README; the diagram has no numbered markers.
The [standalone text copy](architecture-steps.md) contains the same descriptions
for review and reuse in a blog.

Numbers 1–8 cover the brief, BMA planning, authenticated tools, validation,
SSM dispatch, native checks, evidence publication and engineer review.
Number 9 is the optional Runtime workspace; number 10 is the independent
scheduled EC2 stop.

The [study client](../study.py), [study validation](../cad_workflow/study.py),
[broker](../cad_workflow/broker.py), [worker](../cad_workflow/worker.py) and
[FreeCAD verifier](../cad_workflow/backends/freecad/verifier.py) implement this flow.
The simpler [baseline client](../run.py) uses the same CAD tools with fixed input
dimensions.

## What the EC2 rectangle contains

The orange rectangle is **one VM**, managed by a trusted supervisor. Its rows
are job stages, not separate machines or concurrently running agent services.

| Row | Process and responsibility | Output |
| --- | --- | --- |
| a — Build | A fresh FreeCAD process under `cadauthor` builds a constrained sketch/pad/pocket feature tree | Editable `.FCStd`, STEP and preview |
| b — Freeze | The supervisor copies allowlisted files after the author exits and makes the candidate read-only | Fixed files with SHA-256 hashes |
| c — Check | A fresh FreeCAD process under `cadcheck` reopens the frozen files and runs 11 checks | Measurements, check results and input hashes |
| d — Publish | The supervisor verifies the check contract and uploads the available evidence | Versioned artifacts and an accepted or failed receipt |

The author and checker use separate application/configuration directories,
private network and temporary namespaces, a read-only system, and
memory/process/time limits. Only the supervisor has cloud permissions. The
checker constructs its reference solid independently with primitives and boolean
operations, but uses the same FreeCAD/OCCT kernel as the author.

This is a bounded sample with fixed, trusted native programs. It is not a
qualified shared tenant boundary or a sandbox for arbitrary uploaded CAD
files. Native acceptance covers geometry and editable features; structural
strength, manufacturability and engineering release need additional analysis
and a qualified approval process.

## Optional execution workspace (9)

An existing **BMA exec-server AgentCore Runtime** can provide command execution,
planning scripts, reusable filesystem skills and working files such as
`PLAN.md`, `comparison.json` and `STUDY.md`. Persistent session storage can retain
those files across an idle stop when configured as described in the
[design-study guide](design-study.md#add-a-persistent-execution-workspace).

This Runtime executes planning work; native CAD construction and the trusted
checker remain on the separate FreeCAD host. The session role must permit the
chosen model, this CAD Gateway and the selected Runtime. `study.py` accepts that
role and Runtime; it does not provision them or modify IAM.

The Runtime qualification exercised Python planning, saved `PLAN.md`,
`comparison.json` and `STUDY.md`, and resumed review after a pause and compute
replacement. An independent command probe checked file hashes before BMA
continued; the resumed agent then updated all three files. See the
[validation record and limits](runtime-validation.md). BMA's service-side
session context and a Runtime filesystem are separate kinds of state.

## Why EventBridge Scheduler is present (10)

**EventBridge Scheduler implements scheduled EC2 stop for cost control.**
Deployment sets an absolute UTC `StopAtUtc` using `--hours` (1–24).
CloudFormation creates a one-time `at(...)` schedule whose role permits
`ec2:StopInstances` on this worker only.

At the deadline, Scheduler calls the EC2 API independently of the local client,
BMA session and native job. It does not start CAD work, poll job status or wait
for completion. A running job can be interrupted. Bounded retries are configured;
an enabled schedule is not proof that a future stop succeeded.

Stopping EC2 does not delete the BMA session or stop BMA inference. The EBS disk,
S3 evidence and log storage remain provisioned and billable. Stack deletion
terminates EC2 and deletes its root disk, while retaining S3. Restarting after
the deadline requires a new future stop deadline. See
[operation and cleanup](walkthrough.md#operate-and-clean-up).

`study.py` retains the session and worker for explicit lifecycle management.
The baseline `e2e.py` runner instead deletes its test session and confirms its
selected worker is stopped after collecting evidence.

## Where state persists

| State | Location | Effect of stopping the CAD worker |
| --- | --- | --- |
| Study conversation and durable items | BMA session | The same session can continue until explicitly deleted |
| Briefs, round markers, IDs and collected reports | Local `.state/studies/<study-id>/` | Retained on the operator's machine |
| Native files, measurements and version receipts | Private S3 bucket | Available through `get_result` and versioned downloads |
| Worker software and local job files | Encrypted EC2 root EBS volume | Preserved on stop; root disk is deleted on termination |
| Optional planning files | Runtime workspace and configured persistent session storage | Retained independently of EC2; restoration across compute replacement was live-validated |

The client reconciles an ambiguous create or submission using the saved input
marker and hash in BMA's durable items. It does not blindly create a second
session or resubmit the brief. This recovery was exercised in the live study.

## Network and access boundaries

The default deployment has a dedicated VPC, public subnet and Internet Gateway.
EC2 has a public address for outbound HTTPS, but its security group has **no
inbound rules**. Systems Manager delivers logical commands through the worker's
outbound connection. The sample does not expose SSH, a remote desktop or an MCP
listener on EC2. The existing-network option uses a caller-supplied subnet and
Internet Gateway route with the same worker security-group restrictions.

BMA, model inference, AgentCore Gateway, Lambda, Systems Manager, S3 and Scheduler
are regional managed services outside the CAD worker VPC. IAM roles separate
model/tool invocation, broker dispatch, supervisor publication and scheduled
stop. The optional Runtime is also shown outside this worker VPC; a customer
network integration would need its own design.

Candidate allocations are prompt instructions checked by the collector **after
execution**, not tool-enforced cost quotas. For unattended customer operation,
add broker-enforced study leases and candidate quotas, a durable lifecycle
controller, and authenticated engineer review. See the
[study operating limits](design-study.md#operating-limits-and-cleanup).

## Logs and export

| Source | Where it is saved | Export |
| --- | --- | --- |
| BMA study session and durable items | `.state/studies/<study-id>/session.json` and `items.json` | JSON snapshots from `status`, `collect` and recovery |
| Baseline BMA session, events and items | `artifacts/<job-id>/bma-*` | JSON/JSONL saved by `run.py` |
| Native CAD evidence | Private S3 and downloaded candidate directories | Recorded S3 versions with size and SHA-256 verification |
| Lambda and SSM diagnostics | `/cad/<stack>/broker` and `/cad/<stack>/worker` in CloudWatch Logs | Logs API/CLI, or an S3 export task |

CloudWatch Logs retention is 30 days. For example, from the sample directory:

```bash
mkdir -p artifacts/logs
aws logs filter-log-events \
  --profile YOUR_PROFILE --region us-east-1 \
  --log-group-name /cad/bma-cad-freecad/worker \
  --filter-pattern '"JOB_ID_FROM_THE_RUN"' \
  --output json > artifacts/logs/worker-job.json
```

The CLI paginates responses. The caller needs `logs:FilterLogEvents`; use a time
range for large groups. A CloudWatch Logs S3 export task requires its own
permissions and destination bucket policy.

Lambda active X-Ray tracing is configured. The sample does **not** provision an
OpenTelemetry collector or one distributed trace spanning BMA, Gateway, Lambda
and native CAD. BMA item export, CloudWatch log export and OTEL are distinct
mechanisms; additional correlation and an export pipeline are future work.

## Deployment and qualification

The [CloudFormation renderer](../cad_workflow/infrastructure.py) creates the
worker, CAD Gateway and Lambda target, SSM document, private bucket, networking,
roles, log groups and stop schedule. The client creates BMA sessions on demand.
The dashed Runtime is supplied separately. Diagram labels use generic customer
scope and contain no deployment account or resource identifiers.

The code in this sample runs in **`us-east-1`**. The default deployment creates
a dedicated VPC and public subnet, one `t3.medium` worker and a 30 GiB encrypted
gp3 root disk; an existing VPC and compatible subnet can also be supplied.
BMA sessions default to `environment=none`. The
[README component inventory](../README.md#architecture) describes the services
involved.

The [live study reference](../examples/design-study/reference.json) records the
19 October 2026 qualification: three native candidates passed all 11 checks;
BMA first recommended a 13.52% volume reduction, then reused a checked candidate
with a 3 mm floor and 10.85% reduction after review. The second round ran in the
same BMA session with EC2 already stopped and made one `get_result` call with
zero new native jobs. The review brief is simulated engineer feedback for the
demonstration.

The [baseline reference](../examples/support-bracket/reference/verified-example.json)
and [end-to-end runner](../e2e.py) additionally cover the native grader audit,
log export and confirmed worker stop. Passing unit tests or a diagram alone
does not establish a qualified CAD run. CATIA V6 remains a future backend;
[its integration contract](adding-a-backend.md) is not an implemented service.

## Updating the diagram

Edit `docs/architecture.drawio`, then regenerate both published images from the
sample directory with draw.io Desktop's CLI:

```bash
drawio --export --format png --embed-diagram --scale 1.5 --border 20 \
  --output docs/architecture.png docs/architecture.drawio
drawio --export --format svg --embed-diagram --border 20 \
  --output docs/architecture.svg docs/architecture.drawio
```

Keep this numbered explanation, the diagram and the machine manifest in sync.
The editable source remains checked in alongside the PNG and scalable SVG.
