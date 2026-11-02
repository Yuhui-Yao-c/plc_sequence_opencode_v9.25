"""Source-only SMC2 network stream with location and explicit parse diagnostics.

JSON dictionaries, JSON arrays and adjacent JSON objects are supported. Malformed
fragments are reported and decoding continues at the next physical JSON-object
line; they are never silently treated as a complete source. No GT input.
"""
from __future__ import annotations
import json,re
from pathlib import Path
from lxml import etree


def iter_networks(source, audit=None):
    audit = audit if audit is not None else {}
    audit.setdefault('entries', 0);audit.setdefault('networks', 0)
    audit.setdefault('entry_types', {});audit.setdefault('parse_issues', [])
    decoder=json.JSONDecoder()
    for _,entry in etree.iterparse(str(source),events=('end',),tag='entry',huge_tree=True,
                                  resolve_entities=False,no_network=True):
        kind=entry.get('type','');name=entry.get('name','<unnamed>')
        audit['entries']+=1;audit['entry_types'][kind]=audit['entry_types'].get(kind,0)+1
        if kind=='json':
            raw=entry.text or '';pos=0;network=0;line=entry.sourceline or 1;line_pos=0
            while pos<len(raw):
                while pos<len(raw) and (raw[pos].isspace() or raw[pos]==','):pos+=1
                if pos>=len(raw):break
                line+=raw.count('\n',line_pos,pos);line_pos=pos
                try:obj,end=decoder.raw_decode(raw,pos)
                except json.JSONDecodeError as ex:
                    audit['parse_issues'].append({'entry':name,'line':line,'offset':pos,'reason':str(ex)})
                    m=re.search(r'\n\s*(?=\{\s*"CLs"\s*:)',raw[pos+1:])
                    if not m:break
                    pos=pos+1+m.end();continue
                pos=end
                objects=obj if isinstance(obj,list) else [obj]
                for net in objects:
                    if not isinstance(net,dict) or not isinstance(net.get('CLs'),list):
                        audit['parse_issues'].append({'entry':name,'line':line,'reason':'JSON item has no CLs list'})
                        continue
                    audit['networks']+=1
                    yield {'entry':name,'network':network,'source_line':line,'net':net}
                    network+=1
        entry.clear()
        while entry.getprevious() is not None:del entry.getparent()[0]
