"""粉尘治理前后对比口径（视图与月度报表共用，分段规则只此一份）。

分段规则（唯一口径，冲突时一律以措施生效日期为准）：
1. 一个测点的对比段按「降尘措施生效日期」切分；采样中断、补测不产生新段，
   补回来的读数落在原段窗口内，接着原段续算。
2. 存量测点（is_legacy）按治理日期回填：治理日期就是首段起点；当治理日期早于
   措施生效日期时，仍以生效日期分段，治理日期到生效日期之间的旧区间按当时措施
   （无措施）保留，并在首段备注说明。
3. 每段取「措施生效前最近一次有效读数」为治理前读数、「段窗口内最近一次有效
   读数」为治理后读数；变更段的前读数即上一段的末值，前后读数必须同单位。
4. 效率 = (前 - 后) / 前 ×100%。前/后读数缺失或单位不一致时一律留空并写明原因，
   绝不按 0 处理。
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date
from typing import Any

from app.store import store

POINTS_TABLE = "dust_points"
MEASURES_TABLE = "dust_measures"
SAMPLES_TABLE = "dust_samples"

# 游离二氧化硅含量 10% 限值：超过即按矽尘限值单独标出。
SILICA_LIMIT = 10.0


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    parts = str(value).strip().split("-")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _number(value: Any) -> float | None:
    """把读数转成数值；空值、非数值（含采样未取到数）一律视为无有效读数。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unit_key(unit: Any) -> str:
    """归一化浓度单位书写：NFKC 收敛上下标（mg/m³ 与 mg/m3 视为同单位），

    再去掉空格、间隔号、斜杠等符号差异；mg/L 与 mg/m3、g/m3 与 mg/m3
    这类物理量纲不同的写法归一后仍不一致，必须人工复核，不猜换算。
    """
    text = unicodedata.normalize("NFKC", str(unit or "")).strip().lower()
    text = re.sub(r"[\s·/．.]", "", text)
    text = text.replace("−", "-")
    return text


def _round1(value: float) -> float:
    # 四舍五入到 1 位小数，避免银行家舍入与报表口径不一致。
    return int(value * 10 + 0.5) / 10


def _load_rows(as_of: date) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]:
    points = store.rows(POINTS_TABLE)
    measures_by_point: dict[int, list[dict[str, Any]]] = {}
    for row in store.rows(MEASURES_TABLE):
        measures_by_point.setdefault(int(row["point_id"]), []).append(row)
    samples_by_point: dict[int, list[dict[str, Any]]] = {}
    for row in store.rows(SAMPLES_TABLE):
        sample_date = _parse_date(row.get("采样日期"))
        # as_of 之后的数据尚未发生，不参与任何口径。
        if sample_date is not None and sample_date <= as_of:
            samples_by_point.setdefault(int(row["point_id"]), []).append(row)
    for rows in measures_by_point.values():
        rows.sort(key=lambda r: (_parse_date(r.get("生效日期")) or date.max, int(r.get("id", 0))))
    for rows in samples_by_point.values():
        rows.sort(key=lambda r: (_parse_date(r.get("采样日期")) or date.max, int(r.get("id", 0))))
    return points, measures_by_point, samples_by_point


def _latest_valid(samples: list[dict[str, Any]], before: date, *, after: date | None = None) -> dict[str, Any] | None:
    """取 after <= 采样日期 < before 窗口内最近一次有效读数（中断补测同样参与）。"""
    picked = None
    for sample in samples:
        sample_date = _parse_date(sample.get("采样日期"))
        if sample_date is None or sample_date >= before:
            continue
        if after is not None and sample_date < after:
            continue
        if not int(sample.get("有效读数", 1)):
            picked = sample  # 记住窗口内的无效读数，用于区分「未复测」和「复测缺数」
            continue
        if _number(sample.get("粉尘浓度")) is None:
            picked = sample
            continue
        picked = sample
    # 只有真实有效读数才返回；否则返回最近一条（可能是缺数记录）交给调用方判断。
    if picked is not None and _number(picked.get("粉尘浓度")) is not None:
        return picked
    return picked if picked is not None and not int(picked.get("有效读数", 1)) else None


def _reading_view(sample: dict[str, Any] | None) -> dict[str, Any]:
    if sample is None or _number(sample.get("粉尘浓度")) is None:
        return {"value": None, "date": None, "sample_id": None, "resumed": False}
    return {
        "value": _number(sample.get("粉尘浓度")),
        "date": sample.get("采样日期"),
        "sample_id": sample.get("id"),
        "resumed": bool(int(sample.get("中断补测", 0))),
        "unit": sample.get("单位"),
    }


