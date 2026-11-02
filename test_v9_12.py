import csv,json,random,tempfile,unittest
from pathlib import Path
from lxml import etree
from fb_transport import collect,required_signals
from successor_graph import collect as transitions, WireBody
from boundary_logic import parse,Expr
from atomic_boundaries import AtomicProjector,atomic,export_atomic
from atomic_evaluation import evaluate
from boundary_normalizer import empty_graph
from candidate_filter import filter_rows
from workflow import create,validate
from agent import run


def inp(name,ref):return f'<variable formalParameter="{name}"><connectionPointIn><connection refLocalId="{ref}"/></connectionPointIn></variable>'
def val(id,v):return f'<inVariable localId="{id}"><expression>{v}</expression></inVariable>'
def contact(id,ref,v,neg=False):return f'<contact localId="{id}" negated="{str(neg).lower()}"><connectionPointIn><connection refLocalId="{ref}"/></connectionPointIn><variable>{v}</variable></contact>'
def coil(id,ref,v,mode):return f'<coil localId="{id}" storage="{mode}"><connectionPointIn><connection refLocalId="{ref}" formalParameter="ENO"/></connectionPointIn><variable>{v}</variable></coil>'
def move(id,ref,data,target):return f'<block localId="{id}" typeName="MOVE"><inputVariables>{inp("EN",ref)}{inp("",data)}</inputVariables><outputVariables><variable formalParameter="ENO"><connectionPointOut/></variable><variable formalParameter=""><connectionPointOut><expression>{target}</expression></connectionPointOut></variable></outputVariables></block>'
def eq(id,ref,a,b):return f'<block localId="{id}" typeName="EQ"><inputVariables>{inp("EN",ref)}{inp("",a)}{inp("",b)}</inputVariables><outputVariables><variable formalParameter=""><connectionPointOut/></variable></outputVariables></block>'
def param(name):return f'<variable name="{name}"><type><BOOL/></type></variable>'


def fixture(marker='Ticket.Launch',arrival='Inbox.Ready',command='Drive.Shift',state='Cell.Phase[6]',a=17,b=24):
    definition=f'''<pou name="ParcelProtocol" pouType="functionBlock"><interface><inOutVars>{param('Marker')}{param('Drive')}</inOutVars><inputVars>{param('Arrived')}</inputVars></interface><body><LD><leftPowerRail localId="0"/>
    {contact('1','0','Marker',True)}{contact('2','1','Arrived',True)}
    {val('3','1')}{move('4','2','3','Internal.Stage')}{coil('5','4','Marker','set')}{coil('6','4','Drive','set')}
    {contact('7','0','Arrived')}{val('8','2')}{move('9','7','8','Internal.Stage')}{coil('10','9','Marker','reset')}
    </LD></body></pou>'''
    caller=f'''<pou name="Dispatch" pouType="program"><body><LD><leftPowerRail localId="0"/>
    {val('1',marker)}{val('2',arrival)}{val('3',command)}
    <block localId="4" typeName="ParcelProtocol" instanceName="ChannelA"><inputVariables>{inp('EN','0')}{inp('Marker','1')}{inp('Arrived','2')}{inp('Drive','3')}</inputVariables></block>
    </LD></body></pou>'''
    receiver=f'''<pou name="Receiver" pouType="program"><body><LD><leftPowerRail localId="0"/>
    {val('1',state)}{val('2',str(a))}{eq('3','0','1','2')}
    {contact('4','3',arrival)}{contact('5','4','Permit')}{val('6',str(b))}{move('7','5','6',state)}
    </LD></body></pou>'''
    return '<project xmlns="http://www.plcopen.org/xml/tc6_0200"><types><pous>'+definition+caller+receiver+'</pous></types></project>'

