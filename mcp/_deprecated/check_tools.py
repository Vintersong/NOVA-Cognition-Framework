import nova_server

tools = nova_server.mcp._tool_manager.list_tools()
print(f"Total tools registered: {len(tools)}")
for t in tools:
    print(f"  - {t.name}")