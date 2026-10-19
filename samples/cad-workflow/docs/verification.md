# Verification and native grader audit

Acceptance comes from a native checker applied to specific artifact bytes.
BMA's final message is not used as the grader.

The part has a 160 × 42 mm envelope, bores centred at `(14,10)`, `(14,32)` and
`(148,21)` mm, and a capsule pocket spanning `x=44..134`, `y=10..32` with
11 mm end radii. Thickness is 10–20 mm; pocket depth leaves at least 2 mm
of material.

## Native checks

| Check | Evidence |
| --- | --- |
| `native_solid` | One valid solid after opening and recomputing the native file |
| `native_features` | Expected native pad/pocket feature types |
| `constrained_sketches` | Fully constrained stock, bore and pocket sketches |
| `blind_pocket` | Blind mode, direction and requested depth |
| `stock_thickness` | Requested native pad length |
| `units_and_envelope` | Millimetre dimensions and stock bounds |
| `material_and_mounting_interfaces` | Added/missing volume against an independent primitive/boolean reference |
| `analytic_volume` | Volume from stock, bore and pocket dimensions |
| `residual_floor` | Material intersection and inside/outside probes of the actual solid |
| `step_roundtrip` | Valid re-imported STEP solid with matching material |
| `native_parameter_roundtrip` | Thickness edit `T → T+1 → T`, recomputation and geometric agreement |

The checker uses tolerances appropriate to this deterministic fixture:
`1e-7` mm for selected native parameters and floor measurements, `1e-5` mm
for the bounding box, and `0.001` mm³ for material differences and volume.
These are numerical test tolerances, not manufacturing specifications.

The author constructs a feature tree; the checker constructs its reference
from primitives and boolean operations in a fresh process. Both use the
same FreeCAD/OCCT kernel. This separation can catch authoring defects but is
not an independent-kernel certification.

## Audit the grader itself

[`audit.py`](../audit.py) sends an operator-owned audit to the worker after a
successful baseline. It uses FreeCAD to produce the following candidates:

| Candidate | Expected outcome |
| --- | --- |
| Valid control | Every check passes |
| Through pocket | `blind_pocket` fails |
| 1 mm residual floor | `residual_floor` fails |
| First mounting bore shifted by 2 mm | `material_and_mounting_interfaces` fails |
| Unrelated stock block exported as STEP | `step_roundtrip` fails |

The audit requires the intended named rule to reject each defect. A missing
file, parse failure, crash or timeout does not satisfy a negative case.
Results go to `artifacts/<source-job>/audit-<suite-id>/` and
`health/audits/<suite-id>/` in the deployment's S3 bucket.

The audit is an operator tool with SSM permissions. It is never exposed to
BMA and cannot be triggered by the agent's MCP arguments.

## Local validation versus deployment qualification

`uv run --frozen python deploy.py validate` tests input rejection, fixed tool
dispatch, source packaging, receipt integrity, required checks and scoped
session cleanup. It also validates both CloudFormation network variants and
checks Guard against four weakened configurations.

These tests use synthetic bytes for artifact transport and do not claim
native CAD correctness. Native geometry requires a deployed Linux FreeCAD
worker (or an equivalently configured native test environment).

To qualify a deployment, retain:

1. The rendered template, resolved AMI, source hash and bootstrap record.
2. A real BMA run with all 11 native checks and its versioned native artifacts.
3. A second valid parameter pair, such as thickness 16 mm and depth 11.5 mm.
4. The native audit's valid control and four intended rejections.

Keep these records in the customer's evidence store; runtime records and
account identifiers are ignored by Git.

For the complete executable path, use `e2e.py` as described in the
[README](../README.md#run-the-complete-example). The runner verifies both MCP
tools, checks the executed source hashes, captures job-correlated CloudWatch
events, deletes the test session, confirms EC2 has stopped, and retrieves the
recorded S3 versions again after that stop.

The [portable reference](../examples/support-bracket/reference/verified-example.json)
is backed by a real support-bracket run. `evidence.py verify-reference`
validates its JSON and local file hashes without contacting AWS. That offline
integrity check does not execute a geometry kernel; the recorded native audit
and a fresh live run serve different purposes.

## Relationship to RLVR

This checker could supply a verifiable reward signal for a separate
reinforcement learning experiment. RLVR is the training approach; the native
geometry checker is the verifier. This repository sample implements
evaluation and artifact acceptance, not model training.

A training setup would additionally need a task distribution, held-out
designs, broader positive and negative cases, anti-shortcut checks and a
training pipeline. This single support-part fixture is neither a general
CAD benchmark nor an open-source CAD dataset.
