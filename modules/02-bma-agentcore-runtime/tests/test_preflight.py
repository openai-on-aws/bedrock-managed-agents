"""Regression tests for Module 2's security-sensitive helpers.

Run from the module directory with the stdlib test runner (no extra deps):

    cd modules/02-bma-agentcore-runtime
    python -m unittest discover -s tests -v

These cover the three places where a subtle bug would silently weaken the
least-privilege story the labs teach:

  1. the Runtime ARN validator in scripts/create_session_role.py — the only
     thing standing between a user-supplied string and the IAM policy's
     Resource (wildcards / wrong account / wrong service must be rejected);
  2. the IAM scope matcher in preflight.py — exact Runtime match, so
     runtime/rt-1 never passes for runtime/rt-10, and a Deny wins over an
     Allow;
  3. the profile-bound credential provider in bma_common.py — the bearer
     token must be minted under the profile the user selected, not the
     ambient default chain.

The production modules live in hyphenated directories (``02-bma-...``) that
are not importable as packages, so we load them from their file paths.
"""

import importlib.util
import pathlib
import sys
import types
import unittest

_MODULE_DIR = pathlib.Path(__file__).resolve().parent.parent


def _stub_module_if_missing(name, build):
    """Install a minimal stub for ``name`` only if it cannot be imported.

    The production modules import ``boto3`` and ``openai`` at module top level
    (the real scripts genuinely need them). None of the logic under test here
    touches either package at import time, so when they are absent — e.g. a
    bare Python with no AWS deps — we register a lightweight stub so the file
    can load. If the real package IS installed we leave it alone and the tests
    exercise against it. This keeps the documented standalone command
    (``python -m unittest discover -s tests``) truly dependency-free.
    """
    if name in sys.modules:
        return
    try:
        importlib.import_module(name)
        return  # the real package is available; use it
    except ImportError:
        sys.modules[name] = build()


def _build_boto3_stub():
    mod = types.ModuleType("boto3")

    class _Session:  # overridden per-test where behavior matters
        def __init__(self, *a, **k):
            pass

        def client(self, *a, **k):
            raise RuntimeError("boto3 stub: no live clients in offline tests")

        def get_credentials(self):
            return None

    mod.Session = _Session
    return mod


def _build_openai_stub():
    mod = types.ModuleType("openai")
    mod.OpenAI = type("OpenAI", (), {})
    return mod


# Must run BEFORE any production module is loaded below.
_stub_module_if_missing("boto3", _build_boto3_stub)
_stub_module_if_missing("openai", _build_openai_stub)


def _load(name, relpath):
    """Import a module from a file path under the Module 2 directory."""
    path = _MODULE_DIR / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


role = _load("bma_create_session_role", "scripts/create_session_role.py")
preflight = _load("bma_preflight", "preflight.py")
bma_common = _load("bma_common", "bma_common.py")


# A realistic, well-formed Runtime ARN used across the matcher tests.
RT = "arn:aws:bedrock-agentcore:us-east-1:610137094485:runtime/ACRuntime1-F0fkky4I5Y"
RT_OTHER = "arn:aws:bedrock-agentcore:us-east-1:610137094485:runtime/OtherRuntime-ZZ"


