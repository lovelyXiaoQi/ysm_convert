# -*- coding: utf-8 -*-
"""Java molang → 主包"运行期状态"(ysm_bp/ysmModelCoreScripts/config/javaState.py)的移植期接线。

Java 的 YSMBinding / QueryBinding 里有一批量基岩 molang 没有对应查询, 但网易 ModAPI 取得到(2026-09-18 双开实测,
缺口表与实测记录见 docs/ysm-java-molang-mapping.md 第七节)。主包运行层逐 tick 算好写回玩家实体, 移植期这里负责:

1. **名字映射行**(RUNTIME_NAME_ROWS / RUNTIME_CTRL_ROWS, 并进 port_java_pack 的 _JAVA_NAME_MAP / _CTRL_NAME_MAP):
   `ysm.weather` → `query.mod.ysm_weather`、`ysm.dimension_name` → `(variable.ysm_env_dimension??'minecraft:overworld')` 等;
2. **探针**(RewriteProbeCall): 带常量参数的函数调用(`ysm.effect_level('minecraft:speed')`、
   `ysm.equipped_enchantment_level('mainhand','minecraft:fire_aspect')`、`ysm.relative_block_name(0,-1,0)`)
   改读探针变量 `variable.ysm_pb_*`, 调用登记进当前包的汇(BeginPack/EndPack), 由 PortPack 写进 ysm.json;
3. **骨骼旋转回读**(`ysm.bone_rot('骨骼').x`): 读侧改读 `variable.ysm_br_<骨骼>_<轴>`, 写侧(InlineBoneRotationWriters)
   把写这根骨骼 rotation 的表达式通道改成"先存变量再返回" —— 官方酒狐 03/06/10/15 与默认控制器的头发跟随链;
4. **声明**(BuildDeclaration): 扫产物文本得出本包要主包维护哪些量(needs)、有哪些 roaming 变量, 连同探针表
   写成 ysm.json 顶级 `java_state`(解析器直通进模型配置, 运行层按模型 ID 查);
5. **鞘翅角度**(BuildElytraStateStatement): Java ysm.elytra_rot_x/y/z 读原版 ElytraModel 的追随角, 主包共享动画
   animation.ysm.java_elytra_state 逐帧复刻(资源包文件与生成语句逐字一致, 测试守护)。

本模块**不 import 主包业务包**(独立转换器的内核快照里没有它); 与 config/javaState.py 同名的常量由
devtools/test_java_state.py 守护逐项相等。
"""
from __future__ import division

import io
import json
import os
import re
import zlib
from collections import OrderedDict

# ---- 与 config/javaState.py 同名同值(测试守护) ----
NEED_WEATHER = "weather"
NEED_OPEN_AIR = "open_air"
NEED_DIMENSION = "dimension"
NEED_LIGHT = "light"
NEED_AIR = "air"
NEED_HEALTH = "health"
NEED_HIT_TARGET = "hit_target"
NEED_FISHING = "fishing"
NEED_LADDER_FACING = "ladder_facing"
NEED_FROZEN = "frozen"
NEED_TEXTURE = "texture"

QUERY_WEATHER = "query.mod.ysm_weather"
QUERY_OPEN_AIR = "query.mod.ysm_is_open_air"
QUERY_BLOCK_LIGHT = "query.mod.ysm_block_light"
QUERY_SKY_LIGHT = "query.mod.ysm_sky_light"
QUERY_AIR_SUPPLY = "query.mod.ysm_air_supply"
QUERY_HEALTH = "query.mod.ysm_health"
QUERY_MAX_HEALTH = "query.mod.ysm_max_health"
QUERY_FISHING = "query.mod.ysm_is_fishing"
QUERY_LADDER_FACING = "query.mod.ysm_ladder_facing"
QUERY_FROZEN_TICKS = "query.mod.ysm_frozen_ticks"
VARIABLE_DIMENSION = "variable.ysm_env_dimension"
VARIABLE_HIT_ID = "variable.ysm_env_hit_id"
VARIABLE_HIT_TYPE = "variable.ysm_env_hit_type"
VARIABLE_TEXTURE = "variable.ysm_env_texture"
VARIABLE_SHOOT_ITEM = "variable.ysm_env_shoot_item"

