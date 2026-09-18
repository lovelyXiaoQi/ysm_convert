# -*- coding: utf-8 -*-
"""资源包动画/控制器产物的**引擎红线**离线体检(Python 2.7 / 3 皆可)。

网易引擎对动画与控制器文件的容错极差, 且失败方式是**整份文件静默作废**或只在
Debug_Log 里留一行 ERROR —— 游戏里只看到"模型停在绑定姿态 / 换装件全亮":
- 动画: 一个 `"bones": {}` 空节点 → "Required child ... not found / node parse failed:
  bones", 该文件全部动画不可用(2026-09-03 ref_warden jump_fall / ref_sahmet pre_parallel2);
- 动画: timeline 语句里出现表达式内赋值(`v.x || v.x = 0;`) → "assignment to
  non-variable not allowed", 整条语句作废并逐次刷错;
- 控制器: 状态里的 `"animations": []` 空数组 → 整份控制器文件拒载, **不打任何日志**;
- 控制器/动画: 残缺的 `ctrl.` / `ysm.` / `fn.` 前缀 → unrecognized token, 整个控制器作废;
- 资源 ID 含大写/`$`/`:` → 整份文件解析失败;
- 动画/控制器: 任何一个 molang 表达式基岩解析不了(`0.0.x`、`YSM.head_yaw`、裸标识符、括号不配对、
  复杂表达式语句缺结尾分号……)→ 整份文件拒载(2026-09-17 官方酒狐 7 个包 8 份文件, 判定口径 =
  引擎解析器实测, 见 molang_syntax.py)。
这些形态由移植/修复工具保证不产出(port_java_pack.SanitizeAnimationBody 等), 本脚本是
最后一道围栏: 改过产物后跑一遍, 有 ERROR 即退出码 1。

运行: python devtools/validate_rp_animations.py [包名 ...]   (不给包名 = 全部 RP 文件)
"""
from __future__ import print_function
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RP = os.path.join(ROOT, "ysm_rp")
BP_MODELS = os.path.join(ROOT, "ysm_bp", "ysm_models")


def SetLayout(rp=None, bpModels=None, root=None):
    """重定向被体检的产物目录(转换器宿主用); root 只影响报告里的相对路径"""
    global RP, BP_MODELS, ROOT
    if rp is not None:
        RP = rp
    if bpModels is not None:
        BP_MODELS = bpModels
    if root is not None:
        ROOT = root


def _DisplayPath(path):
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:  # 产物与脚本不在同一盘符
        return path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import molang_syntax  # noqa: E402  基岩 molang 语法判定(移植工具的守卫用同一实现)

_CHANNELS = ("rotation", "position", "scale")


def _FrameIsExpression(frame):
    """关键帧取值里是否含 molang 表达式字符串(裸数组 / pre / post 三种形态都看)"""
    items = []
    if isinstance(frame, dict):
        for side in ("pre", "post"):
            sideValue = frame.get(side)
            if isinstance(sideValue, list):
                items += sideValue
            elif sideValue is not None:
                items.append(sideValue)
    elif isinstance(frame, list):
        items = frame
    else:
        items = [frame]
    return any(isinstance(item, _STRING_TYPES) for item in items)


def _StampOrder(value):
    """通道关键帧字典 → 时间戳升序列表(非数值键排末尾)"""
    stamps = []
    tail = []
    for stamp in value.keys():
        try:
            stamps.append((float(stamp), stamp))
        except (TypeError, ValueError):
            tail.append(stamp)
    stamps.sort()
    return [stamp for _t, stamp in stamps] + tail


def _CatmullromNearExpressionStamps(value):
    """通道里「lerp_mode 为 catmullrom、且关键帧 i-1..i+2 里有 molang 表达式帧」的时间戳。

    **引擎判据(2026-09-16 实测定案)**: 网易引擎加载时对 catmullrom 关键帧 i 预计算三次样条,
    控制点取关键帧 i-1、i、i+1、i+2, 其中任一帧含表达式就在 Debug_Log 报
    `Precomputed cubic interpolation requires keyframes have constant data`, 该段退化成
    "停在起点值、到下一帧再跳过去"。对部署产物逐通道检验, 这个窗口与日志里 9 条报错动画
    逐条、逐通道数量完全吻合(对称窗口 [i-2, i+2] 多报, 旧的 ±1 邻域一条都抓不到)。
    实机(2026-09-05 warden 拉弓): use_mainhand.cls.bow 的 UpBody 起手半秒停在侧面再跳到正面。
    """
    if not isinstance(value, dict):
        return []
    order = _StampOrder(value)
    flags = [_FrameIsExpression(value[stamp]) for stamp in order]
    if not any(flags):
        return []
    hits = []
    for index, stamp in enumerate(order):
        frame = value[stamp]
        if not isinstance(frame, dict) or frame.get("lerp_mode") != "catmullrom":
            continue
        if any(flags[max(0, index - 1):index + 3]):
            hits.append(stamp)
    return hits


