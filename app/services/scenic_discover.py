"""搜索无结果时：高德 POI + 百度百科 + 维基导游写入国内景点数据。"""

from __future__ import annotations



import asyncio

import concurrent.futures

from typing import Optional, Tuple



import httpx

from sqlalchemy import func, or_

from sqlalchemy.orm import Session



from app.config import settings

from app.models.scenic import Scenic

from crawler.amap_client import (

    CATEGORY_NONE,

    amap_type_to_category,

    extract_city_from_location,

    resolve_amap_scenic,

)

from crawler.baike import resolve_baidu_baike

from crawler.mediawiki import resolve_wikivoyage





def _extract_relevant(text: str, scenic_name: str, min_chars: int = 2) -> bool:

    """检查百科摘要是否与景点名相关（避免城市词条污染景点描述）。"""

    if not text or not scenic_name:

        return False

    t = text[:400]  # 只看前 400 字符即可判断

    # 景点名完整出现在摘要中 → 相关

    if scenic_name in t:

        return True

    # 去掉常见后缀后检查（如"慕田峪长城" → "慕田峪"）

    for suffix in ("风景区", "景区", "公园", "博物馆", "遗址", "古城", "古镇", "长城", "旅游区"):

        if scenic_name.endswith(suffix) and len(scenic_name) > len(suffix) + min_chars:

            short = scenic_name[: -len(suffix)]

            if short in t:

                return True

    return False



def _guess_category(keyword: str, amap_type: str | None = None, typecode: str | None = None) -> str:

    if (amap_type or "").strip() or (typecode or "").strip():

        return amap_type_to_category(typecode, amap_type)

    return CATEGORY_NONE





def _is_domestic_poi(amap: dict) -> bool:

    city = (amap.get("cityname") or "").strip()

    if not city:

        return True

    overseas_markers = ("日本", "泰国", "法国", "美国", "英国", "韩国", "新加坡", "马来西亚")

    return not any(m in city for m in overseas_markers)





def _find_existing(db: Session, kw: str) -> Optional[Scenic]:

    return (

        db.query(Scenic)

        .filter(

            Scenic.is_active == 1,

            or_(Scenic.name == kw, Scenic.name.contains(kw), Scenic.location.contains(kw)),

        )

        .first()

    )





async def _fetch_all(kw: str, city: str | None):

    city_hint = city or extract_city_from_location(kw)

    amap = await resolve_amap_scenic(kw, city_hint) if settings.AMAP_KEY else None

    search_name = (amap or {}).get("name") or kw

    loc = (amap or {}).get("location_text")



    async with httpx.AsyncClient(follow_redirects=True, timeout=40.0) as client:

        # 并行请求 百度百科 + Wikivoyage（独立 API，无依赖关系）
        baike, voy = await asyncio.gather(
            resolve_baidu_baike(client, search_name, loc),
            resolve_wikivoyage(client, search_name, loc),
            return_exceptions=True,
        )
        if isinstance(baike, BaseException):
            baike = None
        if isinstance(voy, BaseException):
            voy = None



    return amap, baike, voy





def _persist_discovered(

    db: Session,

    kw: str,

    amap: dict | None,

    wiki: dict | None,

    voy: dict | None,

) -> Tuple[Optional[Scenic], bool]:

    if amap and not _is_domestic_poi(amap):

        return None, False



    display_name = (amap or {}).get("name") or kw

    if any(x in display_name for x in ("学校", "大学", "中学", "小学", "幼儿园", "学院", "校区")):

        return None, False

    if display_name != kw:

        dup2 = db.query(Scenic).filter(Scenic.is_active == 1, Scenic.name == display_name).first()

        if dup2:

            return dup2, False



    desc_parts = []

    # 百度百科：仅当摘要与景点名相关时才使用（避免回退到城市词条）

    wiki_extract = (wiki or {}).get("extract") or ""

    if wiki_extract and _extract_relevant(wiki_extract, display_name):

        desc_parts.append(wiki_extract[:800])

    elif amap and amap.get("address"):

        desc_parts.append(f"{display_name}位于{amap.get('location_text') or amap.get('address')}，是当地知名旅游目的地。")

    if voy and voy.get("extract"):

        ve = voy["extract"][:800]

        if len(ve) > 60 and _extract_relevant(ve, display_name):

            we = (wiki or {}).get("extract") or ""

            if ve[:120] not in we:

                desc_parts.append(ve)

    # 无可用描述时生成简洁模板

    if not desc_parts:

        loc_info = (amap or {}).get("location_text") or (amap or {}).get("address") or ""

        if loc_info:

            desc_parts.append(f"{display_name}位于{loc_info}。")

        else:

            desc_parts.append(f"{display_name}，收录自高德地图与国内公开资料。")

    description = "\n\n".join(desc_parts)



    images: list[str] = []

    image = None

    if amap:

        for u in amap.get("photos") or []:

            if u and u not in images:

                images.append(u)

        image = amap.get("image") or (images[0] if images else None)

    if wiki and wiki.get("thumbnail"):

        if wiki["thumbnail"] not in images:

            images.append(wiki["thumbnail"])

        if not image:

            image = wiki["thumbnail"]

    if voy and voy.get("thumbnail") and voy["thumbnail"] not in images:

        images.append(voy["thumbnail"])

        if not image:

            image = voy["thumbnail"]



    lat = (amap or {}).get("latitude") or (wiki.get("latitude") if wiki else None)

    lon = (amap or {}).get("longitude") or (wiki.get("longitude") if wiki else None)

    location = (amap or {}).get("location_text") or (wiki or {}).get("title") or kw

    address = (amap or {}).get("address")



    tags = ["搜索发现", "高德地图"]

    if amap and amap.get("id"):

        tags.append("POI")



    new_id = (db.query(func.max(Scenic.id)).scalar() or 0) + 1

    scenic = Scenic(

        id=new_id,

        name=display_name,

        category=_guess_category(

            display_name,

            (amap or {}).get("type"),

            (amap or {}).get("typecode"),

        ),

        region="domestic",

        location=location,

        address=address,

        price=0.0,

        image=image,

        images=images,

        description=description,

        opening_hours="以景区当日公告为准",

        best_season="四季皆宜",

        tips="坐标与地址来自高德地图，介绍来自百度百科等公开资料，出行前请核实开放时间与票价。",

        latitude=lat,

        longitude=lon,

        tags=tags,

        review_count=0,

        view_count=0,

        is_hot=0,

        is_active=1,

    )

    db.add(scenic)

    db.commit()

    db.refresh(scenic)

    return scenic, True





