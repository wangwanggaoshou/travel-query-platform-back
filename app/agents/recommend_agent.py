"""智能推荐 Agent：以"可执行行程"为核心，结合出行方式、耗时、天气、衣物、住宿等维度生成行程预案。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agents.config import is_agent_configured, is_weather_configured, is_web_search_configured
from app.agents.llm import chat_completion, parse_json_from_llm
from app.models.scenic import Scenic
from app.services.scenic_discover import try_discover_scenic_async

logger = logging.getLogger(__name__)

MAX_RECOMMEND = 3

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

重要规则：
- 用户消息中会明确指出「当前日期」和「Day1 从哪天开始」，行程首日为 Day1（明天）。
- weatherHint 中的日期为真实天气预报日期（来自 OpenWeatherMap），必须直接引用，严禁假设或编造日期（如"假设7月1日"）。将 weatherHint 中的日期与 Day1-DayN 逐日对齐：Day1 对应预报第一天，Day2 对应第二天，以此类推。
- itinerary 中每天标注真实日期和星期，如「Day1(6.25周四·晴): 到达+外滩夜景」。

你的核心任务是为用户生成一份可执行的行程预案，而不仅仅是筛选景点。对每个推荐景点，必须包含以下完整信息：

1. matchReason（50～120字）：结合出发地、偏好、预算与天数说明推荐理由，并自然带出门票花费（如"门票仅40元""免费参观""无需购票"等）。
2. tripPlan：一份可直接执行的行程预案，必须覆盖以下五个维度：
   - transportation：推荐出行方式（高铁/飞机/自驾/大巴/拼车等）、预估单程耗时、往返交通费用估算
   - weather：直接引用 weatherHint 中的真实日期和天气数据，按天描述变化趋势并给出出行适宜度判断。格式示例：「Day1(6.25周四)：多云，22~30°C，湿度70%，适合傍晚户外；Day2(6.26周五)：阵雨，20~27°C，降水概率60%，建议室内备选」
   - clothing：根据逐日天气变化与活动场景（爬山/海边/城市漫步等）给出具体穿搭建议，如遇温差大需提示叠穿
   - accommodation：住宿类型建议（酒店/民宿/青旅/度假村）与每晚预算参考
   - itinerary：按天列出的简明日程，每天 1-3 项核心活动，格式为「Day1(真实日期+天气): 具体活动」。日程安排需与当天天气严格匹配

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
        "weather": "Day1(6.25周四)：晴，22~30°C，湿度55%，适宜户外；Day2(6.26周五)：多云转阵雨，20~27°C，降水概率60%，建议备雨具；Day3(6.27周六)：晴，21~31°C，适合主景区深度游",
        "clothing": "Day1-3 建议短袖+薄外套叠穿，Day2 需备雨衣/折叠伞",
        "accommodation": "景区周边舒适型酒店，约200-300元/晚",
        "itinerary": ["Day1(6.25周四·晴): 到达+入住+城市漫步赏夜景", "Day2(6.26周五·阵雨): 室内文化场馆+美食街", "Day3(6.27周六·晴): 主景区全天深度游"]
      }
    }
  ],
  "summary": "整体行程概览说明，概括推荐思路与总预算范围"
}
若无合适景点，picks 返回空数组。"""

