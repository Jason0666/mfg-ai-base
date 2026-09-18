"""M02 规则引擎：条款发现 + 企业档案比对 + 风险分级。

设计原则（蓝图 M01/M05 同一原则）：
- AI 负责"读懂条款、组织语言"；规则引擎负责"数值判定"，结论可复现、可测试
- 正则条款发现构成 5 个埋点的"确定性骨架"，LLM 结果只做补集与润色
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ===== 招标文件特征（P6 用）=====
TENDER_HINTS = ["招标", "投标", "资格", "废标", "评标", "保证金", "招标人", "投标人"]
REQUIRED_HINTS = ("招标", "投标")


def is_tender_document(pages: List[Dict[str, Any]]) -> Tuple[bool, int]:
    full = "\n".join(p.get("text", "") for p in pages)
    hit = sum(1 for w in TENDER_HINTS if w in full)
    ok = all(w in full for w in REQUIRED_HINTS) and hit >= 4
    return ok, hit


def _sentences(text: str) -> List[str]:
    # PDF/Word 文本的物理换行不是语义边界，先去除再按句末标点切句
    flat = re.sub(r"\s+", "", text)
    parts = re.split(r"(?<=[。；;])", flat)
    return [p for p in parts if p and len(p) > 1]


def _clause_of(page_text: str, fallback: str) -> str:
    m = re.search(r"(\d+\.\d+)\s*([一-龥A-Za-z]{2,12})", page_text)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return fallback or ""


# ===== 数值与单位 =====

_NUM_RE = r"(\d+(?:\.\d+)?)"


def parse_cn_date(text: str) -> Optional[datetime]:
    """从任意中文/横排日期时间串解析。"""
    m = re.search(
        r"(\d{4})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})\s*日?[^\d]{0,6}(\d{1,2})\s*[:：]\s*(\d{2})",
        text,
    )
    if not m:
        m = re.search(r"(\d{4})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})\s*日?", text)
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
        return datetime(y, mo, d)
    y, mo, d, hh, mm = (int(x) for x in m.groups())
    return datetime(y, mo, d, hh, mm)


def normalize_unit(u: str) -> str:
    u = (u or "").replace("／", "/").replace(" ", "")
    alias = {
        "件每小时": "件/小时",
        "百分比": "%",
    }
    return alias.get(u, u)


# 越小越优的参数（精度/误差/噪声类）
_LOWER_BETTER_KEYS = ("精度", "误差", "噪声")


def is_lower_better(item: str) -> bool:
    return any(k in item for k in _LOWER_BETTER_KEYS)


# 资格项别名 → profile 匹配关键词
QUAL_KEYWORDS = {
    "audit_report": ["审计", "财务报告", "财务报表", "审计报告"],
    "iso9001": ["iso9001", "ISO9001", "9001质量体系", "质量管理体系认证"],
    "iso14001": ["iso14001", "ISO14001", "14001环境", "环境管理体系认证"],
    "hightech": ["高新技术企业"],
    "safety_std": ["安全生产标准化"],
}


def _find_chapter_range(pages: List[Dict[str, Any]], start_kw: str, end_kw: str) -> Tuple[int, int]:
    """返回章节页区间 [start, end)（0-based 索引），找不到时 (0, len)。"""
    start = next((i for i, p in enumerate(pages) if start_kw in p.get("text", "")), None)
    end = next((i for i, p in enumerate(pages) if start is not None and i > start and end_kw in p.get("text", "")), None)
    if start is None:
        return 0, len(pages)
    return start, (end if end is not None else len(pages))


# ===== 资格条款发现 =====

def discover_qualifications(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    s, e = _find_chapter_range(pages, "投标人资格要求", "采购需求")
    scope_pages = pages[s:e] or pages
    out: List[Dict[str, Any]] = []

    def add(key: str, pg: Dict[str, Any], req_text: str, required_value: Any = None, unit: str = ""):
        if any(x["key"] == key for x in out):
            return
        out.append({
            "key": key,
            "page": pg["page"],
            "clause": _clause_of(pg.get("text", ""), pg.get("clause", "")),
            "requirement": req_text,
            "required_value": required_value,
            "unit": unit,
        })

    for pg in scope_pages:
        flat = re.sub(r"\s+", "", pg.get("text", ""))
        for sent in _sentences(pg.get("text", "")):
            if "注册资本" in sent and ("不低于" in sent or "不少于" in sent or "≥" in sent or ">=" in sent):
                m = re.search(r"(?:不低于|不少于|≥|>=)\s*人民币?\s*" + _NUM_RE + r"\s*万元", sent)
                if m:
                    add("registered_capital", pg,
                        "投标人实缴注册资本不低于人民币 %s 万元" % m.group(1),
                        float(m.group(1)), "万元")
            if "审计" in sent and ("财务报告" in sent or "财务报表" in sent):
                add("audit_report", pg,
                    "须提供近三年经会计师事务所审计的财务报告/报表")
            if re.search(r"iso\s*9001|9001质量管理体系", sent, re.IGNORECASE):
                add("iso9001", pg, "具备有效期内的 ISO9001 质量管理体系认证")
            if re.search(r"iso\s*14001|14001环境管理体系", sent, re.IGNORECASE):
                add("iso14001", pg, "具备 ISO14001 环境管理体系认证证书（加分/要求项）")
            if "高新技术企业" in sent:
                add("hightech", pg, "具备高新技术企业证书")
            if "安全生产标准化" in sent:
                add("safety_std", pg, "具备安全生产标准化证书")
            m = re.search(r"不少于\s*(\d+)\s*个[^。；]{0,30}?(业绩|合同)", sent)
            if m:
                add("similar_count", pg,
                    "近三年具有不少于 %s 个同类业绩合同" % m.group(1),
                    int(m.group(1)), "个")
    return out


# ===== 技术条款发现 =====

TECH_ITEM_PATTERNS = [
    ("处理能力", [r"处理能力", r"产能"]),
    ("重复定位精度", [r"重复定位精度"]),
    ("定位精度", [r"(?<!复)定位精度"]),
    ("设备综合效率OEE", [r"\s*OEE\s*", r"综合效率"]),
    ("平均无故障工作时间MTBF", [r"MTBF", r"无故障工作时间"]),
    ("质保期", [r"质保期", r"质量保证期"]),
    ("售后响应时间", [r"响应时间", r"到达现场"]),
]
_BOUND_RE = r"(不低于|不少于|不高于|不大于|不超过|≥|≤|>=|<=|>|<)"


def _match_item(sent: str) -> Optional[str]:
    for name, pats in TECH_ITEM_PATTERNS:
        for p in pats:
            if re.search(p, sent, re.IGNORECASE):
                return name
    return None


def discover_tech(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pg in pages:
        for sent in _sentences(pg.get("text", "")):
            item = _match_item(sent)
            if not item:
                continue
            m = re.search(_BOUND_RE + r"[^0-9０-９]{0,12}?" + _NUM_RE + r"\s*"
                          r"(件/小时|件每小时|mm|毫米|%|小时|年|dB\(A\)|dB)?", sent)
            if not m:
                continue
            bound_word, val, unit = m.group(1), float(m.group(2)), normalize_unit(m.group(3) or "")
            lower_bound = bound_word in ("不低于", "不少于", "≥", ">=", ">")
            material = ("★" in sent) or ("实质性" in sent) or item in (
                "定位精度", "重复定位精度", "处理能力")
            if any(x["item"] == item for x in out):
                # 保留标★的一次
                if material:
                    for x in out:
                        if x["item"] == item:
                            x.update(material=True, page=pg["page"], value=val, unit=unit,
                                     lower_bound=lower_bound, clause=_clause_of(pg.get("text", ""), pg.get("clause", "")),
                                     raw=sent[:120])
                continue
            out.append({
                "item": item,
                "page": pg["page"],
                "clause": _clause_of(pg.get("text", ""), pg.get("clause", "")),
                "value": val,
                "unit": unit,
                "lower_bound": lower_bound,
                "bound_word": bound_word,
                "material": material,
                "raw": sent[:120],
            })
    return out


# ===== 盖章 / 时间发现 =====

def discover_seals(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()

    def add(key: str, pg: Dict[str, Any], desc: str):
        if key in seen:
            return
        seen.add(key)
        out.append({"key": key, "page": pg["page"],
                    "clause": _clause_of(pg.get("text", ""), pg.get("clause", "")),
                    "desc": desc})

    for pg in pages:
        flat = re.sub(r"\s+", "", pg.get("text", ""))
        if "授权委托书" in flat and ("公章" in flat or "盖章" in flat or "亲笔签署" in flat or "签字" in flat):
            add("power_of_attorney", pg,
                "法定代表人授权委托书须由法定代表人签字/名章并加盖投标人单位公章，附双方身份证复印件盖章")
        if ("封口处" in flat and "公章" in flat) or ("封条" in flat and "公章" in flat):
            add("seal_pack", pg, "投标文件封套加贴封条，封口处加盖投标人公章")
        if "骑缝章" in flat:
            add("cross_seal", pg, "投标文件按要求加盖骑缝章")
    return out


def discover_timelines(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for pg in pages:
        for sent in _sentences(pg.get("text", "")):
            dt = parse_cn_date(sent)
            if not dt:
                continue
            if "保证金" in sent and "deposit" not in seen:
                seen.add("deposit")
                out.append({"key": "deposit", "event": "投标保证金到账截止",
                            "deadline": dt.strftime("%Y-%m-%d %H:%M"),
                            "_dt": dt, "page": pg["page"]})
            elif ("递交截止" in sent or "投标文件" in sent and "截止" in sent) and "submit" not in seen:
                seen.add("submit")
                out.append({"key": "submit", "event": "投标文件递交截止 / 开标",
                            "deadline": dt.strftime("%Y-%m-%d %H:%M"),
                            "_dt": dt, "page": pg["page"]})
    return out


# ===== 企业档案比对 =====

def _profile_qual(profile: dict, key: str) -> Tuple[bool, str]:
    quals = profile.get("qualifications", [])
    kws = QUAL_KEYWORDS.get(key, [])
    for q in quals:
        name = str(q.get("name", ""))
        flat = name + str(q.get("evidence", "")) + str(q.get("note", ""))
        if any(k.lower() in flat.lower() for k in kws):
            return bool(q.get("held")), f"{name}：{q.get('evidence') or q.get('note') or ('具备' if q.get('held') else '不具备/未提供')}"
    return False, "企业档案中未提供该项材料"


def compare_qualifications(reqs: List[Dict[str, Any]], profile: dict) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in reqs:
        key = r["key"]
        if key == "registered_capital":
            ours = float(profile.get("registered_capital_wan", 0) or 0)
            need = float(r["required_value"])
            ok = ours >= need
            rows.append({
                "requirement": r["requirement"],
                "ours": f"实缴注册资本 {ours:g} 万元",
                "match": ok,
                "page": r["page"],
                "key": key,
                "gap": 0 if ok else need - ours,
            })
        elif key == "similar_count":
            ours_n = int(profile.get("similar_performance", {}).get("count_3y", 0) or 0)
            need = int(r["required_value"])
            rows.append({
                "requirement": r["requirement"],
                "ours": f"近三年同类业绩 {ours_n} 个",
                "match": ours_n >= need,
                "page": r["page"],
                "key": key,
                "gap": 0 if ours_n >= need else need - ours_n,
            })
        else:
            held, ours_text = _profile_qual(profile, key)
            rows.append({
                "requirement": r["requirement"],
                "ours": ours_text,
                "match": held,
                "page": r["page"],
                "key": key,
                "gap": 0 if held else 1,
            })
    return rows


def _find_our_param(profile: dict, item: str) -> Optional[Dict[str, Any]]:
    for p in profile.get("tech_params", []):
        if p.get("item") == item:
            return p
    return None


def compare_tech(reqs: List[Dict[str, Any]], profile: dict) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in reqs:
        ours = _find_our_param(profile, r["item"])
        ours_text = f"{ours['value']:g} {ours.get('unit','')}".strip() if ours else "企业档案中未提供该参数"
        deviation = "unknown"
        if ours is not None:
            v = float(ours["value"])
            req_v = float(r["value"])
            if abs(v - req_v) < 1e-9:
                meets = True
            elif r["lower_bound"]:
                meets = (v >= req_v) if not is_lower_better(r["item"]) else (v <= req_v)
            else:
                meets = (v <= req_v) if is_lower_better(r["item"]) else (v >= req_v)
            if meets:
                deviation = "positive" if v != req_v else "none"
            else:
                deviation = "negative"
        rows.append({
            "item": r["item"],
            "required": f"{r['bound_word']} {r['value']:g} {r.get('unit','')}".strip(),
            "ours": ours_text,
            "deviation": deviation,
            "material": bool(r.get("material")),
            "page": r["page"],
            "key": "tech:" + r["item"],
        })
    return rows


# ===== 风险分级 =====

def enrich_timelines(timelines: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    rows = []
    for t in timelines:
        days = (t["_dt"].date() - now.date()).days
        if days < 0:
            level = "high"
        elif days <= 7:
            level = "high"
        elif days <= 21:
            level = "medium"
        else:
            level = "low"
        rows.append({**{k: v for k, v in t.items() if k != "_dt"},
                     "days_left": days, "level": level})
    rows.sort(key=lambda x: x["days_left"])
    return rows


def build_risks(qual_rows: List[Dict[str, Any]], tech_rows: List[Dict[str, Any]],
                seals: List[Dict[str, Any]], timelines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """汇总废标风险（资格缺项/不满足 + 负偏离 + 盖章 + 临近时间）。"""
    risks: List[Dict[str, Any]] = []

    name_map = {
        "audit_report": ("经审计的近三年财务报告", "missing",
                         "未提供经审计财务报告将导致资格审查不通过",
                         "立即联系会计师事务所启动审计，或投标决策阶段止损"),
        "registered_capital": ("实缴注册资本要求", "mismatch",
                               "注册资本不满足要求，资格审查不通过",
                               "核实实缴口径，评估减资/增资可行性后再决策"),
        "iso9001": ("ISO9001 认证", "missing", "缺少有效 ISO9001 证书，资格审查不通过",
                    "确认证书有效期；过期立即监督审核/复审"),
        "iso14001": ("ISO14001 认证", "missing", "缺少 ISO14001 证书（招标要求时不通过）",
                     "补充证书或按加分项评估影响"),
        "hightech": ("高新技术企业证书", "missing", "未提供高新技术企业证书", "补充证书复印件"),
        "safety_std": ("安全生产标准化证书", "missing", "未提供安全生产标准化证书", "补充证书复印件"),
        "similar_count": ("类似业绩数量", "mismatch", "类似业绩数量不满足资格要求",
                          "核查业绩口径（验收/合同/近三年），补齐证明材料"),
    }
    for row in qual_rows:
        if row["match"]:
            continue
        nm, status, reason, sug = name_map.get(row["key"], (row["requirement"], "mismatch",
                                                            "资格条件不满足", "人工复核"))
        risks.append({
            "id": "RISK-" + row["key"],
            "level": "high",
            "clause": f"第{row['page']}页 资格要求",
            "page": row["page"],
            "reason": f"{nm}：{reason}（招标要求：{row['requirement']}；{row['ours']}）",
            "our_status": status,
            "suggestion": sug,
        })

    for row in tech_rows:
        if row["deviation"] == "negative":
            risks.append({
                "id": "RISK-" + row["key"],
                "level": "high" if row["material"] else "medium",
                "clause": f"第{row['page']}页 技术规格" + ("（★实质性条款）" if row["material"] else ""),
                "page": row["page"],
                "reason": f"{row['item']} 负偏离：招标要求 {row['required']}，我方 {row['ours']}"
                          + ("，实质性条款不允许负偏离，将被否决" if row["material"] else "，预计扣分"),
                "our_status": "negative",
                "suggestion": "技术负责人评估升级配置/外协达标；无法满足的★条款应放弃投标",
            })

    for s in seals:
        label = "授权委托书签字盖章" if s["key"] == "power_of_attorney" else "投标文件盖章密封"
        risks.append({
            "id": "RISK-seal-" + s["key"],
            "level": "medium",
            "clause": f"第{s['page']}页 {s.get('clause','格式要求')}",
            "page": s["page"],
            "reason": f"{label}易漏，遗漏将导致废标：{s['desc']}",
            "our_status": "to_check",
            "suggestion": "用盖章签字清单双人复核，封装后拍照留存",
        })

    for t in timelines:
        if t["level"] in ("high", "medium"):
            risks.append({
                "id": "RISK-time-" + t["key"],
                "level": t["level"],
                "clause": f"第{t['page']}页 投标邀请",
                "page": t["page"],
                "reason": f"{t['event']}：{t['deadline']}（距今 {t['days_left']} 天），错过节点直接废标",
                "our_status": "pending",
                "suggestion": "倒排计划并设提前提醒，保证金至少提前 2 个工作日汇出",
            })

    order = {"high": 0, "medium": 1, "low": 2}
    risks.sort(key=lambda x: (order.get(x["level"], 9), x["page"]))
    return risks
