from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "旅途智览 API"
    DEBUG: bool = True
    SECRET_KEY: str = "your-secret-key-change-in-production-2024"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    DATABASE_URL: str = "sqlite:///./data/travel.db"

    # 高德 Web 服务 Key（地点搜索 / POI 详情）
    AMAP_KEY: str = ""

    # 攻略 Agent — 大模型（OpenAI 兼容接口）
    GUIDE_AGENT_LLM_API_KEY: str = ""
    GUIDE_AGENT_LLM_BASE_URL: str = ""
    GUIDE_AGENT_LLM_MODEL: str = ""

    # 攻略 Agent — 联网搜索（tavily | serper）
    GUIDE_AGENT_WEB_SEARCH_API_KEY: str = ""
    GUIDE_AGENT_WEB_SEARCH_PROVIDER: str = "tavily"

    # 天气 API（OpenWeatherMap 兼容接口，用于智能推荐中获取目的地天气）
    WEATHER_API_KEY: str = ""
    WEATHER_API_BASE_URL: str = "https://api.openweathermap.org/data/2.5"

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

    class Config:
        env_file = str(Path(__file__).resolve().parent.parent.parent / ".env")
        extra = "ignore"


settings = Settings()
