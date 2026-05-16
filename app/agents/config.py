"""攻略 Agent 配置（请在 .env 中填写，未配置时 Agent 不可用）."""

from app.config import settings


def is_llm_configured() -> bool:
    return bool(settings.GUIDE_AGENT_LLM_API_KEY and settings.GUIDE_AGENT_LLM_BASE_URL)


def is_web_search_configured() -> bool:
    return bool(settings.GUIDE_AGENT_WEB_SEARCH_API_KEY)


def is_google_images_configured() -> bool:
    from app.agents.tools.image_search import _google_serper_api_key, _is_google_cse_configured

    return bool(_google_serper_api_key() or _is_google_cse_configured())


def is_agent_configured() -> bool:
    return is_llm_configured()
