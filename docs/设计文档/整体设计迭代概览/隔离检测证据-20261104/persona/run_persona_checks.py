"""Ephemeral deterministic host-contract checks; no real model or private data."""
import sys
from pathlib import Path
RUN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUN))
from check_support import isolated_workspace, protect, Results
WORK = isolated_workspace('persona', 'workspace-run1')
ATTEMPTS = protect(WORK)

import contextlib
import json
import random
import sqlite3
from types import SimpleNamespace as NS
from alice import config as paths, persona, persona_context as pc, persona_growth as pg, thought
from alice.config import Config
from alice.store import Store
from alice.observation_memory import version

R = Results('persona')
CFG = Config()
CFG.llm.chat_model = 'deterministic-fixture-chat'
CFG.llm.cheap_model = 'deterministic-fixture-cheap'
SERIAL = 0
BASE = '# Alice\n\n## 底色 · 气质与风格\n\n- 温和、直接，保留条件。\n\n## 生长 · 自我叙事\n\n旧叙事保持。\n\n## 生长 · 与用户的关系\n\n原关系保持。\n\n## 生长 · 我现在的想法\n\n（尚未形成）\n'

def require(value, message):
    if not value:
        raise AssertionError(message)

def rejected(callback, exception=ValueError):
    try:
        callback()
    except exception:
        return
    raise AssertionError('Expected explicit rejection')

def document(style, samples=''):
    return '# Alice\n\n## 底色 · 气质与风格\n\n' + style + '\n\n' + (
        '## 底色 · 表达样本\n\n' + samples + '\n\n' if samples else '') + '## 生长 · 与用户的关系\n\nPRIVATE_RELATION_SENTINEL\n'

@contextlib.contextmanager
def fixture(name, md=BASE):
    global SERIAL
    SERIAL += 1
    root = WORK / 'fixtures' / ('%03d-' % SERIAL + name)
    root.mkdir(parents=True)
    paths.DATA_DIR = root
    paths.DB_PATH = root / 'alice.db'
    paths.PERSONA_DIR = root / 'persona'
    paths.PERSONA_FILE = paths.PERSONA_DIR / 'persona.md'
    paths.PERSONA_HISTORY_DIR = paths.PERSONA_DIR / 'history'
    paths.PERSONA_CHANGELOG = paths.PERSONA_DIR / 'CHANGELOG.md'
    persona.PERSONA_IMPORT_DIR = paths.PERSONA_DIR / 'imports'
    if md is not None:
        paths.PERSONA_DIR.mkdir()
        paths.PERSONA_FILE.write_text(md, encoding='utf-8')
    store = Store(paths.DB_PATH)
    store.meta_set('session_epoch', '%032x' % SERIAL)
    try:
        yield store, root
    finally:
        store.close()

class FakeClient:
    def __init__(self, result, hook=None):
        self.result, self.hook, self.calls = result, hook, []
        self.chat = NS(completions=NS(create=self.create))
    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.hook:
            self.hook(kwargs)
        payload = self.result(kwargs) if callable(self.result) else self.result
        return NS(choices=[NS(message=NS(content=json.dumps(payload, ensure_ascii=False)))],
                  usage=None, model='deterministic-fixture-chat')

def prior(store, claim, state='先验'):
    content = ('主张：' + thought._field_value(claim) + '\n事实：合成契约夹具，不是真实经历\n'
               '解释：捐赠——仅验证宿主状态机\n不确定：尚待验证\n下次回应：保留条件\n出处：合成夹具\n状态：' + state)
    return store.insert_memory(title=claim[:24], content=content, layer='longterm', kind='stance')

def event(store, title='独立事件A'):
    return store.insert_memory(title=title, content='合成宿主契约夹具；无真实用户信息。', kind='event')

def ref(store, mid):
    return {'id': mid, 'version': version(store.get_memory(mid))}

def verdict(target, kind='证实', source='独立事件A'):
    return {'target': target, 'verdict': kind, 'honored': '无从判断', 'note': '确定性夹具裁决', 'sources': [source]}

