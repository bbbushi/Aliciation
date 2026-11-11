"""C2: bounded, channel-local, in-memory working sets. All calls hold Store lock.

Only request-issued exact refs are accepted. Release affects future assembly,
never removes this request's exposure ledger or any provider tool messages.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import hashlib

from .context import estimate_tokens
from .context_memory import dump, tool, version, prior_notice
from .memory_projection import conversation_content

from .capability_registry import sdk_group

TOOLS = sdk_group("workspace")


class WorkingSet:
    """Service owner; partitions never share bodies, refs, TTL or call budgets."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.epoch = None
        self.scopes = {}

    def for_request(self, session):
        if session.scope not in ('gui', 'qq-owner'):
            return None
        if self.epoch != session.epoch:
            self.scopes.clear()
            self.epoch = session.epoch
        if session.scope not in self.scopes:
            self.scopes[session.scope] = _ScopeSet(self.cfg, session.epoch, session.scope)
        return self.scopes[session.scope]


class _ScopeSet:
    def __init__(self, cfg, epoch, scope):
        self.cfg, self.epoch, self.scope = cfg, epoch, scope
        self.items = {}
        self.events = deque(maxlen=40)
        self.calls = 0
        self.sequence = 0
        self.request_nonce = None

    def _bound(self, session):
        return (session.epoch == self.epoch and session.scope == self.scope
                and session.nonce == self.request_nonce
                and session.store.meta_get('session_epoch') == self.epoch)

    def begin_turn(self, session):
        """Called once by MemoryContext.initial; issue fresh refs only for sent bodies."""
        self.calls = 0
        self.request_nonce = session.nonce
        rows = {row['id']: row for row in session.rows()}
        for mid, item in list(self.items.items()):
            if item['turns_left'] <= 0 or mid not in rows or version(rows[mid]) != item['version']:
                self.events.append({'action': 'auto_release', 'memory_id': mid,
                                    'reason': 'ttl' if item['turns_left'] <= 0 else 'unavailable_or_stale'})
                del self.items[mid]
        cap = self.cfg.context
        order = sorted(self.items, key=lambda mid: self.items[mid]['order'])
        while (len(self.items) > cap.working_set_max_items or
               sum(item['cost'] for item in self.items.values()) > cap.working_set_max_chars):
            mid = order.pop(0)
            del self.items[mid]
            self.events.append({'action': 'auto_release', 'memory_id': mid, 'reason': 'capacity'})
        entries = []
        for mid in order:
            item, row = self.items[mid], rows[mid]
            item['turns_left'] -= 1  # TTL counts this channel's requests, never tool subcalls.
            ref = f"memory:{mid}@{version(row)[:16]}:{session.nonce}"
            body = {'ref': ref, 'kind': row['kind'], 'title': row['title'], 'content': conversation_content(row),
                    'recorded_at': datetime.fromtimestamp(row['created_at']).astimezone().isoformat(),
                    'turns_left': item['turns_left'], **prior_notice(row)}
            cost = len(dump(body)) + 1
            if session.expanded_chars + cost > cap.memory_expanded_chars:
                self.events.append({'action': 'not_injected', 'memory_id': mid, 'reason': 'body_budget'})
                continue
            session.ref(row)
            session.loaded[ref] = (mid, version(row))
            session.expanded_chars += cost
            entries.append(body)
        self.events.append({'action': 'begin_turn', 'injected': len(entries)})
        return entries

    def execute(self, name, args, session):
        if not self._bound(session):
            return dump({'error': 'workspace_unavailable'})
        if self.calls >= self.cfg.context.working_set_calls:
            return dump({'error': 'workspace_call_limit'})
        self.calls += 1
        maximum = 4 if name == 'context_keep' else 8
        if (name not in ('context_keep', 'context_release') or not isinstance(args, dict)
                or set(args) != {'refs'} or not isinstance(args['refs'], list)
                or not 1 <= len(args['refs']) <= maximum
                or any(not isinstance(ref, str) or len(ref) > 160 for ref in args['refs'])):
            return dump({'error': 'invalid_arguments'})
        # The receipt has bounded metadata only, but must fit before mutation.
        bound = dump({'items': [{'ref': ref, 'status': 'workspace_unavailable'} for ref in args['refs']]})
        if session.remaining_tokens is not None and estimate_tokens(bound) + 32 > session.remaining_tokens:
            return dump({'error': 'budget_exceeded'})
        rows = {row['id']: row for row in session.rows()}
        results = []
        for ref in args['refs']:
            identity = session.issued.get(ref)
            status = 'unknown_ref'
            if identity is not None:
                mid, digest = identity
                row = rows.get(mid)
                if row is None:
                    status = 'unavailable'
                elif version(row) != digest:
                    status = 'stale_ref'
                elif name == 'context_release':
                    item = self.items.get(mid)
                    if item is not None and item['version'] == digest:
                        del self.items[mid]
                        self.events.append({'action': 'release', 'memory_id': mid})
                        status = 'released'
                    else:
                        status = 'not_kept'
                elif mid in self.items:
                    if self.items[mid]['version'] != digest:
                        del self.items[mid]
                        self.events.append({'action': 'auto_release', 'memory_id': mid, 'reason': 'stale'})
                        status = 'stale_ref'
                    else:
                        status = 'already_kept'  # Repeating keep does not renew TTL.
                elif ref not in session.explicitly_loaded or ref not in session.sent:
                    status = 'not_loaded'
                else:
                    cost = len(dump({'title': row['title'], 'content': conversation_content(row)}))
                    used = sum(item['cost'] for item in self.items.values())
                    if (len(self.items) >= self.cfg.context.working_set_max_items or
                            used + cost > self.cfg.context.working_set_max_chars):
                        status = 'capacity_full'
                    else:
                        self.sequence += 1
                        self.items[mid] = {'version': digest, 'cost': cost, 'order': self.sequence,
                                          'turns_left': self.cfg.context.working_set_ttl_turns}
                        self.events.append({'action': 'keep', 'memory_id': mid})
                        status = 'kept'
            results.append({'ref': ref, 'status': status})
        self.events.append({'action': name, 'results': [
            {'ref_hash': hashlib.sha256(item['ref'].encode()).hexdigest()[:16],
             'memory_id': session.issued[item['ref']][0] if item['ref'] in session.issued else None,
             'status': item['status']} for item in results], 'kept_count': len(self.items)})
        return dump({'items': results})

    def metadata(self):
        return {'scope': self.scope, 'calls': self.calls,
                'kept': [{'memory_id': mid, 'turns_left': item['turns_left']}
                         for mid, item in self.items.items()], 'events': list(self.events)}