class V912CrossFB(unittest.TestCase):
    def scan(self,xml):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.xml';p.write_text(xml);return collect(p)
    def test_source_lifecycle_and_instance_not_name(self):
        m=self.scan(fixture());self.assertEqual(m['summary']['protocol_models'],1)
        self.assertEqual([(r['开始标签'],r['结束标签']) for r in m['candidates']],[('Ticket.Launch','Cell.Phase[6]=24')])
    def test_random_identifiers_and_steps(self):
        r=random.Random(24012)
        for _ in range(8):
            a,b=r.sample(range(40,5000),2);marker='Gate'+str(r.randrange(900))+'.Pulse';state='Unit'+str(r.randrange(900))+'.Stage[9]'
            m=self.scan(fixture(marker=marker,state=state,a=a,b=b))
            self.assertEqual(m['candidates'][0]['开始标签'],marker);self.assertEqual(m['candidates'][0]['结束标签'],f'{state}={b}')
    def test_no_definition_no_invented_mapping(self):
        x=fixture().replace('typeName="ParcelProtocol" instanceName','typeName="AbsentDefinition" instanceName')
        self.assertFalse(self.scan(x)['candidates'])
    def test_wrong_bound_arrival_not_cross_linked(self):
        x=fixture().replace('<expression>Inbox.Ready</expression>','<expression>OtherBox.Ready</expression>')
        self.assertFalse(self.scan(x)['candidates'])
    def test_negative_receiving_arrival_rejected(self):
        x=fixture().replace(contact('4','3','Inbox.Ready'),contact('4','3','Inbox.Ready',True))
        self.assertFalse(self.scan(x)['candidates'])
    def test_or_bypass_is_not_arrival_requirement(self):
        x=fixture().replace(move('7','5','6','Cell.Phase[6]'),contact('99','0','BypassPermit')+move('7','5','6','Cell.Phase[6]').replace('refLocalId="5"','refLocalId="5"/><connection refLocalId="99"'))
        m=self.scan(x);self.assertFalse(m['candidates']);self.assertTrue(any('all_OR' in r['reason'] for r in m['reviews']))
    def test_unknown_block_guard_never_EN_passthrough(self):
        x=fixture().replace(contact('5','4','Permit'),'<block localId="5" typeName="Opaque"><inputVariables>'+inp('EN','4')+'</inputVariables></block>')
        self.assertFalse(self.scan(x)['candidates'])
    def test_false_call_enable(self):
        x=fixture().replace('<block localId="4" typeName="ParcelProtocol"',val('99','FALSE')+'<block localId="4" typeName="ParcelProtocol"').replace(inp('EN','0')+inp('Marker','1'),inp('EN','99')+inp('Marker','1'))
        self.assertFalse(self.scan(x)['candidates'])
    def test_marker_reset_must_use_destination(self):
        x=fixture().replace(contact('7','0','Arrived'),contact('7','0','ResetButton'))
        self.assertFalse(self.scan(x)['candidates'])
    def test_marker_must_share_command_launch(self):
        x=fixture().replace(coil('6','4','Drive','set'),coil('6','9','Drive','set'))
        self.assertFalse(self.scan(x)['candidates'])
    def test_later_receiver_step_not_used_as_transport_end(self):
        extra=val('11','Cell.Phase[6]')+val('12','24')+eq('13','0','11','12')+contact('14','13','Inbox.Ready')+val('15','29')+move('16','14','15','Cell.Phase[6]')
        x=fixture().replace('</LD></body></pou></pous>',extra+'</LD></body></pou></pous>')
        m=self.scan(x);self.assertEqual(len(m['candidates']),1);self.assertEqual(m['summary']['later_receiver_phases_not_emitted'],1)
    def test_full_guard_kept_outside_atomic_cells(self):
        m=self.scan(fixture());self.assertIn('Permit',m['links'][0]['receiver_guard']);self.assertFalse(m['links'][0]['arrival_equals_end'])
    def test_singleton_contact_required_is_not_or_membership(self):
        self.assertIn(('a',True),required_signals(parse('A AND (B OR C)')))
        self.assertNotIn(('a',True),required_signals(parse('(A AND B) OR C')))
    def test_unrelated_duplicate_instance_is_not_merged(self):
        x=fixture();start=x.index('<pou name="Dispatch"');end=x.index('</pou>',start)+6
        other=x[start:end].replace('name="Dispatch"','name="OtherDispatch"').replace('Ticket.Launch','Other.Launch').replace('Inbox.Ready','Other.Arrived')
        x=x.replace('</pous>',other+'</pous>');m=self.scan(x)
        self.assertEqual(m['summary']['bound_instances'],2);self.assertEqual(len(m['candidates']),1)

