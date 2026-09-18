# -*- coding: utf-8 -*-
"""Java 脚本控制器(functions/*@player_ctrl_<通道>.molang) → Java 格式的基岩式动画控制器(Python 2.7 / 3)。

Java(geckolib3 CodedAnimationController + CtrlBinding): 模型 functions/ 目录里名为
`<任意>@player_ctrl_<通道>.molang` 的脚本是该通道的"事件谓词", **每帧**执行一遍:
`ctrl.set_animation('名')` 选动画、`ctrl.set_beginning_transition_length(秒)` 设起始过渡,
`return ctrl.state_continue` 播放 / `state_stop` 停止 / `state_pause` 暂停, 其余返回值(含
`state_bypass`、脚本走完)交回通道的内置谓词。pre_main/post_main/pre_hold/... 这些"空白通道"
(PlayerControllerCollection.multi, EmptyPredicate)的内置谓词就是 STOP, 所以放行 = 不播。
MultiControllerDiscovery 按脚本名建出 `player.<通道>` 控制器 —— 没有同名基岩控制器也会挂上。

基岩没有每帧脚本, 但这类脚本几乎都是"条件块套条件块、叶子上 set_animation + return"的决策树
(官方酒狐 15 号 @player_ctrl_pre_main: 按 idle/walk/run/... × 是否开车 × 饱食度选动画; 它的
idle/walk/run 主链动画是空的, 姿态全靠这份脚本 —— 不转换就是直立不动)。决策树展开成**有序规则表**:
每个 return 一条规则, 条件 = 路径上各分支条件相与; 文本序在前的 return 先执行, 于是"本帧播哪个"就是
"第一条条件成立的规则"。每条规则一个状态, 转移按首个命中:
- 从状态 i 去更早的规则 j: 条件 C_j(优先级更高, 成立就走);
- 去更晚的规则 j: `!(C_i) && (C_j)`(自己不再成立才让位; 转移表按序取第一个成立者, 中间的已排在前面)。
路径上的常量赋值(`v.anim_ctrl=1;`)落到该状态 on_entry(每帧重复赋同一个值, 进入时赋一次等价);
起始过渡写成该状态的 blend_transition(Java 口径 = 被进入的状态, 移植工具随后按目标重映射)。

不转换(整份跳过并留痕, 宁缺勿错): 内置谓词不是空的通道(main/use/swing/parallel_N 等 —— 放行要交回
内置谓词, 基岩侧对应物是主链状态机/一次性通道, 拆不开)、同名基岩控制器已存在(Java 优先用基岩控制器)、
非常量赋值(计数器/随机数/逐帧积分)、循环、set_animation 带循环类型参数、走到 continue 时本路径没选过
动画(Java 沿用上一帧的动画, 状态相关)、分支块在不 return 的路径上改了状态。
声音 / indicate_reload / reset 之类副作用调用忽略(基岩表达式层无对应)。
"""
import io
import os
import re
from collections import OrderedDict

import molang_syntax as ms

_SCRIPT_NAME = re.compile(r"@player_ctrl_([A-Za-z0-9_]+)\.molang$")
# 内置谓词为空(EmptyPredicate)的多实例通道: 放行 = 不播
_EMPTY_PREDICATE_CHANNEL = re.compile(r"^(?:pre|post)_(?:main|hold|swing|use)(?:_.+)?$")
_IGNORED_CALLS = ("ysm.play_sound", "ysm.stop_sound", "ysm.stop_all_sounds",
                  "ctrl.indicate_reload", "ctrl.reset")
_RETURN_ACTIONS = OrderedDict([
    ("ctrl.state_continue", "continue"),
    ("ctrl.state_stop", "stop"),
    ("ctrl.state_pause", "pause"),
    ("ctrl.state_bypass", "bypass"),
])
_CONSTANT = re.compile(r"^\s*-?(?:\d+(?:\.\d*)?|\.\d+)\s*$")
_NAME_AT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
STATE_PREFIX = "ysm_script_"


class ScriptNotConvertible(Exception):
    pass


def StripComments(text):
    """剥掉 // 行注释与 /* */ 块注释(单引号字符串里的不动)"""
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
            continue
        out.append(char)
        index += 1
    return u"".join(out)


