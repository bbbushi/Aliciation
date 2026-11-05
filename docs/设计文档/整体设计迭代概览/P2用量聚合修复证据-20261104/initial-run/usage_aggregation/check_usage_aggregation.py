"""Supplemental P2 accounting checks, only in a copied and audit-guarded workspace."""
import sys
from pathlib import Path
RUN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUN_ROOT))
import check_support as support
import time
WORKSPACE = support.isolated_workspace('usage_aggregation', 'workspace_' + str(time.time_ns()))
GUARD = support.protect(WORKSPACE)
import json
import threading
import uuid
from types import SimpleNamespace as NS
from unittest.mock import patch
from alice import llm, loop, metrics, observation, channels, persona_context
from alice.config import Config
from alice.store import Store

ROOT = WORKSPACE.parent / ('fixtures_' + str(time.time_ns()))
ROOT.mkdir()
RESULTS = support.Results('usage_aggregation')
FACTS = {}
FINAL = 'Synthetic final reply.'


class ProviderError(Exception):
    status_code = 429


class FakeProvider:
    def __init__(self, sequence):
        self.sequence, self.calls = list(sequence), []
        self.chat = NS(completions=self)
    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.sequence:
            raise AssertionError('Unexpected extra request')
        item = self.sequence.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeStream:
    def __init__(self, items):
        self.items, self.closed = items, False
    def __iter__(self):
        for item in self.items:
            if isinstance(item, BaseException):
                raise item
            yield item
    def close(self):
        self.closed = True


def receipt(prompt=20, completion=8, *, unknown=False, text=FINAL):
    return NS(model='synthetic-receipt-model', usage=None if unknown else NS(prompt_tokens=prompt, completion_tokens=completion),
              choices=[NS(message=NS(content=text, tool_calls=None, role='assistant'), finish_reason='stop')])


def chunk(prompt=None, completion=None, *, has_usage=True, text=''):
    return NS(model='synthetic-stream-model', usage=NS(prompt_tokens=prompt, completion_tokens=completion) if has_usage else None,
              choices=[NS(delta=NS(content=text, tool_calls=None))])


def fixture(name):
    store = Store(ROOT / (name + '.sqlite3'))
    store.conn.executescript(channels.SCHEMA)
    store.meta_set('session_epoch', 'fixture_epoch')
    cfg = Config()
    cfg.llm.chat_model, cfg.llm.cheap_model = 'synthetic-main-model', 'synthetic-review-model'
    cfg.llm.base_url = 'https://invalid.example'
    cfg.context.transform_review_enabled = False
    return store, cfg