def view(target=None, claim='替代后的完整主张保留全部限制条件而不是只有二十四字符的索引', source='独立事件A'):
    return {'claim': claim, 'encounter': '合成输入', 'facts': '夹具事实',
            'interp': {'kind': '推断', 'note': '宿主契约验证'}, 'missing': '不验证模型语义',
            'next_response': '保留完整条件', 'sources': [source], 'replaces': target}

def execute(store, data, hook=None):
    client = FakeClient(data, hook)
    result = thought.deliberate(store, client, CFG, request_id='fixture-request-' + str(SERIAL))
    require(len(client.calls) == 1, 'Expected exactly one deterministic model boundary call')
    return result, client

def rows(store):
    return [dict(x) for x in store.conn.execute('SELECT * FROM memories ORDER BY id')]

def parser_matrix():
    total = 0
    for indent in ('', ' ', '  ', '\t', ' \t'):
        for newline in ('\n', '\r\n', '\r'):
            raw = BASE.replace('## ', indent + '## ').replace('\n', newline)
            snap = pc.parse_snapshot(raw)
            require('温和' in snap['sections']['底色 · 气质与风格'], 'Indented style disappeared')
            require(pg.bodies(raw) == snap['sections'], 'Reader and writer boundaries disagree')
            out = pg.replace_bodies(raw, {'生长 · 自我叙事': '新的有界叙事'})
            result = pc.parse_snapshot(out)
            require(result['sections']['生长 · 自我叙事'] == '新的有界叙事', 'Wrong section changed')
            for key in ('底色 · 气质与风格', '生长 · 与用户的关系', '生长 · 我现在的想法'):
                require(result['sections'][key] == snap['sections'][key], 'Untargeted section changed')
            total += 1
    return {'variants': total}

def parser_rejection_matrix():
    bad = [BASE + '\n  ## 底色 · 气质与风格\n第二个底色\n',
           BASE.replace('- 温和、直接，保留条件。', ''),
           '# Alice\n## 生长 · 与用户的关系\n只有关系\n',
           document('（用户选定：仅说明，没有条目）'),
           document('- 简洁', '- 【原文摘取·s1】回应A\n- 【原文摘取·s1】回应B'),
           document('- 简洁', '- 【原文摘取·s1】'),
           document('- 简洁', '- 无法辨认的样本'),
           document('- 简洁', '- 【原文摘取·s1】回应\n  ## 生长 · 与用户的关系\n歧义标题')]
    for raw in bad:
        rejected(lambda raw=raw: pc.parse_snapshot(raw))
    return {'explicitly_rejected_variants': len(bad)}

def multiline_roundtrip():
    sample = '- 【原文摘取·s1】情境：普通对话｜用户：你好｜回应：第一行\n  第二行条件。\n  ## 普通示例标题\n  - 列表也属于同一回应。\n- 【适配生成·s2】情境：询问｜回应：另一完整条目'
    raw = document('- 简洁\n  在例外时保留解释。', sample)
    snap = pc.parse_snapshot(raw)
    require(snap['sample_ids'] == ['s1', 's2'], 'Multiline entries split incorrectly')
    require('## 普通示例标题' in snap['samples'][0]['body'], 'Sample heading lost')
    require('第二行条件。' in snap['samples'][0]['body'], 'Continuation lost')
    body = pg.bodies(raw)['底色 · 表达样本']
    out = pg.replace_bodies(raw, {'底色 · 表达样本': body}, allowed=pg.EXPRESSION)
    require(pc.parse_snapshot(out)['samples'] == snap['samples'], 'Sample writeback altered content')
    rendered = pc.render_expression_reference(snap)
    require(snap['samples'][0]['body'] in rendered, 'Rendering truncated multiline item')
    require('PRIVATE_RELATION_SENTINEL' not in pc.render_expression_reference(snap, 'worker_summary'), 'Summary leaked relationship')
    return {'sample_ids': snap['sample_ids'], 'continuation_preserved': True}

