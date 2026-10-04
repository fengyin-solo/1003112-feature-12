"""粉尘防治业务规则：状态流转、字段校验，以及治理前后对比的统一计算口径。

对比口径（视图和月度报表都只走这一份，不允许各算各的）：
1. 分段只按降尘措施的「生效日期」切分。同一测点措施变更后，新措施从生效日期起另开一段，
   旧区间按当时的措施保留——两种做法冲突时一律以生效日期为准。
2. 读数按「锚点日期」归入当时的措施区间：
   - 措施 i 的治理前窗口 = [上一措施生效日, 本措施生效日)，第一段为早于首个生效日的全部读数；
   - 措施 i 的治理后窗口 = [本措施生效日, 下一措施生效日)。
   因此旧措施治理后窗口内的读数，同时就是下一措施的治理前基线。
3. 采样中断（浓度作废）不参与平均；中断后补回来的读数带着原中断位置的锚点日期，接着原来
   那段续上，不会因为补测日期落在新措施区间而被算到新措施头上。
4. 治理前没采样或治理后没复测时，降尘效率留空并写明原因，绝不算成 0。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.store import store

MODULE = "dust"
POINT_TABLE = "dust_point"
MEASURE_TABLE = "dust_measure"
READING_TABLE = "dust_reading"

# 游离二氧化硅含量 ≥10% 时适用更严的粉尘限值（GBZ 2.1），对比视图单独标出。
LIMIT_FREE_SILICA_PCT = 10.0
# 只有这两种状态的读数参与浓度平均；「采样中断」读数浓度作废，仅保留中断痕迹。
VALID_READING_STATES = ("正常", "补测")
REQUIRED_FIELDS = ["测点编号", "所在区域", "粉尘浓度"]
STATUS_ORDER = ["达标", "接近限值", "超标", "已治理"]
ACTION_RULES = {"限值预警": "接近限值", "超标治理": "超标", "治理确认": "已治理"}
NEGATIVE_ACTIONS = []


def _to_number(value: Any) -> float | None:
    """把「8.2」这类字符串读数转成数字；空串、文字、None 都视为没有读数。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _half_up(value: float, digits: int) -> float:
    """四舍五入（避免内置 round 的银行家舍入在 3.85 这类值上反直觉）。"""
    quantum = Decimal("1").scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return _half_up(sum(values) / len(values), 2)


