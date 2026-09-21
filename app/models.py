"""请求 / 响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DesignIn(BaseModel):
    """一份待审计的机织纹样设计。"""

    name: str | None = Field(default=None, description="设计名称（可选）")
    cyclic: bool = Field(
        default=False,
        description="纹样是否循环；为 true 时合并矩阵首尾跨边界的连续浮长段",
    )
    max_float: int = Field(..., ge=1, description="允许的最大浮长，超过即判违规")
    threading: list[int] = Field(
        ..., description="经纱穿综映射：threading[w] 为第 w 根经纱所穿综框的索引"
    )
    tie_up: list[list[int]] = Field(
        ...,
        description="绑综矩阵：tie_up[t][s] ∈ {0,1}，踏板 t 是否提升综框 s；"
        "行数 = 踏板数，列数 = 综框数",
    )
    treadling: list[int] = Field(
        ..., description="逐纬踏综序列：treadling[h] 为第 h 纬踩下的踏板索引"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "2/2 twill, 8x8 repeat",
                    "cyclic": True,
                    "max_float": 2,
                    "threading": [0, 1, 2, 3, 0, 1, 2, 3],
                    "tie_up": [
                        [1, 1, 0, 0],
                        [0, 1, 1, 0],
                        [0, 0, 1, 1],
                        [1, 0, 0, 1],
                    ],
                    "treadling": [0, 1, 2, 3, 0, 1, 2, 3],
                }
            ]
        }
    }


class Violation(BaseModel):
    direction: Literal["warp", "weft"] = Field(
        ..., description="warp=经向浮长（经纱连续浮于纬上），weft=纬向浮长"
    )
    yarn_index: int = Field(..., description="纱线索引：经纱列号或纬纱行号")
    start: int = Field(
        ..., description="连续段起点；循环合并段取尾部段的起点（跨边界延伸）"
    )
    length: int = Field(..., description="连续浮长")


class AuditOut(BaseModel):
    exceeds: bool = Field(..., description="是否存在超过 max_float 的浮长")
    interlacing: list[list[int]] = Field(
        ..., description="经纬交织矩阵，1=经纱在上，0=纬纱在上"
    )
    violations: list[Violation]


class DesignOut(BaseModel):
    id: int
    name: str | None
    cyclic: bool
    max_float: int
    shafts: int
    treadles: int
    warp_ends: int
    weft_picks: int
    threading: list[int]
    tie_up: list[list[int]]
    treadling: list[int]
    created_at: str
    audit: AuditOut


class DesignSummary(BaseModel):
    id: int
    name: str | None
    cyclic: bool
    max_float: int
    shafts: int
    treadles: int
    warp_ends: int
    weft_picks: int
    exceeds: bool
    created_at: str


class TieUpSuggestionOut(BaseModel):
    """单步绑综修复建议：翻转一个 tie_up 单元后违规总数严格减少的候选。"""

    treadle: int = Field(..., description="踏板索引（tie_up 行号）")
    shaft: int = Field(..., description="综框索引（tie_up 列号）")
    new_value: Literal[0, 1] = Field(..., description="该单元翻转后的新值")
    remaining_violations: int = Field(
        ..., description="翻转后按原 cyclic、max_float 重跑审计的剩余违规总数"
    )
    affected_points: int = Field(
        ..., description="受影响交织点数：翻转前后交织矩阵中取值变化的单元数"
    )


class ErrorOut(BaseModel):
    detail: str
