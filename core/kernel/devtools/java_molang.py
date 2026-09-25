# -*- coding: utf-8 -*-
"""Java 版 YSM molang 的解析器与基岩文本打印器(Python 2.7 / 3), 供自定义函数编译用(java_functions.py)。

文法与优先级对照 com.elfmcys.ysm.molang.parser.MolangParserImpl(C 口径):
  `=`(1) < `??`(1200) < `?:` / 无 else 的 `?`(1400, 右结合) < `||`(1600) < `&&`(1800) < `==` `!=`(2000)
  < 比较(2200) < `+` `-`(2400) < `*` `/`(2600) < 一元 `!` `-` `+`(2800) < `->`(3000); `return` 的操作数是
  整个表达式(优先级 -1, `return v.x = 1` 返回赋值结果)。
- 单项: 数字 / 'str' / true false / (表达式) / {语句; ...} / break / continue / return 表达式 /
  标识符(`命名空间.成员`, 其后再跟 `.x` 是结构体成员访问) / 前缀 + - !(一元 + 直接取操作数)
- 复合: 调用 f(a, b)(函数名后可省略空括号: `fn.b;`) / 隐式乘法 `x(y)` / 三元 / 条件 / 成员 / 下标 / 二元
- 语句以 ';' 分隔。C 风格注释由 StripComments 先剥(函数文件, Wiki《自定义函数》口径)。

标识符保留原文大小写(Java 词法整体小写, 基岩变量名大小写不敏感 —— 比较时一律 lower()), 让产物与包里其余
文件对同一变量的写法一致。
"""
import re

try:
    _STRING_TYPES = (str, unicode)  # noqa: F821  (py2)
except NameError:
    _STRING_TYPES = (str,)


class MolangParseError(ValueError):
    pass


_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<string>'[^']*')
  | (?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?[fF]?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\?\?|->|==|!=|<=|>=|&&|\|\||[-+*/<>!?:=;,()\[\]{}.])
""", re.X)

_BINARY_PRECEDENCE = {
    "??": 1200, "||": 1600, "&&": 1800, "==": 2000, "!=": 2000,
    "<": 2200, "<=": 2200, ">": 2200, ">=": 2200,
    "+": 2400, "-": 2400, "*": 2600, "/": 2600, "->": 3000,
}
PREC_ASSIGN = 1
PREC_TERNARY = 1400
PREC_UNARY = 2800
PREC_MUL = 2600
VARIABLE_NAMESPACES = frozenset(("v", "variable", "t", "temp", "c", "context"))


def StripComments(text):
    """剥掉 // 行注释与 /* */ 块注释(单引号字符串里的不动); 行注释保留换行"""
    out = []
    index, length = 0, len(text)
    quote = False
    while index < length:
        char = text[index]
        if quote:
            out.append(char)
            if char == "'":
                quote = False
            index += 1
            continue
        if char == "'":
            quote = True
            out.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            out.append(" ")
            continue
        out.append(char)
        index += 1
    return u"".join(out)


# ======================================================================
# AST
# ======================================================================
class Node(object):
    """语法树节点。kind 与字段:
    num(value: 原文) / str(value: 不含引号) / name(path: 元组, 如 ('v', 'x')) /
    call(path, args: 列表) / member(target, field) / index(target, index) /
    unary(op, operand) / binary(op, left, right) / cond(test, then) / ternary(test, then, other) /
    assign(target, value) / block(body: 语句列表) / ret(value) / brk / cont /
    loop(count, body: block) / foreach(var: name, iterable, body: block)
    """
    __slots__ = ("kind", "value", "path", "args", "target", "field", "index", "op", "left", "right",
                 "test", "then", "other", "body", "operand", "iterable")

    def __init__(self, kind, **fields):
        self.kind = kind
        for slot in self.__slots__:
            if slot != "kind":
                setattr(self, slot, fields.get(slot))

    def Copy(self, **changes):
        fields = dict((slot, getattr(self, slot)) for slot in self.__slots__ if slot != "kind")
        fields.update(changes)
        return Node(self.kind, **fields)

    def __repr__(self):
        return "Node({!r}: {})".format(self.kind, Print(self))


def Num(text):
    return Node("num", value=text if isinstance(text, _STRING_TYPES) else FormatNumber(text))


