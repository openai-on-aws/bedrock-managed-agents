# From an engineering request to checked CAD with BMA

**Amazon Bedrock Managed Agents (BMA)** runs the managed reasoning and
tool-calling loop. A native CAD worker on Amazon EC2 builds a support part,
checks the frozen files, and publishes a verdict with evidence. The current
backend is FreeCAD; the tools accept bounded dimensions for one part family.

[Editable AWS diagram](architecture.drawio) · [Full-resolution PNG](architecture.png)

![BMA orchestrates eight numbered steps to build, freeze, verify and deliver native CAD on AWS.](architecture.png)

*The orange EC2 rectangle represents one VM. Its four rows are sequential job
stages. EventBridge Scheduler provides a separate stop deadline for compute.*

## The eight steps

1. **Submit the engineering task to BMA.** The Python client creates a managed
   session with the job ID, dimensions, model configuration and tool
   instructions. `environment=none` means the session uses remote tools for
   native CAD execution.
2. **Invoke an authenticated CAD tool.** BMA uses its session IAM role to call
   AgentCore Gateway. The MCP connector exposes `create_support` and
   `get_result`.
3. **Validate and dispatch.** Lambda validates the exact input fields and
   numerical bounds, reserves the job ID in S3 and submits a fixed Systems
   Manager Run Command document to the deployment's worker. The model cannot
   select a different instance, document or shell command.
4. **Build native CAD.** The root supervisor starts a fresh FreeCAD process
   as the `cadauthor` Linux user. It builds a constrained sketch/pad/pocket
   feature tree and writes `.FCStd`, STEP and a preview.
5. **Freeze the candidate.** After the author exits, the supervisor copies
   allowlisted files into its own read-only directory and hashes them. The
   checker receives this fixed candidate.
6. **Verify independently.** A fresh FreeCAD process under the `cadcheck`
   user reopens the frozen files and performs 11 checks. It constructs its
   reference solid with primitives and boolean operations separately from
   the author's sketches, using the same FreeCAD/OCCT kernel.
7. **Publish evidence and verdict.** The supervisor requires all named
   checks to pass and their input hashes to match the frozen files. It
   publishes the available artifacts, checks and an accepted or failed result
   to private, versioned S3. Conditional writes prevent job records from
   being silently overwritten.
8. **Retrieve checked artifacts.** BMA calls `get_result` after creation to
   retrieve the published receipt, repeating if it returns `pending`.
   The local client downloads the recorded S3 versions,
   verifies sizes and hashes, and checks the report against those native
   files. The engineer receives both an editable model and its evidence.

The [client](../run.py), [broker](../cad_workflow/broker.py),
[worker](../cad_workflow/worker.py) and
[FreeCAD verifier](../cad_workflow/backends/freecad/verifier.py) implement this
sequence. A BMA completion message alone cannot establish geometric acceptance.

## What the EC2 rectangle contains

The supervisor, author and checker share one host. Author and checker execute
sequentially under different Linux users, each with a fresh application and
configuration directory, private network and temporary namespaces, a
read-only system, and memory/process/time limits. Only the supervisor has
cloud permissions.

The model runs in BMA. Gateway, Lambda and Systems Manager are regional managed
services outside the worker VPC. This sample does not provision AgentCore
Runtime or a remote desktop.

The default network has a public subnet and Internet Gateway. EC2 uses a
public address for outbound HTTPS, with no inbound security-group rules.
The SSM arrow represents a logical command delivered over the worker's
outbound connection. The existing-network option uses the same arrangement
with a customer-supplied subnet and route.

This host design is appropriate for the sample's fixed, trusted native
programs. It is not a qualified shared tenant boundary or a sandbox for
arbitrary uploaded CAD documents.

## Why EventBridge Scheduler is present

**EventBridge Scheduler implements scheduled EC2 stop for cost control.**
Deployment sets an absolute UTC `StopAtUtc` value using `--hours` (1–24).
CloudFormation creates a one-time `at(...)` schedule and a role that permits
`ec2:StopInstances` on this worker only.

At the deadline, Scheduler calls the EC2 API independently of the laptop, BMA
session and CAD job. It does not start jobs, poll their status, or wait for
completion. A job still running at the deadline can be interrupted. The
schedule has bounded retries; the configured deadline is not proof that a
future stop has succeeded.

Stopping preserves EBS and S3, which remain billable. Stack deletion instead
terminates the worker and deletes its root disk, while retaining S3. Restarting
after the deadline requires a new future stop deadline. See
[operation and cleanup](walkthrough.md#operate-and-clean-up).

## Logs and export

The sample captures three separate evidence streams:

| Source | Where to find it | Export |
| --- | --- | --- |
| BMA session, events and items | Local `artifacts/<job-id>/bma-*` files | Already saved as JSON/JSONL by `run.py` |
| Native files and check reports | Private S3 and the downloaded artifact directory | Versioned downloads with SHA-256 verification |
| Lambda and SSM diagnostics | `/cad/<stack>/broker` and `/cad/<stack>/worker` in CloudWatch Logs | CloudWatch Logs API, CLI, or an S3 export task |

For example, from the sample directory:

```bash
mkdir -p artifacts/logs
aws logs filter-log-events \
  --profile YOUR_PROFILE --region us-east-1 \
  --log-group-name /cad/bma-cad-freecad/worker \
  --filter-pattern '"JOB_ID_FROM_THE_RUN"' \
  --output json > artifacts/logs/worker-job.json
```

The AWS CLI paginates the response. The caller needs
`logs:FilterLogEvents`; use a time range for large log groups. Repeat with the
broker group when needed. A CloudWatch Logs S3 export requires its own export
task permissions and destination bucket policy.

Lambda active X-Ray tracing is configured. This sample does **not** install
an OpenTelemetry collector or automatically export BMA service traces into
one distributed trace. BMA event JSON, CloudWatch log export and OTEL are
different mechanisms. Add a collector or subscription pipeline, correlation
IDs and appropriate retention when that observability requirement arises.

## Deployment and verification status

The diagram describes the resources produced by this sample's
[CloudFormation renderer](../cad_workflow/infrastructure.py). It uses generic
account labels and includes no customer deployment identifiers.

Offline tests validate the packaged Python, transport checks and generated
infrastructure. To qualify a deployment, run a real BMA task and the
[native grader audit](verification.md) and retain those results. Neither a
diagram, passing unit tests nor bootstrap success proves a native CAD run.

The [support-bracket reference](../examples/support-bracket/reference/verified-example.json)
records a live qualification of this sample. The
[end-to-end runner](../e2e.py) performs the native task, audit, log export and
confirmed stop and produces a schema-validated report.
