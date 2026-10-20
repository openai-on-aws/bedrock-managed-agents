# Live validation of the AgentCore Runtime workspace

[Sample overview](../README.md) · [Design-study guide](design-study.md) ·
[Machine-readable result](../examples/design-study/runtime-reference.json)

The optional Runtime integration was live-validated on **20 October 2026**
in **`us-east-1`**, using `openai.gpt-6.1-sol`. BMA ran Python planning commands,
maintained workspace files, and resumed the same study after a pause and
replacement of its Runtime compute. Native CAD continued to use the separate
FreeCAD worker.

## Configuration

The qualification used a dedicated BMA exec-server Runtime with:

- An ARM64 container pinned by image digest; `codex-cli 0.159.2`.
- Managed session storage mounted at `/mnt/home`, with `BMA_HOME_DIR=/mnt/home`
  and the study workspace at `/mnt/home/workspace`.
- A 10-minute idle timeout and a one-hour maximum compute lifetime.
- Separate Runtime execution and BMA session roles. The session role permitted
  the selected model, CAD Gateway and this Runtime.
- The existing FreeCAD tools and native checker, with unchanged worker code.

The Runtime definition is supplied separately from the sample's CAD stack.
Follow [Runtime setup](design-study.md#add-a-persistent-execution-workspace)
to provision an equivalent environment.

## What passed

| Check | Observed result |
| --- | --- |
| BMA uses the workspace | Two successful Python command executions in the initial round |
| Native CAD integration | Three new candidates, each passing all 11 native checks |
| Files exist independently of the agent's answer | AgentCore's command API read `PLAN.md`, `comparison.json` and `STUDY.md` |
| Persistent storage survives compute replacement | All three files and a random 32-byte proof file retained identical SHA-256 hashes |
| Compute was replaced | A marker in `/tmp` disappeared and the kernel boot ID changed |
| Review resumes | The same BMA session ran another Python command and updated all three workspace files |
| CAD compute can remain stopped | Review retrieved an existing result with zero new CAD jobs; EC2 was stopped before and after review |

The temporary marker was first confirmed in two separate commands before
stopping the Runtime session. After approximately **4 hours 43 minutes**
between the stop response and the restoration check, it was gone and the
workspace hashes were unchanged. The restored files were checked **before**
submitting the next BMA turn, so the agent had no opportunity to recreate them.

## The engineering result

Candidate choices can vary between runs. This run built:

| Candidate | Stock / pocket | Floor | Measured volume | Reduction |
| --- | --- | --- | --- | --- |
| Baseline | 14 / 12 mm | 2 mm | 70,172.718 mm³ | 0% |
| Initial recommendation | 12 / 10 mm | 2 mm | 60,684.082 mm³ | 13.52% |
| Thicker-floor comparison | 12 / 8 mm | 4 mm | 64,436.348 mm³ | 8.17% |

The review required a floor of at least 3 mm and at least 10% volume reduction.
The initial recommendation failed the revised floor requirement; the
thicker-floor candidate missed the reduction target. BMA retrieved its
existing receipt and returned **no recommendation**, with `goal_met: false`
and `status: awaiting_review`. It correctly requested another design round
instead of relaxing the brief or creating an unallocated job.

This qualifies Runtime execution and resumption. A further CAD design round
is needed to meet the revised engineering brief for this run.

## Repeat the validation

1. Provision a BMA exec-server Runtime with the storage configuration above.
   Start the [design study](design-study.md#add-a-persistent-execution-workspace)
   using its Runtime ARN and a session role that also permits the CAD Gateway.
2. Wait for an idle BMA session and collect the initial round. Require successful
   `command_execution` items and independently verified native CAD results.
3. Identify the **Runtime session ID** from the matching Runtime lifecycle
   records in CloudWatch. It is distinct from the BMA session ID.
4. Use [InvokeAgentRuntimeCommand](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntimeCommand.html)
   for that Runtime session to read the three workspace files and record their
   sizes and SHA-256 hashes. Create a random proof file in the persisted workspace
   and a separate random marker in `/tmp`. Confirm both with a second command.
5. Stop the idle Runtime session with
   [StopRuntimeSession](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_StopRuntimeSession.html).
   Wait for the stop to complete. A successful stop response alone is insufficient
   evidence of compute replacement.
6. Inspect the same Runtime session again before giving BMA another input.
   Require the temporary marker to be absent and every persisted file hash
   to match. Record the boot ID as additional evidence.
7. With EC2 stopped, submit the supplied review to the same BMA session.
   Collect it, verify that it created no CAD jobs, and independently read
   the updated workspace files.

The inspecting identity needs `bedrock-agentcore:InvokeAgentRuntimeCommand`
and `bedrock-agentcore:StopRuntimeSession` scoped to the selected Runtime and
endpoint. Keep the marker values out of BMA's prompts.

For command probes, pass a program with arguments, such as
`python3 -I -c '...'`, or explicitly invoke a shell. A bare heredoc was not
executed by the command API during qualification; an exit code of zero with
empty output must not be accepted as a successful file check.

## Evidence, limits and cleanup

The [reference JSON](../examples/design-study/runtime-reference.json) records
timestamps, command and tool counts, native results, file hashes and source
report hashes. Full BMA items, version receipts, workspace snapshots and
deployment details remain in the operator's ignored `.state` directory.
The earlier [environment-none reference](../examples/design-study/reference.json)
describes a separate run.

This test covered a paused study and subsequent compute replacement.
It did not isolate exact stop latency or automatic idle expiry, exercise
hours of continuous agent execution, or validate reusable filesystem skills.
Managed session storage expires after 14 idle days and resets on Runtime
version updates; see [AWS filesystem behavior](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-filesystem-configurations.html).

After collecting evidence, stop the CAD worker and request a stop for the
idle Runtime session. Retain the BMA session, Runtime definition and storage
when further review is needed. Deleting a BMA session does not delete the
Runtime or its storage. Use the
[Runtime cleanup guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-bma.html)
to remove those resources when finished, after preserving needed files.
