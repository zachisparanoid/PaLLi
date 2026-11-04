"""Opt-in, metadata-only causal tracing for a PaLLi installation.

Enable with PALLI_COGNITIVE_TRACE=1. This module never records prompt or
conversation text. Event nesting describes execution, not causation.
"""
from __future__ import annotations

import contextvars
import dataclasses
import functools
import hashlib
import hmac
import importlib
import inspect
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / 'data' / 'cognitive_traces'
_trace = contextvars.ContextVar('cognitive_trace', default=None)
_session = contextvars.ContextVar('cognitive_session', default='')
_parent = contextvars.ContextVar('cognitive_span', default=None)
_shown = contextvars.ContextVar('cognitive_stakes_shown', default=())
_prompt_tags = contextvars.ContextVar('cognitive_prompt_tags', default={})
_rank_cf = contextvars.ContextVar('cognitive_rank_counterfactual', default=None)
_own_candidates = contextvars.ContextVar('cognitive_own_candidates', default=None)
_own_index: dict[str, dict[str, str]] = {}
_versions: dict[str, dict[str, str]] = {}
_moment_index: dict[str, str] = {}
_lock = threading.RLock()
_installed = False
_alias_pairs: list[tuple[Any, Any]] = []
TRACE_REVISION = 'palli-1'
_key = None
_key_dir = None
_sink_state = None
_cap_warned = False
logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.environ.get('PALLI_COGNITIVE_TRACE') == '1'


def trace_root() -> Path:
    """Keep tests in their isolated test root; ordinary runs stay under data/."""
    test_root = os.environ.get('PALLI_TEST_DATA_ROOT')
    return (Path(test_root).resolve() / 'cognitive_traces') if test_root else RUN_ROOT.resolve()


def trace_dir() -> Path:
    base = trace_root()
    p = Path(os.environ.get('PALLI_COGNITIVE_TRACE_DIR', str(base))).resolve()
    if p != base and base not in p.parents:
        raise ValueError('trace directory must be inside the configured cognitive trace root')
    return p


def _trace_key() -> bytes:
    global _key, _key_dir
    with _lock:
        d = trace_dir()
        if _key is not None and _key_dir == d: return _key
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        keyfile = d / '.trace-key'
        try:
            fd = os.open(keyfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as f: f.write(os.urandom(32))
        except FileExistsError:
            pass
        for _ in range(10):
            value = keyfile.read_bytes()
            if len(value) == 32:
                _key, _key_dir = value, d
                return value
            time.sleep(0.01)  # another process may still be creating the key
        raise ValueError('invalid trace key')


def _sink_fd():
    """Pin a verified directory descriptor; avoid repeated filesystem opens."""
    global _sink_state, _cap_warned
    raw=os.environ.get('PALLI_COGNITIVE_TRACE_DIR',str(trace_root()))
    day=time.strftime('events-%Y-%m-%d',time.gmtime())+'.jsonl'
    if _sink_state is not None:
        old_raw,old_day,dirfd,fd=_sink_state
        if old_raw==raw and old_day==day and os.fstat(fd).st_nlink:
            return fd
        os.close(fd);os.close(dirfd);_sink_state=None
    d=trace_dir();d.mkdir(parents=True,exist_ok=True,mode=0o700)
    dirfd=os.open(d,os.O_RDONLY | getattr(os,'O_DIRECTORY',0) | getattr(os,'O_NOFOLLOW',0))
    try:
        fd=os.open(day,os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os,'O_NOFOLLOW',0),0o600,dir_fd=dirfd)
    except BaseException:
        os.close(dirfd);raise
    _sink_state=(raw,day,dirfd,fd);_cap_warned=False
    return fd


def entity(namespace: str, value: Any) -> str:
    if value is None or value == '': return ''
    return namespace + ':' + hmac.new(_trace_key(), (namespace + ':' + str(value)).encode(), hashlib.sha256).hexdigest()[:16]


def _trim_cache(cache: dict) -> None:
    # Detached tasks can outlive their root span; bound their retained IDs.
    while len(cache) > 512:
        cache.pop(next(iter(cache)))


