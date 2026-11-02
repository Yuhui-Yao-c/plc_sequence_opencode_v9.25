"""Content-based XML dialect selection. Never routes by project/variable name."""
from pathlib import Path
from lxml import etree

IEC10_NS = 'www.iec.ch/public/TC65SC65BWG7TF10'

def detect_source_format(source):
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open('rb') as stream:
        for _, root in etree.iterparse(stream, events=('start',), resolve_entities=False,
                                    no_network=True, huge_tree=True):
            q = etree.QName(root)
            if q.localname == 'smc2':
                return 'smc2'
            if q.localname == 'Project' and q.namespace == IEC10_NS:
                return 'omron_iec61131_10'
            if q.localname.lower() == 'project' and (not q.namespace or 'plcopen' in q.namespace.lower()):
                return 'plcopen'
            raise ValueError(f'Unsupported XML dialect: root={q.localname!r}, namespace={q.namespace!r}. '
                             'Refusing to silently return a partial ST-only result.')
    raise ValueError('Empty XML source')
