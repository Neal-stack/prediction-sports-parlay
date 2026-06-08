from datetime import datetime, timezone
from typing import Optional

_last_odds_sync_at: Optional[datetime] = None
_last_odds_sync_count: int = 0
_last_odds_sync_error: Optional[str] = None
_last_games_source: Optional[str] = None


def record_odds_sync(count: int) -> None:
    global _last_odds_sync_at, _last_odds_sync_count, _last_odds_sync_error
    _last_odds_sync_at = datetime.now(timezone.utc)
    _last_odds_sync_count = count
    _last_odds_sync_error = None


def record_odds_sync_error(message: str) -> None:
    global _last_odds_sync_error
    _last_odds_sync_error = message


def record_games_source(source: str) -> None:
    global _last_games_source
    _last_games_source = source


def get_sync_state() -> dict:
    return {
        "last_odds_sync_at": _last_odds_sync_at,
        "last_odds_sync_count": _last_odds_sync_count,
        "last_odds_sync_error": _last_odds_sync_error,
        "games_source": _last_games_source,
    }
