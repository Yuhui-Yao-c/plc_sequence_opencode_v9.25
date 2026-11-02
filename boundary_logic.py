"""Small, fail-closed Boolean grammar for boundary labels (not a full ST compiler).
Whitespace/case/integer types and same-level AND/OR order are canonicalized.
Missing operators, arithmetic, and shorthand `S=1 OR 2` are NEVER repaired silently.
Equality/inequality between two symbols, ordering comparisons (var op integer)
and 2#/8#/16# based literals are parsed as opaque atoms: they are preserved
verbatim in guard text, can never anchor a positive state, and count as
"possible" in satisfiability checks (never proofs).
"""
from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Iterable

class BoundarySyntaxError(ValueError): pass

IDENT = r'[^\W\d]\w*(?:\s*\[(?:[^\[\]]|\[[^\[\]]*\])+\])*(?:\s*\.\s*[^\W\d]\w*(?:\s*\[(?:[^\[\]]|\[[^\[\]]*\])+\])*)*'
NUM = r'(?:(?:2|8|16)\s*#\s*[0-9A-Fa-f]+|(?:(?:SINT|USINT|INT|UINT|DINT|UDINT|LINT|ULINT|BYTE|WORD|DWORD|LWORD)\s*#\s*)?[+-]?\d+)'
TOKEN = re.compile(rf'\s*(?:(?P<num>{NUM})(?![\w#])|(?P<id>{IDENT})|(?P<op><>|!=|==|>=|<=|>|<|=|[()]))', re.I)

def integer(s: str) -> int | None:
    s=str(s).strip()
    m=re.fullmatch(r'(2|8|16)\s*#\s*([0-9A-Fa-f]+)',s,re.I)
    if m: return int(m[2],int(m[1]))
    if not re.fullmatch(NUM,s,re.I): return None
    return int(s.split('#')[-1].strip())

def symbol(s: str) -> str: return re.sub(r'\s+', '', s)

@dataclass(frozen=True)
class Expr:
    kind: str
    args: tuple = ()
    def text(self) -> str:
        if self.kind == 'const': return 'TRUE' if self.args[0] else 'FALSE'
        if self.kind == 'signal': return self.args[0]
        if self.kind == 'opaque_cmp': return self.args[0]
        if self.kind in ('eq','ne','veq','vne','gt','lt','ge','le'):
            sym={'eq':'=','ne':'<>','veq':'=','vne':'<>','gt':'>','lt':'<','ge':'>=','le':'<='}[self.kind]
            return f'{self.args[0]}{sym}{self.args[1]}'
        if self.kind == 'not': return f'NOT ({self.args[0].text()})'
        return (' '+self.kind.upper()+' ').join('('+x.text()+')' if x.kind in ('and','or','xor') and x.kind!=self.kind else x.text() for x in self.args)
    def key(self) -> tuple:
        if self.kind in ('eq','ne'): return self.kind, self.args[0].casefold(), self.args[1]
        if self.kind in ('veq','vne','gt','lt','ge','le'): return self.kind, self.args[0].casefold(), str(self.args[1])
        if self.kind=='signal': return self.kind, self.args[0].casefold()
        if self.kind=='const': return self.kind, self.args[0]
        if self.kind=='opaque_cmp': return self.kind, re.sub(r'\s+','',self.args[0]).casefold()
        return self.kind, tuple(x.key() for x in self.args)

TRUE=Expr('const',(True,)); FALSE=Expr('const',(False,))
def combine(kind: str, *items: Expr) -> Expr:
    flat=[]
    for x in items:
        if x.kind=='const':
            if (kind=='and' and not x.args[0]) or (kind=='or' and x.args[0]): return x
            continue
        flat.extend(x.args if x.kind==kind else (x,))
    unique={x.key():x for x in flat}
    xs=tuple(unique[k] for k in sorted(unique,key=repr))
    if not xs: return TRUE if kind=='and' else FALSE
    if len(xs)==1: return xs[0]
    return Expr(kind,xs)

def negate(x: Expr) -> Expr:
    if x.kind=='const': return TRUE if not x.args[0] else FALSE
    if x.kind=='not': return x.args[0]
    if x.kind in ('eq','ne'): return Expr('ne' if x.kind=='eq' else 'eq',x.args)
    if x.kind in ('veq','vne'): return Expr('vne' if x.kind=='veq' else 'veq',x.args)
    if x.kind in ('gt','lt','ge','le'): return Expr({'gt':'le','le':'gt','lt':'ge','ge':'lt'}[x.kind],x.args)
    if x.kind in ('and','or'): return combine('or' if x.kind=='and' else 'and',*(negate(a) for a in x.args))
    return Expr('not',(x,))