def _remember_version(eid: str, value: Any) -> None:
    tid=_trace.get()
    if tid and eid and value:
        version=entity('version',value)
        with _lock:
            _versions.setdefault(tid,{})[eid]=version
            _trim_cache(_versions)


def emit(stage: str, organ: str, action: str, *, evidence: str = 'execution',
         entity_id: str = '', outcome: str = '', count: int | None = None,
         duration_ms: int | None = None, parent: str | None = None,
         event_id: str | None = None, changed: bool | None = None,
         details: dict | None = None) -> None:
    """Best-effort sink; only explicitly allowed, bounded metadata is persisted."""
    if not enabled(): return
    try:
        # Labels are operator supplied but restricted to non-sensitive identifiers.
        label = os.environ.get('PALLI_COGNITIVE_TRACE_RUN', 'default')
        label = label if re.fullmatch(r'[A-Za-z0-9_-]{1,40}', label) else 'default'
        event = {'v':1, 'revision':TRACE_REVISION, 'id':event_id or uuid.uuid4().hex, 'ts':time.time(),
                 'trace_id':_trace.get() or '', 'parent_id':parent if parent is not None else (_parent.get() or ''),
                 'run':label, 'stage':stage, 'organ':organ, 'action':action,
                 'evidence':evidence, 'entity':entity_id, 'outcome':outcome}
        version=_versions.get(_trace.get() or '',{}).get(entity_id,'')
        if version: event['entity_version']=version
        if _session.get(): event['session'] = _session.get()
        if count is not None: event['count'] = int(count)
        if duration_ms is not None: event['duration_ms'] = max(0, int(duration_ms))
        if changed is not None: event['changed'] = bool(changed)
        if details:
            # Never forward arbitrary model output, raw user IDs, exception text or content.
            allowed = {'responded','quiet','selected','included','input_count','output_count','rank_changed','set_changed','success','has_error','candidate_count','has_text','has_result','actual_rank','baseline_rank'}
            event['details'] = {k:v for k,v in details.items() if k in allowed and isinstance(v,(bool,int,float))}
        encoded = (json.dumps(event, separators=(',',':'), ensure_ascii=True)+'\n').encode()
        cap = max(1, min(200, int(os.environ.get('PALLI_COGNITIVE_TRACE_MAX_MB', '20')))) * 1024 * 1024
        with _lock:
            global _cap_warned
            fd=_sink_fd()
            if os.fstat(fd).st_size + len(encoded) > cap:
                if not _cap_warned:
                    logger.warning('cognitive trace: daily size limit reached; events are being dropped')
                    _cap_warned=True
                return
            os.write(fd,encoded)
    except Exception:
        # Do not expose exception values: they may contain private data.
        logger.debug('cognitive trace: event dropped', exc_info=False)


def prune(days: int = 14) -> int:
    """Prune only owned event files, never the HMAC key or unrelated files."""
    cutoff = time.time() - max(1, int(days))*86400
    d = trace_dir(); n = 0
    if not d.exists(): return n
    for p in d.glob('events-????-??-??.jsonl'):
        if p.is_file() and not p.is_symlink() and p.stat().st_mtime < cutoff:
            p.unlink(); n += 1
    return n


def _safe(callback, *args):
    try: callback(*args)
    except Exception: logger.debug('cognitive trace: observer failed', exc_info=False)


def _entry_id(item):
    if isinstance(item, dict): return item.get('id') or item.get('entry_id')
    return getattr(item, 'id', None) or getattr(item, 'entry_id', None)


