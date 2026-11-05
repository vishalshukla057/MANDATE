from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.services.feed import feed_broker

router = APIRouter(prefix="/feed", tags=["Feed"])


@router.get("/stream")
async def stream_feed():
    """Server-Sent Events endpoint streaming agent actions, approvals, payments, and policy decisions."""
    return StreamingResponse(
        feed_broker.subscribe(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("")
def get_feed_history(limit: int = 50):
    """Retrieve recent feed events."""
    return {"items": feed_broker.get_history(limit=limit)}

