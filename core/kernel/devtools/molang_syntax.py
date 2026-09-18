# -*- coding: utf-8 -*-
"""基岩 molang 语法离线判定 + Java 同款"解析失败只作废这一个值"守卫(Python 2.7 / 3 皆可)。

两边对**解析失败**的处理天差地别:
- Java(geckolib MolangParser.parseExpression): 捕获异常返回 FloatValue.ZERO —— 作废的只是
  这一个值: 关键帧的一个分量、timeline 数组的一条、控制器的一个转移条件 / 动画条件 /
  on_entry 条目(client/model/internal/render/AnimationProtoMapper 逐值调用);
- 基岩(网易引擎): 动画文件里**任何一个**表达式解析失败, 整份文件拒载 —— 文件里所有动画 ID
  查无(AddPlayerAnimation 返回 False), 只在 Debug_Log 留一行 ERROR。

2026-09-17 酒狐合集实机(mcdkSelfTest.mcdk_test_probe_model_resources 逐 ID 探针): 7 个包
8 份动画文件整份作废, 换上模型没有并行动画、装饰件全亮、主链动作全空。成因四类:
1. `0.0.x`: Java `ysm.bone_rot('骨骼').x` 读别的骨骼当前旋转(结构体), 函数剥离置 0.0 后
   残留成员访问(移植工具现连成员访问一起置换);
2. `YSM.head_yaw`: Java 词法把标识符整体小写(MolangLexerImpl), 大写前缀漏过全部映射表
   (移植工具现先做大小写归一);
3. 裸标识符 `O`: 作者把数字 0 打成字母 —— Java 同样解析失败 → 0(scale 0 = 隐藏);
4. 多余右括号 `math.sin(q.head_y_rotation))*(...)`: Java "Expected a semicolon" → 0。
3、4 是作者原文在 Java 侧也不成立的表达式, 移植期按 Java 口径落成 0 / 删掉
(GuardAnimationMolang / GuardControllerMolang, 排在所有改写之后), 保住整份文件;
validate_rp_animations.py 用同一判定把残留当错误拦。

**判定口径 = 引擎解析器实测**(2026-09-17 客户端 EvalMolangExpression 逐条喂边界写法, 与
动画文件同一个 molang 解析器; 再拿引擎实际放行 / 拒载的全部 RP 产物回归: 拒载文件逐份命中,
放行文件零误报):
- 放行: 命名空间与关键字大小写不敏感(`V.x`、`Query.Position(0)`、`RETURN 1;`)、`1.0f` / `1e-5` /
  `1.5e+2` / `.5` / `1.`、`true`、`this`、`math.abs (1)`(名字与括号间可空格)、`[1]`、`!!1`、
  `-!1`、`1--1`、`1 < 2 < 3`、`(v.x = 1);`、`v.x = (v.y = 1);`、`return v.x = 1;`、`v.x = 1;;`、
  `q.life_time.x`(查询名后接成员段)、`1 ? {v.x = 1;} : {v.x = 2;};`;
- 拒载: `0.0.x`、`YSM.x`、裸标识符 `O`、`math.sin(1))*(2)`、`(1)(2)` / `2(3)`(隐式乘法)、`1 2`、
  `math . abs(1)`、`+1`(无一元加)、`--1` / `1 * --1`(一元负号不能连写)、`q.life_time()`(空参数表)、
  `(1, 2)`、`f(1,, 3)`、`f(1,)`、`''`(空串)、`0x10`、`1_000`;
  **复杂表达式**(出现任何 `;`, 含块内)每条语句必须以 `;` 结尾(`v.x = 1; return v.x`、`{return 1;}`、
  `loop(2, {v.x = 1;})` 都拒载)、不能以 `;` 开头(`;1`)、`return` 之后不能再有语句、空块 `{}` 拒载;
  单表达式里不能赋值(`v.x = 1`)也不能 `return`; 赋值左侧必须是 variable/temp/context 变量
  (`q.x = 1;`、`(v.x) = 1;`、`1 = 1`), 不能连续赋值(`v.x = v.y = 1;`), 三元分支里不能赋值。
- **含赋值 `=` 就是复杂表达式**(2026-09-18 引擎日志原文 `complex expressions (contains either '=' or ';')
  must end with a ';'`): `q.is_on_ground?(v.x=1):v.x` 不带分号 → 整份文件拒载(官方酒狐 17 号飞机动画);
  `return 1 ? (v.x = 3) : v.x;` 合法且赋值生效、假分支不执行赋值。
- **`??` 的左操作数必须以变量为根**(同日引擎报 `found left-hand-side of ?? expression that isn't a
  direct-variable reference`, EvalMolangExpression 逐条实测): 按 Java 优先级建树、剥掉括号后, 左侧是变量、
  变量取负、含变量的四则运算(`1 + v.zz ?? 2`、`2 * v.a ?? 3`、`-v.qq ?? 5`)合法; 是三元(`1 ? a : b ?? 5`、
  `(1 ? a : 2) ?? 9`)、常量(`3 ?? 1`、`1 + 2 ?? 3`)、查询/函数调用(`q.life_time ?? 1`、`math.abs(v.a) ?? 1`)、
  比较或逻辑运算(`v.a > 1 ?? 4`、`v.a && 1 ?? 7`)非法。EvalMolangExpression 在**带分号的语句里**放行
  `return (1 ? a : b) ?? 5;`, 但资源包文件(旧版语义)连语句里的三元左侧也拒载(语义探针文件
  `variable.ysm_mp_nullish_tern = (1 ? a : b ?? 5);` 让整份探针作废) —— 这里按资源包的严格口径一律判非法。
"""
import re

