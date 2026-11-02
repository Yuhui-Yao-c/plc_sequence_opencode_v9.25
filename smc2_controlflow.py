"""Conservative SMC2 CLs/VLs local power-flow and state inventory.

A horizontal cell connects (X,Y) to (X+1,Y). A vertical VL segment joins
(X,Y) and (X,Y+1). These relationships reconstruct the exported rung layout;
unknown cells, missing links, duplicate positions and unsupported function
outputs are blockers. No numeric successor guessing. No project identifiers.
This is a local source model, NOT runtime timing or physical-completion proof.
"""
from __future__ import annotations
from collections import defaultdict,Counter
import re
from boundary_logic import (Expr,TRUE,FALSE,parse,combine,negate,integer,symbol,
                            comparisons,compatible,substitute,BoundarySyntaxError,IDENT)
from smc2_source import iter_networks

class GridError(ValueError):pass


def parameters(node,side):
    return {str(p.get('Arg','')).upper():str(p.get('Var','')).strip()
            for p in node.get(side,[]) if p.get('__type')=='PRM'}


def comparison(node):
    if node.get('__type')!='F' or str(node.get('Name','')).upper().lstrip('@') not in ('=','==','EQ','<>','NE','!='):return None
    ps=parameters(node,'In');a=ps.get('IN1','');b=ps.get('IN2','')
    av,bv=integer(a),integer(b)
    if av is None and bv is not None and re.fullmatch(IDENT,a):var,val=symbol(a),bv
    elif bv is None and av is not None and re.fullmatch(IDENT,b):var,val=symbol(b),av
    else:return None
    eq=str(node.get('Name','')).upper().lstrip('@') in ('=','==','EQ')
    return Expr('eq' if eq else 'ne',(var,val))


def source_states(guard,var):
    return sorted({a.args[1] for a in comparisons(guard) if a.kind=='eq'
                   and a.args[0].casefold()==var.casefold() and compatible(guard,var,a.args[1])})


def binding_scope(var,entry):
    # Qualified/array operands are retained as shared symbol names. Unqualified
    # scalars remain local to the entry: no global merging of generic "Step".
    return entry if '.' not in var and '[' not in var else ''


class Grid:
    def __init__(self,net):
        self.net=net;self.nodes=net['CLs'];self.parents={};self.cells={};self.coords={};self.incoming=defaultdict(list)
        self.cache={};self.active=set();self.error=None;self.memo_output={};self.visit=set()
        def point(x,y):
            if not isinstance(x,int) or not isinstance(y,int) or x<0 or y<0:raise GridError('non_integer_or_negative_coordinate')
            return (x,y)
        try:
            for i,n in enumerate(self.nodes):
                xy=point(n.get('X',0),n.get('Y',0))
                if xy in self.cells:raise GridError('duplicate_cell_position:'+str(xy))
                self.cells[xy]=i;self.coords[i]=xy;self.find(xy);self.find((xy[0]+1,xy[1]))
            for v in net.get('VLs',[]):
                x,y=point(v.get('X',0),v.get('Y',0));self.union((x,y),(x,y+1))
            for i,(x,y) in self.coords.items():
                left,right=self.find((x,y)),self.find((x+1,y))
                if left==right:raise GridError('cell_short_circuited:'+str(i))
                self.incoming[right].append(i)
            self.rails={self.find(p) for p in list(self.parents) if p[0]==0}
        except GridError as ex:self.error=str(ex);self.rails=set()
    def find(self,p):
        self.parents.setdefault(p,p)
        if self.parents[p]!=p:self.parents[p]=self.find(self.parents[p])
        return self.parents[p]
    def union(self,a,b):
        a,b=self.find(a),self.find(b)
        if a!=b:self.parents[b]=a
    def at(self,p):
        if self.error:raise GridError(self.error)
        r=self.find(p)
        if r in self.cache:return self.cache[r]
        if r in self.rails:return TRUE
        if r in self.active:raise GridError('powerflow_cycle')
        incoming=self.incoming.get(r,[])
        if not incoming:raise GridError('unconnected_input:'+str(p))
        self.active.add(r)
        try:
            result=combine('or',*(self.output(i) for i in incoming))
            if len(result.text())>24000:raise GridError('guard_expression_limit')
            self.cache[r]=result;return result
        finally:self.active.remove(r)
    def gate(self,i):return self.at(self.coords[i])
    def trace_gate(self,i):
        self.visit=set();self.cache.clear();self.memo_output.clear();self.active.clear()
        return self.gate(i)
    def output(self,i):
        if i in self.memo_output:return self.memo_output[i]
        self.visit.add(i);n=self.nodes[i];kind=n.get('__type');gate=self.gate(i)
        if gate==FALSE:return FALSE
        if kind=='HL':result=gate
        elif kind=='LD':
            if n.get('Up') or n.get('Dwn'):raise GridError('edge_sensitive_contact:'+str(i))
            var=str(n.get('Var','')).strip()
            if var in ('0','1'):expr=TRUE if var=='1' else FALSE
            else:expr=parse(var)
            if n.get('Not'):expr=negate(expr)
            result=combine('and',gate,expr)
        elif kind=='ST':
            if n.get('Up') or n.get('Dwn'):raise GridError('edge_sensitive_coil:'+str(i))
            # Coil writes a value; power flow continues from its input. We do not
            # infer its target variable has become true or pair it with feedback.
            result=gate
        elif kind=='F':
            name=str(n.get('Name','')).upper().lstrip('@')
            cmp=comparison(n)
            if cmp is not None:
                result=combine('and',gate,cmp)
            elif name=='MOVE':result=gate
            else:raise GridError('opaque_function_output:'+name+':'+str(i))
        else:raise GridError('unsupported_powerflow_node:'+str(kind)+':'+str(i))
        self.memo_output[i]=result;return result


