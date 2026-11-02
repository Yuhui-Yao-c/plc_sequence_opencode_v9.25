"""Evidence-based postprocessor: deduplication, intra-state event candidates, and gap auditing.
Never constructs a transition unless it was observed in the parser evidence.
"""
from __future__ import annotations
import collections,csv,json,re
from pathlib import Path

EQ = re.compile(r'(?P<var>[\w.\[\],\u0080-\uffff]+)\s*=\s*(?P<num>-?\d+)|(?P<rev>-?\d+)\s*=\s*(?P<rvar>[\w.\[\],\u0080-\uffff]+)')
TOKEN = re.compile(r'[a-zA-Z_\u0080-\uffff][\w.\[\],\u0080-\uffff]*')
STATE_HINTS=('step','state','phase','seq','步序')
CMD_HINTS=('absEx','execute','mov','start','act','cmd','clamp','open','close','vacuum','trigger','go','run','吸','夹','伸','缩')
FEEDBACK_HINTS=('posok','done','complete','finish','reach','clamping_signal','open_signal','feedback','switch','ready','到位','完成')
EXCLUDE=('autorun','suspend','safe','safety','alarm','reset','init','manual','error','timeout','flag','check','estop','enabled')
FIELDS=['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数']

def comparisons(s):
    return [(m.group('var') or m.group('rvar'),int(m.group('num') or m.group('rev'))) for m in EQ.finditer(s or '')]

def unique(rows):
    seen=set();out=[]
    for r in rows:
        key=(r['开始标签'],r['结束标签'],r['动作'],r['控制器'])
        if key in seen:continue
        seen.add(key);out.append(r)
    return out

def extract_guards(transitions,events):
    """A phase exit condition may contain a completion sensor. Keep it as candidate,
    not an asserted equivalence to successful completion."""
    out=[]
    for t in transitions:
        v=t['controller']; state=t['from']; target=t['to']
        if state is None:continue
        g=t['guard'] or ''
        # only use single-state guards with an actual (possibly composite) non-step operand
        if (v,state) not in comparisons(g):continue
        other=[name for name in TOKEN.findall(g) if name.lower() not in ('and','or','not','true','false') and name!=v and not name.isdigit()]
        other=[x for x in other if x.lower() not in ('unresolved_block','st_unresolved') and not any(z in x.lower() for z in EXCLUDE)]
        if not other:continue
        # retain exact original logic as evidence; a sensor may be combined with arbitrary interlocks
        label=f'{v}={state}'
        if (' OR ' in g or 'UNRESOLVED' in g or t['uncertain']):status='review_composite_guard'
        else:status='guard_observed_not_completion_proven'
        for x in sorted(set(other)):
            x_lower=x.lower()
            if not any(w.lower() in x_lower for w in CMD_HINTS+FEEDBACK_HINTS):continue
            out.append(dict(zip(FIELDS,[t['pou'].split('/')[0],x,'转换条件中的信号候选',label,label+' AND '+x,status,t['pou'],t['evidence'],g,v,1])))
    return out

def collect_state_gaps(rows,step_controllers):
    keys=set()
    for r in rows:
        for v,n in comparisons(r['开始标签']):keys.add((v,n))
    gaps=[]
    for controller,info in step_controllers.items():
        for state in info['states']:
            if (controller,state) not in keys:
                gaps.append({'控制器':controller,'状态':state,'问题':'no_start_candidate'})
    return gaps

def postprocess(folder):
    folder=Path(folder)
    evidence=json.loads((folder/'evidence.json').read_text(encoding='utf-8'))
    raw=evidence['candidates'];trs=evidence['transitions']
    original_unique=unique(raw)
    derived=extract_guards(trs,[])
    combined=unique(original_unique+derived)
    with (folder/'sequence_candidates_optimized.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(combined)
    gaps=collect_state_gaps(combined,evidence['step_controllers'])
    audit={'raw_rows':len(raw),'raw_unique':len(original_unique),'derived_event_candidates':len(unique(derived)),
           'optimized_rows':len(combined),'missing_state_start_candidates':gaps,
           'limitations':['End signals embedded in transition guards are proposals only; they are not proven completion events.',
                          'Unknown external signals cannot be reconstructed.',
                          'Intra-state command transitions and cross-controller handshakes require separate code-evidence review.']}
    (folder/'candidate_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    return audit