def ScriptChannel(fileName):
    """脚本文件名 → 通道名(`car_stuff@player_ctrl_parallel_6.molang` → parallel_6); 不是通道脚本返回 None"""
    matched = _SCRIPT_NAME.search(fileName)
    return matched.group(1).lower() if matched else None


class _PathState(object):
    __slots__ = ("animation", "blend", "effects")

    def __init__(self, animation=None, blend=None, effects=None):
        self.animation = animation
        self.blend = blend
        self.effects = OrderedDict(effects or ())

    def Copy(self):
        return _PathState(self.animation, self.blend, self.effects)

    def Key(self):
        return (self.animation, self.blend, tuple(self.effects.items()))


class _Converter(object):
    def __init__(self, text):
        self.text = text
        self.rules = []          # [(条件列表, 路径状态, 动作)]
        self.notes = []

    def _Source(self, node):
        return self.text[node.start:node.end].strip()

    def _CallName(self, node):
        matched = _NAME_AT.match(self.text, node.start)
        return matched.group(0).lower() if matched else u""

    def Walk(self, statements, conditions, state):
        """顺序执行语句; 返回本序列是否在所有路径上都已 return"""
        for node in statements:
            if node.kind == "return":
                action = _RETURN_ACTIONS.get(self._Source(node.children[0]).lower(), "bypass")
                self.rules.append((list(conditions), state.Copy(), action))
                return True
            if node.kind == "keyword":
                raise ScriptNotConvertible(u"break/continue(循环)")
            if node.kind == "block":
                if self.Walk(node.children, conditions, state):
                    return True
                continue
            if node.kind == "ternary":
                if self._Ternary(node, conditions, state):
                    return True
                continue
            if node.kind == "assign":
                self._Assign(node, state)
                continue
            if node.kind == "call":
                self._Call(node, state)
                continue
            if node.kind in ("atom", "group"):
                continue
            raise ScriptNotConvertible(u"语句形态 {}: {}".format(node.kind, self._Source(node)[:60]))
        return False

    def _Ternary(self, node, conditions, state):
        condition = self._Source(node.children[0])
        branches = node.children[1:]
        if any(branch.kind != "block" for branch in branches):
            raise ScriptNotConvertible(u"三元语句的分支不是块: {}".format(self._Source(node)[:60]))
        results = []
        for index, branch in enumerate(branches):
            branchConditions = conditions + [condition if index == 0 else u"!({})".format(condition)]
            branchState = state.Copy()
            terminated = self.Walk(branch.children, branchConditions, branchState)
            if not terminated and branchState.Key() != state.Key():
                raise ScriptNotConvertible(u"分支在不 return 的路径上改了状态: {}".format(condition[:60]))
            results.append(terminated)
        return len(results) == 2 and all(results)

    def _Assign(self, node, state):
        target = self._Source(node.children[0])
        value = self._Source(node.children[1])
        lowered = target.lower()
        if not (lowered.startswith(u"v.") or lowered.startswith(u"variable.")):
            raise ScriptNotConvertible(u"赋值目标不是实体变量: {}".format(target))
        if value.lower() in (u"true", u"false"):
            value = u"1" if value.lower() == u"true" else u"0"
        if not _CONSTANT.match(value):
            raise ScriptNotConvertible(u"非常量赋值(逐帧计算): {} = {}".format(target, value[:40]))
        state.effects[target] = value.strip()

    def _Call(self, node, state):
        name = self._CallName(node)
        if name == u"ctrl.set_animation":
            if len(node.children) != 1 or node.children[0].kind != "atom" \
                    or not self._Source(node.children[0]).startswith(u"'"):
                raise ScriptNotConvertible(u"set_animation 参数不是单个字符串字面量")
            state.animation = self._Source(node.children[0])[1:-1]
            return
        if name == u"ctrl.set_beginning_transition_length":
            value = self._Source(node.children[0]) if len(node.children) == 1 else u""
            if not _CONSTANT.match(value):
                raise ScriptNotConvertible(u"过渡时长不是常量: {}".format(value[:40]))
            state.blend = float(value)
            return
        if name in _IGNORED_CALLS:
            self.notes.append(u"忽略副作用调用 {}".format(name))
            return
        raise ScriptNotConvertible(u"不支持的调用 {}".format(self._Source(node)[:60]))


