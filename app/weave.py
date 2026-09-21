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


@dataclass
class RepairPlanStep:
    step: int               # 第几步（从 1 开始）
    treadle: int            # 踏板索引（tie_up 行号）
    shaft: int              # 综框索引（tie_up 列号）
    old_value: int          # 翻转前该单元的值（0 或 1）
    new_value: int          # 翻转后该单元的值（0 或 1）
    violations_after: int   # 本次翻转后完整审计的违规总数（允许不少于上一步）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RepairPlan:
    changes: int                          # 翻转次数（方案长度）
    final_violations: int                 # 最终违规总数（必须严格少于原设计）
    changed_points: int                   # 最终矩阵相对原矩阵的变化点数
    steps: list[RepairPlanStep]           # 逐步翻转序列
    violations: list[FloatViolation]      # 最终违规列表
    interlacing: list[list[int]]          # 最终交织矩阵

    def to_dict(self) -> dict:
        return {
            "changes": self.changes,
            "final_violations": self.final_violations,
            "changed_points": self.changed_points,
            "steps": [s.to_dict() for s in self.steps],
            "violations": [v.to_dict() for v in self.violations],
            "interlacing": self.interlacing,
        }


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


def violations_of_matrix(
    matrix: list[list[int]],
    max_float: int,
    cyclic: bool,
) -> list[FloatViolation]:
    """对给定交织矩阵找出所有长度超过 max_float 的浮长段。"""
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

    return violations


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
    return matrix, violations_of_matrix(matrix, max_float, cyclic)


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


def _matrix_diff(a: list[list[int]], b: list[list[int]]) -> int:
    return sum(
        x != y
        for row_a, row_b in zip(a, b)
        for x, y in zip(row_a, row_b)
    )


def search_repair_plans(
    threading: list[int],
    tie_up: list[list[int]],
    treadling: list[int],
    max_float: int,
    cyclic: bool,
    max_changes: int = 2,
    limit: int = 50,
) -> list[RepairPlan]:
    """在最多 max_changes（1～3）次绑综单元翻转内搜索修复方案。

    规则：
    - 广度优先逐层展开 1..max_changes 步翻转；中间状态允许违规数不下降，
      但方案终态的违规总数必须严格少于原设计，否则不收录；
    - 按绑综状态（tie_up 0/1 位图）去重：同一状态只保留首次到达的翻转序列，
      且一条序列内同一单元不会被重复翻转（翻转两次等于未翻转）；
    - 单元按 (treadle, shaft) 字典序展开，因此首次到达某状态的序列即
      翻转序列字典序最小者；
    - 收录方案按 (最终违规数, 改动数, 最终矩阵相对原矩阵的变化点数,
      翻转序列字典序) 升序稳定排序，等价终态只保留排序最优的序列；
    - 返回前 limit 条。不修改传入的 tie_up。
    """
    if not isinstance(max_changes, int) or isinstance(max_changes, bool):
        raise DesignError("max_changes must be an integer")
    if not 1 <= max_changes <= 3:
        raise DesignError("max_changes must be within [1, 3]")
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise DesignError("limit must be an integer")
    if not 1 <= limit <= 50:
        raise DesignError("limit must be within [1, 50]")

    validate_design(threading, tie_up, treadling, max_float)

    treadles = len(tie_up)
    shafts = len(tie_up[0])
    cells = [(t, s) for t in range(treadles) for s in range(shafts)]
    base_mask = sum(
        tie_up[t][s] << (t * shafts + s) for t, s in cells
    )

    base_matrix, base_violations = audit_floats(
        threading, tie_up, treadling, max_float, cyclic
    )
    base_count = len(base_violations)

    # 踩同一踏板的纬、穿同一综框的经交叉处的交织点，翻转该单元时一起变化
    affected_points: list[tuple[list[int], list[int]]] = []
    for t, s in cells:
        picks_h = [h for h, pick in enumerate(treadling) if pick == t]
        ends_w = [w for w, shaft in enumerate(threading) if shaft == s]
        affected_points.append((picks_h, ends_w))

    # BFS 节点：(tie_up 位图, 当前矩阵, 翻转单元索引序列, 逐步记录)
    # visited 保证每个绑综状态只展开一次；按单元 (treadle, shaft) 字典序展开，
    # 首次到达某状态的序列即翻转序列字典序最小者。
    visited: set[int] = {base_mask}
    frontier: list[tuple[int, list[list[int]], list[int], list[RepairPlanStep]]] = [
        (base_mask, base_matrix, [], [])
    ]
    plans: list[RepairPlan] = []

    for depth in range(1, max_changes + 1):
        next_frontier: list[
            tuple[int, list[list[int]], list[int], list[RepairPlanStep]]
        ] = []
        for mask, matrix, seq, steps in frontier:
            used = set(seq)
            for idx, (t, s) in enumerate(cells):
                if idx in used:
                    continue
                bit = t * shafts + s
                child_mask = mask ^ (1 << bit)
                if child_mask in visited:
                    continue
                visited.add(child_mask)

                old_value = (mask >> bit) & 1
                new_value = 1 - old_value
                picks_h, ends_w = affected_points[idx]
                child_matrix = [row[:] for row in matrix]
                for h in picks_h:
                    for w in ends_w:
                        child_matrix[h][w] = new_value

                child_violations = violations_of_matrix(
                    child_matrix, max_float, cyclic
                )
                child_seq = seq + [idx]
                child_steps = steps + [
                    RepairPlanStep(
                        step=depth,
                        treadle=t,
                        shaft=s,
                        old_value=old_value,
                        new_value=new_value,
                        violations_after=len(child_violations),
                    )
                ]

                # 中间状态允许违规数不下降：仍入队以便继续展开；
                # 仅当终态严格优于原设计时收录为方案。
                if depth < max_changes:
                    next_frontier.append(
                        (child_mask, child_matrix, child_seq, child_steps)
                    )
                if len(child_violations) < base_count:
                    plans.append(
                        RepairPlan(
                            changes=len(child_seq),
                            final_violations=len(child_violations),
                            changed_points=_matrix_diff(base_matrix, child_matrix),
                            steps=child_steps,
                            violations=child_violations,
                            interlacing=child_matrix,
                        )
                    )
        frontier = next_frontier

    plans.sort(
        key=lambda p: (
            p.final_violations,
            p.changes,
            p.changed_points,
            tuple((s.treadle, s.shaft) for s in p.steps),
        )
    )
    return plans[:limit]