def _observe(kind, result, args, kwargs):
    if kind == 'web_search_result':
        # A None response can indicate provider failure; it is not an empty result.
        if isinstance(result, dict) and isinstance(result.get('results'), list):
            count=len(result['results'])
            emit('retrieval','web_search','results_returned',evidence='retrieval',count=count,details={'has_result':count>0})
        else:
            emit('retrieval','web_search','result_unavailable',evidence='execution')
    elif kind == 'impulse' and isinstance(result,dict):
        value=result.get('kind')
        if value not in ('say','do','hold','none'): value='other'
        emit('decision','present_moment','impulse_candidate',evidence='model_decision',outcome=value)
    elif kind == 'candidate':
        entry=getattr(result,'entry',None)
        if entry is not None:
            raw=importlib.import_module('ai.inner_memory_index')._entry_id(entry)
            emit('prioritization','proactive','eligible_candidate',evidence='decision',entity_id=entity('inner',raw),details={'has_result':True})
        else: emit('prioritization','proactive','eligible_candidate',evidence='decision',details={'has_result':False})
    elif kind == 'timing' and result is not None:
        reason=str(getattr(result,'reason','') or '')
        allowed={'action_policy_block','quiet_hours','daily_cap_reached','frequency_limit','allowed'}
        emit('decision','proactive','timing',evidence='decision',outcome=reason if reason in allowed else 'other',details={'success':bool(getattr(result,'allowed',False))})
    elif kind == 'night_write':
        text=str(kwargs.get('text') or '').strip()
        if text: emit('persistence','night_cycle','reflection_written',evidence='persisted',entity_id=entity('night',text))
    elif kind == 'night_read' and result is not None:
        text=str(getattr(result,'text','') or '').strip()
        if text: emit('retrieval','night_cycle','reflection_read',evidence='retrieval',entity_id=entity('night',text))
    elif kind == 'stakes':
        for item in result or ():
            eid=entity('stake',_entry_id(item))
            _remember_version(eid,getattr(item,'stake',''))
            emit('retrieval','stakes','open_stake',evidence='retrieval',entity_id=eid)
    elif kind == 'stake_write' and isinstance(result,dict):
        for key in ('opened','closed','dormant'):
            for item in result.get(key,[]) or []:
                raw = _entry_id(item) if isinstance(item,dict) else item
                if isinstance(raw,(str,int)):
                    emit('persistence','stakes',key,evidence='persisted',entity_id=entity('stake',raw))
        emit('persistence','stakes','apply_ops',evidence='persisted',count=int(result.get('applied',0)),details={'input_count':len(args[0]) if args and isinstance(args[0],list) else 0})
    elif kind == 'fact_write' and result is not None:
        eid=entity('fact',result)
        content=kwargs.get('content') or (args[3] if len(args)>3 else '')
        _remember_version(eid,content)
        emit('persistence','facts','memory_written',evidence='persisted',entity_id=eid)
    elif kind == 'today_fact' and result is not None:
        eid=entity('fact',result); _remember_version(eid,kwargs.get('content',''))
        emit('persistence','facts','today_slot_upsert',evidence='persisted',entity_id=eid)
    elif kind == 'stake_check':
        ctx=args[0] if args else kwargs.get('ctx')
        drawn=getattr(ctx,'own_stake_drawn',None)
        if drawn is not None: emit('feedback','stakes','said_check_completed',evidence='execution',entity_id=entity('stake',_entry_id(drawn)))
    elif kind == 'stake_feedback':
        raw = args[0] if args else kwargs.get('stake_id')
        if result and raw is not None: emit('feedback','stakes','said_stamp',evidence='persisted',entity_id=entity('stake',raw))
    elif kind == 'fear_plant' and result is not None:
        emit('persistence','fears','plant',evidence='persisted',entity_id=entity('fear',_entry_id(result)))
    elif kind == 'fears':
        for item in result or ():
            eid=entity('fear',_entry_id(item))
            _remember_version(eid,getattr(item,'text',''))
            emit('retrieval','fears','held',evidence='retrieval',entity_id=eid)
            candidates=_own_candidates.get()
            if candidates is not None:
                short=importlib.import_module('core.agent.own_life')._short(getattr(item,'text',''))
                candidates.setdefault(('fear',short),[]).append(eid)
    elif kind == 'daydreams':
        for item in result or ():
            text=str(getattr(item,'text','') or '').strip()
            if not text: continue
            eid=entity('daydream',text)
            emit('retrieval','daydream','entry',evidence='retrieval',entity_id=eid)
            candidates=_own_candidates.get()
            if candidates is not None:
                short=importlib.import_module('core.agent.own_life')._short(text)
                candidates.setdefault(('daydream',short),[]).append(eid)
    elif kind in ('daydream_write','moment_write'):
        text=str(kwargs.get('text') or '').strip()
        if text:
            name='daydream' if kind=='daydream_write' else 'moment'
            limit=900 if name=='daydream' else 1200
            emit('persistence',name,'written',evidence='persisted',entity_id=entity(name,text[:limit]))
    elif kind == 'moment_read' and result:
        item=result[0]; text=str(getattr(item,'text','') or '').strip()
        if text:
            eid=entity('moment',text)
            if _trace.get():
                with _lock:
                    _moment_index[_trace.get()]=eid
                    _trim_cache(_moment_index)
            emit('retrieval','present_moment','current',evidence='retrieval',entity_id=eid)
    elif kind == 'own_items':
        candidates=_own_candidates.get() or {}; mapped={}
        for item in result or ():
            matches=set(candidates.get((getattr(item,'kind',''),getattr(item,'text','')),[]))
            if len(matches)==1:
                eid=next(iter(matches)); mapped[str(item.tag)]=eid
                emit('prioritization','own_life','selected_for_slate',evidence='decision',entity_id=eid)
        if _trace.get():
            with _lock:
                _own_index[_trace.get()]=mapped
                _trim_cache(_own_index)
    elif kind == 'thought_write' and args:
        raw=importlib.import_module('ai.inner_memory_index')._entry_id(args[0])
        emit('persistence','monologue','thought_written',evidence='persisted',entity_id=entity('inner',raw))
    elif kind == 'quests' and isinstance(result,dict):
        for key, item in result.items():
            eid=entity('quest',key)
            _remember_version(eid,getattr(item,'text',''))
            emit('retrieval','wants','quest',evidence='retrieval',entity_id=eid)
            candidates=_own_candidates.get()
            if candidates is not None:
                short=importlib.import_module('core.agent.own_life')._short(getattr(item,'text',''))
                candidates.setdefault(('want',short),[]).append(eid)
    elif kind == 'quest_plant' and isinstance(result,tuple) and result[0] is not None:
        emit('persistence','wants','plant',evidence='persisted',entity_id=entity('quest',_entry_id(result[0])))
    elif kind == 'quest_change':
        if result:
            raw = args[0] if args else kwargs.get('quest_id')
            emit('feedback','wants','quest_update',evidence='persisted',entity_id=entity('quest',raw))
    elif kind == 'slice' and isinstance(result,dict):
        shown = kwargs.get('shown') or []
        ids = tuple(entity('stake',_entry_id(s)) for s in shown)
        _shown.set(ids)
        for name in ('relationship_posture','emotional_arc','stakes','tonight','own_life'):
            emit('retrieval', {'stakes':'stakes','own_life':'own_life','tonight':'tonight','relationship_posture':'relationship','emotional_arc':'emotional_memory'}[name], 'first_look_slice',evidence='retrieval',details={'has_result':bool(result.get(name))})
    elif kind == 'look_prompt':
        mapped_tags={}
        # The renderer may clip the slate. Count only tags actually present
        # in the returned prompt, never every candidate gathered upstream.
        if isinstance(result,str) and kwargs.get('owner') and not kwargs.get('in_room'):
            limit=importlib.import_module('core.agent.first_look').STAKES_CHARS
            slate=str(kwargs.get('stakes') or '').strip()[:limit]
            for i,eid in enumerate(_shown.get(),1):
                if re.search(r'(?m)^- \[s' + str(i) + r'\] ',slate) and f'[s{i}]' in result:
                    mapped_tags[f's{i}']=eid
                    emit('prompt','stakes','first_look_inclusion',evidence='prompt_inclusion',entity_id=eid)
        for name in ('relationship_posture','emotional_arc','tonight','own_life'):
            if kwargs.get(name): emit('prompt',name,'first_look_inclusion',evidence='prompt_inclusion')
        if kwargs.get('owner') and kwargs.get('own_life'):
            for tag,eid in _own_index.get(_trace.get() or '',{}).items():
                if re.search(r'(?m)^- \['+re.escape(tag)+r'\] ',str(kwargs['own_life'])) and f'[{tag}]' in result:
                    mapped_tags[tag]=eid
                    emit('prompt','own_life','first_look_inclusion',evidence='prompt_inclusion',entity_id=eid)
        _prompt_tags.set(mapped_tags)
    elif kind == 'look' and result is not None:
        tag = str(getattr(result,'my_own_from','') or '')
        # Only link a tag actually included in this call's prompt. A model
        # may invent a tag, or the corresponding candidate may be clipped.
        eid=_prompt_tags.get().get(tag,'')
        emit('decision','first_look','model_selection',evidence='model_decision',entity_id=eid,details={'selected':bool(getattr(result,'my_own',''))})
    elif kind == 'memory':
        for item in result or ():
            raw = _entry_id(item)
            if raw:
                eid=entity('inner',raw); _remember_version(eid,getattr(item,'text_content',''))
                emit('retrieval','inner_memory','hit',evidence='retrieval',entity_id=eid)
    elif kind == 'queryrank':
        alternate=_rank_cf.get()
        if alternate is not None:
            m=importlib.import_module('ai.inner_memory_index')
            candidates=[dataclasses.replace(x,relevance_score=score) for x,score in alternate]
            baseline=m._dedupe_hits(candidates)[:int(kwargs.get('max_results',3))]
            actual_ids=[x.entry_id for x in (result or [])]
            baseline_ids=[x.entry_id for x in baseline]
            emit('prioritization','present_moment','selected_recall_counterfactual',evidence='deterministic_counterfactual',entity_id=_moment_index.get(_trace.get() or '',''),changed=actual_ids!=baseline_ids,count=len(actual_ids),details={'rank_changed':actual_ids!=baseline_ids,'set_changed':set(actual_ids)!=set(baseline_ids),'output_count':len(baseline_ids)})
            if actual_ids != baseline_ids:
                for raw in dict.fromkeys(actual_ids+baseline_ids):
                    a=actual_ids.index(raw)+1 if raw in actual_ids else 0
                    b=baseline_ids.index(raw)+1 if raw in baseline_ids else 0
                    if a!=b: emit('prioritization','present_moment','selected_rank_delta',evidence='deterministic_counterfactual',entity_id=entity('inner',raw),changed=True,details={'actual_rank':a,'baseline_rank':b})
    elif kind == 'facts':
        for item in result or ():
            fact = getattr(item,'fact',{})
            if isinstance(fact,dict) and fact.get('id') is not None:
                emit('retrieval','facts','weighted_fact',evidence='retrieval',entity_id=entity('fact',fact['id']))
    elif kind == 'recall' and result is not None:
        for field, organ in [('held_stakes_text','stakes'),('relationship_posture','relationship'),('emotional_arc_text','emotional_memory'),('present_moment_text','present_moment'),('night_cycle_reflection','night_cycle'),('monologue_digest','monologue'),('own_work_trace','workbench')]:
            emit('retrieval',organ,'recall_band',evidence='retrieval',details={'has_result':bool(getattr(result,field,None))})
    elif kind == 'reply_prompt' and result:
        text = '\n'.join(x for x in result if isinstance(x,str)) if isinstance(result,tuple) else ''
        ctx=args[0] if args else kwargs.get('ctx')
        memory_text=str(getattr(ctx,'memory_text','') or '')
        if memory_text and memory_text in text:
            with _lock: versions=dict(_versions.get(_trace.get() or '',{}))
            for line in memory_text.splitlines():
                if not line.startswith('- '): continue
                digest=entity('version',line[2:].strip())
                matches=[eid for eid,version in versions.items() if eid.startswith('inner:') and version==digest]
                if len(matches)==1: emit('prompt','inner_memory','exact_text_in_reply_prompt',evidence='correlation',entity_id=matches[0])
        for heading, organ in [('## What\'s on your mind','stakes'),('## Your present moment','present_moment'),('## Your own recent work','workbench'),('## Last night I reflected','night_cycle'),('## My story','narrative'),('## How they\'ve been lately','emotional_memory')]:
            if heading in text:
                eid=''
                if organ=='present_moment':
                    band=str(getattr(ctx,'present_moment_text','') or '')
                    if band and band in text and '\n' in band:
                        eid=entity('moment',band.split('\n',1)[1].strip())
                emit('prompt',organ,'reply_inclusion',evidence='prompt_inclusion',entity_id=eid)
    elif kind == 'reply' and result is not None:
        emit('behavior','conversation','reply_generated',evidence='model_behavior',details={'responded':bool(getattr(result,'responded',False)),'quiet':bool(getattr(result,'quiet',False)),'has_text':bool(getattr(result,'response_text','')),'has_error':bool(getattr(result,'error',None))})
    elif kind == 'reflection':
        emit('feedback','reflection','applied' if result else 'no_update',evidence='persisted' if result else 'execution',details={'success':bool(result)})
    elif kind == 'release' and isinstance(result,dict):
        extra=kwargs.get('extra') or {}
        if isinstance(extra,dict):
            for key,ns in (('want_id','quest'),('view_id','stake'),('regret_id','stake')):
                if extra.get(key): emit('behavior','arbiter','delivery_requested',evidence='delivery_requested',entity_id=entity(ns,extra[key]))
    elif kind == 'arbiter' and isinstance(result,dict):
        disposition = result.get('disposition','')
        allowed = {'sent','stale_dropped','stale_check_failed','do_recorded_only','held_no_framing','held_no_channel','held_conversation','held_quiet_window','held_learned_quiet','held_care_first_look','held_stale_wait','ceiling_blocked','error_closed','held_surprise_budget'}
        if disposition not in allowed: disposition = 'other' if disposition else 'no_impulse'
        emit('behavior' if disposition=='sent' else 'decision','arbiter','release_verdict',evidence='delivery_requested' if disposition=='sent' else 'decision',outcome=disposition)


