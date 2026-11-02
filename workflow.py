#!/usr/bin/env python3
"""Deterministic orchestration/checkpointing for an OpenCode-driven PLC analysis.
No gold labels, project names or specific PLC step constants are used.
"""
import argparse,csv,glob,hashlib,json,re,sys
from collections import defaultdict,Counter
from pathlib import Path
from agent import run as extract
from candidate_filter import filter_rows,filter_csv
from boundary_normalizer import META,normalize_rows,load_graph,empty_graph
from process_contract import FIELDS as PROCESS_FIELDS,review as review_contract

CANDIDATE_FIELDS=['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数']+META
FINAL_FIELDS=['构成要素','动作','动作描述','开始标签','结束标签','核验状态','证据候选ID','证据事件ID','说明']+META+PROCESS_FIELDS
def csv_read(path):
 with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def csv_write(path,rows,fields):
 with open(path,'w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
def digest(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()[:16]
def norm(s):return re.sub(r'\s+','',str(s or '')).lower()

def pack_controller_batches(crows,evrows,batch_size):
 """Attach each event to a matching candidate, not an unrelated ordinal chunk.
 Overflow batches repeat only referenced evidence rows; the main pool is unchanged.
 """
 if batch_size<1:raise ValueError('batch_size must be positive')
 packs=[{'c':crows[i:i+batch_size],'e':[]} for i in range(0,len(crows),batch_size)]
 bystart=defaultdict(list)
 for i,c in enumerate(crows):bystart[norm(c.get('开始标签',''))].append(i)
 anchors={};unmatched=[]
 for ev in evrows:
  eligible=bystart.get(norm(ev.get('guard','')),[])
  target=norm(ev.get('next_state',''))
  exact=[i for i in eligible if target and norm(crows[i].get('结束标签',''))==target]
  choices=exact or eligible
  if not choices:unmatched.append(ev);continue
  anchor=min(choices,key=lambda i:(0 if any(k in crows[i].get('状态','') for k in ('smc2_wired','native_wired','cross_fb_transport')) else 1,i))
  anchors[ev.get('事件ID',str(id(ev)))]=crows[anchor]
  packs[anchor//batch_size]['e'].append(ev)
 result=[]
 for pack in packs:
  result.append({'c':pack['c'],'e':pack['e'][:batch_size]})
  for i in range(batch_size,len(pack['e']),batch_size):
   es=pack['e'][i:i+batch_size];cs={}
   for ev in es:
    c=anchors.get(ev.get('事件ID',str(id(ev))))
    if c:cs[c['候选ID']]=c
   result.append({'c':list(cs.values()),'e':es})
 for i in range(0,len(unmatched),batch_size):result.append({'c':[],'e':unmatched[i:i+batch_size]})
 return result

def create(source,out,batch_size=120,force=False,pattern=None,manual=None,trace=None):
 out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
 manifest=out/'workflow_manifest.json'
 if manifest.exists() and not force:raise SystemExit('已存在 workflow_manifest.json；为保护人工整理进度，请指定新输出目录，或显式 --force。')
 report=extract(str(source),str(out))
 rows=csv_read(out/'sequence_candidates.csv');events=csv_read(out/'event_graph.csv')
 if pattern:
  filter_summary=filter_csv(out/'sequence_candidates.csv',out/'sequence_candidates_filtered.csv',pattern)
  rows=filter_rows(rows,pattern)
 else:filter_summary={'before':len(rows),'after':len(rows),'removed':0,'pattern':None}
 from process_semantics import classify_file
 process_triage=classify_file(out/'sequence_candidates_filtered.csv' if pattern else out/'sequence_candidates.csv',out)
 from temporal_audit import timing_relations,write_csv as write_diag_csv
 time_rows=timing_relations(events)
 write_diag_csv(out/'timing_relations.csv',time_rows,list(time_rows[0]) if time_rows else ['事件源行号','时间关系'])
 (out/'filter_audit.json').write_text(json.dumps(filter_summary,ensure_ascii=False,indent=2),encoding='utf-8')
 for i,r in enumerate(rows,1):r['候选ID']=f'C{i:07d}'
 raw_pairs=[r.copy() for r in rows if r.get('开始标签','').strip() and r.get('结束标签','').strip()]
 csv_write(out/'prediction_candidate_pairs_raw.csv',raw_pairs,['候选ID']+CANDIDATE_FIELDS)
 graph_path=out/'source_transition_graph.json'
 source_graph=load_graph(graph_path) if graph_path.exists() else empty_graph()
 rows,boundary_stats=normalize_rows(rows,source_graph)
 # Recompute source-stage preservation AFTER this workflow's name filter.
 stages=filter_rows(source_graph.get('stage_records',[]),pattern)
 csv_write(out/'source_stage_candidates_filtered.csv',stages,list(stages[0]) if stages else CANDIDATE_FIELDS)
 kept={(norm(r.get('开始标签','')),norm(r.get('结束标签',''))) for r in rows}
 hits=sum((norm(r['开始标签']),norm(r['结束标签'])) in kept for r in stages)
 preservation={'pattern':pattern,'source_stage_rows_in_scope':len(stages),
  'stage_rows_with_pair_in_filtered_candidates':hits,'no_source_stage_pair_lost':hits==len(stages),
  'not_physical_action_coverage':True}
 (out/'source_stage_preservation.json').write_text(json.dumps(preservation,ensure_ascii=False,indent=2),encoding='utf-8')
 csv_write(out/'state_boundary_review.csv',[r for r in rows if r['边界规范化状态'].startswith('待核验')],['候选ID']+CANDIDATE_FIELDS)
 for i,r in enumerate(events,1):r['事件ID']=f'E{i:07d}'
 csv_write(out/'indexed_candidates.csv',rows,['候选ID']+CANDIDATE_FIELDS)
 evfields=['事件ID']+list(events[0] if events else [])
 csv_write(out/'indexed_events.csv',events,evfields)
 # Group first by controller; non-state events remain in a separate auditable bucket.
 groups=defaultdict(lambda:{'c':[],'e':[]})
 controllers=list(dict.fromkeys(r['控制器'].strip() for r in rows if r['控制器'].strip()))
 for r in rows:groups[r['控制器'].strip() or '__ungrouped__']['c'].append(r)
 # Associate events to controllers using exact symbol boundaries in guards; may match more than one.
 # Events without any state guard still need their own batches and must not be silently dropped.
 for e in events:
  guard=e.get('guard','')
  # Source-backed cross-FB intervals start at a BOOL, so attach evidence to the
  # receiver controller named in the endpoint instead of orphaning it.
  if e.get('command')=='cross_fb_transport':guard=guard+' OR '+e.get('next_state','')
  matches=[c for c in controllers if re.search(r'(?<![\w.\[\]])'+re.escape(c)+r'\s*=',guard)] if guard else []
  if matches:
   for c in matches:groups[c]['e'].append(e)
  else:groups['__ungrouped_events__']['e'].append(e)
 batches=[];bid=0; bdir=out/'batches';bdir.mkdir(exist_ok=True)
 for controller,data in sorted(groups.items()):
  crows=data['c'];evrows=data['e']
  if not crows and not evrows:continue
  # Match stage events to their own candidate before applying context limits.
  for pack in pack_controller_batches(crows,evrows,batch_size):
   bid+=1;batch_id=f'B{bid:05d}'
   cp=pack['c'];ep=pack['e']
   payload={'batch_id':batch_id,'controller':controller,'candidate_count':len(cp),'event_count':len(ep),
    'candidates':cp,'events':ep,'task':'SMC2 state_wait代表源网络中的等待阶段，无机械线圈不等于无工艺意义。首先保留source_stage_candidates.csv中的有条件等待与交接，并解释其起止，不将注释中的数字当成新步号。为真实动作/加工保持/检测/工艺等待/关键交接填写过程类型、统计对象、起点含义、终点含义、工艺依据、边界依据。状态退出按源码后继规范化；不得按数字邻接猜测。动作紧接信号/数据收尾时按signal_merge_lineage.json合并；原始分段留档，内部延时计入；不得吞掉下一独立动作或等待。不能确定时记录待核验，不删除主候选。每个事件须入表或unresolved_events。'}
   (bdir/f'{batch_id}.input.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
   batches.append({'batch_id':batch_id,'controller':controller,'input':f'batches/{batch_id}.input.json','output':f'batches/{batch_id}.result.json','candidate_ids':[r['候选ID'] for r in cp],'event_ids':[r['事件ID'] for r in ep]})
 # A deterministic lossless candidate-pair export BEFORE the optional OpenCode semantic stage.
 # Do not drop weak hypotheses until after recall evaluation; retain provenance.
 pair_rows=[r for r in rows if r.get('开始标签','').strip() and r.get('结束标签','').strip()]
 csv_write(out/'prediction_candidate_pairs.csv',pair_rows,['候选ID']+CANDIDATE_FIELDS)
 from atomic_boundaries import export_atomic
 atomic_stats=export_atomic(raw_pairs,source_graph,out)
 from signal_tail_merge import export_collection
 collection_stats=export_collection(out,source_graph)
 # Extraction starts unfiltered; the public audit must describe the actual
 # post-filter files rather than retaining the temporary unfiltered projection.
 report['unfiltered_projection_before_workflow_filter']={
  'atomic_projection':report.get('atomic_projection'),
  'collection_projection':report.get('collection_projection')}
 report['filter']=filter_summary;report['atomic_projection']=atomic_stats
 report['collection_projection']=collection_stats
 (out/'candidate_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


 meta={'version':'9.25','collection_projection':collection_stats,'atomic_projection':atomic_stats,'boundary_normalization':boundary_stats,'source':str(Path(source).resolve()),'manual':str(Path(manual).resolve()) if manual else None,'trace':str(Path(trace).resolve()) if trace else None,'extraction':report,'batches':batches,'batch_size':batch_size,'candidate_count':len(rows),'candidate_pairs_count':len(pair_rows),'event_count':len(events),'status':'PREPARED','filter':filter_summary,'process_triage':process_triage,'plcopen_action_trace':report.get('plcopen_action_trace'),'timing_relation_hypotheses':len(time_rows)}
 manifest.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
 (out/'RESULT_SCHEMA.md').write_text('''# 每批结果JSON协议\n```json\n{"batch_id":"B00001","steps":[{"构成要素":"...","动作":"...","动作描述":"...","开始标签":"...","结束标签":"...","核验状态":"confirmed_or_candidate","证据候选ID":["C0000001"],"证据事件ID":["E0000001"],"说明":"..."}],"unresolved_events":[{"event_id":"E0000002","reason":"无法确定使能或反馈"}],"notes":"..."}\n```\n\n要求：开始标签必须来自候选开始标签（或明确写出证据候选ID）；结束标签必须来自候选结束标签；特殊的新条件只能标记 `source_review_required` 并注明源码位置，不能无证据认定已验证。每个事件ID必须出现在某个steps.证据事件ID或unresolved_events中；不可只凭同网络共现证明因果。每条step还要填写：过程类型（机械动作/加工保持/检测测量/工艺等待/交接数据/周期汇总/内部辅助/未分类）、统计对象、起点含义、终点含义、工艺依据、边界依据。缺字段仅进入复核队列，不能删主候选。\n''',encoding='utf-8')
 return meta

def validate(out,write=False):
 out=Path(out);manifest=json.loads((out/'workflow_manifest.json').read_text(encoding='utf-8'))
 # v9.22: fallback — if step_comments.json is missing (e.g. predict ran on
 # an older version without step-comment extraction), extract it now from
 # the source XML so A-tier descriptions work on re-runs.
 if not (out/'step_comments.json').exists():
  src=manifest.get('source','')
  if src and Path(src).is_file():
   try:
    from extract_step_comments import extract as _extract_sc
    _extract_sc(str(Path(src)),str(out/'step_comments.json'))
    print(f'step_comments.json extracted (fallback) from {src}',file=sys.stderr)
   except Exception as exc:
    print(f'step_comments fallback extraction failed: {exc}',file=sys.stderr)
 cmap={r['候选ID']:r for r in csv_read(out/'indexed_candidates.csv')}
 emap={r['事件ID']:r for r in csv_read(out/'indexed_events.csv')}
 all_steps=[];unresolved=[];issues=[];missing=[];coverage=Counter();done=0
 for b in manifest['batches']:
  p=out/b['output']
  if not p.exists():missing.append(b['batch_id']);continue
  try:result=json.loads(p.read_text(encoding='utf-8'))
  except Exception as ex:issues.append(f"{b['batch_id']} JSON格式错误: {ex}");continue
  if result.get('batch_id')!=b['batch_id']:issues.append(f"{b['batch_id']} ID错误");continue
  local_used=set()
  for step in result.get('steps',[]):
   ids=step.get('证据候选ID',[]);eids=step.get('证据事件ID',[])
   if isinstance(ids,str):ids=[ids]
   if isinstance(eids,str):eids=[eids]
   if not ids and not eids:issues.append(f"{b['batch_id']} 无任何证据的步序")
   for cid in ids:
    if cid not in cmap:issues.append(f'{b["batch_id"]} 无效候选 {cid}')
    elif cid not in b['candidate_ids']:issues.append(f'{b["batch_id"]} 越批候选 {cid}')
   for eid in eids:
    if eid not in emap:issues.append(f'{b["batch_id"]} 无效事件 {eid}')
    elif eid not in b['event_ids']:issues.append(f'{b["batch_id"]} 越批事件 {eid}')
    else:local_used.add(eid)
   start=step.get('开始标签','');end=step.get('结束标签','')
   if ids and not any(norm(start)==norm(cmap[x]['开始标签']) for x in ids if x in cmap):
    issues.append(f'{b["batch_id"]} 开始标签无法由引用候选核验: {start}')
   if end and ids and not any(norm(end)==norm(cmap[x]['结束标签']) for x in ids if x in cmap):
    issues.append(f'{b["batch_id"]} 结束标签无法由引用候选核验: {end}')
   entry={k:step.get(k,'') for k in FINAL_FIELDS};entry['证据候选ID']=';'.join(ids);entry['证据事件ID']=';'.join(eids)
   for k in META:
    if not entry.get(k) and len(ids)==1 and ids[0] in cmap:entry[k]=cmap[ids[0]].get(k,'')
   all_steps.append(entry)
  for item in result.get('unresolved_events',[]):
   eid=item.get('event_id','')
   if eid not in b['event_ids']:issues.append(f'{b["batch_id"]} 未解决事件ID无效: {eid}')
   else:local_used.add(eid);unresolved.append({'批次':b['batch_id'],'事件ID':eid,'原因':item.get('reason','未提供')})
  uncovered=set(b['event_ids'])-local_used
  if uncovered:issues.append(f'{b["batch_id"]} 有{len(uncovered)}条动作观察未处理（例如{sorted(uncovered)[:3]}）')
  done+=1;coverage.update(local_used)
 if not manifest['batches']:issues.append('解析未生成任何候选或动作事件；不允许将空表视为分析成功。')
 status={'total_batches':len(manifest['batches']),'completed_batches':done,'missing_batches':missing,'invalid_issues':issues,'total_events':len(emap),'accounted_event_ids':len(coverage),'final_rows':len(all_steps),'candidate_pairs_before_semantic_filter':manifest.get('candidate_pairs_count',0),'unresolved_event_rows':len(unresolved),'success':not missing and not issues}
 if write:
  (out/'workflow_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
  if status['success']:
   unique={tuple(r.get(k,'') for k in FINAL_FIELDS):r for r in all_steps}
   final_rows=list(unique.values())
   if manifest.get('filter',{}).get('pattern'):
    final_rows=filter_rows(final_rows,manifest['filter']['pattern'])
    csv_write(out/'final_sequence_unfiltered.csv',list(unique.values()),FINAL_FIELDS)
   csv_write(out/'final_sequence_before_boundary_normalization.csv',final_rows,FINAL_FIELDS)
   graph_path=out/'source_transition_graph.json'
   source_graph=load_graph(graph_path) if graph_path.exists() else empty_graph()
   final_rows,final_norm=normalize_rows(final_rows,source_graph)
   csv_write(out/'final_sequence.csv',final_rows,FINAL_FIELDS)
   # Never use semantic keyword filtering to replace the candidate-pair deliverable.
   # Also include any complete, LLM-proposed pair not already in the deterministic pool.
   base_pairs=csv_read(out/'prediction_candidate_pairs.csv') if (out/'prediction_candidate_pairs.csv').exists() else [r for r in csv_read(out/'indexed_candidates.csv') if r.get('开始标签','').strip() and r.get('结束标签','').strip()]
   base_keys={(norm(x['开始标签']),norm(x['结束标签'])) for x in base_pairs}
   extra=[{**r,'候选ID':'','来源':'OpenCode工艺整理'} for r in final_rows if r.get('开始标签') and r.get('结束标签') and (norm(r['开始标签']),norm(r['结束标签'])) not in base_keys]
   all_pairs=[{**r,'来源':'全量候选-变量过滤后'} for r in base_pairs]+extra
   csv_write(out/'final_prediction_pairs.csv',all_pairs,list(dict.fromkeys(['来源','候选ID']+CANDIDATE_FIELDS+FINAL_FIELDS)))
   # Preserve complete human-review draft. Only explicit, evidence-linked process
   # actions / process checks / waits are promoted to the action-centric table.
   process_rows=[];needs_review=[];contract_rows=[]
   for row in final_rows:
    verdict=review_contract(row,cmap,emap)
    checked={**row,**verdict};contract_rows.append(checked)
    if verdict['工艺契约结果']=='推荐待复核':process_rows.append(checked)
    else:needs_review.append(checked)
   csv_write(out/'final_process_steps.csv',process_rows,FINAL_FIELDS+['工艺契约结果','筛选原因'])
   from atomic_boundaries import export_atomic
   export_atomic(all_pairs,source_graph,out,prefix='final')
   export_atomic(process_rows,source_graph,out,prefix='process')
   export_atomic(final_rows,source_graph,out,prefix='sequence')
   from signal_tail_merge import export_collection
   for prefix in ('final','process','sequence'):
    export_collection(out,source_graph,prefix)

   # Public final tables obey the atomic-only contract. Their lossless originals
   # stay beside them under *_full / *_raw names and lineage accounts for all rows.
   import shutil
   for original,archive,atomic_file in (
       ('final_prediction_pairs.csv','final_prediction_pairs_full.csv','final_collection_pairs.csv'),
       ('final_sequence.csv','final_sequence_raw.csv','sequence_collection_pairs.csv'),
       ('final_process_steps.csv','final_process_steps_raw.csv','process_collection_pairs.csv')):
    shutil.copyfile(out/original,out/archive)
    shutil.copyfile(out/atomic_file,out/original)


   csv_write(out/'process_review_queue.csv',needs_review,FINAL_FIELDS+['工艺契约结果','筛选原因'])
   csv_write(out/'process_contract_check.csv',contract_rows,FINAL_FIELDS+['工艺契约结果','筛选原因'])
   csv_write(out/'unresolved_events.csv',unresolved,['批次','事件ID','原因'])
   # v9.19: auto-generate A-tier action descriptions (source-facts template,
   # no LLM needed, reads only predict products — never GT).  Then build
   # B-tier batches (batches_desc/*.input.json) for agent to process.
   # If batches_desc/*.result.json exist (agent已处理), validate-merge B+A.
   # v9.24: cmd_a + cmd_build moved to predict phase; finish only does
   # cmd_validate (merge B+A).  This eliminates the need for two finish runs.
   try:
    from action_describe import cmd_validate
    desc_files = glob.glob(str(out/'batches_desc'/'*.result.json'))
    if desc_files:
     cmd_validate(str(out))
    else:
     import shutil as _sh
     a_path = out/'action_desc_a.csv'
     f_path = out/'action_desc_final.csv'
     if a_path.exists():
      _sh.copyfile(a_path, f_path)
    # v9.23:如实报告 B 档状态。b_tier=True 仅表示 agent 已逐批处理；
    # 未处理时 b_tier=False，交付表会有大面积空描述，需 agent 处理后重跑。
    b_inputs = glob.glob(str(out/'batches_desc'/'*.input.json'))
    status['action_desc'] = {
     'a_tier': True,
     'b_tier': bool(desc_files),
     'b_tier_batches_built': bool(b_inputs),
     'b_tier_input_count': len(b_inputs),
     'b_tier_result_count': len(desc_files),
     'b_tier_pending': len(b_inputs) - len(desc_files),
    }
    if not desc_files and b_inputs:
     print(f'B档未处理: {len(b_inputs)}批 input.json 待 agent 逐批处理写 result.json 后重跑 finish；当前交付表动作描述为空（仅 A 档步注释）', file=sys.stderr)
   except Exception as exc:
    status['action_desc'] = {'error': str(exc)}
    print(f'warning: action description not generated ({exc}); CSV deliverables intact', file=sys.stderr)
   # v9.23: runtime trace jump analysis — parse the .trace file (if provided
   # in manifest), match predicted pairs to observed jumps, and produce a
   # runtime map that gen_workstation_xlsx reads to fill the 时长(ms) column.
   # Reads only trace + prediction; never opens GT/manual tables.
   trace_path = manifest.get('trace')
   if trace_path and Path(trace_path).is_file():
    try:
     from trace_jump_analyzer import analyze as trace_analyze
     ta_out = out/'trace_analysis'
     ta_stats = trace_analyze(str(trace_path), str(out/'final_prediction_pairs.csv'), str(ta_out))
     status['trace_analysis'] = ta_stats
     print(f'trace分析: {ta_stats["matched_pairs"]}/{ta_stats["prediction_pairs"]} 预测对在trace中观察到', file=sys.stderr)
    except Exception as exc:
     status['trace_analysis'] = {'error': str(exc)}
     print(f'warning: trace analysis failed ({exc}); CSV deliverables intact', file=sys.stderr)
   # Delivery table: workstation-format xlsx ships with every finished run.
   try:
    from gen_workstation_xlsx import generate as gen_ws
    status['workstation_xlsx'] = gen_ws(str(out/'final_prediction_pairs.csv'),run=str(out))
   except Exception as exc:
    status['workstation_xlsx'] = {'error': str(exc)}
    print(f'warning: workstation xlsx not generated ({exc}); CSV deliverables intact', file=sys.stderr)
   # v9.20: auto-run coverage evaluation when manifest has a manual table path
   manual_path = manifest.get('manual')
   if manual_path and Path(manual_path).is_file():
    try:
     import sys as _sys
     _sys.path.insert(0,str(Path(__file__).resolve().parent/'评价器'))
     import gen_coverage_eval as gce
     cov_proc = gce.infer_proc_name(Path(manual_path))
     cov_kind = gce.infer_kind(Path(manual_path),'auto')
     cov_out = out/'评价'/f'{cov_proc}_覆盖评价.xlsx'
     cov_out.parent.mkdir(parents=True,exist_ok=True)
     gce.main_with(str(out/'final_prediction_pairs.csv'),str(manual_path),str(cov_out),cov_proc,'STxx.AutoStep',cov_kind)
     status['coverage'] = {'manual': manual_path, 'out': str(cov_out), 'proc_name': cov_proc}
    except Exception as exc:
     status['coverage'] = {'error': str(exc)}
     print(f'warning: coverage evaluation failed ({exc}); CSV deliverables intact', file=sys.stderr)
   (out/'workflow_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
   (out/'review.md').write_text(f'# 工艺草表核验\n\n候选工艺步序：{len(unique)}条；已处理动作观察ID：{len(coverage)}/{len(emap)}；待解决事件：{len(unresolved)}条。\n\n注意：逐事件可追踪不等于已证明物理动作的完整性。所有状态为候选的步骤需人工复核。\n',encoding='utf-8')
 return status

def main():
 p=argparse.ArgumentParser(description='OpenCode PLC全流程工作台')
 sub=p.add_subparsers(dest='cmd',required=True)
 a=sub.add_parser('prepare');a.add_argument('source');a.add_argument('-o','--out',required=True);a.add_argument('--batch-size',type=int,default=120);a.add_argument('--force',action='store_true');a.add_argument('--pattern',default=None,help='Glob variable pattern, e.g. ST*.AutoStep[*]')
 for op in ('status','assemble'):
  s=sub.add_parser(op);s.add_argument('out')
 args=p.parse_args()
 if args.cmd=='prepare':r=create(args.source,args.out,args.batch_size,args.force,args.pattern);print(json.dumps({k:v for k,v in r.items() if k!='batches'}|{'batch_count':len(r['batches'])},ensure_ascii=False))
 else:
  r=validate(args.out,args.cmd=='assemble');print(json.dumps(r,ensure_ascii=False,indent=2));sys.exit(0 if r['success'] else 2)
if __name__=='__main__':main()
