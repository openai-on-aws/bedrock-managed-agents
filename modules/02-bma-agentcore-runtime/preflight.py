"""Preflight — a read-only "verify you're ready" check before Lab 01.

The goal of this script is to let a learner go from a fresh checkout to their
first successful BMA session on an AgentCore Runtime without guessing. It
inspects, and only inspects, the pieces the labs depend on and reports each one
as:

  * CONFIRMED  — this specific thing was verified just now.
  * UNVERIFIED — we could NOT check it (e.g. you lack IAM read permission).
                 This is NOT the same as "broken"; it means "unknown".
  * FAILED     — this is actively wrong and will stop the labs.


It is strictly READ-ONLY: it may call STS get-caller-identity and best-effort
IAM reads (get-role / get-role-policy / list-role-policies). It never creates a
session, never invokes the Runtime, never writes, and never mutates IAM. It
also never prints credentials, secret keys, or session tokens.

Run it from the module root with the venv active:

    python preflight.py --runtime arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-runtime-id
    python preflight.py --runtime <arn> --aws-profile my-profile

Exit code: 0 when nothing is FAILED (UNVERIFIED is fine); non-zero if any
check FAILED.
"""

import argparse
import json
import os
import sys
import urllib.parse

# --- Import the shared module helpers ------------------------------------
# preflight.py lives at the module root, right next to bma_common.py, so the
# module root is simply this file's own directory. Insert it on sys.path so
# `import bma_common` works no matter which directory you run the script from.
MODULE_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, MODULE_ROOT)

import bma_common  # noqa: E402 — must come after the sys.path shim above

# Status constants. Keeping them as named values avoids typos and lets the
# summary logic below reason about them without magic strings scattered around.
CONFIRMED = "CONFIRMED"
UNVERIFIED = "UNVERIFIED"
FAILED = "FAILED"

# A short glyph per status so the output scans quickly in a terminal.
_GLYPH = {CONFIRMED: "[OK ]", UNVERIFIED: "[ ? ]", FAILED: "[FAIL]"}


def _policy_statements(doc):
    """Return a policy document's Statement entries as a list, or None.

    Normalizes the shapes an IAM policy document can take so the trust/
    inference checks don't have to:

      * boto3 returns IAM policy documents already parsed as a ``dict`` (it
        URL-decodes and JSON-parses the API's string form for us), which is
        the normal case here;
      * we still defensively accept a ``str`` (URL-encoded and/or raw JSON, as
        the underlying REST/CLI API returns) and parse it, so an unexpected
        shape degrades gracefully instead of raising;
      * ``Statement`` may be a single object rather than a list (the IAM
        policy grammar allows both) — we always return a list.

    Returns ``None`` when the document cannot be understood, so the caller can
    report UNVERIFIED rather than crash or over-claim.
    """
    if isinstance(doc, str):
        # Underlying IAM REST/CLI returns URL-encoded JSON; boto3 normally
        # decodes it, but handle the string form defensively anyway.
        try:
            doc = json.loads(urllib.parse.unquote(doc))
        except (ValueError, TypeError):
            return None
    if not isinstance(doc, dict):
        return None
    stmts = doc.get("Statement", [])
    if isinstance(stmts, dict):  # singleton statement -> list
        stmts = [stmts]
    if not isinstance(stmts, list):
        return None
    return [s for s in stmts if isinstance(s, dict)]


def _as_list(value):
    """Return a scalar-or-list IAM field as a list (``[]`` for None)."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# ---------------------------------------------------------------------------
# IAM statement matchers (module-level so they are unit-testable).
#
# These are presence checks over a single parsed IAM statement, NOT full IAM
# policy evaluation (no condition semantics, NotAction, boundaries, SCPs). Each
# takes an already-parsed statement dict; the caller handles Effect precedence
# (an explicit Deny wins over an Allow).
# ---------------------------------------------------------------------------
def _stmt_matches_inference(stmt):
    """True if the statement's Action set includes CreateInference."""
    actions = _as_list(stmt.get("Action"))
    return any(
        a in ("bedrock-mantle:CreateInference", "bedrock-mantle:*", "*")
        for a in actions
    )


