from sqlalchemy.orm import Session
from typing import Optional
import asyncio
import logging

from app.agents.config import is_web_search_configured, is_weather_configured
from app.agents.recommend_agent import RecommendAgent, MAX_RECOMMEND
from app.agents.tools.web_search import web_search
from app.services.scenic_discover import try_discover_scenic_async
from app.models.scenic import Scenic
from app.models.guide import Guide
from app.utils.response import success, error

logger = logging.getLogger(__name__)


class RecommendService:
    @staticmethod
    def agent_status() -> dict:
        return success({
            "configured": RecommendAgent.is_ready(),
            "webSearchConfigured": is_web_search_configured(),
        })

    @staticmethod
    async def agent_recommend(
        db: Session,
        *,
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: Optional[str] = None,
        limit: int = 3,
    ) -> dict:
        if not RecommendAgent.is_ready():
            return error(3003, "推荐 Agent 未配置，请设置 GUIDE_AGENT_LLM_API_KEY 与 GUIDE_AGENT_LLM_BASE_URL")

        departure_city = (departure_city or "").strip()
        if len(departure_city) < 2:
            return error(400, "请填写出发地")

        if not travel_styles and not (custom_prompt or "").strip():
            return error(400, "请至少选择旅行类型或填写自定义需求")

        try:
            payload = await RecommendAgent.recommend(
                db,
                departure_city=departure_city,
                travel_styles=travel_styles,
                budget_min=budget_min,
                budget_max=budget_max,
                days=days,
                custom_prompt=custom_prompt,
                limit=limit,
            )
        except RuntimeError as exc:
            return error(3003, str(exc))
        except Exception as exc:
            return error(500, f"智能推荐失败: {exc}")

        return success(payload)

    @staticmethod
    async def agent_recommend_more(
        db: Session,
        *,
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: Optional[str] = None,
        limit: int = 3,
        exclude_ids: Optional[list[int]] = None,
    ) -> dict:
        if not RecommendAgent.is_ready():
            return error(3003, "推荐 Agent 未配置，请设置 GUIDE_AGENT_LLM_API_KEY 与 GUIDE_AGENT_LLM_BASE_URL")

        departure_city = (departure_city or "").strip()
        if len(departure_city) < 2:
            return error(400, "请填写出发地")

        if not travel_styles and not (custom_prompt or "").strip():
            return error(400, "请至少选择旅行类型或填写自定义需求")

        try:
            payload = await RecommendAgent.recommend(
                db,
                departure_city=departure_city,
                travel_styles=travel_styles,
                budget_min=budget_min,
                budget_max=budget_max,
                days=days,
                custom_prompt=custom_prompt,
                limit=limit,
                exclude_ids=set(exclude_ids or []),
            )
        except RuntimeError as exc:
            return error(3003, str(exc))
        except Exception as exc:
            return error(500, f"智能推荐失败: {exc}")

        return success(payload)

    @staticmethod
    def get_scenic_recommend(db: Session, user_id: Optional[int] = None, limit: int = 10) -> dict:
        items = db.query(Scenic).filter(
            Scenic.is_active == 1, Scenic.is_hot == 1
        ).order_by(Scenic.view_count.desc(), Scenic.id.desc()).limit(limit).all()

        scenic_list = [{
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "location": item.location,
            "price": item.price,
            "image": item.image,
            "description": item.description,
            "matchReason": "热门推荐",
        } for item in items]
        return success({"list": scenic_list})

    @staticmethod
    def get_guide_recommend(db: Session, user_id: Optional[int] = None, limit: int = 10) -> dict:
        items = db.query(Guide).filter(
            Guide.is_active == 1, Guide.is_hot == 1
        ).order_by(Guide.view_count.desc()).limit(limit).all()

        guide_list = [{
            "id": item.id,
            "title": item.title,
            "cover": item.cover,
            "summary": item.summary,
            "author": item.author,
            "tags": item.tags or [],
            "date": str(item.created_at.date()) if item.created_at else None,
            "matchReason": "热门推荐"
        } for item in items]
        return success({"list": guide_list})

    @staticmethod
    async def agent_recommend_stream(
        db: Session,
        *,
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: Optional[str] = None,
        limit: int = 3,
    ):
        try:
            async for chunk in RecommendService._agent_recommend_stream_impl(
                db,
                departure_city=departure_city,
                travel_styles=travel_styles,
                budget_min=budget_min,
                budget_max=budget_max,
                days=days,
                custom_prompt=custom_prompt,
                limit=limit,
            ):
                yield chunk
        except Exception as exc:
            logger.exception("智能推荐流式输出出错")
            yield {"error": f"智能推荐失败: {exc}"}

    @staticmethod
    async def _agent_recommend_stream_impl(
        db: Session,
        *,
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: Optional[str] = None,
        limit: int = 3,
    ):
        if not RecommendAgent.is_ready():
            yield {"error": "推荐 Agent 未配置，请设置 GUIDE_AGENT_LLM_API_KEY 与 GUIDE_AGENT_LLM_BASE_URL"}
            return

        departure_city = (departure_city or "").strip()
        if len(departure_city) < 2:
            yield {"error": "请填写出发地"}
            return

        if not travel_styles and not (custom_prompt or "").strip():
            yield {"error": "请至少选择旅行类型或填写自定义需求"}
            return

        # Step 0: Gather candidates
        yield {"step": 0, "progress": 10, "message": "正在筛选库内候选景点..."}
        await asyncio.sleep(0.05)

        limit = min(max(1, limit), MAX_RECOMMEND)
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
            limit=limit,
            exclude_ids=set(),
        )
        from_web = 0

        # 立即反馈候选结果，避免前端一直显示"正在筛选…"
        yield {"step": 0, "progress": 15, "message": f"已筛选 {len(candidates)} 个库内候选景点，正在分析匹配度..."}
        await asyncio.sleep(0.05)

        # Step 1 & 2: Discover if needed
        if len(candidates) < limit:
            place_names: list[str] = []
            search_results: list[dict] = []

            # 快速检查：联网搜索是否可用，不可用则直接跳过避免无意义等待
            if not is_web_search_configured():
                yield {"step": 1, "progress": 25, "message": "联网搜索未配置，直接使用库内候选 + AI 分析..."}
                await asyncio.sleep(0.1)
            else:
                yield {"step": 0, "progress": 20, "message": "发现库内景点较少，正在联网搜索目的地建议..."}

                query = RecommendAgent._build_search_query(
                    departure_city, travel_styles, budget_min, budget_max, days, custom_prompt
                )
                try:
                    search_results = await asyncio.wait_for(
                        web_search(query, max_results=6), timeout=12.0
                    )
                except (asyncio.TimeoutError, Exception):
                    yield {"step": 0, "progress": 25, "message": "联网搜索超时，降级为库内 + AI 常识推荐..."}
                    await asyncio.sleep(0.1)

                yield {"step": 1, "progress": 35, "message": "AI 已建议目的地，正在通过高德与维基抓取详情..."}
                try:
                    place_names, summary = await asyncio.wait_for(
                        RecommendAgent._llm_suggest_names(
                            user_context, search_results, limit - len(candidates)
                        ), timeout=45.0
                    )
                except (asyncio.TimeoutError, Exception) as exc:
                    logger.warning("LLM 建议地名超时/失败: %s", exc)
                    if custom_prompt:
                        place_names = [custom_prompt[:40]]

            # Parallel discovery（无论联网搜索是否配置，都执行）
            names_to_try = [n.strip() for n in place_names[:limit - len(candidates) + 1] if len(n.strip()) >= 2]
            if names_to_try:
                yield {"step": 2, "progress": 50, "message": f"正在联网发现景点「{'、'.join(names_to_try)}」数据..."}
                tasks = [try_discover_scenic_async(db, name) for name in names_to_try]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        continue
                    scenic, created = result
                    if not scenic:
                        continue
                    if created:
                        tags = list(scenic.tags or [])
                        if "智能推荐" not in tags:
                            tags.append("智能推荐")
                            scenic.tags = tags
                            db.commit()
                            db.refresh(scenic)
                    if scenic.id not in {s.id for s in candidates}:
                        candidates.append(scenic)
                        if created:
                            from_web += 1
                        if len(candidates) >= limit:
                            break
        else:
            yield {"step": 2, "progress": 50, "message": "库内景点充足，正在准备整合推荐计划..."}
            await asyncio.sleep(0.3)

        if not candidates:
            yield {
                "done": True,
                "progress": 100,
                "message": "已完成！",
                "result": {
                    "code": 200,
                    "data": {
                        "list": [],
                        "fromDatabase": 0,
                        "fromWeb": 0,
                        "summary": "未找到与您需求相符的景点，请调整标签或描述后重试",
                        "agentUsed": True,
                        "webSearchConfigured": is_web_search_configured(),
                    }
                }
            }
            return

        # Step 3: Fetch weather
        yield {"step": 3, "progress": 70, "message": "正在获取目的地未来天气预报，对齐行程穿搭..."}

        weather_map = {}
        if is_weather_configured():
            city_weather = {}
            unique_locs = {}
            for s in candidates:
                city_key = RecommendAgent._shorten_location(s.location or s.name)
                if city_key not in unique_locs:
                    unique_locs[city_key] = s.id

            tasks = {
                city: RecommendAgent._fetch_weather(city, days)
                for city in unique_locs
            }
            try:
                weather_results = await asyncio.wait_for(
                    asyncio.gather(*tasks.values(), return_exceptions=True),
                    timeout=20.0,
                )
            except asyncio.TimeoutError:
                logger.warning("天气查询整体超时")
                weather_results = [None] * len(tasks)
            for city, result in zip(tasks.keys(), weather_results):
                city_weather[city] = None if isinstance(result, Exception) else result

            for s in candidates:
                city_key = RecommendAgent._shorten_location(s.location or s.name)
                weather_map[s.id] = city_weather.get(city_key)
        else:
            yield {"step": 3, "progress": 72, "message": "天气服务未配置，跳过实时天气查询..."}
            await asyncio.sleep(0.1)

        # Step 4: AI Rank & Trip Plan
        yield {"step": 4, "progress": 85, "message": "AI 正在生成精选行程推荐和建议细节..."}

        try:
            picks, summary = await asyncio.wait_for(
                RecommendAgent._llm_rank_picks(candidates, user_context, limit, days),
                timeout=90.0,
            )
        except asyncio.TimeoutError:
            logger.warning("LLM 排序超时，使用默认摘要")
            picks, summary = [], RecommendAgent._default_summary(
                departure_city, travel_styles, custom_prompt, 0
            )
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

        final_data = {
            "list": final_list,
            "fromDatabase": max(0, len(final_list) - from_web),
            "fromWeb": min(from_web, len(final_list)),
            "summary": summary or RecommendAgent._default_summary(
                departure_city, travel_styles, custom_prompt, len(final_list)
            ),
            "agentUsed": True,
            "webSearchConfigured": is_web_search_configured(),
        }
        yield {"done": True, "progress": 100, "message": "行程规划推荐生成成功！", "result": {"code": 200, "data": final_data}}