def _observe_score(result,args,kwargs):
    """Reconstruct the no-lean ranking from actual scored hits; no I/O or model call."""
    lean = kwargs.get('lean')
    if lean not in ('warm','heavy'): return
    m = importlib.import_module('ai.inner_memory_index')
    factor = m._recall_bias_boost()
    congruent = m._WARM_TAGS if lean == 'warm' else m._HEAVY_TAGS
    threshold = float(kwargs['min_relevance'])
    alternate=[]
    for x in result:
        score=float(x.relevance_score) / (factor if str(x.sentiment_tag or '') in congruent else 1.0)
        if score >= threshold: alternate.append((x,score))
    alternate.sort(key=lambda x:(x[1],x[0].timestamp),reverse=True)
    _rank_cf.set(alternate)
    actual=[x.entry_id for x in result]; other=[x[0].entry_id for x in alternate]
    emit('prioritization','present_moment','recall_lean_counterfactual',evidence='deterministic_counterfactual',entity_id=_moment_index.get(_trace.get() or '',''),changed=actual!=other,count=len(actual),details={'rank_changed':actual!=other,'set_changed':set(actual)!=set(other),'input_count':len(args[0]) if args else 0,'output_count':len(other)})


def _start_session(args, kwargs, root, background):
    if not (root or (background and not _trace.get())): return None
    try:
        turn=args[0] if args else kwargs.get('turn')
        metadata=getattr(turn,'metadata',None)
        channel=getattr(metadata,'channel_id',None) or getattr(metadata,'surface_id',None)
        surface=getattr(turn,'surface',None)
        if channel and surface: return _session.set(entity('conversation',str(surface)+':'+str(channel)))
    except Exception:
        pass
    return None


