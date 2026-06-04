# roll_logic.py
# Advanced AST-based Dice Roller for Flurazide bot

import random
import re
from typing import Dict, List, Tuple, Optional, Any, Union

# Constants for dice limits
MAX_DICE = 100
MAX_SIDES = 1000
MAX_RECURSION_DEPTH = 10

# Helper to check conditions
def match_condition(value: Union[int, float], op: str, target: Union[int, float]) -> bool:
    if op in ('=', '=='):
        return value == target
    elif op == '<':
        return value < target
    elif op == '>':
        return value > target
    elif op == '<=':
        return value <= target
    elif op == '>=':
        return value >= target
    elif op == '!=':
        return value != target
    return False

# AST Nodes
class ASTNode:
    pass

class ConstantNode(ASTNode):
    def __init__(self, value: Union[int, float]):
        self.value = value
    def __repr__(self):
        return f"ConstantNode({self.value})"

class VariableNode(ASTNode):
    def __init__(self, name: str):
        self.name = name
    def __repr__(self):
        return f"VariableNode({self.name})"

class DiceNode(ASTNode):
    def __init__(self, count: ASTNode, sides: ASTNode, modifiers: List[Any] = None):
        self.count = count
        self.sides = sides
        self.modifiers = modifiers or []
    def __repr__(self):
        return f"DiceNode({self.count}d{self.sides}, mods={self.modifiers})"

class BinaryOpNode(ASTNode):
    def __init__(self, op: str, left: ASTNode, right: ASTNode):
        self.op = op
        self.left = left
        self.right = right
    def __repr__(self):
        return f"BinaryOpNode({self.op}, {self.left}, {self.right})"

class UnaryOpNode(ASTNode):
    def __init__(self, op: str, expr: ASTNode):
        self.op = op
        self.expr = expr
    def __repr__(self):
        return f"UnaryOpNode({self.op}, {self.expr})"

class LabelAssignmentNode(ASTNode):
    def __init__(self, expr: ASTNode, label: str):
        self.expr = expr
        self.label = label
    def __repr__(self):
        return f"LabelAssignmentNode({self.expr} : {self.label})"

class ConditionalNode(ASTNode):
    def __init__(self, cond: ASTNode, true_expr: ASTNode, false_expr: Optional[ASTNode] = None):
        self.cond = cond
        self.true_expr = true_expr
        self.false_expr = false_expr
    def __repr__(self):
        return f"ConditionalNode(if {self.cond} then {self.true_expr} else {self.false_expr})"

class MacroDefNode(ASTNode):
    def __init__(self, name: str, params: List[str], body: ASTNode):
        self.name = name
        self.params = params
        self.body = body
    def __repr__(self):
        return f"MacroDefNode({self.name}({', '.join(self.params)}) = {self.body})"

class MacroCallNode(ASTNode):
    def __init__(self, name: str, args: List[ASTNode]):
        self.name = name
        self.args = args
    def __repr__(self):
        return f"MacroCallNode({self.name}({self.args}))"

class SequenceNode(ASTNode):
    def __init__(self, exprs: List[ASTNode]):
        self.exprs = exprs
    def __repr__(self):
        return f"SequenceNode({self.exprs})"

class IndexNode(ASTNode):
    def __init__(self, expr: ASTNode, index_expr: ASTNode):
        self.expr = expr
        self.index_expr = index_expr
    def __repr__(self):
        return f"IndexNode({self.expr}[{self.index_expr}])"

class SliceNode(ASTNode):
    def __init__(self, expr: ASTNode, start_expr: Optional[ASTNode], end_expr: Optional[ASTNode]):
        self.expr = expr
        self.start_expr = start_expr
        self.end_expr = end_expr
    def __repr__(self):
        return f"SliceNode({self.expr}[{self.start_expr}:{self.end_expr}])"

# Modifiers
class RerollModifier:
    def __init__(self, once: bool, condition_op: Optional[str] = None, condition_val: Optional[Union[int, float]] = None, face_list: Optional[List[int]] = None):
        self.once = once
        self.condition_op = condition_op
        self.condition_val = condition_val
        self.face_list = face_list
    def __repr__(self):
        return f"RerollMod(once={self.once}, op={self.condition_op}, val={self.condition_val}, list={self.face_list})"

class ExplodeModifier:
    def __init__(self, compound: bool, penetrate: bool, condition_op: Optional[str] = None, condition_val: Optional[Union[int, float]] = None, cascades: List[Tuple[Any, ASTNode]] = None):
        self.compound = compound
        self.penetrate = penetrate
        self.condition_op = condition_op
        self.condition_val = condition_val
        self.cascades = cascades or []
    def __repr__(self):
        return f"ExplodeMod(compound={self.compound}, penetrate={self.penetrate}, op={self.condition_op}, val={self.condition_val}, cascades={self.cascades})"

class KeepDropModifier:
    def __init__(self, action: str, count: Optional[ASTNode] = None, predicates: List[Tuple[str, int]] = None):
        self.action = action
        self.count = count
        self.predicates = predicates or []
    def __repr__(self):
        return f"KeepDropMod({self.action}, count={self.count}, predicates={self.predicates})"

class SuccessModifier:
    def __init__(self, op: str, val: ASTNode, failure_op: Optional[str] = None, failure_val: Optional[ASTNode] = None):
        self.op = op
        self.val = val
        self.failure_op = failure_op
        self.failure_val = failure_val
    def __repr__(self):
        return f"SuccessMod(op={self.op}, val={self.val}, fail_op={self.failure_op}, fail_val={self.failure_val})"

class PatternModifier:
    def __init__(self, patterns: List[Tuple[str, str, ASTNode]]):
        self.patterns = patterns
    def __repr__(self):
        return f"PatternMod({self.patterns})"

class DependentModifier:
    def __init__(self, condition_type: str, condition_op: Optional[str], condition_val: Optional[int], action_expr: ASTNode):
        self.condition_type = condition_type
        self.condition_op = condition_op
        self.condition_val = condition_val
        self.action_expr = action_expr
    def __repr__(self):
        return f"DependentMod({self.condition_type}{self.condition_op}{self.condition_val} -> {self.action_expr})"

class WeightedModifier:
    def __init__(self, mapping: Dict[int, int]):
        self.mapping = mapping
    def __repr__(self):
        return f"WeightedMod({self.mapping})"

class ConditionalMathModifier:
    def __init__(self, count: ASTNode, op: str, value_expr: ASTNode):
        self.count = count
        self.op = op
        self.value_expr = value_expr
    def __repr__(self):
        return f"ConditionalMathMod(&{self.count}{self.op}{self.value_expr})"