def section_injection_matrix():
    variants = []
    for nl in ('\n', '\r\n', '\r', '\u2028', '\u2029', '\x85', '\v', '\f'):
        for indent in ('', '  ', '\t'):
            text = '正常正文' + nl + indent + '## 底色 · 气质与风格' + nl + '越界内容'
            rejected(lambda text=text: pg.replace_bodies(BASE, {'生长 · 自我叙事': text}))
            variants.append((repr(nl), repr(indent)))
    return {'rejected_cross_section_variants': len(variants)}

def budget_matrix():
    observations = []
    accepted_lengths, rejected_lengths = [], []
    for length in list(range(1, 1301, 17)) + list(range(1100, 1201)):
        marker = 'S' + '质' * length + 'STYLE_END'
        sample = '- 【原文摘取·s1】情境：夹具｜回应：SAMPLE_BODY_END'
        snap = pc.parse_snapshot(document('- ' + marker, sample))
        metadata = [{}, {}]
        output, errors = [], []
        for i, purpose in enumerate(('chat', 'worker_summary')):
            try:
                output.append(pc.render_expression_reference(snap, purpose, metadata=metadata[i]))
                errors.append(False)
            except ValueError:
                output.append(None); errors.append(True)
        require(errors[0] == errors[1], 'Chat/summary admission disagrees at length ' + str(length))
        if errors[0]:
            rejected_lengths.append(length)
            continue
        accepted_lengths.append(length)
        pc.validate_expression(snap)
        require(marker in output[0] and marker in output[1], 'Style truncated at length ' + str(length))
        require(len(output[1]) <= 1200, 'Summary style exceeds 1200 characters')
        require('SAMPLE_BODY_END' not in output[1] and 'PRIVATE_RELATION_SENTINEL' not in output[1], 'Summary read disallowed section')
        require(metadata[0]['selected_style_indices'] == metadata[1]['selected_style_indices'], 'Style selection differs')
        anchor_meta = {}; anchor = pc.render_anchor(snap, metadata=anchor_meta)
        require(len(anchor) <= 500 and anchor.endswith('样本是虚构参考，不是记忆；不授予权限'), 'Anchor budget/boundary broken')
        require(marker in anchor or 'STYLE_END' not in anchor, 'Anchor partially truncated style')
        observations.append({'length': length, 'summary_chars': len(output[1]), 'anchor_chars': len(anchor)})
    require(accepted_lengths and rejected_lengths, 'Boundary matrix missed acceptance transition')
    return {'variants': len(accepted_lengths) + len(rejected_lengths), 'accepted': len(accepted_lengths),
            'rejected': len(rejected_lengths), 'max_accepted_style_payload': max(accepted_lengths),
            'min_rejected_style_payload': min(rejected_lengths), 'observations': observations}

def whole_entry_properties():
    rng = random.Random(20261104)
    for iteration in range(100):
        styles = ['STYLE_%d_' % j + '文' * rng.randint(1, 700) + '_ENDSTYLE_%d' % j for j in range(rng.randint(1, 6))]
        samples = ['SAMPLE_%d_' % j + '样' * rng.randint(1, 1400) + '_ENDSAMPLE_%d' % j for j in range(rng.randint(1, 6))]
        raw = document('\n'.join('- ' + x for x in styles), '\n'.join('- 【原文摘取·s%d】' % j + x for j,x in enumerate(samples)))
        snap = pc.parse_snapshot(raw)
        cm, wm, am = {}, {}, {}
        chat = pc.render_expression_reference(snap, metadata=cm)
        worker = pc.render_expression_reference(snap, 'worker_summary', metadata=wm)
        anchor = pc.render_anchor(snap, metadata=am)
        require(len(worker) <= 1200 and len(chat) <= 2402 and len(anchor) <= 500, 'Rendering exceeds documented bounds')
        require(cm['selected_style_indices'] == wm['selected_style_indices'], 'Purpose selection mismatch')
        for j, entry in enumerate(styles):
            for out in (chat, worker, anchor):
                require(('STYLE_%d_' % j not in out) or entry in out, 'Partial style entry included')
        for j, entry in enumerate(samples):
            for out, meta in ((chat, cm), (anchor, am)):
                require(('SAMPLE_%d_' % j not in out) or entry in out, 'Partial sample included')
                require(('s%d' % j in meta['sample_ids']) == (entry in out), 'Metadata differs from actual selected sample')
            require(entry not in worker, 'Summary included sample body')
        require(cm['chars'] == len(chat) and wm['chars'] == len(worker) and am['chars'] == len(anchor), 'Metadata length mismatch')
    return {'seed': 20261104, 'documents': 100}