_CHANNEL_DEFAULTS = {"rotation": 0.0, "position": 0.0, "scale": 1.0}


def _PostOnlyLinearStamps(value, channel):
    """通道里「只写 post、入段是线性、值不等于通道默认值」的关键帧时间戳。

    **实机(2026-09-16 骨骼缩放探针)**: 线性段 [i-1, i] 的终点取关键帧 i 的 pre; i 只写 post 时
    引擎把 pre 当成**通道默认值**(scale 1、rotation/position 0), 不是 post: 凋灵娘火焰精灵帧
    `0.25: {pre 1, post 0}` → `0.5: {"post": [0,0,0], "lerp_mode": "linear"}`(移植工具补的收尾帧)
    这一段从 0 线性涨到 1(首帧值是 0, 排除了"回绕到首帧"的解释), death 的 Root 同样涨回 1。
    Java(JsonKeyFrameUtils)把只写的 post 同时当 pre。Blockbench 导出的平滑帧
    `{"post": …, "lerp_mode": "catmullrom"}` 是另一回事(入段是样条), 不在此列。
    """
    if not isinstance(value, dict):
        return []
    order = _StampOrder(value)
    hits = []
    for index, stamp in enumerate(order):
        frame = value[stamp]
        if index == 0 or not isinstance(frame, dict) or "post" not in frame or "pre" in frame:
            continue
        previous = value[order[index - 1]]
        if isinstance(previous, dict) and previous.get("lerp_mode") == "catmullrom":
            continue
        post = frame["post"]
        items = post if isinstance(post, list) else [post]
        default = _CHANNEL_DEFAULTS.get(channel, 0.0)
        if all(isinstance(item, (int, float)) and abs(item - default) < 1e-9 for item in items):
            continue
        hits.append(stamp)
    return hits

_BONE_EXTRA_KEYS = ("relative_to",)
_RESOURCE_ID_OK = re.compile(r"^[a-z0-9_.\-]+$")
# 表达式内赋值: 逻辑运算符之后紧跟 `v.x =`(不是 ==)
_ASSIGN_IN_EXPR = re.compile(r"(?:\|\||&&|\?\?)\s*\(?\s*(?:v|variable)\.[A-Za-z_][A-Za-z0-9_.]*\s*=(?!=)")
_DANGLING_JAVA_PREFIX = re.compile(r"(?:ysm|ctrl|fn|tlm|args)\.(?![A-Za-z_])")
_RESIDUAL_JAVA_TOKEN = re.compile(r"\b(?:ysm|ctrl|fn|tlm|args)\.[A-Za-z_][A-Za-z0-9_.]*")
# Java 解析器把 `(a)(b)` 当乘法, 基岩当非法调用; true/false 关键字基岩是否认无证据(移植工具改 1.0/0.0)
_IMPLICIT_MULTIPLICATION = re.compile(r"\)\s*\(")
_BOOLEAN_KEYWORD = re.compile(r"\b(?:true|false)\b")
_MOLANG_QUOTED = re.compile(r"'[^']*'")
# 输入状态锁存/挥动序号(主包 java_input_state 动画 + 挥击状态机维护, packParser._INPUT_STATE_VARIABLE_PATTERN):
# 不参与"文件扫描补 0"初始化, 所以每处读取都必须带 ?? 回落 —— 裸读在共享动画缺席或首帧求值失败。
# 同一字符串里先赋值后读取的(共享动画自身的语句序列)不算
_INPUT_STATE_VARIABLE_TOKEN = re.compile(
    r"variable\.(ysm_(?:block_hold|item_in_use|use_hold|swing_muted|swing_serial|(?:fp_)?swing_seen))\b(\s*\?\?|\s*=(?!=))?")
_KEYFRAME_KEYS = ("pre", "post", "lerp_mode")
_LOOP_VALUES = (True, False, "hold_on_last_frame")

_STRING_TYPES = (str, u"".__class__)


def _LoadJson(path):
    """引擎与移植工具都容忍 JSONC(注释/尾逗号, 如 compat/ysm_fight.animation_controllers.json),
    这里同样先严格解析, 失败再走 port_java_pack.LoadJson 的注释剥离"""
    with io.open(path, "r", encoding="utf-8-sig") as handle:
        text = handle.read()
    try:
        return json.loads(text)
    except ValueError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import port_java_pack as port
        return port.LoadJson(path)