def _stmt_has_invoke_runtime_action(stmt):
    """True if the statement's Action set includes InvokeAgentRuntime.

    Effect-agnostic (used for both Allow and Deny); the caller inspects
    Effect separately so a Deny can override an Allow.
    """
    actions = _as_list(stmt.get("Action"))
    return any(
        a in (
            "bedrock-agentcore:InvokeAgentRuntime",
            "bedrock-agentcore:*",
            "*",
        )
        for a in actions
    )


def _stmt_resource_covers_runtime(stmt, runtime_arn):
    """Does this statement's Resource cover ``runtime_arn``?

    A presence check, not full IAM evaluation. We match EXACTLY (not by
    prefix, which would let runtime/rt-1 falsely match runtime/rt-10):
      * "*"                         -> covers everything;
      * the exact Runtime ARN;
      * a runtime-endpoint ARN under THIS Runtime, i.e.
        "<runtime_arn>/runtime-endpoint/..." (so StopRuntimeSession's
        endpoint-scoped resource still counts).
    Returns None when there is no Runtime ARN to compare against.
    """
    if not runtime_arn:
        return None  # unknown — no ARN to compare
    endpoint_prefix = runtime_arn + "/runtime-endpoint/"
    for r in _as_list(stmt.get("Resource")):
        if not isinstance(r, str):
            continue
        if r == "*" or r == runtime_arn or r.startswith(endpoint_prefix):
            return True
    return False


class Check:
    """One preflight result: a named check with a status and guidance.

    * name        — short label (e.g. "AWS identity").
    * status      — one of CONFIRMED / UNVERIFIED / FAILED.
    * detail      — one line of what we found (never secrets).
    * next_action — what to do about it; required for FAILED/UNVERIFIED so the
                    learner always has a concrete next step.
    """

    def __init__(self, name, status, detail, next_action=None):
        self.name = name
        self.status = status
        self.detail = detail
        self.next_action = next_action

    def render(self):
        """Return the printable block for this check (status line + detail)."""
        lines = [f"{_GLYPH[self.status]} {self.name}: {self.status}"]
        if self.detail:
            lines.append(f"       {self.detail}")
        # Only CONFIRMED checks have nothing more to do; the others always
        # carry a next action so the learner is never left guessing.
        if self.next_action:
            lines.append(f"       NEXT ACTION: {self.next_action}")
        return "\n".join(lines)


def check_python_and_sdk():
    """Confirm Python 3.12+ and that all three SDKs import; show versions.

    FAILED if Python is too old or a required import is missing — in either
    case the labs cannot run and setup.py needs to be (re-)run. Module 2 also
    needs aws-bedrock-token-generator (for the bearer-token auth), so it is
    checked here alongside openai and boto3.
    """
    py = "%d.%d.%d" % sys.version_info[:3]
    if sys.version_info[:2] < (3, 12):
        return Check(
            "Python & SDK",
            FAILED,
            f"Python {py} is too old; the labs need 3.12+.",
            "Install Python 3.12+ and re-run `python setup.py` (or `python3.12 setup.py`).",
        )
    try:
        import boto3  # noqa: E402 — imported lazily so a missing dep is a clean FAIL
        import openai  # noqa: E402
        import aws_bedrock_token_generator  # noqa: E402
    except Exception as exc:  # noqa: BLE001 — any import failure is a setup problem
        return Check(
            "Python & SDK",
            FAILED,
            f"Python {py} OK, but importing the SDKs failed: {exc}",
            "Re-run `python setup.py` to (re)install openai[bedrock], boto3, and "
            "aws-bedrock-token-generator into the venv.",
        )
    openai_ver = getattr(openai, "__version__", "unknown")
    boto3_ver = getattr(boto3, "__version__", "unknown")
    token_ver = getattr(aws_bedrock_token_generator, "__version__", "unknown")
    return Check(
        "Python & SDK",
        CONFIRMED,
        f"Python {py}, openai {openai_ver}, boto3 {boto3_ver}, "
        f"aws-bedrock-token-generator {token_ver}.",
    )


