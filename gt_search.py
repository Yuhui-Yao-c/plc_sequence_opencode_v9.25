"""Evidence-ranked GT-to-candidate search; no gold-informed candidate generation.
Modes: exact_pair, boolean_exact, conditional_transition, endpoint_only, related, missing.
A conditional transition is NEVER called exact timestamp equivalence.
"""
import argparse,csv,json,re,collections
from pathlib import Path
from benchmark_v6 import truth_rows

EQ=re.compile(r'([\w\u4e00-\u9fff.]+(?:\[[^\]]+\])*)\s*=\s*(?:[A-Za-z]+#)?(-?\d+)',re.I)
SP=re.compile(r'\s+')
def norm(s):
 s=SP.sub('',str(s or '')).replace('（','(').replace('）',')')
 return re.sub(r'(?i)\b(?:INT|DINT|UINT|WORD|SINT)#(?=-?\d)', '',s).lower()
def read_table(p):
 with open(p,encoding='utf-8-sig',newline='') as f:
  first=f.readline();f.seek(0);return list(csv.DictReader(f,delimiter='\t' if '\t' in first else ','))
def atoms(s):return {(v.lower(),int(n)) for v,n in EQ.findall(norm(s))}
def feedbacks(s):
 t=re.sub(EQ,'',norm(s));return set(re.findall(r'[\w\u4e00-\u9fff]+(?:\[[^\]]+\])?(?:\.[\w]+(?:\[[^\]]+\])?)+',t))
def has_same_state(s,e):
 a=atoms(s);b=atoms(e);return bool(a and (a & b))
def canonical_bool(s):
 """Conservative AND/OR canonicalization; no distribution, negation, or temporal inference."""
 s=norm(s)
 # only reorder flat conjunction/disjunction without nested operators
 if s.count('(')!=s.count(')'):return s
 s=re.sub(r'\bnot\(', 'not(',s)
 for op in ('and','or'):
  if op in s and ('or' if op=='and' else 'and') not in s:
   pieces=re.split(r'(?i)\b'+op+r'\b',s)
   if len(pieces)>1:return op.join(sorted(x.strip('()') for x in pieces))
 return s

def load_relations(p):
 if not p:return []
 rows=read_table(p);out=[]
 for r in rows:
  src=(r.get('source_state') or r.get('原始事件') or '').strip()
  dest=(r.get('target_state') or r.get('替代事件') or '').strip()
  variable=r.get('state_variable','').strip()
  if variable and re.fullmatch(r'-?\d+',src):src=f'{variable}={src}'
  if variable and re.fullmatch(r'-?\d+',dest):dest=f'{variable}={dest}'
  guard=(r.get('feedback') or r.get('guard') or r.get('附加条件') or '').strip()
  extras=(r.get('other_guards') or '').strip()
  proof=(r.get('proof_level') or r.get('evidence') or r.get('证据等级') or '').strip()
  if src and dest:out.append(dict(src=norm(src),dst=norm(dest),guard=norm(guard),extras=norm(extras),proof=proof,raw=r))
 return out

_CACHE={}
def prepared(r):
 key=id(r)
 if len(_CACHE)>40000:_CACHE.clear()
 if key not in _CACHE:
  ps,pe=norm(r.get('开始标签')),norm(r.get('结束标签'))
  _CACHE[key]=(ps,pe,atoms(ps),atoms(pe),feedbacks(pe),canonical_bool(ps),canonical_bool(pe))
 return _CACHE[key]
def search_one(start,end,candidates,relations=(),top_k=5):
 sn,en=norm(start),norm(end);results=[]
 for line,r in enumerate(candidates,2):
  ps,pe,asp,aep,fep,cps,cpe=prepared(r)
  if not ps and not pe:continue
  mode=None;score=0;why='';rel=None
  if ps==sn and pe==en:
   mode,score,why='严格起止命中',100,'起止规范化后一致（不证明实际机械动作完成）'
  elif cps==canonical_bool(sn) and cpe==canonical_bool(en):
   mode,score,why='布尔结构命中',96,'只进行了安全的同级AND/OR换序，不推导时序'
  elif ps==sn:
   # relation describes: GT end expression is guard at old state, candidate end is new state
   for e in relations:
    if e['dst']==pe and e['src'] in en and e['proof']=='wired_guard_local' and e['guard'] and all(x in en for x in e['guard'].split('and') if x) and not e.get('extras'):
     mode,score,why,rel='有条件的迁移替代',78,'GT终点与预测终点存在源码记录的状态迁移线索；是否同一采样时刻尚未证明',e
     if 'incomplete' in e['proof'].lower() or 'hypothesis' in e['proof'].lower():score=62
     break
   if not mode:
    mode,score,why='同起点/终点不同',60,'已找到相同起点，但不能用不同结束事件代替GT结束'
  elif pe==en:
   mode,score,why='同终点/起点不同',55,'已找到GT终点，但动作起点不匹配'
  elif pe and (aep&atoms(end)):
   mode,score,why='终点状态原子相关',32,'终点包含部分相同的状态变量和值；不等于完整边界'
  elif sn and (asp&atoms(start)):
   mode,score,why='起点状态原子相关',24,'起点部分相关；不等于完整区间'
  elif pe and (fep&feedbacks(end)):
   mode,score,why='终点反馈信号相关',20,'包含同名反馈信号，但缺少起止事件连接'
  if mode:results.append(dict(类别=mode,得分=score,预测行号=line,预测开始=r.get('开始标签',''),预测结束=r.get('结束标签',''),预测状态=r.get('状态',''),证据单元=r.get('证据POU',''),依据=why,迁移证据=rel['proof'] if rel else ''))
 results.sort(key=lambda x:(-x['得分'],x['预测行号']))
 # prevent cheap state overlaps obscuring strongest hits
 result=results[:top_k]
 best=result[0]['类别'] if result else '没有检索到相关候选'
 return best,result,len(results)

