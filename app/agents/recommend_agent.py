"""智能推荐 Agent：以"可执行行程"为核心，结合出行方式、耗时、天气、衣物、住宿等维度生成行程预案。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agents.config import is_agent_configured, is_web_search_configured, is_weather_configured
from app.agents.llm import chat_completion, parse_json_from_llm
from app.agents.tools.web_search import web_search
from app.models.scenic import Scenic
from app.services.scenic_discover import try_discover_scenic_async

logger = logging.getLogger(__name__)

MAX_RECOMMEND = 3
CANDIDATE_POOL = 24

TRAVEL_STYLE_TO_CATEGORY: dict[str, list[str]] = {
    "自然风光": ["nature"],
    "历史古迹": ["history"],
    "海滨度假": ["beach"],
    "城市观光": ["city"],
    "主题乐园": ["theme_park"],
    "山岳徒步": ["mountain", "nature"],
    "山岳景观": ["mountain"],
    "文化体验": ["history", "city"],
    "美食之旅": ["city"],
}

CATEGORY_LABELS = {
    "nature": "自然风光",
    "history": "历史古迹",
    "beach": "海滨度假",
    "city": "城市观光",
    "theme_park": "主题乐园",
    "mountain": "山岳景观",
    "none": "暂无分类",
}

RANK_SYSTEM_PROMPT = """你是旅途智览的智能旅行规划助手。
用户会给出出发地、旅行偏好、预算与出行天数，你会收到一批候选景点（含天气提示）。

你的核心任务是为用户生成一份可执行的行程预案，而不仅仅是筛选景点。对每个推荐景点，必须包含以下完整信息：

1. matchReason（50～120字）：结合出发地、偏好、预算与天数说明推荐理由，并自然带出门票花费（如"门票仅40元""免费参观""无需购票"等）。
2. tripPlan：一份可直接执行的行程预案，必须覆盖以下五个维度：
   - transportation：推荐出行方式（高铁/飞机/自驾/大巴/拼车等）、预估单程耗时、往返交通费用估算
   - weather：根据提供的天气提示或季节常识，描述目的地近期天气概况（温度、晴雨、湿度），并给出出行适宜度判断
   - clothing：根据天气与活动场景（爬山/海边/城市漫步等）给出具体穿搭建议
   - accommodation：住宿类型建议（酒店/民宿/青旅/度假村）与每晚预算参考
   - itinerary：按天列出的简明日程，每天 1-3 项核心活动，确保节奏合理、可执行

行程可行性约束：
- 短途（1-3天）优先推荐周边/同省目的地，长途（5天+）才考虑远途
- 总费用（往返交通 + 住宿 + 门票 + 餐饮）须在用户预算范围内
- 行程节奏与出行天数匹配，避免过度紧凑或松散

