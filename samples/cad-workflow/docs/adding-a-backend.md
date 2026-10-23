# Adding a CAD backend, including a future CATIA V6 implementation

FreeCAD is the only implemented backend. The sample exposes a small
deployment-owned interface so another CAD system can reuse the BMA tool
contract and evidence flow. Calling `get_backend("catia-v6")` currently raises an error;
there is no placeholder that reports success.

## Keep the tool contract stable

BMA sees two tools: `create_support` with a job ID, stock thickness and pocket
depth, and `get_result` with the job ID. Units are millimetres. Backend choice
is not a tool argument, and models cannot upload code or select a runtime.

[`contract.py`](../cad_workflow/contract.py) defines the input bounds, schema
and required check names. If a new backend implements this same part family,
it should produce the same measurements and acceptance semantics. If the
engineering task changes, version the contract and add suitable fixtures.

## The adapter boundary

[`backends/freecad/`](../cad_workflow/backends/freecad/) supplies:

| Interface | Purpose |
| --- | --- |
| `NAME` | Backend identity written into reports and stack outputs |
| `CANDIDATE_FILES` | Exact native, exchange, preview and author-record filenames the supervisor may freeze |
| `CHECKED_FILES` | Native and exchange files whose hashes must bind to the accepted report |
| `AUTHOR_MODULE` / `VERIFIER_MODULE` | Packaged entrypoints for the two native phases |
| `SOURCE_FILES` | Explicit source allowlist included in the worker bundle and source hashes |
| `command(phase, directory, code)` | Fixed native application invocation for a packaged phase |

The FreeCAD entrypoints receive `CAD_REQUEST`, `CAD_OUTPUT` and `CAD_SOURCE`
paths from the supervisor. The author creates the candidate; the checker
writes `checks.json` against the frozen input. Each phase must exit within the
supervisor's limits. A valid accepted report includes the backend, schema,
all 11 named checks, finite measurements and the exact input hashes.

Add a backend in [`backends/__init__.py`](../cad_workflow/backends/__init__.py)
only after implementing and testing it. Update the explicit source
packaging, bootstrap and stack outputs together. The current bootstrap,
`systemd-run` isolation and mutation audit are specific to Linux/FreeCAD;
the interface is not a promise that every CAD application runs on that host.

## What CATIA V6 changes

A CATIA implementation needs engineering work in the following areas:

1. **Licensed runtime and supported deployment.** Establish the exact CATIA /
   3DEXPERIENCE release, licensing, automation entitlement, operating system,
   unattended-session requirements and supported hosting model with the
   customer and vendor. This sample distributes no CATIA software.
2. **Native author.** Implement the support-part features through the
   supported automation API for that release. Preserve editable design
   intent, millimetre units, constraint information and stable exports.
3. **Independent checker.** Reopen the saved native design in a fresh
   application/session and verify the geometry, mounting interfaces,
   dimensions, STEP agreement and parameter edit/restore. Choose measurable
   CATIA equivalents for FreeCAD-specific feature checks; do not merely
   rename the `.FCStd` extension.
4. **Host and process isolation.** If the deployment uses Windows or a
   vendor-managed service, replace the Linux bootstrap and launcher with an
   appropriate supervisor and identity/process isolation design. Review
   licensing/network dependencies without handing cloud credentials or
   arbitrary commands to the agent.
5. **Artifacts and evidence.** Define the required native artifact set,
   references and export formats for the chosen CATIA deployment. Adapt the
   allowlists and manifest while retaining immutable job IDs, independent
   verification, versioned storage and verified downloads.
6. **Qualification.** Provide valid controls, boundary-value parts, invalid
   geometry, stale exports and failed recompute cases. The FreeCAD-specific
   `ops/freecad_audit.py` must be replaced with a native suite. Archive the
   runtime versions, source hashes and results for that backend.

BMA integration, bounded tool dispatch and S3 evidence handling can remain
largely stable when the contract is stable. Runtime installation, native
authoring and verification are the substantive backend work.

## Moving beyond the sample

For a customer deployment, establish engineering tolerances, accepted file
types and data retention first. Add workload admission and per-job isolation
before sharing workers. Qualify native-file parsing before allowing uploads.
Add engineer review and explicit approval before releasing files to a PLM or
manufacturing system. A geometry pass is evidence for the specified contract,
not structural or manufacturing sign-off.
