import ast
import collections
import inspect
import types
import numpy as np

from .exceptions import FunctionClosureError, TransformError


INDENT_TAB = '  '


UNARY_OP_FORMATS = {
    ast.Invert: '~{value}',
    ast.Not: '!{value}',
    ast.UAdd: '+{value}',
    ast.USub: '-{value}',
}


BIN_OP_FORMATS = {
    ast.Add: '{left} + {right}',
    ast.Sub: '{left} - {right}',
    ast.Mult: '{left} * {right}',
    ast.Div: '{left} / {right}',
    ast.Mod: '{left} % {right}',
    ast.Pow: 'pow({left}, {right})',
    ast.LShift: '{left} << {right}',
    ast.RShift: '{left} >> {right}',
    ast.BitOr: '{left} | {right}',
    ast.BitAnd: '{left} & {right}',
    ast.FloorDiv: 'floor({left} / {right})',
}


AUG_OP_FORMATS = {
    ast.Add: '{left} += {right}',
    ast.Sub: '{left} -= {right}',
    ast.Mult: '{left} *= {right}',
    ast.Div: '{left} /= {right}',
    ast.Mod: '{left} %= {right}',
    ast.Pow: '{left} = pow({left}, {right})',
    ast.LShift: '{left} << {right}',
    ast.RShift: '{left} >>= {right}',
    ast.BitOr: '{left} |= {right}',
    ast.BitAnd: '{left} &= {right}',
    ast.FloorDiv: '{left} = floor({left} /= {right})',
}


COMPARE_OP_STR = {
    ast.Eq: '==',
    ast.NotEq: '!=',
    ast.Lt: '<',
    ast.Gt: '>',
    ast.GtE: '>=',
    ast.Is: '==',
    ast.IsNot: '!=',
}


VALID_GLOBAL_NAMES = {
    'okl',
}


VALID_GLOBAL_VALUE_TYPES = {
    type(None): 'void',
    bool: 'bool',
    int: 'int',
    float: 'double',
    np.bool_: 'bool',
    np.int8: 'char',
    np.uint8: 'char',
    np.int16: 'short',
    np.uint16: 'short',
    np.int32: 'int',
    np.uint32: 'int',
    np.int64: 'long',
    np.uint64: 'long',
    np.float32: 'float',
    np.float64: 'double',
}


VALID_BUILTINS = {
    'range',
    'len',
}


OKL_DECORATORS = {
    '@okl.kernel': '@kernel',
}