def _WalkStrings(node, path, out):
    if isinstance(node, dict):
        for key, value in node.items():
            _WalkStrings(value, path + [key], out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _WalkStrings(value, path + [index], out)
    elif isinstance(node, _STRING_TYPES):
        out.append((path, node))


def _CheckMolangStrings(node, where, errors, warnings=None):
    if warnings is None:
        warnings = []
    strings = []
    _WalkStrings(node, [], strings)
    for path, text in strings:
        if _ASSIGN_IN_EXPR.search(text):
            errors.append(u"{} {}: 表达式内赋值(引擎: assignment to non-variable not allowed): {}".format(
                where, u"/".join(str(p) for p in path), text[:80]))
        if _DANGLING_JAVA_PREFIX.search(text):
            errors.append(u"{} {}: 残缺的 ysm./ctrl./fn. 前缀(unrecognized token): {}".format(
                where, u"/".join(str(p) for p in path), text[:80]))
        if _RESIDUAL_JAVA_TOKEN.search(text):
            errors.append(u"{} {}: 未转换的 Java 专有 token: {}".format(
                where, u"/".join(str(p) for p in path), text[:80]))
        unquoted = _MOLANG_QUOTED.sub("''", text)
        if _IMPLICIT_MULTIPLICATION.search(unquoted):
            errors.append(u"{} {}: 隐式乘法 )( (Java 解析为乘法, 基岩视为非法调用): {}".format(
                where, u"/".join(str(p) for p in path), text[:80]))
        if _BOOLEAN_KEYWORD.search(unquoted):
            warnings.append(u"{} {}: true/false 关键字(基岩是否认无证据, 应写 1.0/0.0): {}".format(
                where, u"/".join(str(p) for p in path), text[:80]))
        assignedInText = set()
        for match in _INPUT_STATE_VARIABLE_TOKEN.finditer(text):
            name, suffix = match.group(1), (match.group(2) or u"").strip()
            if suffix.startswith(u"="):
                assignedInText.add(name)
            elif not suffix and name not in assignedInText:
                errors.append(u"{} {}: 输入状态变量 variable.{} 裸读(不做初始化, 必须带 ?? 回落): {}".format(
                    where, u"/".join(str(p) for p in path), name, text[:80]))
                break


def _CheckMolangSyntax(slots, where, errors):
    """基岩解析不了的表达式 = 整份文件拒载(移植工具已按 Java 口径守卫, 残留即回归)"""
    for slotPath, text, problem in molang_syntax.SlotProblems(slots):
        errors.append(u"{} {}: 基岩解析不了的 molang(整份文件拒载): {} <- {}".format(
            where, molang_syntax.FormatSlotPath(slotPath), problem, text[:80]))


def _CheckMolangPrecedence(slots, where, path, errors, warnings):
    """新旧 Molang 语义下取值可能不同的表达式(资源包按 min_engine_version 1.18.0 走旧语义: 三元左结合、
    && 不比 || 紧, 2026-09-17 实机探针)。移植/自有产物按错误拦(移植工具 ExplicitPackPrecedence 已补括号,
    残留即回归), 兼容资源(compat/)是历史手工资源、按旧语义调好的, 只提示"""
    if os.path.basename(path) == "molang_probe.animation.json":
        return      # 语义探针本身就是故意写的歧义式(mcdkSelfTest.MOLANG_SEMANTICS_PROBES)
    sink = warnings if (os.sep + "compat" + os.sep) in path else errors
    for slotPath, container, key, _mode in slots:
        text = molang_syntax.SlotText(container, key, slotPath)
        newText, count = molang_syntax.ExplicitPrecedence(text)
        if count:
            sink.append(u"{} {}: 新旧 Molang 语义下取值可能不同(三元嵌套 / && || 混写 / 比较连写), 应补括号: {} -> {}".format(
                where, molang_syntax.FormatSlotPath(slotPath), text[:80], newText[:90]))


def ValidateAnimationFile(path, errors, warnings):
    try:
        data = _LoadJson(path)
    except ValueError as exc:
        errors.append(u"{}: JSON 语法错误: {}".format(path, exc))
        return
    animations = data.get("animations")
    if not isinstance(animations, dict):
        errors.append(u"{}: 缺少 animations 段".format(path))
        return
    rel = os.path.relpath(path, ROOT)
    for animId, body in animations.items():
        where = u"{} [{}]".format(rel, animId)
        if not _RESOURCE_ID_OK.match(animId):
            errors.append(u"{}: 动画 ID 含非法字符(大写/$/:/空格), 整份文件解析失败".format(where))
        if not isinstance(body, dict):
            errors.append(u"{}: 动画体不是对象".format(where))
            continue
        bones = body.get("bones")
        if "bones" in body:
            if not isinstance(bones, dict) or not bones:
                errors.append(u"{}: 空 bones 节点(引擎: Required child not found, 整份文件作废)".format(where))
            else:
                for boneName, channels in bones.items():
                    if not isinstance(channels, dict) or not channels:
                        errors.append(u"{}: 骨骼 {} 为空/非对象".format(where, boneName))
                        continue
                    for channel, value in channels.items():
                        if channel not in _CHANNELS and channel not in _BONE_EXTRA_KEYS:
                            warnings.append(u"{}: 骨骼 {} 未知通道 {}".format(where, boneName, channel))
                        if isinstance(value, (dict, list)) and not value:
                            errors.append(u"{}: 骨骼 {} 通道 {} 为空".format(where, boneName, channel))
                        if isinstance(value, dict):
                            for stamp, frame in value.items():
                                if isinstance(frame, dict) and [k for k in frame if k not in _KEYFRAME_KEYS]:
                                    errors.append(
                                        u"{}: 骨骼 {} 通道 {} 关键帧 {} 含未知键 {}(geckolib vector/easing 等, "
                                        u"引擎 child not valid here)".format(
                                            where, boneName, channel, stamp,
                                            [k for k in frame if k not in _KEYFRAME_KEYS]))
                        # 兼容资源(compat/)是历史手工资源, 同形态只提示; 移植/自有产物按错误拦
                        sink = warnings if (os.sep + "compat" + os.sep) in path else errors
                        for stamp in _CatmullromNearExpressionStamps(value):
                            sink.append(
                                u"{}: 骨骼 {} 通道 {} 的关键帧 {} 是 catmullrom "
                                u"且关键帧 i-1..i+2 里有 molang 表达式 —— "
                                u"引擎预计算样条要求这四帧都是常量(Precomputed cubic "
                                u"interpolation requires keyframes have constant data), "
                                u"整段退化成“停在起点值、到下一帧再跳过去”"
                                u"(实机: 拉弓先停在侧面半秒再跳到正面)。"
                                u"应改 lerp_mode: linear".format(
                                    where, boneName, channel, stamp))
                        for stamp in _PostOnlyLinearStamps(value, channel):
                            sink.append(
                                u"{}: 骨骼 {} 通道 {} 的关键帧 {} 只写 post 且入段是线性 —— "
                                u"引擎把缺失的 pre 当通道默认值(scale 1 / 其余 0), 上一帧到"
                                u"这一帧会朝默认值插值再跳回 post(实机: 凋灵娘火焰莫名缩放)。"
                                u"应补同值 pre".format(where, boneName, channel, stamp))
        if "timeline" in body:
            timeline = body.get("timeline")
            if not isinstance(timeline, dict) or not timeline:
                warnings.append(u"{}: 空 timeline 节点".format(where))
            else:
                for stamp, value in timeline.items():
                    if value in ("", [], None, {}):
                        errors.append(u"{}: timeline {} 条目为空".format(where, stamp))
        if "animation_length" in body:
            length = body.get("animation_length")
            if not isinstance(length, (int, float)) or length < 0:
                errors.append(u"{}: animation_length 非法: {!r}".format(where, length))
        if "loop" in body and body.get("loop") not in _LOOP_VALUES:
            errors.append(u"{}: loop 取值非法 {!r}(只认布尔与 hold_on_last_frame)".format(where, body.get("loop")))
        _CheckMolangStrings(body, where, errors, warnings)
        _CheckMolangSyntax(molang_syntax.AnimationMolangSlots(body), where, errors)
        _CheckMolangPrecedence(molang_syntax.AnimationMolangSlots(body), where, path, errors, warnings)


def ValidateControllerFile(path, errors, warnings):
    try:
        data = _LoadJson(path)
    except ValueError as exc:
        errors.append(u"{}: JSON 语法错误: {}".format(path, exc))
        return
    controllers = data.get("animation_controllers")
    if not isinstance(controllers, dict):
        errors.append(u"{}: 缺少 animation_controllers 段".format(path))
        return
    rel = os.path.relpath(path, ROOT)
    for ctlId, body in controllers.items():
        where = u"{} [{}]".format(rel, ctlId)
        if not _RESOURCE_ID_OK.match(ctlId):
            errors.append(u"{}: 控制器 ID 含非法字符".format(where))
        if not isinstance(body, dict):
            errors.append(u"{}: 控制器体不是对象".format(where))
            continue
        states = body.get("states")
        if not isinstance(states, dict) or not states:
            errors.append(u"{}: 无 states".format(where))
            continue
        initial = body.get("initial_state", "default")
        if initial not in states:
            errors.append(u"{}: initial_state {} 不存在".format(where, initial))
        for stateName, state in states.items():
            if not isinstance(state, dict):
                errors.append(u"{}: 状态 {} 不是对象".format(where, stateName))
                continue
            if "animations" in state and not state["animations"]:
                errors.append(u"{}: 状态 {} 的 animations 为空数组(整份控制器文件静默拒载)".format(
                    where, stateName))
            blend = state.get("blend_transition")
            if blend is not None and (isinstance(blend, bool) or not isinstance(blend, (int, float)) or blend < 0):
                errors.append(u"{}: 状态 {} 的 blend_transition 不是非负数字(Java 点集曲线形态基岩不认): {!r}".format(
                    where, stateName, blend))
            if "blend_via_shortest_path" in state and not isinstance(state["blend_via_shortest_path"], bool):
                errors.append(u"{}: 状态 {} 的 blend_via_shortest_path 不是布尔".format(where, stateName))
            if "transitions" in state and not state["transitions"]:
                warnings.append(u"{}: 状态 {} 的 transitions 为空数组".format(where, stateName))
            for entry in state.get("transitions") or []:
                if not isinstance(entry, dict):
                    errors.append(u"{}: 状态 {} 转移条目不是对象".format(where, stateName))
                    continue
                for target, condition in entry.items():
                    if target not in states:
                        errors.append(u"{}: 状态 {} 转移到未定义状态 {}".format(where, stateName, target))
                    if condition in ("", None):
                        errors.append(u"{}: 状态 {} → {} 条件为空".format(where, stateName, target))
        _CheckMolangStrings(body, where, errors, warnings)
        _CheckMolangSyntax(molang_syntax.ControllerMolangSlots(body), where, errors)
        _CheckMolangPrecedence(molang_syntax.ControllerMolangSlots(body), where, path, errors, warnings)


def _CollectFiles(subDir, packs):
    base = os.path.join(RP, subDir)
    for dirPath, _dirs, files in os.walk(base):
        relDir = os.path.relpath(dirPath, base)
        if packs and relDir.split(os.sep)[0] not in packs:
            continue
        for name in sorted(files):
            if name.endswith(".json"):
                yield os.path.join(dirPath, name)


def CheckControllerOneShotLoops(packs, warnings):
    """控制器状态引用的动画若 loop 为空/false, 出态前会漏一帧底层姿态。

    geckolib 的 PLAY_ONCE 保持末帧, 基岩的 loop:false **播完即撤** —— 靠
    `q.all_animations_finished` 出态时, 动画停作用的那一刻转移还没生效,
    骨骼掉回底层姿态一帧。实机(2026-09-05 warden 持剑攻击): punch_left /
    punch_right 在挥击收回瞬间闪一下。旁证: CSM 全库 578 条动画里
    loop 为 true(473)或 hold_on_last_frame(101), 裸的不循环动画只有 4 条且不挂状态。
    修法: `port_java_pack.HoldControllerOneShots`(移植工具与就地修复工具都会跑)。
    """
    base = os.path.join(RP, "animation_controllers")
    if not os.path.isdir(base):
        return
    for packName in sorted(os.listdir(base)):
        if packs and packName not in packs:
            continue
        ctlDir = os.path.join(base, packName)
        animDir = os.path.join(RP, "animations", packName)
        if not (os.path.isdir(ctlDir) and os.path.isdir(animDir)):
            continue
        refs = set()
        for name in sorted(os.listdir(ctlDir)):
            if not name.endswith(".json"):
                continue
            try:
                data = _LoadJson(os.path.join(ctlDir, name))
            except (ValueError, IOError):
                continue
            for _ctlId, body in (data.get("animation_controllers") or {}).items():
                if not isinstance(body, dict):
                    continue
                for _stateName, state in (body.get("states") or {}).items():
                    if not isinstance(state, dict):
                        continue
                    for entry in (state.get("animations") or []):
                        if isinstance(entry, dict):
                            refs.update(str(k) for k in entry.keys())
                        elif isinstance(entry, _STRING_TYPES):
                            refs.add(str(entry))
        if not refs:
            continue
        mainPrefix = "animation.{}.".format(packName)
        armPrefix = "animation.{}_arm.".format(packName)
        for name in sorted(os.listdir(animDir)):
            if not name.endswith(".json"):
                continue
            try:
                data = _LoadJson(os.path.join(animDir, name))
            except (ValueError, IOError):
                continue
            for animId, body in (data.get("animations") or {}).items():
                if not isinstance(body, dict) or body.get("loop") not in (None, False, "false"):
                    continue
                if animId.startswith(mainPrefix):
                    keys = [str(animId[len(mainPrefix):])]
                elif animId.startswith(armPrefix):
                    shortKey = str(animId[len(armPrefix):])
                    keys = [shortKey, "fp_" + shortKey]
                else:
                    continue
                if [k for k in keys if k in refs]:
                    warnings.append(
                        u"{}/{}: {} 被控制器状态引用但 loop 为空/false —— "
                        u"基岩播完即撤(geckolib PLAY_ONCE 会保持末帧), "
                        u"出态前会漏一帧底层姿态。应改 "
                        u"loop: hold_on_last_frame".format(packName, name, animId))


_OWNERSHIP_SET_READ = re.compile(r"variable\.(ysm_ownset_\d+)\s*\?\?")
_OWNERSHIP_ASSIGN = re.compile(r"^\s*variable\.(ysm_own(?:set)?_[a-z0-9_]+)\s*=")
_OWNERSHIP_INDEX_READ = re.compile(r"variable\.(ysm_own_[a-z0-9_]+)\s*\?\?")
_OWNERSHIP_COMPANION = re.compile(r"^(.+)__own\d+$")


def CheckChannelOwnership(packs, errors):
    """Java 逐通道覆盖(port_java_pack.ApplyChannelOwnership)的接线: 伴生权重读的集合变量、集合
    表达式读的状态序号变量都必须在某个控制器状态 on_entry 里写入; 状态里的伴生条目必须有动画。
    缺一环 = 权重恒按 0 求值(??0), 早层通道在晚层活跃时也不让出 —— 与没做覆盖一样叠加。"""
    ctlBase = os.path.join(RP, "animation_controllers")
    if not os.path.isdir(ctlBase):
        return
    for packName in sorted(os.listdir(ctlBase)):
        if packs and packName not in packs:
            continue
        ctlDir = os.path.join(ctlBase, packName)
        animDir = os.path.join(RP, "animations", packName)
        if not os.path.isdir(ctlDir):
            continue
        assigned, readSets, readIndices, companions = set(), set(), set(), set()
        for name in sorted(os.listdir(ctlDir)):
            if not name.endswith(".json"):
                continue
            try:
                data = _LoadJson(os.path.join(ctlDir, name))
            except (ValueError, IOError):
                continue
            for _ctlId, body in (data.get("animation_controllers") or {}).items():
                for _stateName, state in ((body or {}).get("states") or {}).items():
                    if not isinstance(state, dict):
                        continue
                    for line in state.get("on_entry") or []:
                        if not isinstance(line, _STRING_TYPES):
                            continue
                        matched = _OWNERSHIP_ASSIGN.match(line)
                        if matched:
                            assigned.add(matched.group(1))
                            readIndices.update(_OWNERSHIP_INDEX_READ.findall(line.split("=", 1)[1]))
                    for entry in state.get("animations") or []:
                        items = entry.items() if isinstance(entry, dict) else [(entry, None)]
                        for ref, weight in items:
                            if isinstance(ref, _STRING_TYPES) and _OWNERSHIP_COMPANION.match(ref):
                                companions.add(ref)
                            if isinstance(weight, _STRING_TYPES):
                                readSets.update(_OWNERSHIP_SET_READ.findall(weight))
                                readIndices.update(_OWNERSHIP_INDEX_READ.findall(weight))
        # 直挂条件动画的伴生权重在 ysm.json 顶级 channel_ownership(主包 _AttachOwnershipCompanions 读)
        manifest = _LoadPackManifest(packName) or {}
        for companion, weight in sorted((manifest.get("channel_ownership") or {}).items()):
            companions.add(companion)
            if isinstance(weight, _STRING_TYPES):
                readSets.update(_OWNERSHIP_SET_READ.findall(weight))
                readIndices.update(_OWNERSHIP_INDEX_READ.findall(weight))
        for variable in sorted((readSets | readIndices) - assigned):
            errors.append(u"{}: 占用变量 variable.{} 被读取但没有任何控制器状态 on_entry 写入"
                          u"(伴生动画永不让出通道)".format(packName, variable))
        if not companions:
            continue
        known = set()
        if os.path.isdir(animDir):
            for name in sorted(os.listdir(animDir)):
                if not name.endswith(".json"):
                    continue
                try:
                    data = _LoadJson(os.path.join(animDir, name))
                except (ValueError, IOError):
                    continue
                for animId in (data.get("animations") or {}):
                    known.add(animId.split(".", 2)[-1])
                    # arm 命名空间的动画另以 fp_ 前缀注册(packParser._LoadArmAnimations), 第一人称
                    # 挥击状态机按这个键引用
                    if animId.startswith(u"animation.{}_arm.".format(packName)):
                        known.add(u"fp_" + animId.split(".", 2)[-1])
        for companion in sorted(companions - known):
            errors.append(u"{}: 控制器引用了伴生动画 {} 但动画文件里没有".format(packName, companion))


_FINISHED_QUERY = re.compile(r"(?:q|query)\.(?:all_animations_finished|any_animation_finished)")
_STATE_CLOCK_READ = re.compile(r"variable\.(ysm_t0_[a-z0-9_]+)(\s*\?\?|\s*=(?!=))?")
_OWNERSHIP_WEIGHT_FLOOR = u"0.0001"
_GENERATED_CONTROLLER_FILES = ("ysm_state.json", "ysm_oneshot.json", "ysm_variable_init.json")


def CheckOwnershipWeights(packs, errors):
    """基岩权重 0 的动画**暂停计时**, 且让所在状态的 all_animations_finished 永不成立(2026-09-17 实机,
    见 port_java_pack.ApplyChannelOwnership / RewriteFinishedQueries 注)。据此体检:
    - 不带 override 的伴生动画在控制器状态里的权重必须带 1e-4 下限(否则让出通道时暂停, 时间轴与原动画
      错位, 还会卡住作者的"播完"转移);
    - 作者控制器状态里有权重条目(带条件的作者动画 / 带 override 的伴生)时不能再用原生"播完"判据;
    - 状态计时变量 variable.ysm_t0_* 读取必须带 ?? 且在该状态 on_entry 写入。"""
    ctlBase = os.path.join(RP, "animation_controllers")
    if not os.path.isdir(ctlBase):
        return
    for packName in sorted(os.listdir(ctlBase)):
        if packs and packName not in packs:
            continue
        ctlDir = os.path.join(ctlBase, packName)
        animDir = os.path.join(RP, "animations", packName)
        if not os.path.isdir(ctlDir) or not os.path.isdir(animDir):
            continue
        overrideFlags = {}
        for name in sorted(os.listdir(animDir)):
            if not name.endswith(".json"):
                continue
            try:
                data = _LoadJson(os.path.join(animDir, name))
            except (ValueError, IOError):
                continue
            for animId, body in (data.get("animations") or {}).items():
                if isinstance(body, dict):
                    overrideFlags.setdefault(animId.split(".", 2)[-1],
                                             bool(body.get("override_previous_animation")))
        for name in sorted(os.listdir(ctlDir)):
            if not name.endswith(".json"):
                continue
            try:
                data = _LoadJson(os.path.join(ctlDir, name))
            except (ValueError, IOError):
                continue
            authored = name not in _GENERATED_CONTROLLER_FILES
            for ctlId, body in (data.get("animation_controllers") or {}).items():
                for stateName, state in ((body or {}).get("states") or {}).items():
                    if not isinstance(state, dict):
                        continue
                    where = u"{}/{} {}.{}".format(packName, name, ctlId.split(".")[-1], stateName)
                    weighted = False
                    for entry in state.get("animations") or []:
                        if not isinstance(entry, dict):
                            continue
                        for ref, weight in entry.items():
                            companion = _OWNERSHIP_COMPANION.match(ref)
                            if companion is None:
                                weighted = True
                                continue
                            if overrideFlags.get(ref):
                                weighted = True
                            elif isinstance(weight, _STRING_TYPES) and _OWNERSHIP_WEIGHT_FLOOR not in weight:
                                errors.append(u"{}: 伴生动画 {} 不带 override, 权重缺 1e-4 下限(让出通道时"
                                              u"暂停计时、卡住播完判据): {}".format(where, ref, weight[:80]))
                    texts = [value for item in state.get("transitions") or [] if isinstance(item, dict)
                             for value in item.values() if isinstance(value, _STRING_TYPES)]
                    if authored and weighted and any(_FINISHED_QUERY.search(text) for text in texts):
                        errors.append(u"{}: 状态里有权重条目却用原生\"动画播完\"判据 —— 基岩权重 0 的动画"
                                      u"暂停且算没播完, 判据永不成立(应由 RewriteFinishedQueries 改写)".format(where))
                    onEntry = state.get("on_entry") or []
                    onEntry = [onEntry] if isinstance(onEntry, _STRING_TYPES) else onEntry
                    assignedClocks = set()
                    for line in onEntry:
                        if isinstance(line, _STRING_TYPES):
                            for match in _STATE_CLOCK_READ.finditer(line):
                                if (match.group(2) or u"").strip().startswith(u"="):
                                    assignedClocks.add(match.group(1))
                    for text in texts:
                        for match in _STATE_CLOCK_READ.finditer(text):
                            suffix = (match.group(2) or u"").strip()
                            if not suffix.startswith(u"??"):
                                errors.append(u"{}: 状态计时变量 variable.{} 裸读(必须带 ?? 回落)".format(
                                    where, match.group(1)))
                            elif match.group(1) not in assignedClocks:
                                errors.append(u"{}: 状态计时变量 variable.{} 被读取但本状态 on_entry 没写入".format(
                                    where, match.group(1)))


def _LoadPackManifest(packName):
    """ysm_bp/ysm_models 下(含合集子目录)同名包的 ysm.json"""
    base = BP_MODELS
    for dirPath, _dirs, files in os.walk(base):
        if os.path.basename(dirPath) == packName and "ysm.json" in files:
            try:
                return _LoadJson(os.path.join(dirPath, "ysm.json"))
            except (ValueError, IOError):
                return None
    return None


def CheckSoundEffectRegistration(packs, warnings):
    """动画 sound_effects 关键帧的效果键必须在 ysm.json files.player.sound_effect 登记(主包据此
    AddPlayerSoundEffect), 且 ysm.* 定义要在 sounds/sound_definitions.json 里 —— 三件缺一即无声。"""
    base = os.path.join(RP, "animations")
    if not os.path.isdir(base):
        return
    definitions = {}
    definitionPath = os.path.join(RP, "sounds", "sound_definitions.json")
    if os.path.isfile(definitionPath):
        try:
            loaded = _LoadJson(definitionPath)
            definitions = loaded.get("sound_definitions") if isinstance(
                loaded.get("sound_definitions"), dict) else loaded
        except (ValueError, IOError):
            definitions = {}
    for packName in sorted(os.listdir(base)):
        if packs and packName not in packs:
            continue
        animDir = os.path.join(base, packName)
        if not os.path.isdir(animDir):
            continue
        used = set()
        for name in sorted(os.listdir(animDir)):
            if not name.endswith(".json"):
                continue
            try:
                data = _LoadJson(os.path.join(animDir, name))
            except (ValueError, IOError):
                continue
            for _animId, body in (data.get("animations") or {}).items():
                sounds = body.get("sound_effects") if isinstance(body, dict) else None
                if not isinstance(sounds, dict):
                    continue
                for value in sounds.values():
                    for entry in (value if isinstance(value, list) else [value]):
                        effect = entry.get("effect") if isinstance(entry, dict) else entry
                        if isinstance(effect, _STRING_TYPES) and effect:
                            used.add(effect)
        if not used:
            continue
        manifest = _LoadPackManifest(packName)
        if manifest is None:
            warnings.append(u"{}: 动画用了 sound_effects 效果键 {} 种, 但找不到 ysm.json 核对注册".format(
                packName, len(used)))
            continue
        registered = {}
        sources = ((manifest.get("files") or {}).get("player") or {}, manifest.get("netease") or {}, manifest)
        for source in sources:
            table = source.get("sound_effect") if isinstance(source, dict) else None
            if isinstance(table, dict):
                registered.update(table)
            elif isinstance(table, list):
                for entry in table:
                    if isinstance(entry, list) and len(entry) >= 2:
                        registered[entry[0]] = entry[1]
        for effect in sorted(used):
            if effect not in registered:
                warnings.append(u"{}: sound_effects 效果键 {} 未在 ysm.json 的 files.player.sound_effect "
                                u"登记(无声)".format(packName, effect))
                continue
            definition = registered[effect]
            if isinstance(definition, _STRING_TYPES) and definition.startswith("ysm.") \
                    and definition not in definitions:
                warnings.append(u"{}: 效果键 {} 的定义 {} 不在 sounds/sound_definitions.json(无声)".format(
                    packName, effect, definition))


def Run(packs):
    """体检指定包(空集合 = 全部 RP 文件); 返回 (errors, warnings, 动画文件数, 控制器文件数)。
    命令行 main 与转换器宿主(port_cli)共用。"""
    packs = set(packs)
    errors, warnings = [], []
    animCount = ctlCount = 0
    for path in _CollectFiles("animations", packs):
        animCount += 1
        ValidateAnimationFile(path, errors, warnings)
    for path in _CollectFiles("animation_controllers", packs):
        ctlCount += 1
        ValidateControllerFile(path, errors, warnings)
    CheckControllerOneShotLoops(packs, warnings)
    CheckSoundEffectRegistration(packs, warnings)
    CheckChannelOwnership(packs, errors)
    CheckOwnershipWeights(packs, errors)
    return errors, warnings, animCount, ctlCount


def main():
    packs = set(sys.argv[1:])
    errors, warnings, animCount, ctlCount = Run(packs)

    def _Emit(line):
        if not isinstance(line, str):
            line = line.encode("utf-8") if sys.version_info[0] < 3 else line
        print(line)

    for line in warnings:
        _Emit(u"[WARN] " + line)
    for line in errors:
        _Emit(u"[ERROR] " + line)
    _Emit(u"[RESULT] 动画文件 {} / 控制器文件 {}; 错误 {} / 警告 {}".format(
        animCount, ctlCount, len(errors), len(warnings)))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