class IndexModifier:
    def __init__(self, index_expr: ASTNode):
        self.index_expr = index_expr
    def __repr__(self):
        return f"IndexModifier({self.index_expr})"

class SliceModifier:
    def __init__(self, start_expr: Optional[ASTNode], end_expr: Optional[ASTNode]):
        self.start_expr = start_expr
        self.end_expr = end_expr
    def __repr__(self):
        return f"SliceModifier({self.start_expr}:{self.end_expr})"

# Token representation
class Token:
    def __init__(self, type_: str, value: str, pos: int):
        self.type = type_
        self.value = value
        self.pos = pos
    def __repr__(self):
        return f"Token({self.type}, {repr(self.value)}, {self.pos})"

# Tokenizer
def tokenize(expression: str) -> List[Token]:
    tokens = []
    i = 0
    n = len(expression)
    expression = expression.strip()
    
    while i < n:
        c = expression[i]
        
        if c.isspace():
            i += 1
            continue
            
        if c == '&':
            tokens.append(Token('AMP', '&', i))
            i += 1
            continue
            
        if i + 1 < n and expression[i:i+2] == '->':
            tokens.append(Token('ARROW', '->', i))
            i += 2
            continue
            
        if i + 1 < n and expression[i:i+2] in ('>=', '<=', '==', '!='):
            tokens.append(Token('COMP', expression[i:i+2], i))
            i += 2
            continue
            
        if c == '!':
            if i + 1 < n and expression[i+1] == '!':
                tokens.append(Token('DBL_BANG', '!!', i))
                i += 2
            else:
                tokens.append(Token('BANG', '!', i))
                i += 1
            continue
            
        if c in ('+', '-', '*', '/', '^', '%', '(', ')', '[', ']', '{', '}', ':', '=', ',', ';', '<', '>'):
            if c in ('<', '>'):
                tokens.append(Token('COMP', c, i))
            else:
                tokens.append(Token(c, c, i))
            i += 1
            continue
            
        # Match specific patterns with dashes/digits first to avoid splitting
        pat_match = re.match(r'^(3-of-a-kind|4-of-a-kind|two-pair|2-pair|full-house)', expression[i:], re.IGNORECASE)
        if pat_match:
            val = pat_match.group(1)
            tokens.append(Token('IDENTIFIER', val, i))
            i += len(val)
            continue

        if c.isdigit():
            start = i
            while i < n and expression[i].isdigit():
                i += 1
            if i < n and expression[i] == '.' and i + 1 < n and expression[i+1].isdigit():
                i += 1
                while i < n and expression[i].isdigit():
                    i += 1
            tokens.append(Token('NUMBER', expression[start:i], start))
            continue
            
        if c.isalpha() or c == '_':
            start = i
            # No dashes allowed in general identifiers to prevent subtraction clashes (e.g. hit-2)
            while i < n and (expression[i].isalpha() or expression[i] == '_'):
                # Stop before 'd' if it looks like a die separator:
                #   - Exactly 1 char accumulated before 'd' (e.g. 'n' in 'nd20')
                #   - Followed by: digit/% (e.g. nd20, nd%)
                #   - Multi-char names like 'adv', 'advantage' won't match (accumulated > 1 char
                #     at that point, or 'd' isn't followed by digit/%)
                if (i - start == 1) and expression[i].lower() == 'd' and i + 1 < n:
                    nxt = expression[i + 1]
                    if nxt.isdigit() or nxt == '%':
                        break
                i += 1
            val = expression[start:i]
            val_lower = val.lower()
            
            if val_lower == 'if':
                tokens.append(Token('IF', val, start))
            elif val_lower == 'then':
                tokens.append(Token('THEN', val, start))
            elif val_lower == 'else':
                tokens.append(Token('ELSE', val, start))
            elif val_lower == 'vs':
                tokens.append(Token('VS', val, start))
            elif val_lower in ('kh', 'kl', 'dh', 'dl', 'k', 'd', 'ro', 'r', 'f'):
                tokens.append(Token(val_lower.upper(), val, start))
            else:
                tokens.append(Token('IDENTIFIER', val, start))
            continue
            
        raise ValueError(f"Unexpected character '{c}' at position {i}")
        
    return tokens