def _Condition(conditions):
    if not conditions:
        return u"1"
    if len(conditions) == 1:
        return conditions[0]
    return u"&&".join(u"({})".format(item) for item in conditions)


def ConvertScript(text, channel, defaultBlend=0.0):
    """脚本文本 → (Java 格式控制器文件数据, 备注列表); 不能可靠转换时抛 ScriptNotConvertible"""
    if not _EMPTY_PREDICATE_CHANNEL.match(channel):
        raise ScriptNotConvertible(u"通道 {} 的内置谓词不是空的(放行要交回内置动画, 基岩侧拆不开)".format(channel))
    stripped = StripComments(text)
    try:
        statements = ms.ParseStatementTree(stripped)
    except ValueError as error:
        raise ScriptNotConvertible(u"{}".format(error))
    converter = _Converter(stripped)
    if not converter.Walk(statements, [], _PathState()):
        # 走完脚本没有 return: Java 返回值为 0 → 交回内置谓词(空通道 = 不播)
        converter.rules.append(([], _PathState(), "bypass"))
    rules = []
    for conditions, state, action in converter.rules:
        rules.append((conditions, state, action))
        if not conditions:
            break                        # 无条件 return 之后的规则不可达
    else:
        rules.append(([], _PathState(), "bypass"))
    if not any(action == "continue" for _conditions, _state, action in rules):
        raise ScriptNotConvertible(u"脚本从不播放动画")
    names = []
    states = OrderedDict()
    texts = [_Condition(conditions) for conditions, _state, _action in rules]
    for index, (conditions, state, action) in enumerate(rules):
        name = u"{}{}".format(STATE_PREFIX, index + 1)
        names.append(name)
    for index, (conditions, state, action) in enumerate(rules):
        body = OrderedDict()
        if action == "continue":
            if state.animation is None:
                raise ScriptNotConvertible(u"continue 路径上没有选动画(Java 沿用上一帧的动画)")
            body["animations"] = [state.animation]
        if state.effects:
            body["on_entry"] = [u"{}={};".format(target, value) for target, value in state.effects.items()]
        transitions = []
        own = texts[index]
        for other, otherText in enumerate(texts):
            if other == index:
                continue
            if other < index:
                transitions.append(OrderedDict([(names[other], otherText)]))
            elif own != u"1":
                condition = u"!({})".format(own) if otherText == u"1" \
                    else u"!({})&&({})".format(own, otherText)
                transitions.append(OrderedDict([(names[other], condition)]))
        if transitions:
            body["transitions"] = transitions
        body["blend_transition"] = state.blend if state.blend is not None else defaultBlend
        states[names[index]] = body
    controller = OrderedDict([("initial_state", names[-1]), ("states", states)])
    data = OrderedDict([
        ("format_version", "1.19.0"),
        ("animation_controllers", OrderedDict([(u"player.{}".format(channel), controller)])),
    ])
    notes = sorted(set(converter.notes))
    return data, notes


def ConvertPackScripts(javaDir, declaredControllerNames):
    """Java 包 functions/ 里的通道脚本 → ([(通道, 控制器数据, 备注)], [(文件名, 跳过原因)])。

    declaredControllerNames: 包里已声明的基岩控制器名集合(同名时 Java 优先用基岩控制器, 脚本不转换)。
    """
    converted, skipped = [], []
    functionsDir = os.path.join(javaDir, "functions")
    if not os.path.isdir(functionsDir):
        return converted, skipped
    for fileName in sorted(os.listdir(functionsDir)):
        displayName = fileName.decode("utf-8") if isinstance(fileName, bytes) else fileName
        channel = ScriptChannel(displayName)
        if channel is None:
            continue
        if u"player.{}".format(channel) in declaredControllerNames:
            skipped.append((displayName, u"已有同名基岩控制器 player.{}(Java 优先用它)".format(channel)))
            continue
        with io.open(os.path.join(functionsDir, fileName), encoding="utf-8-sig") as handle:
            text = handle.read()
        try:
            data, notes = ConvertScript(text, channel)
        except ScriptNotConvertible as error:
            skipped.append((displayName, u"{}".format(error)))
            continue
        converted.append((channel, data, notes))
    return converted, skipped
