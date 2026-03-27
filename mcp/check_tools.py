import nova_server_v2

tools = nova_server_v2.mcp._tool_manager.list_tools()
print(f"Total tools registered: {len(tools)}")
for t in tools:
    print(f"  - {t.name}")