def facts(name, data):
    FACTS[name] = data
    (ROOT / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def run_loop(name, sequence, *, stream=False, review=False, budget=None, expected_error=None):
    store, cfg = fixture(name)
    cfg.context.transform_review_enabled = review
    if budget is not None:
        cfg.context.total_budget_tokens = budget
    provider = FakeProvider(sequence)
    request = 'p2_' + uuid.uuid4().hex
    now = time.time()
    store.conn.execute('INSERT INTO channel_requests(request_id,session_epoch,channel_id,source_key,source_json,payload_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
        (request, 'fixture_epoch', 'gui', request, '{}', 'synthetic', 'accepted', now, now))
    store.conn.commit()
    snapshot = persona_context.parse_snapshot('## 底色 · 气质与风格\n- 用简明直接的话回应。\n')
    usage, delays, deltas, reply, failure = {}, [], [], None, None
    with patch.object(persona_context, 'load_persona_snapshot', return_value=snapshot), \
         patch.object(llm, 'time', NS(monotonic=time.monotonic, sleep=delays.append)):
        try:
            reply = loop.chat_turn(store, provider, cfg, '请改写：“此为隔离诊断材料，不是现实承诺。”' if review else '请回应这条隔离诊断消息。',
                request_id=request, usage_out=usage, on_delta=deltas.append if stream else None, lock=threading.RLock())
        except BaseException as exc:
            if expected_error is None or not isinstance(exc, expected_error):
                raise
            failure = type(exc).__name__
    if expected_error is not None:
        assert failure is not None, 'Expected failure/cancellation not raised'
    rows = observation.llm_calls(store, request_id=request)['items']
    bases = observation.context_basis(store)['items']
    basis = bases[0]['basis'] if bases else None
    turns = [dict(row) for row in store.conn.execute('SELECT role,content FROM turns ORDER BY id')]
    if basis is not None:
        assert basis['request_id'] == request and basis['usage'] == usage
    data = {'aggregate': dict(usage), 'context_basis_usage': basis['usage'] if basis else None,
            'basis': basis, 'calls': len(provider.calls), 'events': rows,
            'reply': reply, 'exception': failure, 'turn_count': len(turns), 'backoff_replaced_seconds': delays,
            'stubs': ['synthetic persona loader', 'fake provider', 'backoff sleep'],
            'real_path': 'copied loop.chat_turn -> steps.drive -> assembly -> provider interface -> SQLite turns/context_basis/observations'}
    store.close()
    return data


def assert_usage(data, expected, calls):
    assert data['aggregate'] == expected, (data['aggregate'], expected)
    assert data['calls'] == calls, data['calls']
    assert len(data['events']) == calls, data['events']
    if data['exception'] is None:
        assert data['context_basis_usage'] == expected
        assert data['turn_count'] == 2
    return data


def clean_nonstream():
    data = run_loop('clean_nonstream', [receipt()])
    assert data['reply'] == FINAL
    return facts('clean_nonstream', assert_usage(data, {'prompt': 20, 'completion': 8}, 1))


def clean_stream_latest_receipt_only():
    data = run_loop('clean_stream', [FakeStream([chunk(10, 4), chunk(20, 8, text=FINAL)])], stream=True)
    assert data['reply'] == FINAL
    assert data['events'][0]['usage_state'] == 'reported'
    return facts('clean_stream_latest_receipt_only', assert_usage(data, {'prompt': 20, 'completion': 8}, 1))


def partial_stream_exact_sum():
    data = run_loop('partial_stream', [FakeStream([chunk(10, 4), ConnectionError('synthetic')]), receipt()], stream=True)
    stream_event = next(e for e in data['events'] if e['purpose'] == 'main_stream')
    assert stream_event['usage'] == {'prompt': 10, 'completion': 4}
    assert stream_event['usage_state'] == 'partial_receipt' and stream_event['status'] == 'error'
    return facts('partial_stream_exact_sum', assert_usage(data, {'prompt': 30, 'completion': 12, 'incomplete': True}, 2))


def retry_unknown_exact_sum():
    data = run_loop('retry_unknown', [ProviderError('synthetic'), TimeoutError('synthetic'), receipt()])
    assert sorted(e['attempt'] for e in data['events']) == [1, 2, 3]
    assert len({e['call_id'] for e in data['events']}) == 1
    assert sum(e['usage'] == 'unknown' for e in data['events']) == 2
    return facts('retry_unknown_exact_sum', assert_usage(data, {'prompt': 20, 'completion': 8, 'incomplete': True}, 3))


def partial_stream_then_retry_fallback():
    data = run_loop('stream_retry', [FakeStream([chunk(10, 4), ConnectionError('synthetic')]), ProviderError('synthetic'), receipt()], stream=True)
    assert sum(e['purpose'] == 'main_fallback' for e in data['events']) == 2
    return facts('partial_stream_then_retry_fallback', assert_usage(data, {'prompt': 30, 'completion': 12, 'incomplete': True}, 3))


def partial_nonstream():
    data = run_loop('partial_nonstream', [receipt(9, None)])
    assert data['events'][0]['usage_state'] == 'partial_receipt'
    return facts('partial_nonstream', assert_usage(data, {'prompt': 9, 'incomplete': True}, 1))


def unknown_nonstream():
    data = run_loop('unknown_nonstream', [receipt(unknown=True)])
    return facts('unknown_nonstream', assert_usage(data, {'incomplete': True}, 1))


def successful_stream_missing_final_usage():
    data = run_loop('missing_final_usage', [FakeStream([chunk(10, 4), chunk(has_usage=False, text=FINAL)])], stream=True)
    assert data['events'][0]['status'] == 'ok' and data['events'][0]['usage_state'] == 'partial_receipt'
    return facts('successful_stream_missing_final_usage', assert_usage(data, {'prompt': 10, 'completion': 4, 'incomplete': True}, 1))


def whole_failure_keeps_unknown():
    data = run_loop('whole_failure', [ProviderError('synthetic') for _ in range(3)], expected_error=RuntimeError)
    assert data['basis'] is None and data['turn_count'] == 1
    assert all(e['status'] == 'error' for e in data['events'])
    return facts('whole_failure_keeps_unknown', assert_usage(data, {'incomplete': True}, 3))


def cancellation_keeps_unknown():
    data = run_loop('cancellation', [KeyboardInterrupt('synthetic cancellation')], expected_error=KeyboardInterrupt)
    assert data['basis'] is None and data['events'][0]['status'] == 'cancelled'
    return facts('cancellation_keeps_unknown', assert_usage(data, {'incomplete': True}, 1))


def review_retry_false_failure():
    data = run_loop('review_failure', [receipt(), ProviderError('synthetic')], review=True)
    assert data['basis']['transform_review']['calls'] == 1
    assert data['basis']['transform_review']['usage'] == {'incomplete': True}
    assert data['basis']['transform_review']['status'] == 'original_preserved'
    assert data['basis']['transform_review']['error'] == 'RuntimeError'
    assert sum(e['purpose'] == 'rewrite_review' and e['attempt'] == 1 for e in data['events']) == 1
    return facts('review_retry_false_failure', assert_usage(data, {'prompt': 20, 'completion': 8, 'incomplete': True}, 2))


def budget_rejection_no_attempt():
    data = run_loop('budget_reject', [], budget=1, expected_error=ValueError)
    assert data['basis'] is None
    return facts('budget_rejection_no_attempt', assert_usage(data, {}, 0))


def callback_failure_does_not_retry_or_mask():
    store, cfg = fixture('callback_failure')
    seen = []
    def broken_callback(response, *, incomplete=False):
        seen.append({'has_response': response is not None, 'incomplete': incomplete})
        raise ValueError('synthetic callback failure')
    provider = FakeProvider([ProviderError('synthetic'), receipt()])
    with patch.object(llm, 'time', NS(monotonic=time.monotonic, sleep=lambda seconds: None)):
        response = llm.chat(provider, cfg, [], on_attempt_usage=broken_callback,
            observation={'store': store, 'purpose': 'callback_failure', 'request_id': 'callback_failure'})
    assert response.choices[0].message.content == FINAL and len(provider.calls) == 2 and len(seen) == 2
    cancelled = FakeProvider([KeyboardInterrupt('synthetic')])
    try:
        llm.chat(cancelled, cfg, [], on_attempt_usage=broken_callback,
            observation={'store': store, 'purpose': 'callback_cancel', 'request_id': 'callback_cancel'})
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError('Callback failure swallowed cancellation')
    assert len(cancelled.calls) == 1 and len(seen) == 3
    rows = observation.llm_calls(store)['items']
    assert len(rows) == 3
    store.close()
    return facts('callback_failure_does_not_retry_or_mask', {'calls': 3, 'callback_calls': seen, 'events': rows})


def callback_and_sqlite_observation_failure():
    store, cfg = fixture('callback_sqlite_failure')
    store.conn.executescript("CREATE TRIGGER p2_block_receipts BEFORE INSERT ON turn_metrics WHEN NEW.module LIKE 'llm_call:%' BEGIN SELECT RAISE(FAIL,'synthetic_receipt_failure'); END;")
    callbacks = []
    def broken_callback(response, *, incomplete=False):
        callbacks.append(incomplete)
        raise ValueError('synthetic callback failure')
    provider = FakeProvider([receipt()])
    response = llm.chat(provider, cfg, [], on_attempt_usage=broken_callback,
        observation={'store': store, 'purpose': 'sqlite_failure', 'request_id': 'sqlite_failure'})
    for _ in range(2):
        metrics.flush_calls(store)
    assert response.choices[0].message.content == FINAL and len(provider.calls) == 1 and callbacks == [False]
    assert len(store._llm_events) == 1
    assert store.conn.execute("SELECT COUNT(*) FROM turn_metrics WHERE module='llm_call:sqlite_failure'").fetchone()[0] == 0
    store.conn.execute('DROP TRIGGER p2_block_receipts')
    store.conn.commit()
    metrics.flush_calls(store)
    assert not store._llm_events and len(provider.calls) == 1
    rows = observation.llm_calls(store)['items']
    assert len(rows) == 1
    store.close()
    return facts('callback_and_sqlite_observation_failure', {'provider_calls': 1, 'callback_calls': callbacks,
        'failed_flushes': 2, 'events': rows, 'injection': 'SQLite receipt INSERT trigger, not replacement of metrics API'})


for name, callback in [
    ('clean_nonstream', clean_nonstream),
    ('clean_stream_latest_receipt_only', clean_stream_latest_receipt_only),
    ('partial_stream_exact_sum', partial_stream_exact_sum),
    ('retry_unknown_exact_sum', retry_unknown_exact_sum),
    ('partial_stream_then_retry_fallback', partial_stream_then_retry_fallback),
    ('partial_nonstream', partial_nonstream),
    ('unknown_nonstream', unknown_nonstream),
    ('successful_stream_missing_final_usage', successful_stream_missing_final_usage),
    ('whole_failure_keeps_unknown', whole_failure_keeps_unknown),
    ('cancellation_keeps_unknown', cancellation_keeps_unknown),
    ('review_retry_false_failure', review_retry_false_failure),
    ('budget_rejection_no_attempt', budget_rejection_no_attempt),
    ('callback_failure_does_not_retry_or_mask', callback_failure_does_not_retry_or_mask),
    ('callback_and_sqlite_observation_failure', callback_and_sqlite_observation_failure),
]:
    RESULTS.check(name, callback)

result = RESULTS.save(workspace=str(WORKSPACE), fixtures=str(ROOT), facts=FACTS, python=sys.version,
    guard_attempts=GUARD, real_provider_calls=0, real_subprocesses=0, real_service_starts=0,
    limitations=['Copied source only; synthetic responses/errors and persona loader',
                 'No real provider billing, Windows CLI, GUI, service, or user data'])
print(json.dumps({'passed': result['passed'], 'failed': result['failed'], 'guard_attempts': GUARD}, ensure_ascii=False))
raise SystemExit(bool(result['failed']))