class V912ElementaryAndWorkflow(unittest.TestCase):
    def test_boolean_is_elementary(self):self.assertIsNotNone(atomic('Ticket.Launch'))
    def test_compound_is_not_elementary(self):self.assertIsNone(atomic('Ticket.Launch AND Ready'))
    def test_signal_not_converted_to_step(self):
        p=AtomicProjector().project({'开始标签':'Ticket.Launch','结束标签':'Cell.Phase[6]=24'})
        self.assertTrue(p.strict_equivalence);self.assertEqual(p.rows[0]['开始标签'],'Ticket.Launch')
    def test_same_signal_cycle_review(self):
        self.assertFalse(AtomicProjector().project({'开始标签':'A','结束标签':'A'}).rows)
    def test_one_to_one_signal_pair_evaluation(self):
        r={'开始标签':'Ticket.Launch','结束标签':'Cell.Phase[6]=24'}
        rr,_,mm,_=evaluate([r],[r,r]);self.assertEqual(mm['unique_scored_gt'],1);self.assertEqual(rr[1]['最佳匹配'],'一对一匹配冲突')
    def test_signal_same_start_different_end_not_hit(self):
        p={'开始标签':'Ticket.Launch','结束标签':'Cell.Phase[6]=29'};g={**p,'结束标签':'Cell.Phase[6]=24'}
        rr,_,mm,_=evaluate([p],[g]);self.assertEqual(mm['unique_scored_gt'],0);self.assertEqual(rr[0]['最佳匹配'],'同起点/终点不同')
    def test_name_filter_one_endpoint_suffices(self):
        r={'开始标签':'Ticket.Launch','结束标签':'Cell.Phase[6]=24'}
        self.assertEqual(filter_rows([r],'Cell.Phase[*]'),[r])
    def test_nested_output_expression_and_blank_pins(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.xml';p.write_text(fixture());g=transitions(p)
            self.assertIn(('Cell.Phase[6]',17,24),[(x['variable'],x['from'],x['to']) for x in g['transitions']])
    def test_guard_size_budget(self):
        w=WireBody(etree.fromstring('<LD/>'));w.max_guard_nodes=12;x=parse('A')
        for _ in range(8):x=Expr('or',(x,Expr('and',(x,parse('B')))))
        self.assertGreater(w.guard_size(x),12)
    def test_full_prepare_grouping_and_assemble_preserve_signal(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);p=d/'x.xml';p.write_text(fixture());out=d/'run';m=create(p,out,batch_size=100,pattern='Cell.Phase[*]')
            seen=False
            for b in m['batches']:
                q=json.loads((out/b['input']).read_text());events=[e for e in q['events'] if e['command']=='cross_fb_transport']
                if events:
                    self.assertEqual(b['controller'],'Cell.Phase[6]');self.assertTrue(any(c['开始标签']=='Ticket.Launch' for c in q['candidates']));seen=True
                (out/b['output']).write_text(json.dumps({'batch_id':b['batch_id'],'steps':[],
                    'unresolved_events':[{'event_id':e['事件ID'],'reason':'synthetic workflow test'} for e in q['events']]}))
            self.assertTrue(seen);self.assertTrue(validate(out,write=True)['success'])
            with (out/'final_prediction_pairs.csv').open(encoding='utf-8-sig') as f:rr=list(csv.DictReader(f))
            self.assertIn(('Ticket.Launch','Cell.Phase[6]=24'),[(r['开始标签'],r['结束标签']) for r in rr])
    def test_no_gt_parameter(self):
        with self.assertRaises(TypeError):collect('not_a_file',gt='anything')

if __name__=='__main__':unittest.main()
