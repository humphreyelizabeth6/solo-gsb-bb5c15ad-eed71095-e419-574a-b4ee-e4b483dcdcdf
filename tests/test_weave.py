"""核心织纹逻辑的单元测试（不依赖 FastAPI）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.weave import (  # noqa: E402
    DesignError,
    audit_floats,
    build_interlacing,
    search_repair_plans,
    suggest_tie_up_flips,
    validate_design,
    _runs,
)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def test_interlacing():
    # 平纹：2 综 2 踏
    threading = [0, 1, 0, 1]
    tie_up = [[1, 0], [0, 1]]
    treadling = [0, 1, 0, 1]
    m = build_interlacing(threading, tie_up, treadling)
    check(m == [[1, 0, 1, 0],
                [0, 1, 0, 1],
                [1, 0, 1, 0],
                [0, 1, 0, 1]], "plain weave interlacing")


def test_warp_and_weft_floats():
    # 第 0 根经纱连续 3 纬浮起；第 0 纬纬纱连续浮过 3 根经纱
    threading = [0, 0, 0]
    tie_up = [[1], [0]]
    treadling = [0, 0, 0, 1]
    # 矩阵:
    # 1 1 1
    # 1 1 1
    # 1 1 1
    # 0 0 0
    _, v = audit_floats(threading, tie_up, treadling, max_float=2, cyclic=False)
    warp = [x for x in v if x.direction == "warp"]
    weft = [x for x in v if x.direction == "weft"]
    check(len(warp) == 3 and all(x.length == 3 and x.start == 0 for x in warp),
          "warp floats of length 3 on all 3 ends")
    check(len(weft) == 1 and weft[0].yarn_index == 3 and weft[0].length == 3,
          "weft float of length 3 on pick 3")
    check(all(x.yarn_index in (0, 1, 2) for x in warp), "warp yarn indices")


def test_cyclic_merge():
    # 列: 1 1 0 0 1 1 -> 非循环两段(0,2),(4,2)；循环合并为 (4,4)
    check(_runs([1, 1, 0, 0, 1, 1], 1, False) == [(0, 2), (4, 2)],
          "non-cyclic runs")
    check(_runs([1, 1, 0, 0, 1, 1], 1, True) == [(4, 4)],
          "cyclic merge across boundary")
    # 全 1 列：只有一段，不自我合并
    check(_runs([1, 1, 1], 1, True) == [(0, 3)],
          "all-float line stays a single run")
    # 首尾不相接时不合并
    check(_runs([1, 0, 0, 1, 0], 1, True) == [(0, 1), (3, 1)],
          "no merge when boundary not both target")


def test_cyclic_audit_end_to_end():
    # 2/2 斜纹 4x4 循环，max_float=2：循环合并后每根经纱浮长恰为 2，不超限
    threading = [0, 1, 2, 3]
    tie_up = [[1, 1, 0, 0],
              [0, 1, 1, 0],
              [0, 0, 1, 1] if False else [0, 0, 1, 1],
              [1, 0, 0, 1]]
    treadling = [0, 1, 2, 3]
    _, v = audit_floats(threading, tie_up, treadling, max_float=2, cyclic=True)
    check(v == [], "2/2 twill cyclic: no float exceeds 2")
    # 非循环时边界处浮长仍 <= 2，也不超限
    _, v2 = audit_floats(threading, tie_up, treadling, max_float=2, cyclic=False)
    check(v2 == [], "2/2 twill non-cyclic: no float exceeds 2")
    # max_float=1 时循环与非循环都应报出浮长 2
    _, v3 = audit_floats(threading, tie_up, treadling, max_float=1, cyclic=True)
    check(len(v3) > 0 and all(x.length == 2 for x in v3),
          "2/2 twill with max_float=1 flags length-2 floats")


def test_cyclic_wrap_violation():
    # 构造跨边界合并后才超限的情形：列 1 1 0 1 1，max_float=2
    # 非循环：两段长度 2、2，不超限；循环：合并为长度 4，超限
    threading = [0]
    tie_up = [[1], [0]]
    treadling = [0, 0, 1, 0, 0]
    _, v_off = audit_floats(threading, tie_up, treadling, max_float=2, cyclic=False)
    check(v_off == [], "wrap-around float not flagged when cyclic=False")
    _, v_on = audit_floats(threading, tie_up, treadling, max_float=2, cyclic=True)
    check(len(v_on) == 1 and v_on[0].direction == "warp"
          and v_on[0].yarn_index == 0 and v_on[0].start == 3 and v_on[0].length == 4,
          "wrap-around float merged to length 4 starting at 3")


def test_tie_up_suggestions_basic():
    # 列 1 1 0 1 1 循环合并为长度 4 的经向浮长（max_float=2），共 1 条违规
    threading = [0]
    tie_up = [[1], [0]]
    treadling = [0, 0, 1, 0, 0]
    suggestions = suggest_tie_up_flips(threading, tie_up, treadling, 2, True)
    # 仅 flip tie_up[0][0] -> 0 可消除违规；受影响点 = 踩踏板 0 的 4 纬 × 1 经
    check(len(suggestions) == 1, "exactly one improving flip")
    s = suggestions[0]
    check(s.treadle == 0 and s.shaft == 0 and s.new_value == 0,
          "flip tie_up[0][0] to 0")
    check(s.remaining_violations == 0, "no violations remain after flip")
    check(s.affected_points == 4, "4 interlacing points affected")
    # 原设计不被修改
    check(tie_up == [[1], [0]], "original tie_up unchanged")


def test_tie_up_suggestions_ordering():
    # 3 根经纱各浮 3 纬 + 末纬纬浮 3 经，共 4 条违规；多个单步翻转可改善
    threading = [0, 1, 1]
    tie_up = [[1, 1], [0, 0]]
    treadling = [0, 0, 0, 1]
    suggestions = suggest_tie_up_flips(threading, tie_up, treadling, 2, False)
    got = [(s.treadle, s.shaft, s.remaining_violations, s.affected_points)
           for s in suggestions]
    # 按 (剩余违规数, 受影响点数, 踏板索引, 综框索引) 升序
    check(got == [(0, 1, 2, 6), (1, 0, 3, 1), (1, 1, 3, 2), (0, 0, 3, 3)],
          "suggestions sorted by remaining, affected, treadle, shaft")
    check(all(s.remaining_violations < 4 for s in suggestions),
          "all candidates strictly reduce violations")
    check(tie_up == [[1, 1], [0, 0]], "original tie_up unchanged")


def test_tie_up_suggestions_no_improvement():
    # 平纹无违规：违规数为 0，不存在严格更少的翻转，返回空列表
    threading = [0, 1, 0, 1]
    tie_up = [[1, 0], [0, 1]]
    treadling = [0, 1, 0, 1]
    check(suggest_tie_up_flips(threading, tie_up, treadling, 2, False) == [],
          "empty list when no flip improves")


def test_repair_plans_basic():
    # 列 1 1 0 1 1 循环合并为长度 4 的经向浮长（max_float=2），共 1 条违规
    threading = [0]
    tie_up = [[1], [0]]
    treadling = [0, 0, 1, 0, 0]
    plans = search_repair_plans(threading, tie_up, treadling, 2, True, max_changes=2)
    # 1 步方案 tie_up[0][0]->0 直接清零；2 步方案仅 (0,0)+(1,0) 可行
    check(len(plans) == 2, "one 1-step and one 2-step plan")
    best = plans[0]
    check(best.changes == 1 and best.final_violations == 0,
          "best plan is the 1-step full repair")
    check(best.changed_points == 4, "4 interlacing points changed vs original")
    flip = best.flips[0]
    check(flip.treadle == 0 and flip.shaft == 0 and flip.new_value == 0
          and flip.violations_after == 0,
          "flip tie_up[0][0] to 0 with zero violations after")
    check(best.interlacing == [[0]] * 5, "final interlacing all 0")
    check(best.violations == [], "final violation list empty")
    check(tie_up == [[1], [0]], "original tie_up unchanged")
    # 2 步方案：同一单元不重复翻转，序列按字典序升序
    two = plans[1]
    check(two.changes == 2 and len(two.flips) == 2, "second plan takes 2 flips")
    check([(f.treadle, f.shaft) for f in two.flips]
          == sorted((f.treadle, f.shaft) for f in two.flips),
          "flip sequence in lexicographic order")


def test_repair_plans_intermediate_may_stay_worse():
    # 原矩阵两行全长纬浮，共 2 条违规（max_float=1）；任何单步翻转都无法
    # 严格减少（故 max_changes=1 返回空），但 2 步可降到 1，且中间状态
    # 违规数保持为 2（不下降）
    threading = [0, 1, 1]
    tie_up = [[0, 0], [0, 1]]
    treadling = [0, 1, 0]
    check(search_repair_plans(threading, tie_up, treadling, 1, False,
                              max_changes=1) == [],
          "no plan within 1 flip")
    plans = search_repair_plans(threading, tie_up, treadling, 1, False,
                                max_changes=2)
    check(len(plans) == 1, "exactly one 2-flip plan")
    p = plans[0]
    check(p.changes == 2 and p.final_violations == 1,
          "2 flips leave 1 violation")
    check(p.flips[0].violations_after == 2,
          "intermediate violation count is allowed to stay at 2")
    check(p.flips[1].violations_after == 1, "final violation count is 1")
    check(p.changed_points == 6, "all 6 interlacing points differ from original")
    check(tie_up == [[0, 0], [0, 1]], "original tie_up unchanged")


def test_repair_plans_sorting_and_dedup():
    # 3 根经纱各浮 3 纬 + 末纬纬浮 3 经，共 4 条违规
    threading = [0, 1, 1]
    tie_up = [[1, 1], [0, 0]]
    treadling = [0, 0, 0, 1]
    plans = search_repair_plans(threading, tie_up, treadling, 2, False,
                                max_changes=3)
    keys = [
        (p.final_violations, p.changes, p.changed_points,
         [(f.treadle, f.shaft) for f in p.flips])
        for p in plans
    ]
    check(keys == sorted(keys),
          "plans sorted by final violations, changes, changed points, sequence")
    for p in plans:
        seq = [(f.treadle, f.shaft) for f in p.flips]
        check(len(set(seq)) == len(seq), "no cell flipped twice in a plan")
        check(seq == sorted(seq), "flip sequence is the lexicographically minimal one")
        check(p.changes <= 3, "no plan exceeds max_changes")
        check(p.final_violations < 4, "every plan strictly improves")
    # 等价终态唯一：同一绑综终态（翻转单元集合）只出现一次
    end_states = [
        frozenset((f.treadle, f.shaft) for f in p.flips) for p in plans
    ]
    check(len(end_states) == len(set(end_states)),
          "equivalent end states are deduplicated")


def test_repair_plans_no_violations_empty():
    # 平纹无违规：无法比 0 更少，返回空列表
    threading = [0, 1, 0, 1]
    tie_up = [[1, 0], [0, 1]]
    treadling = [0, 1, 0, 1]
    check(search_repair_plans(threading, tie_up, treadling, 2, False,
                              max_changes=3) == [],
          "empty plan list when design already complies")


def test_validation():
    ok_cases = []
    # 维度不一致：绑综矩阵不是矩形
    try:
        validate_design([0], [[1, 0], [1]], [0], 2)
        ok_cases.append(False)
    except DesignError:
        ok_cases.append(True)
    # 穿综索引越界
    try:
        validate_design([0, 2], [[1, 0], [0, 1]], [0], 2)
        ok_cases.append(False)
    except DesignError:
        ok_cases.append(True)
    # 踏综索引越界
    try:
        validate_design([0], [[1], [0]], [2], 2)
        ok_cases.append(False)
    except DesignError:
        ok_cases.append(True)
    # 绑综取值非 0/1
    try:
        validate_design([0], [[2]], [0], 2)
        ok_cases.append(False)
    except DesignError:
        ok_cases.append(True)
    # 空输入
    try:
        validate_design([], [[1]], [0], 2)
        ok_cases.append(False)
    except DesignError:
        ok_cases.append(True)
    # 负索引
    try:
        validate_design([-1], [[1]], [0], 2)
        ok_cases.append(False)
    except DesignError:
        ok_cases.append(True)
    check(all(ok_cases), "invalid designs are rejected")


if __name__ == "__main__":
    test_interlacing()
    test_warp_and_weft_floats()
    test_cyclic_merge()
    test_cyclic_audit_end_to_end()
    test_cyclic_wrap_violation()
    test_tie_up_suggestions_basic()
    test_tie_up_suggestions_ordering()
    test_tie_up_suggestions_no_improvement()
    test_repair_plans_basic()
    test_repair_plans_intermediate_may_stay_worse()
    test_repair_plans_sorting_and_dedup()
    test_repair_plans_no_violations_empty()
    test_validation()
    print("all tests passed")
