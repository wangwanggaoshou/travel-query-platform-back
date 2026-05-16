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

    # 攻略 Agent — 联网搜索（tavily | serper | google，google/serper 均走 Serper 的 Google 搜索）
    GUIDE_AGENT_WEB_SEARCH_API_KEY: str = ""
    GUIDE_AGENT_WEB_SEARCH_PROVIDER: str = "tavily"

    # 可选：主搜索为 tavily 时，单独配置 Serper Key 以启用 Google 图片爬取
    GUIDE_AGENT_GOOGLE_API_KEY: str = ""

    # 可选：Google 自定义搜索（Programmable Search Engine）图片
    GOOGLE_CSE_API_KEY: str = ""
    GOOGLE_CSE_CX: str = ""

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