def build_point_segments(
    point: dict[str, Any],
    measures: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    as_of: date,
) -> list[dict[str, Any]]:
    """按措施生效日期把一个测点切成若干对比段；无措施测点返回一条「未治理」行。"""
    point_id = int(point["id"])
    legacy = bool(int(point.get("is_legacy", 0)))
    governed_at = _parse_date(point.get("治理日期"))

    # 同一生效日期只保留最后登记的一条（措施同日变更以最新登记为准）。
    anchors: list[dict[str, Any]] = []
    for measure in measures:
        effective = _parse_date(measure.get("生效日期"))
        if effective is None or effective > as_of:
            continue
        if anchors and _parse_date(anchors[-1].get("生效日期")) == effective:
            anchors[-1] = measure
        else:
            anchors.append(measure)

    notes_global: list[str] = []
    if not anchors:
        # 存量/无措施测点：没有生效日期可切，治理日期只用来提示，不成段算效率。
        if governed_at and legacy:
            notes_global.append(
                f"存量测点登记的治理日期为 {governed_at.isoformat()}，但未回填降尘措施及生效日期，无法分段计算效率"
            )
        before_sample = None
        for sample in samples:
            if _number(sample.get("粉尘浓度")) is not None:
                before_sample = sample
        return [{
            "point_id": point_id,
            "测点编号": point.get("测点编号"),
            "所在区域": point.get("所在区域"),
            "粉尘类型": point.get("粉尘类型"),
            "游离二氧化硅": point.get("游离二氧化硅"),
            "超限": _number(point.get("游离二氧化硅")) is not None
            and _number(point.get("游离二氧化硅")) > SILICA_LIMIT,
            "存量回填": legacy,
            "未治理": not bool(anchors),
            "段序号": 1,
            "段数": 1,
            "区间起": _iso(governed_at),
            "区间止": None,
            "降尘措施": None,
            "措施生效日期": None,
            "前读数": _reading_view(before_sample),
            "后读数": _reading_view(None),
            "降尘效率": None,
            "效率留空原因": "尚未登记降尘措施与生效日期，无法对比" if not notes_global else notes_global[0],
            "备注": notes_global,
        }]

    segments: list[dict[str, Any]] = []
    for index, anchor in enumerate(anchors):
        start = _parse_date(anchor.get("生效日期"))
        end = _parse_date(anchors[index + 1].get("生效日期")) if index + 1 < len(anchors) else None
        window_end = end if end is not None else None  # 开区间，读数截至 as_of

        before_sample = _latest_valid(samples, start)  # 生效前最近一次有效读数
        after_sample = _latest_valid(
            samples,
            window_end or date.max,
            after=start,
        )

        before = _reading_view(before_sample)
        after = _reading_view(after_sample)

        notes: list[str] = []
        # 段窗口内只要出现过中断补测（哪怕末值读数不是补测那条），就在该段上标明续接。
        resumed_in_window = any(
            int(s.get("中断补测", 0))
            and (sd := _parse_date(s.get("采样日期"))) is not None
            and sd >= start
            and (window_end is None or sd < window_end)
            for s in samples
        )
        if resumed_in_window:
            notes.append("采样中断后补测，补测数据接原段续算，不另分段")
        if legacy and index == 0:
            anchor_treatment = governed_at or start
            notes.append(f"存量测点，按治理日期 {anchor_treatment.isoformat()} 回填治理前后读数")
            # 口径冲突：治理日期与措施生效日期不一致时，以生效日期分段，
            # 治理日期到生效日期之间的旧区间按当时措施（无措施）保留。
            if governed_at and governed_at != start:
                notes.append(
                    f"治理日期 {governed_at.isoformat()} 与措施生效日期 {start.isoformat()} 不一致，"
                    f"按生效日期分段，{governed_at.isoformat()}~{start.isoformat()} 旧区间沿用当时措施（无措施）"
                )

        efficiency = None
        reason = ""
        if before["value"] is None:
            reason = "治理前未采样，缺少可比基线，降尘效率留空" if index == 0 else "上一措施段缺少末值读数，变更段缺少可比基线，降尘效率留空"
        elif after["value"] is None:
            window_samples = [
                s for s in samples
                if (sd := _parse_date(s.get("采样日期"))) is not None and sd >= start
                and (window_end is None or sd < window_end)
            ]
            invalid_in_window = [
                s for s in window_samples
                if not int(s.get("有效读数", 1)) or _number(s.get("粉尘浓度")) is None
            ]
            if any(int(s.get("中断补测", 0)) for s in invalid_in_window):
                reason = "治理后复测因采样中断未取到有效数据，待补测后续算，降尘效率留空，不计为零"
            elif invalid_in_window:
                reason = "治理后复测未取到有效数据，降尘效率留空，不计为零"
            else:
                reason = "治理后尚未复测，降尘效率留空，不计为零"
        elif _unit_key(before.get("unit")) != _unit_key(after.get("unit")):
            reason = (
                f"治理前后读数单位不一致（{before.get('unit')} / {after.get('unit')}），"
                "需复核原始记录，降尘效率留空"
            )
        elif before["value"] == 0:
            reason = "治理前浓度为 0，效率分母为零，无法计算"
        else:
            efficiency = _round1((before["value"] - after["value"]) / before["value"] * 100)
            if efficiency < 0:
                notes.append(f"治理后浓度反弹（{before['value']}→{after['value']}），效率为负值，请核查措施有效性")

        segments.append({
            "point_id": point_id,
            "测点编号": point.get("测点编号"),
            "所在区域": point.get("所在区域"),
            "粉尘类型": point.get("粉尘类型"),
            "游离二氧化硅": point.get("游离二氧化硅"),
            "超限": _number(point.get("游离二氧化硅")) is not None
            and _number(point.get("游离二氧化硅")) > SILICA_LIMIT,
            "存量回填": legacy and index == 0,
            "未治理": False,
            "段序号": index + 1,
            "段数": len(anchors),
            "区间起": start.isoformat(),
            "区间止": _iso(window_end),
            "降尘措施": anchor.get("降尘措施"),
            "措施生效日期": anchor.get("生效日期"),
            "前读数": before,
            "后读数": after,
            "降尘效率": efficiency,
            "效率留空原因": reason,
            "备注": notes,
        })
    return segments