WAIT_WORDS=re.compile(r'等待|等待许可|wait|await',re.I)
COMPARE_RE=re.compile(rf'({IDENT})\s*(=|<>|!=)\s*((?:(?:U?D?INT|LINT|SINT|USINT|ULINT)\s*#\s*)?[-+]?\d+)(?![\w#])',re.I)
ASSIGN_RE=re.compile(rf'({IDENT})\s*:=\s*([^;]+);',re.I)
COMMENT_STEP=re.compile(r'^\s*(?:步\s*)?(\d+)\s*(?:[-—:：>]|步)',re.I)


def collect_smc2(source):
    from successor_graph import STParser,WireError,st_tokens
    transitions=[];blockers=[];observations=[];states=[];diagnostics=[];warnings=['SMC2 grid evidence is a local source model, not runtime/CT equivalence.'];meta={};seen_states=set();events=[];stage_rows=[];opaque_writers=[]
    def add_state(var,val,kind,loc,node,comment='',operator='='):
        key=(var.casefold(),val,kind,loc['entry'],loc['network'],str(node))
        if key in seen_states:return
        seen_states.add(key)
        states.append({'variable':var,'value':val,'kind':kind,'operator':operator,'entry':loc['entry'],
                       'network':loc['network'],'node':str(node),'source_line':loc['source_line'],'comment':comment})
    def block(var,loc,node,reason,from_states=None):
        for val in from_states or [None]:
            blockers.append({'scope':loc['entry'],'binding_scope':binding_scope(var,loc['entry']),
                'variable':var,'from':val,'reason':reason,'body':str(loc['network']),'node':str(node)})
    def emit(var,dest,guard,loc,node,evidence,path,comment):
        srcs=source_states(guard,var)
        if dest is None:
            block(var,loc,node,'dynamic_state_assignment',srcs);return []
        vals={a.args[1] for a in comparisons(guard) if a.args[0].casefold()==var.casefold()}
        if compatible(guard,var,max(vals)+1 if vals else 0):
            block(var,loc,node,'writer_without_constrained_positive_source',None)
        emitted=[]
        for value in srcs:
            rel={'id':f'T{len(transitions)+1:07d}','scope':loc['entry'],'binding_scope':binding_scope(var,loc['entry']),
                'body':f"network:{loc['network']};line:{loc['source_line']};node:{node}",
                'variable':var,'from':value,'to':dest,'guard':guard.text(),'guard_complete':True,
                'evidence':evidence,'path':path,'timing_proof':'local_exported_rung_model_only; scan order and external writers unresolved'}
            transitions.append(rel);emitted.append(rel)
            if value!=dest:
                start=f'{var}={value}';end=f'{var}={dest}'
                wait=bool(WAIT_WORDS.search(comment))
                # Wait events do not require an actuator, coil or function call.
                stage_rows.append({'构成要素':var,'动作':'工艺等待阶段候选' if wait else '源码状态阶段候选',
                    '动作描述':comment,'开始标签':start,'结束标签':end,
                    '状态':'smc2_wired_wait_transition' if wait else 'smc2_wired_transition',
                    '证据POU':loc['entry'],'证据节点':rel['id'],'原始条件':guard.text(),'控制器':var,'分支数':0,
                    '源码行号':loc['source_line'],'网络序号':loc['network'],'完整门控':guard.text(),
                    '工艺类型建议':'工艺等待' if wait else '待语义核验'})
                events.append(['smc2',loc['entry'],loc['network'],start,'state_wait' if wait else 'state_transition',
                    f'{var}:={dest}',substitute(guard,var,value).text(),end,'local_state_stage',evidence,comment])
        return emitted
    for loc in iter_networks(source,meta):
        net=loc['net'];comment=str(net.get('CMT','')).strip();nodes=net['CLs'];grid=Grid(net)
        compared=defaultdict(set)
        for i,node in enumerate(nodes):
            cmp=comparison(node)
            if cmp is not None:
                var,val=cmp.args;add_state(var,val,'comparison',loc,node.get('Ix',i),comment,'=' if cmp.kind=='eq' else '<>')
                if cmp.kind=='eq':compared[var].add(val)
            if node.get('__type')=='F' and str(node.get('Name','')).upper().lstrip('@')=='MOVE':
                ins,outs=parameters(node,'In'),parameters(node,'Out');dest=integer(ins.get('IN',''))
                var=outs.get('OUT','')
                if not re.fullmatch(IDENT,var):continue
                var=symbol(var)
                if dest is not None:add_state(var,dest,'assignment',loc,node.get('Ix',i),comment)
                try:
                    guard=grid.trace_gate(i)
                    depended={a.args[0].casefold() for a in comparisons(guard)}|{var.casefold()}
                    for upstream in grid.visit:
                        prev=nodes[upstream]
                        if prev.get('__type')=='F' and str(prev.get('Name','')).upper().lstrip('@')=='MOVE':
                            written=parameters(prev,'Out').get('OUT','').casefold()
                            if written in depended:raise GridError('upstream_state_write_requires_scan_order')
                    emit(var,dest,guard,loc,node.get('Ix',i),'smc2_grid_guard',
                         [f"cell:{nodes[j].get('Ix',j)}@{grid.coords[j]}" for j in sorted(grid.visit)]+[f"MOVE:{node.get('Ix',i)}"],comment)
                except (GridError,BoundarySyntaxError) as ex:
                    block(var,loc,node.get('Ix',i),str(ex))
            elif node.get('__type') in ('F','FB'):
                for target in parameters(node,'Out').values():
                    if re.fullmatch(IDENT,target or ''):
                        opaque_writers.append((symbol(target),{k:v for k,v in loc.items() if k!='net'},node.get('Ix',i)))
            elif node.get('__type')=='IST' and node.get('TXT'):
                code=str(node['TXT']);clean=' '.join(st_tokens(code));vars_=set()
                for m in COMPARE_RE.finditer(clean):
                    v=symbol(m.group(1));n=integer(m.group(3))
                    if n is not None:add_state(v,n,'st_comparison',loc,node.get('Ix',i),comment,m.group(2))
                for m in ASSIGN_RE.finditer(clean):
                    v=symbol(m.group(1));vars_.add(v);n=integer(m.group(2))
                    if n is not None:add_state(v,n,'st_assignment',loc,node.get('Ix',i),comment)
                try:
                    parser=STParser(code);parser.seq();external=grid.trace_gate(i)
                    for v,dest,guard,pos,rhs in parser.writes:
                        for atom in comparisons(guard):
                            if atom.kind=='eq':add_state(atom.args[0],atom.args[1],'st_comparison',loc,node.get('Ix',i),comment)
                        emit(v,dest,combine('and',external,guard),loc,node.get('Ix',i),
                             'smc2_embedded_st_guard',[f"IST:{node.get('Ix',i)}",f'token:{pos}'],comment)
                    observations.extend({**a,'scope':loc['entry'],'source':'SMC2_IST_assignment'} for a in parser.actions)
                except (GridError,WireError,BoundarySyntaxError) as ex:
                    for v in vars_:block(v,loc,node.get('Ix',i),'unsupported_embedded_ST:'+str(ex))
                    if vars_:warnings.append(f"{loc['entry']}:{loc['network']}: {ex}")
        heading=COMMENT_STEP.match(comment)
        if heading:
            number=int(heading.group(1))
            # A comment number is only a diagnostic hint, never a new state.
            for var,vals in compared.items():
                if number not in vals:
                    diagnostics.append({'entry':loc['entry'],'network':loc['network'],'source_line':loc['source_line'],
                        'variable':var,'comment_number':number,'actual_comparison_values':';'.join(map(str,sorted(vals))),
                        'comment':comment,'diagnosis':'注释数字与同网络比较值不同；可能过期或指代其他流程，不自动修正'})
    observed_vars={r['variable'] for r in states if r['kind'] in ('comparison','st_comparison')}
    for target,loc,node in opaque_writers:
        for var in observed_vars:
            if var.casefold()==target.casefold() or var.casefold().startswith(target.casefold()+'['):
                block(var,loc,node,'opaque_function_state_writer')
    # A malformed executable chunk could hide a writer to ANY state controller.
    if meta.get('parse_issues'):
        for var in sorted({r['variable'] for r in states}):
            blockers.append({'scope':'<unparsed>','binding_scope':'','variable':var,'from':None,'reason':'unparsed_JSON_may_hide_writer'})
    return {'transitions':transitions,'blockers':blockers,'observations':observations,'warnings':warnings,
            'state_observations':states,'comment_state_diagnostics':diagnostics,
            'stage_records':stage_rows,'stage_events':events,'smc2_audit':meta,
            'summary':{'bodies':meta['networks'],'transitions':len(transitions),'blockers':len(blockers),
                       'state_observations':len(states),'source_stages':len(stage_rows),
                       'wait_stages':sum(e[4]=='state_wait' for e in events),'json_parse_issues':len(meta['parse_issues'])}}
