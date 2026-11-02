"""Source-only cross-FB trigger -> arrival-gated state-entry candidates.

Discover a protocol from the FB DEFINITION, then bind EACH call instance:
  latched marker and command are co-set; a destination input is false at launch
  and true when the marker is reset. Follow that bound input into explicit MOVE
  guards in the receiver. No station numbers, model answers, FB names or step
  values are encoded. This is a structural protocol association, not a claim that
  software triggers cause physical arrival, or that CT timestamps are identical.
"""
from __future__ import annotations
from collections import defaultdict, Counter
from pathlib import Path
import argparse, json, re, hashlib
from lxml import etree
from successor_graph import (WireBody, WireError, tag, text, own_expr, pins, pin,
    connections, owner, binding, sha256)
from boundary_logic import (Expr, TRUE, FALSE, parse, combine, integer, symbol,
    comparisons, compatible, BoundarySyntaxError, IDENT)
from boundary_normalizer import write_csv
from source_format import detect_source_format

FIELDS=['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数']
LINK_FIELDS=['relation_id','fb_type','instance','call_scope','trigger','command','arrival',
             'receiver_scope','source_states','target_label','move_id','source_line',
             'arrival_required','frontier','proof_level','caveat']


def uid(prefix, value):
    return prefix+hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16]


def required_signals(x: Expr) -> set[tuple[str,bool]]:
    """Sufficient syntactic implication, AND union / OR intersection, not co-occurrence."""
    if x.kind=='signal':return {(x.args[0].casefold(),True)}
    if x.kind=='not' and x.args[0].kind=='signal':return {(x.args[0].args[0].casefold(),False)}
    if x.kind=='and':return set().union(*(required_signals(a) for a in x.args))
    if x.kind=='or':
        sets=[required_signals(a) for a in x.args if a!=FALSE]
        return set.intersection(*sets) if sets else set()
    return set()


def all_signals(x):
    if x.kind=='signal':return {x.args[0]}
    return set().union(*(all_signals(a) for a in x.args if isinstance(a,Expr)))


class ProtocolWire(WireBody):
    """Pass a primitive MOVE ENO only for dependency/protocol tracing.

    ENO's numeric-transfer success is NOT a physical or time-equivalence proof.
    No unknown FB output is interpreted as its EN.
    """
    def resolve(self,ref,port=''):
        n=self.nodes.get(ref)
        if n is not None and tag(n)=='block' and n.get('typeName','').upper()=='MOVE' and port.upper()=='ENO':
            key=(ref,port.upper())
            if key in self.stack:raise WireError('control_cycle:'+ref)
            self.stack.add(key);self.seen.add(ref)
            try:return self.cp(pin(n,'inputVariables','EN'))
            finally:self.stack.remove(key)
        return super().resolve(ref,port)


def interface(pou):
    out={}
    for section in pou:
        if tag(section)!='interface':continue
        for group in section:
            for v in group:
                if tag(v)!='variable':continue
                typ=next((n for c in v if tag(c)=='type' for n in c),None)
                out[v.get('name','').casefold()]={
                    'name':v.get('name',''),'direction':tag(group),
                    'bool':typ is not None and tag(typ).upper()=='BOOL',
                    'documentation':' '.join(text(c) for c in v if tag(c)=='documentation')}
    return out


