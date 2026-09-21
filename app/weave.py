"""机织纹样核心计算：由穿综、绑综、踏综生成经纬交织矩阵，并审计浮长。

约定：
- 交织矩阵 interlacing[h][w] == 1 表示第 h 纬处第 w 根经纱在纬纱之上（经纱浮起），
  == 0 表示纬纱在上。
- 经向浮长（warp float）：同一根经纱连续浮于纬纱之上的纬纱数，即矩阵某一列中
  连续的 1。
- 纬向浮长（weft float）：同一根纬纱连续浮于经纱之上的经纱数，即矩阵某一行中
  连续的 0。
- 纹样声明循环（cyclic）时，矩阵在经纬两个方向上都首尾相接：跨越末行/首行
  （或末列/首列）边界的同向连续段合并为一段，其起点取尾部段的起点，长度为
  两段之和。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


class DesignError(ValueError):
    """设计校验失败（维度不一致或索引越界等），整份设计应被拒绝。"""


@dataclass
class FloatViolation:
    direction: str   # "warp" | "weft"
    yarn_index: int  # 经纱索引（列）或纬纱索引（行）
    start: int       # 连续段起点（对循环合并段，为尾部段的起点）
    length: int      # 连续浮长

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TieUpSuggestion:
    treadle: int               # 踏板索引（tie_up 行号）
    shaft: int                 # 综框索引（tie_up 列号）
    new_value: int             # 该单元翻转后的新值（0 或 1）
    remaining_violations: int  # 翻转后完整审计的剩余违规总数
    affected_points: int       # 交织矩阵中取值发生变化的点数

    def to_dict(self) -> dict:
        return asdict(self)


def _check_int(value, what: str) -> None:
    # bool 是 int 的子类，单独排除，避免 True/False 被当成合法索引
    if not isinstance(value, int) or isinstance(value, bool):
        raise DesignError(f"{what} must be an integer, got {value!r}")


def validate_design(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
) -> tuple[int, int]:
    """校验设计，返回 (综框数, 踏板数)。任何不一致都抛 DesignError。"""

    _check_int(max_float, "max_float")
    if max_float < 1:
        raise DesignError("max_float must be >= 1")

    if not threading:
        raise DesignError("threading must be a non-empty list")
    if not treadling:
        raise DesignError("treadling must be a non-empty list")
    if not tie_up:
        raise DesignError("tie_up must be a non-empty matrix")

    shafts = len(tie_up[0])
    if shafts == 0:
        raise DesignError("tie_up rows must be non-empty")
    for r, row in enumerate(tie_up):
        if not isinstance(row, list) or len(row) != shafts:
            got = len(row) if isinstance(row, list) else type(row).__name__
            raise DesignError(
                f"tie_up row {r} has length {got}, expected {shafts} "
                "(tie_up must be a rectangular matrix)"
            )
        for c, v in enumerate(row):
            _check_int(v, f"tie_up[{r}][{c}]")
            if v not in (0, 1):
                raise DesignError(f"tie_up[{r}][{c}] must be 0 or 1, got {v}")

    treadles = len(tie_up)

    for i, s in enumerate(threading):
        _check_int(s, f"threading[{i}]")
        if s < 0 or s >= shafts:
            raise DesignError(
                f"threading[{i}]={s} out of range: valid shaft index is [0, {shafts})"
            )
    for i, t in enumerate(treadling):
        _check_int(t, f"treadling[{i}]")
        if t < 0 or t >= treadles:
            raise DesignError(
                f"treadling[{i}]={t} out of range: valid treadle index is [0, {treadles})"
            )

    return shafts, treadles


def build_interlacing(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
) -> list[list[int]]:
    """生成交织矩阵：interlacing[h][w] = tie_up[treadling[h]][threading[w]]。"""
    return [
        [tie_up[pick][shaft] for shaft in threading]
        for pick in treadling
    ]


def _runs(line: list[int], target: int, cyclic: bool) -> list[tuple[int, int]]:
    """返回一维序列中所有等于 target 的连续段 [(start, length), ...]。

    cyclic=True 时，若首段从 0 开始且尾段到达序列末尾，则两段在循环意义上
    相连，合并为 (尾段起点, 两段长度之和)。整行皆为 target 时只有一段，
    不参与合并，长度即序列长度。
    """
    n = len(line)
    runs: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if line[i] == target:
            j = i + 1
            while j < n and line[j] == target:
                j += 1
            runs.append((i, j - i))
            i = j
        else:
            i += 1

    if cyclic and len(runs) > 1:
        first, last = runs[0], runs[-1]
        if first[0] == 0 and last[0] + last[1] == n:
            merged = (last[0], last[1] + first[1])
            runs = [merged] + runs[1:-1]
    return runs


def audit_floats(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
    cyclic: bool,
) -> tuple[list[list[int]], list[FloatViolation]]:
    """生成交织矩阵并找出所有长度超过 max_float 的浮长段。"""
    validate_design(threading, tie_up, treadling, max_float)
    matrix = build_interlacing(threading, tie_up, treadling)
    picks = len(matrix)          # 纬纱数（行数）
    ends = len(matrix[0])        # 经纱数（列数）

    violations: list[FloatViolation] = []

    # 经向浮长：每根经纱（列）上连续的 1
    for w in range(ends):
        column = [matrix[h][w] for h in range(picks)]
        for start, length in _runs(column, 1, cyclic):
            if length > max_float:
                violations.append(FloatViolation("warp", w, start, length))

    # 纬向浮长：每根纬纱（行）上连续的 0
    for h in range(picks):
        for start, length in _runs(matrix[h], 0, cyclic):
            if length > max_float:
                violations.append(FloatViolation("weft", h, start, length))

    return matrix, violations


def suggest_tie_up_flips(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
    cyclic: bool,
) -> list[TieUpSuggestion]:
    """单步绑综修复建议：逐一翻转 tie_up 的一个 0/1 单元，按原 cyclic、max_float
    重跑完整审计，仅保留违规总数严格减少的候选。

    受影响交织点数 = 翻转前后交织矩阵中取值发生变化的单元数（即所有
    踩该踏板的纬 × 穿该综框的经 的交叉点）。结果按 (剩余违规数, 受影响点数,
    踏板索引, 综框索引) 升序稳定排序。不修改传入的 tie_up。
    """
    base_matrix, base_violations = audit_floats(
        threading, tie_up, treadling, max_float, cyclic
    )
    base_count = len(base_violations)

    suggestions: list[TieUpSuggestion] = []
    for t, row in enumerate(tie_up):
        for s, value in enumerate(row):
            flipped = [r[:] for r in tie_up]
            flipped[t][s] = 1 - value
            matrix, violations = audit_floats(
                threading, flipped, treadling, max_float, cyclic
            )
            if len(violations) >= base_count:
                continue
            affected = sum(
                a != b
                for row_a, row_b in zip(base_matrix, matrix)
                for a, b in zip(row_a, row_b)
            )
            suggestions.append(
                TieUpSuggestion(t, s, 1 - value, len(violations), affected)
            )

    suggestions.sort(
        key=lambda x: (x.remaining_violations, x.affected_points, x.treadle, x.shaft)
    )
    return suggestions