PROBE_EFFECT_LEVEL = "effect_level"
PROBE_ENCHANT_LEVEL = "enchant_level"
PROBE_BLOCK_NAME = "block_name"
PROBE_BLOCK_NAME_ANY = "block_name_any"
_EQUIPMENT_SLOTS = ("mainhand", "offhand", "head", "chest", "legs", "feet")
MAX_BLOCK_OFFSET = 8.0

# 读取处的落点文本 → 需求键(BuildDeclaration 扫产物文本用; 一个需求可能有多个落点)
_NEED_TOKENS = OrderedDict([
    (QUERY_WEATHER, NEED_WEATHER), (QUERY_OPEN_AIR, NEED_OPEN_AIR),
    (QUERY_BLOCK_LIGHT, NEED_LIGHT), (QUERY_SKY_LIGHT, NEED_LIGHT),
    (QUERY_AIR_SUPPLY, NEED_AIR), (QUERY_HEALTH, NEED_HEALTH), (QUERY_MAX_HEALTH, NEED_HEALTH),
    (QUERY_FISHING, NEED_FISHING), (QUERY_LADDER_FACING, NEED_LADDER_FACING),
    (QUERY_FROZEN_TICKS, NEED_FROZEN), (VARIABLE_DIMENSION, NEED_DIMENSION),
    (VARIABLE_HIT_ID, NEED_HIT_TARGET), (VARIABLE_HIT_TYPE, NEED_HIT_TARGET),
    (VARIABLE_TEXTURE, NEED_TEXTURE),
])
# 天空光的运行层口径依赖露天判定(见 client/javaStateSync._ApplyServerState)
_NEED_IMPLIES = {NEED_LIGHT: (NEED_OPEN_AIR,)}


def _StringRead(variable, fallback="''"):
    """字符串变量的读取式: 运行层还没写过(或纸娃娃/预览实体)时回落 —— 变量不参与文件扫描补 0"""
    return "({}??{})".format(variable, fallback)


# ---- 腾空(不含主状态优先级): Java query.is_jumping = !flying && !passenger && !onGround && !inWater ----
# 与 ctrl.jump 的闩锁同源, 但文本上故意写成 `0.5<(...)`: 修复工具把旧产物里裸的 `((variable.ysm_airborne??0)>0.5)`
# (早年 ctrl.jump 的展开)整串迁成主状态互斥形态, 同一串文本出现在这里会被一并迁走, 移植产物过一遍修复工具就变样。
# "不在创造飞行"同理读主状态序号(10 = fly, 见 port_java_pack._CTRL_MAIN_PRIORITY), 不写 query.mod.ysm_is_flying。
# 基岩同名 query.is_jumping 是"跳跃键按住"(2026-09-18 实测: 点按只有 2 帧为 1, 松键后仍在空中归 0, 纯下落全程 0)
AIRBORNE_RAW = "(0.5<(variable.ysm_airborne??0))"
IS_JUMPING_EXPR = "({}&&!((variable.ysm_ctrl_main??0)==10)&&!query.is_riding)".format(AIRBORNE_RAW)

# ---- 鞘翅追随角(见 BuildElytraStateStatement) ----
ELYTRA_ROT_X = "(variable.ysm_elytra_rot_x??15.0)"
ELYTRA_ROT_Y = "(variable.ysm_elytra_rot_y??0.0)"
ELYTRA_ROT_Z = "(variable.ysm_elytra_rot_z??-15.0)"