def protocol_models(pou):
    """Match structural latch/command/arrival lifecycle, not spelling conventions."""
    params=interface(pou)
    output={k for k,v in params.items() if v['bool'] and v['direction'] in ('inOutVars','outputVars')}
    inputs={k for k,v in params.items() if v['bool'] and v['direction']=='inputVars'}
    result=[];issues=[]
    for body in pou.iter():
        if tag(body) not in ('LD','FBD'):continue
        w=ProtocolWire(body);sets=defaultdict(list);resets=defaultdict(list)
        for n in body:
            if tag(n)!='coil' or n.get('negated','false').lower()=='true':continue
            v=own_expr(n).casefold()
            if v not in output:continue
            mode=n.get('storage','').lower()
            if mode not in ('set','reset'):continue
            try:g=w.cp(n)
            except (WireError,BoundarySyntaxError):continue
            info={'node_id':n.get('localId'),'line':n.sourceline,'guard':g,
                  'upstream':tuple(connections(next((c for c in n if tag(c)=='connectionPointIn'),None)))}
            (sets if mode=='set' else resets)[v].append(info)
        for marker,launches in sets.items():
            for launch in launches:
                req=required_signals(launch['guard'])
                # A latch that requires its own false value describes a new transaction.
                if (marker,False) not in req:continue
                peers=[(v,s) for v,vs in sets.items() for s in vs if v!=marker and s['upstream']==launch['upstream']]
                if not peers:continue
                for clear in resets.get(marker,[]):
                    completed=required_signals(clear['guard'])
                    arrivals=sorted(v for v in inputs if (v,False) in req and (v,True) in completed)
                    for arrival in arrivals:
                        commands=sorted({params[v]['name'] for v,_ in peers})
                        result.append({'fb_type':pou.get('name'),
                            'marker_formal':params[marker]['name'],'arrival_formal':params[arrival]['name'],
                            'command_formals':commands,'interface':params,
                            'launch_node':launch['node_id'],'reset_node':clear['node_id'],
                            'launch_line':launch['line'],'reset_line':clear['line'],
                            'launch_guard':launch['guard'].text(),'reset_guard':clear['guard'].text(),
                            'evidence':'co_set_command_marker_and_destination_reset_lifecycle'})
    return result,issues


def bindings(node,wire):
    """Named parameters stay instance-local; repeated/ambiguous bindings fail closed."""
    vals={};bad=set()
    for section in ('inputVariables','inOutVariables','outputVariables'):
        for p in pins(node,section):
            name=p.get('formalParameter','').casefold()
            if not name or name in ('en','eno'):continue
            try:value=wire.value(p)
            except (WireError,BoundarySyntaxError):continue
            if not value:continue
            value=symbol(value)
            if name in vals and vals[name]!=value:bad.add(name)
            else:vals[name]=value
    for name in bad:vals.pop(name,None)
    return vals,bad


def bound_expr(code,mapping):
    # Match a formal symbol/root without changing unrelated longer names.
    rx=re.compile(IDENT,re.U)
    def sub(m):
        v=symbol(m.group());root=re.split(r'[.\[]',v,maxsplit=1)[0]
        dest=mapping.get(root.casefold())
        return dest+v[len(root):] if dest else v
    return rx.sub(sub,code)


def signal_key(v,scope,pou):
    return binding(v,scope,pou),symbol(v).casefold()


