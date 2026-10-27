import json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mcp_handler import MCPHandler


class TestMCPHandler(unittest.TestCase):
    def setUp(self):
        self.handler = MCPHandler({
            "hello": {
                "handler": lambda args: {"message": "hello " + args.get("name", "world")},
                "description": "Test tool",
                "inputSchema": {"type": "object", "properties": {}},
            }
        })

    def call(self, method, params=None):
        return json.loads(self.handler(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})))

    def test_initialize(self):
        self.assertEqual(self.call("initialize")["result"]["serverInfo"]["name"], "mc-iclone8-mcp")

    def test_list_tools(self):
        self.assertEqual(self.call("tools/list")["result"]["tools"][0]["name"], "hello")

    def test_call_tool(self):
        response = self.call("tools/call", {"name": "hello", "arguments": {"name": "iClone"}})
        content = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(content["message"], "hello iClone")

    def test_unknown_tool(self):
        response = self.call("tools/call", {"name": "missing"})
        self.assertIn("Unknown tool", response["error"]["message"])
        self.assertEqual(response["id"], 1)

    def test_tool_error_is_a_result_with_the_request_id(self):
        def fail(_args):
            raise ValueError("No avatar in scene")
        self.handler.tools["fail"] = {"handler": fail, "description": "", "inputSchema": {"type": "object"}}
        response = self.call("tools/call", {"name": "fail"})
        self.assertEqual(response["id"], 1)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["content"][0]["text"], "ValueError: No avatar in scene")

    def test_image_is_returned_as_image_content(self):
        self.handler.tools["shot"] = {"handler": lambda args: {"path": "x.png", "_image": {"data": "AAAA", "mimeType": "image/png"}}, "description": "", "inputSchema": {"type": "object"}}
        content = self.call("tools/call", {"name": "shot"})["result"]["content"]
        self.assertEqual(json.loads(content[0]["text"]), {"path": "x.png"})
        self.assertEqual(content[1], {"type": "image", "data": "AAAA", "mimeType": "image/png"})

    def test_reload_tools_replaces_the_registry(self):
        # reload_tools runs through the iClone main-thread dispatcher; run it inline here.
        sys.modules["dispatch"] = types.SimpleNamespace(run=lambda function, timeout=120: function())
        self.addCleanup(sys.modules.pop, "dispatch")
        fresh = {"other": {"handler": lambda args: {}, "description": "", "inputSchema": {"type": "object"}}}
        handler = MCPHandler({}, reload_tools=lambda: dict(fresh))
        result = json.loads(handler(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "reload_tools"}})))
        self.assertEqual(json.loads(result["result"]["content"][0]["text"])["tool_count"], 2)
        self.assertEqual(sorted(handler.tools), ["other", "reload_tools"])


if __name__ == "__main__":
    unittest.main()