class RuntimeArnValidator(unittest.TestCase):
    """scripts/create_session_role.py :: _runtime_arn_match and friends."""

    def test_accepts_well_formed_arn(self):
        m = role._runtime_arn_match(RT)
        self.assertIsNotNone(m)
        self.assertEqual(role._runtime_region(RT), "us-east-1")
        self.assertEqual(role._runtime_account(RT), "610137094485")

    def test_accepts_govcloud_partition(self):
        arn = "arn:aws-us-gov:bedrock-agentcore:us-gov-west-1:123456789012:runtime/rt-1"
        self.assertIsNotNone(role._runtime_arn_match(arn))
        self.assertEqual(role._runtime_region(arn), "us-gov-west-1")

    def test_rejects_wildcard_in_id(self):
        self.assertIsNone(
            role._runtime_arn_match(
                "arn:aws:bedrock-agentcore:us-east-1:610137094485:runtime/*"
            )
        )
        self.assertIsNone(
            role._runtime_arn_match(
                "arn:aws:bedrock-agentcore:us-east-1:610137094485:runtime/rt-?"
            )
        )

    def test_rejects_non_12_digit_account(self):
        self.assertIsNone(
            role._runtime_arn_match(
                "arn:aws:bedrock-agentcore:us-east-1:12345:runtime/rt-1"
            )
        )
        self.assertIsNone(
            role._runtime_arn_match(
                "arn:aws:bedrock-agentcore:us-east-1:6101370944850000:runtime/rt-1"
            )
        )

    def test_rejects_wrong_service(self):
        self.assertIsNone(
            role._runtime_arn_match(
                "arn:aws:bedrock:us-east-1:610137094485:runtime/rt-1"
            )
        )

    def test_rejects_garbage_and_non_string(self):
        self.assertIsNone(role._runtime_arn_match("not-an-arn"))
        self.assertIsNone(role._runtime_arn_match(""))
        self.assertIsNone(role._runtime_arn_match(None))

    def test_strips_surrounding_whitespace(self):
        self.assertIsNotNone(role._runtime_arn_match(f"  {RT}\n"))

    def test_endpoint_arn_is_wildcard_child(self):
        self.assertEqual(
            role._runtime_endpoint_arn(RT), f"{RT}/runtime-endpoint/*"
        )


def _allow(action, resource, condition=None):
    stmt = {"Effect": "Allow", "Action": action, "Resource": resource}
    if condition is not None:
        stmt["Condition"] = condition
    return stmt


def _deny(action, resource, condition=None):
    stmt = {"Effect": "Deny", "Action": action, "Resource": resource}
    if condition is not None:
        stmt["Condition"] = condition
    return stmt


class RuntimeScopeMatcher(unittest.TestCase):
    """preflight.py :: _stmt_resource_covers_runtime / action matcher.

    These guard the "is this Allow actually scoped to MY Runtime?" verdict.
    The headline regression: a prefix match let runtime/rt-1 pass for
    runtime/rt-10 — the matcher must now require an exact (or endpoint-child)
    match.
    """

    def test_exact_arn_covers(self):
        self.assertIs(
            preflight._stmt_resource_covers_runtime(_allow("x", RT), RT), True
        )

    def test_wildcard_resource_covers(self):
        self.assertIs(
            preflight._stmt_resource_covers_runtime(_allow("x", "*"), RT), True
        )

    def test_endpoint_child_covers(self):
        child = f"{RT}/runtime-endpoint/DEFAULT"
        self.assertIs(
            preflight._stmt_resource_covers_runtime(_allow("x", child), RT), True
        )

    def test_different_runtime_does_not_cover(self):
        self.assertIs(
            preflight._stmt_resource_covers_runtime(_allow("x", RT_OTHER), RT),
            False,
        )

    def test_prefix_collision_rt1_vs_rt10(self):
        # The headline bug: a startswith() match let the shorter ARN pass for
        # the longer one. rt-1 must NOT cover rt-10, and vice versa.
        rt1 = "arn:aws:bedrock-agentcore:us-east-1:610137094485:runtime/rt-1"
        rt10 = "arn:aws:bedrock-agentcore:us-east-1:610137094485:runtime/rt-10"
        self.assertIs(
            preflight._stmt_resource_covers_runtime(_allow("x", rt1), rt10), False
        )
        self.assertIs(
            preflight._stmt_resource_covers_runtime(_allow("x", rt10), rt1), False
        )

    def test_no_runtime_arn_is_unknown(self):
        self.assertIsNone(
            preflight._stmt_resource_covers_runtime(_allow("x", RT), None)
        )

    def test_non_string_resource_ignored(self):
        self.assertIs(
            preflight._stmt_resource_covers_runtime(_allow("x", [123, RT]), RT),
            True,
        )
        self.assertIs(
            preflight._stmt_resource_covers_runtime(_allow("x", [123]), RT), False
        )

    def test_invoke_action_matcher(self):
        has = preflight._stmt_has_invoke_runtime_action
        self.assertTrue(has(_allow("bedrock-agentcore:InvokeAgentRuntime", RT)))
        self.assertTrue(has(_allow("bedrock-agentcore:*", RT)))
        self.assertTrue(has(_allow("*", RT)))
        self.assertTrue(
            has(_allow(["s3:GetObject", "bedrock-agentcore:InvokeAgentRuntime"], RT))
        )
        self.assertFalse(has(_allow("bedrock-agentcore:ListSessions", RT)))

    def test_inference_action_matcher(self):
        m = preflight._stmt_matches_inference
        self.assertTrue(m(_allow("bedrock-mantle:CreateInference", "*")))
        self.assertTrue(m(_allow("bedrock-mantle:*", "*")))
        self.assertTrue(m(_allow("*", "*")))
        self.assertFalse(m(_allow("bedrock-mantle:ListInferences", "*")))

    def test_deny_action_is_effect_agnostic(self):
        # The action matcher is effect-agnostic: it must report the action on a
        # Deny too, so the caller can let the Deny override an Allow.
        has = preflight._stmt_has_invoke_runtime_action
        self.assertTrue(
            has(_deny("bedrock-agentcore:InvokeAgentRuntime", RT))
        )