def evaluate(candidates,truth,relations=(),top_k=5):
 """Global one-to-one allocation: one normalized start/end *pair* may credit at most one GT.
 Search hits remain visible for diagnostics but cannot be counted as multiple matches.
 """
 records=[];hits=[];pool=[]
 ix=collections.defaultdict(set)
 for j,r in enumerate(candidates):
  ps,pe,asp,aep,fe,_,_=prepared(r)
  if ps:ix[('s',ps)].add(j)
  if pe:ix[('e',pe)].add(j)
  for a in asp:ix[('sa',a)].add(j)
  for a in aep:ix[('ea',a)].add(j)
 for i,(s,e,desc) in enumerate(truth,1):
  ns,ne=norm(s),norm(e)
  subset=set(ix.get(('s',ns),set()))|set(ix.get(('e',ne),set()))
  for atom in atoms(s):subset.update(ix.get(('sa',atom),set()))
  for atom in atoms(e):subset.update(ix.get(('ea',atom),set()))
  ordered=sorted(subset)
  # Keep more positive alternatives than visible top_k so another pair can be selected on conflict.
  candidates2=[candidates[j] for j in ordered]
  _,found,total=search_one(s,e,candidates2,relations,max(top_k,50))
  for f in found:
   j=ordered[f['预测行号']-2];f['预测行号']=j+2
   f['_pair']=(norm(candidates[j].get('开始标签')),norm(candidates[j].get('结束标签')))
  pool.append(found)
  records.append({'人工编号':i,'动作描述':desc,'GT开始':s,'GT结束':e,'检索结果数':total})
 # First allocate highest-confidence complete-pair matches globally. The same pair is unique
 # even if duplicated in the source file. Sort by evidence score, GT position, candidate row.
 accepted={'严格起止命中','布尔结构命中','有条件的迁移替代'}
 proposals=[]
 for i,options in enumerate(pool):
  for h in options:
   if h['类别'] in accepted:proposals.append((-h['得分'],i,h['预测行号'],h))
 proposals.sort(key=lambda item:(item[0],item[1],item[2]))
 used=set();assigned={}
 for _,i,_,h in proposals:
  if i not in assigned and h['_pair'] not in used:
   assigned[i]=h;used.add(h['_pair'])
 conflicts=0
 for i,(r,found) in enumerate(zip(records,pool)):
  winner=assigned.get(i)
  positive=[h for h in found if h['类别'] in accepted]
  if winner:
   category=winner['类别'];basis=winner['依据'];score=winner['得分'];state='已唯一分配'
  elif positive:
   category='一对一匹配冲突';basis='存在符合条件的预测标签对，但已分配给另一GT；不可重复计为正确';score=0;state='冲突未计分';conflicts+=1
  else:
   category=found[0]['类别'] if found else '没有检索到相关候选'
   basis=found[0]['依据'] if found else '没有足够证据；不能据此判定GT错误'
   score=0;state='相关证据，不计覆盖'
  primary=winner or (found[0] if found else None)
  r.update({'最佳匹配':category,'分配状态':state,'计入覆盖': '是' if winner else '否',
    '最高得分':score,'唯一匹配候选行号':winner['预测行号'] if winner else '',
    '唯一匹配预测开始':winner['预测开始'] if winner else '',
    '唯一匹配预测结束':winner['预测结束'] if winner else '',
    '预测候选行号':';'.join(str(x['预测行号']) for x in found[:top_k]),
    '预测开始（按排序）':'\n'.join(x['预测开始'] for x in found[:top_k]),
    '预测结束（按排序）':'\n'.join(x['预测结束'] for x in found[:top_k]),'证据解释':basis})
  for rank,h in enumerate(found[:top_k],1):
   detail={k:v for k,v in h.items() if k!='_pair'}
   detail['是否唯一分配']='是' if winner is h else '否'
   detail['是否被其他GT占用']='是' if h['_pair'] in used and winner is not h else '否'
   hits.append({'人工编号':i+1,'排序':rank,'GT开始':r['GT开始'],'GT结束':r['GT结束'],**detail})
 cnt=collections.Counter(r['最佳匹配'] for r in records)
 cnt['唯一计分GT数']=len(assigned);cnt['匹配冲突GT数']=conflicts
 cnt['候选标签对唯一占用数']=len(used)
 return records,hits,dict(cnt)

def run(pred,gt,output,relations=None,top_k=5):
 c=read_table(pred);t=truth_rows(gt);rel=load_relations(relations)
 rows,hits,counts=evaluate(c,t,rel,top_k)
 o=Path(output);o.mkdir(parents=True,exist_ok=True)
 for name,rows0 in [('gt_match_summary.csv',rows),('gt_match_hits.csv',hits)]:
  with (o/name).open('w',encoding='utf-8-sig',newline='') as f:
   fields=list(rows0[0]) if rows0 else ['人工编号'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows0)
 report={'gt_rows':len(t),'candidates':len(c),'relations':len(rel),'categories':counts,'important':'conditional_transition only indicates potential event replacement; no scan/timestamp identity established'}
 (o/'gt_match_metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False));return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--candidates',required=True);p.add_argument('--gt',required=True);p.add_argument('--relations');p.add_argument('--out',required=True);p.add_argument('--top-k',type=int,default=5)
 a=p.parse_args();run(a.candidates,a.gt,a.out,a.relations,a.top_k)