def explicit_edit_and_growth():
    with fixture('edit') as (store, root):
        md = BASE.replace('## ', '  ## ')
        paths.PERSONA_FILE.write_text(md, encoding='utf-8')
        before = pg.bodies(md)
        sections = {key: before.get(key, '') for key in pg.EXPRESSION}
        sections['底色 · 气质与风格'] = '- 温和且保留完整限定。'
        sections['底色 · 表达样本'] = '- 【原文摘取·s1】回应第一行\n  ## 普通示例标题\n  下一行保留。'
        require(persona.revise_expression(store, pg.digest(md), sections), 'Valid explicit edit not applied')
        after = paths.PERSONA_FILE.read_text(encoding='utf-8')
        snap = pc.parse_snapshot(after); pc.validate_expression(snap)
        require('下一行保留。' in snap['samples'][0]['body'], 'Saved sample lost continuation')
        require(pg.bodies(after)['生长 · 与用户的关系'] == before['生长 · 与用户的关系'], 'Expression edit changed relationship')
        rejected(lambda: persona.revise_expression(store, pg.digest(md), sections))
        eid = event(store)
        evidence = pg.evidence_rows(store)
        payload, supplied = pg.proposal_input(after, evidence, 0)
        section = next(x for x in payload['sections'] if x['title'] == '生长 · 自我叙事')
        patches = {'patches':[{'title':section['title'], 'base_hash':section['base_hash'], 'content':'仅来自指定夹具的叙事。', 'evidence_ids':[eid], 'reason':'契约夹具'}]}
        grown, audit = pg.apply_proposal(after, patches, supplied, evidence)
        require(pc.parse_snapshot(grown)['samples'] == snap['samples'], 'Growth altered fixed expression samples')
        projected, metadata = pg.project(pg.bodies(grown), evidence)
        require(metadata['sections']['生长 · 自我叙事'] == 'valid', 'Valid growth evidence not recognized')
        store.expire_memory(eid, 'fixture evidence withdrawal')
        _, invalidated = pg.project(pg.bodies(grown), pg.evidence_rows(store))
        require(invalidated['sections']['生长 · 自我叙事'] == 'unavailable_evidence', 'Growth retained withdrawn evidence')
        return {'saved_samples': snap['sample_ids'], 'growth_patch_count': len(audit), 'evidence_invalidated': True}

PREFIX = '这是前二十四字符完全相同的条件性主张用于核查身份并保留边界'[:24]
CLAIM_A, CLAIM_B = PREFIX + '；仅当条件甲成立时适用，否则拒绝。', PREFIX + '；仅当条件乙成立时适用，否则退出。'

def identity_verification():
    with fixture('verify') as (store, root):
        a,b = prior(store, CLAIM_A),prior(store, CLAIM_B); e = event(store)
        vb = version(store.get_memory(b)); ta = ref(store,a)
        result, client = execute(store, {'views':[], 'verifications':[verdict(ta)]})
        prompt = client.calls[0]['messages'][0]['content']
        require(CLAIM_A in prompt and CLAIM_B in prompt and ta['version'] in prompt, 'Complete target identities absent from actual prompt')
        require(thought._field_of(store.get_memory(a)['content'],'状态') == '验证', 'Long claim not verified')
        require(version(store.get_memory(b)) == vb, 'Same-prefix unrelated prior changed')
        dep = store.conn.execute('SELECT parent_memory_id FROM memory_dependencies WHERE memory_id=?',(a,)).fetchall()
        require([x[0] for x in dep] == [e], 'Verification evidence not bound to target')
        rendered = thought._render_stances(store)
        require(CLAIM_A in rendered and CLAIM_B in rendered, 'Same-title claims hidden')
        store.expire_memory(e, 'fixture source withdrawn')
        require(store.get_memory(a) is None and store.get_memory(b) is not None, 'Withdrawal not limited to dependent target')
        return {'result':result, 'verified_id':a, 'unaffected_id':b, 'withdrawal_cascaded':True}