try:
    _STRING_TYPES = (str, unicode)  # noqa: F821  (py2)
except NameError:
    _STRING_TYPES = (str,)

_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<string>'[^']*')
  | (?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?[fF]?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
  | (?P<op>\?\?|->|==|!=|<=|>=|&&|\|\||[-+*/<>!?:=;,()\[\]{}])
""", re.X)

_NAMESPACES = frozenset(("query", "q", "variable", "v", "temp", "t", "context", "c", "math",
                         "geometry", "texture", "material", "array"))
_ASSIGNABLE_NAMESPACES = frozenset(("variable", "v", "temp", "t", "context", "c"))
_BARE_OPERANDS = frozenset(("this", "true", "false"))
_BINARY_OPERATORS = frozenset(("??", "||", "&&", "==", "!=", "<", "<=", ">", ">=",
                               "+", "-", "*", "/", "->"))


class _MolangSyntaxError(Exception):
    pass


class _Parser(object):
    """递归下降只判合法性(不建树, 不管优先级 —— 优先级不影响"能不能解析")"""

    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0

    def _Peek(self):
        if self.index < len(self.tokens):
            return self.tokens[self.index]
        return ("eof", u"")

    def _IsOp(self, text):
        kind, value = self._Peek()
        return kind == "op" and value == text

    def _Take(self):
        token = self._Peek()
        self.index += 1
        return token

    def _Expect(self, text):
        kind, value = self._Take()
        if kind != "op" or value != text:
            raise _MolangSyntaxError(u"缺少 '{}'(遇到 {})".format(text, value or u"结尾"))

    def Parse(self):
        if not self.tokens:
            raise _MolangSyntaxError(u"空表达式")
        if ("op", ";") in self.tokens:
            self._Statements(closer=None)
            return
        if ("op", "=") in self.tokens:
            # 引擎按有没有 '=' / ';' 判复杂表达式: 括号 / 三元分支里的赋值同样要求以 ';' 结尾
            raise _MolangSyntaxError(u"含赋值的表达式是复杂表达式, 必须以 ';' 结尾")
        # 单表达式: 不能赋值、不能 return(引擎报 no return)
        kind, value = self._Peek()
        if kind == "name" and value.lower() == "return":
            raise _MolangSyntaxError(u"单表达式不能 return(写成复杂表达式要以 ';' 结尾)")
        self._Expression(allowAssign=False)
        kind, value = self._Peek()
        if kind != "eof":
            raise _MolangSyntaxError(u"多余的 '{}'(缺运算符, 或括号不配对)".format(value))

    def _Statements(self, closer):
        """复杂表达式 / 块: 每条语句以 ';' 结尾, 不能以 ';' 开头, return 之后只能跟空语句"""
        count = 0
        returned = False
        while True:
            kind, value = self._Peek()
            if kind == "eof" or (closer and kind == "op" and value == closer):
                if kind == "eof" and closer:
                    raise _MolangSyntaxError(u"块缺少 '{}'".format(closer))
                if not count:
                    raise _MolangSyntaxError(u"空块")
                return
            if kind == "op" and value == ";":
                if not count:
                    raise _MolangSyntaxError(u"以空语句 ';' 开头")
                self._Take()
                continue
            if returned:
                raise _MolangSyntaxError(u"return 之后还有语句")
            returned = self._Statement()
            count += 1
            kind, value = self._Peek()
            if kind == "op" and value == ";":
                self._Take()
                continue
            if kind == "eof" or (closer and kind == "op" and value == closer):
                raise _MolangSyntaxError(u"复杂表达式的语句缺少结尾 ';'")
            raise _MolangSyntaxError(u"多余的 '{}'(缺运算符, 或括号不配对)".format(value))

    def _Statement(self):
        """一条语句; 返回是否 return 语句"""
        kind, value = self._Peek()
        if kind == "name":
            lowered = value.lower()
            if lowered == "return":
                self._Take()
                self._Expression(allowAssign=True)
                return True
            if lowered in ("break", "continue"):
                self._Take()
                return False
        self._Expression(allowAssign=True)
        return False

    def _Expression(self, allowAssign):
        start = self.index
        self._Conditional()
        if self._IsOp("="):
            if not allowAssign:
                raise _MolangSyntaxError(u"此处不能赋值(单表达式 / 连续赋值 / 三元分支)")
            if not self._Assignable(start, self.index):
                raise _MolangSyntaxError(u"赋值左侧不是变量(assignment to non-variable)")
            self._Take()
            self._Expression(allowAssign=False)

    def _Assignable(self, start, end):
        kind, value = self.tokens[start]
        if kind != "name" or "." not in value \
                or value.split(".", 1)[0].lower() not in _ASSIGNABLE_NAMESPACES:
            return False
        if end - start == 1:
            return True
        return self.tokens[start + 1] == ("op", "[") and self.tokens[end - 1] == ("op", "]")

    def _Conditional(self):
        self._Binary()
        if self._IsOp("?"):
            self._Take()
            self._Expression(allowAssign=False)
            if self._IsOp(":"):
                self._Take()
                self._Expression(allowAssign=False)

    def _Binary(self):
        self._Unary()
        while True:
            kind, value = self._Peek()
            if kind != "op" or value not in _BINARY_OPERATORS:
                return
            self._Take()
            self._Unary()

    def _Unary(self):
        previous = None
        while True:
            kind, value = self._Peek()
            if kind != "op" or value not in ("!", "-", "+"):
                break
            if value == "+":
                raise _MolangSyntaxError(u"不支持一元 '+'")
            if value == "-" and previous == "-":
                raise _MolangSyntaxError(u"一元 '-' 不能连写")
            previous = value
            self._Take()
        self._Primary()

    def _Primary(self):
        kind, value = self._Take()
        if kind in ("number", "string"):
            return
        if kind == "eof":
            raise _MolangSyntaxError(u"表达式不完整(意外结尾)")
        if kind == "op":
            if value in ("(", "["):
                self._Expression(allowAssign=True)
                self._Expect(")" if value == "(" else "]")
                return
            if value == "{":
                self._Statements(closer="}")
                self._Expect("}")
                return
            raise _MolangSyntaxError(u"此处需要操作数, 遇到 '{}'".format(value))
        parts = value.split(".")
        head = parts[0].lower()
        if len(parts) == 1:
            if head in ("loop", "for_each") and self._IsOp("("):
                self._Arguments()
                return
            if head in _BARE_OPERANDS:
                return
            raise _MolangSyntaxError(u"裸标识符 {}".format(value))
        if head not in _NAMESPACES:
            raise _MolangSyntaxError(u"未知命名空间 {}".format(parts[0]))
        if self._IsOp("("):
            self._Arguments()
        if self._IsOp("["):
            self._Take()
            self._Expression(allowAssign=True)
            self._Expect("]")

    def _Arguments(self):
        self._Expect("(")
        if self._IsOp(")"):
            raise _MolangSyntaxError(u"空参数表 ()")
        while True:
            self._Expression(allowAssign=True)
            if self._IsOp(","):
                self._Take()
                continue
            self._Expect(")")
            return


def MolangSyntaxProblem(text):
    """基岩解析不了时返回问题描述(unicode), 能解析返回 None; 非字符串(数字/布尔)恒合法"""
    if not isinstance(text, _STRING_TYPES):
        return None
    tokens = []
    position, length = 0, len(text)
    while position < length:
        match = _TOKEN.match(text, position)
        if match is None:
            return u"非法字符 '{}'(第 {} 个字符)".format(text[position], position + 1)
        if match.lastgroup != "space":
            tokens.append((match.lastgroup, match.group(0)))
        position = match.end()
    try:
        _Parser(tokens).Parse()
    except _MolangSyntaxError as exc:
        return exc.args[0]
    if ("op", "??") in tokens:
        return _NullishProblem(text)
    return None


_VARIABLE_ROOT_NAMESPACES = frozenset(("variable", "v", "temp", "t", "context", "c"))
_ARITHMETIC_OPS = frozenset(("+", "-", "*", "/"))


def _VariableRooted(node, text):
    """?? 左操作数是否以变量为根(规则与实测见模块注释): 括号透明; 变量(含 [下标]); 一元运算套变量;
    四则运算里至少一侧以变量为根; ?? 链看它自己的左侧"""
    while node.kind == "group" and node.children and text[node.start:node.start + 1] == "(":
        node = node.children[0]
    if node.kind == "atom" or node.kind == "call":
        source = text[node.start:node.end]
        head, dot, rest = source.partition(".")
        if not dot or head.strip().lower() not in _VARIABLE_ROOT_NAMESPACES:
            return False
        return node.kind == "atom" or "(" not in source
    if node.kind == "unary":
        return _VariableRooted(node.children[0], text)
    if node.kind == "binary":
        if node.op == "??":
            return _VariableRooted(node.children[0], text)
        if node.op in _ARITHMETIC_OPS:
            return any(_VariableRooted(child, text) for child in node.children)
    return False


def _NullishProblem(text):
    """按 Java 优先级建树, 逐个 ?? 检查左操作数; 建不了树(前面的判定已放行的少见写法)时不下结论"""
    try:
        statements = _PrecedenceParser(_PositionedTokens(text)).ParseRoot()
    except (_MolangSyntaxError, IndexError, RuntimeError):
        return None
    pending = list(statements)
    while pending:
        node = pending.pop()
        if node is None:
            continue
        if node.kind == "binary" and node.op == "??" and not _VariableRooted(node.children[0], text):
            return u"?? 的左侧不是变量引用(三元 / 常量 / 查询 / 函数调用 / 比较 / 逻辑运算): {}".format(
                text[node.children[0].start:node.children[0].end])
        pending.extend(node.children or [])
    return None


# ---- 表达式所在位置(槽位): (路径元组, 容器, 键, 处置) ----
# 处置 "value": 解析失败按 Java 落成 0(关键帧分量 / 转移条件 / blend_weight);
# 处置 "drop" : 解析失败按 Java 整条不执行 → 删除(timeline 条目 / on_entry / on_exit /
#              控制器状态里的动画条目 / pre_effect_script)。
# 控制器状态动画条件 Java 是**布尔 apply 条件**, 失败 = 永不应用; 写权重 0 在基岩会暂停计时并
# 卡死 all_animations_finished(见 CLAUDE.md 权重红线), 故删条目而不是写 0。
_CHANNEL_NAMES = ("rotation", "position", "scale")
_ANIMATION_VALUE_FIELDS = ("blend_weight", "anim_time_update", "start_delay", "loop_delay")
_EFFECT_KEYS = ("particle_effects", "sound_effects")


def _VectorSlots(container, key, path, out):
    value = container[key]
    if isinstance(value, _STRING_TYPES):
        out.append((path, container, key, "value"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, _STRING_TYPES):
                out.append((path + (index,), value, index, "value"))


def _ChannelSlots(channels, channel, path, out):
    value = channels[channel]
    if not isinstance(value, dict):
        _VectorSlots(channels, channel, path, out)
        return
    for stamp in value:
        frame = value[stamp]
        if isinstance(frame, dict):
            for side in ("pre", "post"):
                if side in frame:
                    _VectorSlots(frame, side, path + (stamp, side), out)
        else:
            _VectorSlots(value, stamp, path + (stamp,), out)


def _EffectScriptSlots(items, path, out):
    for index, item in enumerate(items):
        if isinstance(item, dict) and isinstance(item.get("pre_effect_script"), _STRING_TYPES):
            out.append((path + (index, "pre_effect_script"), item, "pre_effect_script", "drop"))


def AnimationMolangSlots(body):
    """动画体里全部 molang 表达式槽位(骨骼通道 / timeline / blend_weight 等 / 特效前置脚本)。

    不碰非 molang 字段: 骨骼的 relative_to(`"rotation": "entity"`)、lerp_mode、loop、
    特效名与定位器。
    """
    out = []
    if not isinstance(body, dict):
        return out
    bones = body.get("bones")
    if isinstance(bones, dict):
        for bone in bones:
            channels = bones[bone]
            if not isinstance(channels, dict):
                continue
            for channel in _CHANNEL_NAMES:
                if channel in channels:
                    _ChannelSlots(channels, channel, ("bones", bone, channel), out)
    timeline = body.get("timeline")
    if isinstance(timeline, dict):
        for stamp in timeline:
            entry = timeline[stamp]
            if isinstance(entry, _STRING_TYPES):
                out.append((("timeline", stamp), timeline, stamp, "drop"))
            elif isinstance(entry, list):
                for index, line in enumerate(entry):
                    if isinstance(line, _STRING_TYPES):
                        out.append((("timeline", stamp, index), entry, index, "drop"))
    for field in _ANIMATION_VALUE_FIELDS:
        if isinstance(body.get(field), _STRING_TYPES):
            out.append(((field,), body, field, "value"))
    for effectsKey in _EFFECT_KEYS:
        effects = body.get(effectsKey)
        if isinstance(effects, dict):
            for stamp in effects:
                items = effects[stamp]
                _EffectScriptSlots(items if isinstance(items, list) else [items],
                                   (effectsKey, stamp), out)
    return out


def ControllerMolangSlots(body):
    """动画控制器体里全部 molang 表达式槽位(状态动画条件 / 转移 / on_entry / on_exit / 变量输入)"""
    out = []
    states = body.get("states") if isinstance(body, dict) else None
    if not isinstance(states, dict):
        return out
    for stateName in states:
        state = states[stateName]
        if not isinstance(state, dict):
            continue
        base = ("states", stateName)
        animations = state.get("animations")
        if isinstance(animations, list):
            for index, item in enumerate(animations):
                if isinstance(item, dict):
                    for animKey in item:
                        if isinstance(item[animKey], _STRING_TYPES):
                            out.append((base + ("animations", index, animKey), animations, index, "drop"))
        transitions = state.get("transitions")
        if isinstance(transitions, list):
            for index, item in enumerate(transitions):
                if isinstance(item, dict):
                    for target in item:
                        if isinstance(item[target], _STRING_TYPES):
                            out.append((base + ("transitions", index, target), item, target, "value"))
        for hook in ("on_entry", "on_exit"):
            lines = state.get(hook)
            if isinstance(lines, list):
                for index, line in enumerate(lines):
                    if isinstance(line, _STRING_TYPES):
                        out.append((base + (hook, index), lines, index, "drop"))
        variables = state.get("variables")
        if isinstance(variables, dict):
            for name in variables:
                spec = variables[name]
                if isinstance(spec, dict) and isinstance(spec.get("input"), _STRING_TYPES):
                    out.append((base + ("variables", name, "input"), spec, "input", "value"))
        for effectsKey in _EFFECT_KEYS:
            effects = state.get(effectsKey)
            if isinstance(effects, list):
                _EffectScriptSlots(effects, base + (effectsKey,), out)
    return out


def SlotText(container, key, path):
    """槽位当前文本; 控制器动画条目的槽位容器是条目列表, 键在路径末段"""
    value = container[key]
    if isinstance(value, dict):
        return value.get(path[-1])
    return value


def SlotProblems(slots):
    """只列出解析不了的槽位, 不改数据: [(路径, 原文, 问题)](体检脚本用)"""
    problems = []
    for path, container, key, _mode in slots:
        text = SlotText(container, key, path)
        problem = MolangSyntaxProblem(text)
        if problem is not None:
            problems.append((path, text, problem))
    return problems


def _GuardSlots(slots, zero):
    """解析不了的槽位: value 写 zero, drop 删除; 返回 [(路径, 原文, 问题)]"""
    hits = []
    drops = []
    for path, container, key, mode in slots:
        text = SlotText(container, key, path)
        problem = MolangSyntaxProblem(text)
        if problem is None:
            continue
        hits.append((path, text, problem))
        if mode == "value":
            container[key] = zero
        else:
            drops.append((container, key))
    # 同一列表从后往前删(下标不串位); 同一条目被判两次(多键动画条目)只删一次
    seen = set()
    for container, key in sorted(drops, key=lambda item: (id(item[0]), item[1]), reverse=True):
        marker = (id(container), key)
        if marker in seen:
            continue
        seen.add(marker)
        del container[key]
    return hits


def GuardAnimationMolang(body):
    """动画体里基岩解析不了的表达式按 Java 口径处置(分量 → 0, timeline 条目删除); 返回命中列表。

    删空的 timeline 时间点 / timeline 段一并删掉(空条目引擎同样报错, 见体检脚本)。
    """
    hits = _GuardSlots(AnimationMolangSlots(body), 0.0)
    timeline = body.get("timeline") if hits else None
    if isinstance(timeline, dict):
        for stamp in list(timeline.keys()):
            if timeline[stamp] in ([], u"", ""):
                del timeline[stamp]
        if not timeline:
            del body["timeline"]
    return hits


def GuardControllerMolang(body):
    """控制器体里基岩解析不了的表达式按 Java 口径处置(转移条件 → 永不成立, 动画条目 / on_entry /
    on_exit 删除); 返回命中列表。删空的列表键一并删掉 —— 空 animations 数组会让整份控制器文件拒载。"""
    hits = _GuardSlots(ControllerMolangSlots(body), u"0.0")
    states = body.get("states") if hits else None
    if isinstance(states, dict):
        for state in states.values():
            if not isinstance(state, dict):
                continue
            for key in ("animations", "on_entry", "on_exit"):
                if key in state and state[key] == []:
                    del state[key]
    return hits


def FormatSlotPath(path):
    return u"/".join(u"{}".format(part) for part in path)


# ============ 运算符优先级显式化(旧版 Molang 语义下与 Java 同值) ============
# 引擎按资源包 min_engine_version 映射 MolangVersion(引擎头文件 MolangVersion.h): v5 三元运算符改右结合、
# v6 比较与逻辑运算符优先级、v7 负数除法。本包 manifest 写 1.18.0, **资源包文件**(动画/控制器)里的
# 表达式按旧语义解析; Python 注册的 animate 条件与 EvalMolangExpression 按新语义。2026-09-17 实机探针
# (mcdkSelfTest.mcdk_test_molang_semantics_*, 同一批式子三个上下文各算一遍):
#   `1 ? 0 : 1 ? 2 : 3`  资源包 3 / 其余 0   —— 旧语义三元左结合 `(1 ? 0 : 1) ? 2 : 3`
#   `1 || 0 && 0`        资源包 0 / 其余 1   —— 旧语义 && 不比 || 紧, 从左往右
#   `1 == 1 && 2 == 2`   三处都是 1          —— 比较仍比逻辑紧
#   `4 / -2`             三处都是 -2
# Java(molang/parser/MolangParserImpl)是 C 口径: `=` < `??` < `?:`(右结合) < `||` < `&&` < `==`/`!=` <
# 比较 < `+-` < `*/` < 一元 < `->`, 同级左结合。移植产物按 Java 口径建树, 只在两种语义可能分叉的位置
# 插括号, 其余字符一个不动(幂等: 括号本身就是分组, 第二遍不再插):
#   ① 三元作为任何运算的操作数 / 另一个三元的条件或分支 → 整个三元加括号(赋值右侧除外);
#   ② && 与 || 互为操作数 → 内层加括号;
#   ③ 等值(== !=)与大小比较(< <= > >=)互为操作数, 或同类比较连写(a < b < c) → 内层加括号;
#   ④ ?? 与任何二元运算互为操作数 → 内层加括号;
#   ⑤ 三元的条件 / 分支是 && || ?? 运算 → 加括号(旧版三元与逻辑运算的相对优先级没有实测, 保守);
#   ⑥ 一元负号作 * / 的右操作数 → 加括号(v7 DivideByNegativeValue 的具体变化未知, 保守)。
# 比较作逻辑运算的操作数(`a == 1 && b == 2`)与算术不加括号: 实测两种语义同值, 加了只会让产物面目全非。
_PREC_BINARY = {
    "??": 1200, "||": 1600, "&&": 1800, "==": 2000, "!=": 2000,
    "<": 2200, "<=": 2200, ">": 2200, ">=": 2200,
    "+": 2400, "-": 2400, "*": 2600, "/": 2600, "->": 3000,
}
_PREC_TERNARY = 1400
_PREC_ASSIGN = 1
_PREC_UNARY = 2800
_LOGICAL_OPS = frozenset(("&&", "||"))
_EQUALITY_OPS = frozenset(("==", "!="))
_RELATIONAL_OPS = frozenset(("<", "<=", ">", ">="))
_PRECEDENCE_MARKERS = ("?", "&&", "||", "==", "!=", "<", ">", "-")


class _PNode(object):
    """带源码字符区间的语法树节点: kind 为 atom/unary/binary/ternary/assign/group/call/block"""
    __slots__ = ("kind", "op", "children", "start", "end")

    def __init__(self, kind, start, end, op=None, children=None):
        self.kind = kind
        self.op = op
        self.children = children or []
        self.start = start
        self.end = end


class _PrecedenceParser(object):
    """Java 优先级爬升(MolangParserImpl.parseCompound 同构), 只为定位需要加括号的子表达式"""

    def __init__(self, tokens):
        self.tokens = tokens          # [(kind, text, start, end)]
        self.index = 0

    def _Peek(self, offset=0):
        position = self.index + offset
        if position < len(self.tokens):
            return self.tokens[position]
        end = self.tokens[-1][3] if self.tokens else 0
        return ("eof", u"", end, end)

    def _IsOp(self, text, offset=0):
        token = self._Peek(offset)
        return token[0] == "op" and token[1] == text

    def _Take(self):
        token = self._Peek()
        self.index += 1
        return token

    def _Expect(self, text):
        token = self._Take()
        if token[0] != "op" or token[1] != text:
            raise _MolangSyntaxError(u"缺少 '{}'".format(text))
        return token

    def ParseRoot(self):
        """→ 语句节点列表(单表达式也包成一条)"""
        statements = self._Statements(closer=None)
        if self._Peek()[0] != "eof":
            raise _MolangSyntaxError(u"多余的 token")
        return statements

    def _Statements(self, closer):
        statements = []
        while True:
            token = self._Peek()
            if token[0] == "eof" or (closer and token[0] == "op" and token[1] == closer):
                return statements
            if token[0] == "op" and token[1] == ";":
                self._Take()
                continue
            if token[0] == "name" and token[1].lower() in ("return", "break", "continue"):
                self._Take()
                if token[1].lower() == "return":
                    value = self._Parse(-10)
                    statements.append(_PNode("return", token[2], value.end, children=[value]))
                else:
                    statements.append(_PNode("keyword", token[2], token[3], op=token[1].lower()))
                continue
            statements.append(self._Parse(-10))
            if not (self._IsOp(";") or self._Peek()[0] == "eof"
                    or (closer and self._IsOp(closer))):
                raise _MolangSyntaxError(u"语句之间缺少 ';'")

    def _Parse(self, lastPrecedence):
        node = self._Unary()
        while True:
            token = self._Peek()
            if token[0] != "op":
                return node
            text = token[1]
            if text == "?":
                if lastPrecedence > _PREC_TERNARY:
                    return node
                self._Take()
                then = self._Parse(_PREC_TERNARY)
                children = [node, then]
                if self._IsOp(":"):
                    self._Take()
                    children.append(self._Parse(_PREC_TERNARY))
                node = _PNode("ternary", node.start, children[-1].end, children=children)
                continue
            if text == "=":
                if lastPrecedence >= _PREC_ASSIGN:
                    return node
                self._Take()
                value = self._Parse(_PREC_ASSIGN)
                node = _PNode("assign", node.start, value.end, children=[node, value])
                continue
            precedence = _PREC_BINARY.get(text)
            if precedence is None or lastPrecedence >= precedence:
                return node
            self._Take()
            right = self._Parse(precedence)
            node = _PNode("binary", node.start, right.end, op=text, children=[node, right])

    def _Unary(self):
        token = self._Peek()
        if token[0] == "op" and token[1] in ("!", "-"):
            self._Take()
            operand = self._Parse(_PREC_UNARY)
            return _PNode("unary", token[2], operand.end, op=token[1], children=[operand])
        return self._Primary()

    def _Primary(self):
        kind, text, start, end = self._Take()
        if kind in ("number", "string"):
            return _PNode("atom", start, end)
        if kind == "op" and text == "(":
            inner = self._Parse(-10)
            close = self._Expect(")")
            return self._Postfix(_PNode("group", start, close[3], children=[inner]))
        if kind == "op" and text == "{":
            statements = self._Statements(closer="}")
            close = self._Expect("}")
            return _PNode("block", start, close[3], children=statements)
        if kind == "op" and text == "[":
            inner = self._Parse(-10)
            close = self._Expect("]")
            return _PNode("group", start, close[3], children=[inner])
        if kind != "name":
            raise _MolangSyntaxError(u"此处需要操作数")
        return self._Postfix(_PNode("atom", start, end))

    def _Postfix(self, node):
        while True:
            if self._IsOp("("):
                self._Take()
                args = []
                if not self._IsOp(")"):
                    while True:
                        args.append(self._Parse(-10))
                        if self._IsOp(","):
                            self._Take()
                            continue
                        break
                close = self._Expect(")")
                node = _PNode("call", node.start, close[3], children=args)
                continue
            if self._IsOp("["):
                self._Take()
                inner = self._Parse(-10)
                close = self._Expect("]")
                node = _PNode("call", node.start, close[3], children=[inner])
                continue
            return node


def _PositionedTokens(text):
    tokens = []
    position, length = 0, len(text)
    while position < length:
        match = _TOKEN.match(text, position)
        if match is None:
            raise _MolangSyntaxError(u"非法字符")
        if match.lastgroup != "space":
            tokens.append((match.lastgroup, match.group(0), match.start(), match.end()))
        position = match.end()
    return tokens


def _NeedsParens(node, parent, role):
    if parent is None:
        return False
    if node.kind == "ternary":
        if parent.kind == "assign":
            return False
        return parent.kind in ("binary", "unary", "ternary")
    if node.kind == "binary":
        op = node.op
        if parent.kind == "binary":
            parentOp = parent.op
            if op in _LOGICAL_OPS and parentOp in _LOGICAL_OPS:
                return op != parentOp
            if (op in _EQUALITY_OPS and parentOp in _RELATIONAL_OPS) \
                    or (op in _RELATIONAL_OPS and parentOp in _EQUALITY_OPS):
                return True
            if op in _RELATIONAL_OPS and parentOp in _RELATIONAL_OPS:
                return True
            if op in _EQUALITY_OPS and parentOp in _EQUALITY_OPS:
                return True
            return op == "??" or parentOp == "??"
        if parent.kind == "ternary":
            return op in _LOGICAL_OPS or op == "??"
        return False
    if node.kind == "unary" and node.op == "-" and parent.kind == "binary" \
            and parent.op in ("*", "/") and role == "right":
        return True
    return False


def _CollectParens(node, parent, role, out):
    if _NeedsParens(node, parent, role):
        out.append((node.start, node.end))
    if node.kind == "binary":
        _CollectParens(node.children[0], node, "left", out)
        _CollectParens(node.children[1], node, "right", out)
    elif node.kind == "unary":
        _CollectParens(node.children[0], node, "operand", out)
    elif node.kind == "ternary":
        for index, child in enumerate(node.children):
            _CollectParens(child, node, ("cond", "then", "else")[index], out)
    elif node.kind == "assign":
        _CollectParens(node.children[0], node, "target", out)
        _CollectParens(node.children[1], node, "value", out)
    elif node.kind in ("group", "call", "block", "return"):
        for child in node.children:
            _CollectParens(child, None, "top", out)


def ExplicitPrecedence(text):
    """表达式里新旧 Molang 语义可能分叉的位置补括号(规则见上方长注); 返回 (新文本, 插入的括号对数)。

    非字符串 / 解析不了的原样返回(解析失败交给 GuardAnimationMolang / GuardControllerMolang)。
    """
    if not isinstance(text, _STRING_TYPES) or not text:
        return text, 0
    if not any(marker in text for marker in _PRECEDENCE_MARKERS):
        return text, 0
    try:
        tokens = _PositionedTokens(text)
        if not tokens:
            return text, 0
        statements = _PrecedenceParser(tokens).ParseRoot()
    except (_MolangSyntaxError, IndexError, RuntimeError):
        return text, 0
    spans = []
    for statement in statements:
        _CollectParens(statement, None, "top", spans)
    spans = set(spans)
    if not spans:
        return text, 0
    opens, closes = {}, {}
    for start, end in spans:
        opens[start] = opens.get(start, 0) + 1
        closes[end] = closes.get(end, 0) + 1
    pieces = []
    for position in range(len(text) + 1):
        if position in closes:
            pieces.append(u")" * closes[position])
        if position in opens:
            pieces.append(u"(" * opens[position])
        if position < len(text):
            pieces.append(text[position])
    return u"".join(pieces), len(spans)


def ExplicitPrecedenceInSlots(slots):
    """槽位列表里的表达式逐个显式化优先级(就地改写); 返回 [(路径, 原文, 新文)]"""
    changes = []
    for path, container, key, _mode in slots:
        value = container[key]
        if isinstance(value, dict):          # 控制器状态动画条目 {动画: 条件}
            animKey = path[-1]
            text = value.get(animKey)
            newText, count = ExplicitPrecedence(text)
            if count:
                value[animKey] = newText
                changes.append((path, text, newText))
            continue
        newText, count = ExplicitPrecedence(value)
        if count:
            container[key] = newText
            changes.append((path, value, newText))
    return changes


def ExplicitAnimationPrecedence(body):
    """动画体全部表达式槽位显式化优先级; 返回改动列表"""
    return ExplicitPrecedenceInSlots(AnimationMolangSlots(body))


def ExplicitControllerPrecedence(body):
    """控制器体全部表达式槽位显式化优先级; 返回改动列表"""
    return ExplicitPrecedenceInSlots(ControllerMolangSlots(body))


def ParseStatementTree(text):
    """molang 文本 → 语句节点列表(带源码区间; return / keyword / 表达式节点), 解析不了抛 ValueError。

    给 Java 脚本控制器转换用(devtools/script_controller.py): 调用方先剥掉 // 注释。
    """
    try:
        tokens = _PositionedTokens(text)
        return _PrecedenceParser(tokens).ParseRoot()
    except (_MolangSyntaxError, IndexError) as error:
        raise ValueError(u"molang 脚本解析失败: {}".format(error))