def check_region(region):
    """Confirm the selected Region is one the labs support."""
    if region in bma_common.SUPPORTED_REGIONS:
        return Check("Region", CONFIRMED, f"Using supported Region {region}.")
    supported = ", ".join(bma_common.SUPPORTED_REGIONS)
    return Check(
        "Region",
        FAILED,
        f"Region {region!r} is not a supported labs Region.",
        f"Select a supported Region with --region (one of: {supported}), or pass "
        "a --runtime ARN whose Region is supported.",
    )


def check_identity(profile):
    """Resolve the caller identity via STS get-caller-identity (read-only).

    Returns (Check, account_id). account_id is None when identity could not be
    resolved, so later checks can report UNVERIFIED instead of guessing.
    Never prints access keys or session tokens — only the account id and the
    caller ARN, which are safe to display.
    """
    try:
        import boto3  # noqa: E402

        session = boto3.Session(profile_name=profile)
        ident = session.client("sts").get_caller_identity()
    except Exception as exc:  # noqa: BLE001 — creds may be absent/expired; treat as FAIL
        who = f"profile {profile!r}" if profile else "the default credential chain"
        return (
            Check(
                "AWS identity",
                FAILED,
                f"Could not resolve credentials for {who}: {exc}",
                "Refresh your SSO session or AWS credentials (e.g. `aws sso login "
                "--profile <p>`), or pass --aws-profile with valid credentials.",
            ),
            None,
        )
    account = ident.get("Account")
    arn = ident.get("Arn")
    return (
        Check(
            "AWS identity",
            CONFIRMED,
            f"Account {account}, caller {arn}.",
        ),
        account,
    )


def check_runtime(runtime_arn, region, caller_account):
    """Validate the --runtime ARN's shape and Region (presence/shape only).

    This does NOT call any AWS AgentCore runtime API — reachability is a
    best-effort note, not a verified fact. It confirms:
      * --runtime was provided,
      * it is a well-formed arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>,
      * its Region matches the selected Region,
      * (informational) whether its account matches the caller account.
    Returns the Check only; the labs still need an actual session to prove the
    Runtime is live.
    """
    if not runtime_arn:
        return Check(
            "AgentCore Runtime",
            FAILED,
            "No --runtime ARN was provided; Module 2 runs the agent on a "
            "specific AgentCore Runtime.",
            "Pass --runtime arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id> "
            "(or set $BMA_RUNTIME_ARN) for the Runtime you created from the "
            "AgentCore get-started tutorial.",
        )

    parts = runtime_arn.split(":")
    well_formed = (
        len(parts) >= 6
        and parts[0] == "arn"
        and parts[2] == "bedrock-agentcore"
        and bool(parts[3])
        and bool(parts[4])
        and parts[5].startswith("runtime/")
        and len(parts[5].split("/", 1)[-1]) > 0
    )
    if not well_formed:
        return Check(
            "AgentCore Runtime",
            FAILED,
            f"--runtime ARN {runtime_arn!r} is not well-formed.",
            "Use arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<runtime-id>.",
        )

    runtime_region = bma_common.region_from_runtime_arn(runtime_arn)
    runtime_account = bma_common.account_from_runtime_arn(runtime_arn)
    if runtime_region != region:
        return Check(
            "AgentCore Runtime",
            FAILED,
            f"--runtime ARN Region ({runtime_region}) does not match the "
            f"selected Region ({region}); a session must run in the Runtime's "
            "own Region.",
            "Drop --region (it is derived from the Runtime ARN) or pass a "
            "--runtime ARN in the Region you want.",
        )

    note = ""
    if caller_account and runtime_account and runtime_account != caller_account:
        note = (
            f" NOTE: the Runtime account ({runtime_account}) differs from your "
            f"caller account ({caller_account})."
        )
    return Check(
        "AgentCore Runtime",
        UNVERIFIED,
        f"{runtime_arn} is well-formed and in Region {runtime_region}. "
        "Existence/reachability was not checked (no runtime API call is made "
        f"here).{note}",
        "If a session later fails to start, confirm the Runtime exists and is "
        "READY in the AgentCore console or via the AgentCore CLI.",
    )