# Parser
class DiceParser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def current_token(self) -> Optional[Token]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def match(self, type_: str) -> Optional[Token]:
        tok = self.current_token()
        if tok and tok.type == type_:
            self.pos += 1
            return tok
        return None

    def expect(self, type_: str) -> Token:
        tok = self.match(type_)
        if not tok:
            curr = self.current_token()
            raise ValueError(f"Expected token of type {type_}, got {curr} at pos {self.pos}")
        return tok

    def get_precedence(self, tok: Token) -> int:
        return PRECEDENCE.get(tok.type, 0)

    def parse_statements(self) -> ASTNode:
        nodes = []
        while True:
            nodes.append(self.parse_statement())
            tok = self.current_token()
            if tok and tok.type in (',', ';'):
                self.pos += 1
                if not self.current_token():
                    break
            else:
                break
        if len(nodes) == 1:
            return nodes[0]
        return SequenceNode(nodes)

    def parse_statement(self) -> ASTNode:
        tok = self.current_token()
        if tok and tok.type in ('IDENTIFIER', 'F', 'K', 'R', 'KH', 'KL', 'DH', 'DL', 'RO') and self.pos + 1 < len(self.tokens) and self.tokens[self.pos+1].type == '(':
            # Scan from AFTER the '(' (pos+2) so the opening paren is not double-counted.
            i = self.pos + 2
            depth = 1
            has_eq = False
            while i < len(self.tokens):
                t = self.tokens[i]
                if t.type == '(':
                    depth += 1
                elif t.type == ')':
                    depth -= 1
                    if depth == 0:
                        if i + 1 < len(self.tokens) and self.tokens[i+1].type == '=':
                            has_eq = True
                        break
                i += 1
            if has_eq:
                name_tok = self.expect(tok.type)
                self.expect('(')
                params = []
                if self.current_token() and self.current_token().type != ')':
                    params.append(self.expect('IDENTIFIER').value)
                    while self.match(','):
                        params.append(self.expect('IDENTIFIER').value)
                self.expect(')')
                self.expect('=')
                body = self.parse_expression()
                return MacroDefNode(name_tok.value, params, body)
                
        return self.parse_expression()

    def parse_expression(self, precedence: int = 0) -> ASTNode:
        left = self.parse_prefix()
        while True:
            tok = self.current_token()
            if not tok:
                break
            op_prec = self.get_precedence(tok)
            if op_prec <= precedence:
                break
            left = self.parse_infix(left, tok)
        return left

    def parse_prefix(self) -> ASTNode:
        tok = self.current_token()
        if not tok:
            raise ValueError("Unexpected end of expression")
            
        if tok.type == 'NUMBER':
            self.pos += 1
            val = float(tok.value)
            if val == int(val):
                val = int(val)
            return ConstantNode(val)
            
        if tok.type == 'D':
            self.pos += 1
            sides = self.parse_sides()
            node = DiceNode(ConstantNode(1), sides)
            self.parse_dice_modifiers(node)
            return node
            
        if tok.type in ('IDENTIFIER', 'F', 'K', 'R', 'KH', 'KL', 'DH', 'DL', 'RO'):
            self.pos += 1
            if self.match('('):
                args = []
                if self.current_token() and self.current_token().type != ')':
                    args.append(self.parse_expression())
                    while self.match(','):
                        args.append(self.parse_expression())
                self.expect(')')
                return MacroCallNode(tok.value, args)
            return VariableNode(tok.value)
            
        if tok.type == 'IF':
            self.pos += 1
            cond = self.parse_expression()
            self.expect('THEN')
            true_expr = self.parse_expression()
            false_expr = None
            if self.match('ELSE'):
                false_expr = self.parse_expression()
            return ConditionalNode(cond, true_expr, false_expr)
            
        if tok.type == '(':
            self.pos += 1
            expr = self.parse_statements()
            self.expect(')')
            return expr
            
        if tok.type in ('-', '+'):
            self.pos += 1
            expr = self.parse_expression(8)
            return UnaryOpNode(tok.type, expr)
            
        if tok.type == '[':
            self.pos += 1
            exprs = []
            exprs.append(self.parse_expression())
            while self.match('ARROW'):
                exprs.append(self.parse_expression())
            self.expect(']')
            # Attach modifiers to the final DiceNode in the sequence if present
            if exprs and isinstance(exprs[-1], DiceNode):
                self.parse_dice_modifiers(exprs[-1])
            return SequenceNode(exprs)
            
        raise ValueError(f"Unexpected token {tok} in prefix position")

    def parse_infix(self, left: ASTNode, tok: Token) -> ASTNode:
        self.pos += 1
        prec = self.get_precedence(tok)
        
        if tok.type in ('+', '-', '*', '/', '%', '^', 'VS'):
            right = self.parse_expression(prec)
            return BinaryOpNode(tok.type, left, right)
            
        if tok.type == 'COMP':
            right = self.parse_expression(prec)
            return BinaryOpNode(tok.value, left, right)
            
        if tok.type == ':':
            label_tok = self.expect('IDENTIFIER')
            return LabelAssignmentNode(left, label_tok.value)
            
        if tok.type == '[':
            mod = self.parse_bracket_modifier(left)
            if isinstance(mod, DependentModifier):
                if isinstance(left, DiceNode):
                    left.modifiers.append(mod)
                    return left
                return mod
            elif isinstance(mod, SliceModifier):
                return SliceNode(left, mod.start_expr, mod.end_expr)
            else:
                return IndexNode(left, mod.index_expr)
                    
        if tok.type == 'D':
            sides = self.parse_sides()
            node = DiceNode(left, sides)
            self.parse_dice_modifiers(node)
            return node
            
        raise ValueError(f"Unexpected infix token {tok}")

    def parse_sides(self) -> ASTNode:
        tok = self.current_token()
        if tok and tok.type == '%':
            self.pos += 1
            return ConstantNode(100)
        # Precedence 10 (same as D) prevents a second D from being greedily consumed
        # as infix here — that D belongs to parse_dice_modifiers (e.g. 4d6d[r=1: 1d4]).
        return self.parse_expression(10)

    def parse_dice_modifiers(self, dice_node: DiceNode):
        while True:
            tok = self.current_token()
            if not tok:
                break
            if tok.type in ('R', 'RO'):
                self.pos += 1
                dice_node.modifiers.append(self.parse_reroll_modifier(tok))
            elif tok.type in ('BANG', 'DBL_BANG'):
                self.pos += 1
                dice_node.modifiers.append(self.parse_explode_modifier(tok, dice_node.sides))
            elif tok.type in ('KH', 'KL', 'DH', 'DL', 'K'):
                self.pos += 1
                dice_node.modifiers.append(self.parse_keepdrop_modifier(tok))
            elif tok.type == 'D':
                # Bare 'D' (no number/[ suffix) after dice = drop lowest, stop here.
                # 'D' followed by '[' means dl1 (drop one), then let the '[' be handled as a
                # bracket modifier on the next loop iteration.
                next_tok = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
                if next_tok and next_tok.type == '[':
                    # Consume just the 'D', emit a default dl1. The '[' stays for next iteration.
                    self.pos += 1
                    dice_node.modifiers.append(KeepDropModifier(action='dl', count=ConstantNode(1)))
                else:
                    break
            elif tok.type == 'AMP':
                self.pos += 1
                dice_node.modifiers.append(self.parse_conditional_math_modifier())
            elif tok.type in ('COMP', '='):
                self.pos += 1
                dice_node.modifiers.append(self.parse_success_modifier(tok))
            elif tok.type == '{':
                self.pos += 1
                dice_node.modifiers.append(self.parse_brace_modifier())
            elif tok.type == '[':
                self.pos += 1
                dice_node.modifiers.append(self.parse_bracket_modifier(dice_node))
            else:
                break

    def parse_bracket_modifier(self, left: ASTNode) -> Any:
        next_tok = self.current_token()
        is_dep = False
        if next_tok and next_tok.type in ('IDENTIFIER', 'K', 'D', 'R', 'F'):
            val_lower = next_tok.value.lower()
            if val_lower in ('r', 'sum', 'any', 'all', 'count'):
                i = self.pos + 1
                if i < len(self.tokens) and self.tokens[i].type in ('COMP', '='):
                    i += 1
                    if i < len(self.tokens) and self.tokens[i].type == 'NUMBER':
                        i += 1
                        if i < len(self.tokens) and self.tokens[i].type == ':':
                            is_dep = True
        
        if is_dep:
            cond_tok = self.expect(next_tok.type)
            cond_type = cond_tok.value.lower()
            op_tok = self.current_token()
            if op_tok and op_tok.type in ('COMP', '='):
                op = op_tok.value
                self.pos += 1
            else:
                op = '='
            val_tok = self.expect('NUMBER')
            val = int(float(val_tok.value))
            self.expect(':')
            # Handle 'x N' prefix (multiply-by) inside dependent bracket actions, e.g. [all=6: x2]
            action_tok = self.current_token()
            if action_tok and action_tok.type == 'IDENTIFIER' and action_tok.value.lower() == 'x':
                self.pos += 1  # consume 'x'
                multiplier = self.parse_expression()
                action_expr = BinaryOpNode('*', ConstantNode(1), multiplier)
            else:
                action_expr = self.parse_expression()
            self.expect(']')
            return DependentModifier(cond_type, op, val, action_expr)
        else:
            start_expr = None
            end_expr = None
            is_slice = False
            # Limit precedence inside slice brackets to avoid absorbing label assignments
            if self.current_token() and self.current_token().type != ':':
                start_expr = self.parse_expression(5)
            if self.match(':'):
                is_slice = True
                if self.current_token() and self.current_token().type != ']':
                    end_expr = self.parse_expression(5)
            self.expect(']')
            if is_slice:
                return SliceModifier(start_expr, end_expr)
            else:
                if start_expr is None:
                    raise ValueError("Empty index bracket is not allowed")
                return IndexModifier(start_expr)

    def parse_reroll_modifier(self, tok: Token) -> RerollModifier:
        once = (tok.type == 'RO')
        
        if self.match('['):
            faces = []
            faces.append(int(float(self.expect('NUMBER').value)))
            while self.match(','):
                faces.append(int(float(self.expect('NUMBER').value)))
            self.expect(']')
            trailing_tok = self.current_token()
            if trailing_tok and (trailing_tok.type == 'RO' or (trailing_tok.type == 'IDENTIFIER' and trailing_tok.value.lower() == 'o')):
                self.pos += 1
                once = True
            return RerollModifier(once=once, face_list=faces)
            
        op = None
        val = None
        next_tok = self.current_token()
        if next_tok and next_tok.type in ('COMP', '='):
            self.pos += 1
            op = next_tok.value
            val_tok = self.expect('NUMBER')
            val = float(val_tok.value)
        elif next_tok and next_tok.type == 'NUMBER':
            self.pos += 1
            op = '='
            val = float(next_tok.value)
        else:
            op = '='
            val = 1.0
            
        if val is not None and val == int(val):
            val = int(val)
        return RerollModifier(once=once, condition_op=op, condition_val=val)

    def parse_explode_modifier(self, tok: Token, sides: ASTNode) -> ExplodeModifier:
        compound = (tok.type == 'DBL_BANG')
        penetrate = False
        
        next_tok = self.current_token()
        if next_tok and next_tok.type == 'IDENTIFIER' and next_tok.value.lower() == 'p':
            self.pos += 1
            penetrate = True
            
        if self.match('['):
            cascades = []
            while True:
                cond_op = '='
                cond_val = None
                
                cond_tok = self.current_token()
                if cond_tok and cond_tok.type in ('COMP', '='):
                    self.pos += 1
                    cond_op = cond_tok.value
                    cond_val_tok = self.expect('NUMBER')
                    cond_val = int(float(cond_val_tok.value))
                elif cond_tok and cond_tok.type == 'NUMBER':
                    self.pos += 1
                    cond_val = int(float(cond_tok.value))
                    if self.match('+'):
                        cond_op = '>='
                else:
                    raise ValueError("Expected condition inside cascade brackets")
                    
                self.expect(':')
                subpool = self.parse_expression()
                cascades.append(((cond_op, cond_val), subpool))
                
                if not self.match(','):
                    break
            self.expect(']')
            return ExplodeModifier(compound=compound, penetrate=penetrate, cascades=cascades)
            
        op = None
        val = None
        next_tok = self.current_token()
        if next_tok and next_tok.type in ('COMP', '='):
            self.pos += 1
            op = next_tok.value
            val_tok = self.expect('NUMBER')
            val = float(val_tok.value)
        elif next_tok and next_tok.type == 'NUMBER':
            self.pos += 1
            op = '='
            val = float(next_tok.value)
            
        next_tok = self.current_token()
        if next_tok and next_tok.type == 'IDENTIFIER' and next_tok.value.lower() == 'p':
            self.pos += 1
            penetrate = True
            
        if val is not None and val == int(val):
            val = int(val)
        return ExplodeModifier(compound=compound, penetrate=penetrate, condition_op=op, condition_val=val)

    def parse_keepdrop_modifier(self, tok: Token) -> KeepDropModifier:
        action = tok.type.lower()
        if action == 'k':
            action = 'kh'
        elif action == 'd':
            action = 'dl'
            
        if self.match('['):
            predicates = []
            while True:
                pred_tok = self.expect('IDENTIFIER')
                pred_name = pred_tok.value.lower()
                val_tok = self.expect('NUMBER')
                val = int(float(val_tok.value))
                predicates.append((pred_name, val))
                if not self.match(','):
                    break
            self.expect(']')
            return KeepDropModifier(action=action, predicates=predicates)
            
        count_expr = None
        next_tok = self.current_token()
        if next_tok and next_tok.type in ('NUMBER', '('):
            # Use precedence 9 so the '[' bracket (prec 9) is NOT consumed into the count
            count_expr = self.parse_expression(9)
            
        return KeepDropModifier(action=action, count=count_expr)

    def parse_success_modifier(self, tok: Token) -> SuccessModifier:
        op = tok.value
        val = self.parse_expression(8)
        
        failure_op = None
        failure_val = None
        next_tok = self.current_token()
        if next_tok and next_tok.type == 'F':
            self.pos += 1
            f_cond = self.current_token()
            if f_cond and f_cond.type in ('COMP', '='):
                self.pos += 1
                failure_op = f_cond.value
                failure_val = self.parse_expression(8)
            else:
                failure_op = '='
                failure_val = self.parse_expression(8)
                
        return SuccessModifier(op=op, val=val, failure_op=failure_op, failure_val=failure_val)

    def parse_brace_modifier(self) -> Union[WeightedModifier, PatternModifier]:
        if self.match('}'):
            return WeightedModifier({})
            
        first_tok = self.current_token()
        if not first_tok:
            raise ValueError("Empty brace modifier")
            
        if first_tok.type == 'NUMBER':
            mapping = {}
            while True:
                face_tok = self.expect('NUMBER')
                face = int(float(face_tok.value))
                self.expect(':')
                neg = False
                if self.match('-'):
                    neg = True
                val_tok = self.expect('NUMBER')
                val = int(float(val_tok.value))
                if neg:
                    val = -val
                mapping[face] = val
                if not self.match(','):
                    break
            self.expect('}')
            return WeightedModifier(mapping)
        else:
            patterns = []
            while True:
                pat_tok = self.expect('IDENTIFIER')
                pat_name = pat_tok.value.lower()
                self.expect(':')
                
                op_tok = self.current_token()
                if op_tok and op_tok.type in ('+', '-', '*'):
                    op = op_tok.type
                    self.pos += 1
                elif op_tok and op_tok.type == 'IDENTIFIER' and op_tok.value.lower() == 'x':
                    op = 'x'
                    self.pos += 1
                else:
                    op = '+'
                    
                val_expr = self.parse_expression(8)
                patterns.append((pat_name, op, val_expr))
                if not self.match(','):
                    break
            self.expect('}')
            return PatternModifier(patterns)

    def parse_conditional_math_modifier(self) -> ConditionalMathModifier:
        count_expr = self.parse_expression(8)
        op_tok = self.current_token()
        if op_tok and op_tok.type in ('+', '-', '*'):
            op = op_tok.type
            self.pos += 1
        else:
            raise ValueError("Expected arithmetic operator in conditional math modifier")
        val_expr = self.parse_expression(8)
        return ConditionalMathModifier(count_expr, op, val_expr)

