# tests/test_roll.py
import pytest
from utils.roll_logic import execute_roll

VALID_CASES = [
    # Base Dice
    "d20", "1d20", "20d6", "d%", "d6+d8", "2d6+5", "2d6-3", "2d6*2", "-1d6",
    
    # Rerolls
    "2d6r1", "2d6ro1", "2d6r<3", "2d6r[1,2]", "2d6r[1,2]o", "4d6r6", "2d6r>5r<2",
    
    # Exploding
    "4d6!", "4d6!!", "4d6!p", "4d6!>4", "4d6!3", "4d6!!p", "4d6![4+: 1d6+1d4]", "4d6![6: 2d6, 5: 1d8]", "4d6!!>4", "4d6!p>5",
    
    # Keep / Drop
    "4d6kh3", "4d6k3", "4d6kl1", "4d6dh1", "4d6dl1", "4d6d1", "4d6kh5", "4d6kh3dl1", "4d6k[gt3]", "4d6k[neq1,neq6]", "4d6k[gte3,lte5]", "4d6k[gt10]",
    
    # Successes & Failures
    "5d10>=8", "5d10>8", "5d10=6", "5d10<=3", "5d10!=5", "5d10>=8f1", "5d10>=8f<=2", "5d10>=8f>=8", "5d10>=8+2", "5d10!>=8",
    
    # Conditional / Basic Math
    "4d6&2+3", "4d6&0+5", "4d6&10+1", "2d6+10", "2d6-10", "2d6+10-3",
    
    # Positional Indexing
    "5d10[0]", "5d10[4]", "5d10[2]", "5d10[-1]", "5d10[1:3]", "5d10[0:5]", "5d10[2:2]", "5d10[0]+5d10[4]", "(5d10)[0]",
    
    # Set-Based Patterns
    "4d6{3-of-a-kind: +5}", "5d6{3-of-a-kind: +5, 4-of-a-kind: +15}", "5d6{straight: x2}", "5d6{pair: +2, 2-pair: +4}", "4d4{straight: x2}",
    
    # Dependent / Conditional Dice
    "4d6d[r=1: 1d4]", "4d6kh3[any=1: +1d6]", "4d6[all=6: x2]", "4d6[r=6: -2]", "4d6[count>=3: 1d8]", "4d6kh3[r=1: 1d4][r=6: 1d8]", "1d6[r=6: 1d6[r=6: 1d6]]",
    
    # Cross-Pool Opposed
    "(4d6) vs (3d8)", "(3d6) vs (3d6)", "(1d6) vs (1d6)", "(4d6) vs (2d8)", "(4d6+2) vs (3d8)", "(4d6kh3) vs (3d8)",
    
    # Temporal / Sequencing
    "[1d6 -> 1d8 -> 1d10]", "[1d6 -> d8]", "[2d4 -> d6 -> d8]", "[1d1 -> d100]", "[1d6 -> d6 -> d6 -> d6 -> d6 -> d6]", "[0d6 -> d8]",
    
    # Macros / Functions
    "advantage(n, s) = nd20kh1+s; advantage(2, 3)", "adv(n) = nd20kh1; adv(2)+5", "f(n) = nd6; f(f(2))", "f(n, m) = nd6+m; f(3, 2)+f(2, 5)", "f(n) = nd20; g(n) = ndf(1)+3",
    
    # Weighted Face Mapping
    "1d6{1:10, 2:20, 3:30, 4:40, 5:50, 6:100}", "1d6{1:10, 6:100}", "1d6{}", "2d6{1:10, 2:20, 3:30, 4:40, 5:50, 6:100}", "1d6{1:10}kh1", "1d6{1:-5, 6:20}", "1d6{1:10}>=15", "1d6{1:10, 2:10, 3:10, 4:10, 5:10, 6:10}!",
    
    # Label / Variable Reference
    "(2d6+3):hit", "(2d6+3):hit, if hit>10 then 2d8 else 1d8", "(1d20):atk, (2d6):dmg, if atk>=15 then dmg*2", "(1d6):a, (1d6):b, a+b", "(1d6):a, a+a", "(1d6):a, (a):b", "(2d6):hit, if hit>10 then hit+5 else hit-2",
    
    # Combination / Interaction Cases
    "4d6kh3!", "4d6!kh3", "4d6r1kh3", "4d6kh3r1", "4d6>=5f1", "4d6kh3>=5", "4d6!>=5", "(4d6) vs (3d8) >= 5", "[1d4 -> d6]kh1", "4d6{pair: +2}kh3", "2d6+3:bonus", "4d6[0]kh1"
]

ERROR_CASES = [
    "1d0",
    "1d1r1", 
    "1d1!", 
    "5d10[5]", 
    "advantage(2, 3)", 
    "f(n) = nd6+f(n-1); f(3)", 
    "f(n) = nd6; f(-1)", 
    "f(n) = nd6; f(2.5)", 
    "[1d6 -> d0]", 
    ":hit", 
    "(1d6):a, if a>3 then (1d8):a"
]

@pytest.mark.parametrize("expr", VALID_CASES)
def test_valid_dice_expressions(expr):
    res = execute_roll(expr)
    assert "total" in res
    assert "breakdown" in res
    assert isinstance(res["total"], (int, float))
    assert isinstance(res["breakdown"], str)

def test_multi_die_roll_breakdown_keeps_each_die_separate():
    res = execute_roll("2d1")
    assert res["total"] == 2
    assert res["breakdown"] == "[1, 1]"

@pytest.mark.parametrize("expr", ERROR_CASES)
def test_invalid_dice_expressions(expr):
    with pytest.raises(ValueError):
        execute_roll(expr)