__last_error_node = None
class Oklifier:
    def __init__(self, obj):
        self.obj = obj
        self.source = None

        self.globals = dict()
        self.functions = dict()
        self.scope_stack = []

        if isinstance(obj, ast.AST):
            self.root = obj
        elif isinstance(obj, types.FunctionType):
            self.inspect_function_closure(obj)
            self.source = inspect.getsource(obj)
            self.root = ast.parse(self.source)
        else:
            raise TypeError('Unable to oklify object')

        self.stringify_node_map = {
            ast.AnnAssign: self.stringify_AnnAssign,
            ast.Assign: self.stringify_Assign,
            ast.Attribute: self.stringify_Attribute,
            ast.AugAssign: self.stringify_AugAssign,
            ast.BinOp: self.stringify_BinOp,
            ast.BoolOp: self.stringify_BoolOp,
            ast.Break: self.stringify_Break,
            ast.Call: self.stringify_Call,
            ast.Continue: self.stringify_Continue,
            ast.Compare: self.stringify_Compare,
            ast.If: self.stringify_If,
            ast.Index: self.stringify_Index,
            ast.Expr: self.stringify_Expr,
            ast.For: self.stringify_For,
            ast.FunctionDef: self.stringify_FunctionDef,
            ast.List: self.stringify_List,
            ast.Module: self.stringify_Module,
            ast.Name: self.stringify_Name,
            ast.NameConstant: self.stringify_NameConstant,
            ast.Num: self.stringify_Num,
            ast.Pass: self.stringify_Pass,
            ast.Return: self.stringify_Return,
            ast.Subscript: self.stringify_Subscript,
            ast.UnaryOp: self.stringify_UnaryOp,
            ast.While: self.stringify_While,
        }

    def inspect_function_closure(self, func):
        closure_vars = inspect.getclosurevars(func)
        # Inspect globals
        for name, value in closure_vars.globals.items():
            type_str = VALID_GLOBAL_VALUE_TYPES.get(type(value))
            if type_str:
                self.globals[name] = type_str
            elif isinstance(value, types.FunctionType):
                oklifier = Oklifier(value)
                self.functions[name] = {
                    'signature': oklifier.stringify_function_signature(
                        node=oklifier.root.body[0],
                        semicolon=True,
                    ),
                    'source': oklifier.to_str(),
                }
            elif name not in VALID_GLOBAL_NAMES:
                raise FunctionClosureError(
                    'Unable to transform non-local variable: {}'.format(name)
                )

        # Inspect builtins
        for builtin in closure_vars.builtins.keys():
            if builtin not in VALID_BUILTINS:
                raise FunctionClosureError(
                    'Unable to transform builtin: {}'.format(builtin)
                )

    def stringify_Arguments(self, args, indent=''):
        if args.kwarg:
            self.raise_error(args, 'Cannot handle **kwargs')
        if args.vararg:
            self.raise_error(args, 'Cannot handle *args')
        if args.defaults or args.kw_defaults:
            self.raise_error(args, 'Cannot handle default arguments yet')

        args = [
            *args.args,
            *args.kwonlyargs,
        ]
        arg_count = len(args)

        args_str = ''
        for index, arg in enumerate(args):
            arg_name = arg.arg
            arg_str = self.stringify_annotation(arg.annotation, arg_name)
            if arg_str == arg_name:
                self.raise_error(arg,
                                 'Arguments must have a type annotation')

            args_str += arg_str
            if index < (arg_count - 1):
                args_str += ',\n' + indent

        return args_str

    def stringify_AnnAssign(self, node, indent=''):
        var_name = self.stringify_node(node.target)
        self.add_to_scope(var_name)

        node_str = self.stringify_annotation(node.annotation, var_name)
        if node.value:
            node_str += ' = {value}'.format(
                value=self.stringify_node(node.value),
            )

        return node_str + ';'

    def stringify_Assign(self, node, indent=''):
        if len(node.targets) != 1:
            self.raise_error(node,
                             'Cannot handle assignment of more than 1 value')

        var = node.targets[0]
        var_str = self.stringify_node(var, indent)

        if (type(var) is ast.Name and
            not self.is_defined(var_str)):
            self.raise_error(node,
                             'Cannot handle untyped variables')

        return '{left} = {right};'.format(
            left=var_str,
            right=self.stringify_node(node.value, indent),
        )

    def stringify_Attribute(self, node, indent=''):
        return '{name}.{value}'.format(
            name=self.stringify_node(node.value, indent),
            value=node.attr,
        )

    def stringify_AugAssign(self, node, indent=''):
        str_format = AUG_OP_FORMATS.get(type(node.op))
        if str_format is None:
            self.raise_error(node.op,
                             'Unable to handle operator')
        return str_format.format(
            left=self.stringify_node(node.target, indent),
            right=self.stringify_node(node.value, indent),
        )

    def stringify_BinOp(self, node, indent=''):
        str_format = BIN_OP_FORMATS.get(type(node.op))
        if str_format is None:
            self.raise_error(node.op,
                             'Unable to handle operator')

        return str_format.format(
            left=self.stringify_node(node.left, indent),
            right=self.stringify_node(node.right, indent),
        )

    def stringify_BoolOp(self, node, indent=''):
        op = ' && ' if isinstance(node.op, ast.And) else ' || '
        return '{values}'.format(values=op.join(
            self.stringify_node(subnode, indent)
            for subnode in node.values
        ))

    def stringify_Break(self, node, indent=''):
        return 'break;'

    def stringify_Call(self, node, indent=''):
        args = ', '.join(
            self.stringify_node(arg)
            for arg in node.args
        )
        return '{func}({args})'.format(
            func=self.stringify_node(node.func, indent),
            args=args,
        )

    def stringify_Continue(self, node, indent=''):
        return 'continue;'

    def stringify_Compare(self, node, indent=''):
        ops = [type(op) for op in node.ops]
        for op_index, op in enumerate(ops):
            if op not in COMPARE_OP_STR:
                self.raise_error(node.ops[op_index],
                                 'Cannot handle comparison operator')
            ops = [
                COMPARE_OP_STR[op]
                for op in ops
            ]
            values = [
                self.stringify_node(subnode, indent)
                for subnode in [node.left, *node.comparators]
            ]
        return ' && '.join(
            '{left} {op} {right}'.format(
                left=left,
                right=right,
                op=ops[index],
            )
            for index, (left, right) in enumerate(zip(values[:-1], values[1:]))
        )

    def stringify_If(self, node, indent=''):
        if_str = 'if ({test})'.format(test=self.stringify_node(node.test))
        if_str += self.stringify_body(node.body, indent)

        orelse_nodes = node.orelse
        if orelse_nodes:
            has_elif = (len(orelse_nodes) == 1
                        and type(orelse_nodes[0]) is ast.If)
            if_str += '\n' + indent
            if_str += 'else '
            if has_elif:
                if_str += self.stringify_node(orelse_nodes[0], indent)
            else:
                if_str += self.stringify_body(orelse_nodes, indent)

        return if_str

    def stringify_Index(self, node, indent=''):
        return self.stringify_node(node.value, indent)

    def stringify_Expr(self, node, indent=''):
        return self.stringify_node(node.value, indent)

    def get_range(self, node):
        return 0, 10, 1

    def get_okl_range(self, node):
        return 0, 10, 1

    def split_for_iter(self, node):
        node_str = self.stringify_node(node)
        if node_str.startswith('range'):
            return self.get_range(node)
        if node_str.startswith('okl.range'):
            return self.get_okl_range(node)
        self.raise_error(node,
                         'Unable to transform this iterable')

    def stringify_For(self, node, indent=''):
        if not isinstance(node.target, ast.Name):
            self.raise_error(node.target,
                             'Can only handle one variable for the for-loop index')
        index = self.stringify_node(node.target, indent)
        start, end, step = self.split_for_iter(node.iter)

        if isinstance(step, int):
            if step == 0:
                self.raise_error(node.iter,
                                 'Cannot have for-loop with a step size of 0')
            if step > 0:
                if step == 1:
                    step = '++{index}'.format(index=index)
                else:
                    step = '{index} += {step}'.format(index=index, step=step)
            else:
                if step == -1:
                    step = '--{index}'.format(index=index)
                else:
                    step = '{index} -= {step}'.format(index=index, step=-step)
        elif isinstance(step, str):
            step = '{index} += {step}'.format(index=index, step=step)

        for_str = (
            'for (int {index} = {start}; {index} < {end}; {step})'
        ).format(index=index,
                 start=start,
                 end=end,
                 step=step)

        return for_str + self.stringify_body(node.body, indent)

    def stringify_function_signature(self, node, semicolon=True):
        name = node.name

        returns = self.stringify_annotation(node.returns)
        if not returns:
            self.raise_error(node,
                             'Function must have a return value type')

        decorators = ''
        for decorator in node.decorator_list:
            decorator_str = '@{attr}'.format(attr=self.stringify_node(decorator))
            okl_decorator = OKL_DECORATORS.get(decorator_str)
            if okl_decorator is None:
                self.raise_error(decorator,
                                 'Cannot handle decorator')
            decorators += okl_decorator + ' '

        func_str = (
            '{decorators}{returns} {name}('
        ).format(decorators=decorators,
                 name=name,
                 returns=returns)

        args = self.stringify_Arguments(node.args, indent=(' ' * len(func_str)))

        func_str += '{args})'.format(args=args)
        if semicolon:
            func_str += ';'

        return func_str

    def stringify_FunctionDef(self, node, indent=''):
        func_str = self.stringify_function_signature(node, semicolon=False)

        return func_str + self.stringify_body(node.body, indent)

    def stringify_List(self, node, indent=''):
        entries = ', '.join(
            self.stringify_node(item, indent)
            for item in node.elts
        )
        return '{' + entries + '}'

    def stringify_Module(self, node, indent=''):
        return self.stringify_block(node.body, indent)

    def stringify_Name(self, node, indent=''):
        name = node.id
        var = self.globals.get(name)
        return var or name

    def stringify_constant(self, value):
        if value is True:
            return 'true'
        if value is False:
            return 'false'
        if value is None:
            return 'NULL'
        return None

    def stringify_NameConstant(self, node, indent=''):
        value = self.stringify_constant(node.value)
        if value is None:
            self.raise_error(node,
                             'Cannot handle NameConstant')
        return value

    def stringify_Num(self, node, indent=''):
        return str(node.n)

    def split_subscript(self, node):
        if not isinstance(node.slice, ast.Index):
            self.raise_error(node.slice,
                             'Can only handle single access slices')
        return [node.value, node.slice.value]

    def stringify_Pass(self, node, indent=''):
        return ''

    def stringify_Return(self, node, indent=''):
        return 'return {value};'.format(value=self.stringify_node(node.value))


    def stringify_Subscript(self, node, indent=''):
        value, index = self.split_subscript(node)
        return '{value}[{index}]'.format(
            value=self.stringify_node(value, indent),
            index=self.stringify_node(index, indent),
        )

    def stringify_annotation(self, node, var_name=''):
        if node is None:
            return var_name

        node_type = type(node)
        if node_type is ast.Name:
            type_str = self.py_to_c_type(self.stringify_Name(node))
            if var_name:
                return type_str + ' ' + var_name
            return type_str

        if node_type is ast.NameConstant:
            node_str = self.stringify_NameConstant(node)
            if node_str == 'NULL':
                if var_name:
                    return 'void ' + var_name
                return 'void'

        if node_type is ast.Subscript:
            value, index = self.split_subscript(node)
            value_str = self.stringify_node(value)
            if value_str == 'List':
                return self.stringify_list_annotation(index, var_name)
            if value_str == 'Const':
                return 'const ' + self.stringify_annotation(index, var_name)
            if value_str == 'Exclusive':
                return '@exclusive ' + self.stringify_annotation(index, var_name)
            if value_str == 'Shared':
                return '@shared ' + self.stringify_annotation(index, var_name)

        self.raise_error(node,
                         'Cannot handle type annotation')

    def stringify_list_annotation(self, node, var_name):
        if not isinstance(node, ast.Tuple):
            type_str = self.stringify_annotation(node)
            if not type_str.endswith('*'):
                type_str += ' '
            return type_str + '*' + var_name

        type_node, *indices = node.elts
        type_str = self.stringify_annotation(type_node)
        arrays_str = ''.join((
            '[{index}]'.format(index=self.stringify_node(index))
            for index in indices
        ))
        return type_str + ' ' + var_name + arrays_str

    def stringify_UnaryOp(self, node, indent=''):
        str_format = UNARY_OP_FORMATS.get(type(node.op))
        if str_format is None:
            self.raise_error(node.op,
                             'Unable to handle operator')
        return str_format.format(value=self.stringify_node(node.operand, indent))

    def stringify_While(self, node, indent=''):
        if node.orelse:
            self.raise_error(node.orelse[0],
                             'Cannot handle statement after while')

        while_str = 'while ({test})'.format(test=self.stringify_node(node.test))
        return while_str + self.stringify_body(node.body, indent)

    def stringify_node(self, node, indent=''):
        node_str_func = self.stringify_node_map.get(type(node))
        if node_str_func is not None:
            return node_str_func(node, indent)
        self.raise_error(node, 'Unable to handle node type {}'.format(type(node)))

    def stringify_block(self, nodes, indent=''):
        self.scope_stack.append(set())
        nodes_str = (
            self.stringify_node(node, indent=indent)
            for node in nodes
        )
        block_str = '\n'.join(
            (indent + node_str)
            for node_str in nodes_str
            if node_str
        )
        self.scope_stack.pop()
        return block_str

    def stringify_body(self, node, indent):
        body = self.stringify_block(node, indent + INDENT_TAB)
        if not body:
            return ' {}'
        return ' {{\n{body}\n{indent}}}'.format(body=body,
                                               indent=indent)

    def add_to_scope(self, var_name):
        self.scope_stack[-1].add(var_name)

    def is_defined(self, var_name):
        for scope in self.scope_stack:
            if var_name in scope:
                return True
        return False

    def py_to_c_type(self, type_name):
        return type_name

    @staticmethod
    def get_last_error_node():
        global __last_error_node
        return __last_error_node

    def raise_error(self, node, message):
        global __last_error_node
        __last_error_node = node

        error_line = node.lineno - 1
        char_pos = node.col_offset

        error_message = 'Error: {message}\n'.format(message=message)

        if self.source:
            source_lines = self.source.splitlines()

            # Get context lines
            lines = [
                line
                for line in range(error_line - 2, error_line + 3)
                if 0 <= line < len(source_lines)
            ]
            # Stringify lines and pad them
            lines_str = [str(line + 1) for line in lines]
            char_size = max(len(line) for line in lines_str)
            lines_str = [line.ljust(char_size) for line in lines_str]

            prefix = '   '

            for index, line in enumerate(lines):
                error_message += prefix
                error_message += lines_str[index]
                error_message += ' | '
                error_message += source_lines[line] + '\n'
                if line == error_line:
                    error_message += prefix
                    error_message += ' ' * char_size
                    error_message += ' | '
                    error_message += (' ' * char_pos) + '^\n'

        raise TransformError(error_message)

    def to_str(self):
        functions = self.functions.values()
        return '\n\n'.join([
            *[func['signature'] for func in functions],
            *[func['source'] for func in functions],
            self.stringify_node(self.root),
        ])


def py2okl(obj):
    if not isinstance(obj, collections.Iterable):
        return Oklifier(obj).to_str()
    return [
        Oklifier(item).to_str()
        for item in obj
    ]