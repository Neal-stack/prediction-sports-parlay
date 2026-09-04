from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Storage ---
    supabase_url: str = ""
    supabase_service_key: str = ""

    # --- Odds source (primary: The Odds API free tier) ---
    odds_api_key: str = ""
    # Legacy paid aggregator. Used only if odds_api_key is unset.
    sharpapi_key: str = ""

    # --- Player stats (NBA). Free tier key from balldontlie.io ---
    balldontlie_api_key: str = ""

    # --- Optional legacy keys (no longer required; ESPN replaces them) ---
    api_sports_key: str = ""  # fallback score source only
    gnews_api_key: str = ""  # deprecated; ESPN news is the default

    # --- AI (Gemini is the primary, free engine) ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    openai_api_key: str = ""  # optional fallback only
    openai_model: str = "gpt-4o-mini"

    # --- App ---
    cors_origins: str = "http://localhost:3000"
    use_demo_data: bool = False

    # --- Tuning / budget ---
    odds_sync_minutes: int = 15
    research_ttl_minutes: int = 30
    enable_player_props: bool = True
    # Book prop lines cost ~4 credits per game (markets x regions), so cap how
    # many games we price per generation. 8 games = ~32 credits.
    prop_line_max_games: int = 8
    use_book_prop_lines: bool = True

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def odds_source(self) -> Optional[str]:
        if self.odds_api_key:
            return "odds_api"
        if self.sharpapi_key:
            return "sharpapi"
        return None

    @property
    def has_live_pipeline(self) -> bool:
        return bool(self.odds_source and self.supabase_url and self.supabase_service_key)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key or self.openai_api_key)

    @property
    def ai_provider(self) -> Optional[str]:
        if self.gemini_api_key and self.openai_api_key:
            return "gemini+openai"
        if self.gemini_api_key:
            return "gemini"
        if self.openai_api_key:
            return "openai"
        return None


settings = Settings()