# Java 名(ysm./query./q. 三前缀共用一张表) → 替换式。这些行**取代** port_java_pack._JAVA_NAME_MAP 里的同名旧行
RUNTIME_NAME_ROWS = [
    ("is_jumping", IS_JUMPING_EXPR),
    # 两帧间隔的倒数; 下限防除零(暂停/首帧 delta_time 可能为 0), Java Minecraft.getFps 同样不会是 0
    ("fps", "(1/math.max(query.delta_time,0.004))"),
    # 睡觉时闭眼(Java getEyeCloseState: isSleeping || 眨眼窗口); 眨眼节拍由主包按实体错相位下发
    ("is_close_eyes", "(query.mod.ysm_is_close_eyes||query.is_sleeping)"),
    # "更真实的第一人称"类模组要求藏头 ↔ 主包的真实第一人称开关(UI 打开时让路)
    ("first_person_mod_hide",
     "(query.mod.ysm_real_first_person&&!query.mod.ysm_real_first_person_blocked)"),
    ("weather", QUERY_WEATHER),
    ("is_open_air", QUERY_OPEN_AIR),
    ("air_supply", QUERY_AIR_SUPPLY),
    ("block_light", QUERY_BLOCK_LIGHT),
    ("sky_light", QUERY_SKY_LIGHT),
    ("frozen_ticks", QUERY_FROZEN_TICKS),
    ("ladder_facing", QUERY_LADDER_FACING),
    ("is_fishing", QUERY_FISHING),
    # 远程玩家的血量不同步到别的客户端(原生 query.health 对远程恒为满血): 改读服务端同步值
    ("health", QUERY_HEALTH),
    ("max_health", QUERY_MAX_HEALTH),
    ("dimension_name", _StringRead(VARIABLE_DIMENSION, "'minecraft:overworld'")),
    ("hit_target_id", _StringRead(VARIABLE_HIT_ID)),
    ("hit_target_type", _StringRead(VARIABLE_HIT_TYPE)),
    ("texture_name", _StringRead(VARIABLE_TEXTURE)),
    ("shoot_item_id", _StringRead(VARIABLE_SHOOT_ITEM)),
    ("elytra_rot_x", ELYTRA_ROT_X),
    ("elytra_rot_y", ELYTRA_ROT_Y),
    ("elytra_rot_z", ELYTRA_ROT_Z),
]

# ctrl.* 行(取代 _CTRL_NAME_MAP 里的同名旧行)。
# **ctrl.tac_* 暂不接**(仍是 _CTRL_NAME_MAP 里的中性常量): 网易 TACZ 联动的数据在结构体变量 variable.tac.* 里
# (compat/ysm.tacz 控制器读 v.tac.gun_type == 'rifle' 等, 与 Java 同名的字符串), 但产物直接读它有两处没有实机依据 ——
# ① 变量扫描按 `variable.<名>` 截到 `tac`, 会往预览实体与每实例初始化控制器里写 `variable.tac = 0.0;`, 把结构体变量
# 写成数值之后再读成员是什么行为不知道; ② `??` 的左侧要求"以变量为根", 结构体成员算不算没测过, 不算的话整份文件拒载
# (官方酒狐 03 号的主控制器、凋灵娘的 main 动画)。要接先实机探这两条, 再让扫描认结构体根名。
RUNTIME_CTRL_ROWS = [
    # 主包 query.mod.ysm_carryon: 1 实体 / 2 方块 / 3 玩家(compat/ysm_carryon 控制器同口径), 0 = 没抱东西。
    # Java 是字符串('block'/'entity'/'player'), molang 没有数值→字符串的换算, 比较式由 RewriteCarryonType 改写
    ("carryon_type", "query.mod.ysm_carryon"),
]

_CARRYON_TYPE_VALUES = {"entity": 1, "block": 2, "player": 3}
_CARRYON_COMPARE = re.compile(
    r"\bctrl\.carryon_type\s*(==|!=)\s*'(entity|block|player|)'"
    r"|'(entity|block|player|)'\s*(==|!=)\s*ctrl\.carryon_type\b")


def RewriteCarryonType(text):
    """`ctrl.carryon_type=='block'` → `(query.mod.ysm_carryon==2)`(空串 = 没抱东西 = 0); 返回 (新文本, 替换数)。
    排在名字映射之前: 映射后剩下的裸 `ctrl.carryon_type` 才落成数值"""
    def _Replace(match):
        operator = match.group(1) or match.group(4)
        name = match.group(2) if match.group(1) else match.group(3)
        return "(query.mod.ysm_carryon{}{})".format(operator, _CARRYON_TYPE_VALUES.get(name, 0))

    return _CARRYON_COMPARE.subn(_Replace, text)


def MergeNameRows(baseRows, overrideRows):
    """按名字合并映射表: 同名旧行原位换成新行, 新名字追加在末尾"""
    overrides = OrderedDict(overrideRows)
    merged, seen = [], set()
    for name, replacement in baseRows:
        if name in overrides:
            replacement = overrides[name]
        merged.append((name, replacement))
        seen.add(name)
    merged.extend((name, overrides[name]) for name in overrides if name not in seen)
    return merged


# ------------------------------------------------------------------ 每包的汇 ----

