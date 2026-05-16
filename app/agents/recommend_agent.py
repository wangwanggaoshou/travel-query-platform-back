"""智能推荐 Agent：严格按用户需求从库内筛选并由大模型精选最多 3 个景点。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agents.config import is_agent_configured, is_web_search_configured
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

RANK_SYSTEM_PROMPT = """你是旅途智览的智能旅行推荐助手。
用户会给出出发地、旅行类型标签、预算、出行天数和自定义描述。你会收到一批候选景点（可能来自数据库或新发现）。
请严格挑选与用户真实需求最匹配的景点，最多推荐 3 个；不相关的一律不要选。
须综合出发地、预算与出行天数评估交通耗时、往返费用与行程是否可行（短途优先周边/同省，长途才推荐远途目的地）。
每个推荐必须写 matchReason：50～120 字，明确结合出发地、标签、预算、天数或自定义描述说明「为什么适合您」，不要泛泛而谈。
输出合法 JSON，不要 markdown 代码块：
{
  "picks": [
    {"scenicId": 1, "matchReason": "结合用户需求的具体理由…"}
  ],
  "summary": "一句整体说明"
}
若无合适景点，picks 返回空数组。"""

SUGGEST_NAMES_PROMPT = """你是旅途智览的智能旅行推荐助手。
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
    ) -> dict[str, Any]:
        limit = min(max(1, limit), MAX_RECOMMEND)
        departure_city = (departure_city or "").strip()
        custom_prompt = (custom_prompt or "").strip()
        travel_styles = [s.strip() for s in (travel_styles or []) if s and s.strip()]
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
                seen_ids={s.id for s in candidates},
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
            final_list.append(RecommendAgent._to_payload(item, reason))
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
    ) -> list[Scenic]:
        categories: list[str] = []
        for style in travel_styles:
            categories.extend(TRAVEL_STYLE_TO_CATEGORY.get(style, []))
        categories = list(set(categories))

        seen: set[int] = set()
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
        catalog = []
        for s in candidates:
            desc = (s.description or "")[:280]
            catalog.append({
                "scenicId": s.id,
                "name": s.name,
                "category": CATEGORY_LABELS.get(s.category or "", s.category or ""),
                "location": s.location or "",
                "price": s.price,
                "summary": desc,
            })

        user_msg = (
            f"{user_context}\n\n"
            f"请从以下候选景点中严格挑选最多 {limit} 个最符合用户需求的，"
            f"并为每个写出结合上述需求的 matchReason：\n"
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
    def _to_payload(item: Scenic, match_reason: str) -> dict:
        return {
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