class DustService:
    def __init__(self) -> None:
        # 存量测点按治理日期回填是迁移逻辑，只做一次：测点编号已存在就跳过。
        self._backfill_legacy_rows()

    # ------------------------------------------------------------------ 存量回填
    def _backfill_legacy_rows(self) -> None:
        """老台账（dust 表）按治理日期回填到新三表，标记「存量回填」，幂等。"""
        points = store.rows(POINT_TABLE)
        measures = store.rows(MEASURE_TABLE)
        readings = store.rows(READING_TABLE)
        known_codes = {row.get("测点编号") for row in points}

        for legacy in store.rows(MODULE):
            code = str(legacy.get("测点编号") or "").strip()
            if not code or code in known_codes:
                continue
            silica = _to_number(legacy.get("游离二氧化硅"))
            points.append({
                "id": self._next_id(points),
                "测点编号": code,
                "所在区域": legacy.get("所在区域", ""),
                "游离二氧化硅": silica if silica is not None else 0.0,
                "备注": "存量回填",
            })
            known_codes.add(code)

            treat_date = str(legacy.get("治理日期") or "").strip()
            measure_name = str(legacy.get("降尘措施") or "").strip()
            before = _to_number(legacy.get("粉尘浓度"))
            sample_date = str(legacy.get("监测日期") or "").strip()
            after = _to_number(legacy.get("治理后浓度"))

            if before is not None and sample_date:
                readings.append({
                    "id": self._next_id(readings),
                    "测点编号": code,
                    "采样日期": sample_date,
                    "锚点日期": sample_date,
                    "粉尘浓度": before,
                    "采样状态": "正常",
                    "备注": "存量回填：治理前读数",
                })
            if measure_name and treat_date:
                measures.append({
                    "id": self._next_id(measures),
                    "测点编号": code,
                    "降尘措施": measure_name,
                    "生效日期": treat_date,
                    "备注": "存量回填：以治理日期作为措施生效日期",
                })
                if after is not None:
                    readings.append({
                        "id": self._next_id(readings),
                        "测点编号": code,
                        "采样日期": treat_date,
                        "锚点日期": treat_date,
                        "粉尘浓度": after,
                        "采样状态": "正常",
                        "备注": "存量回填：治理后复测",
                    })

        # 台账里的「降尘效率」也回填成同一口径算出来的结果，保证列表、视图、月报对得上。
        for segment in self.build_comparison()["items"]:
            if segment.get("数据来源") != "存量回填":
                continue
            for legacy in store.rows(MODULE):
                if legacy.get("测点编号") == segment["测点编号"] and segment.get("降尘效率") is not None:
                    legacy["降尘效率"] = f"{segment['降尘效率']}%"

    @staticmethod
    def _next_id(rows: list[dict[str, Any]]) -> int:
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    # ------------------------------------------------------------------ 台账动作
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("测点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"粉尘测点 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于粉尘防治可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"粉尘测点已{action}"

    # ------------------------------------------------------------------ 对比口径
    def _point_readings(self, code: str) -> list[dict[str, Any]]:
        rows = [dict(r) for r in store.rows(READING_TABLE) if r.get("测点编号") == code]
        for row in rows:
            row["_浓度"] = _to_number(row.get("粉尘浓度"))
        # 分段归段只认锚点日期；锚点相同再按实际采样日期、id 稳定排序。
        rows.sort(key=lambda r: (str(r.get("锚点日期") or ""), str(r.get("采样日期") or ""), int(r.get("id", 0))))
        return rows

    @staticmethod
    def _window(readings: list[dict[str, Any]], start: str | None, end: str | None) -> list[dict[str, Any]]:
        """取锚点日期落在 [start, end) 的读数；start/end 为空表示不设该侧边界。"""
        out = []
        for row in readings:
            anchor = str(row.get("锚点日期") or "")
            if start and anchor < start:
                continue
            if end and anchor >= end:
                continue
            out.append(row)
        return out

    @staticmethod
    def _window_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        valid = [r for r in rows if r.get("采样状态") in VALID_READING_STATES and r["_浓度"] is not None]
        values = [float(r["_浓度"]) for r in valid]
        supplements = [r for r in rows if r.get("采样状态") == "补测"]
        interrupts = [r for r in rows if r.get("采样状态") == "采样中断"]
        return {
            "浓度均值": _average(values),
            "读数条数": len(valid),
            "读数日期": [str(r.get("锚点日期") or "") for r in valid],
            "补测条数": len(supplements),
            "采样中断次数": len(interrupts),
        }

    def _build_point_segments(self, point: dict[str, Any]) -> list[dict[str, Any]]:
        code = str(point.get("测点编号") or "")
        measures = sorted(
            (dict(m) for m in store.rows(MEASURE_TABLE) if m.get("测点编号") == code),
            key=lambda m: (str(m.get("生效日期") or ""), int(m.get("id", 0))),
        )
        readings = self._point_readings(code)
        silica = _to_number(point.get("游离二氧化硅")) or 0.0
        silica_over = silica >= LIMIT_FREE_SILICA_PCT
        source = "存量回填" if "回填" in str(point.get("备注") or "") else "采样台账"

        def pack(
            measure: dict[str, Any] | None,
            index: int,
            start: str | None,
            end: str | None,
        ) -> dict[str, Any]:
            pre_rows = self._window(readings, start, end)
            if measure is not None:
                next_eff = str(measures[index + 1].get("生效日期") or "") if index + 1 < len(measures) else None
                post_rows = self._window(readings, str(measure.get("生效日期") or ""), next_eff)
            else:
                post_rows = []
            pre = self._window_summary(pre_rows)
            post = self._window_summary(post_rows)

            efficiency: float | None = None
            empty_reason = ""
            if measure is None:
                empty_reason = "尚未落实降尘措施，暂无治理前后对比"
            elif pre["浓度均值"] is None:
                empty_reason = "治理前未采样，无法计算降尘效率"
            elif post["浓度均值"] is None:
                empty_reason = "治理后未复测，无法计算降尘效率"
            else:
                efficiency = _half_up(
                    (pre["浓度均值"] - post["浓度均值"]) / pre["浓度均值"] * 100, 1
                )

            segment = {
                "测点编号": code,
                "所在区域": point.get("所在区域", ""),
                "游离二氧化硅": silica,
                "二氧化硅超限": silica_over,
                "降尘措施": None if measure is None else measure.get("降尘措施"),
                "生效日期": None if measure is None else measure.get("生效日期"),
                "治理前浓度": pre["浓度均值"],
                "治理前读数条数": pre["读数条数"],
                "治理前读数日期": pre["读数日期"],
                "治理前补测条数": pre["补测条数"],
                "治理后浓度": post["浓度均值"],
                "治理后读数条数": post["读数条数"],
                "治理后读数日期": post["读数日期"],
                "治理后补测条数": post["补测条数"],
                "治理后中断次数": post["采样中断次数"],
                "降尘效率": efficiency,
                "效率留空原因": empty_reason,
                "数据来源": source,
                "措施备注": "" if measure is None else str(measure.get("备注") or ""),
            }
            segment["对比结论"] = self._conclusion(segment)
            return segment

        if not measures:
            return [pack(None, 0, None, None)]

        segments: list[dict[str, Any]] = []
        for index, measure in enumerate(measures):
            eff_date = str(measure.get("生效日期") or "")
            start = str(measures[index - 1].get("生效日期") or "") if index > 0 else None
            segments.append(pack(measure, index, start, eff_date))
        return segments

    @staticmethod
    def _conclusion(segment: dict[str, Any]) -> str:
        silica_tip = (
            f"游离二氧化硅{segment['游离二氧化硅']}%已达{LIMIT_FREE_SILICA_PCT:g}%限值，须按矽尘要求重点防控。"
            if segment["二氧化硅超限"]
            else ""
        )
        measure = segment["降尘措施"]
        if measure is None:
            return f"{segment['效率留空原因']}。{silica_tip}".strip()
        head = f"「{measure}」{segment['生效日期']}生效"
        if segment["降尘效率"] is None:
            return f"{head}：{segment['效率留空原因']}。{silica_tip}".strip()
        resume = ""
        if segment["治理后补测条数"]:
            resume = f"含补测{segment['治理后补测条数']}条，已按锚点日期续入原措施区间；"
        return (
            f"{head}：{resume}粉尘浓度由治理前{segment['治理前浓度']}mg/m³"
            f"降至治理后{segment['治理后浓度']}mg/m³，降尘效率{segment['降尘效率']}%。"
            f"{silica_tip}"
        )

    def build_comparison(
        self,
        *,
        area: str | None = None,
        measure: str | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """治理前后对比的唯一出口：视图、月度报表都消费这里的结果。"""
        segments: list[dict[str, Any]] = []
        for point in sorted(store.rows(POINT_TABLE), key=lambda p: int(p.get("id", 0))):
            segments.extend(self._build_point_segments(point))

        if area:
            segments = [s for s in segments if s["所在区域"] == area]
        if measure:
            segments = [s for s in segments if s["降尘措施"] == measure]
        if keyword:
            segments = [s for s in segments if keyword in s["测点编号"]]

        # 按降尘效率从高到低；算不出来的留空段统一排最后，不允许冒充 0% 参与排序。
        segments.sort(key=lambda s: (
            s["降尘效率"] is None,
            -(s["降尘效率"] or 0.0),
            s["所在区域"],
            s["测点编号"],
            str(s["生效日期"] or "9999"),
        ))

        measures = sorted({s["降尘措施"] for s in segments if s["降尘措施"]})
        areas = sorted({s["所在区域"] for s in segments if s["所在区域"]})
        valid_eff = [s["降尘效率"] for s in segments if s["降尘效率"] is not None]
        return {
            "items": segments,
            "total": len(segments),
            "measures": measures,
            "areas": areas,
            "游离二氧化硅限值": LIMIT_FREE_SILICA_PCT,
            "summary": {
                "分段总数": len(segments),
                "可计算效率": len(valid_eff),
                "效率留空": len(segments) - len(valid_eff),
                "二氧化硅超限": sum(1 for s in segments if s["二氧化硅超限"]),
                "平均降尘效率": _average(valid_eff),
            },
        }

    # ------------------------------------------------------------------ 月度报表
    @staticmethod
    def _segment_months(segment: dict[str, Any]) -> set[str]:
        """分段归属月份：措施生效当月，或有治理后复测读数的月份。"""
        months: set[str] = set()
        if segment.get("生效日期"):
            months.add(str(segment["生效日期"])[:7])
        months.update(str(d)[:7] for d in segment.get("治理后读数日期", []))
        return months

    def available_months(self) -> list[str]:
        months: set[str] = set()
        for segment in self.build_comparison()["items"]:
            months.update(self._segment_months(segment))
        return sorted(months, reverse=True)

    def monthly_report(self, month: str | None = None) -> dict[str, Any]:
        """粉尘防治月度报表：直接取对比口径的分段按月过滤，结论与视图逐条一致。"""
        all_segments = self.build_comparison()["items"]
        months = self.available_months()
        month = month or (months[0] if months else None)

        items = [s for s in all_segments if month in self._segment_months(s)] if month else []
        # 月内同样按效率高低排列，留空段垫底。
        items.sort(key=lambda s: (
            s["降尘效率"] is None,
            -(s["降尘效率"] or 0.0),
            s["测点编号"],
        ))
        valid_eff = [s["降尘效率"] for s in items if s["降尘效率"] is not None]
        reason_breakdown: dict[str, int] = {}
        for segment in items:
            if segment["降尘效率"] is None and segment["效率留空原因"]:
                reason_breakdown[segment["效率留空原因"]] = (
                    reason_breakdown.get(segment["效率留空原因"], 0) + 1
                )
        measure_breakdown: dict[str, int] = {}
        for segment in items:
            if segment["降尘措施"]:
                measure_breakdown[segment["降尘措施"]] = (
                    measure_breakdown.get(segment["降尘措施"], 0) + 1
                )
        return {
            "月份": month,
            "可选月份": months,
            "口径说明": "与治理前后对比视图共用同一分段口径：按降尘措施生效日期分段，补测按锚点日期续入原区间；效率缺基线或缺复测时留空并注明原因。",
            "items": items,
            "summary": {
                "治理分段数": len(items),
                "涉及测点数": len({s["测点编号"] for s in items}),
                "可计算效率": len(valid_eff),
                "效率留空": len(items) - len(valid_eff),
                "留空原因": reason_breakdown,
                "二氧化硅超限": sum(1 for s in items if s["二氧化硅超限"]),
                "平均降尘效率": _average(valid_eff),
                "措施分布": measure_breakdown,
            },
        }
