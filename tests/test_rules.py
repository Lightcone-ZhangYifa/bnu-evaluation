from decimal import Decimal
from datetime import date
import pytest
from app.rules import calculate, local_score, within_period, academic_average, bnu_template, validate_tree, category_path
from app.schemas import Annotation, PublicRow, RuleSet, Node, decimal

def node(id,parent=None,**kw):return Node(id=id,parent=parent,name=id,**kw).model_dump()
def item(id,cat,score):return dict(id=id,category_id=cat,score=score)

def test_exact_decimals_and_trace():
    r=calculate([node('root')],[item('a','root','0.1'),item('b','root','0.2')]);assert r['total']=='0.3';assert r['nodes'][0]['raw_sum']=='0.3'

def test_caps_do_not_mutate_input():
    inputs=[item('a','sports','1.6'),item('b','sports','0.9')]
    r=calculate([node('root',cap='7'),node('sports','root',cap='2')],inputs)
    assert r['total']=='2' and inputs[0]['score']=='1.6';assert r['nodes'][1]['raw_sum']=='2.5';assert r['nodes'][1]['cap_delta']=='0.5'

def test_max_at_arbitrary_nested_level():
    r=calculate([node('root'),node('jobs','root',aggregate='max',cap='3'),node('one','jobs'),node('two','jobs')],[item('a','one','1.9'),item('b','two','2.85')])
    assert r['total']=='2.85';assert r['nodes'][1]['selected_ids']==['two'];assert r['nodes'][1]['raw_sum']=='4.75'

def test_max_ties_record_all_but_count_once():
    r=calculate([node('root',aggregate='max')],[item('z','root','1.9'),item('a','root','1.9')]);assert r['total']=='1.9';assert r['nodes'][0]['selected_ids']==['a'];assert r['nodes'][0]['tied_ids']==['a','z']

@pytest.mark.parametrize('policy,expected',[('signed','2'),('separate','1')])
def test_cap_and_negative_order_is_explicit(policy,expected):
    r=calculate([node('r',cap='2',negative_policy=policy)],[item('a','r','3'),item('b','r','-1')]);assert r['total']==expected

def test_negative_not_silently_floored():
    assert calculate([node('r')],[item('a','r','-2')])['total']=='-2'

def test_no_leaf_no_score():
    with pytest.raises(ValueError):calculate([node('r'),node('leaf','r')],[item('bad','r','1')])

def test_deep_tree_is_iterative():
    nodes=[node(str(i),str(i-1) if i else None) for i in range(6000)]
    assert calculate(nodes,[item('x','5999','0.5')])['total']=='0.5'
    assert len(category_path(nodes,'5999'))==6000

@pytest.mark.parametrize('nodes',[[node('x'),node('x')],[node('r'),node('a','missing')],[node('r'),node('a','b'),node('b','a')],[node('a','b'),node('b','a')]])
def test_reject_malformed_trees(nodes):
    with pytest.raises(ValueError):validate_tree(nodes)

def test_required_input_missing_not_zero_truth():
    r=calculate([node('r'),node('m','r',required=True),node('a','r')],[])
    assert not r['complete'] and r['missing_categories']==['m']
    assert calculate([node('r'),node('m','r',required=True)],[item('x','m','0')])['complete']

@pytest.mark.parametrize('v',['NaN','Infinity','-Infinity','1e10','100000000','1.1234567891234','--1',''])
def test_invalid_score_rejected(v):
    with pytest.raises(ValueError):decimal(v)

@pytest.mark.parametrize('d,valid',[('2026-08-31',True),('2025-09-01',True),('2026-09-01',False),('2025-08-31',False),('2026',False),('2026-08',True),('2026-02-30',False),(None,False),('2026-9',False)])
def test_dates_are_bounded_intervals(d,valid):
    assert within_period(d,'2025-09-01','2026-08-31')==valid

def test_local_coefficients_and_division():
    assert local_score('1',['0.8'])==Decimal('.8')
    assert local_score('2',['0.8'],'2')==Decimal('.8')
    assert local_score('2',[],'3')>Decimal('.66666666')

def test_annotation_formula_cannot_lie():
    with pytest.raises(ValueError):Annotation(status='valid',category_id='x',rule_item='x',scenario='x',rule_source='p1',score='1',base_score='1',factors=['0.8'])
    with pytest.raises(ValueError):Annotation(status='manual',score='1')
    with pytest.raises(ValueError):Annotation(status='valid',score='1',support_for='x')

def test_academic_grade_rules():
    c=[dict(id='a',name='数学',credits='2',scheme='numeric'),dict(id='b',name='等级',credits='1',scheme='five'),dict(id='c',name='两级',credits='4',scheme='two')]
    r=academic_average(c,[dict(course_id='a',score='80'),dict(course_id='b',score='优秀'),dict(course_id='c',score='合格')]);assert r['average']=='85'
    r=academic_average(c,[dict(course_id='b',score='优秀')]);assert Decimal(r['average'])<32

@pytest.mark.parametrize('grade',['-1','101','NaN','A'])
def test_academic_invalid_numeric(grade):
    with pytest.raises(ValueError):academic_average([dict(id='a',name='课程',credits='1',scheme='numeric')],[dict(course_id='a',score=grade)])

def test_bnu_multi_level_actual_rules():
    nodes=bnu_template()['nodes'];inp=[item('m','moral','5'),item('a','academic','90'),item('x','competition.professional','4'),item('x2','competition.professional','1'),item('job1','positions','1.9'),item('job2','positions','2.85'),item('sport1','activities.sports','1.6'),item('sport2','activities.sports','0.9'),item('large1','activities.large','0.3'),item('large2','activities.large','0.3')]
    r=calculate(nodes,inp);assert r['total']=='91.75';by={n['id']:n for n in r['nodes']};assert by['competition.professional']['value']=='4';assert by['activities.sports']['value']=='2';assert by['activities.large']['raw_sum']=='0.6';assert by['activities.large']['value']=='0.5'