def _segment_overlaps_month(segment: dict[str, Any], first_day: date, last_day: date) -> bool:
    start = _parse_date(segment.get("区间起"))
    end = _parse_date(segment.get("区间止"))
    if start is None:
        return False
    if start > last_day:
        return False
    if end is not None and end <= first_day:
        return False
    return True


def build_comparison(
    *,
    as_of: date | None = None,
    measure: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """治理前后对比视图数据：按区域分组、效率降序，超限单独标出。"""
    as_of = as_of or date.today()
    points, measures_by_point, samples_by_point = _load_rows(as_of)

    all_rows: list[dict[str, Any]] = []
    measure_options = set()
    region_options = set()
    for point in sorted(points, key=lambda p: int(p.get("id", 0))):
        point_id = int(point["id"])
        region_options.add(str(point.get("所在区域") or "未分区"))
        for measure_row in measures_by_point.get(point_id, []):
            if measure_row.get("降尘措施"):
                measure_options.add(str(measure_row["降尘措施"]))
        if region and str(point.get("所在区域") or "未分区") != region:
            continue
        segments = build_point_segments(
            point,
            measures_by_point.get(point_id, []),
            samples_by_point.get(point_id, []),
            as_of,
        )
        for segment in segments:
            if measure and not segment["未治理"] and segment["降尘措施"] != measure:
                continue
            # 未治理行在按措施筛选时不展示（它不属于任何措施类别）。
            if measure and segment["未治理"]:
                continue
            all_rows.append(segment)

    groups_map: dict[str, list[dict[str, Any]]] = {}
    for row in all_rows:
        groups_map.setdefault(row["所在区域"] or "未分区", []).append(row)

    groups: list[dict[str, Any]] = []
    for region_name, rows in groups_map.items():
        valid = [r for r in rows if r["降尘效率"] is not None]
        valid.sort(key=lambda r: r["降尘效率"], reverse=True)
        blanks = sorted(
            (r for r in rows if r["降尘效率"] is None),
            key=lambda r: (r["point_id"], r["段序号"]),
        )
        ordered = valid + blanks
        avg = _round1(sum(r["降尘效率"] for r in valid) / len(valid)) if valid else None
        groups.append({
            "区域": region_name,
            "对比段数": len(rows),
            "有效段数": len(valid),
            "超限测点数": len({r["point_id"] for r in rows if r["超限"]}),
            "区域平均效率": avg,
            "rows": ordered,
        })
    # 区域间按平均效率降序，没有有效段的区域排最后，再按区域名稳定排序。
    groups.sort(key=lambda g: (-(g["区域平均效率"] if g["区域平均效率"] is not None else -1), g["区域"]))

    valid_rows = [r for r in all_rows if r["降尘效率"] is not None]
    over_limit = [
        {
            "测点编号": r["测点编号"],
            "所在区域": r["所在区域"],
            "游离二氧化硅": r["游离二氧化硅"],
            "降尘措施": r["降尘措施"],
            "区间起": r["区间起"],
            "前读数": r["前读数"]["value"],
            "后读数": r["后读数"]["value"],
            "降尘效率": r["降尘效率"],
        }
        # 超限测点在分组内只列一次，取效率最高的那段代表。
        for r in sorted(
            (row for row in all_rows if row["超限"]),
            key=lambda row: (row["point_id"], -(row["降尘效率"] if row["降尘效率"] is not None else -1)),
        )
    ]
    seen_points = set()
    over_limit_dedup = []
    for item in over_limit:
        if item["测点编号"] in seen_points:
            continue
        seen_points.add(item["测点编号"])
        over_limit_dedup.append(item)

    return {
        "as_of": as_of.isoformat(),
        "silica_limit": SILICA_LIMIT,
        "filters": {"measure": measure, "region": region},
        "options": {"measures": sorted(measure_options), "regions": sorted(region_options)},
        "summary": {
            "纳入测点数": len({r["point_id"] for r in all_rows}),
            "对比段数": len(all_rows),
            "有效段数": len(valid_rows),
            "留空段数": len(all_rows) - len(valid_rows),
            "超限测点数": len({r["point_id"] for r in all_rows if r["超限"]}),
            "平均降尘效率": _round1(sum(r["降尘效率"] for r in valid_rows) / len(valid_rows)) if valid_rows else None,
        },
        "groups": groups,
        "over_limit": over_limit_dedup,
    }


def build_monthly_report(month: str | None) -> dict[str, Any]:
    """粉尘防治月度报表：结论直接取自对比视图同一口径，保证两边对得上。"""
    if month is None:
        raise ValueError("报表月份必填，格式 YYYY-MM")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("报表月份格式应为 YYYY-MM")
    year, month_num = (int(part) for part in month.split("-"))
    if not 1 <= month_num <= 12:
        raise ValueError("月份必须在 01~12 之间")
    first_day = date(year, month_num, 1)
    last_day = date(year, month_num, calendar.monthrange(year, month_num)[1])

    # 关键：月末时点跑同一份对比口径，报表行是视图行的月度切片，逐字段一致。
    comparison = build_comparison(as_of=last_day)

    conclusions: list[dict[str, Any]] = []
    region_summary: dict[str, dict[str, Any]] = {}
    untreated: list[dict[str, Any]] = []
    for group in comparison["groups"]:
        for row in group["rows"]:
            if row["未治理"]:
                untreated.append({
                    "测点编号": row["测点编号"],
                    "所在区域": row["所在区域"],
                    "原因": row["效率留空原因"],
                })
                continue
            if not _segment_overlaps_month(row, first_day, last_day):
                continue
            if row["降尘效率"] is None:
                conclusion = (
                    f"{row['测点编号']}「{row['降尘措施']}」段（{row['区间起']} 起）："
                    f"{row['效率留空原因']}"
                )
            else:
                conclusion = (
                    f"{row['测点编号']} 采用{row['降尘措施']}，粉尘浓度由 "
                    f"{row['前读数']['value']} 降至 {row['后读数']['value']}"
                    f"（{row['前读数'].get('unit') or row['后读数'].get('unit')}），"
                    f"降尘效率 {row['降尘效率']}%"
                )
                if row["超限"]:
                    conclusion = f"【游离二氧化硅超限 {row['游离二氧化硅']}%】{conclusion}"
                if row["存量回填"]:
                    conclusion += "（存量测点按治理日期回填）"
            conclusions.append({"结论": conclusion, "row": row})

            bucket = region_summary.setdefault(row["所在区域"], {"有效段数": 0, "效率合计": 0.0, "超限测点": set()})
            if row["降尘效率"] is not None:
                bucket["有效段数"] += 1
                bucket["效率合计"] += row["降尘效率"]
            if row["超限"]:
                bucket["超限测点"].add(row["测点编号"])

    regions = [
        {
            "区域": region_name,
            "有效段数": bucket["有效段数"],
            "区域平均效率": _round1(bucket["效率合计"] / bucket["有效段数"]) if bucket["有效段数"] else None,
            "超限测点数": len(bucket["超限测点"]),
        }
        for region_name, bucket in sorted(region_summary.items())
    ]
    valid = [item["row"] for item in conclusions if item["row"]["降尘效率"] is not None]

    return {
        "month": month,
        "区间": [first_day.isoformat(), last_day.isoformat()],
        "silica_limit": SILICA_LIMIT,
        "summary": {
            "纳入测点数": len({item["row"]["point_id"] for item in conclusions}),
            "对比段数": len(conclusions),
            "有效段数": len(valid),
            "留空段数": len(conclusions) - len(valid),
            "超限测点数": len({item["row"]["point_id"] for item in conclusions if item["row"]["超限"]}),
            "未治理测点数": len(untreated),
            "平均降尘效率": _round1(sum(r["降尘效率"] for r in valid) / len(valid)) if valid else None,
        },
        "regions": regions,
        "conclusions": [item["结论"] for item in conclusions],
        "rows": [item["row"] for item in conclusions],
        "untreated": untreated,
    }