def Name(*path):
    return Node("name", path=tuple(path))


def NamePath(node):
    """name 节点的小写路径(比较用); 非 name 返回 None"""
    if node is None or node.kind != "name":
        return None
    return tuple(part.lower() for part in node.path)


def CallPath(node):
    """call 节点的小写路径; 非 call 返回 None"""
    if node is None or node.kind != "call":
        return None
    return tuple(part.lower() for part in node.path)


def FormatNumber(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if float(value) == int(value) and abs(value) < 1e15:
        return str(int(value))
    text = repr(float(value))
    return text


def NumberValue(node):
    """num 节点(或取负的 num) → float; 其余 None"""
    if node is None:
        return None
    if node.kind == "num":
        text = node.value.rstrip("fF")
        try:
            return float(text)
        except ValueError:
            return None
    if node.kind == "unary" and node.op == "-":
        inner = NumberValue(node.operand)
        return -inner if inner is not None else None
    return None


# ======================================================================
# 解析
# ======================================================================
class _Parser(object):
    def __init__(self, text):
        self.tokens = []
        position, length = 0, len(text)
        while position < length:
            match = _TOKEN.match(text, position)
            if match is None:
                raise MolangParseError(u"非法字符 '{}'(第 {} 个字符)".format(text[position], position + 1))
            if match.lastgroup != "space":
                self.tokens.append((match.lastgroup, match.group(0)))
            position = match.end()
        self.index = 0

    def _Peek(self, offset=0):
        position = self.index + offset
        if position < len(self.tokens):
            return self.tokens[position]
        return ("eof", u"")

    def _IsOp(self, text, offset=0):
        kind, value = self._Peek(offset)
        return kind == "op" and value == text

    def _Take(self):
        token = self._Peek()
        self.index += 1
        return token

    def _Expect(self, text):
        kind, value = self._Take()
        if kind != "op" or value != text:
            raise MolangParseError(u"缺少 '{}'(遇到 {})".format(text, value or u"结尾"))

    def ParseProgram(self):
        statements = self._Statements(closer=None)
        if self._Peek()[0] != "eof":
            raise MolangParseError(u"多余的 '{}'".format(self._Peek()[1]))
        return statements

    def _Statements(self, closer):
        statements = []
        while True:
            kind, value = self._Peek()
            if kind == "eof" or (closer and kind == "op" and value == closer):
                return statements
            if kind == "op" and value == ";":
                self._Take()
                continue
            statements.append(self._Expression(-10))
            kind, value = self._Peek()
            if kind == "op" and value == ";":
                self._Take()
                continue
            if kind == "eof" or (closer and kind == "op" and value == closer):
                return statements
            raise MolangParseError(u"语句之间缺少 ';'(遇到 '{}')".format(value))

    def _Expression(self, lastPrecedence):
        node = self._Single()
        while True:
            kind, text = self._Peek()
            if kind != "op":
                return node
            if text == "(":
                # Java parseCompound: 非调用表达式后紧跟 '(' = 隐式乘法
                if lastPrecedence >= PREC_MUL:
                    return node
                right = self._Expression(PREC_MUL)
                node = Node("binary", op="*", left=node, right=right)
                continue
            if text == "?":
                if lastPrecedence > PREC_TERNARY:
                    return node
                self._Take()
                then = self._Expression(PREC_TERNARY)
                if self._IsOp(":"):
                    self._Take()
                    other = self._Expression(PREC_TERNARY)
                    node = Node("ternary", test=node, then=then, other=other)
                else:
                    node = Node("cond", test=node, then=then)
                continue
            if text == ".":
                self._Take()
                kind, field = self._Take()
                if kind != "name":
                    raise MolangParseError(u"结构体成员访问后缺标识符")
                node = Node("member", target=node, field=field)
                continue
            if text == "[":
                self._Take()
                index = self._Expression(0)
                self._Expect("]")
                node = Node("index", target=node, index=index)
                continue
            if text == "=":
                if lastPrecedence >= PREC_ASSIGN:
                    return node
                self._Take()
                value = self._Expression(PREC_ASSIGN)
                node = Node("assign", target=node, value=value)
                continue
            precedence = _BINARY_PRECEDENCE.get(text)
            if precedence is None or lastPrecedence >= precedence:
                return node
            self._Take()
            right = self._Expression(precedence)
            node = Node("binary", op=text, left=node, right=right)

    def _Single(self):
        kind, text = self._Take()
        if kind == "number":
            return Node("num", value=text)
        if kind == "string":
            return Node("str", value=text[1:-1])
        if kind == "op":
            if text == "(":
                inner = self._Expression(0)
                self._Expect(")")
                return inner
            if text == "{":
                body = self._Statements(closer="}")
                self._Expect("}")
                return Node("block", body=body)
            if text == "+":
                return self._Expression(PREC_UNARY)
            if text in ("-", "!"):
                operand = self._Expression(PREC_UNARY)
                return Node("unary", op=text, operand=operand)
            raise MolangParseError(u"此处需要操作数, 遇到 '{}'".format(text))
        if kind == "name":
            lowered = text.lower()
            if lowered == "true":
                return Node("num", value="1")
            if lowered == "false":
                return Node("num", value="0")
            if lowered == "break":
                return Node("brk")
            if lowered == "continue":
                return Node("cont")
            if lowered == "return":
                return Node("ret", value=self._Expression(-1))
            path = [text]
            if self._IsOp(".") and self._Peek(1)[0] == "name":
                self._Take()
                path.append(self._Take()[1])
            # 变量命名空间的成员是变量不是函数: 其后的 '(' 走隐式乘法(Java IdentifierExpression 按绑定类型分)
            if self._IsOp("(") and lowered not in VARIABLE_NAMESPACES and lowered != "args":
                self._Take()
                args = []
                if not self._IsOp(")"):
                    while True:
                        args.append(self._Expression(0))
                        if self._IsOp(","):
                            self._Take()
                            continue
                        break
                self._Expect(")")
                call = Node("call", path=tuple(path), args=args)
                return _SpecialForm(call)
            return Node("name", path=tuple(path))
        raise MolangParseError(u"表达式不完整")


def _SpecialForm(call):
    """loop(n, {…}) / for_each(t.x, 列表, {…}) 转成专用节点(参数形态不对的保持普通调用, 与 Java 一样什么都不做)"""
    head = CallPath(call)
    if head == ("loop",) and len(call.args) == 2 and call.args[1].kind == "block":
        return Node("loop", operand=call.args[0], body=call.args[1])
    if head == ("for_each",) and len(call.args) == 3 and call.args[0].kind == "name" \
            and call.args[2].kind == "block":
        return Node("foreach", target=call.args[0], iterable=call.args[1], body=call.args[2])
    return call


def ParseProgram(text):
    """molang 文本 → 语句节点列表; 解析失败抛 MolangParseError"""
    return _Parser(text).ParseProgram()


# ======================================================================
# 打印(基岩文本)
# ======================================================================
def _Precedence(node):
    kind = node.kind
    if kind == "assign":
        return PREC_ASSIGN
    if kind in ("ternary", "cond"):
        return PREC_TERNARY
    if kind == "binary":
        return _BINARY_PRECEDENCE[node.op]
    if kind == "unary":
        return PREC_UNARY
    if kind == "ret":
        return -1
    return 10000


def _Child(node, parentPrecedence, strict=False):
    """子表达式; 优先级不高于父节点(strict: 严格低于才免括号)时加括号。三元/条件作任何运算的操作数一律加括号
    (基岩资源包走旧版语义, 三元结合性与新版相反, 见 molang_syntax.ExplicitPrecedence 注)"""
    text = Print(node)
    precedence = _Precedence(node)
    if node.kind in ("ternary", "cond", "assign"):
        return u"(" + text + u")"
    if precedence < parentPrecedence or (strict and precedence == parentPrecedence):
        return u"(" + text + u")"
    return text


def _LogicalChild(node, op):
    """&& 与 || 互为操作数、等值与比较互为操作数时加括号(旧版语义二者优先级不同)"""
    if node.kind == "binary":
        inner = node.op
        if inner in ("&&", "||") and op in ("&&", "||") and inner != op:
            return u"(" + Print(node) + u")"
        if inner in ("==", "!=", "<", "<=", ">", ">=") and op in ("==", "!=", "<", "<=", ">", ">="):
            return u"(" + Print(node) + u")"
        if inner == "??" or op == "??":
            if node.kind == "binary" and inner != "??":
                return u"(" + Print(node) + u")"
    return None


def Print(node):
    """节点 → 基岩 molang 文本(单个表达式/语句, 不带结尾分号)"""
    kind = node.kind
    if kind == "num":
        return node.value
    if kind == "str":
        return u"'" + node.value + u"'"
    if kind == "name":
        return u".".join(node.path)
    if kind == "call":
        return u".".join(node.path) + u"(" + u", ".join(Print(arg) for arg in node.args) + u")"
    if kind == "member":
        return _Child(node.target, 10000) + u"." + node.field
    if kind == "index":
        return _Child(node.target, 10000) + u"[" + Print(node.index) + u"]"
    if kind == "unary":
        operand = node.operand
        text = _Child(operand, PREC_UNARY)
        if node.op == "-" and (text.startswith(u"-") or operand.kind == "unary"):
            text = u"(" + text + u")"          # 基岩: 一元负号不能连写(--1 拒载)
        return node.op + text
    if kind == "binary":
        precedence = _BINARY_PRECEDENCE[node.op]
        left = _LogicalChild(node.left, node.op) or _Child(node.left, precedence)
        right = _LogicalChild(node.right, node.op) or _Child(node.right, precedence, strict=True)
        if node.op in ("*", "/") and node.right.kind == "unary" and node.right.op == "-" \
                and not right.startswith(u"("):
            right = u"(" + right + u")"        # 除以负数(v7 语义)保守加括号
        return left + node.op + right
    if kind == "cond":
        return _TestText(node.test) + u"?" + _BranchText(node.then)
    if kind == "ternary":
        return _TestText(node.test) + u"?" + _BranchText(node.then) + u":" + _BranchText(node.other)
    if kind == "assign":
        return Print(node.target) + u"=" + _AssignValueText(node.value)
    if kind == "block":
        return u"{" + u"".join(Print(statement) + u";" for statement in node.body) + u"}"
    if kind == "ret":
        return u"return " + Print(node.value)
    if kind == "brk":
        return u"break"
    if kind == "cont":
        return u"continue"
    if kind == "loop":
        return u"loop(" + Print(node.operand) + u"," + Print(node.body) + u")"
    if kind == "foreach":
        return u"for_each(" + Print(node.target) + u"," + Print(node.iterable) + u"," + Print(node.body) + u")"
    raise ValueError(u"未知节点 {}".format(kind))


def _TestText(node):
    if node.kind in ("ternary", "cond", "assign") or (
            node.kind == "binary" and node.op in ("&&", "||", "??")):
        return u"(" + Print(node) + u")"
    return Print(node)


def _BranchText(node):
    if node.kind in ("ternary", "cond", "assign") or (
            node.kind == "binary" and node.op in ("&&", "||", "??")):
        return u"(" + Print(node) + u")"
    return Print(node)


def _AssignValueText(node):
    if node.kind == "assign":
        return u"(" + Print(node) + u")"
    return Print(node)


def PrintStatements(statements):
    """语句列表 → 带结尾分号的复杂表达式文本"""
    return u"".join(Print(statement) + u";" for statement in statements)


# ======================================================================
# 遍历
# ======================================================================
def Children(node):
    """直接子节点(按求值顺序)"""
    kind = node.kind
    if kind == "call":
        return list(node.args)
    if kind in ("member",):
        return [node.target]
    if kind == "index":
        return [node.target, node.index]
    if kind == "unary":
        return [node.operand]
    if kind == "binary":
        return [node.left, node.right]
    if kind == "cond":
        return [node.test, node.then]
    if kind == "ternary":
        return [node.test, node.then, node.other]
    if kind == "assign":
        return [node.target, node.value]
    if kind == "block":
        return list(node.body)
    if kind == "ret":
        return [node.value]
    if kind == "loop":
        return [node.operand, node.body]
    if kind == "foreach":
        return [node.target, node.iterable, node.body]
    return []


def Walk(node):
    """先序遍历全部节点"""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(Children(current)))


def WalkAll(statements):
    for statement in statements:
        for node in Walk(statement):
            yield node
