#!/usr/bin/env python3
"""Two isolated user workflows. Prediction never opens ground truth."""
import argparse, json, sys, hashlib
from pathlib import Path
from workflow import create, validate
from pair_evaluation import run as evaluate_candidates

def fingerprint(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
 return h.hexdigest()

def predict(args):
 src=Path(args.source).resolve()
 if not src.is_file():raise FileNotFoundError(f'PLC源码不存在：{src}')
 if src.suffix.lower() not in ('.xml','.smc2','.txt'):raise ValueError('源码需为XML/SMC2容器或其文本导出')
 out=Path(args.out).resolve()
 # Deliberately no --gt argument or gold-file discovery in prediction.
 if out==src.parent or out==src:raise ValueError('输出必须位于独立目录，不能覆盖源码所在目录')
 # v9.23: optional --trace argument (runtime trace file for duration backfill).
 trace_path=args.trace
 if trace_path:
  tp=Path(trace_path).resolve()
  if not tp.is_file():raise FileNotFoundError(f'trace文件不存在：{tp}')
  trace_path=str(tp)
 manifest=create(str(src),str(out),batch_size=args.batch_size,pattern=args.pattern,force=args.force,manual=args.manual,trace=trace_path)
 # v9.16: extract variable documentation (source facts) for workstation naming;
 # failure is non-fatal — it only degrades display names, not predictions.
 try:
  from extract_variable_docs import extract as extract_vdocs
  extract_vdocs(str(src),str(out/'variable_documentation.json'))
 except Exception as exc:
  print(f'variable_documentation 提取失败（不影响预测）：{exc}',file=sys.stderr)
 # v9.18: extract network comments from PLCopen actions (source facts);
 # used by delivery workbook for action descriptions and review highlighting.
 try:
  from extract_network_comments import extract as extract_ncomments
  extract_ncomments(str(src),str(out/'network_comments.json'))
 except Exception as exc:
  print(f'network_comments 提取失败（不影响预测）：{exc}',file=sys.stderr)
 # v9.21: extract step-level comments from ST code (source facts);
 # used by action_describe A-tier to replace variable-name descriptions
 # with source-authored Chinese step comments.
 try:
  from extract_step_comments import extract as extract_scomments
  extract_scomments(str(src),str(out/'step_comments.json'))
 except Exception as exc:
  print(f'step_comments 提取失败（不影响预测）：{exc}',file=sys.stderr)
 # v9.17: build station identity map from candidate controllers + var docs;
 # produces station_identity.json and an audit segment in candidate_audit.json.
 si_audit={'total_var_roots':0,'with_documentation':0,'without_documentation':0,'without_documentation_roots':[]}
 try:
  from station_identity import build_identity
  si_map,si_audit=build_identity(str(out))
  (out/'station_identity.json').write_text(json.dumps(si_map,ensure_ascii=False,indent=1),encoding='utf-8')
  if si_audit['without_documentation_roots']:
   print(f'station_identity: {len(si_audit["without_documentation_roots"])} 个变量根无文档 -> review',file=sys.stderr)
 except Exception as exc:
  print(f'station_identity 构建失败（不影响预测）：{exc}',file=sys.stderr)
 try:
  ap_=json.loads((out/'candidate_audit.json').read_text(encoding='utf-8'))
  ap_['station_identity']=si_audit
  (out/'candidate_audit.json').write_text(json.dumps(ap_,ensure_ascii=False,indent=1),encoding='utf-8')
 except Exception:
  pass
 prov={'version':'9.25.0','source_format':json.loads((out/'candidate_audit.json').read_text(encoding='utf-8'))['format'],'pipeline_order':['source_parse_all','source_cross_fb_protocols','name_filter','boundary_normalization','action_signal_tail_composition','optional_process_review'],'mode':'prediction','source_sha256':fingerprint(src),'source_name':src.name,'pattern':args.pattern,'candidate_sha256':fingerprint(out/'sequence_candidates.csv'),'prediction_has_gt_access':False,'status':'prepared_needs_opencode_batches','workflow_manifest':'workflow_manifest.json','trace':trace_path}
 prov['atomic_output_sha256']=fingerprint(out/'prediction_collection_pairs.csv')
 prov['atomic_archive_sha256']=fingerprint(out/'prediction_atomic_pairs.csv')
 prov['full_pair_archive_sha256']=fingerprint(out/'prediction_candidate_pairs.csv')
 (out/'prediction_provenance.json').write_text(json.dumps(prov,ensure_ascii=False,indent=2),encoding='utf-8')
 # v9.24: generate A-tier descriptions + B-tier batches in predict phase
 # (previously done in finish, requiring two finish runs).  Now predict
 # produces action_desc_a.csv + batches_desc/*.input.json, so agent can
 # process B-tier batches immediately, and finish only needs cmd_validate.
 try:
  from action_describe import cmd_a, cmd_build
  cmd_a(str(out))
  cmd_build(str(out))
 except Exception as exc:
  print(f'动作描述A档/B档批次生成失败（不影响预测）：{exc}',file=sys.stderr)
 audit=json.loads((out/'candidate_audit.json').read_text(encoding='utf-8'))
 with (out/'prediction_candidate_pairs.csv').open(encoding='utf-8-sig',newline='') as f:
  import csv
  complete_pairs=sum(1 for _ in csv.DictReader(f))
 print(json.dumps({'mode':'predict','source_format':audit['format'],'output':str(out),'complete_candidate_pairs':complete_pairs,'main_prediction':str(out/'prediction_collection_pairs.csv'),'full_recall_archive':str(out/'prediction_candidate_pairs.csv'),'atomic_projection':json.loads((out/'prediction_atomic_summary.json').read_text(encoding='utf-8')),'batch_count':len(manifest['batches']),'next':'OpenCode逐批写result.json后运行 python plc_cli.py finish --run "目录"'},ensure_ascii=False,indent=2))

def finish(args):
 r=validate(args.run,write=True)
 print(json.dumps(r,ensure_ascii=False,indent=2))
 if not r['success']:sys.exit(2)

def coverage(args):
 """v9.19: coverage evaluation against a manual collection workbook.
 Wraps 评价器/gen_coverage_eval.py — reads prediction + manual, never modifies either."""
 prediction=Path(args.prediction).resolve()
 manual=Path(args.manual).resolve()
 if not prediction.is_file():raise FileNotFoundError(f'预测文件不存在：{prediction}')
 if not manual.is_file():raise FileNotFoundError(f'人工表不存在：{manual}')
 out=Path(args.out).resolve()
 out.parent.mkdir(parents=True,exist_ok=True)
 if out==prediction.parent or out==manual.parent:raise ValueError('评价输出请单独指定目录')
 import sys as _sys
 _sys.path.insert(0,str(Path(__file__).resolve().parent/'评价器'))
 import gen_coverage_eval as gce
 proc_name=args.proc_name or gce.infer_proc_name(manual)
 kind=args.kind or gce.infer_kind(manual,'auto')
 var_desc=args.var_desc or 'STxx.AutoStep'
 # call the engine directly (it prints stats + saves xlsx)
 gce.main_with(prediction,manual,out,proc_name,var_desc,kind)
 print(f'覆盖率评价完成: {out}')

def evaluate(args):
 prediction=Path(args.prediction).resolve();truth=Path(args.gt).resolve()
 if not prediction.is_file() or not truth.is_file():raise FileNotFoundError('预测文件或GT不存在')
 out=Path(args.out).resolve()
 if out==prediction.parent or out==truth.parent:raise ValueError('评估输出请单独指定一个目录，避免覆盖预测或GT')
 relations=args.relations
 strict_graph=None
 transition_graph=getattr(args,'transition_graph',None)
 if not transition_graph and not getattr(args,'xml',None):
  sidecar=prediction.parent/'source_transition_graph.json'
  if sidecar.is_file():transition_graph=str(sidecar)
 if transition_graph:
  from boundary_normalizer import load_graph
  if Path(transition_graph).resolve() in (prediction,truth):raise ValueError('迁移图不得与预测/GT相同')
  strict_graph=load_graph(transition_graph)
 xml=getattr(args,'xml',None)
 if xml and transition_graph:raise ValueError('--xml 与 --transition-graph 二选一')
 if xml and relations:raise ValueError('--xml 和 --relations 请二选一，防止混用证据来源')
 if xml:
  xml_path=Path(xml).resolve()
  if not xml_path.is_file():raise ValueError('评价源码路径不存在')
  with xml_path.open('rb') as f:head=f.read(4096).lstrip(b'\xef\xbb\xbf \t\r\n')
  if not head.startswith(b'<'):raise ValueError('评价源码必须为XML文本容器；扩展名可以是.xml/.txt/.smc2')
  if xml_path in (prediction,truth):raise ValueError('XML不能与预测/GT文件相同')
  from successor_graph import write_graph as xml_scan
  evidence_dir=out/'xml_evidence';evidence_dir.mkdir(parents=True,exist_ok=True)
  strict_graph=xml_scan(xml_path,evidence_dir)
 if strict_graph is not None:
  provenance=prediction.parent/'prediction_provenance.json'
  if provenance.is_file():
   expected=json.loads(provenance.read_text(encoding='utf-8')).get('source_sha256')
   if expected and expected!=strict_graph.get('source_sha256'):raise ValueError('预测源码SHA256与评价迁移图来源不一致，拒绝混用工程')
 # Never build relations from GT; accept only explicit prediction-produced sidecar.
 if relations:
  rel=Path(relations).resolve()
  if not rel.is_file():raise FileNotFoundError(rel)
  if rel==truth:raise ValueError('关系文件不能与GT相同')
 from table_io import prediction_records
 sample=prediction_records(prediction)
 if sample and '原子对ID' in sample[0]:
  from atomic_evaluation import run as evaluate_atomic
  result=evaluate_atomic(str(prediction),str(truth),str(out),strict_graph,args.top_k)
 else:
  result=evaluate_candidates(str(prediction),str(truth),str(out),relations,args.top_k,graph=strict_graph)
 report={'mode':'evaluation','prediction_sha256':fingerprint(prediction),'gt_sha256':fingerprint(truth),'relations_sha256':fingerprint(relations) if relations else None,'evaluation_xml_sha256':fingerprint(xml) if xml else None,'transition_graph_sha256':fingerprint(transition_graph) if transition_graph else None,'gt_used_only_for_evaluation':True,'categories_not_all_successful':True}
 (out/'evaluation_provenance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 if not args.no_excel:
  try:
   from evaluation_excel import write_excel
   write_excel(out/'gt_match_summary.csv',out/'gt_match_hits.csv',out/'gt_match_metrics.json',out/'gt_match.xlsx')
   print('Excel:',out/'gt_match.xlsx')
  except ImportError as exc:
   print(f'Excel依赖未安装（CSV/JSON已生成）：{exc}',file=sys.stderr)
 print('评估文件：',out)
 return result

def main():
 p=argparse.ArgumentParser(description='PLC Agent v9.25，候选保全、逻辑后继统一、动作描述纯中文化(A档步注释自动+B档LLM补充/变量文档注入/简短动作重点)、predict生成A档+B档(finish一次完成)、预测评价隔离、trace跳变分析与时长回填(--trace)、覆盖评价(--manual自动触发)')
 sub=p.add_subparsers(dest='mode',required=True)
 x=sub.add_parser('predict',help='源码+变量模式；不接受GT')
 x.add_argument('--source',required=True);x.add_argument('--pattern');x.add_argument('--out',required=True);x.add_argument('--batch-size',type=int,default=120);x.add_argument('--force',action='store_true');x.add_argument('--manual',default=None,help='可选：人工采集表路径，传入后finish自动运行覆盖评价');x.add_argument('--trace',default=None,help='可选：运行时trace文件路径(.trace)，传入后finish自动分析跳变并回填交付表时长列');x.set_defaults(func=predict)
 x=sub.add_parser('evaluate',help='已有预测+GT；可读取XML独立核验，不修改预测')
 x.add_argument('--prediction',required=True);x.add_argument('--gt',required=True);x.add_argument('--out',required=True);x.add_argument('--relations');x.add_argument('--transition-graph',help='预测阶段生成的source_transition_graph.json，不接受GT作为证据');x.add_argument('--xml',help='仅评价读取PLCopen/SMC2/欧姆龙原生IEC61131-10 XML文本，生成独立控制流证据，绝不回写预测');x.add_argument('--top-k',type=int,default=5);x.add_argument('--no-excel',action='store_true');x.set_defaults(func=evaluate)
 x=sub.add_parser('finish',help='组装所有OpenCode批次+自动A档动作描述+交付表');x.add_argument('--run',required=True);x.set_defaults(func=finish)
 x=sub.add_parser('coverage',help='覆盖对比评价（有人工表时用gen_coverage_eval）')
 x.add_argument('--prediction',required=True);x.add_argument('--manual',required=True);x.add_argument('--out',required=True);x.add_argument('--proc-name',default=None);x.add_argument('--var-desc',default=None);x.add_argument('--kind',default=None);x.set_defaults(func=coverage)
 a=p.parse_args();a.func(a)
if __name__=='__main__':main()