class RuntimeSink(object):
    """一个移植包的运行期接线汇: 探针登记 + 主几何的骨骼绑定旋转(bone_rot 读侧要加上它) + 皮肤序号表"""

    def __init__(self):
        self.probes = OrderedDict()       # 探针变量短名 → 规整后的声明
        self.bindRotations = {}           # 骨骼名(小写) → [rx, ry, rz]
        self.textureIndex = {}            # Java 贴图名(小写, 不带扩展名) → 主包皮肤序号

    def SetBindRotations(self, geoPath):
        self.bindRotations = LoadBindRotations(geoPath)

    def SetTextures(self, textureNames, defaultName=None):
        self.textureIndex = TextureIndexTable(textureNames, defaultName)


def TextureIndexTable(textureNames, defaultName=None):
    """Java 贴图名 → 主包皮肤序号(query.mod.ysm_skin_index)。与解析器 packParser._DeriveSkins 同序:
    默认贴图(properties.default_texture, 不在表里则取首项)排 0, 其余保持声明顺序"""
    names = []
    for name in textureNames or []:
        if isinstance(name, (str, type(u""))) and name and name.lower() not in names:
            names.append(name.lower())
    if not names:
        return {}
    default = defaultName.lower() if isinstance(defaultName, (str, type(u""))) and defaultName.lower() in names \
        else names[0]
    ordered = [default] + [name for name in names if name != default]
    return dict((name, index) for index, name in enumerate(ordered))


# `ysm.texture_name == '贴图名'`: 字面量比较直接改写成皮肤序号比较 —— 不经字符串变量, 也就不需要 `变量 ?? '字符串'`
# 这个在资源包文件里没有实机先例的形态(官方莫莫酒狐的主动画文件里有 40 处, 整份拒载 = 模型僵直)
_TEXTURE_COMPARE = re.compile(
    r"\bysm\.texture_name\s*(==|!=)\s*'([^']*)'"
    r"|'([^']*)'\s*(==|!=)\s*ysm\.texture_name\b")


def RewriteTextureNameCompare(text, report=None):
    """`ysm.texture_name=='skin_pink'` → `(query.mod.ysm_skin_index==0)`; 表里没有的贴图名 → == 恒假 / != 恒真。
    没有活动的包汇或皮肤表为空时原样返回(裸引用落到字符串变量, 见 RUNTIME_NAME_ROWS)。返回 (新文本, 替换数)"""
    sink = CurrentSink()
    if sink is None or not sink.textureIndex:
        return text, 0

    def _Replace(match):
        operator = match.group(1) or match.group(4)
        name = (match.group(2) if match.group(1) else match.group(3)).lower()
        name = re.sub(r"[.](png|jpg|jpeg)$", "", name)      # 早期 Java 版本的贴图名带扩展名
        index = sink.textureIndex.get(name)
        if index is None:
            if report is not None:
                report["warn:ysm.texture_name 比较的贴图 '{}' 不在本包贴图表里(== 恒假)".format(name)] += 1
            return "(0.0)" if operator == "==" else "(1.0)"
        return "(query.mod.ysm_skin_index{}{})".format(operator, index)

    return _TEXTURE_COMPARE.subn(_Replace, text)


_current = [None]


def BeginPack():
    _current[0] = RuntimeSink()
    return _current[0]


def EndPack():
    sink, _current[0] = _current[0], None
    return sink


def CurrentSink():
    return _current[0]


# ------------------------------------------------------------------ 探针 ----

_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def _Slug(text, limit=24):
    return _SLUG_UNSAFE.sub("_", text.lower()).strip("_")[:limit] or "x"


def ProbeVariableName(probe):
    """探针声明 → 变量短名(确定性: 同一声明永远同名, 重新移植不改名)。可读片段 + 声明全文的 CRC"""
    body = json.dumps(probe, sort_keys=True, separators=(",", ":"))
    digest = "{:08x}".format(zlib.crc32(body.encode("utf-8") if not isinstance(body, bytes) else body) & 0xffffffff)
    hint = probe.get("ids") or []
    hintText = _Slug(hint[0].split(":")[-1]) if hint else _Slug("_".join(
        str(int(component)) for component in probe.get("offset") or []))
    return "ysm_pb_{}_{}_{}".format(probe["type"], hintText, digest)


def _Literal(arg):
    arg = arg.strip()
    if len(arg) >= 2 and arg[0] == "'" and arg[-1] == "'":
        return arg[1:-1]
    return None


def _Number(arg):
    try:
        value = float(arg.strip())
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def _OffsetFromArgs(args):
    if len(args) < 3:
        return None
    offset = [_Number(arg) for arg in args[:3]]
    if any(value is None or abs(value) > MAX_BLOCK_OFFSET for value in offset):
        return None
    return offset


