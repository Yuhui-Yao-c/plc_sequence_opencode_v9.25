"""Symbol-aware filtering, not plain substring matching. Glob syntax *, ?; square brackets are literal."""
import argparse,csv,fnmatch,re,json
from pathlib import Path
SYMBOL=re.compile(r'(?<![\w.])(?:[A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*\.)*[A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*(?:\[[\w,]+\])*(?:\.[A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*(?:\[[\w,]+\])*)*')
EXCLUDE={'AND','OR','NOT','TRUE','FALSE','INT','BOOL','REAL','T','TIME'}
def extract_symbols(expr):
 expr=re.sub(r'(?:INT|DINT|UINT|UDINT|LINT|USINT|WORD|DWORD|BYTE)#[-+]?\d+','',expr or '',flags=re.I)
 return [x for x in SYMBOL.findall(expr) if x.upper() not in EXCLUDE and not x.isdigit()]
def glob_regex(pattern):
 if pattern.startswith(('re:', 'regex:')):
  return re.compile(pattern.split(':',1)[1],re.I)
 return re.compile("^"+re.escape(pattern).replace(r"\*",".*").replace(r"\?",".")+"$",re.I)
def match_boundary(start,end,pattern):
 regex=glob_regex(pattern)
 return any(regex.fullmatch(s) for e in (start,end) for s in extract_symbols(e))
def filter_rows(rows,pattern):
 if not pattern:return list(rows)
 return [r for r in rows if match_boundary(r.get('开始标签',''),r.get('结束标签',''),pattern)]
def filter_csv(input_path,output_path,pattern):
 with open(input_path,encoding='utf-8-sig',newline='') as f:
  first=f.readline();f.seek(0);delim='\t' if '\t' in first else ','
  reader=csv.DictReader(f,delimiter=delim);fields=reader.fieldnames;original=list(reader)
 selected=filter_rows(original,pattern)
 output_path=Path(output_path);output_path.parent.mkdir(parents=True,exist_ok=True)
 with open(output_path,'w',encoding='utf-8-sig',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(selected)
 return {'before':len(original),'after':len(selected),'removed':len(original)-len(selected),'pattern':pattern}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('-o','--out',required=True);p.add_argument('--pattern',required=True);a=p.parse_args()
 print(json.dumps(filter_csv(a.source,a.out,a.pattern),ensure_ascii=False))