def _account_from_arn(role_arn):
    """Best-effort extract the account id from an IAM role ARN.

    ARN shape: arn:aws:iam::<account-id>:role/<name>. Returns None if the ARN
    does not have an account id in the expected position.
    """
    parts = role_arn.split(":")
    if len(parts) >= 5 and parts[4]:
        return parts[4]
    return None


def check_session_role(explicit_role_arn, runtime_arn, profile, caller_account):
    """Resolve the exact session-role ARN the labs will use and show it.

    Returns (Check, role_arn). resolve_session_role_arn may itself need STS
    (and a Region from the Runtime ARN) when no explicit ARN is given; if that
    fails we report UNVERIFIED (we could not determine the ARN) rather than
    FAILED (we have no evidence it is wrong).
    """
    try:
        role_arn = bma_common.resolve_session_role_arn(
            explicit_role_arn, runtime_arn, profile
        )
    except Exception as exc:  # noqa: BLE001 — STS/ARN lookup for the default can fail
        return (
            Check(
                "Session role",
                UNVERIFIED,
                f"Could not determine the role ARN: {exc}",
                "Pass --role-arn explicitly, or fix credentials and the "
                "--runtime ARN so the default BmaSessionRole-<region> ARN can "
                "be built from its Region and your account id.",
            ),
            None,
        )

    role_account = _account_from_arn(role_arn)
    if caller_account and role_account:
        if role_account == caller_account:
            detail = f"{role_arn} (in your own account {caller_account})."
        else:
            detail = (
                f"{role_arn} (account {role_account}, which is NOT your caller "
                f"account {caller_account})."
            )
        return (Check("Session role", CONFIRMED, detail), role_arn)

    # We have the ARN but could not compare accounts (identity unknown).
    return (
        Check(
            "Session role",
            UNVERIFIED,
            f"{role_arn} (could not compare against your account — identity "
            "unresolved).",
            "Resolve the AWS identity check above to confirm the role is in "
            "your account.",
        ),
        role_arn,
    )