def _ProbeFromCall(name, args):
    """Java 函数调用 → 探针声明; 参数不是常量/形态不认识 → None"""
    if name == "effect_level":
        ids = [_Literal(arg) for arg in args]
        if ids and all(ids):
            return {"type": PROBE_EFFECT_LEVEL, "ids": [item.lower() for item in ids]}
    elif name == "equipped_enchantment_level":
        literals = [_Literal(arg) for arg in args]
        if len(literals) >= 2 and all(literals) and literals[0].lower() in _EQUIPMENT_SLOTS:
            return {"type": PROBE_ENCHANT_LEVEL, "slot": literals[0].lower(),
                    "ids": [item.lower() for item in literals[1:]]}
    elif name == "relative_block_name":
        offset = _OffsetFromArgs(args)
        if offset is not None and len(args) == 3:
            return {"type": PROBE_BLOCK_NAME, "offset": offset}
    elif name == "relative_block_name_any":
        offset = _OffsetFromArgs(args)
        ids = [_Literal(arg) for arg in args[3:]]
        if offset is not None and ids and all(ids):
            return {"type": PROBE_BLOCK_NAME_ANY, "offset": offset, "ids": [item.lower() for item in ids]}
    return None


PROBE_FUNCTIONS = ("effect_level", "equipped_enchantment_level", "relative_block_name", "relative_block_name_any")


def RewriteProbeCall(name, args, report=None):
    """`ysm.<探针函数>(常量参数)` → 探针变量读取式(并登记进当前包的汇); 转不了返回 None(调用方按旧策略置常量)。

    没有活动的汇(基线包移植 / 单元用例直接调 PortMolangText)时同样返回 None: 没人会把探针表写进 ysm.json。
    """
    sink = CurrentSink()
    if sink is None or name not in PROBE_FUNCTIONS:
        return None
    probe = _ProbeFromCall(name, args)
    if probe is None:
        if report is not None:
            report["warn:ysm.{}(参数不是常量, 无法登记成探针, 按旧策略置常量)".format(name)] += 1
        return None
    variable = ProbeVariableName(probe)
    sink.probes[variable] = probe
    if report is not None:
        report["map:ysm.{}(...) -> variable.{}(主包运行层探针)".format(name, variable)] += 1
    fallback = "''" if probe["type"] == PROBE_BLOCK_NAME else "0"
    return "(variable.{}??{})".format(variable, fallback)


# ------------------------------------------------------------------ bone_rot ----

BONE_ROT_VALUE_PREFIX = "ysm_br_"
BONE_ROT_STAMP_PREFIX = "ysm_brt_"
# 写侧多久没刷新就视为"这根骨骼现在没人写"(读侧回落绑定旋转): 约 6 帧@30fps
_BONE_ROT_FRESH_SECONDS = 0.2
_BONE_ROT_AXES = ("x", "y", "z")
_BONE_SLUG_UNSAFE = re.compile(r"[^a-z0-9_]+")
_BONE_READ_PATTERN = re.compile(r"\bvariable\.ysm_br_([a-z0-9_]+?)_([xyz])\b")


def BoneSlug(boneName):
    return _BONE_SLUG_UNSAFE.sub("_", boneName.lower()).strip("_") or "bone"


def _FormatNumber(value):
    text = "{:.6g}".format(value)
    return text if ("." in text or "e" in text or "n" in text) else text + ".0"


def BoneRotationRead(boneName, axis, bindRotations=None):
    """`ysm.bone_rot('骨骼').<轴>` 的读取式。Java 返回"绑定旋转 + 动画旋转"(BoneSnapshot: getRotation 含
    initialRotation), 取的是上一帧的值; 这里读写侧存下的动画旋转(过期则视为 0)再加绑定旋转常量"""
    slug = BoneSlug(boneName)
    bind = (bindRotations or {}).get(boneName.lower()) or [0.0, 0.0, 0.0]
    bindValue = float(bind[_BONE_ROT_AXES.index(axis)])
    core = "(math.abs(query.life_time-variable.{stamp}{slug})<{fresh}?variable.{value}{slug}_{axis}:0)".format(
        stamp=BONE_ROT_STAMP_PREFIX, value=BONE_ROT_VALUE_PREFIX, slug=slug, axis=axis,
        fresh=_BONE_ROT_FRESH_SECONDS)
    if abs(bindValue) < 1e-9:
        return core
    return "({}+({}))".format(core, _FormatNumber(bindValue))


