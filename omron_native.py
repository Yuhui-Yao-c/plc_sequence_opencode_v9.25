"""Sysmac Studio IEC 61131-10 export adapter (source-only, no GT dependency).

Native objects use xsi:type and connectionPointOutId/refConnectionPointOutId,
NOT PLCopen localId. Port IDs are indexed independently in each Rung.
Full local guards and partial connectivity evidence are kept separate.
Unknown FB outputs, edges, dynamic/indirect writes are explicitly reported.
This parser does not prove PLC scan timing, library side effects or completion.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from pathlib import Path
import csv, hashlib, json, re
from lxml import etree
from boundary_logic import (Expr, TRUE, FALSE, parse, combine, negate, integer,
    symbol, comparisons, compatible, substitute, BoundarySyntaxError, IDENT)

XSI = '{http://www.w3.org/2001/XMLSchema-instance}type'
CAND_FIELDS = ['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数']
EVENT_FIELDS = ['source_format','source_unit','network','guard','command','arguments','feedback','next_state','relation','evidence','comment']
WAIT = re.compile(r'等待|\bwait(?:ing)?\b|\bawait',re.I)
HEAD_STEP = re.compile(r'^\s*(?:步\s*)?(\d+)\s*(?:[-—:：>]|步)',re.I)
COMPARE = re.compile(rf'({IDENT})\s*(=|<>|!=)\s*((?:(?:U?D?INT|LINT|SINT|USINT|ULINT)\s*#\s*)?[-+]?\d+)(?![\w#])',re.I)
ASSIGN = re.compile(rf'({IDENT})\s*:=\s*([^;]+);',re.I)
GENERIC = {'MOVE','=','EQ','==','NE','<>','!=','>','<','>=','<=','AND','OR','NOT','ADD','SUB','MUL','DIV','+','-','*','/'}

def local(e):
    return etree.QName(e).localname if isinstance(e.tag,str) else ''

def kind(e):
    return e.get(XSI,'').split(':')[-1] or local(e)

def direct(e, name):
    return [c for c in e if local(c)==name] if e is not None else []

def content(e):
    return ''.join(e.itertext()).strip() if e is not None else ''

def params(block, section):
    return [v for s in direct(block,section) for v in s
            if local(v) in ('InputVariable','OutputVariable','InOutVariable')]

def port_name(p):
    return p.get('parameterName','')

def refs(e):
    return [n.get('refConnectionPointOutId') for n in e.iter()
            if local(n)=='Connection' and n.get('refConnectionPointOutId')] if e is not None else []

def pin(block,name,section='InputVariables'):
    return next((v for v in params(block,section) if port_name(v).upper()==name.upper()),None)

def scope_of(e):
    names=[];pou=None
    for p in e.iterancestors():
        if local(p) in ('Program','FunctionBlock','Function'):
            pou=p;names.append(p.get('name','<anonymous>'));break
        if local(p)=='BodyContent':names.append(p.get('name',p.get('evaluationOrder','body')))
    return '::'.join(reversed(names)) or '<anonymous>',pou

def binding_of(var,scope,pou):
    root=re.split(r'[.\[]',var)[0].casefold()
    if pou is not None:
        for group in pou:
            typ=local(group)
            if typ in ('LocalVars','InputVars','OutputVars','InOutVars','TempVars'):
                if any(v.get('name','').casefold()==root for v in group if local(v)=='Variable'):
                    return scope.split('::')[0]
            if typ=='ExternalVars':
                if any(v.get('name','').casefold()==root for v in group if local(v)=='Variable'):
                    return ''
    # Qualified and array globals are common; unresolved simple scalars stay scoped.
    return scope.split('::')[0] if '.' not in var and '[' not in var else ''

class NativeWireError(ValueError):
    pass

class NativeRung:
    def __init__(self,rung,symbolic=False):
        from source_expressions import SourceConditions
        self.symbolic=symbolic;self.source_conditions=SourceConditions();self.temporal_conditions=[]
        self.rung=rung;self.nodes=list(rung);self.ports={};self.duplicates=set()
        self.node_ids={id(n):f'node:{i};line:{n.sourceline or 0}' for i,n in enumerate(self.nodes)}
        self.memo={};self.active=set();self.visited=set()
        for n in self.nodes:
            for cp in n.iter():
                if local(cp)!='ConnectionPointOut' or not cp.get('connectionPointOutId'):continue
                key=cp.get('connectionPointOutId');par=cp.getparent()
                name=port_name(par) if local(par) in ('OutputVariable','InOutVariable') else ''
                if key in self.ports:self.duplicates.add(key)
                self.ports[key]=(n,name,cp)
    def node_id(self,n):return self.node_ids.get(id(n),f'line:{n.sourceline or 0}')
    def input_cp(self,n):return next(iter(direct(n,'ConnectionPointIn')),None)
    def input_gate(self,n):
        if kind(n)=='Block':
            p=pin(n,'EN')
            if p is None:p=pin(n,'_EN')
            if p is None and n.get('typeName','').upper() in ('TON','TOF','TP'):
                p=pin(n,'IN')
            if p is None:raise NativeWireError('missing_EN_port:'+self.node_id(n))
            return p
        return self.input_cp(n)
    def value(self,p):
        rs=refs(p)
        if len(rs)!=1:raise NativeWireError('data_input_requires_one_source')
        if rs[0] in self.duplicates:raise NativeWireError('duplicate_output_port:'+rs[0])
        obj=self.ports.get(rs[0])
        if obj is None:raise NativeWireError('missing_output_port:'+rs[0])
        n,_,_=obj
        if kind(n)!='DataSource':raise NativeWireError('computed_data_value:'+rs[0])
        return n.get('identifier','').strip()
    def comparison(self,n):
        name=n.get('typeName','').upper().lstrip('@')
        if kind(n)!='Block' or name not in ('=','EQ','==','NE','<>','!='):return None
        data=[p for p in params(n,'InputVariables') if port_name(p).upper() not in ('EN','_EN')]
        if len(data)!=2:return None
        try:a,b=map(self.value,data)
        except NativeWireError:return None
        av,bv=integer(a),integer(b)
        if av is None and bv is not None and re.fullmatch(IDENT,a):v,k=symbol(a),bv
        elif bv is None and av is not None and re.fullmatch(IDENT,b):v,k=symbol(b),av
        else:return None
        return Expr('eq' if name in ('=','EQ','==') else 'ne',(v,k))
    def gate(self,p):
        rs=refs(p)
        if not rs:raise NativeWireError('unconnected_control_input')
        e=combine('or',*(self.resolve(r) for r in rs))
        if p is not None and p.get('negated','').lower()=='true':e=negate(e)
        if len(e.text())>30000:raise NativeWireError('guard_expression_budget')
        return e
    def resolve(self,ref):
        if ref in self.memo:return self.memo[ref]
        if ref in self.duplicates:raise NativeWireError('duplicate_output_port:'+ref)
        if ref in self.active:raise NativeWireError('control_cycle:'+ref)
        if ref not in self.ports:raise NativeWireError('missing_output_port:'+ref)
        n,port,cp=self.ports[ref];self.visited.add(self.node_id(n));self.active.add(ref)
        try:
            k=kind(n)
            if k=='LeftPowerRail':e=TRUE
            elif k=='DataSource':
                value=n.get('identifier','').strip()
                e=TRUE if value=='1' else FALSE if value=='0' else parse(value)
            elif k in ('Contact','Coil'):
                if n.get('edge','none').lower() not in ('','none'):
                    raise NativeWireError('edge_sensitive_'+k.lower()+':'+ref)
                e=self.gate(self.input_cp(n))
                if k=='Contact':
                    a=parse(n.get('operand',''))
                    if n.get('negated','').lower()=='true':a=negate(a)
                    e=combine('and',e,a)
            elif k=='Block':
                name=n.get('typeName','').upper()
                if name.startswith('@'):raise NativeWireError('edge_instruction:'+name)
                gate=pin(n,'EN')
                if gate is None:gate=pin(n,'_EN')
                en=self.gate(gate) if gate is not None else TRUE
                cmp=self.comparison(n)
                if cmp is not None:
                    if port.upper()=='ENO':raise NativeWireError('comparison_ENO_not_predicate')
                    e=combine('and',en,cmp)
                elif name in ('AND','OR','NOT'):
                    data=[p for p in params(n,'InputVariables') if port_name(p).upper() not in ('EN','_EN')]
                    if not data or (name=='NOT' and len(data)!=1):raise NativeWireError('boolean_arity')
                    es=[self.gate(p) for p in data]
                    e=combine('and',en,negate(es[0]) if name=='NOT' else combine(name.lower(),*es))
                elif name=='MOVE' and port.upper()=='ENO':
                    e=en
                elif self.symbolic and name in ('<','>','<=','>=','=','<>'):
                    if port.upper()=='ENO':raise NativeWireError('comparison_ENO_not_predicate')
                    data=[p for p in params(n,'InputVariables') if port_name(p).upper() not in ('EN','_EN')]
                    if len(data)!=2:raise NativeWireError('relational_arity')
                    e=combine('and',en,self.source_conditions.relational(self.value(data[0]),name,self.value(data[1])))
                elif self.symbolic and name=='TON' and port.upper()=='Q':
                    # Preserve temporal output, NEVER replace TON.Q by its input.
                    inst=n.get('instanceName','').strip()
                    if not inst or not re.fullmatch(IDENT,inst):raise NativeWireError('timer_instance_required')
                    inp=pin(n,'IN');ingate=self.gate(inp)
                    try:pt=self.value(pin(n,'PT'))
                    except NativeWireError:pt='<unresolved>'
                    self.temporal_conditions.append({'instance':inst,'output':inst+'.Q','kind':'TON',
                        'input':ingate.text(),'preset':pt,'source_line':n.sourceline,
                        'proof':'temporal_dependency_only_not_elapsed_time_proof'})
                    e=combine('and',ingate,Expr('signal',(inst+'.Q',)))
                else:
                    raise NativeWireError('opaque_control_output:'+name+':'+port)
            else:raise NativeWireError('unsupported_control_object:'+k)
            if cp.get('negated','').lower()=='true' or (local(cp.getparent()) in ('OutputVariable','InOutVariable') and cp.getparent().get('negated','').lower()=='true'):e=negate(e)
            self.memo[ref]=e;return e
        finally:self.active.remove(ref)
    def symbolic_gate(self,p):
        other=NativeRung(self.rung,symbolic=True)
        guard=other.gate(p)
        return guard,other.source_conditions.definitions,other.temporal_conditions

    def ancestors(self,p):
        """Connectivity only: do not treat this traversal as a complete guard."""
        todo=list(refs(p));seen=set();objects={};issues=[]
        while todo:
            ref=todo.pop()
            if ref in seen:continue
            seen.add(ref)
            if ref in self.duplicates:issues.append('duplicate_output_port:'+ref)
            item=self.ports.get(ref)
            if item is None:issues.append('missing_output_port:'+ref);continue
            n,port,_=item;objects[self.node_id(n)]=n
            k=kind(n)
            if k=='Block':
                # Follow the controlling EN, not every unrelated data argument.
                try:todo.extend(refs(self.input_gate(n)))
                except NativeWireError:
                    # For partial connectivity, a vendor block's first graphical
                    # boolean input may not be named EN. Follow only a control
                    # producer (never all data inputs); this is not a guard proof.
                    ps=params(n,'InputVariables')
                    if ps:
                        for rr in refs(ps[0]):
                            producer=self.ports.get(rr)
                            if producer and kind(producer[0]) not in ('DataSource','DataSink'):
                                todo.append(rr)
                if n.get('typeName','').upper() in ('AND','OR','NOT'):
                    for p2 in params(n,'InputVariables'):todo.extend(refs(p2))
            elif k not in ('DataSource','LeftPowerRail'):todo.extend(refs(self.input_cp(n)))
        return list(objects.values()),issues
    def targets(self,block):
        owned={ref for ref,(n,name,_) in self.ports.items()
               if n is block and name.upper() in ('OUT','')}
        values=[]
        for n in self.nodes:
            if kind(n)!='DataSink':continue
            rs=refs(self.input_cp(n))
            if len(rs)==1 and rs[0] in owned:
                v=n.get('identifier','').strip()
                if re.fullmatch(IDENT,v):values.append(symbol(v))
        return list(dict.fromkeys(values))
    def args(self,n):
        result=[]
        for p in params(n,'InputVariables')+params(n,'InOutVariables'):
            if port_name(p).upper() in ('EN','_EN'):continue
            try:v=self.value(p)
            except NativeWireError:v='<computed-or-unconnected>'
            result.append(f'{port_name(p)}={v}')
        return ';'.join(result)


def collect_native(source):
    """One streaming pass; every rung is indexed without using a name filter."""
    from successor_graph import STParser, WireError, st_tokens
    source=Path(source);states=[];transitions=[];blockers=[];partial=[];events=[];stages=[]
    command_records=[];diagnostics=[];warnings=[];inline_slices=[];source_predicates={};temporal_dependencies=[];stats=Counter();types=Counter();seen_states=set()
    unsupported=[];discovery=[];units=set();state_bindings={};opaque_targets=[]
    def observe(v,num,role,scope,network,node,line,comment,bscope='',operator='='):
        v=symbol(v);key=(v.casefold(),num,role,scope,str(network),node)
        if key in seen_states:return
        seen_states.add(key);state_bindings[(v,scope)]=bscope
        states.append({'variable':v,'value':num,'kind':role,'operator':operator,'entry':scope,
            'network':str(network),'node':node,'source_line':line,'comment':comment,'binding_scope':bscope})
    def block(v,scope,network,node,reason,bscope='',froms=None):
        for value in froms or [None]:blockers.append({'scope':scope,'binding_scope':bscope,'variable':v,
            'from':value,'reason':reason,'body':str(network),'node':node})
    def add_stage(v,src,dest,guard,complete,scope,network,node,line,comment,bscope,path,evidence):
        if src==dest:return
        label=f'{v}={src}';end=f'{v}={dest}';wait=bool(WAIT.search(comment))
        row={'构成要素':scope,'动作':'工艺等待阶段候选' if wait else '源码状态阶段候选',
            '动作描述':comment,'开始标签':label,'结束标签':end,
            '状态':('native_wired_wait_transition' if wait else 'native_wired_transition') if complete else 'native_connected_transition_unverified',
            '证据POU':scope,'证据节点':node,'原始条件':guard,'控制器':v,'分支数':0,
            '源码行号':line,'网络序号':str(network),'完整门控':guard if complete else '',
            '证据等级':evidence,'binding_scope':bscope,'工艺类型建议':'工艺等待' if wait else '待语义核验'}
        stages.append(row)
        events.append(['omron_iec61131_10',scope,str(network),label,'state_wait' if wait else 'state_transition',
            f'{v}:={dest}',guard,end,'local_state_stage' if complete else 'connected_state_stage_unverified',evidence,comment])
    def emit(v,dest,g,scope,network,node,line,comment,bscope,path,evidence='omron_native_wired_guard'):
        vals={a.args[1] for a in comparisons(g) if a.args[0].casefold()==v.casefold()}
        froms=sorted({a.args[1] for a in comparisons(g) if a.kind=='eq' and a.args[0].casefold()==v.casefold() and compatible(g,v,a.args[1])})
        if dest is None:block(v,scope,network,node,'dynamic_state_assignment',bscope,froms);return
        if compatible(g,v,max(vals)+1 if vals else 0):block(v,scope,network,node,'writer_without_constrained_positive_source',bscope)
        for src in froms:
            tid=f'T{len(transitions)+1:07d}'
            transitions.append({'id':tid,'scope':scope,'binding_scope':bscope,'body':f'{network};line:{line};{node}',
                'variable':v,'from':src,'to':dest,'guard':g.text(),'guard_complete':evidence!='omron_native_symbolic_guard','evidence':evidence,
                'path':path,'timing_proof':'local_port_guard_only; scan visibility and physical completion unproved'})
            add_stage(v,src,dest,g.text(),evidence!='omron_native_symbolic_guard',scope,network,tid,line,comment,bscope,path,evidence)
    def parse_st(code,scope,network,node,line,comment,pou,external=TRUE,external_issue=None,
                 connected_seeds=(),symbolic_external=False,temporal=(),strict=True):
        from source_expressions import SourceConditions,scalar_reads,valid_scalar
        clean=' '.join(st_tokens(code));writes=set();conditions=SourceConditions()
        effect={'scope':scope,'network':str(network),'node':node,'source_line':line,'comment':comment,
            'code':code,'external_guard':external.text(),'external_issue':external_issue or '',
            'flow_complete':False,'symbolic_external':symbolic_external,'temporal':list(temporal),
            'assignments':[],'state_seeds':[a.text() for a in connected_seeds]}
        for m in COMPARE.finditer(clean):
            n=integer(m.group(3));v=symbol(m.group(1))
            if n is not None:observe(v,n,'st_comparison',scope,network,node,line,comment,binding_of(v,scope,pou),m.group(2))
        for m in ASSIGN.finditer(clean):
            v=symbol(m.group(1));n=integer(m.group(2));writes.add(v)
            if n is not None:observe(v,n,'st_assignment',scope,network,node,line,comment,binding_of(v,scope,pou))
        try:
            parser=STParser(code,condition_parser=conditions.parse,strict=strict);parser.seq()
            for v,dest,g,pos,rhs in parser.writes:
                if dest is None and not valid_scalar(rhs):
                    raise WireError('ST opaque/non-scalar assignment RHS may have effects')
            effect['flow_complete']=True
            if parser.skipped:
                effect['skipped_statements']=parser.skipped
                stats['skipped_st_statements']+=len(parser.skipped)
                warnings.append(f'{scope}/{network}/line:{line}: {len(parser.skipped)} ST statement(s) skipped (call/return/opaque); their state effects are unmodeled')
            effect['assignments']=[{'target':v,'value':rhs,'integer':dest,'guard':g.text(),
                'reads':scalar_reads(rhs),'token':pos} for v,dest,g,pos,rhs in parser.writes]
            effect['assignments'] += [{'target':x['variable'],'value':x['value'],'integer':None,
                'guard':x['guard'],'reads':[],'token':x['token']} for x in parser.actions]
            source_predicates.update(conditions.definitions)
            for v,dest,g,pos,rhs in parser.writes:
                for atom in comparisons(g):
                    if atom.kind=='eq':observe(atom.args[0],atom.args[1],'st_comparison',scope,network,node,line,comment,binding_of(atom.args[0],scope,pou))
            if external_issue:
                # Recover a literal target with its true structured path, but do not
                # claim an unknown timer/FB input is TRUE. No flattening of IF branches.
                for v,dest,g,pos,rhs in parser.writes:
                    if dest is None:continue
                    for seed in connected_seeds:
                        if seed.args[0].casefold()!=v.casefold() or not compatible(g,*seed.args):continue
                        add_stage(v,seed.args[1],dest,'UNRESOLVED: '+external_issue+'; ST guard: '+g.text(),
                            False,scope,network,node,line,comment,binding_of(v,scope,pou),[node],
                            'native_inline_connected_assignment')
                raise NativeWireError(external_issue)
            evidence='omron_native_symbolic_guard' if symbolic_external or conditions.definitions else 'omron_native_st_guard'
            for v,dest,g,pos,rhs in parser.writes:
                full=combine('and',external,g)
                emit(v,dest,full,scope,network,node,line,comment,binding_of(v,scope,pou),[node,f'token:{pos}'],evidence)
                if dest is not None and evidence=='omron_native_symbolic_guard':
                    srcs=[a.args[1] for a in comparisons(full) if a.kind=='eq' and a.args[0].casefold()==v.casefold()]
                    block(v,scope,network,node,'symbolic_or_temporal_guard_not_equivalence_proof',binding_of(v,scope,pou),srcs)
            for x in parser.actions:
                guard=combine('and',external,parse(x['guard'])).text()
                events.append(['omron_iec61131_10',scope,str(network),guard,'ST_assignment',f"{x['variable']}:={x['value']}",'','','structured_ST_observation','local_control',comment])
        except (WireError,NativeWireError,BoundarySyntaxError) as ex:
            stats['unsupported_st_bodies']+=1;effect['issue']=str(ex)
            for v in sorted(writes):block(v,scope,network,node,'unsupported_ST:'+str(ex),binding_of(v,scope,pou))
            if writes:warnings.append(f'{scope}/{network}/line:{line}: {ex}')
        inline_slices.append(effect)
    # ST nested inside InlineST is consumed with its rung exactly once.
    rung_consumed=set();body_audit={'parsed_st':{},'rung_path':{},'skipped_unknown':{}}
    for _,e in etree.iterparse(str(source),events=('end',),tag=('{*}Rung','{*}BodyContent'),
                               huge_tree=True,resolve_entities=False,no_network=True):
        if local(e)=='BodyContent':
            # Horizontal-defense: recognize ST bodies by STRUCTURE (a direct
            # <ST> child), not only by enumerating vendor xsi:type names.
            # Name enumeration missed smcext:StSection (Sysmac's own ST section
            # type) and would miss every future vendor spelling; a <ST> child
            # is what IEC 61131-10 itself guarantees for a text body. Direct
            # children only: LdSection/LD keep their Rungs out of this path.
            # Every BodyContent is classified in body_audit — unread vendor
            # types are reported, never silently dropped.
            st_kids=direct(e,'ST');ksig=e.get(XSI,'') or local(e)
            if kind(e) in ('ST','STBody','StructuredText','StSection') or st_kids:
                scope,pou=scope_of(e);scope+='::'+e.get('name','ST')
                code='\n'.join(content(x) for x in st_kids) if st_kids else content(e)
                # Section bodies are large state machines: unprovable statements
                # (calls, texts, expressions) degrade to skips. InlineST inside
                # rungs keeps strict=True (zero-side-effect tail premise).
                parse_st(code,scope,e.get('evaluationOrder','ST'),'ST',e.sourceline or 0,'',pou,strict=False)
                stats['structured_text_bodies']+=1
                body_audit['parsed_st'][ksig]=body_audit['parsed_st'].get(ksig,0)+1
            elif id(e) in rung_consumed:
                body_audit['rung_path'][ksig]=body_audit['rung_path'].get(ksig,0)+1
            else:
                body_audit['skipped_unknown'][ksig]=body_audit['skipped_unknown'].get(ksig,0)+1
            rung_consumed.discard(id(e))
            e.clear()
            while e.getprevious() is not None and local(e.getprevious())=='BodyContent':del e.getparent()[e.getparent().index(e)-1]
            continue
        rung=e;scope,pou=scope_of(rung);units.add(scope);network=rung.get('evaluationOrder',str(stats['rungs']))
        parent=rung.getparent()
        if parent is not None and local(parent)=='BodyContent':rung_consumed.add(id(parent))
        line=rung.sourceline or 0;stats['rungs']+=1
        comment='\n'.join(content(c) for c in rung if kind(c)=='Comment')
        wire=NativeRung(rung);cmps={};compared=defaultdict(set);commands=[]
        for n in wire.nodes:
            types[kind(n)]+=1;node=wire.node_id(n)
            cmp=wire.comparison(n)
            if cmp:
                cmps[node]=cmp;v,num=cmp.args
                observe(v,num,'comparison',scope,network,node,n.sourceline or line,comment,binding_of(v,scope,pou),'=' if cmp.kind=='eq' else '<>')
                if cmp.kind=='eq':compared[v].add(num)
            if kind(n)=='Block':stats['blocks']+=1
            if kind(n)=='Block' and n.get('typeName','').upper().lstrip('@')=='MOVE':stats['moves']+=1
        for n in wire.nodes:
            k=kind(n);node=wire.node_id(n);name=n.get('typeName','');upper=name.upper().lstrip('@')
            if k=='Block' and upper=='MOVE':
                targets=wire.targets(n)
                if not targets:stats['moves_without_explicit_sink']+=1
                try:val=integer(wire.value(pin(n,'IN')))
                except NativeWireError:val=None
                for v in targets:
                    bs=binding_of(v,scope,pou)
                    if val is not None:observe(v,val,'assignment',scope,network,node,n.sourceline or line,comment,bs)
                    upstream=[];g=None
                    try:
                        control=wire.input_gate(n);upstream,issues=wire.ancestors(control)
                        if name.startswith('@'):raise NativeWireError('edge_MOVE_instruction')
                        g=wire.gate(control)
                        depended={a.args[0].casefold() for a in comparisons(g)}|{v.casefold()}
                        for prev in upstream:
                            if prev.get('typeName','').upper()=='MOVE' and any(t.casefold() in depended for t in wire.targets(prev)):
                                raise NativeWireError('upstream_state_write_requires_scan_order')
                        emit(v,val,g,scope,network,node,n.sourceline or line,comment,bs,[wire.node_id(x) for x in upstream]+[node])
                        stats['move_guards_complete']+=1
                    except (NativeWireError,BoundarySyntaxError) as ex:
                        stats['move_guards_incomplete']+=1;reason=str(ex)
                        observed=[cmps[wire.node_id(x)] for x in upstream if wire.node_id(x) in cmps]
                        srcs=sorted({a.args[1] for a in observed if a.kind=='eq' and a.args[0].casefold()==v.casefold()})
                        block(v,scope,network,node,reason,bs)
                        partial.append({'scope':scope,'network':str(network),'source_line':n.sourceline or line,
                            'variable':v,'target':val,'possible_sources':srcs,'reason':reason,'node':node,'comment':comment})
                        if val is not None:
                            for src in srcs:add_stage(v,src,val,'UNRESOLVED: '+reason,False,scope,network,node,n.sourceline or line,comment,bs,[], 'connected_ports_only')
                        try:
                            sg,defs,tm=wire.symbolic_gate(control)
                            depended={a.args[0].casefold() for a in comparisons(sg)}|{v.casefold()}
                            if any(prev.get('typeName','').upper()=='MOVE' and any(t.casefold() in depended for t in wire.targets(prev)) for prev in upstream):
                                raise NativeWireError('symbolic_path_upstream_state_write')
                            source_predicates.update(defs);temporal_dependencies.extend(tm)
                            emit(v,val,sg,scope,network,node,n.sourceline or line,comment,bs,
                                 [wire.node_id(x) for x in upstream]+[node],'omron_native_symbolic_guard')
                        except (NativeWireError,BoundarySyntaxError):pass
            elif k=='InlineST':
                # The vendor wrapper smcext:ST may contain standard ST; only leaf ST text is read.
                leaves=[x for x in n.iter() if local(x)=='ST' and not any(local(y)=='ST' for y in x)]
                code='\n'.join(content(x) for x in leaves)
                if code:
                    stats['inline_st_bodies']+=1;external=TRUE;issue=None
                    symbolic=False;tm=[]
                    control=wire.input_cp(n);up,_=wire.ancestors(control)
                    seeds=[cmps[wire.node_id(x)] for x in up if wire.node_id(x) in cmps and cmps[wire.node_id(x)].kind=='eq']
                    try:external=wire.gate(control)
                    except (NativeWireError,BoundarySyntaxError) as ex:
                        issue=str(ex)
                        try:
                            external,defs,tm=wire.symbolic_gate(control)
                            source_predicates.update(defs);temporal_dependencies.extend(tm)
                            symbolic=True;issue=None
                        except (NativeWireError,BoundarySyntaxError):pass
                    parse_st(code,scope,network,node,n.sourceline or line,comment,pou,external,issue,seeds,symbolic,tm)
            elif k=='Coil' or (k=='Block' and upper not in GENERIC):
                commands.append(n)
                if k=='Block':
                    owned={r for r,(obj,p,cp) in wire.ports.items() if obj is n and p.upper()!='ENO'}
                    for sink in wire.nodes:
                        if kind(sink)=='DataSink' and any(r in owned for r in refs(wire.input_cp(sink))):
                            target=sink.get('identifier','')
                            if re.fullmatch(IDENT,target):opaque_targets.append((symbol(target),scope,network,node,pou.get('name','') if pou is not None else ''))
        # State extraction is independent of whether a call name sounds like motion.
        for n in commands:
            node=wire.node_id(n);up=[];g=None;issue=''
            try:
                control=wire.input_gate(n);up,missing=wire.ancestors(control);g=wire.gate(control)
            except (NativeWireError,BoundarySyntaxError) as ex:issue=str(ex)
            seeds=[cmps[wire.node_id(x)] for x in up if wire.node_id(x) in cmps and cmps[wire.node_id(x)].kind=='eq']
            if g is not None:seeds=[a for a in comparisons(g) if a.kind=='eq' and compatible(g,*a.args)]
            # Raw full gate is evidence, but use each anchored state separately for batching.
            anchors=list(dict.fromkeys(a.text() for a in seeds))
            if kind(n)=='Coil':
                cmd='coil_'+n.get('latch','write');args=n.get('operand','')
            else:cmd=n.get('typeName','');args=wire.args(n)
            for anchor in anchors or ['']:
                ev=['omron_iec61131_10',scope,str(network),anchor,cmd,args,'','',
                    'wired_command_gate' if g is not None else 'connected_command_gate_unverified',
                    'omron_native_wired_guard' if g is not None else 'connected_ports_only',comment]
                events.append(ev)
                command_records.append({'scope':scope,'network':str(network),'source_line':n.sourceline or line,
                    'node':node,'start':anchor,'command':cmd,'arguments':args,
                    'guard':g.text() if g is not None else '', 'guard_complete':g is not None,
                    'issue':issue,'path':[wire.node_id(x) for x in up],'comment':comment})
        hd=HEAD_STEP.match(comment)
        if hd:
            for v,vals in compared.items():
                if int(hd[1]) not in vals:diagnostics.append({'entry':scope,'network':str(network),'source_line':line,
                    'variable':v,'comment_number':int(hd[1]),'actual_comparison_values':';'.join(map(str,sorted(vals))),
                    'comment':comment,'diagnosis':'注释数字与比较值不同；不据此创造或修改状态'})
        discovery.append({'scope':scope,'network':str(network),'source_line':line,'objects':len(wire.nodes),
            'comparison_seeds':len(cmps),'output_ports':len(wire.ports),'duplicate_ports':';'.join(sorted(wire.duplicates)),
            'commands':len(commands),'comment':comment})
        rung.clear()
        while rung.getprevious() is not None:del rung.getparent()[0]
    compvars={s['variable'] for s in states if s['kind'] in ('comparison','st_comparison')}
    for target,scope,network,node,pou_name in opaque_targets:
        for v in sorted(compvars):
            if v.casefold()==target.casefold() or v.casefold().startswith(target.casefold()+'['):
                block(v,scope,network,node,'opaque_function_state_writer',state_bindings.get((v,scope),''))
    if not stats['rungs'] and not stats['structured_text_bodies']:
        raise ValueError('Native IEC61131-10 XML detected but no supported Rung/ST body was read; cannot return success.')
    if body_audit['skipped_unknown']:
        warnings.append('BodyContent types never read (no <ST> child, no Rung consumed): '+
            json.dumps(body_audit['skipped_unknown'],ensure_ascii=False,sort_keys=True))
    stats.update({'state_observations':len(states),'transitions':len(transitions),'partial_transitions':len(partial),
        'events':len(events),'stage_records':len(stages),'units':len(units)})
    h=hashlib.sha256()
    with source.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):h.update(chunk)
    digest=h.hexdigest()
    return {'schema':'plc.state_transitions.v1','adapter':'omron_iec61131_10','source_sha256':digest,
        'source_name':source.name,'source_size':source.stat().st_size,'transitions':transitions,'blockers':blockers,
        'observations':[],'warnings':warnings,'state_observations':states,'comment_state_diagnostics':diagnostics,
        'stage_records':stages,'stage_events':[], 'native_events':events,'native_commands':command_records,
        'native_partial_transitions':partial,'native_inline_slices':inline_slices,
        'source_predicates':source_predicates,'temporal_dependencies':temporal_dependencies,'native_rung_inventory':discovery,
        'native_parse_audit':{'format':'omron_iec61131_10','counts':dict(stats),'object_types':dict(types),
            'bodycontent_audit':body_audit,
            'health':'parsed_with_explicit_limits','no_pattern_applied_during_parse':True,
            'gt_access':False,'warnings':['Local guards only; unresolved control paths are retained, not certified.']},
        'summary':{'bodies':stats['rungs']+stats['structured_text_bodies'],'transitions':len(transitions),
            'blockers':len(blockers),'state_observations':len(states),'source_stages':len(stages),
            'wait_stages':sum(e[4]=='state_wait' for e in events)},
        'limits':['Native IEC61131-10 Rung/port adapter; no whole-program execution or CT timestamp proof.',
            'Unresolved FB outputs, edge contacts, inline ST and dynamic writers retained with uncertainty.',
            'Comments are descriptive only; no GT or annotation monitor values used as transition evidence.',
            'Port references are scoped per rung; custom extensions outside supported bodies need review.']}


def make_recall(model):
    """Exhaustive observed state exits + connected pairs, no numeric sorting edges."""
    byvar=defaultdict(lambda: {'compares':set(),'writes':set(),'sources':set(),'comments':{}})
    for r in model['state_observations']:
        key=(r['variable'],r.get('binding_scope',''));d=byvar[key]
        d['compares' if r['kind'] in ('comparison','st_comparison') else 'writes'].add(r['value'])
        d['sources'].add(r['entry']);d['comments'].setdefault(r['value'],r['comment'])
    rows=[];controllers={}
    for (v,bs),d in sorted(byvar.items()):
        if not d['compares']:continue
        observed=d['compares']|d['writes'];controllers[v+(' @ '+bs if bs else '')]={'states':sorted(observed),'compared':sorted(d['compares']),'assigned':sorted(d['writes'])}
        for n in sorted(observed):
            a=f'{v}={n}'
            rows.append({'构成要素':v,'动作':'状态退出阶段候选','动作描述':d['comments'].get(n,''),
                '开始标签':a,'结束标签':f'NOT ({a})','状态':'state_exit','证据POU':';'.join(sorted(d['sources'])),
                '证据节点':'native_state_inventory','原始条件':'','控制器':v,'分支数':0})
    for s in model['stage_records']:
        rows.append({k:s.get(k,'') for k in CAND_FIELDS})
    return rows,controllers


def write_native_outputs(model,out):
    from boundary_normalizer import write_csv
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    rows,controllers=make_recall(model)
    write_csv(out/'recall_candidates.csv',rows,CAND_FIELDS)
    with (out/'event_graph.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(EVENT_FIELDS);w.writerows(model['native_events'])
    graph={'events':len(model['native_events']),'command_kinds':len({e[4] for e in model['native_events']}),
        'caveat':'Native port graph: explicit local guards or separately marked incomplete paths; no physical completion proof.'}
    (out/'event_graph_audit.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'source':model['source_name'],'units':model['summary']['bodies'],'controller_count':len(controllers),
        'candidate_count':len(rows),'controllers':controllers,'warning':'State exits and local transitions are candidates, not confirmed physical actions.'}
    (out/'recall_evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'native_parse_audit.json').write_text(json.dumps(model['native_parse_audit'],ensure_ascii=False,indent=2),encoding='utf-8')
    write_csv(out/'native_rung_inventory.csv',model['native_rung_inventory'])
    write_csv(out/'native_command_evidence.csv',[{**r,'path':';'.join(r['path'])} for r in model['native_commands']])
    write_csv(out/'native_unresolved_transitions.csv',[{**r,'possible_sources':';'.join(map(str,r['possible_sources']))} for r in model['native_partial_transitions']])
    (out/'native_inline_slices.json').write_text(json.dumps(model.get('native_inline_slices',[]),ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'source_predicate_definitions.json').write_text(json.dumps({'source_sha256':model['source_sha256'],'predicates':model.get('source_predicates',{}),'temporal_dependencies':model.get('temporal_dependencies',[])},ensure_ascii=False,indent=2),encoding='utf-8')
    return report,graph