输出合法 JSON（不要 markdown 代码块）：
{
  "picks": [
    {
      "scenicId": 1,
      "matchReason": "结合用户需求的具体理由…",
      "tripPlan": {
        "transportation": {"mode": "高铁", "duration": "约3小时", "costEstimate": "往返约600元"},
        "weather": "10月中旬气温10-20℃，晴间多云，适宜出行",
        "clothing": "薄外套+长裤，早晚温差大建议带冲锋衣",
        "accommodation": "景区周边舒适型酒店，约200-300元/晚",
        "itinerary": ["Day1: 到达+入住+周边漫步", "Day2: 主景区深度游"]
      }
    }
  ],
  "summary": "整体行程概览说明，概括推荐思路与总预算范围"
}
若无合适景点，picks 返回空数组。"""

SUGGEST_NAMES_PROMPT = """你是旅途智览的智能旅行规划助手。
根据用户出发地、旅行偏好、预算与出行天数，推荐从该出发地出发可合理到达的国内景点/目的地名称，用于后续入库。
必须严格贴合用户需求，不要推荐无关热门地；短途行程勿推荐过远目的地。
输出合法 JSON：
{
  "placeNames": ["名称1", "名称2"],
  "summary": "一句说明"
}
placeNames 最多 3 个，仅中国境内真实景点或景区。"""


class RecommendAgent:
    @staticmethod
    def is_ready() -> bool:
        return is_agent_configured()

    @staticmethod
    async def recommend(
        db: Session,
        *,
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: Optional[str] = None,
        limit: int = MAX_RECOMMEND,
        exclude_ids: Optional[set[int]] = None,
    ) -> dict[str, Any]:
        limit = min(max(1, limit), MAX_RECOMMEND)
        departure_city = (departure_city or "").strip()
        custom_prompt = (custom_prompt or "").strip()
        travel_styles = [s.strip() for s in (travel_styles or []) if s and s.strip()]
        exclude_ids = exclude_ids or set()
        user_context = RecommendAgent._format_user_context(
            departure_city, travel_styles, budget_min, budget_max, days, custom_prompt
        )

        candidates = RecommendAgent._gather_candidates(
            db,
            departure_city=departure_city,
            travel_styles=travel_styles,
            budget_min=budget_min,
            budget_max=budget_max,
            days=days,
            custom_prompt=custom_prompt,
            exclude_ids=exclude_ids,
        )
        from_web = 0

        if len(candidates) < limit:
            discovered, _ = await RecommendAgent._discover_for_user(
                db,
                user_context=user_context,
                departure_city=departure_city,
                travel_styles=travel_styles,
                budget_min=budget_min,
                budget_max=budget_max,
                days=days,
                custom_prompt=custom_prompt,
                need=limit - len(candidates),
                seen_ids={s.id for s in candidates} | exclude_ids,
            )
            for scenic, created in discovered:
                candidates.append(scenic)
                if created:
                    from_web += 1

        if not candidates:
            return {
                "list": [],
                "fromDatabase": 0,
                "fromWeb": from_web,
                "summary": "未找到与您需求相符的景点，请调整标签或描述后重试",
                "agentUsed": True,
                "webSearchConfigured": is_web_search_configured(),
            }

        picks, summary = await RecommendAgent._llm_rank_picks(candidates, user_context, limit)

        scenic_by_id = {s.id: s for s in candidates}
        final_list = []
        for pick in picks:
            sid = pick.get("scenicId")
            reason = (pick.get("matchReason") or "").strip()
            if sid not in scenic_by_id or not reason:
                continue
            item = scenic_by_id[sid]
            trip_plan = pick.get("tripPlan") if isinstance(pick.get("tripPlan"), dict) else None
            final_list.append(RecommendAgent._to_payload(item, reason, trip_plan))
            if len(final_list) >= limit:
                break

        return {
            "list": final_list,
            "fromDatabase": max(0, len(final_list) - from_web),
            "fromWeb": min(from_web, len(final_list)),
            "summary": summary or RecommendAgent._default_summary(
                departure_city, travel_styles, custom_prompt, len(final_list)
            ),
            "agentUsed": True,
            "webSearchConfigured": is_web_search_configured(),
        }

    @staticmethod
    def _format_user_context(
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: str,
    ) -> str:
        lines = [
            f"出发地：{departure_city}",
            f"旅行类型标签：{', '.join(travel_styles) if travel_styles else '未选择'}",
            f"预算范围：{budget_min:.0f}～{budget_max:.0f} 元（含交通、住宿、门票等综合预估）",
            f"出行天数：{days} 天",
            f"自定义需求：{custom_prompt if custom_prompt else '无'}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _apply_budget(query, budget_min: float, budget_max: float):
        if budget_max > 0:
            query = query.filter(or_(Scenic.price == 0, Scenic.price <= budget_max))
        if budget_min > 0:
            query = query.filter(or_(Scenic.price == 0, Scenic.price >= budget_min))
        return query

    @staticmethod
    def _gather_candidates(
        db: Session,
        *,
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: str,
        exclude_ids: Optional[set[int]] = None,
    ) -> list[Scenic]:
        categories: list[str] = []
        for style in travel_styles:
            categories.extend(TRAVEL_STYLE_TO_CATEGORY.get(style, []))
        categories = list(set(categories))

        exclude_ids = exclude_ids or set()
        seen: set[int] = set(exclude_ids)
        items: list[Scenic] = []

        def add_rows(rows: list[Scenic]) -> None:
            for row in rows:
                if row.id not in seen:
                    seen.add(row.id)
                    items.append(row)

        if custom_prompt and categories:
            like = f"%{custom_prompt}%"
            q = (
                db.query(Scenic)
                .filter(Scenic.is_active == 1)
                .filter(Scenic.category.in_(categories))
                .filter(
                    or_(
                        Scenic.name.like(like),
                        Scenic.location.like(like),
                        Scenic.description.like(like),
                        Scenic.address.like(like),
                    )
                )
            )
            q = RecommendAgent._apply_budget(q, budget_min, budget_max)
            add_rows(q.order_by(Scenic.view_count.desc(), Scenic.id.desc()).limit(CANDIDATE_POOL).all())

        if custom_prompt:
            like = f"%{custom_prompt}%"
            q = (
                db.query(Scenic)
                .filter(Scenic.is_active == 1)
                .filter(
                    or_(
                        Scenic.name.like(like),
                        Scenic.location.like(like),
                        Scenic.description.like(like),
                        Scenic.address.like(like),
                    )
                )
            )
            q = RecommendAgent._apply_budget(q, budget_min, budget_max)
            add_rows(q.order_by(Scenic.view_count.desc(), Scenic.id.desc()).limit(CANDIDATE_POOL).all())

        if categories and not custom_prompt:
            q = db.query(Scenic).filter(Scenic.is_active == 1, Scenic.category.in_(categories))
            q = RecommendAgent._apply_budget(q, budget_min, budget_max)
            add_rows(q.order_by(Scenic.view_count.desc(), Scenic.id.desc()).limit(CANDIDATE_POOL).all())

        if departure_city:
            like_dep = f"%{departure_city}%"
            q = (
                db.query(Scenic)
                .filter(Scenic.is_active == 1)
                .filter(
                    or_(
                        Scenic.location.like(like_dep),
                        Scenic.address.like(like_dep),
                        Scenic.name.like(like_dep),
                    )
                )
            )
            q = RecommendAgent._apply_budget(q, budget_min, budget_max)
            pool = CANDIDATE_POOL if days > 3 else max(8, CANDIDATE_POOL // 2)
            add_rows(q.order_by(Scenic.view_count.desc(), Scenic.id.desc()).limit(pool).all())

        return items[:CANDIDATE_POOL]

    @staticmethod
    async def _discover_for_user(
        db: Session,
        *,
        user_context: str,
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: str,
        need: int,
        seen_ids: set[int],
    ) -> tuple[list[tuple[Scenic, bool]], str]:
        if need <= 0:
            return [], ""

        place_names: list[str] = []
        summary = ""

        search_results: list[dict] = []
        if is_web_search_configured():
            query = RecommendAgent._build_search_query(
                departure_city, travel_styles, budget_min, budget_max, days, custom_prompt
            )
            try:
                search_results = await web_search(query, max_results=6)
            except Exception as exc:
                logger.warning("推荐联网搜索失败: %s", exc)

        try:
            place_names, summary = await RecommendAgent._llm_suggest_names(
                user_context, search_results, need
            )
        except Exception as exc:
            logger.warning("推荐地名生成失败: %s", exc)
            if custom_prompt:
                place_names = [custom_prompt[:40]]

        discovered: list[tuple[Scenic, bool]] = []
        for name in place_names[:need + 1]:
            name = name.strip()
            if len(name) < 2:
                continue
            scenic, created = await try_discover_scenic_async(db, name)
            if not scenic or scenic.id in seen_ids:
                continue
            if created:
                tags = list(scenic.tags or [])
                if "智能推荐" not in tags:
                    tags.append("智能推荐")
                    scenic.tags = tags
                    db.commit()
                    db.refresh(scenic)
            discovered.append((scenic, created))
            seen_ids.add(scenic.id)
            if len(discovered) >= need:
                break

        return discovered, summary

    @staticmethod
    async def _llm_rank_picks(
        candidates: list[Scenic],
        user_context: str,
        limit: int,
    ) -> tuple[list[dict], str]:
        # 并行获取所有候选景点的天气信息
        weather_map: dict[int, Optional[str]] = {}
        if is_weather_configured():
            import asyncio
            tasks = {s.id: RecommendAgent._fetch_weather(s.location or s.name) for s in candidates}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for sid, result in zip(tasks.keys(), results):
                if isinstance(result, Exception):
                    weather_map[sid] = None
                else:
                    weather_map[sid] = result

        catalog = []
        for s in candidates:
            desc = (s.description or "")[:280]
            weather_hint = weather_map.get(s.id) or "（季节常识推断）"
            catalog.append({
                "scenicId": s.id,
                "name": s.name,
                "category": CATEGORY_LABELS.get(s.category or "", s.category or ""),
                "location": s.location or "",
                "price": s.price,
                "summary": desc,
                "weatherHint": weather_hint,
            })

        user_msg = (
            f"{user_context}\n\n"
            f"请从以下候选景点中挑选最多 {limit} 个最符合用户需求的，"
            f"并为每个生成完整的行程预案（含出行方式、天气、衣物、住宿、日程）：\n"
            f"{catalog}"
        )

        raw = await chat_completion(RANK_SYSTEM_PROMPT, user_msg)
        data = parse_json_from_llm(raw)
        picks = data.get("picks") or []
        if not isinstance(picks, list):
            picks = []
        summary = (data.get("summary") or "").strip()
        return picks, summary

    @staticmethod
    async def _llm_suggest_names(
        user_context: str,
        search_results: list[dict],
        need: int,
    ) -> tuple[list[str], str]:
        refs = "\n".join(
            f"- {(r.get('title') or '')}: {(r.get('snippet') or '')[:180]}"
            for r in search_results[:5]
        ) or "（无联网摘要）"

        user_msg = (
            f"{user_context}\n\n"
            f"需要约 {min(need, MAX_RECOMMEND)} 个最匹配的国内景点名称。\n"
            f"联网资料：\n{refs}"
        )
        raw = await chat_completion(SUGGEST_NAMES_PROMPT, user_msg)
        data = parse_json_from_llm(raw)
        names = data.get("placeNames") or []
        if isinstance(names, str):
            names = [names]
        summary = (data.get("summary") or "").strip()
        return [str(n).strip() for n in names if n and str(n).strip()], summary

    @staticmethod
    def _build_search_query(
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: str,
    ) -> str:
        parts = [f"从{departure_city}出发"]
        if custom_prompt:
            parts.append(custom_prompt)
        if travel_styles:
            parts.append(" ".join(travel_styles[:4]))
        parts.append(f"{days}天")
        if budget_max > 0:
            parts.append(f"预算{budget_min:.0f}-{budget_max:.0f}元")
        parts.append("国内旅游景点推荐")
        return " ".join(parts)

    @staticmethod
    async def _fetch_weather(location: str) -> Optional[str]:
        """获取目的地天气信息（需配置 WEATHER_API_KEY）。"""
        if not is_weather_configured():
            return None
        try:
            import httpx
            from app.config import settings

            url = f"{settings.WEATHER_API_BASE_URL}/weather"
            params = {
                "q": location,
                "appid": settings.WEATHER_API_KEY,
                "units": "metric",
                "lang": "zh_cn",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    weather = data.get("weather", [{}])[0]
                    main = data.get("main", {})
                    temp = main.get("temp", "?")
                    desc = weather.get("description", "未知")
                    humidity = main.get("humidity")
                    wind = data.get("wind", {})
                    wind_speed = wind.get("speed")
                    parts = [f"{desc}，气温{temp}°C"]
                    if humidity is not None:
                        parts.append(f"湿度{humidity}%")
                    if wind_speed is not None:
                        parts.append(f"风速{wind_speed}m/s")
                    return "，".join(parts)
                else:
                    logger.warning("天气查询失败(%s): HTTP %s", location, resp.status_code)
        except Exception as exc:
            logger.warning("天气查询异常(%s): %s", location, exc)
        return None

    @staticmethod
    def _to_payload(item: Scenic, match_reason: str, trip_plan: Optional[dict] = None) -> dict:
        payload = {
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "region": item.region,
            "location": item.location,
            "price": item.price,
            "image": item.image,
            "description": item.description,
            "tags": item.tags or [],
            "matchReason": match_reason,
        }
        if trip_plan:
            payload["tripPlan"] = trip_plan
        return payload

    @staticmethod
    def _default_summary(
        departure_city: str,
        travel_styles: list[str],
        custom_prompt: str,
        count: int,
    ) -> str:
        if count == 0:
            return "未找到与您需求高度匹配的景点，请调整条件后重试"
        hint = custom_prompt[:36] if custom_prompt else "、".join(travel_styles[:3])
        return f"已从{departure_city}出发，结合您的预算与行程（{hint}）精选 {count} 处最匹配景点。"