def RewriteBoneRotationCall(args, memberTail, report=None):
    """`ysm.bone_rot('骨骼').x` → 读取式; 参数不是字面量/成员不是 x|y|z → None(调用方置零)"""
    boneName = _Literal(args[0]) if len(args) == 1 else None
    axis = (memberTail or "").strip().lstrip(".").strip().lower()
    if not boneName or axis not in _BONE_ROT_AXES:
        return None
    sink = CurrentSink()
    if report is not None:
        report["map:ysm.bone_rot('{}').{} -> 骨骼旋转回读变量".format(boneName, axis)] += 1
    return BoneRotationRead(boneName, axis, sink.bindRotations if sink is not None else None)


def LoadBindRotations(geoPath):
    """几何文件 → {骨骼名(小写): [rx, ry, rz]}(只收有绑定旋转的骨骼)"""
    rotations = {}
    if not geoPath or not os.path.isfile(geoPath):
        return rotations
    try:
        with io.open(geoPath, encoding="utf-8") as handle:
            data = json.load(handle)
    except (IOError, ValueError):
        return rotations
    for geometry in data.get("minecraft:geometry") or []:
        for bone in geometry.get("bones") or []:
            rotation = bone.get("rotation")
            if isinstance(rotation, list) and len(rotation) == 3 \
                    and all(isinstance(value, (int, float)) for value in rotation) \
                    and any(abs(value) > 1e-9 for value in rotation):
                rotations[str(bone.get("name", "")).lower()] = [float(value) for value in rotation]
    return rotations


def _SplitStatements(text):
    """按顶层 ';' 切分(括号/花括号/引号内不算) → 语句列表(不含空语句)"""
    parts, depth, quote, current = [], 0, None, []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch == "'":
            quote = ch
        elif ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        elif ch == ";" and depth <= 0:
            if "".join(current).strip():
                parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


_RETURN_HEAD = re.compile(r"^return\b\s*", re.I)


def WrapBoneRotationWriter(expression, slug, axis, withStamp):
    """写侧通道分量(表达式串) → "先存变量再返回"; 已经包过/不是表达式 → 原样。

    `EXPR` → `variable.ysm_br_<s>_<a>=(EXPR);[variable.ysm_brt_<s>=query.life_time;]return variable.ysm_br_<s>_<a>;`
    复杂表达式(带语句)取末尾的 `return EXPR;`; 没有 return 的语句串(返回 0)不包。
    """
    valueVar = "variable.{}{}_{}".format(BONE_ROT_VALUE_PREFIX, slug, axis)
    if not isinstance(expression, type(u"")) and not isinstance(expression, str):
        return expression
    if valueVar + "=" in expression.replace(" ", ""):
        return expression
    statements = _SplitStatements(expression)
    if not statements:
        return expression
    complexForm = ";" in expression or "=" in re.sub(r"[=!<>]=", "", expression)
    if complexForm:
        if not _RETURN_HEAD.match(statements[-1]):
            return expression
        head, valueExpr = statements[:-1], _RETURN_HEAD.sub("", statements[-1])
    else:
        head, valueExpr = [], statements[0]
    tail = ["{}=({})".format(valueVar, valueExpr)]
    if withStamp:
        tail.append("variable.{}{}=query.life_time".format(BONE_ROT_STAMP_PREFIX, slug))
    tail.append("return {}".format(valueVar))
    return ";".join(head + tail) + ";"


def _IterJsonFiles(directories):
    for directory in directories:
        if not directory or not os.path.isdir(directory):
            continue
        for root, _dirs, files in os.walk(directory):
            for name in sorted(files):
                if name.endswith(".json"):
                    yield os.path.join(root, name)