# Evaluator Environment
class EvalEnv:
    def __init__(self, variables: Dict[str, Any] = None, macros: Dict[str, Any] = None, rng: random.Random = None, depth: int = 0):
        self.variables = variables if variables is not None else {}
        self.macros = macros if macros is not None else {}
        self.rng = rng if rng is not None else random.Random()
        self.depth = depth
        self.last_pool_rolls = []

class EvalResult:
    def __init__(self, value: Union[int, float], text_breakdown: str = "", rolls: List[int] = None, details: Any = None):
        self.value = value
        self.text_breakdown = text_breakdown
        self.rolls = rolls or []
        self.details = details

def evaluate(node: ASTNode, env: EvalEnv) -> EvalResult:
    if env.depth > MAX_RECURSION_DEPTH:
        raise ValueError("Maximum recursion depth exceeded")
        
    if isinstance(node, ConstantNode):
        return EvalResult(node.value, str(node.value))
        
    elif isinstance(node, VariableNode):
        if node.name in env.variables:
            val = env.variables[node.name]
            if isinstance(val, EvalResult):
                return EvalResult(val.value, f"{node.name}({val.value})", val.rolls)
            return EvalResult(val, f"{node.name}({val})")
        raise ValueError(f"Undefined variable: {node.name}")
        
    elif isinstance(node, UnaryOpNode):
        res = evaluate(node.expr, env)
        if node.op == '-':
            return EvalResult(-res.value, f"-({res.text_breakdown})", [-r for r in res.rolls])
        return res
        
    elif isinstance(node, BinaryOpNode):
        if node.op == 'VS':
            left_res = evaluate(node.left, env)
            right_res = evaluate(node.right, env)
            
            left_rolls = sorted(left_res.rolls, reverse=True)
            right_rolls = sorted(right_res.rolls, reverse=True)
            
            wins = 0
            for l, r in zip(left_rolls, right_rolls):
                if l > r:
                    wins += 1
            return EvalResult(wins, f"({left_res.text_breakdown} vs {right_res.text_breakdown} ➔ {wins} wins)", left_res.rolls)
            
        left_res = evaluate(node.left, env)
        right_res = evaluate(node.right, env)
        
        lv = left_res.value
        rv = right_res.value
        
        if node.op == '+':
            val = lv + rv
        elif node.op == '-':
            val = lv - rv
        elif node.op == '*':
            val = lv * rv
        elif node.op == '/':
            if rv == 0:
                raise ValueError("Division by zero")
            val = lv / rv
        elif node.op == '%':
            if rv == 0:
                raise ValueError("Modulo by zero")
            val = lv % rv
        elif node.op == '^':
            val = lv ** rv
        elif node.op in ('>', '<', '>=', '<=', '==', '!=', '='):
            val = 1 if match_condition(lv, node.op, rv) else 0
        else:
            raise ValueError(f"Unknown operator: {node.op}")
            
        if val == int(val):
            val = int(val)
            
        return EvalResult(val, f"({left_res.text_breakdown} {node.op} {right_res.text_breakdown})")
        
    elif isinstance(node, LabelAssignmentNode):
        if node.label in env.variables:
            raise ValueError(f"Reassigning label '{node.label}' is not allowed")
        res = evaluate(node.expr, env)
        env.variables[node.label] = res
        return EvalResult(res.value, f"{res.text_breakdown}:{node.label}", res.rolls)
        
    elif isinstance(node, ConditionalNode):
        cond_res = evaluate(node.cond, env)
        if cond_res.value != 0:
            res = evaluate(node.true_expr, env)
        else:
            if node.false_expr:
                res = evaluate(node.false_expr, env)
            else:
                res = EvalResult(0, "0")
        return EvalResult(res.value, f"(if {cond_res.text_breakdown} then {res.text_breakdown})")
        
    elif isinstance(node, MacroDefNode):
        env.macros[node.name] = (node.params, node.body)
        return EvalResult(0, f"Macro {node.name} defined")
        
    elif isinstance(node, MacroCallNode):
        if node.name not in env.macros:
            raise ValueError(f"Undefined macro: {node.name}")
        params, body = env.macros[node.name]
        if len(params) != len(node.args):
            raise ValueError(f"Macro {node.name} expects {len(params)} arguments, got {len(node.args)}")
            
        args_eval = []
        for arg in node.args:
            arg_res = evaluate(arg, env)
            if not isinstance(arg_res.value, (int, float)):
                raise ValueError("Macro argument must be a number")
            args_eval.append(arg_res)
            
        sub_vars = {**env.variables}
        for p, arg in zip(params, args_eval):
            if arg.value == int(arg.value):
                sub_vars[p] = int(arg.value)
            else:
                sub_vars[p] = arg.value
                
        sub_env = EvalEnv(sub_vars, env.macros, env.rng, env.depth + 1)
        res = evaluate(body, sub_env)
        return EvalResult(res.value, f"{node.name}({', '.join(str(a.value) for a in args_eval)}) ➔ {res.value}", res.rolls)
        
    elif isinstance(node, SequenceNode):
        last_val = 0
        text_parts = []
        last_rolls = []
        for i, expr in enumerate(node.exprs):
            if isinstance(expr, DiceNode) and i > 0:
                # Cap propagated count to MAX_DICE to prevent overflow cascades
                propagated = int(last_val) if isinstance(last_val, float) and last_val == int(last_val) else last_val
                if isinstance(propagated, int) and propagated < 0:
                    propagated = 0
                if isinstance(propagated, int) and propagated > MAX_DICE:
                    propagated = MAX_DICE
                expr.count = ConstantNode(propagated)
            res = evaluate(expr, env)
            last_val = res.value
            last_rolls = res.rolls
            text_parts.append(res.text_breakdown)
            
        return EvalResult(last_val, f"[{' ➔ '.join(text_parts)}]", last_rolls)
        
    elif isinstance(node, IndexNode):
        expr_res = evaluate(node.expr, env)
        idx_res = evaluate(node.index_expr, env)
        if not expr_res.rolls:
            raise ValueError("Indexing can only be applied to dice pools")
        sorted_rolls = sorted(expr_res.rolls)
        idx = int(idx_res.value)
        if idx < 0:
            idx = len(sorted_rolls) + idx
        if idx < 0 or idx >= len(sorted_rolls):
            raise ValueError(f"Index {idx} out of bounds for pool size {len(sorted_rolls)}")
        val = sorted_rolls[idx]
        return EvalResult(val, f"{expr_res.text_breakdown}[{idx_res.value}] ➔ {val}", [val])
        
    elif isinstance(node, SliceNode):
        expr_res = evaluate(node.expr, env)
        if not expr_res.rolls:
            raise ValueError("Slicing can only be applied to dice pools")
        sorted_rolls = sorted(expr_res.rolls)
        
        start = 0
        if node.start_expr:
            start = int(evaluate(node.start_expr, env).value)
            if start < 0:
                start = len(sorted_rolls) + start
                
        end = len(sorted_rolls)
        if node.end_expr:
            end = int(evaluate(node.end_expr, env).value)
            if end < 0:
                end = len(sorted_rolls) + end
                
        slice_vals = sorted_rolls[start:end]
        val = sum(slice_vals)
        return EvalResult(val, f"{expr_res.text_breakdown}[{start if node.start_expr else ''}:{end if node.end_expr else ''}] ➔ [{', '.join(map(str, slice_vals))}] (sum: {val})", slice_vals)
        
    elif isinstance(node, DiceNode):
        count_res = evaluate(node.count, env)
        sides_res = evaluate(node.sides, env)
        
        if not isinstance(count_res.value, int):
            raise ValueError("Dice count must be an integer")
        if not isinstance(sides_res.value, int):
            raise ValueError("Dice sides must be an integer")
            
        count = count_res.value
        sides = sides_res.value
        
        if count < 0:
            raise ValueError("Dice count must be non-negative")
        if count == 0:
            return EvalResult(0, "0", [])
        if sides <= 0:
            raise ValueError("Dice sides must be greater than 0")
            
        if count > MAX_DICE:
            raise ValueError(f"Too many dice in total! Limit: {MAX_DICE} dice.")
        if sides > MAX_SIDES:
            raise ValueError(f"Die with too many sides! Limit: {MAX_SIDES} sides.")
            
        dice_info = []
        for _ in range(count):
            raw_rolls = []
            val = env.rng.randint(1, sides)
            raw_rolls.append(val)
            
            active_rerolls = [m for m in node.modifiers if isinstance(m, RerollModifier)]
            for rr in active_rerolls:
                reroll_depth = 0
                while reroll_depth < 100:
                    should_reroll = False
                    if rr.face_list is not None:
                        if val in rr.face_list:
                            should_reroll = True
                    else:
                        if match_condition(val, rr.condition_op or '=', rr.condition_val or 1):
                            should_reroll = True
                            
                    if should_reroll:
                        val = env.rng.randint(1, sides)
                        raw_rolls.append(val)
                        reroll_depth += 1
                        if rr.once:
                            break
                    else:
                        break
                if reroll_depth >= 100:
                    raise ValueError("Infinite reroll loop detected!")
                    
            dice_info.append({
                "raw_rolls": raw_rolls,
                "final_raw": val,
                "kept": True,
                "explosions": [],
                "mapped_val": val
            })
            
        active_explodes = [m for m in node.modifiers if isinstance(m, ExplodeModifier)]
        for exp in active_explodes:
            for info in dice_info:
                explode_depth = 0
                while explode_depth < 100:
                    val = info["explosions"][-1] if info["explosions"] else info["final_raw"]
                    is_exploded = False
                    
                    if exp.cascades:
                        matched_expr = None
                        for cond, subexpr in exp.cascades:
                            cond_op, cond_val = cond
                            if match_condition(val, cond_op, cond_val):
                                matched_expr = subexpr
                                break
                        if matched_expr:
                            sub_res = evaluate(matched_expr, env)
                            info["explosions"].append(sub_res.value)
                            cascade_triggered = False
                            for r in sub_res.rolls:
                                for cond, _ in exp.cascades:
                                    if match_condition(r, cond[0], cond[1]):
                                        cascade_triggered = True
                            if cascade_triggered or any(match_condition(sub_res.value, c[0], c[1]) for c, _ in exp.cascades):
                                is_exploded = True
                            explode_depth += 1
                    else:
                        trigger_val = exp.condition_val if exp.condition_val is not None else sides
                        trigger_op = exp.condition_op or '='
                        if match_condition(val, trigger_op, trigger_val):
                            if trigger_val == 1 and sides == 1:
                                raise ValueError("Infinite explode loop detected!")
                            nxt_val = env.rng.randint(1, sides)
                            
                            active_rerolls = [m for m in node.modifiers if isinstance(m, RerollModifier)]
                            for rr in active_rerolls:
                                r_depth = 0
                                while r_depth < 100:
                                    s_r = False
                                    if rr.face_list is not None:
                                        if nxt_val in rr.face_list:
                                            s_r = True
                                    else:
                                        if match_condition(nxt_val, rr.condition_op or '=', rr.condition_val or 1):
                                            s_r = True
                                    if s_r:
                                        nxt_val = env.rng.randint(1, sides)
                                        r_depth += 1
                                        if rr.once:
                                            break
                                    else:
                                        break
                                        
                            info["explosions"].append(nxt_val)
                            is_exploded = True
                            explode_depth += 1
                            
                    if not is_exploded:
                        break
                        
                if explode_depth >= 100:
                    raise ValueError("Infinite explode loop detected!")
                    
        active_weighted = [m for m in node.modifiers if isinstance(m, WeightedModifier)]
        if active_weighted:
            mapping = active_weighted[0].mapping
            for info in dice_info:
                info["mapped_val"] = mapping.get(info["final_raw"], info["final_raw"])
                mapped_explodes = []
                for exp_val in info["explosions"]:
                    mapped_explodes.append(mapping.get(exp_val, exp_val))
                info["explosions"] = mapped_explodes
                
        for info in dice_info:
            exp_sum = sum(info["explosions"])
            penetrates = any(isinstance(m, ExplodeModifier) and m.penetrate for m in node.modifiers)
            if penetrates:
                exp_sum -= len(info["explosions"])
            info["total_contrib"] = info["mapped_val"] + exp_sum

        active_kd = [m for m in node.modifiers if isinstance(m, KeepDropModifier)]
        for kd in active_kd:
            if kd.predicates:
                for info in dice_info:
                    match_all = True
                    for pred_name, pred_val in kd.predicates:
                        op = {'gt': '>', 'lt': '<', 'gte': '>=', 'lte': '<=', 'eq': '==', 'neq': '!='}.get(pred_name, '==')
                        if not match_condition(info["total_contrib"], op, pred_val):
                            match_all = False
                            break
                    if kd.action in ('kh', 'k'):
                        info["kept"] = match_all
                    else:
                        info["kept"] = not match_all
            else:
                k_count = 1
                if kd.count:
                    k_count = int(evaluate(kd.count, env).value)
                    
                currently_kept = [i for i, info in enumerate(dice_info) if info["kept"]]
                if kd.action in ('kh', 'k'):
                    if k_count < 0:
                        k_count = 0
                    if kd.action == 'kh' or kd.action == 'k':
                        sorted_idx = sorted(currently_kept, key=lambda idx: dice_info[idx]["total_contrib"], reverse=True)
                    else:
                        sorted_idx = sorted(currently_kept, key=lambda idx: dice_info[idx]["total_contrib"])
                    
                    kept_idx = sorted_idx[:k_count]
                    for idx in currently_kept:
                        if idx not in kept_idx:
                            dice_info[idx]["kept"] = False
                else:
                    if k_count < 0:
                        k_count = 0
                    if kd.action == 'dh':
                        sorted_idx = sorted(currently_kept, key=lambda idx: dice_info[idx]["total_contrib"], reverse=True)
                    else:
                        sorted_idx = sorted(currently_kept, key=lambda idx: dice_info[idx]["total_contrib"])
                    
                    drop_idx = sorted_idx[:k_count]
                    for idx in drop_idx:
                        dice_info[idx]["kept"] = False

        active_cond_math = [m for m in node.modifiers if isinstance(m, ConditionalMathModifier)]
        for cm in active_cond_math:
            cm_count = int(evaluate(cm.count, env).value)
            cm_val = evaluate(cm.value_expr, env).value
            applied = 0
            for info in dice_info:
                if info["kept"]:
                    if applied >= cm_count:
                        break
                    if cm.op == '+':
                        info["total_contrib"] += cm_val
                    elif cm.op == '-':
                        info["total_contrib"] -= cm_val
                    elif cm.op == '*':
                        info["total_contrib"] *= cm_val
                    applied += 1

        active_deps = [m for m in node.modifiers if isinstance(m, DependentModifier)]
        dep_add_sum = 0
        dep_mult_factor = 1
        
        dep_notes = []
        for dep in active_deps:
            matched = False
            if dep.condition_type == 'any':
                for info in dice_info:
                    if info["kept"] and match_condition(info["total_contrib"], dep.condition_op or '=', dep.condition_val or 0):
                        matched = True
                        break
            elif dep.condition_type == 'all':
                matched = True
                kept_count = 0
                for info in dice_info:
                    if info["kept"]:
                        kept_count += 1
                        if not match_condition(info["total_contrib"], dep.condition_op or '=', dep.condition_val or 0):
                            matched = False
                            break
                if kept_count == 0:
                    matched = False
            elif dep.condition_type == 'r':
                for info in dice_info:
                    if any(match_condition(r_val, dep.condition_op or '=', dep.condition_val or 0) for r_val in info["raw_rolls"]):
                        matched = True
                        break
            elif dep.condition_type == 'sum':
                current_sum = sum(info["total_contrib"] for info in dice_info if info["kept"])
                if match_condition(current_sum, dep.condition_op or '=', dep.condition_val or 0):
                    matched = True
            elif dep.condition_type == 'count':
                # count>=3 means: the number of dice matching the condition is >= threshold
                matching_count = sum(1 for info in dice_info if info["kept"] and match_condition(info["total_contrib"], dep.condition_op or '=', dep.condition_val or 0))
                if match_condition(matching_count, dep.condition_op or '>=', dep.condition_val or 0):
                    matched = True
                    
            if matched:
                dep_res = evaluate(dep.action_expr, env)
                dep_notes.append(f"Triggered dependent modifier [{dep.condition_type}{dep.condition_op}{dep.condition_val} ➔ {dep_res.text_breakdown}]")
                if isinstance(dep.action_expr, BinaryOpNode) and dep.action_expr.op == '*':
                    dep_mult_factor *= dep_res.value
                elif isinstance(dep.action_expr, UnaryOpNode) and dep.action_expr.op == '*':
                    dep_mult_factor *= dep_res.value
                else:
                    dep_add_sum += dep_res.value

        kept_vals = [info["total_contrib"] for info in dice_info if info["kept"]]
        
        active_idx = [m for m in node.modifiers if isinstance(m, IndexModifier)]
        active_slice = [m for m in node.modifiers if isinstance(m, SliceModifier)]
        
        final_kept_rolls = []
        for info in dice_info:
            if info["kept"]:
                final_kept_rolls.append(info["total_contrib"])
                final_kept_rolls.extend(info["explosions"])
                
        if active_idx:
            idx_mod = active_idx[0]
            idx_val = int(evaluate(idx_mod.index_expr, env).value)
            sorted_kept = sorted(final_kept_rolls)
            if idx_val < 0:
                idx_val = len(sorted_kept) + idx_val
            if idx_val < 0 or idx_val >= len(sorted_kept):
                raise ValueError(f"Index {idx_val} out of bounds for pool size {len(sorted_kept)}")
            kept_vals = [sorted_kept[idx_val]]
            total_val = kept_vals[0]
        elif active_slice:
            slice_mod = active_slice[0]
            start_val = 0
            if slice_mod.start_expr:
                start_val = int(evaluate(slice_mod.start_expr, env).value)
                if start_val < 0:
                    start_val = len(final_kept_rolls) + start_val
            end_val = len(final_kept_rolls)
            if slice_mod.end_expr:
                end_val = int(evaluate(slice_mod.end_expr, env).value)
                if end_val < 0:
                    end_val = len(final_kept_rolls) + end_val
            sorted_kept = sorted(final_kept_rolls)
            kept_vals = sorted_kept[start_val:end_val]
            total_val = sum(kept_vals)
        else:
            active_patterns = [m for m in node.modifiers if isinstance(m, PatternModifier)]
            pattern_notes = []
            for pm in active_patterns:
                freqs = {}
                for v in kept_vals:
                    freqs[v] = freqs.get(v, 0) + 1
                    
                for pat_name, op, val_expr in pm.patterns:
                    matched_pattern = False
                    if pat_name == '3-of-a-kind':
                        matched_pattern = any(f >= 3 for f in freqs.values())
                    elif pat_name == '4-of-a-kind':
                        matched_pattern = any(f >= 4 for f in freqs.values())
                    elif pat_name == 'pair':
                        matched_pattern = any(f >= 2 for f in freqs.values())
                    elif pat_name == 'two-pair':
                        matched_pattern = sum(1 for f in freqs.values() if f >= 2) >= 2
                    elif pat_name == 'straight':
                        unique_sorted = sorted(list(set(kept_vals)))
                        if len(unique_sorted) >= 4:
                            gaps = [unique_sorted[j+1] - unique_sorted[j] for j in range(len(unique_sorted)-1)]
                            consecutive = 1
                            max_consecutive = 1
                            for gap in gaps:
                                if gap == 1:
                                    consecutive += 1
                                    max_consecutive = max(max_consecutive, consecutive)
                                else:
                                    consecutive = 1
                            matched_pattern = max_consecutive >= 4
                    elif pat_name == 'full-house':
                        has_three = any(f >= 3 for f in freqs.values())
                        has_two = sum(1 for f in freqs.values() if f >= 2) >= 2
                        matched_pattern = has_three and has_two
                        
                    if matched_pattern:
                        pat_res = evaluate(val_expr, env)
                        pattern_notes.append(f"Matched pattern {pat_name} ➔ {op}{pat_res.value}")
                        if op == 'x' or op == '*':
                            dep_mult_factor *= pat_res.value
                        else:
                            dep_add_sum += pat_res.value

            active_success = [m for m in node.modifiers if isinstance(m, SuccessModifier)]
            is_success_pool = len(active_success) > 0
            
            successes = 0
            success_rolls_formatted = []
            
            if is_success_pool:
                succ_mod = active_success[0]
                succ_op = succ_mod.op
                succ_val = evaluate(succ_mod.val, env).value
                
                fail_op = succ_mod.failure_op
                fail_val = evaluate(succ_mod.failure_val, env).value if succ_mod.failure_val else None
                
                for info in dice_info:
                    if info["kept"]:
                        roll_list = [info["mapped_val"]] + info["explosions"]
                        for val in roll_list:
                            is_succ = match_condition(val, succ_op, succ_val)
                            is_fail = fail_op and match_condition(val, fail_op, fail_val)
                            
                            if is_succ:
                                successes += 1
                                success_rolls_formatted.append(f"**{val}**")
                            elif is_fail:
                                successes -= 1
                                success_rolls_formatted.append(f"~~{val}~~")
                            else:
                                success_rolls_formatted.append(str(val))
                    else:
                        roll_list = [info["mapped_val"]] + info["explosions"]
                        for val in roll_list:
                            success_rolls_formatted.append(f"~~{val}~~")
                
                total_val = successes
            else:
                total_val = sum(kept_vals)
                
            total_val = total_val * dep_mult_factor + dep_add_sum
            
        if total_val == int(total_val):
            total_val = int(total_val)
            
        breakdown_parts = []
        is_success_pool = len([m for m in node.modifiers if isinstance(m, SuccessModifier)]) > 0
        if is_success_pool:
            breakdown_parts.append(f"[{', '.join(success_rolls_formatted)}]")
        else:
            die_texts = []
            for info in dice_info:
                part = ""
                if info["explosions"]:
                    part = f"({info['mapped_val']}! + " + "! + ".join(map(str, info["explosions"])) + f")"
                else:
                    if len(info["raw_rolls"]) > 1:
                        part = " ➔ ".join(map(str, info["raw_rolls"]))
                    else:
                        part = str(info["total_contrib"])
                        
                if not info["kept"]:
                    part = f"~~{part}~~"
                die_texts.append(part)
                
            breakdown_parts.append(f"[{', '.join(die_texts)}]")
            
        notes = dep_notes
        if notes:
            breakdown_parts.append(f" ({' | '.join(notes)})")
            
        if active_idx:
            idx_mod = active_idx[0]
            text_breakdown = f"{''.join(breakdown_parts)}[{idx_mod.index_expr}] ➔ {total_val}"
        elif active_slice:
            slice_mod = active_slice[0]
            text_breakdown = f"{''.join(breakdown_parts)}[{slice_mod.start_expr if slice_mod.start_expr else ''}:{slice_mod.end_expr if slice_mod.end_expr else ''}] ➔ {total_val}"
        else:
            text_breakdown = "".join(breakdown_parts)
        
        env.last_pool_rolls = dice_info
        
        final_kept_rolls = []
        for info in dice_info:
            if info["kept"]:
                final_kept_rolls.append(info["total_contrib"])
                final_kept_rolls.extend(info["explosions"])
                
        return EvalResult(total_val, text_breakdown, final_kept_rolls, dice_info)

    raise ValueError(f"Unknown AST node type: {type(node)}")