def check_iam_config(role_arn, profile, runtime_arn=None):
    """Best-effort, read-only inspection of the session role's IAM config.

    Reads and INSPECTS the policy documents, not just their existence:
      * iam:GetRole -> the trust policy must allow
        bedrock-mantle.amazonaws.com to assume the role;
      * iam:ListRolePolicies + iam:GetRolePolicy -> at least one inline policy
        (the BmaSession policy) must Allow bedrock-mantle:CreateInference. We
        additionally NOTE whether bedrock-agentcore:InvokeAgentRuntime is
        present AND whether its Resource actually covers THIS Runtime ARN
        (not just that the action exists), since an Allow scoped to a
        different Runtime would not authorize running the agent here.
    This is a presence check over INLINE policies, not full IAM evaluation
    (no condition semantics, NotAction, permission boundaries, attached managed
    policies, or SCPs). It therefore reports:
      * FAILED only when the result is certain from what we can see — the role
        does not exist, the trust policy does not allow BMA to assume it, or an
        UNCONDITIONAL inline Deny blocks CreateInference;
      * CONFIRMED only when an inline Allow is present with no overriding Deny
        and no unevaluated condition;
      * UNVERIFIED whenever the effective result depends on something we did
        not evaluate — a conditional Allow or Deny, a document we cannot read
        (AccessDenied), an unparseable document, or no inline Allow (the
        permission may live in an attached managed policy we do not inspect).

    CONFIRMED means the trust and inference permission were actually seen; it
    still does not prove every session operation is authorized — only a real
    session call does.
    """
    if not role_arn:
        return Check(
            "IAM configuration",
            UNVERIFIED,
            "No role ARN was resolved, so nothing could be inspected.",
            "Resolve the Session role check above first.",
        )

    role_name = role_arn.split("/")[-1]
    try:
        import boto3  # noqa: E402
        from botocore.exceptions import ClientError  # noqa: E402

        iam = boto3.Session(profile_name=profile).client("iam")
    except Exception as exc:  # noqa: BLE001 — no creds/client means we cannot check
        return Check(
            "IAM configuration",
            UNVERIFIED,
            f"Could not build an IAM client to inspect the role: {exc}",
            "Fix the AWS identity check above, then re-run.",
        )

    try:
        role = iam.get_role(RoleName=role_name)["Role"]
    except ClientError as exc:  # noqa: BLE001 — distinguish NoSuchEntity vs AccessDenied
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchEntity", "NoSuchEntityException"):
            return Check(
                "IAM configuration",
                FAILED,
                f"Role {role_name!r} does not exist in this account.",
                "Create it with `python scripts/create_session_role.py --runtime <arn>` "
                "(or ask an admin), or pass --role-arn for the role you actually use.",
            )
        if code in ("AccessDenied", "AccessDeniedException"):
            return Check(
                "IAM configuration",
                UNVERIFIED,
                f"Not allowed to read role {role_name!r} (iam:GetRole denied).",
                "This usually just means you lack IAM read permission; it does NOT "
                "mean setup is broken. Ask an admin to confirm the role if unsure.",
            )
        return Check(
            "IAM configuration",
            UNVERIFIED,
            f"Could not inspect role {role_name!r}: {exc}",
            "Re-run with valid credentials, or confirm the role with an admin.",
        )
    except Exception as exc:  # noqa: BLE001 — any other error is "unknown", not "broken"
        return Check(
            "IAM configuration",
            UNVERIFIED,
            f"Could not inspect role {role_name!r}: {exc}",
            "Re-run with valid credentials, or confirm the role with an admin.",
        )

    # The role exists and we can read it. Actually INSPECT the policy
    # documents — not just confirm existence — so a CONFIRMED here means we
    # verified the two things the labs depend on:
    #   1. the trust policy lets bedrock-mantle.amazonaws.com assume the role,
    #   2. an inline policy allows bedrock-mantle:CreateInference.
    # We also NOTE whether bedrock-agentcore:InvokeAgentRuntime is present.
    # If a document is missing or wrong, that is a real FAILED. If we cannot
    # READ the documents (AccessDenied) we report UNVERIFIED, never CONFIRMED,
    # so a confirmed result reflects an actual check rather than mere
    # existence.

    # 1. Trust policy — from the get_role response we captured above. Normalize
    # the document shape; if we cannot parse it, say UNVERIFIED rather than
    # crash or guess.
    BMA_PRINCIPAL = "bedrock-mantle.amazonaws.com"
    trust_stmts = _policy_statements(role.get("AssumeRolePolicyDocument"))
    if trust_stmts is None:
        return Check(
            "IAM configuration",
            UNVERIFIED,
            f"Role {role_name!r} exists, but its trust policy document could "
            "not be parsed into a form we recognize.",
            "Inspect the trust policy manually (see 00-prerequisites.md); this "
            "is an unexpected document shape, not necessarily a misconfiguration.",
        )

    def _assume_matches(stmt):
        # Does this statement concern bedrock-mantle assuming the role via
        # sts:AssumeRole? (Used for both the Allow we need and a Deny we honor.)
        principal = stmt.get("Principal", {})
        services = _as_list(principal.get("Service")) if isinstance(principal, dict) else []
        actions = _as_list(stmt.get("Action"))
        assumes = any(a in ("sts:AssumeRole", "sts:*", "*") for a in actions)
        return BMA_PRINCIPAL in services and assumes

    # An explicit Deny that matches wins over any Allow, so check it first.
    denied = any(s.get("Effect") == "Deny" and _assume_matches(s) for s in trust_stmts)
    trust_ok = (not denied) and any(
        s.get("Effect") == "Allow" and _assume_matches(s) for s in trust_stmts
    )
    if not trust_ok:
        reason = (
            "an explicit Deny blocks bedrock-mantle from assuming it"
            if denied
            else "it does not Allow bedrock-mantle.amazonaws.com to call "
            "sts:AssumeRole"
        )
        return Check(
            "IAM configuration",
            FAILED,
            f"Role {role_name!r} exists, but its trust policy will not let BMA "
            f"assume it — {reason}.",
            "Fix the trust policy (see 00-prerequisites.md), or re-create the "
            "role with `python scripts/create_session_role.py --runtime <arn>`.",
        )

    # 2. Inline inference permission — read each inline policy document and
    # look for an Allow of bedrock-mantle:CreateInference. We also note whether
    # bedrock-agentcore:InvokeAgentRuntime is present (Module 2 needs it).
    try:
        names = iam.list_role_policies(RoleName=role_name).get("PolicyNames", [])
    except Exception as exc:  # noqa: BLE001 — listing may be denied separately
        return Check(
            "IAM configuration",
            UNVERIFIED,
            f"Role {role_name!r} exists and trusts BMA, but its inline policies "
            f"could not be listed ({exc}).",
            "You can read the role but not its policies; this is a permission "
            "gap (iam:ListRolePolicies), not necessarily a misconfiguration.",
        )

    # Scan all INLINE policies for statements that touch CreateInference. We do
    # NOT do full IAM policy evaluation (condition semantics, NotAction,
    # permission boundaries, attached/managed policies, SCPs) — out of scope for
    # a readiness check. We only claim what we can actually see, and downgrade
    # to UNVERIFIED (never a false CONFIRMED or a false FAILED) whenever the
    # effective result depends on something we did not evaluate:
    #   * an UNCONDITIONAL matching Deny -> FAILED (it always denies);
    #   * a CONDITIONAL matching Deny -> UNVERIFIED (may not apply to the call);
    #   * a matching Allow with a Condition we don't evaluate -> UNVERIFIED;
    #   * a plain matching Allow with no overriding Deny -> CONFIRMED (presence);
    #   * NO inline Allow found -> UNVERIFIED, because the permission could come
    #     from an attached managed policy we did not inspect (absence of an
    #     inline Allow is not proof inference is denied).
    # These matchers live at module level (see top of file) so they can be
    # unit-tested without a live IAM client. _stmt_resource_covers_runtime
    # takes the Runtime ARN as a parameter; close over the local here.
    _matches_inference = _stmt_matches_inference
    _has_invoke_runtime_action = _stmt_has_invoke_runtime_action

    def _resource_covers_runtime(stmt):
        return _stmt_resource_covers_runtime(stmt, runtime_arn)

    allow_found = False
    allow_conditional = False
    deny_unconditional = False
    deny_conditional = False
    # Runtime invoke permission, tracked by SCOPE and honoring Deny:
    #   invoke_denied        -> a Deny on the action covering THIS Runtime
    #                           (wins over any Allow)
    #   invoke_scoped        -> an Allow whose Resource covers THIS Runtime
    #   invoke_other_only    -> an Allow for the action, but Resource does not
    #                           cover this Runtime (targets a different one)
    #   invoke_scope_unknown -> action present but no Runtime ARN to compare,
    #                           or the statement is condition-gated
    invoke_denied = False
    invoke_scoped = False
    invoke_other_only = False
    invoke_scope_unknown = False
    unreadable = []
    for name in names:
        try:
            raw = iam.get_role_policy(RoleName=role_name, PolicyName=name).get(
                "PolicyDocument", {}
            )
        except Exception:  # noqa: BLE001 — a single policy may be unreadable
            unreadable.append(name)
            continue
        stmts = _policy_statements(raw)
        if stmts is None:  # present but unparseable -> treat as unverifiable
            unreadable.append(name)
            continue
        for stmt in stmts:
            if _has_invoke_runtime_action(stmt):
                covers = _resource_covers_runtime(stmt)
                conditional = bool(stmt.get("Condition"))
                effect = stmt.get("Effect")
                if effect == "Deny":
                    # A Deny on the invoke action covering this Runtime wins.
                    # (Conditional Denies are inconclusive -> scope-unknown.)
                    if covers is True and not conditional:
                        invoke_denied = True
                    else:
                        invoke_scope_unknown = True
                elif effect == "Allow":
                    if covers is True and not conditional:
                        invoke_scoped = True
                    elif covers is False:
                        invoke_other_only = True
                    else:  # covers is None (no ARN) or condition-gated
                        invoke_scope_unknown = True
            if not _matches_inference(stmt):
                continue
            effect = stmt.get("Effect")
            conditional = bool(stmt.get("Condition"))
            if effect == "Deny":
                if conditional:
                    deny_conditional = True
                else:
                    deny_unconditional = True
            elif effect == "Allow":
                allow_found = True
                if conditional:
                    allow_conditional = True

    # A short suffix describing the Runtime invoke permission BY SCOPE, so the
    # inference verdict never implies the Runtime is authorized when the Allow
    # actually targets a different Runtime (or we couldn't check scope). An
    # unconditional Deny covering this Runtime wins over any Allow.
    if invoke_denied:
        invoke_note = (
            " NOTE: an inline policy Denies bedrock-agentcore:InvokeAgentRuntime "
            "on this Runtime — running the agent on it will be denied regardless "
            "of any Allow. Remove or narrow that Deny."
        )
    elif invoke_scope_unknown:
        # Checked BEFORE the scoped-Allow notes on purpose: a condition-gated
        # Deny (or Allow) also lands here, and an unevaluated condition could
        # flip effective authorization. Reporting "present and scoped" when a
        # conditional Deny is in play would be falsely reassuring, so the
        # unknown-scope caveat wins over any scoped-Allow note.
        invoke_note = (
            " NOTE: bedrock-agentcore:InvokeAgentRuntime is present, but its scope "
            "for this Runtime could not be confirmed (no Runtime ARN to compare, or "
            "a condition this check does not evaluate — e.g. a conditional Deny "
            "that may or may not apply to the call)."
        )
    elif invoke_scoped and not invoke_other_only:
        invoke_note = " bedrock-agentcore:InvokeAgentRuntime is also present and scoped to this Runtime."
    elif invoke_scoped:
        invoke_note = " bedrock-agentcore:InvokeAgentRuntime is present and scoped to this Runtime (another Allow targets a different Runtime, which is harmless here)."
    elif invoke_other_only:
        invoke_note = (
            " NOTE: an Allow for bedrock-agentcore:InvokeAgentRuntime exists but its "
            "Resource does NOT cover this Runtime ARN — running the agent on THIS "
            "Runtime will likely be denied. Re-create the role with "
            "`python scripts/create_session_role.py --runtime <this-runtime-arn>`."
        )
    else:
        invoke_note = (
            " NOTE: no inline Allow for bedrock-agentcore:InvokeAgentRuntime was "
            "seen — Module 2 needs it to run the agent on the Runtime (re-create the "
            "role with `python scripts/create_session_role.py --runtime <arn>` if it "
            "is missing)."
        )

    # An UNCONDITIONAL matching Deny always wins -> inference denied.
    if deny_unconditional:
        return Check(
            "IAM configuration",
            FAILED,
            f"Role {role_name!r} trusts BMA, but an inline policy "
            "unconditionally Denies bedrock-mantle:CreateInference — inference "
            "will be denied regardless of any Allow.",
            "Remove or narrow the Deny (see 00-prerequisites.md), or use a role "
            "without it.",
        )

    # A CONDITIONAL matching Deny may or may not apply to the learner's call;
    # we don't evaluate conditions, so we cannot assert denial.
    if deny_conditional:
        return Check(
            "IAM configuration",
            UNVERIFIED,
            f"Role {role_name!r} has a Deny on bedrock-mantle:CreateInference "
            "that is gated by a Condition this check does not evaluate — "
            "whether inference is denied depends on that condition.",
            "Inspect the Deny's condition, or test with an actual session.",
        )

    # A conditional Allow may or may not apply at call time; we don't evaluate
    # conditions, so we cannot claim effective permission.
    if allow_found and allow_conditional:
        return Check(
            "IAM configuration",
            UNVERIFIED,
            f"Role {role_name!r} trusts BMA and has an Allow for "
            "bedrock-mantle:CreateInference, but it is gated by a Condition "
            "this check does not evaluate — effective permission is unknown."
            + invoke_note,
            "Confirm the condition (e.g. bedrock-mantle:Model) matches the "
            "model you will use, or test with an actual session.",
        )

    if allow_found:
        return Check(
            "IAM configuration",
            CONFIRMED,
            f"Role {role_name!r} trusts bedrock-mantle and allows "
            "bedrock-mantle:CreateInference (no overriding Deny; no "
            "unevaluated condition on the Allow)." + invoke_note + " This is a "
            "presence check, not full IAM evaluation — only an actual session "
            "call proves every operation is authorized.",
        )

    if unreadable:
        return Check(
            "IAM configuration",
            UNVERIFIED,
            f"Role {role_name!r} trusts BMA, but inference permission could not "
            f"be confirmed: policies {', '.join(unreadable)} were not readable.",
            "You lack iam:GetRolePolicy on some policies; this is a permission "
            "gap, not necessarily a misconfiguration.",
        )

    # No INLINE Allow found. The permission may still come from an attached
    # managed policy, which this check does not inspect — so report UNVERIFIED
    # rather than claim inference is denied.
    return Check(
        "IAM configuration",
        UNVERIFIED,
        f"Role {role_name!r} exists and trusts BMA, but none of its INLINE "
        "policies allow bedrock-mantle:CreateInference. Attached (managed) "
        "policies were not inspected, so inference permission is unconfirmed — "
        "not necessarily denied." + invoke_note,
        "If you rely on a managed policy, verify it allows "
        "bedrock-mantle:CreateInference; otherwise add the inline BmaSession "
        "policy with `python scripts/create_session_role.py --runtime <arn> --model <id>`.",
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Read-only preflight: verify Python/SDK, AWS identity, Region, "
            "AgentCore Runtime, session role, and IAM config before Lab 01. "
            "Makes no sessions, invokes no Runtime, and mutates nothing."
        )
    )
    # Reuse the same connection/role/runtime flags the labs use so --region /
    # --aws-profile / --role-arn / --runtime behave identically here.
    bma_common.add_connection_args(parser)
    bma_common.add_role_arn_arg(parser)
    bma_common.add_runtime_arg(parser)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # The session Region is the Runtime's own Region when a --runtime ARN is
    # given (it is authoritative); otherwise fall back to --region. The
    # check_region/check_runtime pair surfaces any conflict.
    runtime_region = (
        bma_common.region_from_runtime_arn(args.runtime) if args.runtime else None
    )
    region = runtime_region or args.region

    checks = []

    # 1. Python & SDK — purely local; no AWS involved.
    checks.append(check_python_and_sdk())

    # 2. Region — purely local membership check.
    checks.append(check_region(region))

    # 3. AWS identity — the first thing that touches AWS (read-only STS).
    identity_check, caller_account = check_identity(args.aws_profile)
    checks.append(identity_check)

    # 4. AgentCore Runtime — presence/shape only; no runtime API call.
    checks.append(check_runtime(args.runtime, region, caller_account))

    # 5. Session role — the exact ARN the labs will pass to BMA.
    role_check, role_arn = check_session_role(
        args.role_arn, args.runtime, args.aws_profile, caller_account
    )
    checks.append(role_check)

    # 6. IAM configuration — best-effort, read-only inspection of that role.
    checks.append(check_iam_config(role_arn, args.aws_profile, args.runtime))

    # Print each check as its own block.
    print("BMA Module 2 preflight — read-only readiness check\n")
    for check in checks:
        print(check.render())
        print()

    # Summarize. We deliberately do NOT say "all set": we report the tally and
    # whether anything is blocking.
    confirmed = sum(1 for c in checks if c.status == CONFIRMED)
    unverified = sum(1 for c in checks if c.status == UNVERIFIED)
    failed = sum(1 for c in checks if c.status == FAILED)

    summary = (
        f"Summary: {confirmed} confirmed, {unverified} unverified, "
        f"{failed} failed."
    )
    if failed:
        summary += (
            " Fix the FAILED checks above (see each NEXT ACTION) before Lab 01."
        )
    elif unverified:
        summary += (
            " Nothing is actively broken, but some checks could not be verified "
            "(see UNVERIFIED above). A clean run does not guarantee every "
            "session operation is authorized."
        )
    else:
        summary += (
            " No blockers found. Note: this does not guarantee every session "
            "operation is authorized — only what is listed above was checked."
        )
    print(summary)

    # Exit non-zero only when something is actively wrong (FAILED). UNVERIFIED
    # is informational, not blocking.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