class Parser:
    def __init__(self,text: str):
        text=str(text).replace('\xa0',' ').strip(); self.tokens=[]; self.i=0;pos=0
        while pos<len(text):
            m=TOKEN.match(text,pos)
            if not m: raise BoundarySyntaxError(f'Unsupported syntax at {pos}: {text[pos:pos+40]}')
            typ=m.lastgroup;val=m.group(typ);self.tokens.append((typ,val));pos=m.end()
        if not self.tokens: raise BoundarySyntaxError('Empty boundary')
    def is_(self,*vals): return self.i<len(self.tokens) and self.tokens[self.i][1].upper() in vals
    def eat(self,val=None):
        if self.i>=len(self.tokens) or (val is not None and not self.is_(val)):
            raise BoundarySyntaxError(f'Expected {val or "token"} at token {self.i}')
        t=self.tokens[self.i];self.i+=1;return t
    def parse(self):
        x=self.or_()
        if self.i!=len(self.tokens): raise BoundarySyntaxError(f'Missing operator near {self.tokens[self.i:]}')
        return x
    def or_(self):
        xs=[self.xor_()]
        while self.is_('OR'): self.eat();xs.append(self.xor_())
        return combine('or',*xs)
    def xor_(self):
        # XOR stays a structural node, NOT booleanized: byte-wise XOR compared
        # against a mask has different semantics than boolean XOR. It never
        # anchors a positive state and counts as "possible" in satisfiability.
        xs=[self.and_()]
        while self.is_('XOR'): self.eat();xs.append(self.and_())
        return xs[0] if len(xs)==1 else Expr('xor',tuple(xs))
    def and_(self):
        xs=[self.unary()]
        while self.is_('AND'): self.eat();xs.append(self.unary())
        return combine('and',*xs)
    def unary(self):
        if self.is_('NOT'): self.eat();return negate(self.unary())
        if self.is_('('):
            self.eat();x=self.or_();self.eat(')')
            if self.is_('=','==','<>','!=','>=','<=','>','<'):
                # Parenthesized expression compared against something: the
                # left side's evaluation semantics are unknown (bit ops etc.);
                # keep the comparison verbatim as an opaque atom.
                op=self.eat()[1];typ2,b=self.eat()
                return Expr('opaque_cmp',(f'({x.text()}){op}{b}',))
            return x
        typ,a=self.eat()
        if a.upper() in ('TRUE','FALSE'): return TRUE if a.upper()=='TRUE' else FALSE
        if self.is_('=','==','<>','!=','>=','<=','>','<'):
            op=self.eat()[1];typ2,b=self.eat();n=integer(b);v=a
            if typ=='num' and typ2=='id':
                n=integer(a);v=b
                op={'<':'>','>':'<','<=':'>=','>=':'<='}.get(op,op)
            if n is None:
                # Symbol-symbol equality/inequality (incl. `x = TRUE`): opaque
                # atom, never a positive state anchor, kept verbatim in guards.
                if op in ('=','==','<>','!=') and re.fullmatch(IDENT,a) and re.fullmatch(IDENT,b):
                    return Expr('veq' if op in ('=','==') else 'vne',(symbol(a),symbol(b)))
                raise BoundarySyntaxError('Only symbol/integer comparisons supported')
            if not re.fullmatch(IDENT,v): raise BoundarySyntaxError('Only symbol/integer comparisons supported')
            if op in ('=','=='):return Expr('eq',(symbol(v),n))
            if op in ('<>','!='):return Expr('ne',(symbol(v),n))
            return Expr({'>':'gt','<':'lt','>=':'ge','<=':'le'}[op],(symbol(v),n))
        if typ!='id' or a.upper() in ('OR','AND'): raise BoundarySyntaxError('Expected signal or state comparison')
        return Expr('signal',(symbol(a),))

def parse(s: str) -> Expr: return Parser(s).parse()
def canonical(s: str) -> str:
    try:return repr(parse(s).key())
    except BoundarySyntaxError:return 'unparsed:'+re.sub(r'\s+','',str(s)).casefold()

def comparisons(x: Expr) -> list[Expr]:
    if x.kind in ('eq','ne'): return [x]
    return [a for child in x.args if isinstance(child,Expr) for a in comparisons(child)]

def positive_states(x: Expr) -> tuple[str,set[int]] | None:
    xs=list(x.args) if x.kind=='or' else [x]
    if not xs or any(a.kind!='eq' for a in xs):return None
    if len({a.args[0].casefold() for a in xs})!=1:return None
    return xs[0].args[0],{a.args[1] for a in xs}

def exit_states(x: Expr) -> tuple[str,set[int]] | None:
    """Recognize exactly NOT(S in finite set), including De Morgan and <> forms."""
    return positive_states(negate(x))

def substitute(x: Expr, variable: str, value: int) -> Expr:
    if x.kind in ('eq','ne') and x.args[0].casefold()==variable.casefold():
        ok=(x.args[1]==value);return TRUE if (ok if x.kind=='eq' else not ok) else FALSE
    if x.kind in ('and','or'):return combine(x.kind,*(substitute(a,variable,value) for a in x.args))
    if x.kind=='not':return negate(substitute(x.args[0],variable,value))
    return x

def compatible(x: Expr, variable: str, value: int) -> bool:
    """Conservative satisfiability for the supported Boolean/equality grammar.

    Never flatten equalities from different OR branches into one conjunction.
    A bounded search returns possible on budget exhaustion (not a proof).
    """
    y=substitute(x,variable,value);budget=[4096]
    def possible(todo,eq,neq,signals):
        while todo:
            budget[0]-=1
            if budget[0]<0:return True
            e,*rest=todo;todo=rest
            if e.kind=='const':
                if not e.args[0]:return False
            elif e.kind=='and':todo=list(e.args)+todo
            elif e.kind=='or':
                return any(possible([a]+todo,eq.copy(),{k:set(v) for k,v in neq.items()},signals.copy()) for a in e.args)
            elif e.kind in ('eq','ne'):
                var,n=e.args;var=var.casefold()
                if e.kind=='eq':
                    if (var in eq and eq[var]!=n) or n in neq.get(var,set()):return False
                    eq[var]=n
                else:
                    if eq.get(var)==n:return False
                    neq.setdefault(var,set()).add(n)
            elif e.kind=='signal' or (e.kind=='not' and e.args[0].kind=='signal'):
                v=e.args[0].casefold() if e.kind=='signal' else e.args[0].args[0].casefold()
                yes=e.kind=='signal'
                if v in signals and signals[v]!=yes:return False
                signals[v]=yes
            else:return True
        return True
    return possible([y],{},{},{})
