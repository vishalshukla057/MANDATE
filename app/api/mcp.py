import json
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.agent.tools import get_tool_registry
from app.policy.engine import PolicyEngine
from app.services.audit import AuditService

router = APIRouter(prefix="/mcp", tags=["Model Context Protocol (MCP)"])


class MCPToolCallRequest(BaseModel):
    name: str = Field(..., description="Name of the tool to invoke")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Arguments dictionary matching the tool's inputSchema")


@router.get("/tools", summary="List MCP tools (GET)")
@router.post("/tools/list", summary="List MCP tools (POST)")
def list_mcp_tools():
    """Returns the tool registry formatted according to the Model Context Protocol (MCP) specification."""
    registry = get_tool_registry()
    mcp_tools = []
    for name, tool in registry.items():
        mcp_tools.append({
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.parameters,
            "metadata": {
                "risk_tier": tool.risk_tier,
                "governance": "deterministic_policy_guaranteed",
                "paypal_api_rail": "Orders v2 / Invoicing v2 / Payments v2",
            }
        })
    return {
        "tools": mcp_tools,
        "protocol_version": "2024-11-05",
        "server_info": {
            "name": "paypal-collections-copilot-mcp",
            "version": "2.4.0",
            "description": "PayPal Enterprise Mandate Protocol MCP Server for Autonomous Accounts Receivable",
        }
    }


@router.post("/tools/call", summary="Execute MCP tool call")
async def call_mcp_tool(payload: MCPToolCallRequest, db: Session = Depends(get_db)):
    """Executes a tool call through the Policy Engine and Merkle Audit Log, returning MCP standard content."""
    registry = get_tool_registry()
    tool = registry.get(payload.name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{payload.name}' not found in MCP registry")

    # Evaluate through Policy Engine
    policy_decision = PolicyEngine.evaluate(
        db=db,
        tool_name=tool.name,
        args=payload.arguments or {},
    )

    if policy_decision.is_blocked():
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "status": "blocked_by_policy",
                        "policy_decision": policy_decision.to_dict(),
                        "explanation": "; ".join(policy_decision.reasons),
                    }, indent=2)
                }
            ],
            "isError": True
        }

    # Execute handler
    try:
        result = await tool.handler(db=db, args=payload.arguments)
        
        # Record into Merkle audit log
        audit_entry = AuditService.record(
            db=db,
            actor="mcp_client",
            event=f"mcp_tool_executed_{tool.name}",
            entity_type="mcp_tool",
            payload={
                "tool_name": tool.name,
                "arguments": payload.arguments,
                "policy_decision": policy_decision.decision,
                "result_status": "success",
            }
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "result": result,
                        "policy_decision": policy_decision.to_dict(),
                        "merkle_block_id": audit_entry.id,
                        "tamper_evident_hash": audit_entry.hash,
                    }, indent=2)
                }
            ],
            "isError": False
        }
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"error": str(e), "tool": tool.name})
                }
            ],
            "isError": True
        }


@router.get("/sse", summary="MCP Server-Sent Events (SSE) Stream")
async def mcp_sse_stream():
    """SSE endpoint for standard MCP client discovery and keepalive."""
    async def event_generator():
        yield f"event: endpoint\ndata: /mcp/tools/call\n\n"
        init_data = json.dumps({
            "protocol": "mcp",
            "version": "2024-11-05",
            "server": "paypal-collections-copilot-mcp",
            "capabilities": {"tools": True, "prompts": False, "resources": False}
        })
        yield f"event: initialize\ndata: {init_data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

