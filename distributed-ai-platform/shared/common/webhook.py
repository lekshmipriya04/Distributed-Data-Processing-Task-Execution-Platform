"""
Utility to fire webhooks on async job completion.
"""
import httpx
import structlog
import asyncio

logger = structlog.get_logger(__name__)

async def fire_webhook(url: str, payload: dict) -> None:
    if not url:
        return
    
    async def _post():
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload, timeout=5.0)
                logger.info("webhook_fired", url=url)
        except Exception as e:
            logger.error("webhook_failed", url=url, error=str(e))
            
    # Fire and forget
    asyncio.create_task(_post())