async def try_discover_scenic_async(

    db: Session, keyword: str, city: str | None = None

) -> Tuple[Optional[Scenic], bool]:

    """异步：高德 POI + 百度百科 + 维基导游 → 入库（供 FastAPI async 路由调用）。"""

    kw = (keyword or "").strip()

    if not kw or len(kw) > 80:

        return None, False

    if any(x in kw for x in ("学校", "大学", "中学", "小学", "幼儿园", "学院", "校区")):

        return None, False



    try:

        dup = _find_existing(db, kw)

        if dup:

            return dup, False



        amap, wiki, voy = await _fetch_all(kw, city)

        return _persist_discovered(db, kw, amap, wiki, voy)

    except Exception:

        db.rollback()

        return None, False





def try_discover_scenic(

    db: Session, keyword: str, city: str | None = None

) -> Tuple[Optional[Scenic], bool]:

    """同步入口：在已有事件循环时于独立线程中执行 asyncio.run。"""

    try:

        asyncio.get_running_loop()

    except RuntimeError:

        return asyncio.run(try_discover_scenic_async(db, keyword, city))



    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:

        future = pool.submit(asyncio.run, try_discover_scenic_async(db, keyword, city))

        return future.result(timeout=90)


async def try_discover_multiple_async(

    db: Session, keyword: str, city: str | None = None, max_new: int = 5

) -> int:

    """批量发现：用高德搜索多条 POI，为库中不存在的逐一入库。

    适用于关键字已有一两条结果但仍需扩展的场景（如搜"长城"已有慕田峪，还需八达岭）。
    """

    kw = (keyword or "").strip()

    if not kw or len(kw) > 80:

        return 0

    if any(x in kw for x in ("学校", "大学", "中学", "小学", "幼儿园", "学院", "校区")):

        return 0

    from app.config import settings

    if not settings.AMAP_KEY:

        return 0

    from crawler.amap_client import search_amap_pois

    try:

        pois = await search_amap_pois(kw, city=city, limit=10)

    except Exception:

        return 0

    if not pois:

        return 0

    added = 0

    for poi in pois:

        if added >= max_new:

            break

        poi_name = (poi.get("name") or "").strip()

        if not poi_name or len(poi_name) < 2:

            continue

        # 检查是否已存在

        dup = _find_existing(db, poi_name)

        if dup:

            continue

        # 百科 + 维基导游丰富信息（POI 数据已有，不再重复调高德）

        loc = poi.get("location_text") or ""

        try:

            import httpx

            async with httpx.AsyncClient(follow_redirects=True, timeout=40.0) as client:

                baike, voy = await asyncio.gather(

                    resolve_baidu_baike(client, poi_name, loc),

                    resolve_wikivoyage(client, poi_name, loc),

                    return_exceptions=True,

                )

                if isinstance(baike, BaseException):

                    baike = None

                if isinstance(voy, BaseException):

                    voy = None

        except Exception:

            baike, voy = None, None

        scenic, created = _persist_discovered(db, poi_name, poi, baike, voy)

        if created:

            added += 1

    return added


def try_discover_multiple(

    db: Session, keyword: str, city: str | None = None, max_new: int = 5

) -> int:

    """同步入口。"""

    try:

        asyncio.get_running_loop()

    except RuntimeError:

        return asyncio.run(try_discover_multiple_async(db, keyword, city, max_new))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:

        future = pool.submit(

            asyncio.run, try_discover_multiple_async(db, keyword, city, max_new)

        )

        return future.result(timeout=120)

