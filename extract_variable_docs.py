# -*- coding: utf-8 -*-
"""Extract variable-level documentation from PLC source XML (source facts only).

Supports both formats:
  - PLCopen XML:  <variable name="X"><type>...</type><documentation>中文</documentation>
  - omron Sysmac: <Variable name="X">...<Documentation>中文</Documentation>

Output JSON: {"all": {name: doc}, "stations": {name: doc}}
"stations" holds PLCopen station-struct variables (sStationConrol type) when present.

Usage:
  python extract_variable_docs.py --source "source.xml" --out "outputs/run/variable_documentation.json"
"""
import argparse, json
from lxml import etree


def local(e):
    return etree.QName(e).localname if isinstance(e.tag, str) else ''


def doc_text(e):
    doc = next((c for c in e if local(c) in ('Documentation', 'documentation')), None)
    if doc is None:
        return ''
    return ''.join(doc.itertext()).strip()


def extract(source, out):
    """Extract variable-level documentation from PLC source XML.

    Reads <variable>/<Variable> elements and their <documentation> children.
    Returns (merged_count, station_count) and writes JSON to *out*.
    Safe to import and call from plc_cli.predict (failures caught by caller)."""
    docs = {}       # first seen (local) fallback
    gdocs = {}      # global-scope override
    stations = {}
    for tag in ('{*}variable', '{*}Variable'):
        for _, e in etree.iterparse(source, events=('end',), tag=tag,
                                    huge_tree=True, resolve_entities=False, no_network=True):
            name = e.get('name', '')
            text = doc_text(e)
            if name and text:
                parent = e.getparent()
                pl = local(parent) if parent is not None else ''
                target = gdocs if pl.lower().startswith('global') else docs
                if name not in target:
                    target[name] = text
                typ = e.find('{*}type/{*}derived')
                if typ is not None and 'station' in str(typ.get('name', '')).lower():
                    stations.setdefault(name, text)
            e.clear()
            while e.getprevious() is not None and local(e.getprevious()) in ('variable', 'Variable'):
                del e.getparent()[e.getparent().index(e) - 1]
    merged = dict(docs)
    merged.update(gdocs)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'all': merged, 'stations': stations}, f, ensure_ascii=False, indent=1)
    return len(merged), len(stations)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    merged_count, station_count = extract(a.source, a.out)
    print(f'variables with documentation: {merged_count} (global-override), station structs: {station_count} -> {a.out}')


if __name__ == '__main__':
    main()
