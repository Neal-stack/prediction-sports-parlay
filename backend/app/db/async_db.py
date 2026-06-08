import asyncio
from typing import Callable, TypeVar

T = TypeVar("T")


async def run_sync(fn: Callable[[], T]) -> T:
    """Run blocking Supabase calls without blocking the event loop."""
    return await asyncio.to_thread(fn)
