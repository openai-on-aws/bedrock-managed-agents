import base64
import gzip
import importlib.util
import json
from pathlib import Path
import unittest

from cad_workflow.backends import get_backend
from cad_workflow.contract import digest, json_bytes
from cad_workflow.infrastructure import lambda_source, package_sources, render


class PackagingTests(unittest.TestCase):
    def test_backend_cannot_load_catia_or_arbitrary_code(self):
        for name in ("catia-v6", "../../custom.py", "os", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                get_backend(name)

    def test_worker_bundle_contains_executable_backend_modules(self):
        backend = get_backend()
        encoded, checksum = package_sources(backend)
        decoded = gzip.decompress(base64.b64decode(encoded))
        self.assertEqual(digest(decoded), checksum)
        files = json.loads(decoded)
        for module in (backend.AUTHOR_MODULE, backend.VERIFIER_MODULE):
            path = module.replace(".", "/") + ".py"
            self.assertIn(path, files)
            self.assertIsNotNone(importlib.util.find_spec(module))
        self.assertIn(f"from {backend.AUTHOR_MODULE} import main", files["author.FCMacro"])
        self.assertIn(f"from {backend.VERIFIER_MODULE} import main", files["verifier.FCMacro"])
        for name, text in files.items():
            self.assertTrue(name.endswith((".py", ".FCMacro")))
            compile(text, name, "exec")

    def test_lambda_runs_without_the_worker_package(self):
        namespace = {"__name__": "packaged_lambda"}
        exec(compile(lambda_source(), "index.py", "exec"), namespace)
        self.assertTrue(callable(namespace["handler"]))
        parameters = namespace["request"]({
            "job_id": "a" * 32, "stock_thickness_mm": 14, "pocket_depth_mm": 12,
        })
        self.assertEqual(namespace["json_bytes"](parameters), json_bytes(parameters))
        self.assertNotIn("from cad_workflow", lambda_source())

    def test_native_launcher_accepts_only_fixed_phases(self):
        backend = get_backend()
        with self.assertRaises(ValueError):
            backend.command("../../shell", Path("/work"), Path("/opt/code"))
        command = backend.command("author", Path("/work"), Path("/opt/code"))
        self.assertEqual(command[-1], "/opt/code/author.FCMacro")
        self.assertIn("/opt/freecad/AppRun", command)

    def test_both_templates_fit_inline_limits_and_resolve_network_references(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                template = render(use_existing_network=existing)
                self.assertLessEqual(len(json_bytes(template)), 51200)
                worker = template["Resources"]["Worker"]
                userdata = worker["Properties"]["UserData"]["Fn::Base64"]["Fn::Sub"]
                self.assertLessEqual(len(userdata.encode()), 16 * 1024)
                self.assertNotIn("@@SOURCE", userdata)
                subnet = worker["Properties"]["NetworkInterfaces"][0]["SubnetId"]["Ref"]
                self.assertIn(subnet, template["Parameters"] if existing else template["Resources"])
                self.assertEqual("Vpc" in template["Resources"], not existing)
                self.assertEqual(template["Outputs"]["CadBackend"]["Value"], "freecad")
