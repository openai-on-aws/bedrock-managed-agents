import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cad_workflow import broker
from cad_workflow.contract import request


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.parameters = request({"job_id": "a" * 32, "stock_thickness_mm": 14, "pocket_depth_mm": 12})
        self.context = SimpleNamespace(client_context=SimpleNamespace(
            custom={"bedrockAgentCoreToolName": "cad___create_support"}))

    def test_invalid_dimensions_never_create_cloud_clients(self):
        with patch.object(broker, "clients") as factory:
            with self.assertRaises(ValueError):
                broker.handler({**self.parameters, "pocket_depth_mm": 14}, self.context)
            factory.assert_not_called()

    def test_unknown_tool_and_path_like_job_are_rejected_before_cloud_access(self):
        with patch.object(broker, "clients") as factory:
            with self.assertRaises(ValueError):
                broker.handler({}, SimpleNamespace(client_context=None))
            self.context.client_context.custom["bedrockAgentCoreToolName"] = "cad___get_result"
            with self.assertRaises(ValueError):
                broker.handler({"job_id": "../../other-customer"}, self.context)
            factory.assert_not_called()

    def test_existing_verified_job_does_not_dispatch_again(self):
        client, ssm = Mock(), Mock()
        accepted = {"status": "accepted", "accepted": True}
        with patch.object(broker, "read_json", side_effect=[self.parameters, accepted]):
            self.assertEqual(broker.create(client, ssm, "test-bucket", self.parameters), accepted)
        client.put_object.assert_not_called()
        ssm.send_command.assert_not_called()

    def test_job_cannot_be_reused_with_different_dimensions(self):
        client, ssm = Mock(), Mock()
        with patch.object(broker, "read_json", return_value={**self.parameters, "pocket_depth_mm": 10}):
            with self.assertRaises(ValueError):
                broker.create(client, ssm, "test-bucket", self.parameters)
        client.put_object.assert_not_called()
        ssm.send_command.assert_not_called()

    def test_dispatch_sends_only_validated_data_to_fixed_resources(self):
        client, ssm = Mock(), Mock()
        ssm.send_command.return_value = {"Command": {"CommandId": "synthetic-command"}}
        accepted = {"status": "accepted", "accepted": True}
        with patch.object(broker, "read_json", side_effect=[None, None]), \
             patch.object(broker, "result", return_value=accepted), \
             patch.dict(broker.os.environ, {"CAD_INSTANCE": "i-synthetic", "CAD_DOCUMENT": "fixed-doc",
                                           "CAD_LOG_GROUP": "/cad/synthetic"}):
            self.assertEqual(broker.create(client, ssm, "test-bucket", self.parameters), accepted)
        arguments = ssm.send_command.call_args.kwargs
        self.assertEqual(arguments["DocumentName"], "fixed-doc")
        self.assertEqual(arguments["InstanceIds"], ["i-synthetic"])
        self.assertEqual(set(arguments["Parameters"]), {"RequestBase64"})
        decoded = json.loads(base64.b64decode(arguments["Parameters"]["RequestBase64"][0]))
        self.assertEqual(decoded, self.parameters)
        self.assertEqual(client.put_object.call_args.kwargs["IfNoneMatch"], "*")
