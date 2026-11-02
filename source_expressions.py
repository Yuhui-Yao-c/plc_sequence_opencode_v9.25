"""Source-only scalar predicates. NOT a GT repair or whole ST interpreter.

Scalar relational tests outside the small boundary grammar are represented as
explicit symbolic predicates with a source-text sidecar. They are never actual
PLC tag names and never qualify a NOT -> successor equivalence proof.
"""
from __future__ import annotations
import hashlib
import re
from boundary_logic import parse, Expr, BoundarySyntaxError, IDENT

SCALAR = re.compile(rf'(?:{IDENT}|(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?|[+*/()\-])', re.I)


def scalar_reads(text: str) -> list[str]:
    return sorted(set(re.sub(r'\s+', '', m.group()) for m in re.finditer(IDENT, text)
                      if m.group().upper() not in {'INT', 'DINT', 'REAL', 'TRUE', 'FALSE'}))


def valid_scalar(text: str) -> bool:
    """No calls, indexing expressions with side effects, semicolons or flow."""
    if re.search(r'\b(?:IF|THEN|ELSE|RETURN|FOR|WHILE|CASE)\b|:=|;',text,re.I):return False
    if re.search(rf'(?:{IDENT})\s*\(', text):return False
    pos=0;depth=0;expect_operand=True
    for m in SCALAR.finditer(text):
        if text[pos:m.start()].strip():return False
        t=m.group();pos=m.end()
        if t=='(':
            if not expect_operand:return False
            depth+=1
        elif t==')':
            if expect_operand or not depth:return False
            depth-=1
        elif t in '+-*/':
            if expect_operand and t not in '+-':return False
            expect_operand=True
        else:
            if not expect_operand:return False
            expect_operand=False
    return not text[pos:].strip() and not depth and not expect_operand


class SourceConditions:
    def __init__(self):self.definitions={}

    def relational(self, left: str, op: str, right: str) -> Expr:
        if op not in ('<','>','<=','>=','=','<>','!=','==') or not valid_scalar(left) or not valid_scalar(right):
            raise BoundarySyntaxError('unsupported source scalar predicate')
        text=f'{left.strip()} {op} {right.strip()}'
        key='__source_predicate_'+hashlib.sha256(text.casefold().encode()).hexdigest()[:16]
        self.definitions[key]={'id':key,'kind':'scalar_relation','expression':text,
            'reads':scalar_reads(left)+scalar_reads(right),'not_a_plc_tag':True,
            'proof':'symbolic_source_predicate_not_numeric_or_temporal_proof'}
        return Expr('signal',(key,))

    def parse(self, text: str) -> Expr:
        try:return parse(text)
        except BoundarySyntaxError:
            # This extension deliberately supports only a single scalar test.
            # Unknown Boolean mixtures must not be repaired or partially parsed.
            if re.search(r'\b(?:AND|OR|NOT|XOR)\b',text,re.I):raise
            m=re.fullmatch(r'\s*(.+?)\s*(>=|<=|<>|!=|==|>|<|=)\s*(.+?)\s*',text,re.S)
            if not m:raise
            return self.relational(*m.groups())

    def expand(self,text: str) -> str:
        for key,d in self.definitions.items():text=text.replace(key,'('+d['expression']+')')
        return text
