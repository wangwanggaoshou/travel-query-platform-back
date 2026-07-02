"""一次性脚本：清理现有景点描述（去掉城市百科污染段落）。"""
import sqlite3
import re

DB_PATH = "data/travel.db"

# 段落级城市百科特征
_CITY_PATTERNS = [
    re.compile(r"^.{1,8}(市|省|自治区|特别行政区).{0,12}(是|简称|位于|别称|旧称)"),
    re.compile(r"^.{1,10}(简称|别称|旧称).{0,8}(，|,)"),
    re.compile(r"^.{1,14}是中华人民共和国"),
    re.compile(r"^.{1,8}是.{0,8}(首都|省会|城市|省份|国家)"),
    re.compile(r"^\w+\s?（.{2,20}）.{0,8}(位于|是)"),
    # 城市名开头 + 位于/在（如"晋城位于山西南部"）
    re.compile(r"^[一-鿿]{2,4}位于.{2,10}(部|省|市|区)"),
    # "这个城市/这座..."
    re.compile(r"^(这个|这座)(城市|都市|省份)"),
    # "XX本身和中国大部分..."  ← 桂林残留
    re.compile(r"^[一-鿿]{2,4}本身和"),
]


def _city_para(para: str) -> bool:
    """单段是否来自城市/地区百科。"""
    t = para.strip()
    if len(t) < 30:
        return False
    for pat in _CITY_PATTERNS:
        if pat.search(t):
            return True
    return False


def _short_name(name: str) -> str:
    """去景区后缀取短名，用于在文本中匹配。"""
    for sfx in ("风景区", "景区", "公园", "博物馆", "遗址", "古城", "古镇", "长城", "旅游区"):
        if name.endswith(sfx) and len(name) > len(sfx) + 2:
            return name[:-len(sfx)]
    return name


def clean_one(text: str, name: str, location: str = "") -> str:
    """逐段过滤：保留提及景点名的段，丢弃城市百科段。"""
    if not text or not text.strip():
        return _fallback(name, location)

    paras = [p.strip() for p in text.split("\n") if p.strip()]
    short = _short_name(name)

    kept = []
    for p in paras:
        # 段内包含景点名（全名或短名）→ 保留
        if name in p or (short != name and short in p):
            kept.append(p)
            continue
        # 城市特征段 → 丢弃
        if _city_para(p):
            continue
        # 不含景点名且超过60字符 → 大概率不相关
        if len(p) > 60:
            continue
        # 短段保留
        if len(p) >= 4:
            kept.append(p)

    if kept:
        return "\n\n".join(kept)[:800]
    return _fallback(name, location)


def _fallback(name: str, location: str) -> str:
    if location:
        return f"{name}位于{location}，是当地知名旅游目的地。"
    return f"{name}，收录自高德地图与国内公开资料。"


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, location FROM scenics WHERE is_active = 1")
    rows = cur.fetchall()

    updated = 0
    for sid, name, desc, loc in rows:
        if not desc:
            continue
        new_desc = clean_one(desc, name, loc or "")
        if new_desc != desc:
            cur.execute("UPDATE scenics SET description = ? WHERE id = ?", (new_desc, sid))
            updated += 1

    conn.commit()
    conn.close()
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print(f"Fixed {updated} / {len(rows)} descriptions.")


if __name__ == "__main__":
    main()