def _wrap(module, attr, organ, stage, kind='', root=False, background=False):
    target = importlib.import_module(module)
    owner=target
    parts=attr.split('.')
    for part in parts[:-1]: owner=getattr(owner,part)
    name=parts[-1]; original=getattr(owner,name)
    if getattr(original,'_cognitive_wrapped',False): return
    async_fn=inspect.iscoroutinefunction(original)
    def begin():
        tid=None
        if root or (background and not _trace.get()): tid=_trace.set(uuid.uuid4().hex)
        sid=uuid.uuid4().hex; par=_parent.get() or ''
        emit(stage,organ,'start',event_id=sid,parent=par)
        token=_parent.set(sid)
        return tid,token,sid,time.monotonic()
    def finish(state, outcome):
        tid,token,sid,start=state
        _parent.reset(token)
        emit(stage,organ,'finish',parent=sid,outcome=outcome,duration_ms=int((time.monotonic()-start)*1000))
        if tid is not None:
            with _lock:
                _own_index.pop(_trace.get(),None)
                _versions.pop(_trace.get(),None)
                _moment_index.pop(_trace.get(),None)
            _trace.reset(tid)
    if async_fn:
        @functools.wraps(original)
        async def wrapper(*args,**kwargs):
            session_token=_start_session(args,kwargs,root,background)
            state=begin()
            rank_token=_rank_cf.set(None) if kind=='queryrank' else None
            try:
                result=await original(*args,**kwargs)
                _safe(_observe,kind,result,args,kwargs)
                if rank_token is not None: _rank_cf.reset(rank_token)
                finish(state,'completed')
                if session_token is not None: _session.reset(session_token)
                return result
            except BaseException:
                if rank_token is not None: _rank_cf.reset(rank_token)
                finish(state,'interrupted')
                if session_token is not None: _session.reset(session_token)
                raise
    else:
        @functools.wraps(original)
        def wrapper(*args,**kwargs):
            session_token=_start_session(args,kwargs,root,background)
            state=begin()
            own_token=_own_candidates.set({}) if kind=='own_items' else None
            try:
                result=original(*args,**kwargs)
                _safe(_observe,kind,result,args,kwargs)
                if kind=='score': _safe(_observe_score,result,args,kwargs)
                if own_token is not None: _own_candidates.reset(own_token)
                finish(state,'completed')
                if session_token is not None: _session.reset(session_token)
                return result
            except BaseException:
                if own_token is not None: _own_candidates.reset(own_token)
                finish(state,'interrupted')
                if session_token is not None: _session.reset(session_token)
                raise
    wrapper._cognitive_wrapped=True
    setattr(owner,name,wrapper)
    _alias_pairs.append((original,wrapper))