def collect(source):
    source=Path(source);fmt=detect_source_format(source)
    base={'schema':'plc.cross_fb_transport.v1','source_sha256':sha256(source),'no_gt_access':True,
          'adapter':fmt,'protocol_models':[],'instances':[],'links':[],'candidates':[],'events':[], 'reviews':[]}
    if fmt!='plcopen':
        base['summary']={'supported':False,'protocol_models':0,'bound_instances':0,'generated_pairs':0,
                         'reason':'Cross-FB adapter currently accepts PLCopen LD/FBD only; original extraction untouched.'}
        return base
    root=etree.parse(str(source),etree.XMLParser(huge_tree=True,resolve_entities=False,no_network=True)).getroot()
    model_map=defaultdict(list)
    for pou in root.iter():
        if tag(pou)=='pou' and pou.get('pouType','').lower()=='functionblock':
            models,issues=protocol_models(pou)
            model_map[pou.get('name','').casefold()].extend(models);base['protocol_models'].extend(models)
            base['reviews'].extend(issues)
    # Skip template bodies when matching concrete receivers/calls. Parameters in
    # an uninstantiated FB must never collide with global or another FB's locals.
    bodies=[]
    for body in root.iter():
        if tag(body) not in ('LD','FBD'):continue
        scope,pou=owner(body)
        if pou is not None and pou.get('pouType','').lower()=='functionblock':continue
        bodies.append((body,scope,pou))
    arrivals=defaultdict(list)
    for body,scope,pou in bodies:
        w=ProtocolWire(body)
        for call in body:
            if tag(call)!='block':continue
            for model in model_map.get(call.get('typeName','').casefold(),[]):
                mp,bad=bindings(call,w)
                needed=[model['marker_formal'],model['arrival_formal']]+model['command_formals']
                if any(k.casefold() not in mp for k in needed):
                    base['reviews'].append({'scope':scope,'node':call.get('localId'),'reason':'missing_or_ambiguous_instance_binding'});continue
                trigger=mp[model['marker_formal'].casefold()];arrival=mp[model['arrival_formal'].casefold()]
                commands=[mp[k.casefold()] for k in model['command_formals']]
                if any(not re.fullmatch(IDENT,v) for v in [trigger,arrival]+commands):continue
                if trigger.casefold()==arrival.casefold():continue
                try:enabled=w.cp(pin(call,'inputVariables','EN'),TRUE)
                except (WireError,BoundarySyntaxError) as ex:
                    base['reviews'].append({'scope':scope,'node':call.get('localId'),'reason':'call_enable_unresolved:'+str(ex)});continue
                if enabled==FALSE:continue
                inst={'id':uid('I',[scope,call.get('localId'),trigger,arrival]),
                      'fb_type':model['fb_type'],'instance':call.get('instanceName',''),
                      'call_scope':scope,'call_node':call.get('localId'),'call_line':call.sourceline,
                      'trigger':trigger,'arrival':arrival,'commands':commands,
                      'arrival_key':signal_key(arrival,scope,pou),
                      'call_enable':enabled.text(),'parameter_bindings':mp,
                      'definition':model,'launch_guard_bound':bound_expr(model['launch_guard'],mp),
                      'arrival_reset_guard_bound':bound_expr(model['reset_guard'],mp)}
                arrivals[tuple(inst['arrival_key'])].append(inst);base['instances'].append(inst)
    matches=[];move_count=0
    for body,scope,pou in bodies:
        # Cheap skip based on actual arrival operands, not project naming.
        operands={signal_key(own_expr(n),scope,pou) for n in body if tag(n)=='contact' and own_expr(n)}
        interest=operands & set(arrivals)
        if not interest:continue
        w=ProtocolWire(body);comment=''
        for move in body:
            if tag(move)=='comment':comment=text(move);continue
            if tag(move)!='block' or move.get('typeName','').upper()!='MOVE':continue
            move_count+=1
            try:
                dp=[p for p in pins(move,'inputVariables') if p.get('formalParameter','').upper()!='EN']
                if len(dp)!=1:raise WireError('ambiguous_MOVE_data_pin')
                target=integer(w.value(dp[0]));targets=w.targets(move)
                if target is None:raise WireError('dynamic_MOVE_value')
                w.seen=set();g=w.cp(pin(move,'inputVariables','EN'));required=required_signals(g)
            except (WireError,BoundarySyntaxError) as ex:
                base['reviews'].append({'scope':scope,'node':move.get('localId'),'line':move.sourceline,
                                         'reason':'receiver_guard_unresolved:'+str(ex)});continue
            if g==FALSE:continue
            all_used={signal_key(v,scope,pou) for v in all_signals(g)} & interest
            for key in all_used:
                if (key[1],True) not in required:
                    base['reviews'].append({'scope':scope,'node':move.get('localId'),'line':move.sourceline,
                        'arrival':key[1],'reason':'arrival_not_required_on_all_OR_branches'});continue
                for var in targets:
                    states=sorted({a.args[1] for a in comparisons(g) if a.kind=='eq' and a.args[0].casefold()==var.casefold()
                                   and compatible(g,var,a.args[1]) and a.args[1]!=target})
                    if not states:continue
                    for inst in arrivals[key]:
                        # same call controller + same source receiver always kept as
                        # a protocol, not fabricated from overlapping station numbers.
                        matches.append({'inst':inst,'scope':scope,'variable':var,'from':states,'to':target,
                            'move_id':move.get('localId'),'line':move.sourceline,'guard':g.text(),
                            'guard_nodes':sorted(w.seen),'comment':comment,
                            'binding_scope':binding(var,scope,pou)})
    groups=defaultdict(list)
    for m in matches:
        groups[(m['inst']['id'],m['scope'],m['binding_scope'],m['variable'].casefold())].append(m)
    seen=set();suppressed=0
    for ms in groups.values():
        destinations={m['to'] for m in ms}
        for m in ms:
            frontier=[s for s in m['from'] if s not in destinations]
            if not frontier:
                suppressed+=1;base['reviews'].append({'scope':m['scope'],'node':m['move_id'],'line':m['line'],
                    'arrival':m['inst']['arrival'],'reason':'later_arrival_gated_phase_not_first_receiver_entry'});continue
            inst=m['inst'];start=inst['trigger'];end=f'{m["variable"]}={m["to"]}'
            k=(inst['id'],m['scope'],start.casefold(),end.casefold(),m['move_id'])
            if k in seen:continue
            seen.add(k);rid=uid('X',k)
            caveat=('源码功能块置位/复位、实参绑定及到达门控关联；不是运行轨迹或时间等价证明。'
                    '终点含接收侧其他互锁/数据条件；实际动子ID、采样沿、异常取消仍需核验。')
            link={'relation_id':rid,'fb_type':inst['fb_type'],'instance':inst['instance'],
                'call_scope':inst['call_scope'],'trigger':start,'command':';'.join(inst['commands']),
                'arrival':inst['arrival'],'receiver_scope':m['scope'],'source_states':frontier,
                'target_label':end,'move_id':m['move_id'],'source_line':m['line'],
                'arrival_required':True,'frontier':True,'proof_level':'source_cross_fb_binding_and_gate',
                'caveat':caveat,'receiver_guard':m['guard'],'receiver_guard_nodes':m['guard_nodes'],
                'call_evidence':inst,'comment':m['comment'],
                'timing_semantics':'departure_marker_rise_to_receiving_phase_entry',
                'arrival_equals_end':False,'physical_causality_proved':False,'requires_cycle_pairing':True}
            base['links'].append(link)
            row={'构成要素':m['scope'],'动作':'输送/交接触发至接收阶段',
                '动作描述':'触发 '+start+'；到达信号 '+inst['arrival']+' 参与接收条件；'+m['comment'],
                '开始标签':start,'结束标签':end,'状态':'cross_fb_transport_candidate',
                '证据POU':inst['call_scope']+';'+m['scope'],'证据节点':rid,
                '原始条件':'完整发车/接收门控见 cross_fb_transport_evidence.json#'+rid,
                '控制器':m['variable'],'分支数':len({x['to'] for x in ms if any(s not in destinations for s in x['from'])})}
            base['candidates'].append(row)
            base['events'].append(['plcopen',m['scope'],rid,start,'cross_fb_transport',
                'trigger='+start+';command='+'|'.join(inst['commands']),inst['arrival'],end,
                'departure_arrival_receiver_binding','source_cross_fb_binding_and_gate',m['comment']])
    base['summary']={'supported':True,'protocol_models':len(base['protocol_models']),
        'bound_instances':len(base['instances']),'receiver_moves_checked':move_count,
        'generated_pairs':len(base['candidates']),'unique_pairs':len({(r['开始标签'],r['结束标签']) for r in base['candidates']}),
        'later_receiver_phases_not_emitted':suppressed,'reviews':len(base['reviews']),
        'no_gt_access':True,'scope':'PLCopen LD/FBD declared FB lifecycle and local receiving guards only',
        'physical_completion_or_global_path_proof':False}
    return base


def write_discovery(source,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);m=collect(source)
    (out/'cross_fb_transport_evidence.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'cross_fb_transport_summary.json').write_text(json.dumps(m['summary'],ensure_ascii=False,indent=2),encoding='utf-8')
    write_csv(out/'cross_fb_transport_candidates.csv',m['candidates'],FIELDS)
    write_csv(out/'cross_fb_transport_links.csv',[
        {k:(';'.join(map(str,r[k])) if isinstance(r.get(k),list) else r.get(k,'')) for k in LINK_FIELDS} for r in m['links']],LINK_FIELDS)
    write_csv(out/'cross_fb_transport_review.csv',m['reviews'],['scope','node','line','arrival','reason'])
    return m

if __name__=='__main__':
    p=argparse.ArgumentParser(description='Source-only cross-function-block transport intervals; no GT input.')
    p.add_argument('source');p.add_argument('-o','--out',required=True)
    a=p.parse_args();print(json.dumps(write_discovery(a.source,a.out)['summary'],ensure_ascii=False,indent=2))