# Static label-conflict checker: raises ValueError if any conditional branch
# could re-assign a label that was already defined earlier in the sequence.
def check_label_conflicts(node: ASTNode, assigned: set) -> set:
    """Walk the AST and raise if a label is assigned more than once."""
    if isinstance(node, LabelAssignmentNode):
        if node.label in assigned:
            raise ValueError(f"Reassigning label '{node.label}' is not allowed")
        assigned = assigned | {node.label}
        check_label_conflicts(node.expr, assigned - {node.label})
    elif isinstance(node, SequenceNode):
        for child in node.exprs:
            assigned = check_label_conflicts(child, assigned)
    elif isinstance(node, ConditionalNode):
        # Check both branches against the labels known at the point of the conditional
        check_label_conflicts(node.true_expr, assigned)
        if node.false_expr:
            check_label_conflicts(node.false_expr, assigned)
    elif isinstance(node, BinaryOpNode):
        check_label_conflicts(node.left, assigned)
        check_label_conflicts(node.right, assigned)
    elif isinstance(node, UnaryOpNode):
        check_label_conflicts(node.expr, assigned)
    return assigned

PRECEDENCE = {
    'IF': 1,
    'VS': 2,
    'COMP': 3,
    ':': 4,
    '+': 6, '-': 6,
    '*': 7, '/': 7, '%': 7,
    '^': 8,
    '[': 9,
    'D': 10,
}

# Main Entry point
def execute_roll(dice_expr: str) -> Dict[str, Any]:
    tokens = tokenize(dice_expr)
    parser = DiceParser(tokens)
    ast = parser.parse_statements()
    
    if parser.current_token() is not None:
        raise ValueError(f"Trailing characters in expression at position {parser.current_token().pos}")
    
    # Static check: catch label re-assignments even in untaken conditional branches
    check_label_conflicts(ast, set())
    
    env = EvalEnv()
    res = evaluate(ast, env)
    
    raw_breakdown = res.text_breakdown
    
    return {
        "total": res.value,
        "post_mod_total": res.value,
        "pre_mod_total": res.value,
        "breakdown": raw_breakdown,
        "ast": ast,
        "result_obj": res,
        "group_summaries": [],
        "footer_keepdrop": [],
        "ampersand_notes": [],
        "const_total": 0
    }