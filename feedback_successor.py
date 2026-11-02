"""Read-only, source-supported feedback boundary -> immediate successor lookup.

The rule is structural: a single-state anchor AND positive BOOL conditions must
be necessary in a complete source guard. It never assumes a signal is a physical
arrival because of its name, and never invents a target from integer ordering.
The pre-write predicate and the post-write state are NOT Boolean equivalents.
They are reported as conditional acquisition-boundary alternatives only.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import argparse
import json
from typing import Any
from boundary_logic import (Expr, parse, combine, negate, substitute, compatible,
                            BoundarySyntaxError, TRUE)

PROVEN = {'plcopen_wired_guard', 'st_structured_guard', 'smc2_grid_guard',
          'smc2_embedded_st_guard', 'omron_native_wired_guard', 'omron_native_st_guard'}
LIMIT = ('仅支持正常运行、同一循环的条件迁移替代；不证明物理到位、采样时间戳相同，'
         '也不排除复位、其他写入或同一扫描内再次跳步。附加门控未成立时，不得按反馈时刻结束。')

@dataclass
class Resolution:
    applicable: bool = False
    rows: list[dict[str, Any]] = field(default_factory=list)
    paths: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    status: str = '非本规则适用边界'
    reason: str = ''
    original_end: str = ''


def shape(start: str, end: str):
    """Only S=n -> S=n AND F[AND G...]. Never drop cross-state/OR conditions."""
    try:
        a, b = parse(start), parse(end)
    except (BoundarySyntaxError, TypeError):
        return None
    if a.kind != 'eq' or b.kind != 'and':
        return None
    anchors = [x for x in b.args if x.kind == 'eq']
    feedback = [x for x in b.args if x.kind == 'signal']
    if (len(anchors) != 1 or anchors[0].key() != a.key() or not feedback
            or len(b.args) != 1 + len(feedback)):
        return None
    if any(x.args[0].startswith('__') for x in feedback):
        return None
    return a, b, feedback


def _reduce_true(x: Expr, keys: set[tuple]) -> Expr:
    if x.key() in keys:
        return TRUE
    if x.kind in ('and', 'or'):
        return combine(x.kind, *(_reduce_true(y, keys) for y in x.args))
    if x.kind == 'not':
        return negate(_reduce_true(x.args[0], keys))
    return x


def implies_at(guard: Expr, predicate: Expr, variable: str, value: int) -> bool:
    # compatible returns "possible" on its search-budget exhaustion, so this
    # negation fails closed rather than treating an incomplete search as proof.
    return not compatible(combine('and', guard, negate(predicate)), variable, value)


class FeedbackResolver:
    def __init__(self, graph: dict):
        if graph.get('schema') != 'plc.state_transitions.v1':
            raise ValueError('需要源码迁移图；候选表、GT或手写答案不能作为迁移证据')
        self.edges = defaultdict(list)
        self.blocks = defaultdict(list)
        for e in graph.get('transitions', []):
            self.edges[str(e.get('variable', '')).casefold()].append(e)
        for b in graph.get('blockers', []):
            self.blocks[str(b.get('variable', '')).casefold()].append(b)

    def resolve(self, row: dict) -> Resolution:
        parts = shape(str(row.get('开始标签', '') or ''), str(row.get('结束标签', '') or ''))
        if parts is None:
            return Resolution()
        a, endpoint, feedback = parts
        variable, value = a.args
        result = Resolution(applicable=True, original_end=endpoint.text(),
                            status='反馈后继待核验')
        es, bs = self.edges[variable.casefold()], self.blocks[variable.casefold()]
        scopes = {x.get('binding_scope', '') for x in es + bs}
        if len(scopes) > 1:
            explicit = row.get('binding_scope')
            context = str(row.get('证据POU', '') or row.get('scope', ''))
            matches = {explicit} if explicit is not None and explicit in scopes else {
                s for s in scopes if s and any(c == s or c.startswith(s + '::') for c in context.split(';'))}
            if len(matches) != 1:
                result.reason = '同名状态变量作用域不唯一，不跨POU寻找后继'
                result.issues = [result.reason]
                return result
            chosen = next(iter(matches))
            es = [e for e in es if e.get('binding_scope', '') == chosen]
            bs = [b for b in bs if b.get('binding_scope', '') == chosen]
        relevant = [e for e in es if e.get('from') == value]
        branches = defaultdict(list)
        for e in relevant:
            if e.get('guard_complete') is not True or e.get('evidence') not in PROVEN:
                result.issues.append(f'{e.get("id", "")}: 未完整证明控制条件')
                continue
            try:
                g = parse(e.get('guard', ''))
            except BoundarySyntaxError:
                result.issues.append(f'{e.get("id", "")}: 控制条件不支持')
                continue
            if not compatible(g, variable, value):
                result.issues.append(f'{e.get("id", "")}: 条件矛盾，不作为可执行出口')
                continue
            # Need the real source predicate S=n; an arbitrary transition with a
            # from-field but no state guard is not allowed to masquerade as proof.
            if not any(c.key() == a.key() for c in _atoms(g)):
                result.issues.append(f'{e.get("id", "")}: 使能式缺少同一状态锚点')
                continue
            if not implies_at(g, endpoint, variable, value):
                result.issues.append(f'{e.get("id", "")}: 反馈并非所有分支的必要条件/存在旁路')
                continue
            target = e.get('to')
            if not isinstance(target, int) or isinstance(target, bool):
                result.issues.append(f'{e.get("id", "")}: 目标不是可确定整数状态')
                continue
            if target == value:
                result.issues.append(f'{e.get("id", "")}: 自环，不是下一状态')
                continue
            extra = _reduce_true(substitute(g, variable, value), {f.key() for f in feedback})
            branches[target].append({'edge': dict(e), 'extra_guard': extra.text(),
                'feedback_predicate': combine('and', *feedback).text(),
                'guard_implies_feedback_at_source': True,
                'feedback_sufficient_at_source': implies_at(endpoint, g, variable, value),
                'physical_feedback_role_proved': False,
                'strict_boolean_equivalence': False, 'acquisition_time_equivalence': False})
        for b in bs:
            if b.get('from') is None or b.get('from') == value:
                result.issues.append(f'其他写入/解析阻断: {b.get("reason", "unknown writer")}')
        if not branches:
            result.reason = ('未找到由该反馈条件必需地控制的完整直接后继；不按数值排序、'
                             '不跨过中间状态，不从候选文字或GT补造目标。')
        else:
            result.status = '反馈直接后继-条件替代'
            result.reason = ('按源码直接赋值目标投影；保留完整门控和反馈，不把“到位”名称当作证明。' + LIMIT)
            for target, paths in sorted(branches.items()):
                result.rows.append({'开始标签': a.text(), '结束标签': Expr('eq', (variable, target)).text(),
                    '证据等级': '源码反馈驱动直接后继-条件替代', '全局退出等价': '否/未证明',
                    '计时使用方式': '本次状态进入后锁存；满足完整门控的直接后继结束；异常/复位另行排除',
                    '迁移证据ID': ';'.join(sorted({p['edge']['id'] for p in paths})),
                    '附加门控': ' OR '.join('(' + p['extra_guard'] + ')' for p in paths),
                    '反馈原终点': endpoint.text()})
                result.paths.append({'start': value, 'end': target, 'edges': [p['edge'] for p in paths],
                    'feedback_relations': paths, 'original_end': endpoint.text(), 'timing_limit': LIMIT})
            result.issues.append('反馈事件在赋值前、后继状态在赋值后；不是同时成立的两个布尔条件。')
            if any(p['extra_guard'] != 'TRUE' for pp in branches.values() for p in pp):
                result.issues.append('附加门控未证明在整个动作期间恒成立；可能包含到位后的等待。')
        result.issues = list(dict.fromkeys(result.issues))
        return result


def _atoms(x: Expr):
    if x.kind in ('eq', 'ne', 'signal'):
        return [x]
    # Do not treat negated predicates as positive state-anchor proof.
    if x.kind in ('and', 'or'):
        return [a for y in x.args for a in _atoms(y)]
    return []


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='只读查询反馈边界的直接逻辑后继；不添加预测')
    p.add_argument('--graph', required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p.add_argument('--scope', default='')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    from dataclasses import asdict
    from boundary_normalizer import load_graph
    r = FeedbackResolver(load_graph(a.graph)).resolve({'开始标签': a.start, '结束标签': a.end, '证据POU': a.scope})
    out = Path(a.out)
    if out.resolve() == Path(a.graph).resolve():
        p.error('输出不得覆盖源码图')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(r), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': r.status, 'pairs': r.rows, 'output': str(out)}, ensure_ascii=False, indent=2))
