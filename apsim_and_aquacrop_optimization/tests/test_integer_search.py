"""Exercise real pymoo NSGA-II/III without invoking any simulation engine."""
import pytest

from agro.optimization.nsga2 import integer_search_settings, search_integer


@pytest.mark.parametrize('algorithm,count', [('nsga2',1),('nsga2',2),('nsga2',3),('auto',4),('auto',5),('auto',8),('auto',12),('nsga3',3)])
def test_integer_search_preserves_dimensions_bounds_and_constraints(algorithm,count):
    seen=[]
    def evaluate(vector):
        x,y=map(int,vector)
        assert x==vector[0] and y==vector[1] and 0<=x<7 and 0<=y<7
        seen.append((x,y))
        return {'F':[(x-(i%2)*6)**2+(y-(i%3)*3)**2 for i in range(count)],
                'G':[x+y-10]}
    settings={'population':max(10,count),'generations':3,'seed':42,'verbose':False,'algorithm':algorithm}
    result=search_integer([list(range(7)),list(range(7))],evaluate,settings,count,1)
    assert seen and (0,0) in seen
    assert result.F.shape[-1]==count
    assert (result.G<=0).all()
    seen_again=[]
    def repeated(vector):
        seen_again.append(tuple(map(int,vector)))
        x,y=map(int,vector)
        return {'F':[(x-(i%2)*6)**2+(y-(i%3)*3)**2 for i in range(count)],'G':[x+y-10]}
    search_integer([list(range(7)),list(range(7))],repeated,settings,count,1)
    assert seen_again==seen


def test_reference_direction_budget_and_invalid_settings_fail_before_evaluation():
    base={'population':10,'generations':4,'seed':42,'algorithm':'auto'}
    resolved=integer_search_settings(base,4)
    assert resolved['algorithm']=='nsga3'
    assert resolved['reference_partitions']==2 and resolved['reference_direction_count']==10
    assert 'reference_partitions' not in base
    for settings,count in [({**base,'population':3},4),
                           ({**base,'reference_partitions':3},4),
                           ({**base,'algorithm':'nsga3'},1),
                           ({**base,'generations':0},2),
                           ({**base,'algorithm':'bad'},2)]:
        def forbidden(_):raise AssertionError('Invalid search started evaluating')
        with pytest.raises(ValueError):search_integer([[0,1]],forbidden,settings,count,0)