def _rebind_aliases():
    # Modules can import a hook by value before instrumentation is installed.
    # Replace only exact function aliases in this checkout's namespaces.
    replacements={id(old):(old,new) for old,new in _alias_pairs}
    for module_name,module in list(sys.modules.items()):
        if not module_name.startswith(('ai.', 'core.', 'bots.')) or module is None or module_name==__name__: continue
        for key,value in list(vars(module).items()):
            pair=replacements.get(id(value))
            if pair is not None and value is pair[0]: setattr(module,key,pair[1])


def install() -> bool:
    """Install once and only when explicitly enabled."""
    global _installed
    if not enabled(): return False
    if _installed: return True
    trace_dir()
    specs=[
      ('core.agent.dispatch','dispatch_turn','turn','execution','',True,False),
      ('core.agent.runner','run_turn','conversation','execution','reply',False,True),
      ('core.agent.builtin_tools.web_search','WebSearchTool.execute','web_search','execution','',False,False),
      ('ai.web_search','ExaSearchClient.search','web_search','retrieval','web_search_result',False,True),
      ('core.turn.recall','build_turn_context','recall','retrieval','recall',False,False),
      ('core.turn.reflect','apply_reflection','reflection','feedback','reflection',False,False),
      ('core.turn.reflect','_record_stake_said','stakes','feedback','stake_check',False,False),
      ('core.memory_store','MemoryStore.store_user_memory','facts','persistence','fact_write',False,False),
      ('core.memory_store','MemoryStore.note_today_fact','facts','persistence','today_fact',False,False),
      ('ai.relationship_momentum','update_from_turn','relationship','feedback','',False,False),
      ('core.agent.first_look','gather_slice','first_look','retrieval','slice',False,False),
      ('core.agent.own_life','gather_items','own_life','prioritization','own_items',False,False),
      ('core.agent.first_look','build_prompt','first_look','prompt','look_prompt',False,False),
      ('core.agent.first_look','take','first_look','decision','look',False,False),
      ('core.agent.system_prompt','build_system_prompt_parts','reply_prompt','prompt','reply_prompt',False,False),
      ('ai.stake_ledger','open_stakes','stakes','retrieval','stakes',False,False),
      ('ai.stake_ledger','apply_ops','stakes','persistence','stake_write',False,False),
      ('ai.stake_ledger','mark_stake_said','stakes','feedback','stake_feedback',False,False),
      ('ai.stake_ledger','mark_view_voiced','stakes','feedback','stake_feedback',False,False),
      ('ai.stake_ledger','mark_voiced','stakes','feedback','stake_feedback',False,False),
      ('ai.fear_store','plant','fears','creation','fear_plant',False,False),
      ('ai.fear_store','held_fears','fears','retrieval','fears',False,False),
      ('ai.inner_monologue_store','write_entry','monologue','persistence','thought_write',False,False),
      ('ai.present_moment_store','write_moment','present_moment','persistence','moment_write',False,False),
      ('ai.present_moment_store','read_current','present_moment','retrieval','moment_read',False,False),
      ('ai.daydream_store','write_entry','daydream','persistence','daydream_write',False,False),
      ('ai.daydream_store','read_recent','daydream','retrieval','daydreams',False,False),
      ('ai.quest_store','load_quests','wants','retrieval','quests',False,False),
      ('ai.quest_store','plant','wants','creation','quest_plant',False,False),
      ('ai.quest_store','set_status','wants','feedback','quest_change',False,False),
      ('ai.quest_store','add_progress','wants','feedback','quest_change',False,False),
      ('ai.inner_memory_index','query','inner_memory','retrieval','memory',False,False),
      ('ai.inner_memory_index','_query_inner','inner_memory','retrieval','queryrank',False,False),
      ('ai.inner_memory_index','_score_rows','inner_memory','prioritization','score',False,False),
      ('ai.fact_retrieval','get_decay_weighted_facts','facts','retrieval','facts',False,False),
      ('ai.arbiter','consider_release','arbiter','decision','arbiter',False,True),
      ('ai.arbiter','_release_outbound','arbiter','behavior','release',False,False),
      ('ai.present_moment','PresentMomentEngine.run_cycle_once','present_moment','execution','',False,True),
      ('ai.present_moment','_parse_impulse','present_moment','decision','impulse',False,False),
      ('ai.proactive_candidate_source','get_eligible_candidate','proactive','prioritization','candidate',False,True),
      ('ai.proactive_policy','evaluate_outreach_timing','proactive','decision','timing',False,True),
      ('ai.inner_monologue','InnerMonologueEngine._run_tick','monologue','execution','',False,True),
      ('ai.daydream','run_daydream_tick','daydream','execution','',False,True),
      ('ai.night_cycle','run_night_cycle','night_cycle','execution','',False,True),
      ('ai.night_cycle_store','write_daily_meaning','night_cycle','persistence','night_write',False,False),
      ('ai.night_cycle_store','read_latest_daily_meaning','night_cycle','retrieval','night_read',False,False),
      ('ai.stake_review','run_nightly_stake_review','stake_review','execution','',False,True),
      ('ai.consolidation','run_nightly_consolidation','consolidation','execution','',False,True),
      ('ai.narrative_self','run_weekly_ritual','narrative','execution','',False,True),
      ('ai.workbench','WorkbenchEngine.run_session_once','workbench','execution','',False,True),
      ('ai.stake_appraiser','appraise_exchange','stake_appraiser','creation','',False,True),
      ('ai.memory_reach_search','search','memory_reach','retrieval','',False,False),
    ]
    for spec in specs:
        try: _wrap(*spec)
        except (ImportError,AttributeError): logger.warning('cognitive trace: unavailable hook %s.%s',spec[0],spec[1])
    _rebind_aliases()
    _installed=True
    try: prune(int(os.environ.get('PALLI_COGNITIVE_TRACE_DAYS','14')))
    except (OSError,ValueError): pass
    emit('system','tracing','installed')
    return True