def identity_falsification():
    with fixture('falsify') as (store, root):
        a,b = prior(store,CLAIM_A),prior(store,CLAIM_B); event(store)
        result,_ = execute(store, {'views':[], 'verifications':[verdict(ref(store,a),'证伪')]})
        require(store.get_memory(a) is None and store.get_memory(b) is not None, 'Falsification retired wrong identity')
        require(store.conn.execute('SELECT count(*) FROM memory_tombstones WHERE memory_id=?',(a,)).fetchone()[0] == 1, 'Retirement not audited')
        text = paths.PERSONA_FILE.read_text(encoding='utf-8')
        require(CLAIM_A not in text and CLAIM_B in text, 'Narrative retained falsified same-prefix claim')
        return {'result':result, 'retired_id':a, 'remaining_id':b}

def batch_lifecycle(kind, different_basis):
    with fixture('batch-' + kind + str(different_basis)) as (store, root):
        a,b = prior(store,CLAIM_A),prior(store,CLAIM_B)
        event(store); event(store,'独立事件B'); oldref=ref(store,a)
        newclaim = CLAIM_A + '新增后半段限定不丢失。'
        source = '独立事件B' if different_basis else '独立事件A'
        data={'verifications':[verdict(oldref,kind)],'views':[view(oldref,newclaim,source)]}
        result,_=execute(store,data)
        revised = kind == '证伪' or different_basis
        require((store.get_memory(a) is None)==revised,'Unexpected original retirement')
        require(store.get_memory(b) is not None,'Sibling target changed')
        revisions=[dict(r) for r in store.conn.execute('SELECT * FROM view_revisions WHERE old_id=?',(a,))]
        require(len(revisions)==int(revised),'Revision not atomic with expected retirement')
        if revised:
            new=store.get_memory(revisions[0]['new_id'])
            require(new is not None and thought._field_of(new['content'],'主张')==newclaim,'New full claim missing')
            require(thought._field_of(new['content'],'曾以为')==CLAIM_A,'Former full claim missing')
            require(oldref['version'] in revisions[0]['reason'],'Original input version not audited')
            require(not store.conn.in_transaction,'Operation left transaction open')
        return {'result':result,'kind':kind,'new_basis':different_basis,'revised':revised}

def invalid_receipt_matrix():
    variants=('unknown','stale','bool_id','missing_version','text_ref','duplicate_verdict','duplicate_replacement','oversize_claim')
    for case in variants:
        with fixture('invalid-'+case) as (store,root):
            a=prior(store,CLAIM_A);event(store); target=ref(store,a)
            data={'views':[],'verifications':[verdict(dict(target))]}
            if case=='unknown':data['verifications'][0]['target']['id']=999999
            elif case=='stale':data['verifications'][0]['target']['version']='0'*64
            elif case=='bool_id':data['verifications'][0]['target']['id']=True
            elif case=='missing_version':del data['verifications'][0]['target']['version']
            elif case=='text_ref':data={'views':[view(CLAIM_A)],'verifications':[]}
            elif case=='duplicate_verdict':data['verifications'].append(verdict(target))
            elif case=='duplicate_replacement':data={'views':[view(target),view(target, '另一条替换')],'verifications':[]}
            elif case=='oversize_claim':data={'views':[view(None,'长'*1201)],'verifications':[]}
            before=rows(store);md=paths.PERSONA_FILE.read_bytes()
            result,_=execute(store,data)
            require('无效' in result,'Malformed receipt did not report rejection: '+case)
            require(rows(store)==before and paths.PERSONA_FILE.read_bytes()==md,'Malformed receipt mutated memory/persona: '+case)
    return {'rejected_receipts':len(variants),'variants':list(variants)}

