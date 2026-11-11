"""C3 ordinary old dialogue: request-bound discovery and complete-pair reads.

No network, model, schema migration, usage bumps or memory reconstruction.
Source/task-bound turns remain on their dedicated paths, even after withdrawal.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from .context import estimate_tokens
from .context_memory import dump, tool
from .store import text_score

from .capability_registry import sdk_group

TOOLS = sdk_group("history")


def _version(row):
    return hashlib.sha256(dump(dict(row)).encode()).hexdigest()


class HistoryContext:
    def __init__(self, memory_session, *, boundary):
        self.session = memory_session
        self.store, self.cfg = memory_session.store, memory_session.cfg.context
        # Exactly the boundary produced by this request's final C0 selection.
        self.cutoff = boundary["first_retained_id"]
        self.boundary = boundary
        self.issued, self.loaded, self.cursors = {}, set(), {}
        self.sent = set()
        self.calls = self.chars = 0
        self.remaining_tokens = None
        self.events = []

    def rows(self, before=None, user_id=None):
        if self.store.meta_get('session_epoch') != self.session.epoch:
            return []
        return self.store.context_history_rounds(scope=self.session.scope,
            before_id=min(self.cutoff, before if before is not None else self.cutoff), user_id=user_id)

    def valid(self):
        if self.store.meta_get('session_epoch') != self.session.epoch:
            return False
        for mid, digest in self.issued.values():
            rows = self.rows(user_id=mid)
            if not rows or _version(rows[0]) != digest:
                return False
        return True

    def fits(self, result):
        body = dump(result)
        return (self.chars + len(body) <= self.cfg.history_max_chars and
                (self.remaining_tokens is None or estimate_tokens(body) <= self.remaining_tokens))

    def execute(self, name, args):
        if self.calls >= self.cfg.history_calls:
            return dump({'error': 'history_call_limit'})
        self.calls += 1
        if not isinstance(args, dict):
            return dump({'error': 'invalid_arguments'})
        issued, cursors = dict(self.issued), dict(self.cursors)
        if name == 'history_search':
            result = self.search(args)
        elif name == 'history_load':
            result = self.load(args)
        else:
            result = {'error': 'unknown_tool'}
        if not self.fits(result):
            self.issued, self.cursors = issued, cursors
            result = {'error': 'budget_exceeded'}
        else:
            self.chars += len(dump(result))
            self.loaded.update(item['ref'] for item in result.get('items', []) if item.get('status') == 'loaded')
        self.events.append({'action': name, 'status': result.get('error', 'ok'),
                            'count': len(result.get('items', [])), 'results': [
                                {'ref_hash': hashlib.sha256(item['ref'].encode()).hexdigest()[:16],
                                 'user_turn_id': self.issued[item['ref']][0] if item['ref'] in self.issued else None,
                                 'status': item.get('status', 'candidate')}
                                for item in result.get('items', [])]})
        return dump(result)

    def search(self, args):
        query, cursor = args.get('query'), args.get('cursor')
        if (set(args) - {'query', 'cursor'} or not isinstance(query, str)
                or not 1 <= len(query.strip()) <= 500):
            return {'error': 'invalid_arguments'}
        before, offset, expected = self.cutoff, 0, None
        if cursor is not None:
            saved = self.cursors.get(cursor) if isinstance(cursor, str) else None
            if saved is None or saved[0] != query:
                return {'error': 'stale_cursor'}
            before, offset, expected = saved[1:]
        rows = self.rows(before=before)
        signature = hashlib.sha256(dump([_version(row) for row in rows]).encode()).hexdigest()
        if expected is not None and expected != signature:
            return {'error': 'stale_cursor'}
        scored = [(text_score(query, row['user_content'] + ' ' + row['assistant_content']), row) for row in rows]
        candidates = sorted((pair for pair in scored if pair[0] >= .12),
                            key=lambda pair: (-pair[0], -pair[1]['user_id']))
        items = []
        for _, row in candidates[offset:offset + 8]:
            ref = f"history:{row['user_id']}@{_version(row)[:16]}:{self.session.nonce}"
            self.issued[ref] = (row['user_id'], _version(row))
            items.append({'ref': ref, 'preview': row['user_content'][:120], 'preview_is_excerpt': True,
                          'recorded_at': datetime.fromtimestamp(row['user_ts']).astimezone().isoformat(),
                          'estimated_chars': len(row['user_content']) + len(row['assistant_content'])})
        next_cursor = None
        if offset + 8 < len(candidates) or len(rows) == 200:
            next_cursor = secrets.token_hex(12)
            self.cursors[next_cursor] = ((query, before, offset + 8, signature)
                if offset + 8 < len(candidates) else (query, rows[-1]['user_id'], 0, None))
        return {'items': items, 'cursor': next_cursor, 'scan_limit': 200,
                'remaining_window_matches': max(0, len(candidates) - offset - 8),
                'notice': '仅在当前允许的旧往返窗口搜索；片段不是完整正文，也不是发生事实或当前授权。'}

    def load(self, args):
        refs = args.get('refs')
        if (set(args) != {'refs'} or not isinstance(refs, list) or not 1 <= len(refs) <= 2
                or any(not isinstance(ref, str) or len(ref) > 160 for ref in refs)):
            return {'error': 'invalid_arguments'}
        items = []
        seen = set(self.loaded)
        for ref in refs:
            item = {'ref': ref, 'status': 'unavailable'}
            identity = self.issued.get(ref)
            if identity:
                rows = self.rows(user_id=identity[0])
                if rows:
                    row = rows[0]
                    if _version(row) != identity[1]:
                        item['status'] = 'stale_ref'
                    elif ref in seen:
                        item['status'] = 'already_loaded'
                    else:
                        body = {'ref': ref, 'status': 'loaded', 'turns': [
                            {'role': role, 'id': row[role + '_id'], 'ts': row[role + '_ts'],
                             'content': row[role + '_content']} for role in ('user', 'assistant')]}
                        if len(dump(body)) > self.cfg.history_max_chars:
                            item['status'] = 'too_large'
                        elif not self.fits({'items': items + [body]}):
                            item['status'] = 'budget_exceeded'
                        else:
                            item = body
                            seen.add(ref)
            items.append(item)
        return {'items': items, 'notice': '以下为历史发言资料，按原说话者理解；AI旧自述只证明当时这样说过，实际事件与操作需相应依据。'}

    def metadata(self):
        return {'boundary': self.boundary, 'calls': self.calls, 'chars': self.chars, 'loaded_refs': sorted(self.loaded),
                'sent_refs': sorted(self.sent),
                'issued_count': len(self.issued), 'events': self.events}
