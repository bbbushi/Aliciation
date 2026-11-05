"""Dynamic recap generation/review accounting through the copied real chat loop."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import check_support as support
import time
WORK = support.isolated_workspace('recap_usage', 'workspace_' + str(time.time_ns()))
GUARD = support.protect(WORK)
import json
import threading
import uuid
from types import SimpleNamespace as NS
from unittest.mock import patch
from alice import loop, llm, channels, observation, persona_context
from alice.config import Config
from alice.store import Store

RESULTS = support.Results('recap_usage')
FIXTURES = WORK.parent / ('fixtures_' + str(time.time_ns()))
FIXTURES.mkdir()
QUOTE = 'This synthetic history permits only local reading.'
FINAL = 'Synthetic ordinary final reply.'

class ProviderError(Exception):
    status_code = 429

class FakeProvider:
    def __init__(self, sequence):
        self.sequence, self.calls = list(sequence), []
        self.chat = NS(completions=self)
    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.sequence:
            raise AssertionError('Unexpected additional provider call')
        item = self.sequence.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

def receipt(text, prompt, completion):
    return NS(model='synthetic-receipt-model', usage=NS(prompt_tokens=prompt, completion_tokens=completion),
              choices=[NS(message=NS(content=text, tool_calls=None, role='assistant'), finish_reason='stop')])

def add_request(store, request, epoch):
    now=time.time()
    store.conn.execute('INSERT INTO channel_requests(request_id,session_epoch,channel_id,source_key,source_json,payload_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
        (request,epoch,'gui',request,'{}','synthetic','accepted',now,now))
    store.conn.commit()

def run_case(name, *, partial=False, failure=None):
    store=Store(FIXTURES/(name+'.sqlite3'))
    try:
        store.conn.executescript(channels.SCHEMA)
        epoch=uuid.uuid4().hex
        store.meta_set('session_epoch',epoch)
        pairs=[]
        for index in range(4):
            request='synthetic_old_'+str(index)
            add_request(store,request,epoch)
            uid=store.add_turn('user',QUOTE if index==0 else 'Synthetic earlier user '+str(index),request_id=request)
            aid=store.add_turn('assistant','Synthetic earlier assistant '+str(index),request_id=request)
            store.conn.execute("UPDATE channel_requests SET status='completed' WHERE request_id=?",(request,))
            store.conn.commit()
            pairs.append((uid,aid))
        cfg=Config()
        cfg.llm.chat_model,cfg.llm.cheap_model='synthetic-main','synthetic-review'
        cfg.llm.base_url='https://invalid.example'
        cfg.context.transform_review_enabled=False
        cfg.context.recap_enabled=True
        cfg.context.recent_turns=2
        cfg.context.recap_min_rows=2
        cfg.context.recap_span_max_rows=20
        generation=json.dumps({'items':[{'turn_id':pairs[0][0],'quote':QUOTE}]})
        approval=json.dumps({'decisions':[{'id':0,'valid':True,'reason':'Synthetic deterministic approval'}]})
        sequence=[TimeoutError('synthetic generation failure')] if failure=='generation' else [receipt(generation,7,None if partial else 2)]
        if failure!='generation':
            sequence.append(ProviderError('synthetic review failure') if failure=='review' else receipt(approval,11,3))
        sequence.append(receipt(FINAL,20,8))
        provider=FakeProvider(sequence)
        request='synthetic_current_'+uuid.uuid4().hex
        add_request(store,request,epoch)
        snapshot=persona_context.parse_snapshot('## 底色 · 气质与风格\n- 简洁说明当前问题。\n')
        usage,delays={},[]
        with patch.object(persona_context,'load_persona_snapshot',return_value=snapshot), \
             patch.object(llm,'time',NS(monotonic=time.monotonic,sleep=delays.append)):
            reply=loop.chat_turn(store,provider,cfg,'请继续这次普通对话。',request_id=request,usage_out=usage,lock=threading.RLock())
        assert reply==FINAL and not provider.sequence
        assert delays==[], 'Recap failure must not retry/sleep'
        events=observation.llm_calls(store,request_id=request)['items']
        bases=observation.context_basis(store)['items']
        basis=next(x['basis'] for x in bases if x['basis']['request_id']==request)
        recap=basis['recap']
        cards=[dict(x) for x in store.conn.execute('SELECT * FROM context_recap_cards')]
        assert basis['usage']==usage
        assert len(events)==len(provider.calls)
        assert all(x['request_id']==request and x['attempt']==1 for x in events)
        expected_purposes={'recap_generate','main'} if failure=='generation' else {'recap_generate','recap_review','main'}
        assert {x['purpose'] for x in events}==expected_purposes
        assert recap['model_calls_attempted']==(1 if failure=='generation' else 2)
        assert all(x['usage_state']=='reported' for x in events if x['purpose']=='main')
        assert '原文：' in provider.calls[0]['messages'][0]['content'] and QUOTE in provider.calls[0]['messages'][0]['content']
        if failure!='generation':
            review_input=json.loads(provider.calls[1]['messages'][1]['content'])
            assert review_input['kind']=='recap' and QUOTE in review_input['candidates'][0]['recap_md']
        if failure is None:
            expected_recap={'prompt':18,'completion':3 if partial else 5}
            expected_total={'prompt':38,'completion':11 if partial else 13}
            if partial:
                expected_recap['incomplete']=expected_total['incomplete']=True
            assert recap['usage']==expected_recap and usage==expected_total,(recap['usage'],usage)
            assert recap['generation']=='saved_or_existing' and len(cards)==1
            assert recap['review']['valid'] is True and recap['card_ids']==[cards[0]['id']]
            assert QUOTE in cards[0]['recap_md']
            main_material='\n'.join(x['content'] for x in provider.calls[-1]['messages'] if isinstance(x.get('content'),str))
            assert '历史前情资料' in main_material and QUOTE in main_material
            gen_event=next(x for x in events if x['purpose']=='recap_generate')
            assert gen_event['usage_state']==('partial_receipt' if partial else 'reported')
        else:
            expected_recap={'incomplete':True} if failure=='generation' else {'prompt':7,'completion':2,'incomplete':True}
            expected_total={'prompt':20,'completion':8,'incomplete':True} if failure=='generation' else {'prompt':27,'completion':10,'incomplete':True}
            assert recap['usage']==expected_recap and usage==expected_total,(recap['usage'],usage)
            assert recap['generation']=='generation_failed' and not cards and not recap['card_ids']
            failed=next(x for x in events if x['purpose']==('recap_generate' if failure=='generation' else 'recap_review'))
            assert failed['usage']=='unknown' and failed['status']=='error'
        result={'case':name,'failure':failure,'partial_receipt':partial,'total_usage':usage,'recap_usage':recap['usage'],
                'recap_generation':recap['generation'],'recap_calls':recap['model_calls_attempted'],
                'total_calls':len(provider.calls),'cards_saved':len(cards),'events':events,'context_basis':basis,
                'reply':reply,'history_pairs':pairs,'backoff_seconds':delays,
                'real_path':'loop.chat_turn -> real history selection/RecapContext -> llm.chat -> fake provider -> SQLite recap cards/context_basis/public calls',
                'stubs':['in-memory persona loader','deterministic fake provider','backoff sleep observer']}
        (FIXTURES/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:
        store.close()

def success_matrix():
    return {'variants':[run_case('success_reported'),run_case('success_partial_generation',partial=True)]}

RESULTS.check('recap_generate_review_success_and_partial_sticky',success_matrix)
RESULTS.check('recap_generation_failure_unknown_reaches_both_aggregates',lambda:run_case('generation_failure',failure='generation'))
RESULTS.check('recap_review_failure_unknown_reaches_both_aggregates',lambda:run_case('review_failure',failure='review'))
result=RESULTS.save(workspace=str(WORK),fixtures=str(FIXTURES),guard_attempts=GUARD,
    scope='Three semantic scenarios, four real-loop traces; deterministic fake receipts do not validate real model grounding quality.')
print(json.dumps({'passed':result['passed'],'failed':result['failed'],'guard_attempts':GUARD}),flush=True)
sys.exit(bool(result['failed']))