AI_RECOMMEND_PROMPT = """你是旅途智览的智能旅行规划助手。
根据用户的出发地、旅行偏好、预算与出行天数，直接推荐 3～5 个真实存在的国内景点。

关键规则：
- 只推荐真实存在的中国境内景点/景区/目的地
- 严格贴合用户需求，包括偏好和排除项（如"不去三亚""避开人多的地方"等必须遵守）
- 短途（1-3天）优先推荐周边/同省目的地；长途（5天+）可考虑跨省远途
- 每个景点的名称、所在城市必须真实准确
- 优先推荐有知名度、适合旅游的景点

输出合法 JSON（不要 markdown 代码块）：
{
  "spots": [
    {
      "name": "景点中文名称（如：鼓浪屿）",
      "location": "所在城市（如：厦门市）",
      "matchReason": "80～150字推荐理由，结合出发地、偏好、预算、天数说明为什么推荐",
      "description": "60～120字景点简介，突出特色和游玩亮点",
      "category": "nature / beach / mountain / history / city / theme_park",
      "imageQuery": "用于搜索配图的英文关键词（如：Gulangyu Island Xiamen）"
    }
  ],
  "summary": "一句话概括推荐思路"
}
若无合适景点，spots 返回空数组。"""


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
        """AI-first 推荐：先让 AI 理解需求推荐景点，再查库/爬取，最后生成行程。"""
        limit = min(max(1, limit), MAX_RECOMMEND)
        departure_city = (departure_city or "").strip()
        custom_prompt = (custom_prompt or "").strip()
        travel_styles = [s.strip() for s in (travel_styles or []) if s and s.strip()]
        exclude_ids = exclude_ids or set()

        user_context = RecommendAgent._format_user_context(
            departure_city, travel_styles, budget_min, budget_max, days, custom_prompt
        )

        # Step 1: AI 直接根据用户需求推荐景点
        ai_spots = await RecommendAgent._ai_suggest_spots(user_context, limit)

        if not ai_spots:
            return {
                "list": [], "fromDatabase": 0, "fromWeb": 0,
                "summary": "AI 未找到与您需求匹配的景点，请调整描述后重试",
                "agentUsed": True, "webSearchConfigured": is_web_search_configured(),
            }

        # Step 2: 查库匹配 + 缺失爬取
        candidates, from_web = await RecommendAgent._resolve_spots(
            db, ai_spots, exclude_ids, limit
        )

        if not candidates:
            return {
                "list": [], "fromDatabase": 0, "fromWeb": from_web,
                "summary": "未找到与您需求相符的景点，请调整标签或描述后重试",
                "agentUsed": True, "webSearchConfigured": is_web_search_configured(),
            }

        # Step 3: LLM 排序 + 生成行程预案
        picks, summary = await RecommendAgent._llm_rank_picks(candidates, user_context, limit, days)

        scenic_by_id = {s.id: s for s in candidates}
        final_list = []
        for pick in picks:
            sid = pick.get("scenicId")
            reason = (pick.get("matchReason") or "").strip()
            if sid not in scenic_by_id or not reason:
                continue
            item = scenic_by_id[sid]
            # 如果 LLM 排序没给 matchReason，用 AI 推荐时生成的
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
    async def _ai_suggest_spots(user_context: str, limit: int) -> list[dict]:
        """调用 LLM 直接根据用户需求推荐景点名称与信息。"""
        user_msg = (
            f"{user_context}\n\n"
            f"请推荐最多 {limit} 个最符合用户需求的中国境内真实景点，"
            f"必须严格贴合用户的偏好和排除项。"
        )
        raw = await chat_completion(AI_RECOMMEND_PROMPT, user_msg)
        data = parse_json_from_llm(raw)
        spots = data.get("spots") or []
        if isinstance(spots, dict):
            spots = [spots]
        # 基本校验
        valid = []
        for s in spots:
            if not isinstance(s, dict):
                continue
            name = (s.get("name") or "").strip()
            location = (s.get("location") or "").strip()
            if not name:
                continue
            valid.append({
                "name": name,
                "location": location,
                "matchReason": (s.get("matchReason") or "").strip(),
                "description": (s.get("description") or "").strip(),
                "category": (s.get("category") or "none").strip(),
                "imageQuery": (s.get("imageQuery") or name).strip(),
            })
        return valid[:limit]

    @staticmethod
    async def _resolve_spots(
        db: Session,
        ai_spots: list[dict],
        exclude_ids: set[int],
        limit: int,
    ) -> tuple[list[Scenic], int]:
        """查 DB 模糊匹配 + 缺失的联网爬取，返回 Scenic 对象列表。"""
        candidates: list[Scenic] = []
        seen_ids: set[int] = set(exclude_ids)
        from_web = 0

        for spot in ai_spots:
            if len(candidates) >= limit:
                break

            name = spot["name"]
            location = spot.get("location", "")

            # 1) 模糊匹配库内已有景点（按名称）
            existing = RecommendAgent._fuzzy_find_scenic(db, name, location, seen_ids)
            if existing:
                # 合并 AI 生成的 matchReason 和 description（如果库内没有更好的）
                if spot.get("matchReason"):
                    setattr(existing, "_ai_match_reason", spot["matchReason"])
                if spot.get("description") and not existing.description:
                    existing.description = spot["description"]
                candidates.append(existing)
                seen_ids.add(existing.id)
                continue

            # 2) 库内没有 → 联网爬取
            try:
                scenic, created = await try_discover_scenic_async(
                    db, name, city=location if location else None
                )
            except Exception:
                logger.warning("爬取景点失败: %s (%s)", name, location)
                continue

            if not scenic or scenic.id in seen_ids:
                continue

            if created:
                tags = list(scenic.tags or [])
                if "AI推荐" not in tags:
                    tags.append("AI推荐")
                    scenic.tags = tags
                    db.commit()
                    db.refresh(scenic)
                from_web += 1

            # 合并 AI 信息
            if spot.get("matchReason"):
                setattr(scenic, "_ai_match_reason", spot["matchReason"])
            if spot.get("description") and not scenic.description:
                scenic.description = spot["description"]
            if spot.get("category") and spot["category"] != "none" and not scenic.category:
                scenic.category = spot["category"]

            candidates.append(scenic)
            seen_ids.add(scenic.id)

        return candidates, from_web

    @staticmethod
    def _fuzzy_find_scenic(
        db: Session, name: str, location: str, exclude_ids: set[int]
    ) -> Optional[Scenic]:
        """模糊匹配库内景点：先精确名称，再 LIKE，再按地点+名称组合。"""
        # 精确匹配名称
        q = (
            db.query(Scenic)
            .filter(Scenic.is_active == 1, Scenic.name == name)
            .filter(~Scenic.id.in_(exclude_ids) if exclude_ids else True)
        )
        row = q.first()
        if row:
            return row

        # 模糊匹配名称
        like_name = f"%{name}%"
        q = (
            db.query(Scenic)
            .filter(Scenic.is_active == 1, Scenic.name.like(like_name))
            .filter(~Scenic.id.in_(exclude_ids) if exclude_ids else True)
        )
        row = q.first()
        if row:
            return row

        # 按地点 + 名关键词（取 name 前两个字）
        if location and len(name) >= 2:
            keyword = name[:2]
            like_key = f"%{keyword}%"
            like_loc = f"%{location}%"
            q = (
                db.query(Scenic)
                .filter(Scenic.is_active == 1)
                .filter(Scenic.name.like(like_key))
                .filter(
                    or_(
                        Scenic.location.like(like_loc),
                        Scenic.address.like(like_loc),
                    )
                )
                .filter(~Scenic.id.in_(exclude_ids) if exclude_ids else True)
            )
            row = q.first()
            if row:
                return row

        return None

    @staticmethod
    def _format_user_context(
        departure_city: str,
        travel_styles: list[str],
        budget_min: float,
        budget_max: float,
        days: int,
        custom_prompt: str,
    ) -> str:
        import datetime
        today = datetime.date.today()
        WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        today_str = f"{today.year}年{today.month}月{today.day}日（{WEEKDAY_CN[today.weekday()]}）"
        tomorrow = today + datetime.timedelta(days=1)
        tomorrow_str = f"{tomorrow.month}月{tomorrow.day}日（{WEEKDAY_CN[tomorrow.weekday()]}）"
        lines = [
            f"当前日期：{today_str}（行程从明天 {tomorrow_str} 开始算 Day1）",
            f"出发地：{departure_city}",
            f"旅行类型标签：{', '.join(travel_styles) if travel_styles else '未选择'}",
            f"预算范围：{budget_min:.0f}～{budget_max:.0f} 元（含交通、住宿、门票等综合预估）",
            f"出行天数：{days} 天",
            f"自定义需求：{custom_prompt if custom_prompt else '无'}",
        ]
        return "\n".join(lines)

    @staticmethod
    async def _llm_rank_picks(
        candidates: list[Scenic],
        user_context: str,
        limit: int,
        days: int = 3,
    ) -> tuple[list[dict], str]:
        # 并行获取所有候选景点的天气信息（同城市去重）
        weather_map: dict[int, Optional[str]] = {}
        if is_weather_configured():
            import asyncio
            # 按城市去重，减少冗余 API 调用
            city_weather: dict[str, Optional[str]] = {}
            unique_locs: dict[str, str] = {}  # city_key → scenic_id 的代表
            for s in candidates:
                city_key = RecommendAgent._shorten_location(s.location or s.name)
                if city_key not in unique_locs:
                    unique_locs[city_key] = s.id

            tasks = {
                city: RecommendAgent._fetch_weather(city, days)
                for city in unique_locs
            }
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for city, result in zip(tasks.keys(), results):
                city_weather[city] = None if isinstance(result, Exception) else result

            for s in candidates:
                city_key = RecommendAgent._shorten_location(s.location or s.name)
                weather_map[s.id] = city_weather.get(city_key)

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
            f"重要：weatherHint 中的日期为 OpenWeatherMap 实时预报的真实日期，请直接引用，严禁编造或假设日期。\n"
            f"Day1 对应明天（行程首日），Day2 为第二天，以此类推。请将 weatherHint 中的日期与 Day1-Day{days} 逐日对齐。\n\n"
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
    def _shorten_location(location: str) -> str:
        """精简地名：厦门市思明区 → 厦门 / 成都市锦江区 → 成都，提高 OWM 地理编码命中率。"""
        loc = (location or "").strip()
        # 去掉"区/县/镇/乡"后缀
        for suffix in ("区", "县", "镇", "乡", "街道", "自治州"):
            if loc.endswith(suffix):
                # 找上一级地名分隔符
                for sep in ("市", "省", "地区"):
                    if sep in loc:
                        idx = loc.rfind(sep)
                        shortened = loc[:idx + 1]
                        if len(shortened) >= 2:
                            return shortened
        # 若含"市"，截到市名
        if "市" in loc:
            return loc.split("市")[0] + "市"
        return loc

    @staticmethod
    async def _fetch_weather(location: str, days: int = 3) -> Optional[str]:
        """获取目的地未来 N 天逐日天气预报（需配置 WEATHER_API_KEY；免费版最多 5 天）。"""
        if not is_weather_configured():
            return None
        try:
            import datetime
            from collections import Counter

            import httpx
            from app.config import settings

            async with httpx.AsyncClient(timeout=8) as client:
                # Step 1: 地理编码 — 中文地名 → 坐标（先精准，不行再精简）
                geo_url = "http://api.openweathermap.org/geo/1.0/direct"
                raw_loc = (location or "").strip()
                for geo_query in (raw_loc, RecommendAgent._shorten_location(raw_loc)):
                    if not geo_query:
                        continue
                    geo_resp = await client.get(geo_url, params={
                        "q": geo_query,
                        "limit": 1,
                        "appid": settings.WEATHER_API_KEY,
                    })
                    if geo_resp.status_code == 200 and geo_resp.json():
                        break

                if geo_resp.status_code != 200 or not geo_resp.json():
                    logger.warning("天气地理编码失败(%s): HTTP %s", location, geo_resp.status_code)
                    return None
                geo = geo_resp.json()[0]
                lat, lon = geo.get("lat"), geo.get("lon")
                if lat is None or lon is None:
                    return None

                # Step 2: 5 天逐 3 小时预报（免费 API 上限 5 天）
                forecast_days = min(max(1, days), 5)
                fc_url = f"{settings.WEATHER_API_BASE_URL}/forecast"
                fc_resp = await client.get(fc_url, params={
                    "lat": lat,
                    "lon": lon,
                    "appid": settings.WEATHER_API_KEY,
                    "units": "metric",
                    "lang": "zh_cn",
                    "cnt": forecast_days * 8,
                })
                if fc_resp.status_code != 200:
                    logger.warning("天气预报查询失败(%s): HTTP %s", location, fc_resp.status_code)
                    return None
                data = fc_resp.json()

            # Step 3: 按天聚合 → 每日摘要
            daily: dict[str, list[dict]] = {}
            for item in data.get("list") or []:
                date_str = (item.get("dt_txt") or "").split(" ")[0]
                if date_str:
                    daily.setdefault(date_str, []).append(item)

            WEEKDAY = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            lines: list[str] = []
            for date_str, items in list(daily.items())[:forecast_days]:
                temps = [it["main"]["temp"] for it in items]
                descs = [it["weather"][0]["description"] for it in items]
                hums = [it["main"]["humidity"] for it in items]
                winds = [it["wind"]["speed"] for it in items]
                pop_vals = [it.get("pop", 0) for it in items]

                dom = Counter(descs).most_common(1)[0][0]
                hi, lo = max(temps), min(temps)
                avg_hum = sum(hums) / len(hums)
                avg_wind = sum(winds) / len(winds)
                max_pop = max(pop_vals)

                try:
                    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                    wd = WEEKDAY[dt.weekday()]
                    label = f"{dt.month:02d}.{dt.day:02d}({wd})"
                except ValueError:
                    label = date_str

                parts = [f"{label}：{dom}，{lo:.0f}~{hi:.0f}°C"]
                if avg_hum > 0:
                    parts.append(f"湿度{avg_hum:.0f}%")
                if avg_wind > 0:
                    parts.append(f"风速{avg_wind:.0f}m/s")
                if max_pop > 0:
                    parts.append(f"降水概率{max_pop:.0%}")
                lines.append("，".join(parts))

            if lines:
                return "\n".join(lines)
            logger.warning("天气预报数据为空(%s)", location)
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
