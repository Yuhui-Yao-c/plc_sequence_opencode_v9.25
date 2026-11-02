import unittest
from lxml import etree
from legacy_agent import parse_block,paths_to,COND_RE

class CoreTests(unittest.TestCase):
    def test_eq_operands_reversed(self):
        data='''<LD><inVariable localId="1"><expression>10</expression></inVariable><inVariable localId="2"><expression>S.Step</expression></inVariable><block localId="3" typeName="EQ"><inputVariables><variable formalParameter=""><connectionPointIn><connection refLocalId="1"/></connectionPointIn></variable><variable formalParameter=""><connectionPointIn><connection refLocalId="2"/></connectionPointIn></variable></inputVariables></block></LD>'''
        ld=etree.fromstring(data); nodes={x.get('localId'):x for x in ld}
        self.assertEqual(paths_to('3',nodes)[0],'10 = S.Step')
    def test_output_expression_nesting(self):
        e=etree.fromstring('<block typeName="MOVE"><outputVariables><variable formalParameter=""><connectionPointOut><expression>Ctrl.Step[2]</expression></connectionPointOut></variable></outputVariables></block>')
        self.assertEqual(parse_block(e)[1][0]['expr'],'Ctrl.Step[2]')
    def test_step_match(self):
        self.assertEqual(COND_RE.findall('10 = S.Step'),'[]' if False else [])
        self.assertEqual(COND_RE.findall('S.Step = 10'),[('S.Step','10')])

if __name__=='__main__':unittest.main()

class OptimizationTests(unittest.TestCase):
    def test_dedup(self):
        from enhance import unique
        r={'开始标签':'A=1','结束标签':'A=2','动作':'X','控制器':'A'}
        self.assertEqual(len(unique([r,r])),1)
    def test_guard_candidate(self):
        from enhance import extract_guards
        ts=[{'controller':'Machine.Step','from':10,'to':20,'guard':'Machine.Step = 10 AND Axis.PosOK[1]','pou':'Station/main','evidence':'13','uncertain':False}]
        r=extract_guards(ts,[])
        self.assertEqual(r[0]['结束标签'],'Machine.Step=10 AND Axis.PosOK[1]')
