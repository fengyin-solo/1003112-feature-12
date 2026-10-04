"""粉尘防治接口：维护粉尘测点，覆盖限值预警、超标治理、治理确认等动作。

另含治理前后对比视图与月度报表两个只读接口：两者共用 service 里同一份分段口径，
保证报表结论与视图逐条对得上。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dust import DustService

router = APIRouter(prefix="/api/dust", tags=["粉尘防治"])

service = DustService()

LIST_FIELDS = ["测点编号", "所在区域", "粉尘浓度", "游离二氧化硅", "降尘措施", "降尘效率", "监测日期", "测点状态"]
STATUSES = ["达标", "接近限值", "超标", "已治理"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按测点编号检索"),
    status: str | None = Query(default=None, description="达标、接近限值、超标、已治理"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按测点编号与状态过滤粉尘防治列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/comparison")
def comparison(
    area: str | None = Query(default=None, description="按所在区域过滤"),
    measure: str | None = Query(default=None, description="只看某一类降尘措施"),
    keyword: str | None = Query(default=None, description="按测点编号检索"),
) -> dict[str, Any]:
    """治理前后对比视图：同测点治理前/治理后浓度并排，效率从高到低，按区域分组。

    效率算不出来的分段不返回数值，只返回留空原因（治理前未采样/治理后未复测/未落实措施）。
    """
    return service.build_comparison(area=area, measure=measure, keyword=keyword)


@router.get("/monthly-report")
def monthly_report(
    month: str | None = Query(default=None, description="报表月份 YYYY-MM，缺省取最近有数据的月份"),
) -> dict[str, Any]:
    """粉尘防治月度报表：对比口径按月过滤，结论与对比视图完全一致。"""
    if month and (len(month) != 7 or month[4] != "-"):
        raise HTTPException(status_code=400, detail="月份格式应为 YYYY-MM，例如 2026-09")
    return service.monthly_report(month)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出粉尘防治清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dust", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条粉尘测点明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"粉尘测点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条粉尘测点，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="粉尘测点已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条粉尘测点执行限值预警、超标治理、治理确认；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