def _ReadText(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def CollectBoneRotationReads(directories):
    """产物文本里被回读的骨骼 slug 集合"""
    slugs = set()
    for path in _IterJsonFiles(directories):
        for slug, _axis in _BONE_READ_PATTERN.findall(_ReadText(path).lower()):
            slugs.add(slug)
    return slugs


def InlineBoneRotationWriters(animationDirs, loadJson, dumpJson, readDirs=None):
    """写侧改写: 动画文件里写"被回读骨骼"rotation 的表达式分量包成先存变量再返回。

    只包**非关键帧的表达式分量**(数值常量与关键帧字典不动: 常量通道另有恒等判定/逐通道覆盖等后处理认它们的
    数值形态, 关键帧的插值结果表达式里也拿不到); 头发物理链全是表达式通道。幂等。返回 [(文件名, 动画, 骨骼, 分量数)]。
    """
    wantedSlugs = CollectBoneRotationReads(readDirs or animationDirs)
    wrapped = []
    if not wantedSlugs:
        return wrapped
    for path in _IterJsonFiles(animationDirs):
        data = loadJson(path)
        animations = data.get("animations") if isinstance(data, dict) else None
        if not isinstance(animations, dict):
            continue
        changed = False
        for animKey, body in animations.items():
            bones = body.get("bones") if isinstance(body, dict) else None
            if not isinstance(bones, dict):
                continue
            for boneName, channels in bones.items():
                slug = BoneSlug(boneName)
                if slug not in wantedSlugs or not isinstance(channels, dict):
                    continue
                rotation = channels.get("rotation")
                if isinstance(rotation, (str, type(u""))):
                    rotation = [rotation, rotation, rotation]
                if not isinstance(rotation, list) or len(rotation) != 3:
                    continue
                count, stamped = 0, False
                newRotation = list(rotation)
                for index, axis in enumerate(_BONE_ROT_AXES):
                    component = rotation[index]
                    if not isinstance(component, (str, type(u""))):
                        continue
                    newValue = WrapBoneRotationWriter(component, slug, axis, withStamp=not stamped)
                    stamped = True
                    if newValue != component:
                        newRotation[index] = newValue
                        count += 1
                if count:
                    channels["rotation"] = newRotation
                    changed = True
                    wrapped.append((os.path.basename(path), animKey, boneName, count))
        if changed:
            dumpJson(path, data)
    return wrapped


# ------------------------------------------------------------------ 声明 ----

_ROAMING_NAME_PATTERN = re.compile(r"\b(?:variable|v)\.(roaming_[a-z0-9_]+)")
_PROBE_NAME_PATTERN = re.compile(r"\bvariable\.(ysm_pb_[a-z0-9_]+)")
ROAMING_MAX_NAMES = 64
DECLARATION_KEY = "java_state"
# 派生产物不进扫描: 变量初始化控制器(port_java_pack.VARIABLE_INIT_FILE)里并着默认模型基线动画读的变量
# (roaming_red_bow_headdress 等), 它们不是本包的 roaming 变量; 而且移植期它在声明之后才生成、修复工具重跑时
# 已经在场, 扫进来两条路径的声明就对不上
_DERIVED_FILES = frozenset(["ysm_variable_init.json"])


def _ManifestTexts(node):
    if isinstance(node, dict):
        for value in node.values():
            for text in _ManifestTexts(value):
                yield text
    elif isinstance(node, list):
        for value in node:
            for text in _ManifestTexts(value):
                yield text
    elif isinstance(node, (str, type(u""))):
        yield node


def BuildDeclaration(directories, manifest=None, sinkProbes=None):
    """扫产物文本(动画/控制器目录, 递归) + ysm.json 表单表达式 → `java_state` 声明; 什么都不需要 → None。

    needs = 产物里出现了哪些运行层落点; roaming = 出现过的 roaming_* 变量(Java 上限 64 个, 按名排序截断);
    probes = 产物里还在读的探针(本次移植登记的 ∪ ysm.json 里已有的声明 —— 修复工具重跑时没有汇, 靠后者保留)。
    幂等: 同样的产物得出同样的声明。
    """
    corpus = []
    for path in _IterJsonFiles(directories):
        if os.path.basename(path) in _DERIVED_FILES:
            continue
        corpus.append(_ReadText(path).lower())
    properties = (manifest or {}).get("properties")
    corpus.extend(text.lower() for text in _ManifestTexts(properties))
    joined = "\n".join(corpus)

    needs = []
    for token, need in _NEED_TOKENS.items():
        if token in joined and need not in needs:
            needs.append(need)
    for need in list(needs):
        for implied in _NEED_IMPLIES.get(need, ()):
            if implied not in needs:
                needs.append(implied)
    roaming = sorted(set(_ROAMING_NAME_PATTERN.findall(joined)))[:ROAMING_MAX_NAMES]
    known = OrderedDict()
    existing = ((manifest or {}).get(DECLARATION_KEY) or {}).get("probes")
    if isinstance(existing, dict):
        known.update(existing)
    known.update(sinkProbes or {})
    usedProbes = set(_PROBE_NAME_PATTERN.findall(joined))
    probes = OrderedDict((name, known[name]) for name in sorted(known) if name in usedProbes)

    declaration = OrderedDict()
    if needs:
        declaration["needs"] = needs
    if roaming:
        declaration["roaming"] = roaming
    if probes:
        declaration["probes"] = probes
    return declaration or None


def ApplyDeclaration(manifest, declaration):
    """把声明写进/撤出 ysm.json 顶级 java_state; 返回是否有变化"""
    before = manifest.get(DECLARATION_KEY)
    if declaration:
        manifest[DECLARATION_KEY] = declaration
    else:
        manifest.pop(DECLARATION_KEY, None)
    return before != manifest.get(DECLARATION_KEY)


def DeclarationReportLine(declaration):
    if not declaration:
        return None
    parts = []
    if declaration.get("needs"):
        parts.append(u"运行层状态 {}".format(u"/".join(declaration["needs"])))
    if declaration.get("roaming"):
        parts.append(u"roaming 变量 {} 个(存档 + 多人同步)".format(len(declaration["roaming"])))
    if declaration.get("probes"):
        parts.append(u"探针 {} 个".format(len(declaration["probes"])))
    return u"java_state(顶层) → {}".format(u", ".join(parts))


# ------------------------------------------------------------------ 鞘翅追随角 ----

ELYTRA_STATE_ANIMATION = "animation.ysm.java_elytra_state"


def BuildElytraStateStatement():
    """主包共享动画 java_elytra_state 的逐帧语句: 复刻原版 ElytraModel.setupAnim 的追随角(度)。

    Java: 目标角 —— 滑翔时按速度方向 f4 = 下落时 1-(-v̂y)^1.5、否则 1, rotX = 20·f4 + 15·(1-f4)、
    rotZ = -90·f4 - 15·(1-f4); 潜行 rotX 40 / rotY 5 / rotZ -45; 其余 15 / 0 / -15。每渲染帧向目标靠 10%
    (帧率相关), 这里按 60 帧折成与帧率无关的系数 1-0.9^(60·dt)。速度方向取主包逐帧的位移速度
    (query.mod.ysm_ground_speed2, 格/秒)与 query.vertical_speed。资源包文件走旧版 Molang 语义: 三元全加括号。
    """
    speed = "math.sqrt(query.mod.ysm_ground_speed2*query.mod.ysm_ground_speed2" \
            "+query.vertical_speed*query.vertical_speed)"
    lines = [
        "variable.ysm_el_k=1-math.pow(0.9,math.clamp(query.delta_time,0,0.1)*60)",
        "variable.ysm_el_s={}".format(speed),
        "variable.ysm_el_f=((query.vertical_speed<0&&variable.ysm_el_s>0.0001)"
        "?(1-math.pow(math.clamp(-query.vertical_speed/variable.ysm_el_s,0,1),1.5)):1)",
        "variable.ysm_el_tx=(query.is_gliding?(15+5*variable.ysm_el_f):(query.is_sneaking?40:15))",
        "variable.ysm_el_ty=((!query.is_gliding&&query.is_sneaking)?5:0)",
        "variable.ysm_el_tz=(query.is_gliding?(-15-75*variable.ysm_el_f):(query.is_sneaking?-45:-15))",
        "variable.ysm_elytra_rot_x=(variable.ysm_elytra_rot_x??15)"
        "+(variable.ysm_el_tx-(variable.ysm_elytra_rot_x??15))*variable.ysm_el_k",
        "variable.ysm_elytra_rot_y=(variable.ysm_elytra_rot_y??0)"
        "+(variable.ysm_el_ty-(variable.ysm_elytra_rot_y??0))*variable.ysm_el_k",
        "variable.ysm_elytra_rot_z=(variable.ysm_elytra_rot_z??-15)"
        "+(variable.ysm_el_tz-(variable.ysm_elytra_rot_z??-15))*variable.ysm_el_k",
        "return 0",
    ]
    return ";".join(lines) + ";"


def BuildElytraStateAnimationFile():
    return OrderedDict([
        ("format_version", "1.10.0"),
        ("animations", OrderedDict([
            (ELYTRA_STATE_ANIMATION, OrderedDict([
                ("loop", True),
                ("bones", OrderedDict([
                    ("Head", OrderedDict([("rotation", [BuildElytraStateStatement(), 0, 0])])),
                ])),
            ])),
        ])),
    ])