def changed_during_call_matrix():
    for case in ('target_changed','source_changed','target_deleted','source_deleted'):
        with fixture(case) as (store,root):
            a=prior(store,CLAIM_A);e=event(store); target=ref(store,a)
            def hook(_):
                mid=a if case.startswith('target') else e
                if case.endswith('deleted'):store.expire_memory(mid,'fixture concurrent withdrawal')
                else:store.update_memory_content(mid,store.get_memory(mid)['content']+'\n后来的明确修订')
            result,_=execute(store,{'views':[view(target)],'verifications':[]},hook)
            require('未写入观点' in result,'Changed input was accepted: '+case)
            require(not store.conn.execute('SELECT 1 FROM view_revisions').fetchone(),'Changed input still produced revision')
            require('替代后的完整主张' not in paths.PERSONA_FILE.read_text(encoding='utf-8'),'Changed target affected persona')
    return {'interleavings':4}

def revision_transaction_rollback():
    with fixture('rollback') as (store,root):
        a=prior(store,CLAIM_A);event(store);target=ref(store,a)
        store.conn.execute("CREATE TRIGGER fixture_block_retirement BEFORE DELETE ON memories WHEN old.id="+str(a)+" BEGIN SELECT RAISE(ABORT,'fixture retirement abort'); END;")
        store.conn.commit()
        client=FakeClient({'views':[view(target)],'verifications':[]})
        rejected(lambda:thought.deliberate(store,client,CFG),sqlite3.IntegrityError)
        require(store.get_memory(a) is not None,'Original lost after transaction abort')
        require(store.conn.execute("SELECT count(*) FROM memories WHERE kind='stance'").fetchone()[0]==1,'Inserted replacement survived transaction abort')
        require(not store.conn.execute('SELECT 1 FROM view_revisions').fetchone(),'Revision link survived transaction abort')
        require(not store.conn.in_transaction,'Rollback left transaction open')
        return {'mechanism':'temporary SQLite trigger aborts old-ID DELETE','model_boundary_calls':len(client.calls),'atomic_rollback':True}

def descendant_not_independent():
    with fixture('self-basis') as (store,root):
        a=prior(store,CLAIM_A);e=event(store)
        store.bind_derived_memory(e,[store.get_memory(a)],scope='gui')
        old=version(store.get_memory(a))
        result,_=execute(store,{'views':[view(ref(store,a))],'verifications':[verdict(ref(store,a))]})
        require(version(store.get_memory(a))==old,'Descendant evidence mutated its ancestor')
        require(not store.conn.execute('SELECT 1 FROM view_revisions').fetchone(),'Self-derived evidence revised ancestor')
        return {'result':result,'independent_basis_required':True}

def narrative_heading_matrix():
    for nl in ('\n','\r\n','\r','\u2028','\u2029','\x85','\v','\f'):
        with fixture('heading-'+str(ord(nl[0]))) as (store,root):
            claim='前半完整主张'+nl+'  ## 底色 · 越权新小节'+nl+'后半限定条件'
            prior(store,claim)
            rendered=thought._render_stances(store)
            combined=thought._write_section(BASE,rendered)
            require(set(pc.parse_snapshot(combined)['sections'])==set(pc.parse_snapshot(BASE)['sections']),'Claim created a new persona section: '+repr(nl))
            require('后半限定条件' in combined,'Escaping lost complete claim')
    return {'newline_variants':8,'sections_preserved':True}

