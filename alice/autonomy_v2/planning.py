"""Short declared objectives and outcome accounting, never a second model."""
from __future__ import annotations

import json
import time


class ActivityPlanning:
    def __init__(self, store):
        self.store = store

    def get(self, run_id):
        row = self.store.conn.execute("SELECT * FROM v2_activity_plans WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        from ..thought_workspace import valid_basis
        if not valid_basis(self.store, json.loads(row['basis'])):
            return {'run_id': run_id, 'detail_state': 'unavailable'}
        result = {k: row[k] for k in ('run_id', 'question_id', 'choice', 'object', 'value',
                                     'why_now', 'stop_condition', 'next_action', 'created_at')}
        result['result'] = json.loads(row['result']) if row['result'] else None
        result['detail_state'] = 'loaded'
        return result

    def recent(self, epoch, *, limit=5):
        ids = self.store.conn.execute(
            "SELECT run_id FROM v2_activity_plans WHERE epoch=? ORDER BY created_at DESC LIMIT ?",
            (epoch, limit)).fetchall()
        items = []
        for row in ids:
            plan = self.get(row[0])
            if not plan or plan['detail_state'] != 'loaded':
                continue
            result = plan['result']
            items.append({k: plan[k] for k in ('run_id', 'question_id', 'choice', 'object', 'stop_condition')}
                         | {'value': plan['value'][:120], 'result': {
                             'goal_met': result['goal_met'], 'worth_continuing': result['worth_continuing'],
                             'change_kind': result['change_kind'], 'change': result['change'][:160],
                             'reason': result['reason'][:120]} if result else None})
        return items

    def submit(self, context, args, *, question_id, basis):
        from .adapters import HostAdapter
        from ..thought_workspace import dump, text, valid_basis
        HostAdapter(self.store).validate_background_context(context)
        old = self.get(context.run_id)
        if old is not None:
            return old  # Fixed objective for this window, including resumed runs.
        target = self.store.conn.execute("SELECT question_id FROM exploration_runs WHERE id=?", (context.run_id,)).fetchone()[0]
        if target is not None and target != question_id:
            raise ValueError('选题须关联本窗口原问题；旁支先收纳，另行评估')
        choice = args.get('choice')
        if choice not in {'explore', 'defer', 'rest'}:
            raise ValueError('选题决定须为explore/defer/rest')
        values = [text(args.get(key, ''), 240) for key in ('object', 'value', 'why_now', 'stop_condition')]
        next_action = text(args.get('next_action', ''), 240, empty=choice != 'explore')
        if not valid_basis(self.store, basis):
            raise ValueError('选题依据已变化')
        self.store.conn.execute(
            "INSERT INTO v2_activity_plans(run_id,epoch,question_id,choice,object,value,why_now,"
            "stop_condition,next_action,basis,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (context.run_id, context.scope.session_epoch, question_id, choice, *values, next_action,
             dump(basis), time.time()))
        return self.get(context.run_id)

    def tool_error(self, run_id, name, args, browse=None):
        if name in {'activity_plan', 'activity_finish'}:
            return None
        plan = self.get(run_id)
        if name == 'activity_browse':
            if browse is not None and plan is None:
                return None
            return {'error': 'activity_browse_unavailable', 'reason': '只在自由兴趣窗定题前浏览。'}
        if plan is None and browse is not None:
            if name == 'thought_search':
                return {'error': 'activity_browse_required', 'reason': '定题前从activity_browse选择要浏览的库。'}
            if name in {'memory_load', 'thought_load'}:
                if browse['peeked'] or name == 'memory_load' and len(args['refs']) != 1:
                    return {'error': 'activity_peek_limit', 'reason': '找题阶段只展开一项；先定题再继续读。'}
                browse['peeked'] = True
                return None
        if name in {'thought_search', 'thought_load'}:
            return None
        if plan is None or plan.get('detail_state') != 'loaded' or plan['choice'] != 'explore':
            return {'error': 'activity_plan_required',
                    'reason': '先用activity_plan说明对象、价值、为何现在和停止条件；defer/rest时直接收尾。'}
        return None

    def finish(self, context, args, *, question_id, basis):
        from ..thought_workspace import dump, text, merge_basis
        plan = self.get(context.run_id)
        if not plan or plan.get('detail_state') != 'loaded':
            raise ValueError('先提交本窗口activity_plan，再对照目标收尾')
        if plan['question_id'] is not None and plan['question_id'] != question_id:
            raise ValueError('结束回执须对应选题问题，保持同一目标')
        result = args.get('goal_result')
        if not isinstance(result, dict):
            raise ValueError('结束须附goal_result，对照目标说明变化、是否达到目标及继续价值')
        for key in ('goal_met', 'worth_continuing'):
            if type(result.get(key)) is not bool:
                raise ValueError('goal_result的goal_met/worth_continuing须为布尔值')
        if result.get('change_kind') not in {'understanding', 'restatement', 'none'}:
            raise ValueError('change_kind须为understanding/restatement/none')
        result = {**result, 'change': text(result.get('change', ''), 400),
                  'reason': text(result.get('reason', ''), 240)}
        if question_id is not None:
            question = self.store.conn.execute('SELECT version FROM thought_questions WHERE id=? AND epoch=?',
                                               (question_id, context.scope.session_epoch)).fetchone()
            if question is None:
                raise ValueError('选题问题已不在当前会话')
            result['question_version'] = question[0]
        if args['decision'] == 'continue' and (not result['worth_continuing'] or result['goal_met']):
            raise ValueError('目标已达或不值得继续时请选择done/rest/wait；旁支另行评估')
        if args['decision'] == 'continue' and not args.get('next_step', '').strip():
            raise ValueError('继续时须说明具体下一步')
        if plan['choice'] != 'explore' and (result['worth_continuing'] or args['progress_kind'] != 'none'
                                           or args['communication']['decision'] != 'none'):
            raise ValueError('本窗口选择搁置/休息，按无研究进展收尾，不准备邀请')
        old = self.store.conn.execute('SELECT basis FROM v2_activity_plans WHERE run_id=?', (context.run_id,)).fetchone()[0]
        self.store.conn.execute('UPDATE v2_activity_plans SET question_id=?,result=?,basis=? WHERE run_id=?',
                                (question_id, dump(result), dump(merge_basis(json.loads(old), basis)), context.run_id))
        return result

    def parked(self, question):
        """Value waiting is separate from task/source state and never resolves it.

        A real explicit update or new objective can reopen assessment; an
        unchanged row is not automatically rescheduled, even in test mode.
        """
        row = self.store.conn.execute(
            'SELECT run_id FROM v2_activity_plans WHERE epoch=? AND question_id=? AND result IS NOT NULL '
            'ORDER BY created_at DESC LIMIT 1', (question['epoch'], question['id'])).fetchone()
        plan = self.get(row[0]) if row else None
        result = plan.get('result') if plan else None
        return bool(result and (not result['worth_continuing'] or result['goal_met'])
                    and result.get('question_version') == question['version'])

    def free_waiting(self, epoch, mode, stamp):
        """Do not pay again to invent a purpose after declaring no useful work."""
        row = self.store.conn.execute(
            'SELECT p.run_id,a.input_stamp,a.selection FROM v2_activity_plans p '
            'JOIN autonomous_activity_runs a ON a.run_id=p.run_id '
            'WHERE p.epoch=? AND a.activity=? AND p.question_id IS NULL '
            'ORDER BY p.created_at DESC LIMIT 1', (epoch, mode)).fetchone()
        if row is None or row['input_stamp'] != stamp:
            return False
        if mode == 'interest' and not json.loads(row['selection']).get('free_interest_browse'):
            return False
        plan = self.get(row['run_id'])
        return bool(plan and plan.get('choice') in {'defer', 'rest'} and plan.get('result')
                    and not plan['result']['worth_continuing'])
