#!/usr/bin/env python3
"""v9.12 source-only extraction with preserved recall and local SMC2 guard evidence."""
import argparse,csv,json
from pathlib import Path
from recall import generate
from smc2_adapter import generate as smc_generate
from event_graph_v6 import generate as graph_generate
FIELDS=['构成要素','动作','动作描述','开始标签','结束标签','状态','证据POU','证据节点','原始条件','控制器','分支数']
def run(source,out_dir,pattern=None):
 out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
 from source_format import detect_source_format
 fmt=detect_source_format(source)
 native_model=None
 if fmt=='omron_iec61131_10':
  from omron_native import collect_native,write_native_outputs
  native_model=collect_native(source)
  report,graph=write_native_outputs(native_model,out)
 else:
  report=(smc_generate if fmt=='smc2' else generate)(source,out)
  graph=graph_generate(source,out,fmt)
 control=None
 action_trace=None
 if fmt=="plcopen":
  from plcopen_controlflow import generate as flow_generate
  control=flow_generate(source,out)
  from plcopen_action_trace import generate as action_trace_generate
  action_trace=action_trace_generate(source,out)
 from successor_graph import write_graph
 source_graph=write_graph(source,out,model=native_model)
 # v9.12: discover complete source-only transport protocols before any name filter.
 # A trigger BOOL is a legitimate elementary boundary, not a missing step number.
 from fb_transport import write_discovery as discover_transport
 transport=discover_transport(source,out)
 source_graph['transport_relations']=[{'id':x['relation_id'],'start':x['trigger'],
   'end':x['target_label'],'arrival':x['arrival'],'instance':x['instance'],
   'scope':x['receiver_scope'],'evidence':x['proof_level'],'source_line':x['source_line'],
   'arrival_required':x['arrival_required'],'receiver_guard':x['receiver_guard'],
   'timing_semantics':x['timing_semantics']} for x in transport['links']]
 (out/'source_transition_graph.json').write_text(json.dumps(source_graph,ensure_ascii=False,indent=2),encoding='utf-8')
 if transport['events']:
  with (out/'event_graph.csv').open('a',encoding='utf-8',newline='') as f:
   csv.writer(f).writerows(transport['events'])
  graph['events']+=len(transport['events']);graph['cross_fb_transport_events']=len(transport['events'])
  (out/'event_graph_audit.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
 # A waiting/handshake stage can contain only contacts + a state MOVE. Emit
 # a source-grounded stage event even when no actuator/coil was discovered.
 if source_graph.get('stage_events'):
  from event_graph_v6 import FIELDS as EVENT_FIELDS
  with (out/'event_graph.csv').open('a',encoding='utf-8',newline='') as f:
   csv.writer(f).writerows(source_graph['stage_events'])
  graph['source_stage_events_added']=len(source_graph['stage_events'])
  graph['events']+=len(source_graph['stage_events'])
  graph['wait_stages_added']=sum(e[4]=='state_wait' for e in source_graph['stage_events'])
  (out/'event_graph_audit.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
 with (out/'recall_candidates.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
 seen={(r['开始标签'],r['结束标签'],r['状态'],r['动作']) for r in rows}
 appended=0
 with (out/'event_graph.csv').open(encoding='utf-8-sig',newline='') as f:
  for ev in csv.DictReader(f):
   if not ev['guard'] or ev['command'] in ('state_wait','state_transition','cross_fb_transport'):continue
   description=f"{ev['command']} {ev['arguments']}"[:250]
   key=(ev['guard'],'','action_event_unverified',description)
   if key not in seen:
    seen.add(key);appended+=1
    rows.append({'构成要素':ev['guard'].split('=')[0],'动作':description,'动作描述':ev['comment'],'开始标签':ev['guard'],'结束标签':'','状态':'action_event_unverified','证据POU':ev['source_unit'],'证据节点':str(ev['network']),'原始条件':ev['feedback'],'控制器':ev['guard'].split('=')[0],'分支数':ev['guard'].count(' OR ')})
   # A feedback boundary is an alternative *hypothesis*, not a proven action end.
   for sig in ev['feedback'].split(' AND '):
    if not sig or not ev['guard'] or ' OR ' in ev['guard']:continue
    endpoint=ev['guard']+' AND '+sig
    key=(ev['guard'],endpoint,'feedback_same_body_hypothesis',description)
    if key not in seen:
     seen.add(key);appended+=1
     rows.append({'构成要素':ev['guard'].split('=')[0],'动作':description,'动作描述':ev['comment'],'开始标签':ev['guard'],'结束标签':endpoint,'状态':'feedback_same_body_hypothesis','证据POU':ev['source_unit'],'证据节点':str(ev['network']),'原始条件':sig,'控制器':ev['guard'].split('=')[0],'分支数':0})
 # v9: explicit EN-pin relation is stronger than numerical adjacency, but still not scan-time proof.
 flow_appended=0
 if control:
  with (out/'plcopen_control_relations.csv').open(encoding='utf-8-sig',newline='') as f:
   for rel in csv.DictReader(f):
    if rel['proof_level']!='wired_guard_local' or not rel['source_state']:continue
    state=rel['state_variable'];start=f"{state}={rel['source_state']}";end=f"{state}={rel['target_state']}"
    key=(start,end,'wired_move_guard_local','待确认状态转换')
    if key in seen:continue
    seen.add(key);flow_appended+=1
    rows.append({'构成要素':rel['source_unit'],'动作':'待确认状态转换','动作描述':'MOVE EN沿显式连线回溯到同变量EQ；扫描时序及反馈完成尚未证明','开始标签':start,'结束标签':end,'状态':'wired_move_guard_local','证据POU':rel['source_unit'],'证据节点':rel['move_id'],'原始条件':rel['other_guards'],'控制器':state,'分支数':0})
 # v9.8 retain the exact local comment and guard for the source stage, not a
 # controller-wide comment accidentally copied from an unrelated network.
 for stage in source_graph.get('stage_records',[]):
  key=(stage['开始标签'],stage['结束标签'],stage['状态'],stage['动作'])
  if key not in seen:
   seen.add(key);rows.append({f:stage.get(f,'') for f in FIELDS})
 # v9.7 SOURCE-ONLY explicit guard relations augment, never replace the recall pool.
 existing_boundary_pairs={(r['开始标签'],r['结束标签']) for r in rows}
 for rel in source_graph['transitions']:
  start=f"{rel['variable']}={rel['from']}"; end=f"{rel['variable']}={rel['to']}"
  choices=[(start,end,'source_supported_transition'),(start,f'NOT ({start})','state_exit')]
  # SMC2 stage_records already carry these sourced pairs with local comments.
  # Do not inflate the pool with a second generic copy of every sourced edge.
  if fmt in ('smc2','omron_iec61131_10'):
   choices=[]
   exit_pair=(start,f'NOT ({start})')
   if exit_pair not in existing_boundary_pairs:
    choices=[(exit_pair[0],exit_pair[1],'state_exit')]
    existing_boundary_pairs.add(exit_pair)
  for a,b,kind in choices:
   key=(a,b,kind,'状态阶段候选')
   if key in seen:continue
   seen.add(key)
   rows.append({'构成要素':rel['scope'],'动作':'状态阶段候选','动作描述':'源码局部状态迁移；工艺意义另行核验','开始标签':a,'结束标签':b,'状态':kind,'证据POU':rel['scope'],'证据节点':rel['id'],'原始条件':rel['guard'],'控制器':rel['variable'],'分支数':0})
 for row in transport['candidates']:
  key=(row['开始标签'],row['结束标签'],row['状态'],row['动作'])
  if key not in seen:seen.add(key);rows.append({f:row.get(f,'') for f in FIELDS})
 from signal_tail_merge import write_discovery
 signal_merge=write_discovery(source_graph,out)
 # Composition is prediction-side enrichment, not evaluation-side stitching.
 # No original row is removed; collection-view suppression runs after name filter.
 for proposal in signal_merge['proposals']:
  row=proposal['row'];key=(row['开始标签'],row['结束标签'],row['状态'],row['动作'])
  if key not in seen:seen.add(key);rows.append({f:row.get(f,'') for f in FIELDS})
 with (out/'sequence_candidates.csv').open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
 if pattern:
  from candidate_filter import filter_csv
  filtered=filter_csv(out/'sequence_candidates.csv',out/'sequence_candidates_filtered.csv',pattern)
 else:filtered=None
 from boundary_normalizer import normalize_file,normalize_rows,write_csv as boundary_write
 from candidate_filter import filter_rows
 selected=filter_rows(rows,pattern) if pattern else rows
 raw_pairs=[{**r,'候选ID':f'C{i:07d}'} for i,r in enumerate(selected,1) if r.get('开始标签','').strip() and r.get('结束标签','').strip()]
 boundary_write(out/'prediction_candidate_pairs_raw.csv',raw_pairs,['候选ID']+FIELDS)
 normalized_pairs,_=normalize_rows(raw_pairs,source_graph)
 boundary_write(out/'prediction_candidate_pairs.csv',normalized_pairs)
 norm_input=out/'sequence_candidates_filtered.csv' if pattern else out/'sequence_candidates.csv'
 normalization=normalize_file(norm_input,out/'sequence_candidates_normalized.csv',source_graph,out/'state_boundary_review.csv')
 # Coverage means a stage PAIR survived; it does not certify its process semantics.
 source_stages=source_graph.get('stage_records',[])
 kept={(r['开始标签'],r['结束标签']) for r in selected}
 covered=sum((r['开始标签'],r['结束标签']) in kept for r in source_stages if not pattern or filter_rows([r],pattern))
 stage_count=sum(1 for r in source_stages if not pattern or filter_rows([r],pattern))
 (out/'source_stage_preservation.json').write_text(json.dumps({'source_stage_rows_in_scope':stage_count,
  'stage_rows_with_pair_in_filtered_candidates':covered,'no_source_stage_pair_lost':covered==stage_count,
  'not_physical_action_coverage':True},ensure_ascii=False,indent=2),encoding='utf-8')
 report2={'boundary_normalization':normalization,'source_transition_graph':source_graph['summary'],'filter':filtered,'format':fmt,'controller_count':len({r.get('控制器') for r in rows if r.get('控制器')}),'legacy_controller_count':report['controller_count'],'candidate_count':len(rows),'new_action_boundary_candidates':appended,'event_graph':graph,'plcopen_controlflow':control,'plcopen_action_trace':action_trace,'wired_candidate_additions':flow_appended,'native_parse_audit':native_model.get('native_parse_audit') if native_model else None,'caution':'Co-location hypotheses are not verified action causality or physical completion.'}
 from atomic_boundaries import export_atomic
 report2['cross_fb_transport']=transport['summary']
 report2['atomic_projection']=export_atomic(raw_pairs,source_graph,out)
 from signal_tail_merge import export_collection
 report2['signal_merge']=signal_merge['summary']
 report2['collection_projection']=export_collection(out,source_graph)

 (out/'candidate_audit.json').write_text(json.dumps(report2,ensure_ascii=False,indent=2),encoding='utf-8')
 return report2
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('-o','--out',default='outputs/latest');p.add_argument('--pattern',default=None);a=p.parse_args();print(json.dumps(run(a.source,a.out,a.pattern),ensure_ascii=False))