def no_persona_and_invalid_persona():
    with fixture('no-persona',None) as (store,root):
        event(store)
        result,_=execute(store,{'views':[view()],'verifications':[]})
        require(not paths.PERSONA_FILE.exists(),'Thought created persona without base style')
        require('未创建' in result,'Missing persona not communicated')
    with fixture('invalid-persona','# Alice\n## 生长 · 我现在的想法\n只有观点\n') as (store,root):
        event(store)
        client=FakeClient({'views':[view()],'verifications':[]})
        result=thought.deliberate(store,client,CFG)
        require(not client.calls and '结构' in result,'Invalid persona was sent to paid boundary')
    return {'missing_persona_creates_no_file':True,'invalid_persona_model_calls':0}

def adoption_reason_matrix():
    allowed={'adopt':{'none'},'adapt':{'existence_conflict','honesty_conflict','basecolor_conflict','insufficient_support'},
             'reject':{'existence_conflict','honesty_conflict','basecolor_conflict','insufficient_support'},'undecided':{'insufficient_support'}}
    accepted=denied=0
    with fixture('adoption-reasons') as (store,root):
        extraction={'style_traits':['明确边界'],'situation_reactions':[],
                    'candidate_stances':[{'id':'c1','claim':CLAIM_A,'from_donor':'夹具段落'}]}
        for decision in allowed:
            for reason in ('none','existence_conflict','honesty_conflict','basecolor_conflict','insufficient_support'):
                audit=root/(decision+'-'+reason);audit.mkdir()
                item={'id':'c1','decision':decision,'reason_type':reason,'note':'契约夹具',
                      'evidence_ids':['donor:c1'],'adapted_claim':CLAIM_A+'适配后的明确限制。'}
                client=FakeClient({'decisions':[item]})
                if reason in allowed[decision]:
                    out=persona._review_adoption(client,CFG,extraction,audit,store)
                    require(out[0]['decision']==decision,'Valid combination altered');accepted+=1
                else:
                    rejected(lambda:persona._review_adoption(client,CFG,extraction,audit,store));denied+=1
                require(len(client.calls)==1,'Reason checker retried model')
    return {'combinations':20,'accepted':accepted,'rejected':denied}

R.check('parser_indentation_newline_read_write_matrix',parser_matrix)
R.check('parser_explicit_rejections',parser_rejection_matrix)
R.check('multiline_samples_roundtrip_and_summary_isolation',multiline_roundtrip)
R.check('cross_section_injection_rejection_matrix',section_injection_matrix)
R.check('chat_summary_budget_boundary_matrix',budget_matrix)
R.check('whole_entry_and_metadata_properties',whole_entry_properties)
R.check('explicit_edit_growth_and_evidence_withdrawal',explicit_edit_and_growth)
R.check('long_claim_same_prefix_verification_and_withdrawal',identity_verification)
R.check('long_claim_same_prefix_falsification',identity_falsification)
for kind,different in (('证伪',False),('证伪',True),('证实',False),('证实',True)):
    R.check('batch_'+kind+'_new_basis_'+str(different),lambda kind=kind,different=different:batch_lifecycle(kind,different))
R.check('invalid_identity_version_receipts_no_mutation',invalid_receipt_matrix)
R.check('inputs_changed_during_model_boundary',changed_during_call_matrix)
R.check('revision_insert_link_retirement_transaction_rollback',revision_transaction_rollback)
R.check('descendant_cannot_validate_or_replace_ancestor',descendant_not_independent)
R.check('claim_heading_injection_all_newlines',narrative_heading_matrix)
R.check('missing_and_invalid_persona_safety',no_persona_and_invalid_persona)
R.check('adoption_decision_reason_cartesian_matrix',adoption_reason_matrix)
report=R.save(workspace=str(WORK),source_root=str(WORK),guard_attempts=ATTEMPTS,
    model='deterministic fake responses only; zero network or real models',
    scope='temporary copied source and synthetic SQLite fixtures; host contracts only',
    human_remaining=['real model semantics and adoption quality','real Windows filesystem recovery/deployment','long-term persona quality'])
print(json.dumps({'passed':report['passed'],'failed':report['failed'],'guard_attempts':ATTEMPTS},ensure_ascii=False),flush=True)
sys.exit(bool(report['failed']))
