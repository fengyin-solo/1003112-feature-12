"""粉尘防治接口：维护粉尘测点，覆盖限值预警、超标治理、治理确认等动作。

另提供治理前后对比视图（/compare）与粉尘防治月度报表（/monthly-report），
两者共用 services/dust_compare 的同一份分段口径。
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dust import DustService
from app.services import dust_compare

router = APIRouter(prefix="/api/dust", tags=["粉尘防治"])

service = DustService()

LIST_FIELDS = ["测点编号", "所在区域", "粉尘浓度", "游离二氧化硅", "降尘措施", "降尘效率", "监测日期", "测点状态"]
STATUSES = ["达标", "接近限值", "超标", "已治理"]


# 静态路径必须声明在 /{entry_id} 之前，否则会被动态参数路由抢先匹配。
@router.get("/compare")
def compare_view(
    measure: str | None = Query(default=None, description="只看某一类降尘措施"),
    region: str | None = Query(default=None, description="只看某个区域"),
    as_of: str | None = Query(default=None, description="对比时点 YYYY-MM-DD，默认今天"),
) -> dict[str, Any]:
    """治理前后对比：按区域分组、效率降序，游离二氧化硅超限单独标出。

    治理前未采样、治理后未复测、单位不一致等情况效率留空并写明原因，绝不记零。
    """
    cutoff = None
    if as_of:
        try:
            year, month, day = (int(part) for part in as_of.split("-"))
            cutoff = date_type(year, month, day)
        except ValueError:
            raise HTTPException(status_code=400, detail="as_of 格式应为 YYYY-MM-DD")
    return dust_compare.build_comparison(as_of=cutoff, measure=measure, region=region)


@router.get("/monthly-report")
def monthly_report(
    month: str = Query(..., description="报表月份 YYYY-MM"),
) -> dict[str, Any]:
    """粉尘防治月度报表：对比结论与对比视图同源，逐行对得上。"""
    try:
        return dust_compare.build_monthly_report(month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出粉尘防治清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dust", "total": total, "items": items}


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
