# Architecture walkthrough for review

These numbers match the small markers in the [AWS architecture diagram](architecture.png).
The same descriptions appear in the numbered walkthrough beneath the architecture.
Items 1–8 describe the study flow. Item 9 is optional; item 10 operates independently.

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
   when configured. This integration requires separate provisioning and has
   not been live-validated.

10. **Stop EC2 at the scheduled deadline.** EventBridge Scheduler independently
    calls `StopInstances` at a configured UTC time for cost control. It can
    interrupt a running CAD job. Stopping EC2 preserves the BMA session, EBS disk
    and S3 evidence; retained storage remains billable.

The [architecture notes](architecture.md) describe the boundaries, permissions,
state, logs and live qualification. Geometric acceptance does not establish
structural strength, manufacturability or engineering approval.
