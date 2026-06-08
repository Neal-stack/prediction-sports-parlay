from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_service_key: str = ""
    sharpapi_key: str = ""
    api_sports_key: str = ""
    gnews_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    cors_origins: str = "http://localhost:3000"
    use_demo_data: bool = False

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_live_pipeline(self) -> bool:
        return bool(self.sharpapi_key and self.supabase_url and self.supabase_service_key)

    @property
    def dual_ai_enabled(self) -> bool:
        return bool(self.gemini_api_key and self.openai_api_key)

    @property
    def ai_provider(self) -> Optional[str]:
        if self.dual_ai_enabled:
            return "gemini+openai"
        if self.gemini_api_key:
            return "gemini"
        if self.openai_api_key:
            return "openai"
        return None


settings = Settings()
