"""Memory monitoring middleware for request tracking.

This middleware tracks memory usage before and after requests to help
identify memory-intensive endpoints and potential memory leaks.
"""

from fastapi import Request
from loguru import logger

from app.core.system_memory import get_memory_snapshot


async def memory_monitoring_middleware(request: Request, call_next):
    """Track memory usage before and after request handling.
    
    Logs memory snapshots for requests that might be memory-intensive,
    especially those that could cause OOM issues.
    """
    # Skip memory monitoring for health checks and static assets
    path = request.url.path
    if path in ("/health", "/healthz", "/", "/docs", "/redoc", "/openapi.json"):
        return await call_next(request)
    
    # Get memory before request
    memory_before = get_memory_snapshot()
    
    try:
        response = await call_next(request)
        
        # Get memory after request
        memory_after = get_memory_snapshot()
        memory_delta = memory_after.rss_mb - memory_before.rss_mb
        
        # Log if memory increased significantly (>10MB) or if memory is high
        if memory_delta > 10.0 or memory_after.rss_mb > 350:
            logger.warning(
                f"[MEMORY] Request {request.method} {path} - "
                f"Memory: {memory_before.rss_mb:.1f}MB -> {memory_after.rss_mb:.1f}MB "
                f"(delta: {memory_delta:+.1f}MB)"
            )
        
        # Log critical memory usage
        if memory_after.rss_mb > 450:
            logger.error(
                f"[MEMORY] CRITICAL: Request {request.method} {path} - "
                f"Memory usage is {memory_after.rss_mb:.1f}MB / 512MB limit - OOM risk!"
            )
        
        return response
    except Exception as e:
        # Get memory after error
        memory_after = get_memory_snapshot()
        memory_delta = memory_after.rss_mb - memory_before.rss_mb
        
        logger.error(
            f"[MEMORY] Request {request.method} {path} failed - "
            f"Memory: {memory_before.rss_mb:.1f}MB -> {memory_after.rss_mb:.1f}MB "
            f"(delta: {memory_delta:+.1f}MB), Error: {type(e).__name__}"
        )
        raise
