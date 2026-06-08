from functools import lru_cache
from typing import Optional

from supabase import Client, create_client

from app.config import settings


@lru_cache
def get_supabase() -> Optional[Client]:
    if not settings.supabase_url or not settings.supabase_service_key:
        return None
    return create_client(settings.supabase_url, settings.supabase_service_key)