class ProfileCredentialsProvider(unittest.TestCase):
    """bma_common.py :: _ProfileCredentialsProvider binds the chosen profile.

    The bearer token must be minted under the profile the user selected, not
    the ambient default chain. We stub boto3 so no real credentials are
    needed and assert the Session is built with the exact profile_name.
    """

    def _stub_boto3(self, creds):
        captured = {}

        class _Session:
            def __init__(self, profile_name=None):
                captured["profile_name"] = profile_name

            def get_credentials(self):
                return creds

        stub = types.ModuleType("boto3")
        stub.Session = _Session
        return stub, captured

    def test_binds_named_profile(self):
        sentinel = object()
        stub, captured = self._stub_boto3(sentinel)
        saved = sys.modules.get("boto3")
        sys.modules["boto3"] = stub
        try:
            provider = bma_common._ProfileCredentialsProvider("bma-test")
            result = provider.load()
        finally:
            if saved is not None:
                sys.modules["boto3"] = saved
            else:
                sys.modules.pop("boto3", None)
        self.assertIs(result, sentinel)
        self.assertEqual(captured["profile_name"], "bma-test")

    def test_none_profile_uses_default_chain(self):
        sentinel = object()
        stub, captured = self._stub_boto3(sentinel)
        saved = sys.modules.get("boto3")
        sys.modules["boto3"] = stub
        try:
            provider = bma_common._ProfileCredentialsProvider(None)
            provider.load()
        finally:
            if saved is not None:
                sys.modules["boto3"] = saved
            else:
                sys.modules.pop("boto3", None)
        self.assertIsNone(captured["profile_name"])

    def test_missing_credentials_raises(self):
        stub, _ = self._stub_boto3(None)  # get_credentials() -> None
        saved = sys.modules.get("boto3")
        sys.modules["boto3"] = stub
        try:
            provider = bma_common._ProfileCredentialsProvider("bma-test")
            with self.assertRaises(RuntimeError):
                provider.load()
        finally:
            if saved is not None:
                sys.modules["boto3"] = saved
            else:
                sys.modules.pop("boto3", None)


class _FakeIam:
    """A minimal stand-in for the boto3 IAM client used by check_iam_config.

    It serves one role: a trust policy that Allows bedrock-mantle to assume it,
    plus a single inline policy whose statements the test supplies. Only the
    three calls check_iam_config makes are implemented.
    """

    _TRUST = {
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-mantle.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ]
    }

    def __init__(self, role_name, statements):
        self._role_name = role_name
        self._doc = {"Statement": statements}

    def get_role(self, RoleName):
        return {"Role": {"AssumeRolePolicyDocument": self._TRUST}}

    def list_role_policies(self, RoleName):
        return {"PolicyNames": ["BmaSession"]}

    def get_role_policy(self, RoleName, PolicyName):
        return {"PolicyName": PolicyName, "PolicyDocument": self._doc}


