import sys
from contextlib import AsyncExitStack
from typing import Dict, Any, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import create_model, Field
from langchain_core.tools import StructuredTool


def _make_input_model(tool_name: str, input_schema: dict):
    """Dynamically build a Pydantic model from a JSON Schema dict."""
    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    fields: Dict[str, Any] = {}
    properties = input_schema.get("properties", {})
    required_set = set(input_schema.get("required", []))

    for prop_name, prop_info in properties.items():
        python_type = type_map.get(prop_info.get("type", "string"), str)
        description = prop_info.get("description", "")
        if prop_name in required_set:
            fields[prop_name] = (python_type, Field(description=description))
        else:
            fields[prop_name] = (Optional[python_type], Field(None, description=description))

    # Pydantic requires at least one field
    if not fields:
        fields["_noop"] = (Optional[str], Field(None, description="No arguments required"))

    return create_model(f"{tool_name}Input", **fields)


class MCPToolProvider:
    """Manages connections to MCP servers over stdio and maps their tools to LangChain StructuredTools."""

    def __init__(self):
        self.exit_stack = AsyncExitStack()
        self.sessions: List[ClientSession] = []
        self.langchain_tools: List[StructuredTool] = []

    async def connect_to_server(self, python_script_path: str, server_name: str):
        """Connect to a Python stdio MCP server and register its tools."""
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[python_script_path],
            env=None,
        )

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        read, write = stdio_transport
        session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self.sessions.append(session)

        response = await session.list_tools()

        for mcp_tool in response.tools:
            full_name = f"{server_name}_{mcp_tool.name}"
            input_schema = mcp_tool.inputSchema if hasattr(mcp_tool, "inputSchema") and mcp_tool.inputSchema else {}
            args_model = _make_input_model(full_name, input_schema)

            # Capture variables in closure
            def make_coroutine(sess: ClientSession, t_name: str):
                async def _call(**kwargs) -> str:
                    try:
                        # Remove internal noop field if present
                        kwargs.pop("_noop", None)
                        result = await sess.call_tool(t_name, arguments=kwargs)
                        if not result.content:
                            return ""
                        return "\n".join(c.text for c in result.content if hasattr(c, "text"))
                    except Exception as exc:
                        return f"MCP Tool Error: {exc}"
                return _call

            coroutine = make_coroutine(session, mcp_tool.name)

            structured_tool = StructuredTool.from_function(
                coroutine=coroutine,
                name=full_name,
                description=mcp_tool.description or f"MCP tool: {mcp_tool.name}",
                args_schema=args_model,
                return_direct=False,
            )
            self.langchain_tools.append(structured_tool)

    async def disconnect_all(self):
        await self.exit_stack.aclose()

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a specific tool by server + tool name."""
        target_name = f"{server_name}_{tool_name}"
        for t in self.langchain_tools:
            if t.name == target_name:
                return await t.ainvoke(arguments)
        return f"Error: Tool '{target_name}' not found."

    def get_tools(self) -> List[StructuredTool]:
        return self.langchain_tools


mcp_provider = MCPToolProvider()
