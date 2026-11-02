# -*- coding: utf-8 -*-
"""Extract network-level comments from PLCopen XML actions (source facts only).

PLCopen <action> elements contain <body><LD> with <comment> children that
describe the network's action.  These comments are the best source of
human-written action descriptions (e.g. "开门请求操作", "Z轴走取料位").

This module is the P1 layer of v9.18: it extracts comments per action and
outputs network_comments.json.  The action name matches the "构成要素" column
in final_prediction_pairs.csv, so the presentation layer can join them.

Output: network_comments.json
  {"SC04_Run1短边清洗1": ["短边清洗1吹气", "短边清洗1伺服上位", ...], ...}

Usage:
  python extract_network_comments.py --source "source.xml" --out "outputs/run/network_comments.json"
"""
import argparse, json, re
from lxml import etree


def local(e):
    return etree.QName(e).localname if isinstance(e.tag, str) else ''


# Comments that carry no action-description meaning
_NOISE_PATTERNS = re.compile(
    r'^(?=//|/=|网络注释$|网络\d*$|\s*$)'
)
# Generic template comments that repeat across many actions (low information)
_GENERIC_PATTERNS = re.compile(
    r'^(启动条件|运行条件|互锁条件|报警|初始化位置|模式定义|EM\d+_SV\[\d+\]|IOC\d+_IM\d+|站点丢失|站点错误|电源开关反馈)'
)


def _is_meaningful(text: str) -> bool:
    """Keep comments with real action-description content."""
    t = text.strip()
    if not t or _NOISE_PATTERNS.match(t):
        return False
    if len(t) < 3:
        return False
    return True


def _clean(text: str) -> str:
    """Strip *...* markers and excess whitespace from a comment."""
    t = text.strip()
    # Remove leading/trailing * markers
    t = re.sub(r'^\*+\s*', '', t)
    t = re.sub(r'\s*\*+\s*$', '', t)
    # Collapse internal whitespace
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


def extract(source: str, out: str) -> dict:
    """Extract comments from all <action> elements in PLCopen XML.

    Returns the comments dict (action_name -> [cleaned comments]) and writes
    JSON to *out*.  Safe to import from plc_cli.predict."""
    result = {}
    for _, e in etree.iterparse(source, events=('end',), tag='{*}action',
                                huge_tree=True, resolve_entities=False, no_network=True):
        name = e.get('name', '')
        if not name:
            e.clear()
            continue
        comments = []
        for c in e.iter('{*}comment'):
            raw = ''.join(c.itertext()).strip()
            if not _is_meaningful(raw):
                continue
            cleaned = _clean(raw)
            if cleaned and len(cleaned) >= 3:
                comments.append(cleaned)
        if comments:
            # Deduplicate while preserving order
            seen = set()
            unique = []
            for c in comments:
                if c not in seen:
                    seen.add(c)
                    unique.append(c)
            result[name] = unique
        e.clear()
        while e.getprevious() is not None:
            p = e.getparent()
            if p is not None:
                del p[p.index(e) - 1]
            else:
                break
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def main():
    ap = argparse.ArgumentParser(description='Extract network comments from PLCopen XML actions')
    ap.add_argument('--source', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    result = extract(a.source, a.out)
    total_comments = sum(len(v) for v in result.values())
    print(f'network_comments: {len(result)} actions, {total_comments} comments -> {a.out}')


if __name__ == '__main__':
    main()
