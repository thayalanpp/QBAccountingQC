"""Validate MCP discovery and a real read query through the local QC service."""
import asyncio
import json
import os
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    if len(os.getenv('QC_API_TOKEN',''))<32:
        raise SystemExit('Set QC_API_TOKEN to the same token used by the running QC API')
    adapter=Path(__file__).with_name('mcp_adapter.py')
    params=StdioServerParameters(command=sys.executable,args=[str(adapter)],env=dict(os.environ))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools=await session.list_tools()
            print('MCP tools found:', len(tools.tools))
            assert len(tools.tools)==7
            result=await session.call_tool('get_account_balance',dict(company='dntl',account='22200',fy=2025))
            if result.isError:
                print('MCP query failed:')
                for c in result.content:
                    if hasattr(c,'text'): print(c.text)
                raise SystemExit(1)
            print('MCP query succeeded:')
            payload = result.structuredContent
            if payload is None:
                payload = json.loads(next(c.text for c in result.content if hasattr(c, 'text')))
            print(json.dumps(payload,indent=2))

if __name__=='__main__': asyncio.run(main())
