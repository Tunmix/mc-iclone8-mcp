import json


class MCPHandler:
    def __init__(self, tools, reload_tools=None):
        self.tools = tools
        self.reload_tools = reload_tools
        if reload_tools is not None:
            self.tools["reload_tools"] = self._reload_entry()

    def _reload_entry(self):
        return {"handler": self._reload, "main_thread": True, "description": "Reloads the tool modules from disk without restarting the server. Use after editing files in tools/.", "inputSchema": {"type": "object", "properties": {}}}

    def _reload(self, _args):
        tools = self.reload_tools()
        tools["reload_tools"] = self._reload_entry()
        self.tools = tools
        return {"status": "ok", "tool_count": len(tools)}

    @staticmethod
    def _tool_result(response):
        # A handler may attach a picture under "_image"; MCP clients show it as image content.
        image = response.pop("_image", None) if isinstance(response, dict) else None
        content = [{"type": "text", "text": json.dumps(response, ensure_ascii=False)}]
        if image:
            content.append({"type": "image", "data": image["data"], "mimeType": image["mimeType"]})
        return {"content": content}

    def __call__(self, body):
        request_id = None
        try:
            request = json.loads(body)
            request_id = request.get("id")
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "mc-iclone8-mcp", "version": "0.1.0"}}
            elif method == "tools/list":
                result = {"tools": [{"name": name, "description": meta["description"], "inputSchema": meta["inputSchema"]} for name, meta in self.tools.items()]}
            elif method == "tools/call":
                params = request.get("params", {})
                name = params.get("name")
                if name not in self.tools:
                    raise ValueError("Unknown tool: %s" % name)
                tool = self.tools[name]
                arguments = params.get("arguments", {})
                try:
                    if tool.get("main_thread"):
                        from dispatch import run
                        response = run(lambda: tool["handler"](arguments))
                    else:
                        response = tool["handler"](arguments)
                except Exception as error:
                    # Tool failures are results, not protocol errors, so the agent sees the message.
                    result = {"content": [{"type": "text", "text": "%s: %s" % (type(error).__name__, error)}], "isError": True}
                else:
                    result = self._tool_result(response)
            elif method == "ping":
                result = {}
            else:
                raise ValueError("Unsupported method: %s" % method)
            return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False)
        except Exception as error:
            return json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(error)}}, ensure_ascii=False)