class _IamOnlyBoto3Stub(types.ModuleType):
    """boto3 stub whose Session(...).client('iam') returns a supplied fake."""

    def __init__(self, fake_iam):
        super().__init__("boto3")
        outer = self

        class _Session:
            def __init__(self, profile_name=None):
                pass

            def client(self, name):
                assert name == "iam"
                return outer._fake

        self._fake = fake_iam
        self.Session = _Session


def _run_check_iam_config(statements, runtime_arn):
    """Drive preflight.check_iam_config against a fake IAM client, offline.

    Patches boto3 so the lazy ``import boto3`` inside check_iam_config picks up
    our fake, and ensures botocore.exceptions.ClientError is importable.
    Returns the resulting Check.
    """
    role_arn = "arn:aws:iam::610137094485:role/BmaSessionRole-us-east-1"
    fake = _FakeIam(role_arn.split("/")[-1], statements)

    saved_boto3 = sys.modules.get("boto3")
    sys.modules["boto3"] = _IamOnlyBoto3Stub(fake)
    # botocore.exceptions.ClientError must be importable; install a stub only
    # if the real botocore is absent.
    saved_botocore = sys.modules.get("botocore")
    saved_exc = sys.modules.get("botocore.exceptions")
    if "botocore.exceptions" not in sys.modules:
        try:
            importlib.import_module("botocore.exceptions")
        except ImportError:
            botocore = types.ModuleType("botocore")
            exc_mod = types.ModuleType("botocore.exceptions")

            class ClientError(Exception):
                def __init__(self, response=None, operation_name=None):
                    super().__init__("ClientError")
                    self.response = response or {}

            exc_mod.ClientError = ClientError
            botocore.exceptions = exc_mod
            sys.modules["botocore"] = botocore
            sys.modules["botocore.exceptions"] = exc_mod
    try:
        return preflight.check_iam_config(role_arn, "bma-test", runtime_arn)
    finally:
        def _restore(name, value):
            if value is not None:
                sys.modules[name] = value
            else:
                sys.modules.pop(name, None)

        _restore("boto3", saved_boto3)
        _restore("botocore", saved_botocore)
        _restore("botocore.exceptions", saved_exc)


class ConditionalDenyPrecedence(unittest.TestCase):
    """preflight.check_iam_config :: an unevaluated condition is not masked.

    Regression for: a scoped unconditional Allow of InvokeAgentRuntime plus a
    condition-gated Deny on the same action was reporting "present and scoped"
    — hiding that effective authorization hinges on a condition this check
    does not evaluate. The unknown-scope caveat must win over the scoped-Allow
    note.
    """

    def _inference_allow(self):
        return _allow("bedrock-mantle:CreateInference", "*")

    def test_scoped_allow_plus_conditional_deny_reports_unknown(self):
        statements = [
            self._inference_allow(),
            _allow("bedrock-agentcore:InvokeAgentRuntime", RT),
            _deny(
                "bedrock-agentcore:InvokeAgentRuntime",
                RT,
                condition={"StringEquals": {"aws:SourceVpc": "vpc-123"}},
            ),
        ]
        check = _run_check_iam_config(statements, RT)
        self.assertEqual(check.status, preflight.CONFIRMED)
        self.assertIn("could not be confirmed", check.detail)
        self.assertNotIn("scoped to this Runtime", check.detail)

    def test_scoped_allow_only_reports_scoped(self):
        # The clean counterpart: a scoped Allow with no Deny still reads as
        # scoped (confirms the precedence change did not swallow the happy path).
        statements = [
            self._inference_allow(),
            _allow("bedrock-agentcore:InvokeAgentRuntime", RT),
        ]
        check = _run_check_iam_config(statements, RT)
        self.assertEqual(check.status, preflight.CONFIRMED)
        self.assertIn("scoped to this Runtime", check.detail)

    def test_unconditional_deny_wins_over_scoped_allow(self):
        statements = [
            self._inference_allow(),
            _allow("bedrock-agentcore:InvokeAgentRuntime", RT),
            _deny("bedrock-agentcore:InvokeAgentRuntime", RT),
        ]
        check = _run_check_iam_config(statements, RT)
        self.assertEqual(check.status, preflight.CONFIRMED)
        self.assertIn("Denies bedrock-agentcore:InvokeAgentRuntime", check.detail)


if __name__ == "__main__":
    unittest.main()
