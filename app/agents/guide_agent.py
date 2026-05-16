"""旅游攻略 Agent：联网检索 + 大模型撰写攻略."""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.agents.config import is_agent_configured, is_web_search_configured
from app.agents.llm import chat_completion, parse_json_from_llm
from app.agents.tools.image_search import find_cover_image
from app.agents.tools.web_search import web_search

logger = logging.getLogger(__name__)

GUIDE_AGENT_AUTHOR = "AI攻略助手"

SYSTEM_PROMPT = """你是一位专业的旅游攻略撰写助手。
根据用户提供的主题与联网检索到的参考资料，撰写一篇结构清晰、实用可操作的旅游攻略。
输出必须是合法 JSON，不要包含 markdown 代码块，字段如下：
{
  "title": "攻略标题",
  "summary": "一两句话摘要，不超过120字",
  "tags": ["标签1", "标签2"],
  "content": "正文 HTML，使用 <h2> <p> <ul><li> 等标签，不要包含 <html><body>"
}
要求：内容真实、实用，若参考资料不足请基于常识撰写并注明建议核实；不要编造具体票价与营业时间。"""


class GuideAgent:
    @staticmethod
    def is_ready() -> bool:
        return is_agent_configured()

    @staticmethod
    async def generate(
        topic: str,
        *,
        scenic_name: Optional[str] = None,
        location: Optional[str] = None,
        category: Optional[str] = None,
    ) -> dict[str, Any]:
        if not is_agent_configured():
            raise RuntimeError("攻略 Agent 未配置，请在环境变量中设置大模型相关项")

        topic = (topic or "").strip()
        if not topic:
            raise ValueError("攻略主题不能为空")

        search_query = f"{topic} 旅游攻略 行程 景点"
        if scenic_name:
            search_query = f"{scenic_name} {search_query}"

        search_results: list[dict] = []
        if is_web_search_configured():
            try:
                search_results = await web_search(search_query)
            except Exception as exc:
                logger.warning("联网搜索失败: %s", exc)

        refs_text = _format_search_results(search_results)
        user_prompt = _build_user_prompt(topic, scenic_name, category, refs_text)

        raw = await chat_completion(SYSTEM_PROMPT, user_prompt)
        try:
            data = parse_json_from_llm(raw)
        except Exception:
            data = {
                "title": topic,
                "summary": raw[:120] if raw else "",
                "tags": ["AI生成"],
                "content": f"<p>{raw}</p>" if raw else "<p>生成失败</p>",
            }

        tags = _normalize_tags(data.get("tags"))
        title = (data.get("title") or topic)[:200]

        cover = None
        try:
            cover = await find_cover_image(
                topic,
                tags=tags,
                scenic_name=scenic_name,
                location=location,
            )
        except Exception as exc:
            logger.warning("封面配图失败: %s", exc)

        return {
            "title": title,
            "topic": topic,
            "summary": (data.get("summary") or "")[:500],
            "tags": tags,
            "content": data.get("content") or "",
            "cover": cover,
            "author": GUIDE_AGENT_AUTHOR,
            "source": "agent",
            "searchUsed": bool(search_results),
        }


def _format_search_results(results: list[dict]) -> str:
    if not results:
        return "（未获取到联网参考资料，请结合主题与常识撰写。）"
    lines = []
    for i, item in enumerate(results, 1):
        title = item.get("title") or ""
        url = item.get("url") or ""
        snippet = (item.get("snippet") or "")[:500]
        lines.append(f"{i}. {title}\n   链接: {url}\n   摘要: {snippet}")
    return "\n\n".join(lines)


def _build_user_prompt(
    topic: str,
    scenic_name: Optional[str],
    category: Optional[str],
    refs_text: str,
) -> str:
    parts = [f"攻略主题：{topic}"]
    if scenic_name:
        parts.append(f"关联景点：{scenic_name}")
    if category:
        parts.append(f"分类偏好：{category}")
    parts.append(f"\n联网参考资料：\n{refs_text}")
    return "\n".join(parts)


def _normalize_tags(tags: Any) -> list[str]:
    if not tags:
        return ["AI生成", "智能攻略"]
    if isinstance(tags, str):
        return [tags, "AI生成"]
    return [str(t) for t in tags[:8] if t]
