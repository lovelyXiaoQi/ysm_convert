# -*- coding: utf-8 -*-
"""Java 版 YSM 模型包 → 网易基岩包移植工具(Python 2.7)。

把一个 Java 模型包目录(含 ysm.json)转换为本项目布局, **资源只落一份**:
  资源包 ysm_rp/  —— 几何/动画/贴图 + GUI 预览实体定义(引擎实际加载的唯一副本)
  行为包 ysm_bp/ysm_models/<包名>/ysm.json —— 只有声明文件, 不复制任何资源
                 (主包的资源索引直接扫描资源包磁盘取动画/控制器/变量/缩放,
                  故无需行为包副本 —— 改了资源包不会漏同步)

核心工作是 ID 前缀改写: Java 原包的几何 identifier 是 geometry.unknown、动画名是
裸短名(parallel0 / hold_mainhand:sword), 而基岩的资源 ID 是**全局路由**, 必须带
命名空间前缀防止跨包冲突。改写规则与 packParser 的推导约定严格一致:

    主几何      geometry.<包名>
    手臂几何    geometry.<包名>_arm                  (files.player.model.arm)
    替换实体    geometry.<包名>_<model文件基名>      (projectiles / vehicles)
    主动画      animation.<包名>.<原短名>
    手臂动画    animation.<包名>_arm.<原短名>        (声明键 fp_arm / arm)
    替换实体    animation.<包名>_<model文件基名>.<原短名>
    贴图        textures/entity/<包名>/<文件名去扩展>
    音频        sounds/ysm/<包名>/<安全名>.ogg, 定义 ysm.<包名>.<安全名>(sound_definitions.json)

用法:
    python devtools/port_java_pack.py <Java包目录> [--name <包名>] [--collection <合集目录>] [--with-mods]
    python devtools/port_java_pack.py --list          # 列出子模块自带的官方包

包名缺省取 Java 包目录名; --collection 把模型放进 ysm_models/<合集>/<包名>/,
配合合集目录下的 ysm-pack.json 生成模型选择界面的文件夹分组。
默认只移植 main/arm/extra/carryon/fp_arm 等本体动画, 第三方模组联动动画
(tac/slashblade/parcool/...)整键跳过, 需要时加 --with-mods。
"""
import copy
import errno
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter, OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Java 版内置模型目录("真实文件"用例/移植源的数据源): 取自 ysm-java-src 子模块自带资源。
# 子模块正规位置是 .ref/(参考源码区, 与 docs/ 的自有文档分开); docs/ 为历史位置回落。
# 两处都没有时相关用例自动跳过(git submodule update --init 即可)。
def _FindJavaBuiltinDir():
    tail = ("src", "main", "resources", "assets", "ysm", "builtin")
    for parent in (".ref", "docs"):
        candidate = os.path.join(ROOT, parent, "ysm-java-src", *tail)
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(ROOT, ".ref", "ysm-java-src", *tail)


JAVA_BUILTIN_DIR = _FindJavaBuiltinDir()

# 条件动画名的转义规则/物品判定合成与主包解析器共用同一份实现, 避免两边规则分叉
sys.path.insert(0, os.path.join(ROOT, "ysm_bp"))
from ysmModelScripts.packLoader.packParser import (  # noqa: E402
    AnimationChannelPairs, BuildOneShotControllers, BuildStateChainController,
    EscapeConditionKey, JAVA_TO_BEDROCK_ENTITY_IDS, JavaStateLoopType, ReplacedTargets,
    StripJsonComments, _BuildConditionalAnimates, _BuildJavaStateAnimates, _CLASSIFY_TESTS,
    _ClassifySelfTest,
    _CONDITION_FALLBACK_KEYS, _OWNERSHIP_COMPANION_PATTERN, _OWNERSHIP_VARIABLE_PATTERN,
    _INPUT_STATE_VARIABLE_PATTERN,
    _JAVA_ATTACK_TIME, _ONESHOT_SWING_KEY, _ONESHOT_USE_CHANNELS, _SWING_ACTIVE_TEST,
    _USE_MAINHAND_GATE, _USE_OFFHAND_GATE,
    _IsMainSwingKey, _ItemNameTest, _ItemTagTest, _OneShotConditionMembers, _OneShotSwingMembers,
    _SplitConditionKey, _STATE_CHAIN_KEY, _STATE_ENTER_BLEND, _UnescapeConditionKey)
# 基岩 molang 语法判定与 Java 口径守卫(体检脚本共用同一判定, 见 molang_syntax.py 注)
from molang_syntax import (  # noqa: E402
    AnimationMolangSlots, ExplicitAnimationPrecedence, ExplicitControllerPrecedence, FormatSlotPath,
    GuardAnimationMolang, GuardControllerMolang)
# Java 脚本控制器(functions/*@player_ctrl_<通道>.molang)转换, 见 script_controller.py 注
from script_controller import ConvertPackScripts  # noqa: E402

RP = os.path.join(ROOT, "ysm_rp")
BP_MODELS = os.path.join(ROOT, "ysm_bp", "ysm_models")
# java_default 基线所在资源包: 移植期读它并入状态机成员/初始化变量(BaselineAnimationLoops /
# CollectBaselineVariables)。独立组件的资源包里没有基线, 由宿主(port_cli / 转换器 GUI)指向自带快照
REF_RP = RP


def SetLayout(rp=None, bpModels=None, refRp=None, root=None):
    """重定向产物目录(转换器 GUI / port_cli 宿主用); 缺省仍是本仓库 ysm_rp 与 ysm_bp/ysm_models。

    rp / bpModels = 产物落盘的资源包根 / 行为包 ysm_models 目录; refRp = java_default 基线所在
    资源包(缺省与 rp 相同); root 只影响汇总文本里的相对路径。模块内全部路径都在调用时按这几个
    全局拼出, 唯一在 import 期算好的 _SOUND_DEFINITIONS_FILE 在此同步重算。
    """
    global RP, BP_MODELS, REF_RP, ROOT, _SOUND_DEFINITIONS_FILE
    if rp is not None:
        RP = rp
        REF_RP = rp
        _SOUND_DEFINITIONS_FILE = os.path.join(RP, "sounds", "sound_definitions.json")
    if bpModels is not None:
        BP_MODELS = bpModels
    if refRp is not None:
        REF_RP = refRp
    if root is not None:
        ROOT = root


def _DisplayPath(path):
    """汇总文本用的相对路径(unicode); 产物与仓库不在同一盘符时 relpath 抛 ValueError, 退回绝对路径。
    统一返回 unicode: 汇总行用 u"" 模板, 字节串路径含中文时 u"".format(bytes) 会按 ascii 解码而炸。"""
    try:
        shown = os.path.relpath(path, ROOT)
    except (ValueError, UnicodeError):
        shown = path
    if isinstance(shown, str):
        for encoding in (sys.getfilesystemencoding() or "utf-8", "utf-8"):
            try:
                return shown.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return shown.decode("utf-8", "replace")
    return shown

# 手臂动画声明键(走 <包名>_arm 命名空间, 与 packParser._FP_ARM_ANIMATION_KEYS 一致)
ARM_ANIMATION_KEYS = ("fp_arm", "arm")

# 第三方模组联动动画: 默认不移植。这些动画的条件键绑定 Java 侧模组物品
# (tacz/slashblade/superbwarfare 等), 基岩既无对应物品也无对应判定 —— 移植过来
# 只会堆无效注册与分类告警。要带上时传 --with-mods。
MOD_ANIMATION_KEYS = (
    "tac", "parcool", "swem", "slashblade", "tlm",
    "immersive_melodies", "irons_spell_books",
)


def LoadJson(path):
    """读 JSON; 容忍 JSONC(注释/尾逗号) —— 野外 Java 包普遍带注释, Gson 宽容模式收得下"""
    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig")
    try:
        return json.loads(text, object_pairs_hook=OrderedDict)
    except ValueError:
        return json.loads(StripJsonComments(text), object_pairs_hook=OrderedDict)


# Windows 上刚写完的大文件偶尔被杀毒 / 编辑器 / MC Studio 文件监听短暂占用, open(..., "wb") 报 Errno 22 或 13
# (2026-09-18 移植萨赫梅特、修复坚守者娘各撞一次; 后者发生在撤掉伴生之后、重新应用之前, 产物停在中间态)
_WRITE_RETRY_ERRNOS = (errno.EINVAL, errno.EACCES)
_WRITE_RETRY_ATTEMPTS = 8


def WriteBytes(path, payload):
    """写文件字节; 只对文件被临时占用的两种错误退避重试"""
    EnsureDir(os.path.dirname(path))
    for attempt in range(_WRITE_RETRY_ATTEMPTS):
        try:
            with open(path, "wb") as f:
                f.write(payload)
            return
        except (IOError, OSError) as error:
            if getattr(error, "errno", None) not in _WRITE_RETRY_ERRNOS or attempt == _WRITE_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(0.25 * (attempt + 1))


def DumpJson(path, data):
    """写 JSON(2 空格缩进 + 结尾换行); 先序列化再写, 写入经 WriteBytes 重试"""
    WriteBytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")


def EnsureDir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path)


def BaseName(relPath):
    """包内相对路径 → 文件基名(去目录去扩展), 与 packParser._TextureEntryPath 同口径"""
    name = os.path.basename(relPath if isinstance(relPath, unicode) else str(relPath or ""))  # noqa: F821
    return re.sub(r"\.(json|png|jpg|jpeg)$", "", name, flags=re.I)


# Java GeoBuilder 只认 bone 的 name/parent/pivot/rotation/cubes/inflate/mirror 与 cube 的
# origin/size/uv/inflate/mirror/pivot/rotation; 每面 UV 必须同时有 uv 与 uv_size(缺一的面
# addFace 直接不画)。基岩会渲染 Java 看不见的东西(uv_rotation / binding / texture_meshes /
# poly_mesh), 两边一致起见移植期一律剥掉; 1.21.0 格式只因 uv_rotation 而来, 剥掉后降回 1.12.0
# (网易 3.9 引擎对 1.21 几何的支持无证据, 三个参考包全是 1.12.0)。
_JAVA_IGNORED_BONE_KEYS = ("binding", "texture_meshes", "poly_mesh", "render_group_id")
_JAVA_FACE_KEYS = ("uv", "uv_size")


def _GeometryVersionTuple(text):
    try:
        return tuple(int(part) for part in str(text).split("."))
    except (TypeError, ValueError):
        return (0,)


def StripJavaIgnoredGeometry(data):
    """几何数据里 Java 不渲染的字段一律剥掉(见 _JAVA_IGNORED_BONE_KEYS 注); 返回改动数。幂等。"""
    fixes = 0
    for geo in data.get("minecraft:geometry") or []:
        if not isinstance(geo, dict):
            continue
        for bone in geo.get("bones") or []:
            if not isinstance(bone, dict):
                continue
            for key in _JAVA_IGNORED_BONE_KEYS:
                if key in bone:
                    del bone[key]
                    fixes += 1
            for cube in bone.get("cubes") or []:
                faces = cube.get("uv") if isinstance(cube, dict) else None
                if not isinstance(faces, dict):
                    continue
                for faceName in list(faces.keys()):
                    face = faces[faceName]
                    if not isinstance(face, dict) or not isinstance(face.get("uv"), list) \
                            or len(face["uv"]) < 2 or not isinstance(face.get("uv_size"), list) \
                            or len(face["uv_size"]) < 2:
                        del faces[faceName]     # Java: 缺 uv/uv_size 的面不画
                        fixes += 1
                        continue
                    for key in list(face.keys()):
                        if key not in _JAVA_FACE_KEYS:
                            del face[key]       # uv_rotation / material_instance
                            fixes += 1
    if _GeometryVersionTuple(data.get("format_version")) > (1, 16, 0):
        data["format_version"] = "1.12.0"
        fixes += 1
    return fixes


def RewriteGeometry(srcPath, dstPath, identifier, report=None):
    """几何文件 → 改 identifier + 剥掉 Java 不渲染的字段后写出(Java 与基岩同为 bedrock geometry 格式)"""
    data = LoadJson(srcPath)
    changed = 0
    for geo in data.get("minecraft:geometry") or []:
        desc = geo.get("description")
        if isinstance(desc, dict):
            desc["identifier"] = identifier
            changed += 1
    stripped = StripJavaIgnoredGeometry(data)
    if stripped and report is not None:
        report.append(u"  几何 {} 剥掉 Java 不渲染的字段 {} 处"
                      u"(uv_rotation/binding/texture_meshes/缺 uv_size 的面/1.21 版本号)".format(
                          os.path.basename(dstPath), stripped))
    DumpJson(dstPath, data)
    return changed


# ---- 第一人称手臂几何: 原版手臂骨架包装(对齐 Java 的"纯平移映射") ----
# Java 的第一人称手臂是**独立渲染域**: CustomFirstPersonArmEntity 用 files.player.model.arm
# 的几何, 在原版 ItemInHandRenderer.renderPlayerArm 的姿态栈上做
# `translate(∓0.25, 1.8, 0)` + `scale(-1,-1,1)`, 再按 RENDER_MODE_RIGHT/LEFT_ARM 只画
# RightArm / LeftArm 子树(client/renderer/CustomFirstPersonArmRenderer.java:38-45)。
# 结合 GeoBuilder 的 bedrock→内部映射(pivot x 取反、y 不变, format/parser/GeoBuilder.java:75-79)
# 与原版 ModelPart 手臂(pivot (-5,2,0)、cube x∈[-3,1] y∈[-2,10])换算, 等价于一句话:
#
#   **Java arm 模型坐标 (x, y, z) ≡ 原版手臂骨架坐标系的 (x∓4, y-4.8, z)**(右臂 -4/左臂 +4),
#   且**刚性挂在原版手臂骨骼上** —— 原版那套挥击/换物/走路摆动照样整体套在它身上。
#
# 双旁证: ① Java 自带 CC0 `default/models/arm.json` 右臂 cube x∈[-3.45,-0.2] y∈[16.83,30.95],
# 按上式移位后 x∈[-7.45,-4.2] y∈[12.03,26.15], 原版 rightArm cube 是 x∈[-8,-4] y∈[12,24]
# —— 手部底端对到 0.03px; ② CSM 的转换产物同样把 x 移 ∓4(.ref/csm 的 ysm/default/arm.json)。
# ⚠️ CSM 的 y **不移**, 改在动画里用 `-this` 抹掉原版 empty_hand 的 -10 —— 与 Java 差约 +14px,
# 别照抄它的 y。
#
# 基岩侧同构: 把 arm 几何重建成"原版手臂骨架(包装骨骼) + 移位后的 Java 子树",
# 让原版第一人称动画原样驱动包装骨骼:
#   body(pivot [0,24,0])         ← animation.player.first_person.base_pose 的 [俯仰, 偏航, 0]
#   ├ rightArm(pivot [-5,22,0])  ← empty_hand pos[13.5,-10,12]/rot[95,-45,115] + swap_item/walk/挥击
#   │  └ <Java RightArm 子树: pivot / cube origin / cube pivot / locator 全体移位 (-4,-4.8,0),
#   │     自身 bind 旋转原样保留(12_little 的 ∓30° 在 Java 同样生效)>
#   └ ysm_fp_leftarm(pivot [5,22,0]) ← 左臂子树; 基岩第一人称只渲染主手侧, 由主包动画 scale 0 隐藏
#
# **body 这根是关键**(2026-09-18 逐包核对): 25 个包的 arm 几何全都没有名为 body 的骨骼,
# 原版 base_pose 整条落空 → 手臂完全不跟随视角俯仰(主包 animation.ysm.fp.body_follow 只补了
# 偏航, 而且写的是嫁接进来的 UpBody)。按原版数值算手臂在体坐标里位于相机下方 0.92 方块 /
# 前方 0.70 方块, 不跟俯仰的话抬头 30° 就掉出画面、抬头 45° 跑到相机背后 —— 用户报的
# "第一人称手臂看不见"。左臂**必须用唯一名**而不是 leftArm: 后者会命中附着物锚点几何
# (default_steve)的同名骨骼, scale 0 会把副手物品一起压没。
# 撤掉的旧做法 GraftArmParentChain(把主几何的手臂父链嫁接进来): 父链在第一人称是静态的,
# 既补不出俯仰, 还带进 Java 没有的静态旋转(08_sta 的 RightArm_size 18°)和 UpBody 双偏航。
_FP_ARM_BODY_BONE = "body"
_FP_ARM_BODY_PIVOT = [0, 24, 0]
_FP_ARM_WRAPPERS = (
    # (侧, 包装骨骼名, pivot, 子树整体移位量)
    ("right", "rightArm", [-5, 22, 0], (-4.0, -4.8, 0.0)),
    ("left", "ysm_fp_leftarm", [5, 22, 0], (4.0, -4.8, 0.0)),
)
# 与原版第一人称动画/锚点几何撞名的骨骼: Java 子树里出现时改名让位(包装骨骼要独占这些名字)
_FP_ARM_RESERVED_BONES = ("body", "head", "rightarm", "leftarm")
_FP_ARM_RENAME_SUFFIX = "_ysmfp"


def _ShiftVector3(values, shift):
    """[x,y,z] 整体平移(非法形状原样返回)"""
    if not (isinstance(values, list) and len(values) == 3):
        return values
    out = []
    for index in range(3):
        try:
            out.append(round(float(values[index]) + shift[index], 5))
        except (TypeError, ValueError):
            return values
    return out


def _ShiftBoneGeometry(bone, shift):
    """一根骨骼的 pivot / cube origin / cube pivot / locator 全体平移"""
    if bone.get("pivot") is not None:
        bone["pivot"] = _ShiftVector3(bone["pivot"], shift)
    for cube in bone.get("cubes") or []:
        if not isinstance(cube, dict):
            continue
        if cube.get("origin") is not None:
            cube["origin"] = _ShiftVector3(cube["origin"], shift)
        if cube.get("pivot") is not None:
            cube["pivot"] = _ShiftVector3(cube["pivot"], shift)
    locators = bone.get("locators")
    if isinstance(locators, dict):
        for name in list(locators.keys()):
            value = locators[name]
            if isinstance(value, list):
                locators[name] = _ShiftVector3(value, shift)
            elif isinstance(value, dict) and isinstance(value.get("offset"), list):
                value["offset"] = _ShiftVector3(value["offset"], shift)


def _BoneChildren(bones):
    """{父骨骼名: [子骨骼名...]}(保序)"""
    kids = OrderedDict()
    for bone in bones:
        kids.setdefault(bone.get("parent"), []).append(bone.get("name"))
    return kids


def _BoneSubtree(bones, root):
    """root 及其全部后代骨骼名(保序, 深度优先)"""
    kids = _BoneChildren(bones)
    out = []
    stack = [root]
    while stack:
        name = stack.pop(0)
        out.append(name)
        stack = list(kids.get(name, [])) + stack
    return out


def BuildFirstPersonArmGeometry(armDstPath):
    """arm 几何 → "原版手臂骨架包装 + 移位后的 Java 子树"(见上方长注)。

    返回 (骨骼改名表 {原名: 新名}, 报告行列表)。幂等: 已包装过的文件原样返回 ({}, [])。
    改名表要一路带到 fp_arm 动画的改写(RewriteAnimations 的 boneRenames), 否则作者写在
    RightArm 上的第一人称动画会落到包装骨骼上(pivot 不是模型肩膀)。
    """
    data = LoadJson(armDstPath)
    geos = data.get("minecraft:geometry") or []
    if not geos or not isinstance(geos[0], dict):
        return {}, []
    geo = geos[0]
    bones = geo.get("bones")
    if not isinstance(bones, list) or not bones:
        return {}, []
    byName = OrderedDict((bone.get("name"), bone) for bone in bones)
    if any(name == wrapper for _side, wrapper, _pivot, _shift in _FP_ARM_WRAPPERS
           for name in byName):
        return {}, []                      # 已包装(幂等)

    notes = []
    renames = {}

    def _FindRoot(side):
        """侧手臂子树的根骨骼: 名字 == <side>Arm(大小写不敏感)里最靠上的那根"""
        target = side + "arm"
        hits = [name for name in byName if str(name).lower() == target]
        if not hits:
            return None
        # 取深度最小的(理论上只有一根)
        def _Depth(name):
            depth, cursor, seen = 0, byName[name].get("parent"), set()
            while cursor in byName and cursor not in seen:
                seen.add(cursor)
                depth += 1
                cursor = byName[cursor].get("parent")
            return depth
        return sorted(hits, key=_Depth)[0]

    roots = OrderedDict()
    for side, _wrapper, _pivot, _shift in _FP_ARM_WRAPPERS:
        root = _FindRoot(side)
        if root is not None:
            roots[side] = root
    if not roots:
        # 非标准命名的手臂模型: 整份当主手子树挂上去(不移位, 位置得作者自己调),
        # 至少保证俯仰跟随与"手臂不留在建模原位"
        notes.append(u"[WARN] 手臂几何里找不到 RightArm/LeftArm 骨骼, 整份挂到 rightArm 包装骨骼下"
                     u"(不做 ∓4/-4.8 移位, 位置需作者自行核对)")
        for bone in bones:
            if not bone.get("parent"):
                bone["parent"] = _FP_ARM_WRAPPERS[0][1]
        subtreeNames = set()
    else:
        subtreeNames = set()
        for side, wrapper, _pivot, shift in _FP_ARM_WRAPPERS:
            root = roots.get(side)
            if root is None:
                continue
            names = _BoneSubtree(bones, root)
            subtreeNames.update(names)
            for name in names:
                _ShiftBoneGeometry(byName[name], shift)
            byName[root]["parent"] = wrapper
            notes.append(u"  {} 子树 {} 根骨骼整体移位 {} 并挂到 {}".format(
                root, len(names), list(shift), wrapper))
        # Java 只渲染/只驱动 LeftArm 与 RightArm 子树("任何在此组之外的组既不会渲染,
        # 也无法应用动画" —— wiki《第一人称动画》), 子树外的骨骼(常见是它们共同的父骨骼
        # Arm)一律挂到隐藏的左臂包装下: 带 cube 的不会显示(scale 0), 动画引用仍能解析
        outside = [bone.get("name") for bone in bones
                   if bone.get("name") not in subtreeNames]
        for name in outside:
            byName[name]["parent"] = _FP_ARM_WRAPPERS[1][1]
        if outside:
            notes.append(u"  子树外骨骼 {} 根挂到隐藏包装下(Java 同样不渲染不驱动): {}".format(
                len(outside), ", ".join(str(n) for n in outside[:6])))

    # 与原版第一人称动画撞名的 Java 骨骼改名(包装骨骼要独占 body/rightArm 等名字)
    for name in list(byName.keys()):
        if str(name).lower() not in _FP_ARM_RESERVED_BONES:
            continue
        newName = "{}{}".format(name, _FP_ARM_RENAME_SUFFIX)
        while newName in byName:
            newName += _FP_ARM_RENAME_SUFFIX
        renames[str(name)] = newName
        byName[name]["name"] = newName
        byName[newName] = byName.pop(name)
    if renames:
        for bone in bones:
            parent = bone.get("parent")
            if parent in renames:
                bone["parent"] = renames[parent]
        notes.append(u"  与原版第一人称动画撞名的骨骼改名: {}".format(
            ", ".join(u"{}→{}".format(old, new) for old, new in sorted(renames.items()))))

    wrappers = [OrderedDict([("name", _FP_ARM_BODY_BONE), ("pivot", list(_FP_ARM_BODY_PIVOT))])]
    for _side, wrapper, pivot, _shift in _FP_ARM_WRAPPERS:
        wrappers.append(OrderedDict([
            ("name", wrapper), ("parent", _FP_ARM_BODY_BONE), ("pivot", list(pivot))]))
    geo["bones"] = wrappers + bones
    DumpJson(armDstPath, data)
    notes.insert(0, u"  补原版手臂骨架包装骨骼: {}(原版第一人称动画的落点)".format(
        ", ".join([_FP_ARM_BODY_BONE] + [w for _s, w, _p, _sh in _FP_ARM_WRAPPERS])))
    return renames, notes


def FirstPersonArmBoneRenames(geometryRenames=None):
    """第一人称手臂动画的骨骼改名表: 几何实测的改名 + 保留名兜底。

    保留名(body/head/rightarm/leftarm)即使 arm 几何里原本没有也要改: 作者写在这些名字上的
    第一人称动画在 Java 是**空操作**(模型没这根骨骼), 落到我们补的包装骨骼上却会顶掉原版
    第一人称摆位。改名后仍是空操作 = 与 Java 同语义。
    """
    renames = dict(geometryRenames or {})
    lowered = set(str(name).lower() for name in renames)
    for reserved in _FP_ARM_RESERVED_BONES:
        if reserved not in lowered:
            renames[reserved] = "{}{}".format(reserved, _FP_ARM_RENAME_SUFFIX)
    return renames


def RenameAnimationBones(body, renames):
    """动画体里 bones 段的骨骼名改名(大小写不敏感, 保序); 返回改名数。幂等(新名不在表里)。"""
    if not renames or not isinstance(body, dict):
        return 0
    table = dict((str(old).lower(), new) for old, new in renames.items())
    boneChannels = body.get("bones")
    if not isinstance(boneChannels, dict):
        return 0
    renamed = OrderedDict()
    fixes = 0
    for name in boneChannels:
        target = table.get(str(name).lower())
        if target and str(name).lower() != str(target).lower():
            fixes += 1
            renamed[target] = boneChannels[name]
        else:
            renamed[name] = boneChannels[name]
    if fixes:
        body["bones"] = renamed
    return fixes


def _HasAnimationContent(body):
    """动画体是否有实际内容(骨骼/时间轴/粒子/音效关键帧)"""
    if not isinstance(body, dict):
        return False
    return any(body.get(key) for key in ("bones", "timeline", "particle_effects", "sound_effects"))


def IsDecorativeKey(name, body=None):
    """Java 包里用作分组标题的装饰条目(如 "——并行动画——" / "————头颅动画————"): 不是动画。

    判据 = 名字里没有字母数字 **且** 动画体没有任何内容。
    **只看名字会误杀纯中文名的真动画**(2026-09-04 实机: 凋灵娘的 头颅张开（左）/头颅待机（右）/
    持剑奔跑/头发主动飘动-中等幅度 等 16 条、warden 的 右勾拳/肘击/双手防御 等整套拳击 13 条
    全被丢弃 —— 控制器引用随之被当死引用剪掉, 状态变成空的, 骷髅头停在绑定姿态、拳击动作全无。
    只有名字里恰好带 ASCII 的(火焰动画A、语音13)侥幸活下来)。body 省略时退回旧的只看名字判据。
    """
    if re.search(r"[0-9A-Za-z]", name):
        return False
    return body is None or not _HasAnimationContent(body)


# ================= Java → 基岩 molang 批量替换层 =================
# 一个未知 token 会连锁作废整份动画文件(node parse failed, 引擎只报客户端内容日志,
# Python 侧无感), 因此按三级处理, 保证产物里不存在引擎不认识的 token:
#   ① 函数调用剥离: ysm.xxx(...) 按策略取参数或置常量(括号配对扫描)
#   ② 名字映射: Java 专有名 → 主包 query.mod.* / 基岩原生 query / 中性常量
#   ③ 引擎存在性门: 残余 query/math token 必须在"引擎实测集"内, 否则置零并告警
#
# 判据数据(devtools/ 下, 由扫描脚本生成):
# - data_netease_vanilla_tokens.json —— 网易 3.9 原版 RP 12653 个文件实际使用的
#   query 162 种 / math 16 种, 本机引擎"实测存在"的铁证。**微软文档收录 ≠ 网易引擎
#   存在**: query.yaw_speed / math.exp 文档在册、原版零使用, 实测正是 main 整份
#   作废的元凶(与无分号赋值并列)。
# - data_bedrock_queries.json —— 微软官方文档 303 个 query, 仅用于把告警分成
#   "文档有引擎无"与"完全未知"两类文案。
# 映射决策依据 docs/ysm-java-molang-mapping.md + Java 源码 token 普查
# (.ref/ysm-java-src builtin 182 文件: ysm.* 31 种 / ctrl.* 25 种 / query.* 23 种,
#  普查脚本 devtools/scan_java_molang_tokens.py)。

_DEVTOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _LoadDataJson(name):
    path = os.path.join(_DEVTOOLS_DIR, name)
    if not os.path.isfile(path):
        raise SystemExit("[ERROR] 缺少判据数据 {} —— 运行 scan 脚本重新生成".format(name))
    with open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


_VANILLA_TOKENS = _LoadDataJson("data_netease_vanilla_tokens.json")
_DOC_QUERIES = set(_LoadDataJson("data_bedrock_queries.json"))
_BINARY_TOKENS = _LoadDataJson("data_engine_binary_tokens.json")
# 引擎证据分级(用户纠偏: **原版没使用 ≠ 引擎不存在** —— equipped_item_any_tag/
# yaw_speed/math.exp 都是原版零使用而引擎实有的实例):
#   实机探针(最终裁决) > 引擎二进制字符串表(强, 双向) > 原版 RP 使用(仅正面) > 文档(参考)
# 二进制表来自 Minecraft.UnitTest.dll(370MB 未加壳, 对照组 8/8 命中验证有效;
# 主 Minecraft.Windows.exe 加壳、字符串不可见, 勿用)。
_ENGINE_QUERIES = (set(_BINARY_TOKENS["queries_found"])
                   | set(_VANILLA_TOKENS["queries"])
                   | {"mod"})   # query.mod.* 网易 mod 值域(动态注册, 无静态字符串)
_BINARY_ABSENT_QUERIES = set(_BINARY_TOKENS["queries_absent"]) - {"ysm_is_on_ladder"}
# 2026-09-17 实机探针(mcdkSelfTest.mcdk_test_molang_semantics_result 的 existence 表: EvalMolangExpression
# 不报 "expression is not valid" 即存在)。二进制字符串表只作正面证据, 这批它没扫到但引擎实有;
# math.e / 缓动函数(ease_in_quad 等) / math.random 三参数实测不存在
_ENGINE_MATHS_PROBED = {"ln", "die_roll", "die_roll_integer", "random_integer", "lerprotate",
                        "hermite_blend", "min_angle", "inverse_lerp", "copy_sign", "trunc"}
_ENGINE_MATHS = set(_VANILLA_TOKENS["maths"]) | set(_BINARY_TOKENS["maths_found"]) | _ENGINE_MATHS_PROBED

# Java 专有"值读取"名(以 ysm./query./q. 前缀出现) → 基岩替换。census 驱动:
# 只登记 Java builtin 普查实际出现的名字, 新名字由兜底逻辑置零并告警。
# 头/胸/腿/脚装备判定: 基岩无"槽位非空"查询, 按物品名枚举(原版全量护甲 + 常见
# 头部佩戴物)。官方 wiki"换装设计"章节推荐用 has_* 驱动护甲显隐, 置零会让此类
# 模型永远停在"无甲"形态 —— 宁可枚举长表达式。
_ARMOR_SLOT_ITEMS = {
    "head": ("minecraft:leather_helmet", "minecraft:chainmail_helmet",
             "minecraft:iron_helmet", "minecraft:golden_helmet",
             "minecraft:diamond_helmet", "minecraft:netherite_helmet",
             "minecraft:turtle_helmet", "minecraft:carved_pumpkin",
             "minecraft:skull"),
    "chest": ("minecraft:leather_chestplate", "minecraft:chainmail_chestplate",
              "minecraft:iron_chestplate", "minecraft:golden_chestplate",
              "minecraft:diamond_chestplate", "minecraft:netherite_chestplate"),
    "legs": ("minecraft:leather_leggings", "minecraft:chainmail_leggings",
             "minecraft:iron_leggings", "minecraft:golden_leggings",
             "minecraft:diamond_leggings", "minecraft:netherite_leggings"),
    "feet": ("minecraft:leather_boots", "minecraft:chainmail_boots",
             "minecraft:iron_boots", "minecraft:golden_boots",
             "minecraft:diamond_boots", "minecraft:netherite_boots"),
}

# 内层故意不写成 (query.modified_move_speed*1.9): 修复工具按旧文本整串迁移, 内层若含旧文本
# 会在第二遍再次套一层(幂等性守护逮到)
_GROUND_SPEED_EXPR = "((query.modified_move_speed>0.05)?query.modified_move_speed*1.9:0)"
# 界面纸娃娃(背包/暂停界面的玩家实例、YSM 界面的预览实体)读不到 query.mod.ysm_food_level(按 0 算) →
# 各包 `ysm.food_level<=6?...` 的饥饿姿态在纸娃娃上常驻(2026-09-17 用户反馈)。界面里按满饱食度读;
# 世界里取值不变(query.is_in_ui 世界中为 0, is_paperdoll 世界中为 0, 两个界面变量只由 YSM 界面纸娃娃置 1)
_UI_FULL_FOOD_LEVEL = ("((query.is_in_ui||variable.is_paperdoll"
                       "||((variable.ysm_show??0)+(variable.ysm_preview??0))>0)?20.0:query.mod.ysm_food_level)")
_JAVA_NAME_MAP = [
    # —— 主包已同步的 mod 值 ——
    # !! 符号: Java 交给动画的两个量**都是取负后的**(AnimatableEntity.java:322-324:
    #    headPitch = -rawHeadPitch; netHeadYaw = -clamp(wrapDegrees(netHeadYaw),±85))。
    #    主包 query.mod.ysm_head_pitch 恰好也是 -pitch(molangSystem: headPitch=-headRot[0])
    #    → 同号直通; 而 query.mod.ysm_head_yaw 是 **+**(headRot[1]-bodyRot, 未取负)
    #    → 必须补一个负号, 否则 Java 包里所有吃 head_yaw 的表达式(眼球/头发/饰品跟随)
    #    左右相反。站立时 head-body 差≈0 看不出来, 一走一跑就明显(用户 2026-09-03 实测
    #    "奔跑/行走起来看的方向不对")。主包变量本身不动 —— 旧版副包与内置模型按现符号写的。
    ("head_yaw", "(-query.mod.ysm_head_yaw)"),
    ("head_pitch", "query.mod.ysm_head_pitch"),
    # !! Java 的 query.head_x_rotation = netHeadYaw(偏航), head_y_rotation = 俯仰
    # (QueryBinding.java:58-59, 与 ysm.head_yaw/head_pitch 完全同值) —— 与基岩
    # 同名 query 的轴向**正好相反**(基岩 x=俯仰)。同名放行会"摇头变点头", 必须交换。
    ("head_x_rotation", "(-query.mod.ysm_head_yaw)"),
    ("head_y_rotation", "query.mod.ysm_head_pitch"),
    ("food_level", _UI_FULL_FOOD_LEVEL),
    ("armor_value", "query.mod.ysm_armor_value"),
    ("rendering_in_paperdoll", "variable.is_paperdoll"),
    ("rendering_in_inventory", "variable.is_paperdoll"),
    ("input_vertical", "query.mod.ysm_input_vertical"),      # 主包 GetInputVector 下发
    ("input_horizontal", "query.mod.ysm_input_horizontal"),
    # Java xxa/zza = 左右/前后移动输入分量(±0.98), 主包输入向量同域近似; yya 恒 0
    ("xxa", "query.mod.ysm_input_horizontal"),
    ("zza", "query.mod.ysm_input_vertical"),
    ("is_close_eyes", "query.mod.ysm_is_close_eyes"),        # 主包 5 秒眨眼节拍(Java 4.5 秒)
    ("on_ladder", "query.mod.ysm_is_on_ladder"),
    # —— 基岩原生对应(替换目标全部在引擎实测集内) ——
    ("has_mainhand", "query.is_item_equipped(0)"),
    ("has_offhand", "query.is_item_equipped(1)"),
    ("mainhand_charged_crossbow", "query.item_is_charged(0)"),
    ("offhand_charged_crossbow", "query.item_is_charged(1)"),
    ("has_elytra", "query.is_item_name_any('slot.armor.chest','minecraft:elytra')"),
    ("has_helmet", _ItemNameTest("slot.armor.head", _ARMOR_SLOT_ITEMS["head"])),
    ("has_chest_plate", _ItemNameTest("slot.armor.chest", _ARMOR_SLOT_ITEMS["chest"])),
    ("has_leggings", _ItemNameTest("slot.armor.legs", _ARMOR_SLOT_ITEMS["legs"])),
    ("has_boots", _ItemNameTest("slot.armor.feet", _ARMOR_SLOT_ITEMS["feet"])),
    # —— 同名但数值不可用: 网易引擎的这两个 query 实测噪声极大, 直接沿用会让
    #    吃它们的头发/胸部/饰品物理表达式每帧剧烈抖动(游戏内实测定位的鬼畜主因)。
    # query.ground_speed: 走路中 0↔60 每帧乱跳(平均跳变 16.8) → 换平滑的
    #   modified_move_speed(实测 0.59~0.99 连续、停止后平滑衰减); ×1.9 对齐 Java
    #   量纲(Java 行走约 1.7)。ysm.ground_speed2 同为格/秒, 同一替换。
    #   死区: 作者状态机拿 `ground_speed==0` 判静止(Java 静止时精确为 0), 基岩的
    #   modified_move_speed 在冰面滑行/潜行微动时是小非零值, ==0 永假 → 卡在空中转状态
    #   (凋灵娘潜行不动直立, 2026-09-03)。低于走路阈值 0.05(与 ctrl.walk 一致)钳成 0。
    ("ground_speed", _GROUND_SPEED_EXPR),
    ("ground_speed2", _GROUND_SPEED_EXPR),
    # query.yaw_speed: 走路中 0~290 间歇归零(平均跳变 21.7) → 换主包差分+EMA 平滑值
    ("yaw_speed", "query.mod.ysm_yaw_speed"),
    ("is_passenger", "query.is_riding"),
    ("is_sleep", "query.is_sleeping"),
    ("is_sneak", "query.is_sneaking"),
    ("eye_in_water", "query.is_in_water"),           # 近似: Java 为"眼部入水"
    ("time_delta", "query.delta_time"),              # 两帧间隔(秒); 常作除数, 不可置零
    # Java ysm.attack_time = LivingEntity.getAttackAnim = 原版挥手进度 [0,1)
    # (YSMBinding.java:143) —— 基岩 variable.attack_time 是同一个原版量; 但 Java 使用物品期间
    # 攻击键无效(不挥手), 基岩照常出手 → 用主包输入状态的静音标记把"使用中开始的挥动"读作 0
    # (packParser._JAVA_ATTACK_TIME 注)
    ("attack_time", _JAVA_ATTACK_TIME),
    ("swinging", "(" + _SWING_ACTIVE_TEST + ")"),
    ("swing_time", "(" + _JAVA_ATTACK_TIME + "*6)"),     # Java swingTime 一次挥击约 6 tick
    # Java: 0=一人称 1=三人称背面 2=三人称正面; GUI/纸娃娃渲染恒 2(PersonView.java)
    ("person_view",
     "(variable.is_paperdoll?2.0:(query.is_first_person?0.0:1.0))"),
    ("delta_movement_length",
     "math.sqrt(query.ground_speed*query.ground_speed"
     "+query.vertical_speed*query.vertical_speed)"),
    # —— Java 专有且引擎无对应, 降级**语义中性**常量(除数/阈值场景置零会出错) ——
    # (yaw_speed/math.exp 曾按"原版零使用"误判缺失 —— 经引擎二进制证实存在, 已保留原样)
    ("fps", "60.0"),             # 常见用法 60/fps 帧率补偿, 置零会除零
    ("air_supply", "300.0"),     # 满值=永不触发窒息表情; 置零=恒溺水中
    ("is_player", "1.0"),        # 玩家渲染链路上恒真
    ("is_maid", "0.0"),
    ("entity_type", "'player'"),
    ("block_light", "15.0"),     # 满亮度=不触发"暗处"特效
    ("sky_light", "15.0"),
    ("elytra_rot_z", "0.0"),
    ("shoot_item_id", "''"),
    ("texture_name", "''"),
    ("is_fishing", "0.0"),
    ("weather", "0.0"),
    ("yya", "0.0"),
    ("is_open_air", "0.0"),
    ("has_any_curios", "0.0"),
    # —— 2026-09-16 补齐 YSMBinding 的全部值绑定(普查之外的名字也不再落到"残余置零"):
    #    字符串型给 ''(Java 侧模组缺席/未命中时同为 StringUtils.EMPTY, 保住 =='' 比较),
    #    数值型取原版玩家的默认属性值, 让依赖它们的表达式退化成"原版玩家"而不是全零
    ("hurt_time", "query.hurt_time"),
    ("is_riptide", "0.0"),
    ("dimension_name", "'minecraft:overworld'"),
    ("biome_category", "''"),
    ("hit_target_id", "''"),
    ("hit_target_type", "''"),
    ("frozen_ticks", "0.0"),
    ("ladder_facing", "0.0"),
    ("arrow_count", "0.0"),
    ("stinger_count", "0.0"),
    ("swinging_arm", "0.0"),                  # Java: 0 = 主手
    ("first_person_mod_hide", "0.0"),
    ("has_left_shoulder_parrot", "0.0"),
    ("has_right_shoulder_parrot", "0.0"),
    ("left_shoulder_parrot_variant", "0.0"),
    ("right_shoulder_parrot_variant", "0.0"),
    ("attack_damage", "1.0"),                 # 原版玩家属性默认值
    ("attack_speed", "4.0"),
    ("attack_knockback", "0.0"),
    ("movement_speed", "0.1"),
    ("knockback_resistance", "0.0"),
    ("luck", "0.0"),
    ("block_reach", "4.5"),
    ("entity_reach", "3.0"),
    ("swim_speed", "1.0"),
    ("entity_gravity", "0.08"),
    ("step_height_addition", "0.0"),
    ("nametag_distance", "64.0"),
    ("in_shield_block_cooldown", "0.0"),
    ("elytra_rot_x", "0.0"),
    ("elytra_rot_y", "0.0"),
]

# 地面判据的稳定形态: 走路中 query.is_on_ground 每秒翻转数次(实机 40 帧翻 10 次,
# 见 packParser._JAVA_STATE_GROUPS jump 组注), 裸用会让转换包的控制器状态机在
# 行走时每秒抖切数次(实机"动作鬼畜"元凶之一; Java 的 onGround 无此噪声)。
# 判据与 packParser 的 jump 组同源: 误触帧垂直速度约 -1.6 → 仍视为在地,
# 真跳跃(上升)与真下落(< -4)才算离地。仅用于**陆地语境**的表达式(idle/walk/
# run/jump); 水中语境(swim_stand)不适用 —— 漂浮时垂直速度≈0 会被误判为在地。
# **2026-09-03 改为帧间闩锁** variable.ysm_airborne(主包共享的 animation.ysm.java_input_state
# 逐帧更新, 排在 animate 表首位 —— 2026-09-17 前挂在表末的 java_head_look 上, 作者控制器读到
# 上一帧的值, 起跳/落地比主链状态机晚一帧切换, 见 packParser._JAVA_INPUT_STATE_KEY 注): 死区版判据在跳跃
# 最高点(垂直速度穿过 (-4, 0]) 会把"腾空"判成"在地", 作者自己的状态机(凋灵娘 jump_up →
# cache → idle → cache → jump_down)在最高点与落地各抽一次(用户实测); 闩锁进入仍走死区
# (挡住走路时 is_on_ground 的逐帧翻转), **留在腾空**只看 !is_on_ground&&!is_in_water,
# 与主链状态机的粘滞同一思路。?? 兜底: 第一帧/纸娃娃实例上变量可能尚未初始化。
_AIRBORNE_LATCH = "((variable.ysm_airborne??0)>0.5)"
_STABLE_ON_GROUND = "(!{})".format(_AIRBORNE_LATCH)

# ctrl.*(geckolib 控制器局部状态)有明确对应的映射; 其余由兜底置零并告警。
# idle/walk/run 阈值与 packParser._JAVA_STATE_GROUPS 地面组一致(0.01/0.87)。
_CTRL_NAME_MAP = [
    ("elytra_fly", "query.is_gliding"),
    # Java ctrl.jump = !onGround && !inWater(含下落, CtrlBinding NORMAL 级); 闩锁已含 !in_water
    ("jump", _AIRBORNE_LATCH),
    # Java: sneak=潜行**移动**, sneaking=潜行(含静止兜底) —— 移动阈值 0.05(limbSwing)
    ("sneak", "(query.is_sneaking&&query.modified_move_speed>0.05)"),
    ("sneaking", "query.is_sneaking"),
    ("sleep", "query.is_sleeping"),
    ("swim", "(query.swim_amount>0)"),   # 原生 query; variable.swim_amount 有未初始化风险
    ("swim_stand", "(query.is_in_water&&!query.is_swimming&&!query.is_on_ground)"),
    ("ladder_up", "(query.mod.ysm_is_on_ladder>0.5&&query.mod.ysm_climbing_vector>0)"),
    ("ladder_stillness", "(query.mod.ysm_is_on_ladder>0.5&&query.mod.ysm_climbing_vector==0)"),
    ("ladder_down", "(query.mod.ysm_is_on_ladder>0.5&&query.mod.ysm_climbing_vector<0)"),
    ("fly", "query.mod.ysm_is_flying"),                       # 主包创造飞行状态
    ("playing_extra_animation", "query.mod.ysm_wheel_anim"),  # 主包轮盘动画播放中
    # Java ctrl.climb/climbing = 趴下(爬行)移动/静止 —— 基岩原生 is_crawling
    ("climb", "(query.is_crawling&&query.modified_move_speed>0.05)"),
    ("climbing", "(query.is_crawling&&query.modified_move_speed<=0.05)"),
    ("attacked", "(query.hurt_time>0)"),
    ("death", "(query.death_ticks>0)"),
    ("riptide", "0.0"),   # 基岩无 is_riptide/is_auto_spin_attack(实测), 无法判定
    # 被抱起 = 骑乘玩家(主包 riding 值表: minecraft:player → 5)
    ("carryon_is_princess", "(query.mod.ysm_riding==5)"),
    ("idle", "({}&&query.modified_move_speed<=0.05"
             "&&!query.is_riding&&!query.is_sneaking)".format(_STABLE_ON_GROUND)),
    # Java ctrl.run = onGround && isSprinting(疾跑状态, 非速度阈值)
    ("run", "({}&&query.is_sprinting)".format(_STABLE_ON_GROUND)),
    ("walk", "({}&&query.modified_move_speed>0.05"
             "&&!query.is_sprinting&&!query.is_sneaking)".format(_STABLE_ON_GROUND)),
    # —— 模组联动的 ctrl 变量(client/compat/*Compat.java: 模组未安装时的默认绑定):
    #    字符串型给 ''(`ctrl.parcool_state==''` 这类"没在跑酷"判据才保持为真), 布尔/数值给 0
    ("parcool_state", "''"),
    ("slashblade_animation", "''"),
    ("swem_state", "''"),
    ("bcombat_attack_animation", "''"),
    ("iss_animation", "''"),
    ("tac_gun_type", "''"),
    ("tac_gun_id", "''"),
    ("tac_hold_gun", "0.0"),
    ("tac_is_fire", "0.0"),
    ("tac_is_aim", "0.0"),
    ("tac_is_reload", "0.0"),
    ("im_pitch", "0.0"),
    ("im_volume", "0.0"),
    ("im_current", "0.0"),
    ("im_delta", "0.0"),
    ("im_time", "0.0"),
    ("swem_is_ride", "0.0"),
    ("has_sophisticated_backpack", "0.0"),
]

# Java 专有函数(带括号调用)的剥离策略: int=取第 N 个参数(0 基), str=整体替换。
# second_order/first_order 正常由 PhysicsRewriter 改写成 molang 状态积分(见其注),
# 这里的"取输入值"只是键非字面量等改写不了时的兜底 —— 丢平滑保运动。粒子/声音/
# 骨骼函数基岩表达式层无对应, 置零(粒子走基岩动画原生 particle_effects, 未来增量)。
_FUNCTION_STRATEGIES = [
    ("second_order", 1),
    ("first_order", 1),
    ("particle", "0.0"),
    ("abs_particle", "0.0"),
    ("bone_rot", "0.0"),
    ("bone_pos", "0.0"),
    ("bone_scale", "0.0"),
    ("bone_pivot_abs", "0.0"),
    ("bone_color", "0.0"),
    ("bone_transparency", "0.0"),
    ("bone_glow", "0.0"),
    ("play_sound", "0.0"),
    ("stop_sound", "0.0"),
    ("stop_all_sounds", "0.0"),
    ("perlin_noise", "0.0"),
    ("keyboard", "0.0"),
    ("mouse", "0.0"),
    ("sync", "0.0"),
    ("defer", "0.0"),
    ("mod_version", "0.0"),
    # YSMBinding 其余函数(附魔等级/药水等级/相对方块名/调试转储): 基岩表达式层无对应
    ("equipped_enchantment_level", "0.0"),
    ("effect_level", "0.0"),
    ("relative_block_name", "''"),
    ("relative_block_name_any", "0.0"),
    ("dump_equipped_item", "0.0"),
    ("dump_relative_block", "0.0"),
]


def _ScanCall(text, argStart):
    """从左括号后扫描到配对右括号, 返回 (顶层参数列表, 右括号索引)"""
    depth, cursor, args, current = 1, argStart, [], []
    while cursor < len(text):
        ch = text[cursor]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                args.append("".join(current).strip())
                return args, cursor
        elif ch == "," and depth == 1:
            args.append("".join(current).strip())
            current = []
            cursor += 1
            continue
        current.append(ch)
        cursor += 1
    return args, cursor  # 不配对(截断文本), 尽力而为


# ctrl.hold/swing/use/armor(slotType, 'pattern') 的槽位映射(Java 短名 → 基岩全称)
_JAVA_SLOT_NAMES = {
    "mainhand": ("slot.weapon.mainhand", 0),
    "offhand": ("slot.weapon.offhand", 1),
    "head": ("slot.armor.head", None),
    "chest": ("slot.armor.chest", None),
    "legs": ("slot.armor.legs", None),
    "feet": ("slot.armor.feet", None),
}


def _StripQuotes(text):
    text = text.strip()
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0]:
        return text[1:-1]
    return None


def _ItemConditionFromArgs(args, gate):
    """ctrl.hold/swing/use/armor 的参数 → 基岩物品判定表达式; 转不了返回 None。

    参数形如 ('mainhand', '$minecraft:apple'): 槽短名 + 模式串($物品ID/#tag/:分类),
    判定合成逻辑与 packParser 条件动画完全一致(_CLASSIFY_TESTS 同源)。
    """
    if len(args) < 2:
        return None
    slotName = _StripQuotes(args[0])
    pattern = _StripQuotes(args[1])
    if not slotName or not pattern or slotName.lower() not in _JAVA_SLOT_NAMES:
        return None
    slot, handIndex = _JAVA_SLOT_NAMES[slotName.lower()]
    if gate is _USE_GATE_BY_HAND:      # ctrl.use: 门控按槽位分手(见 packParser 同注)
        gate = _USE_OFFHAND_GATE if handIndex == 1 else _USE_MAINHAND_GATE
    kind, param = pattern[0], pattern[1:]
    if kind == "$" and param:
        test = _ItemNameTest(slot, [param])
    elif kind == "#" and param:
        test = _ItemTagTest(slot, [param])
    elif kind == ":" and param:
        if param == "empty":
            if handIndex is None:
                return None
            test = "!query.is_item_equipped({})".format(handIndex)
        else:
            classify = _CLASSIFY_TESTS.get(param)
            if classify is None:
                return None
            test = _ClassifySelfTest(classify, slot, handIndex)
    else:
        return None
    if gate:
        return "({}&&{})".format(gate, test)
    return "({})".format(test)


def _RideConditionFromArgs(args):
    """ctrl.ride('vehicle'|'passenger', '$实体ID'|'#tag') → 基岩骑乘判定; 转不了返回 None。

    Java RideCheck: vehicle = 玩家骑的实体, passenger = 骑在玩家身上的第一个实体; $ 精确 ID,
    # 实体 tag(Forge tag 体系, 基岩无对应)。基岩只有 query.is_riding_any_entity_of_type
    (自己骑什么), 故只转换 vehicle + $ID(ID 经 JAVA_TO_BEDROCK_ENTITY_IDS 换算)。
    """
    if len(args) < 2:
        return None
    kind = _StripQuotes(args[0])
    pattern = _StripQuotes(args[1])
    if not kind or not pattern or kind.lower() != "vehicle":
        return None
    if pattern[0] != "$" or len(pattern) < 2:
        return None
    entityId = JAVA_TO_BEDROCK_ENTITY_IDS.get(pattern[1:], pattern[1:])
    return "query.is_riding_any_entity_of_type('{}')".format(entityId)


# ctrl 物品条件函数 → 附加门控(与 packParser._CONDITION_PREFIXES 的门控一致)
# ctrl.use 的门控要按槽位分手(主手/副手用的是不同表达式), 用哨兵占位, 见 _ItemConditionFromArgs
_USE_GATE_BY_HAND = "@use_gate_by_hand"
_CTRL_ITEM_FUNCS = {
    "hold": None,
    "armor": None,
    "swing": _SWING_ACTIVE_TEST,
    "use": _USE_GATE_BY_HAND,
}


# Java 结构体返回值的成员访问: `ysm.bone_rot('骨骼').x`(Java 词法在 ')' 之后把 '.' 认成成员访问,
# BoneRotation 返回 Vec3fStruct, 读别的骨骼**当前**旋转)。只置换调用会残留成 `0.0.x`, 引擎
# "非法字符"把整份动画文件拒载(2026-09-17 官方酒狐 03/06/10/15 四个包: 头发摆动链全靠它,
# 连带并行动画/主链动作全部查无) —— 置换时连成员访问一起吃掉
_MEMBER_ACCESS_TAIL = re.compile(r"(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)+")


def _ReplaceFunctionCalls(text, report):
    """剥离全部 ysm./ctrl./fn. 调用: 物品条件函数转等价 query, 其余按策略表。

    fn.* 是 Java 2.5.0 自定义函数(functions/*.molang 过程式脚本), 基岩表达式层
    无对应 —— 未拦截会以 unknown token 作废整份文件, 一律置零并告警。
    """
    strategies = dict(_FUNCTION_STRATEGIES)
    # tlm.* 是车万女仆联动绑定(CustomMolangParser 注册 ysm/ctrl/tlm/args/fn 五个前缀),
    # 基岩无对应, 与 fn.* 同样置零
    pattern = re.compile(r"\b(ysm|ctrl|fn|tlm)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    while True:
        match = pattern.search(text)
        if match is None:
            return text
        args, endIndex = _ScanCall(text, match.end())
        prefix, name = match.group(1), match.group(2)
        memberTail = _MEMBER_ACCESS_TAIL.match(text, endIndex + 1)
        replacement = None
        if prefix == "ctrl" and name in _CTRL_ITEM_FUNCS:
            replacement = _ItemConditionFromArgs(args, _CTRL_ITEM_FUNCS[name])
            if replacement is not None:
                report["map:ctrl.{}({}) -> 物品判定".format(name, args[1].strip())] += 1
            else:
                replacement = "0.0"
                report["zero:ctrl.{}({})(槽位/分类无法转换)".format(
                    name, ",".join(args))] += 1
        elif prefix == "ctrl" and name == "ride":
            replacement = _RideConditionFromArgs(args)
            if replacement is not None:
                report["map:ctrl.ride({}) -> 骑乘判定".format(args[1].strip())] += 1
            else:
                replacement = "0.0"
                report["zero:ctrl.ride({})(passenger/#tag 基岩无对应)".format(
                    ",".join(args))] += 1
        elif prefix in ("fn", "tlm"):
            replacement = "0.0"
            report["zero:{}.{}({})".format(
                prefix, name,
                "自定义函数脚本, 基岩无对应" if prefix == "fn" else "车万女仆联动, 基岩无对应")] += 1
        else:
            strategy = strategies.get(name)
            if strategy is None:
                replacement = "0.0"
                report["func:{}.{}(未知函数置零)".format(prefix, name)] += 1
            elif isinstance(strategy, int):
                replacement = args[strategy] if len(args) > strategy and args[strategy] else "0.0"
                report["func:{}.{}(取参数)".format(prefix, name)] += 1
            else:
                replacement = strategy
                report["func:{}.{}(置常量)".format(prefix, name)] += 1
        resumeIndex = endIndex + 1
        if memberTail is not None:
            # 结构体成员访问(见 _MEMBER_ACCESS_TAIL 注)连同调用一起置零
            resumeIndex = memberTail.end()
            replacement = "0.0"
            report["zero:{}.{}(...).成员(结构体成员访问, 基岩无对应, 连同调用一起置零)".format(
                prefix, name)] += 1
        text = text[:match.start()] + replacement + text[resumeIndex:]


# ---- Java 物理函数 second_order/first_order → 基岩 molang 状态变量积分 ----
# Java(SecondOrderFunction + PhysicsManager): 每个 '键' 一份二阶弹簧状态, 逐渲染帧
# 按真实帧间隔积分, 输出带过冲/回弹地跟随输入 —— 头发/尾巴/胸部/鞘翅随动的全部
# "Q 弹"手感来自它(t3ssel8r "Giving Personality to Procedural Animations")。旧策略
# "取输入参数"等于把弹簧换成刚性连杆, 一切随动都变成硬跟随。
# 基岩 molang 有实体级持久变量与逐帧求值, 足以把积分写成表达式前缀:
#   dt = clamp(q.life_time - t, 0, 0.1); t = q.life_time   (同帧重复求值 dt=0 → 恒等,
#                                                          多处引用同键与 Java 共享一份状态)
#   xd = dt>0 ? (x - x_prev)/dt : 0; x_prev = x
#   k1 = z/(πf), k2 = 1/(2πf)², k3 = rz/(2πf)              (f∈[0,5], z∈[0,1] 同 Java 钳位)
#   k2s = max(k2, dt²/2 + dt·k1/2, dt·k1)                   (半隐式稳定变体, 免 Java 的子步循环)
#   y += dt·yd;  yd += dt·(x + k3·xd - y - k1·yd)/k2s
# 调用处替换为状态变量 v.ysm_so_<键>_y, 积分语句提到该 molang 语句之前(纯表达式串
# 包成 "语句; return 表达式;" 复杂表达式)。状态变量经产物变量扫描自动初始化(值 0,
# 与 Java 新建物理对象 lastSimulation=0 一致)。
_PHYSICS_CALL_PATTERN = re.compile(r"\b(?:ysm\.)?(second_order|first_order)\s*\(")
_PHYSICS_SLUG_PATTERN = re.compile(r"[^a-z0-9_]+")
_PHYSICS_DT_MAX = "0.1"      # 帧间隔上限(秒): 卡顿/动画久未求值后不做巨步积分
_PHYSICS_PI = 3.14159265358979


def _SplitTopLevelStatements(text):
    """按顶层 ';' 切分 molang 语句(括号/引号内不算); 返回 (语句列表, 是否以 ; 结尾)"""
    parts, depth, quote, current = [], 0, None, []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth <= 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    tail = "".join(current)
    if tail.strip():
        parts.append(tail)
        return parts, False
    return parts, bool(parts)


def _MolangNumber(text):
    """字面量数值参数 → float; 非字面量返回 None"""
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def _FormatNumber(value):
    text = "{:.6g}".format(value)
    return text if ("." in text or "e" in text) else text + ".0"


class PhysicsRewriter(object):
    """一个移植包内的物理函数改写器: 键 → 状态变量槽位统一, 跨动画/控制器文件共享状态。"""

    def __init__(self):
        self._slugs = OrderedDict()   # 原键 → slug
        self._taken = set()
        self.report = Counter()

    def Slug(self, key):
        if key in self._slugs:
            return self._slugs[key]
        base = _PHYSICS_SLUG_PATTERN.sub("_", Pinyinize(key).lower()).strip("_") or "k"
        candidate, index = base, 2
        while candidate in self._taken:
            candidate = u"{}_{}".format(base, index)
            index += 1
        self._slugs[key] = candidate
        self._taken.add(candidate)
        return candidate

    @property
    def mapping(self):
        return list(self._slugs.items())

    def RewriteTree(self, node):
        """容器内全部字符串值就地改写; 返回改写的字符串数"""
        count = 0
        if isinstance(node, dict):
            for key in list(node.keys()):
                value = node[key]
                if isinstance(value, (dict, list)):
                    count += self.RewriteTree(value)
                elif isinstance(value, (str, unicode)):  # noqa: F821
                    newValue = self.RewriteText(value)
                    if newValue != value:
                        node[key] = newValue
                        count += 1
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, (dict, list)):
                    count += self.RewriteTree(value)
                elif isinstance(value, (str, unicode)):  # noqa: F821
                    newValue = self.RewriteText(value)
                    if newValue != value:
                        node[index] = newValue
                        count += 1
        return count

    def RewriteText(self, text):
        """一段 molang 文本(单表达式或分号语句序列)里的物理调用 → 状态变量 + 积分前缀"""
        if not _PHYSICS_CALL_PATTERN.search(text):
            return text
        statements, endsWithSemicolon = _SplitTopLevelStatements(text)
        pieces = []
        changed = False
        for statement in statements:
            prefix, rewritten = self._RewriteExpression(statement)
            if prefix:
                changed = True
                pieces.extend(prefix)
            pieces.append(rewritten)
        if not changed:
            return text
        single = len(statements) == 1 and not endsWithSemicolon
        if single and _AssignmentTargetOf(statements[0]) is None \
                and not _RETURN_PATTERN.search(statements[0]):
            # 纯表达式串: 前缀语句 + 显式 return, 求值结果仍是原表达式
            pieces[-1] = "return " + pieces[-1].strip()
            return ";".join(piece.strip() for piece in pieces) + ";"
        joined = ";".join(piece.strip() for piece in pieces)
        return joined + ";" if (endsWithSemicolon or single) else joined

    def _RewriteExpression(self, expression):
        """单条语句/表达式 → (积分前缀语句列表, 调用替换为状态变量后的文本)"""
        prefix = []
        cursor = 0
        while True:
            match = _PHYSICS_CALL_PATTERN.search(expression, cursor)
            if match is None:
                break
            args, endIndex = _ScanCall(expression, match.end())
            kind = match.group(1)
            key = _StripQuotes(args[0]) if args else None
            if endIndex >= len(expression) or not key or len(args) < 2 or not args[1].strip():
                # 键非字面量/参数残缺: 留给 _ReplaceFunctionCalls 按旧策略取输入并告警
                self.report[u"warn:ysm.{}(键非字面量或参数残缺, 退化为取输入)".format(kind)] += 1
                cursor = match.end()
                continue
            innerPrefix, inputExpr = self._RewriteExpression(args[1])   # 输入里嵌套的物理调用
            prefix.extend(innerPrefix)
            slug = self.Slug(key)
            if kind == "second_order":
                block, variable = self._SecondOrderBlock(slug, inputExpr, args[2:5])
            else:
                block, variable = self._FirstOrderBlock(slug, inputExpr, args[2:3])
            prefix.extend(block)
            self.report[u"func:ysm.{}(物理 → molang 状态积分)".format(kind)] += 1
            expression = expression[:match.start()] + variable + expression[endIndex + 1:]
            cursor = match.start() + len(variable)
        return prefix, expression

    def _SecondOrderBlock(self, slug, inputExpr, params):
        state = u"v.ysm_so_" + slug
        rawF = params[0] if len(params) > 0 and params[0].strip() else "1"
        rawZ = params[1] if len(params) > 1 and params[1].strip() else "1"
        rawR = params[2] if len(params) > 2 and params[2].strip() else "1"
        numF, numZ, numR = _MolangNumber(rawF), _MolangNumber(rawZ), _MolangNumber(rawR)
        lines = [
            u"v.ysm_so_dt=math.clamp(q.life_time-{s}_t,0,{m})".format(s=state, m=_PHYSICS_DT_MAX),
            u"{s}_t=q.life_time".format(s=state),
            u"v.ysm_so_in=({x})".format(x=inputExpr.strip()),
            u"v.ysm_so_xd=v.ysm_so_dt>0?(v.ysm_so_in-{s}_x)/v.ysm_so_dt:0".format(s=state),
            u"{s}_x=v.ysm_so_in".format(s=state),
        ]
        if numF is not None and numZ is not None and numR is not None:
            f = min(max(numF, 0.001), 5.0)          # Java Mth.clamp(f,0,5); f=0 在 Java 亦除零
            z = min(max(numZ, 0.0), 1.0)            # Java Mth.clamp(z,0,1)
            k1 = _FormatNumber(z / (_PHYSICS_PI * f))
            k2 = _FormatNumber(1.0 / ((2 * _PHYSICS_PI * f) ** 2))
            k3 = _FormatNumber(numR * z / (2 * _PHYSICS_PI * f))
        else:
            twoPi = _FormatNumber(2 * _PHYSICS_PI)
            lines += [
                u"v.ysm_so_f=math.clamp({f},0.001,5)".format(f=rawF.strip()),
                u"v.ysm_so_z=math.clamp({z},0,1)".format(z=rawZ.strip()),
                u"v.ysm_so_k1=v.ysm_so_z/({pi}*v.ysm_so_f)".format(pi=_FormatNumber(_PHYSICS_PI)),
                u"v.ysm_so_k2=1/(({tp}*v.ysm_so_f)*({tp}*v.ysm_so_f))".format(tp=twoPi),
                u"v.ysm_so_k3=({r})*v.ysm_so_z/({tp}*v.ysm_so_f)".format(r=rawR.strip(), tp=twoPi),
            ]
            k1, k2, k3 = "v.ysm_so_k1", "v.ysm_so_k2", "v.ysm_so_k3"
        lines += [
            u"v.ysm_so_k2s=math.max({k2},math.max(v.ysm_so_dt*v.ysm_so_dt*0.5"
            u"+v.ysm_so_dt*{k1}*0.5,v.ysm_so_dt*{k1}))".format(k1=k1, k2=k2),
            u"{s}_y={s}_y+v.ysm_so_dt*{s}_yd".format(s=state),
            u"{s}_yd={s}_yd+v.ysm_so_dt*(v.ysm_so_in+{k3}*v.ysm_so_xd-{s}_y-{k1}*{s}_yd)"
            u"/v.ysm_so_k2s".format(s=state, k1=k1, k3=k3),
        ]
        return lines, u"{s}_y".format(s=state)

    def _FirstOrderBlock(self, slug, inputExpr, params):
        state = u"v.ysm_fo_" + slug
        rawR = params[0] if params and params[0].strip() else "1"
        lines = [
            u"v.ysm_so_dt=math.clamp(q.life_time-{s}_t,0,{m})".format(s=state, m=_PHYSICS_DT_MAX),
            u"{s}_t=q.life_time".format(s=state),
            # Java: y = (1 - dt/r)·y + (dt/r)·x; 系数钳到 [0,1] 免 dt>r 时反向发散
            u"v.ysm_so_a=math.clamp(v.ysm_so_dt/math.max({r},0.0001),0,1)".format(r=rawR.strip()),
            u"{s}_y={s}_y+v.ysm_so_a*(({x})-{s}_y)".format(s=state, x=inputExpr.strip()),
        ]
        return lines, u"{s}_y".format(s=state)


# Java MoLang 的空值合并 v.x??默认值 —— 语义是"未定义时取默认值"。基岩虽支持 ??,
# 但主包会把扫描到的所有 v.* 自动初始化(值为 0), fallback 因而永不触发、Java 的
# 非零默认值会丢 —— 故改写为变量本身, 默认值收集进 netease.initialize 保住语义。
_NULLISH_PATTERN = re.compile(
    r"\b((?:v|variable)\.[A-Za-z_][A-Za-z0-9_.]*)\s*\?\?\s*(-?\d+(?:\.\d+)?)")

_QUERY_TOKEN = re.compile(r"\b(?:query|q)\.([A-Za-z_][A-Za-z0-9_]*)")
_MATH_TOKEN = re.compile(r"\bmath\.([A-Za-z_][A-Za-z0-9_]*)")
# Java CustomMolangParser 注册的五个专有前缀 ysm/ctrl/fn/tlm/args 一个都不能漏:
# tlm.*(车万女仆联动值, builtin 13_matured 48 处)、args.*(自定义函数参数)漏过去就是
# unknown token, 整份文件作废
_RESIDUAL_JAVA_TOKEN = re.compile(r"\b(?:ysm|ctrl|fn|tlm|args)\.[A-Za-z_][A-Za-z0-9_.]*")
_ROAMING_PATTERN = re.compile(r"\b(v|variable)\.roaming\.([A-Za-z_][A-Za-z0-9_]*)")
# 残缺前缀: 点后没有标识符的 ysm./ctrl./fn.(作者手误或剪贴残片, 实例: 凋灵娘控制器里的
# "q.all_animations_finishedctrl.")。上面的正则要求点后有标识符, 认不出它, 原样落盘后
# 引擎报 unrecognized token, 该转移条件所在的整个控制器作废。不加 \b: 残片常粘在前一个
# 标识符尾巴上; 点后有标识符的(v.myctrl.x 之类)不受影响。
_DANGLING_JAVA_PREFIX = re.compile(r"(?:ysm|ctrl|fn|tlm|args)\.(?![A-Za-z_])")
# Java 侧"声明默认值"语句 `v.x ?? v.x = 0;`(② 的 ?? 降级会把它变成 `v.x || v.x = 0;`):
# 基岩不允许表达式内出现赋值("assignment to non-variable not allowed", 实机 2026-09-03
# ref_sahmet parallel0 的 timeline 整条作废并刷错), 等价改写为 `v.x = v.x ?? (0);`。
_DECLARE_DEFAULT_PATTERN = re.compile(
    r"\b((?:v|variable)\.[A-Za-z_][A-Za-z0-9_.]*)\s*(?:\?\?|\|\|)\s*\(?\s*\1\s*=(?!=)\s*"
    r"([^;()]+?)\s*\)?\s*;")


def RewriteDeclareDefaultStatements(text):
    """`v.x ?? v.x = 0;` / `v.x || v.x = 0;` → `v.x=v.x??(0);`; 返回 (文本, 改写数)"""
    return _DECLARE_DEFAULT_PATTERN.subn(r"\1=\1??(\2);", text)


def NeutralizeDanglingJavaPrefixes(text):
    """删掉残缺的 ysm./ctrl./fn. 前缀(见 _DANGLING_JAVA_PREFIX); 返回 (文本, 删除数)"""
    return _DANGLING_JAVA_PREFIX.subn("", text)

# —— math 函数改写(2026-09-17 按 Java geckolib3 MathBinding 逐函数核对源码):
#    ① 网易引擎二进制缺失的 geckolib/文档函数展开(die_roll 系无法等价, 走告警);
#    ② **同名不同义**: Java 的 acos/asin/atan/atan2 直接返回 Math.acos / Mth.atan2 的**弧度**
#       (只有 sin/cos 的入参按角度换算), 基岩全套三角函数按角度 → 结果乘 π/180 换回 Java 口径;
#       hermite_blend 的 Java 实现是先 ceil 再算 floor(3c²-2c³) —— 对整数 c 是 0/1/-4… 的阶跃, 不是
#       平滑曲线, 按 Java 原样复刻(移植目标是"和 Java 里看到的一样", 早先展开成标准 hermite 不符);
#    ③ 别名与常量: Java 注册了 randomi/roll/rolli/hermite 四个短名; math.e 基岩没有(只有 pi);
#       math.random 的第 3 参 Java 容忍并忽略, 基岩按参数个数拒载整份文件 → 截成 2 参。
#    展开式重复引用参数, molang 无副作用场景安全(random 参数除外, 罕见)。
_MATH_REWRITE_NAMES = re.compile(
    r"\bmath\.(random_integer|randomi|lerprotatee?|min_angle|hermite(?:_blend)?"
    r"|acos|asin|atan2?|rolli?|random|round)\s*\(")
_MATH_E_CONSTANT = re.compile(r"\bmath\.e\b(?!\s*\()")
_JAVA_DEG_TO_RAD = "0.017453292519943295"     # Java 反三角返回弧度, 基岩返回角度
_JAVA_MATH_E = "2.718281828459045"


def _RewriteMathCalls(text, report):
    count = len(_MATH_E_CONSTANT.findall(text))
    if count:
        report["map:math.e -> 常量 e(基岩只有 math.pi)"] += count
        text = _MATH_E_CONSTANT.sub(_JAVA_MATH_E, text)
    cursor = 0
    while True:
        match = _MATH_REWRITE_NAMES.search(text, cursor)
        if match is None:
            return text
        name = match.group(1)
        args, endIndex = _ScanCall(text, match.end())
        replacement = None
        label = "展开式"
        if name in ("random_integer", "randomi") and len(args) >= 2:
            # Java RandomInteger: min + nextInt(max-min) → [min, max) 上界不含且各整数等概率;
            # floor(random) 同分布(round 会让两端概率减半、且含上界)
            replacement = "math.floor(math.random({},{}))".format(args[0], args[1])
        elif name == "random":
            if len(args) <= 2:
                cursor = endIndex + 1          # 2 参就是基岩形态, 原样保留
                continue
            replacement = "math.random({},{})".format(args[0], args[1])   # Java 容忍并忽略第 3 参
            label = "截成 2 参(基岩按参数个数拒载整份文件)"
        elif name in ("lerprotate", "lerprotatee") and len(args) >= 3:
            # 最短角插值: a + min_angle(b-a)*t
            replacement = ("(({a})+(math.mod(math.mod(({b})-({a}),360)+540,360)-180)"
                           "*({t}))").format(a=args[0], b=args[1], t=args[2])
        elif name == "min_angle" and len(args) >= 1:
            replacement = "(math.mod(math.mod(({}),360)+540,360)-180)".format(args[0])
        elif name in ("hermite", "hermite_blend") and len(args) >= 1:
            # Java HermitBlend: c = ceil(t); floor(3c²-2c³) —— 对整数 c 就是 (3-2c)c², 阶跃而非平滑
            replacement = "((3-2*math.ceil({t}))*math.ceil({t})*math.ceil({t}))".format(t=args[0])
            label = "Java 口径展开(ceil 阶跃)"
        elif name in ("acos", "asin", "atan") and len(args) >= 1:
            replacement = "(math.{}({})*{})".format(name, args[0], _JAVA_DEG_TO_RAD)
            label = "角度 -> 弧度(Java 反三角返回弧度)"
        elif name == "atan2" and len(args) >= 2:
            replacement = "(math.atan2({},{})*{})".format(args[0], args[1], _JAVA_DEG_TO_RAD)
            label = "角度 -> 弧度(Java 反三角返回弧度)"
        elif name == "round" and len(args) >= 1:
            # Java Math.round 半数向上(-2.5 → -2), 基岩 math.round 半数远离零(-2.5 → -3, 2026-09-17 实机)
            replacement = "math.floor(({})+0.5)".format(args[0])
            label = "Java 口径(半数向上)"
        elif name in ("roll", "rolli") and len(args) >= 3:
            replacement = "math.{}({})".format(
                "die_roll" if name == "roll" else "die_roll_integer", ",".join(args))
            label = "Java 别名 -> die_roll 系(引擎存在性另查)"
        if replacement is None:
            report["warn:math.{}(参数不足, 保留原样)".format(name)] += 1
            cursor = match.end()
            continue
        report["map:math.{} -> {}".format(name, label)] += 1
        text = text[:match.start()] + replacement + text[endIndex + 1:]
        cursor = match.start() + len(replacement)


# —— 槽位参数改写: Java 版 query.is_item_name_any('mainhand',...) 等用短槽名,
#    基岩要求 'slot.weapon.mainhand' 全称 —— 同名 query 参数形态不同, 不改会恒 0。
_SLOT_PARAM_FUNCS = re.compile(
    r"\b(?:query|q)\.(is_item_name_any|equipped_item_any_tag|equipped_item_all_tags|"
    r"max_durability|remaining_durability)\s*\(")


def _RewriteSlotParams(text, report):
    cursor = 0
    while True:
        match = _SLOT_PARAM_FUNCS.search(text, cursor)
        if match is None:
            return text
        args, endIndex = _ScanCall(text, match.end())
        slotName = _StripQuotes(args[0]) if args else None
        mapped = _JAVA_SLOT_NAMES.get(slotName.lower()) if slotName else None
        if mapped is None:
            cursor = endIndex + 1   # 已是全称或非字面量, 原样保留
            continue
        args[0] = "'{}'".format(mapped[0])
        replacement = "query.{}({})".format(match.group(1), ",".join(args))
        report["map:query.{} 槽位短名 -> {}".format(match.group(1), mapped[0])] += 1
        text = text[:match.start()] + replacement + text[endIndex + 1:]
        cursor = match.start() + len(replacement)


# ---- 无 else 三元 `cond ? value` → `cond ? value : 0` ----
# Java geckolib 的 molang 允许省略 else 分支(缺省 0), Java 包里到处是这种写法
# (`ysm.head_yaw>0 ? -ysm.head_yaw`)。基岩的**动画通道**表达式编译器对此不可靠:
# 实测(2026-09-05 warden 拉弓, 强制播放后目视)整个条件项退化成 0 —— UpBody 的
# `-75+(条件?补偿)` 只剩 -75, 身体侧身 75° 却没有按 head_yaw 回正, 手臂/弓因此
# 甩到视线左侧 60 多度; 而同一串喂给 QueryVariable.EvalMolangExpression 却能
# 正确算出 -25(两套求值器不同)。补上显式 `: 0` 语义完全等价, 认与不认都安全。
# 不动的两种形态: `??`(空值合并)、块形态 `cond ? { ... }`(补 `: 0` 反而变非法)。
_TERNARY_TERMINATORS = ",);"


def NormalizeBareTernary(text):
    """`cond ? value` 补成 `cond ? value : 0`; 返回 (新串, 补全处数)。

    带括号深度与字符串字面量识别: `'slot.armor.head'` 里的冒号不算 else 分支,
    `f(a?b, c)` 的逗号与 `)` / `;` 都终结一个未闭合的三元。
    """
    if "?" not in text:
        return text, 0
    inserts = []
    pending = []          # [(括号深度, 是否块形态)...]
    depth = 0
    quote = None
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            while pending and pending[-1][0] >= depth:
                item = pending.pop()
                if not item[1]:
                    inserts.append(index)
            depth = max(depth - 1, 0)
        elif char in _TERNARY_TERMINATORS:      # , 与 ; 终结当前深度的未闭合三元
            while pending and pending[-1][0] >= depth:
                item = pending.pop()
                if not item[1]:
                    inserts.append(index)
        elif char == "?":
            if index + 1 < length and text[index + 1] == "?":
                index += 2                       # ?? 空值合并, 跳过
                continue
            rest = index + 1
            while rest < length and text[rest].isspace():
                rest += 1
            isBlock = rest < length and text[rest] == "{"
            pending.append((depth, isBlock))
        elif char == ":":
            if pending and pending[-1][0] == depth:
                pending.pop()                    # 与最近的同深度 ? 配对
        index += 1
    tailCount = len([item for item in pending if not item[1]])
    if not inserts and not tailCount:
        return text, 0
    for position in sorted(inserts, reverse=True):
        text = text[:position] + " : 0" + text[position:]
    text += " : 0" * tailCount
    return text, len(inserts) + tailCount


# ---- Java 词法差异(com.elfmcys.ysm.molang.lexer / parser) ----
# - 词法分析器把 true/false 当关键字(TRUE/FALSE token = 1/0); 基岩是否认这两个字面量没有
#   证据(原版资源零使用), 统一换成 1.0/0.0;
# - 解析器 parseCompound 的 LPAREN 分支: 非调用表达式后面紧跟 `(` 是**隐式乘法**
#   (`(a)(b)` / `2(b)`, builtin 14_momo 控制器有一处); 基岩当成非法调用, 补显式 `*`。
# 两条都只动 JSON 字符串内、molang 单引号字符串之外的文本(JSON 自己的 true/false 布尔
# 值在字符串之外, 不受影响)。
_JSON_STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')
_MOLANG_QUOTED_SPLIT = re.compile(r"('[^']*')")
_BOOLEAN_KEYWORD = re.compile(r"\b(true|false)\b")
_IMPLICIT_MUL_AFTER_PAREN = re.compile(r"\)\s*\(")
_IMPLICIT_MUL_AFTER_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*\(")


def _MapMolangTexts(text, func):
    """JSON 文本里每个字符串字面量(去引号)套 func; 文本里没有 JSON 引号时整串视为 molang"""
    if '"' not in text:
        return func(text)

    def _Apply(match):
        literal = match.group(0)
        inner = literal[1:-1]
        if not inner:
            return literal
        newInner = func(inner)
        return literal if newInner == inner else '"' + newInner + '"'
    return _JSON_STRING_LITERAL.sub(_Apply, text)


def _OutsideMolangStrings(text, func):
    """molang 单引号字符串字面量之外的片段套 func"""
    parts = _MOLANG_QUOTED_SPLIT.split(text)
    return "".join(part if index % 2 == 1 else func(part)
                   for index, part in enumerate(parts))


def _MapJsonValueStrings(text, func):
    """同 _MapMolangTexts, 但只动 JSON **值**字符串(后面紧跟 ':' 的是键, 原样)"""
    if '"' not in text:
        return func(text)

    def _Apply(match):
        cursor = match.end()
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        literal = match.group(0)
        inner = literal[1:-1]
        if (cursor < len(text) and text[cursor] == ":") or not inner:
            return literal
        newInner = func(inner)
        return literal if newInner == inner else '"' + newInner + '"'
    return _JSON_STRING_LITERAL.sub(_Apply, text)


_JAVA_CASE_FOLD_MEMBER = re.compile(
    r"\b(?:ysm|ctrl|fn|tlm|args|query|q|math)\.[A-Za-z_][A-Za-z0-9_]*", re.IGNORECASE)
_JAVA_CASE_FOLD_PREFIX = re.compile(
    r"\b(?:variable|v|temp|t|context|c)\.(?:roaming\.)?(?=[A-Za-z_])", re.IGNORECASE)


def FoldJavaMolangCase(text):
    """Java 词法大小写归一; 返回 (新文本, 改写数)。

    Java MolangLexerImpl 把标识符整体 toLowerCase(`YSM.head_yaw` ≡ `ysm.head_yaw`), 本工具的映射表
    只认小写 —— 实例: 官方酒狐 03 宇航员 / 20 幸存者的 extra 动画 `YSM.head_yaw` 漏过全部映射原样落盘,
    引擎"未知命名空间"把整份文件拒载(2026-09-17 实机探针)。基岩的命名空间、查询名、变量名本身都不分
    大小写(引擎实测 `Variable.CaseProbe = 4; return v.CASEPROBE;` 得 4、`Query.Position(0)` 可用), 所以只
    归一**要查表的部分**: Java 专有前缀与 query/math 连同成员名; 变量类命名空间只到前缀(含 roaming 域段),
    变量成员名保持原样(免得同一变量在初始化表里出现两种写法)。只动 JSON 值(键是骨骼名 / 状态名),
    molang 单引号字符串原样。
    """
    counter = [0]

    def _Lower(match):
        token = match.group(0)
        lowered = token.lower()
        if lowered != token:
            counter[0] += 1
        return lowered

    def _Fold(fragment):
        fragment = _JAVA_CASE_FOLD_MEMBER.sub(_Lower, fragment)
        return _JAVA_CASE_FOLD_PREFIX.sub(_Lower, fragment)

    rewritten = _MapJsonValueStrings(text, lambda piece: _OutsideMolangStrings(piece, _Fold))
    return rewritten, counter[0]


def RewriteJavaLexicalForms(text):
    """true/false 关键字 → 1.0/0.0, 隐式乘法 → 显式 `*`; 返回 (新文本, 关键字数, 乘法数)"""
    counters = [0, 0]

    def _Rewrite(fragment):
        def _Bool(match):
            counters[0] += 1
            return "1.0" if match.group(1) == "true" else "0.0"
        fragment = _BOOLEAN_KEYWORD.sub(_Bool, fragment)
        fragment, parenCount = _IMPLICIT_MUL_AFTER_PAREN.subn(")*(", fragment)
        fragment, numberCount = _IMPLICIT_MUL_AFTER_NUMBER.subn(r"\1*(", fragment)
        counters[1] += parenCount + numberCount
        return fragment

    rewritten = _MapMolangTexts(text, lambda piece: _OutsideMolangStrings(piece, _Rewrite))
    return rewritten, counters[0], counters[1]


def PortMolangText(text, defaults=None, report=None):
    """动画/控制器文本里的 Java 专有 molang → 基岩可解析形态(批量替换 + 引擎门)。

    defaults 传入 dict 时收集 ?? 的默认值(变量名 → 值)供写入初始化表;
    report 传入 Counter 时累计各类替换/降级次数供末尾汇总。
    """
    if report is None:
        report = Counter()
    # ⓪ Java 词法大小写归一(见 FoldJavaMolangCase 注): 排在一切映射之前 —— 映射表只认小写
    text, foldCount = FoldJavaMolangCase(text)
    if foldCount:
        report["map:Java 词法大小写归一(YSM./Query./Math./V. 等 -> 小写)"] += foldCount
    # ① v.roaming.<名> 持久化域(Java 侧存档+网络同步的 Struct)扁平化为普通变量
    # v.roaming_<名>: 基岩无自定义 struct, 嵌套名求值恒失败; 扁平化后由主包变量
    # 扫描自动初始化, 丢持久化保功能(builtin 68 个文件 547 处在用)。
    # 排在 ② 之前: ?? 默认值按扁平化后的名字收集
    count = len(_ROAMING_PATTERN.findall(text))
    if count:
        report["map:v.roaming.* -> v.roaming_*(持久化域扁平化)"] += count
        text = _ROAMING_PATTERN.sub(r"\1.roaming_\2", text)

    # ② ?? 默认值收敛进初始化表。**只处理作者原文**, 所以排在 ②.5 函数调用剥离之前:
    #    ctrl.swing/ctrl.use 的替换体是主包运行时门控(packParser._JAVA_ATTACK_TIME /
    #    _BLOCKING_SIGNAL / _USE_DURATION_SIGNAL), 自带 `variable.ysm_*??回落`。排在后面时
    #    它们被剥成裸变量和 `||`, 与 ysm.attack_time 名字映射(③, 保留 ??)落成两种形态,
    #    修复工具按现行形态迁移时就不幂等(2026-09-17 转换器单元用例逮到)
    def _StripNullish(match):
        name, fallback = match.group(1), match.group(2)
        if defaults is not None:
            defaults[name] = fallback
        return name

    text = _NULLISH_PATTERN.sub(_StripNullish, text)
    if "??" in text:
        text = text.replace("??", "||")  # 右侧非字面量的兜底: 语义近似(0 时取右值)
    # ②' 声明默认值语句 v.x || v.x = 0;(上一步降级后的形态) → v.x = v.x ?? (0);
    #    表达式内赋值基岩直接拒绝(整条 timeline 作废), ?? 引擎实测可用(变量初始化控制器同款)
    text, declareCount = RewriteDeclareDefaultStatements(text)
    if declareCount:
        report["map:v.x ?? v.x = 默认值(声明语句) -> v.x = v.x ?? (默认值)"] += declareCount

    # ②.5 函数调用剥离(先于 ③: 函数名会被名字替换弄坏; 后于 ②: 见 ② 注)
    text = _ReplaceFunctionCalls(text, report)

    # ③ 名字映射(ysm./query./q. 三前缀一体处理; ctrl. 单独)
    for name, replacement in _JAVA_NAME_MAP:
        pattern = re.compile(r"\b(?:query|q|ysm)\.{}\b".format(re.escape(name)))
        count = len(pattern.findall(text))
        if count:
            report["map:{} -> {}".format(name, replacement)] += count
            text = pattern.sub(replacement, text)
    for name, replacement in _CTRL_NAME_MAP:
        pattern = re.compile(r"\bctrl\.{}\b".format(re.escape(name)))
        count = len(pattern.findall(text))
        if count:
            report["map:ctrl.{} -> {}".format(name, replacement)] += count
            text = pattern.sub(replacement, text)

    # ③.5 槽位参数形态改写(Java 短槽名 → 基岩全称; 同名 query 参数不兼容)
    text = _RewriteSlotParams(text, report)

    # ③.6 Java 词法差异: true/false 关键字 → 1.0/0.0; `)(` / `2(` 隐式乘法 → 显式 `*`
    #     (只动 JSON 字符串内、molang 单引号字符串之外的文本, 见 RewriteJavaLexicalForms)
    text, boolCount, mulCount = RewriteJavaLexicalForms(text)
    if boolCount:
        report["map:true/false 关键字 -> 1.0/0.0"] += boolCount
    if mulCount:
        report["map:隐式乘法 )( / N( -> 显式 *"] += mulCount

    # ④ 残余 ysm./ctrl./fn./tlm./args. 一律置零(unknown token = 整份文件作废, 宁可缺细节)
    def _NeutralizeResidual(match):
        report["zero:{}(无映射)".format(match.group(0))] += 1
        return "0.0"

    text = _RESIDUAL_JAVA_TOKEN.sub(_NeutralizeResidual, text)
    text, danglingCount = NeutralizeDanglingJavaPrefixes(text)
    if danglingCount:
        report["zero:残缺前缀 ysm./ctrl./fn.(点后无标识符, 直接删除)"] += danglingCount

    # ⑤ 引擎存在性门(证据分级, 不臆断): 二进制表确认缺失的才置零; 证据缺失的
    #    **保留原样**并显著告警, 交给实机探针裁决 —— 原版没使用不能判为不存在。
    def _GateQuery(match):
        name = match.group(1).lower()
        if name in _ENGINE_QUERIES:
            return match.group(0)
        if name in _BINARY_ABSENT_QUERIES:
            report["zero:query.{}(引擎二进制无此名)".format(name)] += 1
            return "0.0"
        kind = "文档有" if name in _DOC_QUERIES else "文档亦无"
        report["warn:query.{}({}, 证据缺失已保留 — 若文件作废先实机探针此名)".format(
            name, kind)] += 1
        return match.group(0)

    text = _QUERY_TOKEN.sub(_GateQuery, text)
    # math: 引擎缺失的已知函数先展开改写(random_integer/lerprotate/hermite/min_angle),
    # 其余证据缺失者只告警不改写(置零会毁掉调用语法; 二进制表只作正面证据)
    text = _RewriteMathCalls(text, report)
    for name in set(_MATH_TOKEN.findall(text)):
        if name.lower() not in _ENGINE_MATHS:
            report["warn:math.{}(证据缺失, 已保留 — 需人工确认)".format(name)] += 1
    # ⑥ 无 else 三元补全(见 NormalizeBareTernary 长注)。**逐 JSON 字符串**处理: 整份 JSON 文本
    #    直接喂进去时, JSON 的双引号被当成 molang 字符串定界符, 引号之间的三元全部被跳过
    #    (2026-09-16 幂等性守护逮到: 移植产物缺 `: 0`, 全靠修复工具逐串补跑才齐)
    counter = [0]

    def _Ternary(piece):
        piece, count = NormalizeBareTernary(piece)
        counter[0] += count
        return piece

    text = _MapMolangTexts(text, _Ternary)
    if counter[0]:
        report["map:a ? b(无 else) -> a ? b : 0"] += counter[0]
    return text


_VECTOR_CHANNELS = ("position", "rotation", "scale")

# molang 赋值(v.x=expr)在基岩必须是带分号的完整语句, 否则整份动画文件
# node parse failed。Java 侧两种写法都有: arm.animation.json 带分号(能加载),
# main.animation.json 不带(实测让 46 条主体动画全丢 —— 模型完全僵直)。
# 补分号后表达式返回 0, 而这些赋值都写在 YSM 约定的 molang 假骨骼上(专门承载
# 副作用求值, 旋转值本就无意义), 与 Java 官方 arm 的写法一致。
_ASSIGNMENT_PATTERN = re.compile(r"(?:v|variable)\.[A-Za-z_][A-Za-z0-9_.]*\s*=(?!=)")


def _NormalizeMolangStatement(value):
    """含赋值的 molang 字符串补足结尾分号; 其他值原样返回"""
    if not isinstance(value, (str, unicode)):  # noqa: F821
        return value, False
    if not _ASSIGNMENT_PATTERN.search(value) or value.rstrip().endswith(";"):
        return value, False
    return value.rstrip() + ";", True


# ---- 复杂表达式(含 ;)的返回值语义 ----
# 微软 Molang 规范: 表达式含 ";" 即为"复杂表达式", **没有 return 就返回 0**; Java 版
# molang 返回最后一条语句/赋值的值。Java 包里两类写法到基岩上全部变成 0:
#   ① 通道值写成赋值 "v.player_size=v.player_size;" —— 实机: Root 缩放 0, 模型整体
#      不可见(sahmet/wither 玩家身上与 GUI 都不显示的真因, warden 写的是纯表达式故正常);
#   ② 纯表达式带尾分号 "(v.qh==1?(0))+...;" —— 官方 default 的 arm 文件 111 处,
#      手臂姿态全部归零。
# 规范化: 单条纯表达式去尾分号; 语句序列末尾补 return(赋值取左值, 表达式取自身)。
_STATEMENT_TARGET_PATTERN = re.compile(
    r"^\s*((?:variable|v|temp|t)\.[A-Za-z_][A-Za-z0-9_.]*)\s*=(?!=)")
_RETURN_PATTERN = re.compile(r"(^|[^A-Za-z0-9_.])return([^A-Za-z0-9_]|$)")


def _AssignmentTargetOf(statement):
    """语句是赋值(单 =, 非 ==)则返回左值, 否则 None"""
    matched = _STATEMENT_TARGET_PATTERN.match(statement)
    return matched.group(1) if matched else None


def _NormalizeComplexExpression(value):
    """含 ; 的通道表达式 → 基岩求值结果与 Java 一致; 返回 (新值, 是否改动)"""
    if not isinstance(value, (str, unicode)) or ";" not in value:  # noqa: F821
        return value, False
    if _RETURN_PATTERN.search(value):
        return value, False
    statements = [s.strip() for s in value.split(";") if s.strip()]
    if not statements:
        return value, False
    last = statements[-1]
    target = _AssignmentTargetOf(last)
    if len(statements) == 1 and target is None:
        if not _ASSIGNMENT_PATTERN.search(last):
            return last, True                               # 纯表达式带尾分号 → 去分号
        # 括号 / 三元分支里带赋值(官方酒狐 17 号飞机 `q.is_on_ground?(v.x=math.round(...)):v.x;`): 引擎按
        # "含 '=' 或 ';'"判复杂表达式, 去掉分号整份文件拒载(2026-09-18 日志)。补 return 保住 Java 的"返回最后
        # 一条语句的值", 实测 `return 1 ? (v.x = 3) : v.x;` 得 3 且赋值生效、假分支不执行赋值
        return "return " + last + ";", True
    if target is not None:
        statements.append("return " + target)               # 赋值序列 → 返回左值
    else:
        statements[-1] = "return " + last                   # 末尾表达式 → 显式 return
    return ";".join(statements) + ";", True


# ---- Java 分层覆盖语义 → 基岩 override_previous_animation ----
# Java(PlayerControllerCollection.init 注册顺序): pre_parallel → vehicle → main → hold →
# swing → use → carry_on → cap → parallel → armor, 同骨骼**后者覆盖前者**(GeckoLib set),
# 仅 parallel 通道旋转做加法。基岩(微软《Animations Overview》)默认"channels are added
# separately across animations" —— 逐通道相加。实机: idle 与 pre_parallel 同骨骼相加
# 成两倍(sahmet 尾巴过卷贴腿), 手持条件动画与 idle 手臂相加。
# 复刻: 非 parallel 动画一律加 override_previous_animation(只重置本动画涉及的骨骼
# 再应用 —— 原版 swim/swim.legs/sleeping 同法), parallel 不加(旋转相加正是 Java 语义);
# animate 表按 Java 通道顺序排列由主包 packParser._OrderJavaAnimates 负责。
_ADDITIVE_PARALLEL_PATTERN = re.compile(r"^parallel\d+$")
# parallel 通道控制器状态里播的动画(如 sword_idle)同样不加 override 标志 —— 但**不是**因为旋转相加:
# Java 只有内置并行通道(CodedAnimationController)旋转相加, 作者控制器接管后 blendRotation 恒 false
# (IAnimationController.blendRotation 注: "如果使用动画控制器，那么将永远返回 false"), 位移/旋转/
# 缩放全是覆盖。不加标志是因为它按**骨骼整体**清空(连同该动画没写的通道)且吃掉状态淡化;
# 逐 (骨骼, 通道) 的覆盖由 ApplyChannelOwnership 复刻。(2026-09-16 前此处注释误作"旋转相加")
_PARALLEL_CHANNEL_CONTROLLER_PATTERN = re.compile(r"^player\.parallel_\d+$")


def _ShouldOverridePrevious(shortKey, additiveKeys=None, preKeys=None):
    """该动画是否带 override_previous_animation(基岩: 应用本动画前先重置骨骼)。

    策略(2026-09-03 定稿): **pre / main / parallel 三个域一律不带**, 只给语义上确实要
    独占骨骼的动画:
    - hold/swing/use/armor 等跨通道条件动画(Java 的通道覆盖语义: 手部动作压住主链手臂);
    - 轮盘 extra 动画(经 /playanimation 播在最上层)。
    不带的:
    - parallel0-7(内置并行通道旋转相加, Java 语义)与 parallel 通道控制器引用的动画(additiveKeys:
      Java 里是逐通道覆盖, 由 ApplyChannelOwnership 用伴生动画复刻, 标志按骨骼整体清空反而过头);
    - 伴生动画 <键>__own<N> 跟随原动画的策略(ApplyChannelOwnership 从原动画复制字段, 这里只是
      兜底口径一致): 主链/pre/parallel 的伴生靠权重让出通道不带标志, 条件动画的伴生保留标志;
    - **主链成员**(JavaStateLoopType 判定) —— 由 ysm_state 状态机驱动; 淡化期引擎同时
      应用出态与入态, 入态带 override 会抹掉出态的贡献(观感硬切+弹一下, 实机多轮核实);
    - **pre 通道**: 裸 pre_parallelN, 以及 player_pre_parallel_*/pre_main/vehicle 控制器
      状态引用的动画(preKeys: jump_up/jump_fall 之类) —— 与主链同处淡化链路, 同上。
    Java 的"main 逐通道覆盖 pre"改由 ApplyChannelOwnership 的伴生动画按状态让位, 不依赖该标志在
    animate 条目之间的确切语义 —— 那一点至今**没有可靠实机结论**: 早先"占住骨骼、挡住
    主链"的判断建立在一次整份动画文件被引擎拒载(空 bones 节点)的观察之上, 已作废,
    见 SanitizeAnimationBody 注。
    """
    if _ADDITIVE_PARALLEL_PATTERN.match(shortKey):
        return False
    if _PARALLEL_SHORT_PATTERN.match(shortKey):     # pre_parallelN
        return False
    matched = _OWNERSHIP_COMPANION_PATTERN.match(shortKey)
    if matched:
        # 通道覆盖伴生动画(ApplyChannelOwnership)与原动画同宿主、同条件播放, 等价原动画的一部分
        return _ShouldOverridePrevious(matched.group(1), additiveKeys, preKeys)
    if JavaStateLoopType(shortKey) is not None:
        return False
    if preKeys and shortKey in preKeys:
        return False
    return not (additiveKeys and shortKey in additiveKeys)


def CollectParallelChannelAnimKeys(controllerData, nameMapper=None):
    """控制器数据(原始 Java 名或已改名) → parallel 通道控制器状态引用的动画短键集。

    接受 Java 原名(player.parallel_N)与移植后注册名(controller.animation.<ns>.
    player_parallel_N)两种形态; 引用名经 nameMapper.Convert 与动画侧同一套改名。
    """
    keys = set()
    controllers = (controllerData or {}).get("animation_controllers") or {}
    for rawName, body in controllers.items():
        last = rawName.split(".")[-1]
        isParallelChannel = (_PARALLEL_CHANNEL_CONTROLLER_PATTERN.match(rawName)
                             or re.match(r"^player_parallel_\d+$", last))
        if not isParallelChannel or not isinstance(body, dict):
            continue
        for state in (body.get("states") or {}).values():
            if not isinstance(state, dict):
                continue
            for item in state.get("animations") or []:
                refs = item.keys() if isinstance(item, dict) else [item]
                for ref in refs:
                    if not isinstance(ref, (str, unicode)):  # noqa: F821
                        continue
                    keys.add(nameMapper.Convert(ref) if nameMapper is not None
                             else EscapeConditionKey(ref))
    return keys


# pre 通道控制器的 Java 原名(player.pre_parallel_N / player.pre_main* / player.vehicle*)
_PRE_CHANNEL_CONTROLLER_PATTERN = re.compile(r"^player\.(?:pre_parallel_\d+|pre_main|vehicle)")


def CollectPreChannelAnimKeys(controllerData, nameMapper=None):
    """控制器数据(Java 原名或已改名) → pre 通道控制器(player_pre_parallel_*/pre_main/
    vehicle)状态引用的动画短键集 —— 这些动画排在主链之前、与主链同处淡化链路, 与裸
    pre_parallelN 一样不带 override(见 _ShouldOverridePrevious)。"""
    keys = set()
    controllers = (controllerData or {}).get("animation_controllers") or {}
    for rawName, body in controllers.items():
        last = str(rawName).split(".")[-1]
        isPreChannel = (_PRE_CHANNEL_CONTROLLER_PATTERN.match(rawName)
                        or last.startswith(_PRE_CHANNEL_CONTROLLER_PREFIXES))
        if not isPreChannel or not isinstance(body, dict):
            continue
        for state in (body.get("states") or {}).values():
            if not isinstance(state, dict):
                continue
            for item in state.get("animations") or []:
                refs = item.keys() if isinstance(item, dict) else [item]
                for ref in refs:
                    if not isinstance(ref, (str, unicode)):  # noqa: F821
                        continue
                    keys.add(nameMapper.Convert(ref) if nameMapper is not None
                             else EscapeConditionKey(ref))
    return keys


def _ExpandVector(value):
    """geckolib 标量通道值 → 基岩三分量向量; 已是向量/关键帧字典的原样返回。

    geckolib 允许 "scale": 2 这种等比标量(表达式字符串同理), 基岩**必须**是
    三分量数组 —— 一处标量就让整份文件 node parse failed(实测: main 里 34 处
    "scale": 1 让 46 条动画全军覆没, 表现为模型完全僵直)。
    """
    if isinstance(value, (int, float)) or isinstance(value, (str, unicode)):  # noqa: F821
        return [value, value, value]
    if isinstance(value, list) and len(value) == 1:
        return [value[0], value[0], value[0]]    # Java Vector3v: 单元素数组 = 标量广播
    return value


def _NormalizeChannelValue(value, counts):
    """规范化一个通道/关键帧取值: 赋值语句补分号 + 标量展开为三分量向量。

    counts 是 [向量修复数, 语句修复数], 原地累加。
    """
    if isinstance(value, list):
        if len(value) == 1:
            value = _ExpandVector(value)      # Java Vector3v: 单元素数组 = 标量广播
            counts[0] += 1
        for index, item in enumerate(value):
            fixedItem, changed = _NormalizeMolangStatement(item)
            fixedItem, changedReturn = _NormalizeComplexExpression(fixedItem)
            if changed or changedReturn:
                value[index] = fixedItem
                counts[1] += 1
        return value
    if isinstance(value, dict):
        return value
    value, changed = _NormalizeMolangStatement(value)
    value, changedReturn = _NormalizeComplexExpression(value)
    if changed or changedReturn:
        counts[1] += 1
    expanded = _ExpandVector(value)
    if expanded is not value:
        counts[0] += 1
    return expanded


def _NormalizeBoneChannels(body):
    """规范化一条动画的全部骨骼通道; 返回 (向量修复数, 赋值语句修复数)"""
    counts = [0, 0]
    for _bone, channels in (body.get("bones") or {}).items():
        if not isinstance(channels, dict):
            continue
        for channel in list(channels.keys()):
            if channel not in _VECTOR_CHANNELS:
                continue
            value = channels[channel]
            if isinstance(value, dict):
                # 时间轴形态: {"0.0": 值 | {"pre": 值, "post": 值, ...}}
                for stamp in list(value.keys()):
                    frame = _NormalizeKeyframeDict(value[stamp], counts)
                    value[stamp] = frame
                    if isinstance(frame, dict):
                        for side in ("pre", "post"):
                            if side in frame:
                                frame[side] = _NormalizeChannelValue(frame[side], counts)
                    else:
                        value[stamp] = _NormalizeChannelValue(frame, counts)
                continue
            channels[channel] = _NormalizeChannelValue(value, counts)
    return counts


def _NormalizeLerpMode(value):
    """Java EasingType.fromJson: 字符串大小写不敏感认 linear/catmullrom, 其余一律 linear(返回 None)"""
    if isinstance(value, (str, unicode)):  # noqa: F821
        word = value.strip().lower()
        if word in ("linear", "catmullrom"):
            return word
    return None


def _NormalizeKeyframeDict(frame, counts):
    """关键帧字典按 Java BoneKeyFrameList 的读法归一(幂等); 返回归一后的帧, counts[0] 累加改动。

    - geckolib easing 形态 {"vector": …, "easing": …} → {"post": …(, "lerp_mode")}: Java 把 vector
      读成 pre 且 contiguous, easing 只认 linear/catmullrom;
    - 只写 pre 不写 post → 补同值 post(Java contiguous 语义);
    - 只写 post 不写 pre → 补同值 pre(Java JsonKeyFrameUtils"没错，post 赋给 pre")。**基岩缺 pre 时
      取通道默认值**(2026-09-16 实机骨骼缩放探针: 凋灵娘火焰精灵帧与 death 的 Root 在收尾帧
      `{"post": 0}` 前一段从 0 线性涨到 1 —— scale 的默认值; rotation/position 则会往 0 漂),
      表现为"莫名其妙的 scale 动画"/收尾段姿态漂回绑定姿态;
    - lerp_mode 大小写/未知值归一(Java 大小写不敏感, 未知 = linear);
    - 其余键删掉(基岩 "child not valid here" 会作废整份文件)。
    """
    if not isinstance(frame, dict):
        return frame
    changed = False
    if "vector" in frame:
        vector = frame.get("vector")
        easing = _NormalizeLerpMode(frame.get("easing"))
        newFrame = OrderedDict([("post", vector)])
        if easing == "catmullrom":
            newFrame["lerp_mode"] = easing
        frame = newFrame
        changed = True
    if "pre" not in frame and "post" not in frame:
        return frame                       # 不是关键帧字典(Java 会抛解析异常), 原样
    if "pre" in frame and "post" not in frame:
        frame["post"] = copy.deepcopy(frame["pre"])
        changed = True
    if "post" in frame and "pre" not in frame:
        rebuilt = OrderedDict([("pre", copy.deepcopy(frame["post"]))])
        for key in frame:
            rebuilt[key] = frame[key]
        frame = rebuilt
        changed = True
    if "lerp_mode" in frame:
        normalized = _NormalizeLerpMode(frame["lerp_mode"])
        if normalized is None:
            del frame["lerp_mode"]
            changed = True
        elif normalized != frame["lerp_mode"]:
            frame["lerp_mode"] = normalized
            changed = True
    for key in list(frame.keys()):
        if key not in ("pre", "post", "lerp_mode"):
            del frame[key]
            changed = True
    if changed:
        counts[0] += 1
    return frame


_TAIL_EPSILON = 1e-4


def _FrameValue(frame):
    """关键帧取值(兼容 {"pre":…, "post":…} 形态); 取不到返回 None"""
    if isinstance(frame, dict):
        for side in ("post", "pre"):
            if side in frame:
                return frame[side]
        return None
    return frame


def _FrameHasExpression(frame):
    """关键帧取值里是否含 molang 表达式字符串(pre/post 两侧都看)"""
    values = []
    if isinstance(frame, dict):
        for side in ("pre", "post"):
            sideValue = frame.get(side)
            if isinstance(sideValue, list):
                values += sideValue
            elif sideValue is not None:
                values.append(sideValue)
    elif isinstance(frame, list):
        values = frame
    else:
        values = [frame]
    return any(isinstance(v, (str, unicode)) for v in values)  # noqa: F821


def _ChannelStampOrder(value):
    """关键帧按时间戳数值升序; 非数值键排到末尾(保持原相对次序)"""
    stamps = []
    tail = []
    for stamp in value.keys():
        try:
            stamps.append((float(stamp), stamp))
        except (TypeError, ValueError):
            tail.append(stamp)
    stamps.sort()
    return [stamp for _t, stamp in stamps] + tail


def NormalizeExpressionKeyframes(body):
    """catmullrom 关键帧的**引擎常量窗口**里出现 molang 表达式 → 改 linear; 返回改写数(幂等)。

    **引擎判据(2026-09-16 实测定案)**: 网易引擎加载时对 catmullrom 关键帧 i 预计算三次样条, 控制点
    取关键帧 **i-1、i、i+1、i+2**, 四帧中任一帧含表达式就报 `Precomputed cubic interpolation requires
    keyframes have constant data`。对部署产物逐通道检验(三个移植包 + compat 全部动画): 规则
    "catmullrom 帧 i 的 [i-1, i+2] 含表达式"与日志里的 9 条报错动画**逐条、逐通道数量**完全吻合,
    零误报零漏报; 对称窗口 [i-2, i+2] 多报 4 条, 旧的 ±1 邻域一条都抓不到。由此也可知基岩的
    lerp_mode 只管关键帧的**出边** [i, i+1](Java 是区间任一端, 换算见 ApplyJavaCatmullSegments)。

    以下为旧版(±1 邻域)的历史注释, 结论方向正确、窗口偏窄: 关键在于两条规则的
    作用域是**"表达式帧 ∪ 它的前后邻居"**, 而不只是表达式帧自己 —— 插值是**帧间**行为,
    一段区间的两端只要有一头是表达式, 这段就受限。CSM 全库统计(30 文件 / 3742 通道):

    | 形态                      | 挨着表达式 | 远离表达式 |
    |---------------------------|-----------|-----------|
    | `lerp_mode: catmullrom`   |   **0**   |    68     |
    | 只写 `post` 不写 `pre`     |   **0**   |   8346    |

    远离表达式的那两列量很大且工作正常 —— 所以这不是"CSM 不用 catmullrom/post", 而是
    **精确地避开表达式的邻域**。

    ① `catmullrom` → `linear`。catmull-rom 要靠**相邻关键帧的值**算切线, 邻居是表达式
       (逐帧变化)时样条没有良定义。实机(2026-09-05 warden/wither): 拉弓时按 head_yaw
       回正那一项不生效, 身体只剩 -75° 侧身, 弓甩到视线左侧六十多度。
    ② 只写 `post` 的补一份同值 `pre`。缺 pre 时引擎不平滑过渡到该帧, 而是**阶跃**。
       实机(2026-09-05 warden): 一次拉弓蓄力"跳变"三次而非一秒内平滑抬起。

    首轮只按"表达式帧自己"修(122 + 184 处), 剩下的邻居帧就是第二轮实机现象的成因:
    warden `use_mainhand.cls.bow` 的 UpBody 里 t=0.0 是**纯数值** catmullrom 帧, 紧邻
    t=0.5 的表达式帧 —— 0→0.5 这段整段退化, 表现为"先停在侧面姿态半秒, 再跳到正面"。
    """
    fixes = 0
    for _bone, channels in (body.get("bones") or {}).items():
        if not isinstance(channels, dict):
            continue
        for channel, value in channels.items():
            if channel not in _VECTOR_CHANNELS or not isinstance(value, dict):
                continue
            order = _ChannelStampOrder(value)
            hasExpr = [_FrameHasExpression(value[stamp]) for stamp in order]
            if not any(hasExpr):
                continue
            for index, stamp in enumerate(order):
                frame = value[stamp]
                if not isinstance(frame, dict):
                    continue
                if frame.get("lerp_mode") == "catmullrom" \
                        and any(hasExpr[max(0, index - 1):index + 3]):
                    frame["lerp_mode"] = "linear"
                    fixes += 1
                if "post" in frame and "pre" not in frame:
                    frame["pre"] = copy.deepcopy(frame["post"])
                    fixes += 1
    return fixes


def _NumericStampOrder(value):
    """时间轴通道里数值时间戳的升序列表(非数值键忽略)"""
    stamps = []
    for stamp in value:
        try:
            stamps.append((float(stamp), stamp))
        except (TypeError, ValueError):
            continue
    stamps.sort()
    return [stamp for _t, stamp in stamps]


def ApplyJavaCatmullSegments(body):
    """Java 的 catmullrom 分段语义 → 基岩的出边语义; 返回改写帧数。**只在移植期跑一次**(非幂等)。

    Java(BoneKeyFrameProcessor, "和 BlockBench 保持一致"): 区间 [i, i+1] 只要**任一端**是 catmullrom
    就走样条, 控制点 i-1..i+2(端点钳位, 不回绕)。基岩: 关键帧 i 的 lerp_mode 只管出边 [i, i+1]
    (见 NormalizeExpressionKeyframes 注的引擎实测)。换算 b[i] = cm[i] or cm[i+1]:
    - "线性帧 → catmullrom 帧"那一段在 Java 是样条、基岩是直线 → 前一帧补 catmullrom;
    - 末帧没有出边, 一律 linear —— Java 末帧之后保持末值不回绕, 基岩末帧 catmullrom 在循环时
      是否回绕首帧无证据, 不留这个口子。
    非幂等: 再跑一遍会把 catmullrom 继续往前传, 修复工具不调用(产物里看不出原始标记)。
    """
    fixes = 0
    for _bone, channels in (body.get("bones") or {}).items():
        if not isinstance(channels, dict):
            continue
        for channel, value in channels.items():
            if channel not in _VECTOR_CHANNELS or not isinstance(value, dict):
                continue
            order = _NumericStampOrder(value)
            flags = [isinstance(value[stamp], dict) and value[stamp].get("lerp_mode") == "catmullrom"
                     for stamp in order]
            if not any(flags):
                continue
            count = len(order)
            for index, stamp in enumerate(order):
                want = index + 1 < count and (flags[index] or flags[index + 1])
                frame = value[stamp]
                if want and not flags[index]:
                    if not isinstance(frame, dict):
                        frame = OrderedDict([("pre", copy.deepcopy(frame)),
                                             ("post", copy.deepcopy(frame))])
                        value[stamp] = frame
                    frame["lerp_mode"] = "catmullrom"
                    fixes += 1
                elif not want and flags[index]:
                    frame["lerp_mode"] = "linear"
                    fixes += 1
    return fixes


def SealAnimationHeads(body):
    """首个关键帧晚于 0 的通道在 0.0 补一帧"保持首帧 pre"; 返回补帧数(幂等)。

    Java(EasingType.buildTransitionKeyFrame): 首帧之前恒输出首帧的 pre 值。基岩首帧之前的取值
    (保持首帧 / 从默认值插值 / 循环时从末帧回绕 —— Blockbench 预览按回绕处理)没有证据, 补一帧
    同值线性帧把 Java 语义显式化。首帧若是 catmullrom, 它出边样条的左控制点变成补的这一帧,
    值等于首帧 pre, 与 Java 钳位(取首帧自身)在 pre == post 时完全相同。
    """
    fixes = 0
    for _bone, channels in (body.get("bones") or {}).items():
        if not isinstance(channels, dict):
            continue
        for channel in list(channels.keys()):
            value = channels[channel]
            if channel not in _VECTOR_CHANNELS or not isinstance(value, dict):
                continue
            order = _NumericStampOrder(value)
            if not order or float(order[0]) <= _TAIL_EPSILON:
                continue
            first = value[order[0]]
            headValue = first.get("pre", first.get("post")) if isinstance(first, dict) else first
            if headValue is None:
                continue
            rebuilt = OrderedDict([("0.0", OrderedDict([
                ("pre", copy.deepcopy(headValue)), ("post", copy.deepcopy(headValue)),
                ("lerp_mode", "linear")]))])
            for stamp in value:
                rebuilt[stamp] = value[stamp]
            channels[channel] = rebuilt
            fixes += 1
    return fixes


# 旧名保留: 文档/外部脚本可能还在引用
ForceLinearOnExpressionKeyframes = NormalizeExpressionKeyframes


def SealAnimationTails(body):
    """在 animation_length 处补一帧"保持末值", 封住关键帧结束后的空档; 返回补帧数。

    **Java 与基岩的收尾语义不同**(2026-09 实机三变体 A/B 定位): geckolib 在最后一个
    关键帧之后**保持末值**直到动画结束; 基岩则把"最后关键帧 → 动画结尾/循环点"当作
    一个待插值的区间 —— 关键帧用 `lerp_mode: catmullrom` 时, 这段跨越数秒的样条会把
    通道值拉到失控范围。实机(ref_wither 眨眼 yanshenazhayan: animation_length 5s,
    末帧 0.1875s)表现为**眼睛常年不可见**(scale 被拉塌), 而非每 5 秒眨一次。

    补一帧同值的线性关键帧即把"保持"显式化, 等价于 geckolib 行为, 且不动作者在
    有效区间内的缓动曲线。对照组: 去掉 catmullrom 同样能修(但会丢缓动), 去掉
    override 标志无效(证明与通道覆盖语义无关)。
    **收尾帧必须 pre、post 都写**(2026-09-16 实机): 只写 post 时基岩把 pre 当通道默认值, 末帧到
    收尾帧这一段会插值到默认值(scale 1 / rotation、position 0) —— 凋灵娘火焰精灵帧隐藏后又从 0
    "长"回 1, 就是早先只写 post 的收尾帧造成的。
    """
    length = body.get("animation_length")
    if not isinstance(length, (int, float)) or isinstance(length, bool):
        return 0
    if length >= JAVA_INFINITE_LENGTH:
        return 0      # Java 无限长的静态动画(见 ApplyJavaImpliedLength): 没有"结尾"可封
    sealed = 0
    for _bone, channels in (body.get("bones") or {}).items():
        if not isinstance(channels, dict):
            continue
        for channel, value in channels.items():
            if channel not in _VECTOR_CHANNELS or not isinstance(value, dict):
                continue
            stamps = []
            for stamp in value:
                try:
                    stamps.append((float(stamp), stamp))
                except (TypeError, ValueError):
                    continue
            if not stamps:
                continue
            lastTime, lastStamp = max(stamps)
            if lastTime >= length - _TAIL_EPSILON:
                continue
            lastValue = _FrameValue(value[lastStamp])
            if lastValue is None:
                continue
            value[repr(float(length))] = OrderedDict([
                ("pre", copy.deepcopy(lastValue)), ("post", copy.deepcopy(lastValue)),
                ("lerp_mode", "linear")])
            sealed += 1
    return sealed


def _NormalizeLoopValue(value):
    """loop 字段的三态归一: 布尔/"true"/"false"/"hold_on_last_frame"/缺省"""
    if isinstance(value, (str, unicode)):  # noqa: F821
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return lowered
    return value


def ApplyJavaLoopSemantics(shortKey, body):
    """主链/一次性通道成员的 loop 字段按 Java **运行**语义改写(幂等); 返回是否改动。

    - 主链成员(idle/walk/sleep/sit/boat/.../vehicle$): Java AnimationRegister 强制
      LOOP, 动画 JSON 的 loop 被忽略 —— Java 包常见 sleep/sit/boat 写成不循环
      (BlockBench 默认), 基岩听 JSON 会播一遍就停(睡下一秒后姿态消失);
    - death/attacked: Java 强制 PLAY_ONCE + 3 tick 尾过渡淡出 → hold_on_last_frame
      (基岩 loop:false 播完即撤, 没有末姿态可供淡出), 淡出由 ysm_state 主链状态机的
      <键>_done 空状态完成;
    - 主手挥击族(swing$/#/:, swing_hand): Java 听 JSON、缺省 PLAY_ONCE + 尾过渡 →
      未写循环者同样改 hold_on_last_frame, 淡出由 ysm_swing 的 cooldown 完成
      (loop:true 者保持: 触发消失即走)。
    """
    raw = body.get("loop")
    current = _NormalizeLoopValue(raw)
    loopType = JavaStateLoopType(shortKey)
    if loopType == "loop":
        if raw is True:
            return False
        body["loop"] = True           # 字符串 "true" 也统一成布尔(引擎只认布尔/hold 串)
        return True
    if loopType == "once" or (_IsMainSwingKey(shortKey) and current in (None, False)):
        if raw == "hold_on_last_frame":
            return False
        body["loop"] = "hold_on_last_frame"
        return True
    return False


# ---- Java 动画字段的解析语义(format/parser/pojo/animation/Animation.java + LoopType.fromJson) ----
# loop: 布尔按值; 字符串大小写不敏感认 true/loop → LOOP, hold_on_last_frame → HOLD,
# false/play_once/其他任何值(含数字) → PLAY_ONCE。基岩只认布尔与小写 hold_on_last_frame 串。
_LOOP_TRUE_WORDS = ("true", "loop")
_LOOP_HOLD_WORD = "hold_on_last_frame"
# Java 解析了但 AnimationBuilder.toProto 不搬进运行时的字段: 基岩会按原版语义执行(延迟起播/
# 循环间隔/自定义时钟), 留着就是"Java 没生效、基岩突然生效"的反向差异, 一律删掉
_JAVA_IGNORED_ANIMATION_KEYS = ("anim_time_update", "start_delay", "loop_delay")
# Java pojo Animation.calculateLength: 无 animation_length 且无带时间戳的骨骼关键帧 →
# Float.MAX_VALUE —— 永不结束、LOOP 永不回绕、timeline **只跑一次**、all_animations_finished
# 永假(凋灵娘 voice_set_1..23 轮盘键靠它设一次 v.voice_short=N)。基岩缺省按关键帧算成 0 长度,
# 语义反转(立即结束 / 起播触发一次后就"播完了"); 移植期显式写一个够用一辈子的长度复刻无限长。
JAVA_INFINITE_LENGTH = 1000000.0
# timeline 时间戳与长度比较的容差: Blockbench 写 4 位小数, 0.0101 与 0.01 是两个不同的帧
# (Java 0.202 tick > 0.2 tick 即"超出"), 不能沿用封尾用的 1e-4
_TIMELINE_EPSILON = 1e-6


def NormalizeLoopField(body):
    """loop 字段按 Java LoopType.fromJson 归一成基岩认的形态; 返回是否改写(幂等)"""
    if not isinstance(body, dict) or "loop" not in body:
        return False
    raw = body["loop"]
    if isinstance(raw, bool):
        return False
    if isinstance(raw, (str, unicode)):  # noqa: F821
        word = raw.strip().lower()
        if word in _LOOP_TRUE_WORDS:
            body["loop"] = True
        elif word == _LOOP_HOLD_WORD:
            if raw == _LOOP_HOLD_WORD:
                return False
            body["loop"] = _LOOP_HOLD_WORD
        else:
            del body["loop"]              # false / play_once / 未知串 → PLAY_ONCE(缺省)
        return True
    del body["loop"]                      # 数字/对象: Java 一律 PLAY_ONCE
    return True


def DropJavaIgnoredAnimationFields(body):
    """删掉 Java 运行时忽略的动画字段(见 _JAVA_IGNORED_ANIMATION_KEYS); 返回删除数"""
    if not isinstance(body, dict):
        return 0
    dropped = 0
    for key in _JAVA_IGNORED_ANIMATION_KEYS:
        if key in body:
            del body[key]
            dropped += 1
    return dropped


def ApplyJavaImpliedLength(body):
    """animation_length 缺省(或非数字)时按 Java 推导写成显式值; 返回 "keyframes"/"infinite"/None。

    Java: 长度 = 最后一个带时间戳的骨骼关键帧(timeline/音效帧不算); 一个都没有 → 无限长。
    基岩缺省算法未文档化(是否算上 timeline 帧无证据), 显式写死让两边一致; 无限长见
    JAVA_INFINITE_LENGTH 注。
    """
    if not isinstance(body, dict):
        return None
    length = body.get("animation_length")
    if isinstance(length, (int, float)) and not isinstance(length, bool):
        return None
    latest = _MaxKeyframeStamp(body)
    if latest > 0:
        body["animation_length"] = latest
        return "keyframes"
    body["animation_length"] = JAVA_INFINITE_LENGTH
    return "infinite"


def ClampTimelineToLength(body):
    """超出 animation_length 的 timeline 条目按 Java 语义处理; 返回处理数。

    Java AnimationPlayer: LOOP 回绕、PLAY_ONCE 结束时都会 executeRemaining —— 没到点的
    timeline 条目在动画结尾**补跑**(builtin wine_fox parallel4: 0.01s 循环里 0.0101 的条目
    每圈都跑); HOLD_ON_LAST_FRAME 锁在末帧, 超长条目永不执行。基岩超出长度的条目大概率
    不触发: 循环/单次的挪到结尾时间戳(合并同刻条目), hold 的删除。
    """
    if not isinstance(body, dict):
        return 0
    length = body.get("animation_length")
    timeline = body.get("timeline")
    if not isinstance(length, (int, float)) or isinstance(length, bool) \
            or length >= JAVA_INFINITE_LENGTH or not isinstance(timeline, dict):
        return 0
    hold = body.get("loop") == _LOOP_HOLD_WORD
    target = None
    for stamp in timeline:
        try:
            if abs(float(stamp) - length) <= _TIMELINE_EPSILON:
                target = stamp
                break
        except (TypeError, ValueError):
            continue
    moved = 0
    for stamp in list(timeline.keys()):
        try:
            seconds = float(stamp)
        except (TypeError, ValueError):
            continue
        if seconds <= length + _TIMELINE_EPSILON:
            continue
        value = timeline.pop(stamp)
        moved += 1
        if hold:
            continue
        lines = value if isinstance(value, list) else [value]
        if target is None:
            target = repr(float(length))
            timeline[target] = value
            continue
        existing = timeline[target]
        timeline[target] = (existing if isinstance(existing, list) else [existing]) + lines
    if not timeline:
        del body["timeline"]
    return moved


# Java 的 molang 里一条只有字符串字面量的语句是空操作, 作者拿它当注释
# (builtin 10_zhiban timeline: '更改频率/响应速度,取值[0,5]';)。基岩对它的容忍无证据, 删掉。
_STRING_STATEMENT = re.compile(r"^\s*'[^']*'\s*$")


def _StripStringStatementsText(text):
    if "'" not in text:
        return text, 0
    statements, endsWithSemicolon = _SplitTopLevelStatements(text)
    kept = [statement for statement in statements if not _STRING_STATEMENT.match(statement)]
    removed = len(statements) - len(kept)
    if not removed:
        return text, 0
    if not kept:
        return "", removed
    joined = ";".join(statement.strip() for statement in kept)
    if endsWithSemicolon or len(kept) > 1:
        joined += ";"
    return joined, removed


def StripStringStatements(body):
    """timeline 里的字符串字面量语句删掉; 语句删空的条目一并删掉; 返回删除的语句数"""
    timeline = body.get("timeline") if isinstance(body, dict) else None
    if not isinstance(timeline, dict):
        return 0
    removed = 0
    for stamp in list(timeline.keys()):
        value = timeline[stamp]
        lines = value if isinstance(value, list) else [value]
        kept = []
        for line in lines:
            if isinstance(line, (str, unicode)):  # noqa: F821
                line, count = _StripStringStatementsText(line)
                removed += count
                if not line.strip():
                    continue
            kept.append(line)
        if kept:
            timeline[stamp] = kept if isinstance(value, list) else kept[0]
        else:
            del timeline[stamp]
    if not timeline:
        del body["timeline"]
    return removed


_ANIMATION_CHANNEL_NAMES = ("rotation", "position", "scale")


def SanitizeAnimationBody(body):
    """删掉引擎拒绝解析的空节点; 返回删除数。

    **一个空节点作废整份动画文件**(2026-09-03 实机, 代价极大的一课): `"bones": {}` 让
    引擎报 "Required child [a-zA-Z0-9_.-]+ not found / node parse failed: bones", 该文件
    (ref_warden/ref_sahmet 的 main.animation.json, 各一条被剥空的动画)随之整份不可用 ——
    主链/pre/parallel 全部消失, 模型停在绑定姿态、换装件与表情面片全亮("显示配件模型")。
    之前把这一幕解读成 override_previous_animation 的"占住骨骼"语义, 是误判。
    规则: 空通道值 / 空骨骼 / 非字典骨骼 / 空 bones / 空 timeline / 空 animation_length
    一律删键 —— 不写 bones 键是合法的(sahmet 的 jump、parallel3 等空动画本就没有)。
    """
    if not isinstance(body, dict):
        return 0
    fixes = 0
    bones = body.get("bones")
    if isinstance(bones, dict):
        for boneName in list(bones.keys()):
            channels = bones[boneName]
            if isinstance(channels, dict):
                for channel in list(channels.keys()):
                    value = channels[channel]
                    if isinstance(value, (dict, list)) and not value:
                        del channels[channel]
                        fixes += 1
            if not isinstance(channels, dict) or not channels:
                del bones[boneName]
                fixes += 1
    if "bones" in body and (not isinstance(bones, dict) or not bones):
        del body["bones"]
        fixes += 1
    timeline = body.get("timeline")
    if "timeline" in body and (not isinstance(timeline, dict) or not timeline):
        del body["timeline"]
        fixes += 1
    if "animation_length" in body and body["animation_length"] is None:
        del body["animation_length"]
        fixes += 1
    return fixes


# ---- Java ysm.particle / ysm.abs_particle → 基岩粒子关键帧 ----
# Java(ParticleSpawner): ysm.particle('id', x, y, z, dx, dy, dz, speed, count, life) 在**每次
# 求值**时于实体脚底 + (x,y,z)(按身体朝向旋转) 处生成粒子, 参数同原版 /particle 指令。
# 基岩表达式层没有副作用函数, 等价物是动画的 particle_effects 关键帧: 到时间戳触发一次,
# 循环动画每圈再触发 —— 写在"每 tick 脚本"动画(ApplyTickTimelineLength → 0.05s 循环)的
# timeline 里的调用于是每 tick 触发一次, 与 Java 同拍(Java 是每渲染帧, 密度略低)。位置走
# 几何 locator: 打在根骨骼上, 模型空间单位 1/16 格; Java 本地坐标 +x 为左、+z 为前, 基岩
# 模型空间 +x 为左、**-z 为前**, 故 z 取反。dx/dy/dz/speed/count/life 由基岩粒子定义自带,
# 无法逐参照搬(取最接近的原版粒子)。abs_particle(世界绝对偏移)同样落到随身 locator(近似)。
# 位置参数不是数字字面量(表达式)时落到原点 locator 并告警。只处理 timeline 里的调用: 通道
# 表达式里的调用没有时间戳, 仍由 _FUNCTION_STRATEGIES 置零。
# 关键帧一律 bind_to_actor:false —— Java ParticleSpawner 把粒子生成在**世界坐标**(实体坐标 + 按身体朝向
# 旋转的偏移)后交给粒子引擎, 粒子**不随实体移动**; 基岩缺省 true 是发射器跟着玩家跑(2026-09-17 源码核对,
# 该键在网易引擎的可用性由 ysm_rp/animations/shared/particle_probe.animation.json 探针文件确认)。
_JAVA_PARTICLE_MAP = OrderedDict([
    ("minecraft:flame", "minecraft:basic_flame_particle"),
    ("minecraft:small_flame", "minecraft:small_flame_particle"),
    ("minecraft:soul_fire_flame", "minecraft:blue_flame_particle"),
    ("minecraft:soul", "minecraft:soul_particle"),
    ("minecraft:smoke", "minecraft:basic_smoke_particle"),
    ("minecraft:large_smoke", "minecraft:campfire_smoke_particle"),
    ("minecraft:campfire_cosy_smoke", "minecraft:campfire_smoke_particle"),
    ("minecraft:campfire_signal_smoke", "minecraft:campfire_tall_smoke_particle"),
    ("minecraft:end_rod", "minecraft:endrod"),
    ("minecraft:crit", "minecraft:critical_hit_emitter"),
    ("minecraft:enchanted_hit", "minecraft:magic_critical_hit_emitter"),
    ("minecraft:heart", "minecraft:heart_particle"),
    ("minecraft:portal", "minecraft:basic_portal_particle"),
    ("minecraft:enchant", "minecraft:enchanting_table_particle"),
    ("minecraft:electric_spark", "minecraft:electric_spark_particle"),
    ("minecraft:glow", "minecraft:glow_particle"),
    ("minecraft:lava", "minecraft:lava_particle"),
    ("minecraft:bubble", "minecraft:basic_bubble_particle"),
    ("minecraft:note", "minecraft:note_particle"),
    ("minecraft:snowflake", "minecraft:snowflake_particle"),
    ("minecraft:sculk_soul", "minecraft:sculk_soul_particle"),
    ("minecraft:sonic_boom", "minecraft:sonic_explosion"),
    ("minecraft:totem_of_undying", "minecraft:totem_particle"),
    ("minecraft:explosion", "minecraft:explosion_particle"),
    ("minecraft:firework", "minecraft:sparkler_emitter"),
    ("minecraft:cherry_leaves", "minecraft:cherry_leaves_particle"),
    ("minecraft:falling_dust", "minecraft:falling_dust"),
    ("minecraft:rain", "minecraft:rain_splash_particle"),
])
_PARTICLE_CALL_PATTERN = re.compile(r"\bysm\.(abs_)?particle\s*\(")
_PARTICLE_LOCATOR_PREFIX = "ysm_pt_"
_PARTICLE_ORIGIN_LOCATOR = _PARTICLE_LOCATOR_PREFIX + "origin"


def _ParticleLiteral(arg):
    """字符串字面量参数 → 去引号文本; 非字面量 → None"""
    text = (arg or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return None


def _ParticleOffset(args):
    """(x, y, z) 三个位置参数 → 模型空间 locator 坐标; 任一非数字字面量 → None"""
    offset = []
    for index in (1, 2, 3):
        raw = args[index].strip() if len(args) > index and args[index].strip() else "0"
        value = _MolangNumber(raw)
        if value is None:
            return None
        offset.append(value)
    return [round(offset[0] * 16.0, 4), round(offset[1] * 16.0, 4), round(-offset[2] * 16.0, 4)]


def _ParticleEffectKey(bedrockId):
    return _PARTICLE_LOCATOR_PREFIX + re.sub(r"[^a-z0-9_]", "_", bedrockId.split(":")[-1].lower())


def ConvertTimelineParticles(body, sink, report=None, shortKey=None):
    """动画体 timeline 里的 ysm.particle 调用 → particle_effects 关键帧; 返回转换的调用数。

    sink: {"effects": OrderedDict(效果键 → 基岩粒子 ID), "locators": OrderedDict(名 → [x,y,z])},
    全包共用(几何 locator 与 ysm.json 登记由 PortPack 收口)。调用文本原地删除, 剩余语句保留;
    语句删空的时间戳一并删掉(空条目引擎拒绝)。
    """
    if report is None:
        report = Counter()
    timeline = body.get("timeline") if isinstance(body, dict) else None
    if not isinstance(timeline, dict):
        return 0
    converted = 0
    for stamp in list(timeline.keys()):
        value = timeline[stamp]
        lines = value if isinstance(value, list) else [value]
        keptLines = []
        for line in lines:
            if not isinstance(line, (str, unicode)):  # noqa: F821
                keptLines.append(line)
                continue
            text = line
            while True:
                match = _PARTICLE_CALL_PATTERN.search(text)
                if match is None:
                    break
                args, endIndex = _ScanCall(text, match.end())
                javaId = _ParticleLiteral(args[0] if args else "")
                if javaId is None:
                    # ID 不是字面量: 无法登记资源, 走原有置零路径
                    report[u"zero:ysm.particle(ID 非字面量, 无法转关键帧)"] += 1
                    text = text[:match.start()] + "0.0" + text[endIndex + 1:]
                    continue
                bedrockId = _JAVA_PARTICLE_MAP.get(javaId)
                if bedrockId is None:
                    bedrockId = javaId
                    report[u"warn:ysm.particle({}) 无基岩对照, 原名直通(引擎查无即无效果)".format(javaId)] += 1
                effectKey = _ParticleEffectKey(bedrockId)
                sink["effects"].setdefault(effectKey, bedrockId)
                offset = _ParticleOffset(args)
                if offset is None:
                    report[u"warn:ysm.particle 位置为表达式, 落到实体原点 locator"] += 1
                    locator = _PARTICLE_ORIGIN_LOCATOR
                    sink["locators"].setdefault(locator, [0.0, 0.0, 0.0])
                elif not any(offset):
                    locator = _PARTICLE_ORIGIN_LOCATOR
                    sink["locators"].setdefault(locator, [0.0, 0.0, 0.0])
                else:
                    locator = None
                    for name, existing in sink["locators"].items():
                        if existing == offset and name != _PARTICLE_ORIGIN_LOCATOR:
                            locator = name
                            break
                    if locator is None:
                        locator = "{}{}".format(_PARTICLE_LOCATOR_PREFIX, len(sink["locators"]) + 1)
                        sink["locators"][locator] = offset
                if match.group(1):
                    report[u"warn:ysm.abs_particle 世界绝对偏移近似为随身 locator"] += 1
                effects = body.setdefault("particle_effects", OrderedDict())
                entry = OrderedDict([("effect", effectKey), ("locator", locator), ("bind_to_actor", False)])
                existing = effects.get(stamp)
                if existing is None:
                    effects[stamp] = entry
                elif isinstance(existing, list):
                    existing.append(entry)
                else:
                    effects[stamp] = [existing, entry]
                report[u"map:ysm.particle({}) -> particle_effects 关键帧 {} @{}".format(
                    javaId, bedrockId, locator)] += 1
                converted += 1
                text = text[:match.start()] + text[endIndex + 1:]
            statements = [part.strip() for part in text.split(";")]
            statements = [part for part in statements if part]
            if statements:
                keptLines.append(";".join(statements) + ";")
        if keptLines:
            timeline[stamp] = keptLines if isinstance(value, list) else keptLines[0]
        else:
            del timeline[stamp]
    if not timeline:
        del body["timeline"]
    return converted


# ---- Java 动画音频关键帧 → 基岩 sound_effects 关键帧 + sound_definitions + 注册 ----
# Java(wiki《添加音频》, 2.3.0+): 动画 sound_effects 关键帧的 effect 写 sounds/ 目录下的文件名
# (不带扩展名; 目录可由 files.sound_path / files.player.sound_path 改), 或原版音效 ID(含 ':')。
# 基岩要三件套才能响: ① ogg 拷进资源包 sounds/ysm/<包>/; ② sounds/sound_definitions.json 登记
# 定义 ysm.<包>.<安全名>; ③ ysm.json files.player.sound_effect 登记 [效果键, 定义名], 主包据此
# AddPlayerSoundEffect, 动画关键帧里的 effect 改写为效果键(与 particle_effects 同一套机制)。
# 原版音效 ID 两边命名不同(Java block.anvil.hit ≠ 基岩 random.anvil_land), 无可靠对照表:
# 定义名直通 ':' 之后的段并告警。
_SOUND_EFFECT_KEY_PREFIX = "ysm_snd_"
_SOUND_DEFINITIONS_FILE = os.path.join(RP, "sounds", "sound_definitions.json")
_SOUND_CATEGORY = "neutral"


def _SoundSafeName(name):
    return re.sub(r"[^a-z0-9_]+", "_", Pinyinize(name).lower()).strip("_") or "sound"


class SoundSink(object):
    """一个移植包的音频汇: 源音频名 → (效果键, 定义名, 是否原版 ID); 跨动画文件共享。"""

    def __init__(self, packName):
        self.packName = packName
        self.entries = OrderedDict()
        self._takenKeys = set()
        self.report = Counter()

    def Register(self, name):
        name = name.strip()
        if name in self.entries:
            return self.entries[name][0]
        vanilla = ":" in name
        base = _SoundSafeName(name.split(":", 1)[1] if vanilla else name)
        candidate, index = base, 2
        while candidate in self._takenKeys:
            candidate = "{}_{}".format(base, index)
            index += 1
        self._takenKeys.add(candidate)
        key = _SOUND_EFFECT_KEY_PREFIX + candidate
        if vanilla:
            definition = name.split(":", 1)[1]
            self.report[u"warn:sound_effects 原版音效 {} 两边命名不同, 定义名直通 {}"
                        u"(基岩查无即无声, 需人工对照)".format(name, definition)] += 1
        else:
            definition = "ysm.{}.{}".format(self.packName, candidate)
        self.entries[name] = (key, definition, vanilla)
        return key

    @property
    def registrations(self):
        return [[key, definition] for _name, (key, definition, _vanilla) in self.entries.items()]


def ConvertSoundKeyframes(body, sink):
    """动画体 sound_effects 关键帧的 effect(Java 源音频名) → 注册效果键; 返回改写数(幂等)。"""
    sounds = body.get("sound_effects") if isinstance(body, dict) else None
    if not isinstance(sounds, dict):
        return 0
    converted = 0
    for stamp in list(sounds.keys()):
        value = sounds[stamp]
        entries = value if isinstance(value, list) else [value]
        kept = []
        for entry in entries:
            name = entry.get("effect") if isinstance(entry, dict) else entry
            if not isinstance(name, (str, unicode)) or not name.strip():  # noqa: F821
                continue                                      # Java: 空名不播
            if name.startswith(_SOUND_EFFECT_KEY_PREFIX):
                kept.append(OrderedDict([("effect", name)]))  # 已是效果键(重跑)
                continue
            kept.append(OrderedDict([("effect", sink.Register(name))]))
            converted += 1
        if kept:
            sounds[stamp] = kept if len(kept) > 1 else kept[0]
        else:
            del sounds[stamp]
    if not sounds:
        del body["sound_effects"]
    return converted


def _SoundSourceDir(manifest):
    """Java 包音频目录(files.sound_path > files.player.sound_path > sounds)"""
    files = manifest.get("files") or {}
    custom = files.get("sound_path") or ((files.get("player") or {}).get("sound_path"))
    if isinstance(custom, (str, unicode)) and custom.strip():  # noqa: F821
        return custom.strip().strip("/")
    return "sounds"


def AsciiFileName(name):
    """资源包文件名 ASCII 化(非 ASCII 段转拼音, 其余非法字符换下划线); 纯 ASCII 原样返回"""
    if isinstance(name, str):
        try:
            name.decode("ascii")
            return name
        except UnicodeDecodeError:
            name = name.decode("utf-8")
    if all(ord(ch) < 128 for ch in name):
        return str(name)
    return str(re.sub(r"[^A-Za-z0-9_.-]", "_", Pinyinize(name)))


def _FsPath(text, base=None):
    """py2: 让待拼接的段(中文音频名/自定义目录)与基路径同型 —— 基路径是 unicode(转换器宿主 port_cli)则段保持
    unicode; 基路径是 sys.argv 的字节串(命令行)则按文件系统编码转字节。异型拼接时 py2 按 ascii 解码字节串, 中文即炸。"""
    if isinstance(base, unicode):  # noqa: F821 — py2
        return text.decode("utf-8") if isinstance(text, str) else text
    if isinstance(text, unicode):  # noqa: F821 — py2
        return text.encode(sys.getfilesystemencoding() or "utf-8")
    return text


def WriteSoundResources(packName, javaDir, soundDirRel, sink):
    """拷贝 ogg + 合并 sound_definitions.json(本包前缀整体替换); 返回 (拷贝数, 缺失源文件名)。幂等。"""
    outDir = os.path.join(RP, "sounds", "ysm", packName)
    copied, missing, definitions, wanted = 0, [], OrderedDict(), set()
    for name, (key, definition, vanilla) in sink.entries.items():
        if vanilla:
            continue
        src = os.path.join(javaDir, _FsPath(soundDirRel, javaDir).replace("/", os.sep),
                           _FsPath(name, javaDir) + ".ogg")
        if not os.path.isfile(src):
            missing.append(name)
            continue
        fileBase = key[len(_SOUND_EFFECT_KEY_PREFIX):]
        CopyBinary(src, os.path.join(outDir, fileBase + ".ogg"))
        wanted.add(fileBase + ".ogg")
        copied += 1
        definitions[definition] = OrderedDict([
            ("category", _SOUND_CATEGORY),
            ("sounds", ["sounds/ysm/{}/{}".format(packName, fileBase)]),
        ])
    if os.path.isdir(outDir):
        for stale in os.listdir(outDir):
            if stale.endswith(".ogg") and stale not in wanted:
                os.remove(os.path.join(outDir, stale))     # 上次移植遗留
    existing = OrderedDict()
    if os.path.isfile(_SOUND_DEFINITIONS_FILE):
        try:
            loaded = LoadJson(_SOUND_DEFINITIONS_FILE)
        except ValueError:
            loaded = {}
        if isinstance(loaded.get("sound_definitions"), dict):
            existing = loaded["sound_definitions"]
        elif isinstance(loaded, dict):          # 旧式根级定义
            existing = OrderedDict((k, v) for k, v in loaded.items() if k != "format_version")
    prefix = "ysm.{}.".format(packName)
    merged = OrderedDict((k, v) for k, v in existing.items() if not str(k).startswith(prefix))
    merged.update(definitions)
    if merged or os.path.isfile(_SOUND_DEFINITIONS_FILE):
        DumpJson(_SOUND_DEFINITIONS_FILE, OrderedDict([
            ("format_version", "1.14.0"), ("sound_definitions", merged)]))
    return copied, missing


# ---- 手持物品的定位骨骼(基岩固定骨骼名) ----
# 基岩把手持物品渲染在**名为 rightItem / leftItem 的骨骼**上(原版 geometry.humanoid.custom:
# `rightItem` 是 `rightArm` 的子骨骼, pivot 在掌心并把 z 前推 1, 骨骼里那个 `lead_hold`
# locator 只是拴绳点)。Java YSM 则渲染在 `RightHandLocator` / `LeftHandLocator` 骨骼上
# (client/model/PlayerLocator.java 的注册表, renderer/layer/CustomPlayerItemInHandLayer
# 用 visitLocatorGroup 取该组)。两边概念一一对应, 但**基岩只认那两个固定骨骼名** —— Java 包
# 没有它们, 直接转换过来手持物品不会出现在手上。故移植期自动补一根空骨骼挂到定位骨骼下。
# Java 的 locator **组**可以有多个成员(凋灵娘的 RightHandLocator2/3 → Java 渲染多份物品),
# 基岩只能一根, 取基名那个。创作者已自己写了 rightItem/leftItem 的包原样保留(可手工微调)。
#
# **pivot 与前推量**(_HELD_ITEM_FORWARDS): 主几何(第三人称/纸娃娃)的物品骨骼 pivot **就在定位
# 骨骼上**(2026-09-17 起) —— 与 CSM(同样把 Java YSM 模型搬到网易基岩的模组, .ref/csm)和作者自制的
# 基岩版凋灵娘(.ref/bedrock_wither 的 ys_wither.json: rightItem pivot == RightHandLocator pivot)一致。
# Java 的手持物画在定位骨骼原点(再平移 (0,-1px,-1.6px)、转 -90° 套物品显示变换), 定位骨骼的缩放
# 不会挪动物品位置; 早先 z 前推 1 时, 拉弓动画把定位骨骼放大到 2 倍会把物品一并往前甩 2 格。两套持物
# 变换的差由主包按物品类别叠的修正动画补(packParser._JavaItemFixAnimates, 数值取自 CSM)。
# 旧产物(主几何前推 1)由 MigrateHeldItemBones 按"形状完全等于生成物"识别后挪到新挂点。
#
# **第一人称同样要补**(model.arm 声明的独立手臂几何): 基岩的第一人称手持物是同一套
# attachable 绑定(原版 bow/shield/crossbow/trident 的几何骨骼就叫 rightitem, 或写
# binding=q.item_slot_to_bone_name(c.item_slot)), 只是换了几何 —— arm 几何缺这根骨骼时
# 弓/弩/盾/三叉戟绑不上(不显示), 普通物品则掉回实体原点(位置严重偏移)。Java 那边第一
# 人称的手持物由原版 ItemInHandRenderer 画在屏幕空间, 与模型无关, 故无对应物, 属基岩
# 专有修正。现行方案下第一人称手持物画在原版体型锚点几何上(playerRender.ApplyItemAnchorGeometry,
# 原版第一人称动画摆位), arm 几何的物品骨骼只为绑定不落空, 沿用原版"相对手臂 z+1"的写法。
_HELD_ITEM_BONE_SPECS = (
    ("rightItem", "RightHandLocator", ("RightHand", "RightArm"), "lead_hold"),
    ("leftItem", "LeftHandLocator", ("LeftHand", "LeftArm"), "lead_hold2"),
)
# 主几何(第三人称/纸娃娃)与 arm 几何(第一人称)都要有; 没声明 arm 的包自动跳过
_HELD_ITEM_GEOMETRIES = ("main.geo.json", "arm.geo.json")
_HELD_ITEM_FORWARDS = {"main.geo.json": 0.0, "arm.geo.json": 1.0}
# 2026-09-17 之前两种几何统一前推 1(迁移识别旧生成物用)
_HELD_ITEM_LEGACY_FORWARD = 1.0
_LOCATOR_DIGIT_SUFFIX = re.compile(r"^(.*?)(\d*)$")


def _FindBoneIndex(bones, name):
    """按骨骼名(大小写不敏感)找下标; 找不到返回 -1"""
    lowered = name.lower()
    for index, bone in enumerate(bones):
        if isinstance(bone, dict) and str(bone.get("name", "")).lower() == lowered:
            return index
    return -1


def _FindLocatorGroupBase(bones, base):
    """定位组的基名骨骼下标: 精确名优先, 否则取带数字后缀里最小的那个(对齐 Java
    GeoLocatorType.getByBoneName 的"剥掉尾部数字"归组规则); 找不到返回 -1"""
    index = _FindBoneIndex(bones, base)
    if index >= 0:
        return index
    lowered = base.lower()
    candidates = []
    for position, bone in enumerate(bones):
        if not isinstance(bone, dict):
            continue
        matched = _LOCATOR_DIGIT_SUFFIX.match(str(bone.get("name", "")))
        if matched and matched.group(1).lower() == lowered and matched.group(2):
            candidates.append((int(matched.group(2)), position))
    if candidates:
        return min(candidates)[1]
    return -1


def _LoadGeometryBones(geoPath):
    """(几何数据, 首个几何的 bones 列表); 文件缺失或结构不对返回 (None, None)"""
    if not os.path.isfile(geoPath):
        return None, None
    data = LoadJson(geoPath)
    geometries = data.get("minecraft:geometry") or []
    if not geometries or not isinstance(geometries[0], dict):
        return None, None
    bones = geometries[0].get("bones")
    if not isinstance(bones, list):
        return None, None
    return data, bones


def _HeldItemParentIndex(bones, locatorBase, fallbacks):
    """物品骨骼该挂的父骨骼下标: 定位组基名优先, 否则按回落名单; 找不到返回 -1"""
    parentIndex = _FindLocatorGroupBase(bones, locatorBase)
    for fallback in fallbacks:
        if parentIndex >= 0:
            break
        parentIndex = _FindBoneIndex(bones, fallback)
    return parentIndex


def _HeldItemAnchor(parent, forward):
    """父骨骼 pivot z 前推 forward 的挂点; 父骨骼 pivot 不合法返回 None"""
    pivot = parent.get("pivot") or [0, 0, 0]
    if not (isinstance(pivot, list) and len(pivot) == 3):
        return None
    return [round(float(pivot[0]), 5), round(float(pivot[1]), 5), round(float(pivot[2]) + forward, 5)]


def AddHeldItemBones(geoPath, forward=0.0):
    """补 rightItem / leftItem 物品骨骼 → [(骨骼名, 父骨骼名)]; 已存在或找不到手部骨骼则跳过。

    forward: pivot 相对父骨骼 pivot 的 z 前推量(按几何取 _HELD_ITEM_FORWARDS)。
    见 _HELD_ITEM_BONE_SPECS 上方长注。幂等: 第二次跑时两根骨骼都已存在。
    """
    data, bones = _LoadGeometryBones(geoPath)
    if bones is None:
        return []
    added = []
    for itemBone, locatorBase, fallbacks, leadName in _HELD_ITEM_BONE_SPECS:
        if _FindBoneIndex(bones, itemBone) >= 0:
            continue                      # 已有(创作者写的或早先生成的), 不补
        parentIndex = _HeldItemParentIndex(bones, locatorBase, fallbacks)
        if parentIndex < 0:
            continue
        parent = bones[parentIndex]
        anchor = _HeldItemAnchor(parent, forward)
        if anchor is None:
            continue
        bones.insert(parentIndex + 1, OrderedDict([
            ("name", itemBone),
            ("parent", str(parent.get("name"))),
            ("pivot", anchor),
            ("locators", OrderedDict([(leadName, list(anchor))])),
        ]))
        added.append((itemBone, str(parent.get("name"))))
    if added:
        DumpJson(geoPath, data)
    return added


def MigrateHeldItemBones(geoPath, forward=0.0, legacyForward=_HELD_ITEM_LEGACY_FORWARD):
    """把本工具早先生成的物品骨骼挪到新挂点 → [(骨骼名, 父骨骼名)]。幂等。

    只认**形状完全等于生成物**的骨骼: 名字与 AddHeldItemBones 补的一致(大小写也一致)、父骨骼就是它会
    选的那根、只有 name/parent/pivot/locators 四个字段、locators 只有同位置的拴绳点、pivot 恰为父骨骼
    pivot 前推 legacyForward。创作者手写的骨骼(带 cubes/旋转/别的 pivot/别的父骨骼)一律不动。
    """
    if abs(forward - legacyForward) < 1e-9:
        return []
    data, bones = _LoadGeometryBones(geoPath)
    if bones is None:
        return []
    moved = []
    for itemBone, locatorBase, fallbacks, leadName in _HELD_ITEM_BONE_SPECS:
        boneIndex = _FindBoneIndex(bones, itemBone)
        if boneIndex < 0:
            continue
        bone = bones[boneIndex]
        parentIndex = _HeldItemParentIndex(bones, locatorBase, fallbacks)
        if parentIndex < 0 or bone.get("name") != itemBone \
                or str(bone.get("parent")) != str(bones[parentIndex].get("name")) \
                or set(bone.keys()) != set(("name", "parent", "pivot", "locators")):
            continue
        legacyAnchor = _HeldItemAnchor(bones[parentIndex], legacyForward)
        anchor = _HeldItemAnchor(bones[parentIndex], forward)
        pivot = bone.get("pivot")
        if legacyAnchor is None or not (isinstance(pivot, list) and len(pivot) == 3) \
                or [round(float(value), 5) for value in pivot] != legacyAnchor:
            continue
        locators = bone.get("locators")
        if not (isinstance(locators, dict) and list(locators.keys()) == [leadName]
                and isinstance(locators[leadName], list)
                and [round(float(value), 5) for value in locators[leadName]] == legacyAnchor):
            continue
        bone["pivot"] = anchor
        bone["locators"] = OrderedDict([(leadName, list(anchor))])
        moved.append((itemBone, str(bone.get("parent"))))
    if moved:
        DumpJson(geoPath, data)
    return moved


def EnsureHeldItemBones(rpModels):
    """两种几何的物品骨骼: 先迁移旧生成物、再补缺 → [(几何文件名, 动作, [(骨骼名, 父骨骼名)])]"""
    results = []
    for geoFile in _HELD_ITEM_GEOMETRIES:
        geoPath = os.path.join(rpModels, geoFile)
        forward = _HELD_ITEM_FORWARDS[geoFile]
        moved = MigrateHeldItemBones(geoPath, forward)
        if moved:
            results.append((geoFile, "moved", moved))
        added = AddHeldItemBones(geoPath, forward)
        if added:
            results.append((geoFile, "added", added))
    return results


def AddGeometryLocators(geoPath, locators):
    """把 locator 表打到几何文件的根骨骼上(模型空间坐标); 返回新增数。幂等。

    "根骨骼"取**模型自己的**第一根无父骨骼 —— 跳过本工具外包的滑翔根(它只在滑翔时反向旋转,
    平时恒等): locator 打在模型根上才跟着身体动, 打在外包根上粒子就不跟随了。
    """
    if not locators or not os.path.isfile(geoPath):
        return 0
    data = LoadJson(geoPath)
    geometries = data.get("minecraft:geometry") or []
    if not geometries or not isinstance(geometries[0], dict):
        return 0
    bones = geometries[0].get("bones") or []
    root = next((bone for bone in bones
                 if isinstance(bone, dict) and not bone.get("parent")
                 and bone.get("name") != GLIDE_ROOT_BONE), None)
    if root is None:
        root = next((bone for bone in bones
                     if isinstance(bone, dict) and bone.get("parent") == GLIDE_ROOT_BONE), None)
    if root is None:
        return 0
    table = root.setdefault("locators", OrderedDict())
    added = 0
    for name, position in locators.items():
        if table.get(name) != position:
            table[name] = list(position)
            added += 1
    if added:
        DumpJson(geoPath, data)
    return added


# Java 包的"每 tick 脚本"惯用法: loop:true + **显式** animation_length 0 + timeline
# —— geckolib 每帧把 0 长度的循环动画重播一遍, timeline 语句因此每帧执行(凋灵娘的
# parallel1 用它逐 tick 把 v.eye_yaw/eye_pitch 写成头部朝向, 眼球才会跟视角)。基岩对 0 长度
# 动画只在起播时触发一次 timeline(实机 2026-09-03: 眼球不跟随), 改成 0.05s(1 tick)循环
# 即每 tick 触发, 与 Java 同拍; 静态通道值不受循环影响。
# **不写长度的不算**: Java 对"无长度且无关键帧"的动画算成无限长, timeline 只跑一次
# (凋灵娘 voice_set_1..23 靠它设一次 v.voice_short=N, 语音状态 on_exit 归零后不再触发;
# 早先把这种也改成 0.05s 循环, 变量每 tick 被顶回去, 站着不动会反复重进语音状态)。
# 那一类由 ApplyJavaImpliedLength 写成 JAVA_INFINITE_LENGTH。
_TICK_TIMELINE_LENGTH = 0.05


def _MaxKeyframeStamp(body):
    """动画体里最大的关键帧时间戳(无关键帧 → 0)"""
    latest = 0.0
    for channels in (body.get("bones") or {}).values():
        if not isinstance(channels, dict):
            continue
        for value in channels.values():
            if isinstance(value, dict):
                for stamp in value:
                    try:
                        latest = max(latest, float(stamp))
                    except (TypeError, ValueError):
                        pass
    return latest


def ApplyTickTimelineLength(body):
    """loop + timeline + 显式 0 长度 → animation_length 0.05; 返回是否改写(无长度的不动, 见上注)"""
    if not isinstance(body, dict) or not body.get("timeline") or body.get("loop") is not True:
        return False
    length = body.get("animation_length")
    if not isinstance(length, (int, float)) or isinstance(length, bool) or length != 0:
        return False
    body["animation_length"] = _TICK_TIMELINE_LENGTH
    return True


# ---- 纸娃娃(界面预览)没有实体: 依赖实体的查询要绕开 ----
# 模型选择界面的纸娃娃(NeteasePaperDoll.RenderEntity)只是渲染实例, 骨骼通道里调 query.position 时
# 引擎逐帧逐骨骼报 `query.position called without an entity specified`(2026-09-17 实机: 打开"酒狐与
# 小伙伴"文件夹即刷屏, 22 精灵 pre_parallel7 蝴蝶的 position 随动)。界面给纸娃娃设了
# variable.ysm_show(列表卡片)/ variable.ysm_preview(大预览窗)= 1, 世界里的玩家从不设 —— 调用处包一层
# `(界面 ? 0 : 原调用)`: 三元只求值选中的分支, 世界里取值不变(引擎实测同值), 纸娃娃取 0(随动归零 =
# 静止摆位)。is_item_name_any 在骨骼通道同样报 "without a specified entity"(CLAUDE.md molang 三套求值
# 上下文), 一并门控; 世界里它在骨骼通道本就不可用, 门控不改变这一点。
PREVIEW_UI_GATE = "((variable.ysm_show??0)+(variable.ysm_preview??0)>0)"
_PREVIEW_ENTITY_QUERY_CALL = re.compile(
    r"\b(?:query|q)\.(?:position|position_delta|is_item_name_any)\s*\(")


def GatePreviewEntityQueries(text):
    """一个 molang 串里依赖实体的查询调用包上界面门控; 返回 (新串, 门控数)。

    幂等: 已包裹的调用(连同参数里嵌套的同类调用)原样跳过; 括号不配对的留给语法守卫。
    """
    prefix = PREVIEW_UI_GATE + "?0:"
    pieces = []
    cursor = count = skipUntil = 0
    for match in _PREVIEW_ENTITY_QUERY_CALL.finditer(text):
        if match.start() < skipUntil:
            continue
        _args, endIndex = _ScanCall(text, match.end())
        if endIndex >= len(text):
            break
        skipUntil = endIndex + 1
        if text[:match.start()].endswith(prefix):
            continue
        pieces.append(text[cursor:match.start()])
        pieces.append("(" + prefix + text[match.start():endIndex + 1] + ")")
        cursor = endIndex + 1
        count += 1
    pieces.append(text[cursor:])
    return "".join(pieces), count


def GatePreviewEntityQueriesInBody(body):
    """动画体骨骼通道 / timeline 里的全部 molang 串套 GatePreviewEntityQueries; 返回门控数"""
    count = 0
    for path, container, key, _mode in AnimationMolangSlots(body):
        if path[0] not in ("bones", "timeline"):
            continue
        newText, added = GatePreviewEntityQueries(container[key])
        if added:
            container[key] = newText
            count += added
    return count


def RewriteAnimations(srcPath, dstPath, namespace, molangDefaults=None, molangReport=None,
                      nameMapper=None, foundVars=None, additiveKeys=None, physics=None,
                      forceStateLoops=False, preKeys=None, particles=None, sounds=None,
                      previewGate=False, boneRenames=None):
    """动画文件 → 裸短名加 animation.<namespace>. 前缀 + 条件名引擎安全转义。

    physics: 包级 PhysicsRewriter(second_order/first_order → 状态积分, 键槽位跨文件共享);
    forceStateLoops: 玩家侧动画按 Java 主链运行语义改写 loop(ApplyJavaLoopSemantics),
    替换实体(弹射物/载具)的动画文件不适用。
    preKeys: pre 通道控制器状态引用的动画短键(CollectPreChannelAnimKeys), 不带 override。
    particles: 粒子汇 {"effects", "locators"}(ConvertTimelineParticles); None = 不转粒子。
    sounds: 音频汇 SoundSink(ConvertSoundKeyframes); None = 不转音频关键帧。
    previewGate: 玩家侧动画(纸娃娃会播)给依赖实体的查询包界面门控(GatePreviewEntityQueries)。
    boneRenames: {原骨骼名: 新骨骼名}(大小写不敏感匹配), 用于第一人称手臂动画 ——
    arm 几何重建时与原版动画撞名的 Java 骨骼被改名(见 BuildFirstPersonArmGeometry)。
    返回 (动画数, 跳过的分组标题, (向量展开, 语句规范化, loop 改写, 物理改写, 空节点清理), ID 表)。

    转义是必须的: 基岩动画解析器拒绝 ID 里的 $ 与 :, 且**一个非法 ID 会让整份
    文件的 animations 段解析失败**(实测 "node parse failed: animations"), 导致
    该文件全部动画丢失。规则与主包解析器共用(packParser.EscapeConditionKey)。
    同理需要规范化 geckolib 的标量通道值(见 _ExpandVector)。
    """
    data = LoadJson(srcPath)
    animations = data.get("animations")
    if not isinstance(animations, dict):
        return 0, [], (0, 0, 0, 0, 0, 0), []
    # geckolib 专有字段基岩不识别("child not valid here" 会连锁作废整份文件)
    data.pop("geckolib_format_version", None)
    renamed = OrderedDict()
    keys = []
    dropped = []
    vectorFixes = 0
    stmtFixes = 0
    lerpFixes = 0
    loopFixes = 0
    physicsFixes = 0
    sanitizeFixes = 0

    def _Note(label, count=1):
        if molangReport is not None and count:
            molangReport[label] += count

    for name in animations:
        if IsDecorativeKey(name, animations[name]):
            dropped.append(name)
            continue
        # py2: 动画名可能含中文(Java 允许), 模板必须是 unicode 否则拼接按 ascii 解码崩溃
        shortKey = name if name.startswith("animation.") else (
            nameMapper.Convert(name) if nameMapper is not None else EscapeConditionKey(name))
        newName = shortKey if name.startswith("animation.") \
            else u"animation.{}.{}".format(namespace, shortKey)
        # 引擎红线: 动画 ID 等资源 key 不能含英文大写(EscapeConditionKey 已统一小写,
        # 骨骼名不受限 —— 引擎按大小写不敏感匹配骨骼)。此处只留痕供人工核对 ysm.json
        # 里对该键的引用(extra_animation 等)是否需要同步。
        if molangReport is not None and name != name.lower() \
                and not name.startswith("animation."):
            molangReport[u"lower:{} -> {}".format(name, name.lower())] += 1
        body = animations[name]
        if isinstance(body, dict):
            _Note(u"norm:第一人称手臂动画的骨骼名按重建后的 arm 几何改名",
                  RenameAnimationBones(body, boneRenames))
            # ---- Java 解析器层面的字段语义(pojo Animation.Adapter)先归一 ----
            if NormalizeLoopField(body):
                _Note(u"norm:loop 字段按 Java LoopType.fromJson 归一(字符串/未知值)")
            _Note(u"norm:删掉 Java 运行时忽略的字段(anim_time_update/start_delay/loop_delay)",
                  DropJavaIgnoredAnimationFields(body))
            _Note(u"norm:timeline 里的字符串字面量语句(Java 当注释)删除", StripStringStatements(body))
            # 物理函数先改写: 产出的是带 return 的复杂表达式, 后面的语句规范化据此放行
            if physics is not None:
                physicsFixes += physics.RewriteTree(body)
            channelFixes, statementFixes = _NormalizeBoneChannels(body)
            vectorFixes += channelFixes
            stmtFixes += statementFixes
            # Java 样条分段语义(区间任一端 catmullrom) → 基岩出边语义; 只在这里跑一次,
            # 必须在封头/封尾之前(补的帧是 linear, 不参与换算)
            _Note(u"norm:catmullrom 分段语义 Java(任一端) -> 基岩(出边)换算",
                  ApplyJavaCatmullSegments(body))
            # 表达式关键帧形态规范化(见 NormalizeExpressionKeyframes 长注)
            # 必须排在 SealAnimationTails 之前: 后者补的收尾帧本就写 linear
            lerpFixes += NormalizeExpressionKeyframes(body)
            # animation_length 缺省 → Java 推导值(末关键帧 / 无限长), 排在封尾之前
            implied = ApplyJavaImpliedLength(body)
            if implied == "keyframes":
                _Note(u"norm:animation_length 缺省 -> 末关键帧时间(Java calculateLength)")
            elif implied == "infinite":
                _Note(u"norm:animation_length 缺省且无关键帧 -> 无限长"
                      u"(Java Float.MAX_VALUE: 永不结束, timeline 只跑一次)")
            _Note(u"norm:首帧晚于 0 的通道补首帧保持帧(Java 首帧前输出首帧 pre)", SealAnimationHeads(body))
            SealAnimationTails(body)
            if _ShouldOverridePrevious(shortKey, additiveKeys, preKeys):
                body["override_previous_animation"] = True
            else:
                body.pop("override_previous_animation", None)
            if forceStateLoops and ApplyJavaLoopSemantics(shortKey, body):
                loopFixes += 1
            if ApplyTickTimelineLength(body):
                _Note(u"tick:每 tick 脚本动画(loop+显式 0 长度+timeline) -> 0.05s 循环")
            _Note(u"norm:timeline 超出 animation_length 的条目按 Java 语义处理"
                  u"(循环/单次挪到结尾补跑, hold 删除)", ClampTimelineToLength(body))
            if particles is not None:
                ConvertTimelineParticles(body, particles, molangReport, shortKey)
            if sounds is not None:
                _Note(u"map:sound_effects 关键帧 -> 注册效果键"
                      u"(拷 ogg + sound_definitions + files.player.sound_effect)",
                      ConvertSoundKeyframes(body, sounds))
            sanitizeFixes += SanitizeAnimationBody(body)
        renamed[newName] = body
        keys.append(newName)
    data["animations"] = renamed
    # molang 兼容层作用于序列化后的完整文本(表达式散落在各级键帧值里)
    finalText = PortMolangText(
        json.dumps(data, ensure_ascii=False, indent=2), molangDefaults, molangReport)
    # molang 兼容层会把**纯数值**帧里的 Java token 换成表达式
    # (`head_yaw` → `(-query.mod.ysm_head_yaw)` 等), 于是上面那轮
    # NormalizeExpressionKeyframes 看到的“纯数值帧”到这里又变成了表达式帧 ——
    # 邻域判定必须在替换**之后**再跑一遍, 否则产物里会残留一批
    # “catmullrom 挨着表达式”(实测 ref_warden 重移植: 残 18 处, 靠修复工具才补上)
    reparsed = json.loads(finalText, object_pairs_hook=OrderedDict)
    postFixes = postVector = postStmt = gateFixes = guardFixes = precedenceFixes = 0
    for animId, postBody in (reparsed.get("animations") or {}).items():
        if not isinstance(postBody, dict):
            continue
        postFixes += NormalizeExpressionKeyframes(postBody)
        # 同理: 替换后才出现的标量/语句形态(标量通道展开为向量、补 return)
        channelFix, stmtFix = _NormalizeBoneChannels(postBody)
        postVector += channelFix
        postStmt += stmtFix
        if previewGate:
            gateFixes += GatePreviewEntityQueriesInBody(postBody)
        # 最后一道: 基岩解析不了的表达式按 Java 口径落 0 / 删除 —— 一个坏值不再拖垮整份文件
        # (见 devtools/molang_syntax.py 注)。排在全部改写之后, 判的是最终落盘形态
        for path, badText, problem in GuardAnimationMolang(postBody):
            guardFixes += 1
            _Note(u"zero:基岩解析不了的表达式按 Java 口径置 0 / 删除(Java 同样解析失败): "
                  u"{} {}: {} <- {}".format(animId.split(".", 2)[-1], FormatSlotPath(path),
                                            problem, badText[:60]))
        # 资源包文件按旧版 Molang 语义解析(三元左结合、&& 不比 || 紧): 两种语义可能分叉处补括号
        precedenceFixes += len(ExplicitAnimationPrecedence(postBody))
    _Note(u"map:纸娃娃无实体的查询(position / position_delta / is_item_name_any) -> 界面门控取 0",
          gateFixes)
    _Note(u"map:新旧 Molang 语义可能分叉处补括号(资源包按 min_engine_version 1.18.0 走旧语义)",
          precedenceFixes)
    if postFixes or postVector or postStmt or gateFixes or guardFixes or precedenceFixes:
        lerpFixes += postFixes
        vectorFixes += postVector
        stmtFixes += postStmt
        finalText = json.dumps(reparsed, ensure_ascii=False, indent=2)
    if foundVars is not None:
        foundVars.update(_MOLANG_VAR_SCAN.findall(finalText))
    WriteBytes(dstPath, finalText.encode("utf-8") + b"\n")
    return len(keys), dropped, (vectorFixes, stmtFixes, loopFixes, physicsFixes,
                                sanitizeFixes, lerpFixes), keys


def CopyBinary(srcPath, dstPath):
    EnsureDir(os.path.dirname(dstPath))
    shutil.copyfile(srcPath, dstPath)


# ---- 中文标识符 → 拼音(基岩标识符只收 ASCII) ----
# 实机证据(2026-08 引擎日志): 控制器状态名含中文 → "child '散热开始' not valid here",
# 该控制器整个失效; 动画 ID 含中文同理, 且**动画是整份文件静默作废**(见红线表),
# 一个中文 ID 能带走同文件全部动画。故转换期统一转拼音。
try:
    from pypinyin import lazy_pinyin as _LazyPinyin
except ImportError:                                   # 未装库时降级(仍保证 ASCII 化)
    _LazyPinyin = None

_CJK_RUN_PATTERN = re.compile(u"[^\u0000-\u007f]+")
_STATE_SAFE_PATTERN = re.compile(r"[^0-9A-Za-z_.]")

# 产物文本里的 molang 变量引用(v.xxx / variable.xxx 短名), 供预览实体初始化收集。
# 与 packParser._MOLANG_VAR_PATTERN 同一判定(\b 防 uv.xxx 误匹配)。
_MOLANG_VAR_SCAN = re.compile(r"\b(?:variable|v)\.([A-Za-z_][A-Za-z0-9_]*)")
# 预览实体定义自带的 GUI 状态变量(主包 UI 经 molang 绑定覆写), 不参与包变量收集
_ENTITY_BASE_VARS = ("ysm_skin", "ysm_gui", "ysm_show", "ysm_preview", "ysm_light")


def Pinyinize(text):
    """把文本里的非 ASCII 段落转成拼音, ASCII 部分原样保留。

    未装 pypinyin 时退化为 "u<码点>" 形式 —— 可读性差但仍唯一且合法, 且会在
    转换汇总里留痕提醒装库。
    """
    if not isinstance(text, unicode):                 # noqa: F821 — py2
        text = text.decode("utf-8")

    def _Convert(match):
        run = match.group(0)
        converted = "".join(_LazyPinyin(run)) if _LazyPinyin is not None else run
        # pypinyin 只认汉字, 其余非 ASCII(假名/西欧变音/emoji)原样退回 —— 统一兜底,
        # 保证返回值一定是纯 ASCII, 否则引擎照样判非法
        return "".join(
            ch if ord(ch) < 128 else "u{:04x}".format(ord(ch)) for ch in converted)

    return _CJK_RUN_PATTERN.sub(_Convert, text)


# 动画短键在资源 ID 里只认 [a-z0-9_.-](引擎红线, validate_rp_animations._RESOURCE_ID_OK):
# 含其他字符的 ID 让**整份**动画文件解析失败。Java 动画名是任意字符串, 官方内置酒狐系实测:
# 引号包裹的 BetterCombat 名("one_handed_stab")、首尾空格( huhu_end_rod / zuozi )、
# #run#、"idle to fight"、"A Cup Of Liber-Tea"。
_ANIM_KEY_UNSAFE = re.compile(r"[^a-z0-9_.-]+")
# 首段是 Java molang 命名空间的短键: 动画/控制器文件整文本过 PortMolangText 时会被当成
# ctrl.* 残留置零(14_momo 的 ctrl:small_huhu → ctrl.cls.small_huhu → "0.0", 三个动画撞成一个)
# —— 前缀下划线破掉词边界
_MOLANG_ROOT_SEGMENT = re.compile(
    r"^(?:ysm|ctrl|fn|tlm|args|query|q|math|variable|v|temp|t|context|c|geometry|texture|material)[.]")


def SafeAnimationKey(key):
    """转义后的动画短键 → 资源 ID 合法形态(去首尾空白/引号, 非法字符折成下划线, 挡住 molang 根前缀)"""
    key = key.strip().strip(u"\"'").strip()
    key = _ANIM_KEY_UNSAFE.sub("_", key).strip(".")
    if _MOLANG_ROOT_SEGMENT.match(key):
        key = "_" + key
    return str(key) if key else "anim"


class AsciiNameMapper(object):
    """原名 → 基岩安全标识符。同名恒得同结果; 不同原名撞车时自动加后缀。

    动画名走 EscapeConditionKey(条件动画的 $ / : / # 转义 + 统一小写),
    控制器状态名只需 ASCII 化(它不是资源 ID, 大小写不敏感问题不存在)。
    """

    def __init__(self, escape):
        self._escape = escape
        self._mapping = {}
        self._taken = {}
        self.converted = []                            # [(原名, 新名)] 供汇总留痕

    def Lookup(self, rawName):
        """只查不建: 已改名返回新名, 未登记过原样返回"""
        return self._mapping.get(rawName, rawName)

    def Convert(self, rawName):
        if rawName in self._mapping:
            return self._mapping[rawName]
        candidate = SafeAnimationKey(self._escape(Pinyinize(rawName)))
        base = candidate
        index = 2
        while self._taken.get(candidate, rawName) != rawName:
            candidate = "{}_{}".format(base, index)    # 异名同音: 加序号区分
            index += 1
        self._mapping[rawName] = candidate
        self._taken[candidate] = rawName
        if candidate != rawName:
            self.converted.append((rawName, candidate))
        return candidate


def _StateSafe(name):
    return _STATE_SAFE_PATTERN.sub("_", Pinyinize(name)) or "state"


def _FlattenRoamingStrings(node):
    """容器内全部字符串值的 v.roaming.* → v.roaming_*(原地), 返回改写处数。

    与 PortMolangText 的 ① 同一条规则(_ROAMING_PATTERN), 供 ysm.json 的
    properties 子树(config_forms 的 value/labels 等 molang 字符串)同步改写。
    只动值不动键 —— dict 键是显示名/动画短名, 不是 molang。
    """
    count = 0
    if isinstance(node, dict):
        for key in list(node.keys()):
            value = node[key]
            if isinstance(value, (str, unicode)):  # noqa: F821
                replaced, n = _ROAMING_PATTERN.subn(r"\1.roaming_\2", value)
                if n:
                    node[key] = replaced
                    count += n
            else:
                count += _FlattenRoamingStrings(value)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, (str, unicode)):  # noqa: F821
                replaced, n = _ROAMING_PATTERN.subn(r"\1.roaming_\2", value)
                if n:
                    node[index] = replaced
                    count += n
            else:
                count += _FlattenRoamingStrings(value)
    return count


# ---- Java 多语言(files.language_path, 缺省 lang/) → 显示文本烘进 ysm.json ----
# 网易版界面只有中文: Java LanguageManager.getI18n(键, 默认值) 在中文客户端的实际显示就是
# zh_cn 值, 缺键回落 ysm.json 原文。键族(AnimationRouletteScreen / AuthorButton / ModelRenderTarget):
#   metadata.name / metadata.tips / metadata.authors.<i>.name|role|comment
#   properties.extra_animation.<键>            根轮盘与各分类里同键同译; 值为 "#按钮id" 时译名顶替按钮名显示
#   properties.extra_animation_buttons.<id>.config_forms.<i>.title|description|labels.<j>
# 不烘: properties.extra_animation.<键>.desc(网易轮盘无悬浮说明)、files.player.texture.<名>(皮肤显示名无字段)
# 官方内置包 03_astronaut 的轮盘值全空、只靠语言表给名字 —— 不烘就是一个空轮盘。
JAVA_LANG_LOCALE = "zh_cn"


def LoadJavaLanguage(manifest, javaDir, locale=JAVA_LANG_LOCALE):
    """Java 包的语言表 {键: 文本}; 没有语言文件返回空 dict"""
    langDir = (((manifest or {}).get("files") or {}).get("language_path")) or "lang"
    path = os.path.join(javaDir, str(langDir).replace("/", os.sep), "{}.json".format(locale))
    if not os.path.isfile(path):
        return {}
    data = LoadJson(path)
    return data if isinstance(data, dict) else {}


def ApplyJavaLanguage(manifest, lang):
    """语言表文本原地烘进 manifest 的显示字段, 返回改写处数(已是译文的不计)。

    轮盘条目的值在网易侧带语义("#按钮id" = 配置按钮引用), 普通条目的译名若以 # 开头会被
    主包误读成按钮引用 —— 换成全角 ＃ 保住显示。
    """
    count = [0]

    def _Text(key):
        value = lang.get(key)
        if isinstance(value, (str, unicode)) and value != "":  # noqa: F821
            return value
        return None

    def _Set(node, field, text):
        if text is not None and node.get(field) != text:
            node[field] = text
            count[0] += 1

    metadata = manifest.get("metadata")
    if isinstance(metadata, dict):
        _Set(metadata, "name", _Text("metadata.name"))
        _Set(metadata, "tips", _Text("metadata.tips"))
        for index, author in enumerate(metadata.get("authors") or []):
            if isinstance(author, dict):
                for field in ("name", "role", "comment"):
                    _Set(author, field, _Text("metadata.authors.{}.{}".format(index, field)))
    properties = manifest.get("properties")
    if not isinstance(properties, dict):
        return count[0]
    buttons = [button for button in (properties.get("extra_animation_buttons") or [])
               if isinstance(button, dict) and button.get("id")]
    buttonMap = dict((button["id"], button) for button in buttons)
    namedButtons = set()

    def _Labels(animDict):
        if not isinstance(animDict, dict):
            return
        for key in list(animDict.keys()):
            value = animDict[key]
            text = _Text(u"properties.extra_animation.{}".format(key))
            if text is None or not isinstance(value, (str, unicode)):  # noqa: F821
                continue
            buttonId = value[1:] if value.startswith("#") else None
            if not key.startswith("#") and buttonId in buttonMap:
                if buttonId not in namedButtons:      # 同一按钮被多处引用时认第一处
                    namedButtons.add(buttonId)
                    _Set(buttonMap[buttonId], "name", text)
                continue
            if not key.startswith("#") and text.startswith("#"):
                text = u"＃" + text[1:]
            if value != text:
                animDict[key] = text
                count[0] += 1

    _Labels(properties.get("extra_animation"))
    for classify in properties.get("extra_animation_classify") or []:
        if isinstance(classify, dict):
            _Labels(classify.get("extra_animation"))
    for button in buttons:
        for formIndex, form in enumerate(button.get("config_forms") or []):
            if not isinstance(form, dict):
                continue
            prefix = u"properties.extra_animation_buttons.{}.config_forms.{}.".format(
                button["id"], formIndex)
            _Set(form, "title", _Text(prefix + "title"))
            _Set(form, "description", _Text(prefix + "description"))
            labels = form.get("labels")
            if not isinstance(labels, dict) or not labels:
                continue
            renamed = OrderedDict()
            for labelIndex, labelKey in enumerate(list(labels.keys())):
                newKey = _Text(u"{}labels.{}".format(prefix, labelIndex)) or labelKey
                if newKey in renamed:           # 译名撞键: 保原名, 不能丢选项
                    newKey = labelKey if labelKey not in renamed else u"{} ({})".format(
                        labelKey, labelIndex)
                renamed[newKey] = labels[labelKey]
            if list(renamed.keys()) != list(labels.keys()):
                form["labels"] = renamed
                count[0] += 1
    return count[0]


# Java 通道名 → 该通道在网易侧对应的"直接播放"动画键。Java 里声明了
# player.parallel_2 控制器 = 该通道改由控制器决定播什么, 不再自动播 parallel2
# (ParallelControllerDiscovery: 显式控制器与动画名自动生成的控制器同名互斥)。
# 网易侧无通道概念, 控制器与动画都是常开条目 —— 不摘掉直播条目就会两处同时播,
# 基岩逐通道相加、Java 除 parallel 外后者覆盖前者(见 _ShouldOverridePrevious), 双驱会把差异放大。
_CHANNEL_TAKEOVER_PATTERN = re.compile(r"^player\.(pre_parallel|parallel)_(\d+)$")
# 控制器状态里被引用的 parallel/pre_parallel 键同样计入接管(野外包惯例: 一个
# player.parallel_0 控制器的状态直接铺满 parallel0-7 —— 只按控制器名摘 parallel0
# 会让 1-7 直播+控制器双驱, 实机即"动作鬼畜"的成因之一)
_PARALLEL_SHORT_PATTERN = re.compile(r"^(?:pre_)?parallel\d+$")

# 控制器死引用剪枝的兜底放行名单: 这些键即便本包动画文件里没有, 也可能由
# java_default 官方基线(javaMode 缺键回落)或并行空壳提供, 不能当死引用剪掉。
# 条件动画家族(.cls./.id./.tag. 转义形态)同理放行(基线含 57 条手持条件动画)。
_BASELINE_FALLBACK_PATTERN = re.compile(
    r"^(?:(?:pre_)?parallel\d+|extra\d+|swing_hand|use_mainhand|use_offhand"
    r"|idle|walk|run|jump|sneak|sneaking|sneak_arm|sneaking_arm|swim|swim_stand"
    r"|sit|ride|ride_pig|boat|sleep|fly|elytra_fly|climb|climbing|death|attacked"
    r"|riptide|ladder_up|ladder_down|ladder_stillness)$")


def _IsResolvableAnimRef(key, knownAnimKeys):
    """控制器状态动画引用是否可解析(注册表可命中/基线兜底可命中)"""
    if not isinstance(key, (str, unicode)):  # noqa: F821
        return True
    if key.startswith("animation.") or key.startswith("controller.animation."):
        return True  # 完整资源 ID 直引, 存在性交给主包坏引用过滤
    if knownAnimKeys is None or key in knownAnimKeys:
        return True
    if _BASELINE_FALLBACK_PATTERN.match(key):
        return True
    return ".cls." in key or ".id." in key or ".tag." in key


def _ControllerRegisterName(rawName):
    """Java 控制器名 → 基岩安全的注册段(小写, 只留 [a-z0-9_])"""
    safe = re.sub(r"[^0-9a-zA-Z_]", "_", Pinyinize(rawName)).lower()
    return re.sub(r"_+", "_", safe).strip("_") or "controller"


def _RewriteControllerAnimRefs(node, nameMapper=None):
    """控制器 states.*.animations 的动画引用 → 与动画文件同一套改名规则。

    必须与 RewriteAnimations 共用同一个 nameMapper, 否则控制器会引用改名前的
    短键(实机表现: 该动画不播, 且没有任何报错)。
    """
    def _Rename(value):
        # py2 红线: LoadJson 产出的是 unicode, isinstance(v, str) 恒 False ——
        # 用 str 判断会让改名整段静默跳过(实测: 状态名改了、动画引用没改)
        if not isinstance(value, (str, unicode)):  # noqa: F821
            return value
        if not value or value.startswith("animation."):
            return value
        if nameMapper is not None:
            return nameMapper.Convert(value)
        return EscapeConditionKey(value)

    fixes = 0
    if isinstance(node, list):
        for index, item in enumerate(node):
            if isinstance(item, (str, unicode)):  # noqa: F821
                renamedItem = _Rename(item)
                if renamedItem != item:
                    node[index] = renamedItem
                    fixes += 1
            elif isinstance(item, dict):
                renamed = OrderedDict()
                for key, value in item.items():
                    newKey = _Rename(key)
                    if newKey != key:
                        fixes += 1
                    renamed[newKey] = value
                node[index] = renamed
    return fixes


def _RewriteControllerStates(body, nameMapper=None):
    """状态名 ASCII 化, 同步改写 initial_state 与全部 transitions 目标。

    实机证据: 状态名含中文 → "child '散热开始' not valid here", 整个控制器失效。
    states 的键与 transitions 的目标是同一命名域, 必须一起改, 漏一处状态机就断链。
    """
    states = body.get("states")
    if not isinstance(states, dict):
        return body
    rename = OrderedDict()
    taken = set()
    for name in states:
        safe = _StateSafe(name)
        base, index = safe, 2
        while safe in taken:
            safe = "{}_{}".format(base, index)
            index += 1
        taken.add(safe)
        rename[name] = safe
    if all(key == value for key, value in rename.items()):
        return body
    body["states"] = OrderedDict((rename[k], states[k]) for k in states)
    initial = body.get("initial_state")
    if initial in rename:
        body["initial_state"] = rename[initial]
    for state in body["states"].values():
        if not isinstance(state, dict):
            continue
        transitions = state.get("transitions")
        if not isinstance(transitions, list):
            continue
        for pos, entry in enumerate(transitions):
            if isinstance(entry, dict):
                transitions[pos] = OrderedDict(
                    (rename.get(k, k), v) for k, v in entry.items())
    return body



def _PruneControllerBody(body, knownAnimKeys, prunedRefs, prunedTransitions,
                         emptiedStates=None):
    """单个控制器体的引用剪枝(状态改名之后调用):

    - states.*.animations 里指向"哪都不存在"的动画短键剪掉 —— Java 对缺失引用
      静默容忍(AnimationStore 查无即跳过), 基岩引擎则每次渲染重建逐条刷
      "can't find animation <键>"(实机: wutoufapiaodong/chijiandaiji 族);
    - transitions 里目标状态未定义(missing_state 之类作者残留)或条件为空白的
      条目剪掉 —— 引擎侧此类转移非法, 有连坐整个控制器失效的风险。

    **空列表必须删键, 不能留 `"animations": []`**(2026-09 实机二分定位):
    网易引擎对"状态里有显式空 animations 数组"的动画控制器文件**整份拒绝加载**
    —— 该文件内所有控制器的 AddPlayerAnimationController 全部返回 False, 与
    ID 不存在同表现, 且不打任何日志。实机证据: 同为转换产物, ref_sahmet(0 处
    空数组)正常加载, ref_warden(15 处)/ref_wither(13 处)整份失效, 后果是包自带
    的 pre_parallel 装饰件隐藏动画永不播放 → 玩家身上糊满配件模型。最小复现:
    单控制器单状态 `{"animations": []}` 即拒载; 改成"不写 animations 键"则正常。
    transitions 空数组实测可容忍(ref_sahmet 有 1 处), 但同属剪枝残留, 一并删键。
    """
    states = body.get("states")
    if not isinstance(states, dict):
        return
    for state in states.values():
        if not isinstance(state, dict):
            continue
        animations = state.get("animations")
        if isinstance(animations, list):
            kept = []
            for item in animations:
                if isinstance(item, dict):
                    keptDict = OrderedDict(
                        (k, v) for k, v in item.items()
                        if _IsResolvableAnimRef(k, knownAnimKeys))
                    for k in item:
                        if k not in keptDict:
                            prunedRefs.append(k)
                    if keptDict:
                        kept.append(keptDict)
                    continue
                if _IsResolvableAnimRef(item, knownAnimKeys):
                    kept.append(item)
                else:
                    prunedRefs.append(item)
            if kept:
                state["animations"] = kept
            else:
                # 空数组是"整份文件拒载"的硬雷(见函数注释), 必须删键
                del state["animations"]
                if emptiedStates is not None:
                    emptiedStates.append("animations")
        transitions = state.get("transitions")
        if isinstance(transitions, list):
            keptTransitions = []
            for entry in transitions:
                if not isinstance(entry, dict):
                    keptTransitions.append(entry)
                    continue
                keptEntry = OrderedDict()
                for target, condition in entry.items():
                    if isinstance(condition, (int, float)):
                        conditionText = repr(condition)      # 数字条件是合法 molang
                    elif isinstance(condition, (str, unicode)):  # noqa: F821
                        conditionText = condition
                    else:
                        conditionText = ""
                    if target not in states or not conditionText.strip():
                        prunedTransitions.append(target)
                        continue
                    keptEntry[target] = condition
                if keptEntry:
                    keptTransitions.append(keptEntry)
            if keptTransitions:
                state["transitions"] = keptTransitions
            else:
                del state["transitions"]
                if emptiedStates is not None:
                    emptiedStates.append("transitions")


def _CollectParallelRefs(body):
    """控制器体内被引用的 parallel/pre_parallel 直播键(通道接管扩展判据)"""
    referenced = []
    for state in (body.get("states") or {}).values():
        if not isinstance(state, dict):
            continue
        for item in state.get("animations") or []:
            keys = item.keys() if isinstance(item, dict) else [item]
            for key in keys:
                if isinstance(key, (str, unicode)) \
                        and _PARALLEL_SHORT_PATTERN.match(key):  # noqa: F821
                    referenced.append(str(key))
    return referenced


def _TransitionItems(entry):
    """转移条目 {目标: 条件} → [(目标, 条件)](多键条目按序展开)"""
    if not isinstance(entry, dict):
        return []
    return [(target, condition) for target, condition in entry.items()]


def _NegatedCore(condition):
    """条件形如 !(A) / !A → 返回 A(去掉一层包裹括号); 其他形态返回 None"""
    text = (condition or "").strip()
    if not text.startswith("!"):
        return None
    core = text[1:].strip()
    if core.startswith("(") and core.endswith(")"):
        depth = 0
        for index, ch in enumerate(core):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and index != len(core) - 1:
                    return core   # 括号不是整体包裹, 原样
        core = core[1:-1]
    return core or None


def _RequiresPositively(text, core):
    """text 里是否出现了未被 ! 否定的 core(前面越过若干 '(' 后不是 '!')"""
    start = 0
    while True:
        index = text.find(core, start)
        if index < 0:
            return False
        cursor = index - 1
        while cursor >= 0 and text[cursor] in "( ":
            cursor -= 1
        if cursor < 0 or text[cursor] != "!":
            return True
        start = index + len(core)


def BypassEmptyHubStates(body):
    """作者状态机的空中转状态(hub: 无 animations、无 on_entry/on_exit、有出边)旁路;
    返回新增的旁路转移数。幂等(旁路条目带标记键 "//ysm_bypass" 以外无法区分, 故用
    "已存在同目标同条件的转移"判重)。

    Java geckolib 切换状态时从**当前姿态快照**插值到新动画, 经过一个空状态只多花 1 tick,
    姿态连续; 基岩控制器的交叉淡化是"出态动画权重 1→0 + 入态动画权重 0→1", 出态进空 hub
    时入态没有动画, 姿态先淡到**绑定姿态**再由 hub 淡进下一状态 —— 凋灵娘落地
    (jump_down → cache → idle)一瞬间直立(用户实测 2026-09-03)。
    做法: 对每条 X→H(条件 cx), 在它之前插入 X→Y(条件 (cx)&&(cy)), Y 取 H 的每条出边
    (顺序即 H 的优先级); Y==X 的自环不插(重进状态会重播动画); H 出边条件含
    all_animations_finished(相对 H 自己的动画)的不插。X→H 原条目保留作兜底。
    """
    states = body.get("states") if isinstance(body, dict) else None
    if not isinstance(states, dict):
        return 0
    hubs = {}
    for name, state in states.items():
        if not isinstance(state, dict):
            continue
        # 占用变量赋值(ApplyChannelOwnership)不算作者脚本: 只写它的状态照样是空中转
        onEntry = state.get("on_entry")
        if isinstance(onEntry, list):
            onEntry = [line for line in onEntry if not _IsOwnershipStatement(line)]
        if state.get("animations") or onEntry or state.get("on_exit"):
            continue
        outgoing = []
        for entry in state.get("transitions") or []:
            for target, condition in _TransitionItems(entry):
                if isinstance(condition, (str, unicode)) and "all_animations_finished" in condition:  # noqa: F821
                    continue
                outgoing.append((target, condition))
        if outgoing:
            hubs[name] = outgoing
    if not hubs:
        return 0
    added = 0
    for name, state in states.items():
        if not isinstance(state, dict) or name in hubs:
            continue
        transitions = state.get("transitions")
        if not isinstance(transitions, list):
            continue
        existing = set()
        for entry in transitions:
            for target, condition in _TransitionItems(entry):
                existing.add((target, condition))
        rebuilt, hubEntries = [], []
        for entry in transitions:
            items = _TransitionItems(entry)
            for target, condition in items:
                if target in hubs and isinstance(condition, (str, unicode)):  # noqa: F821
                    negatedCore = _NegatedCore(condition)
                    for hubTarget, hubCondition in hubs[target]:
                        if hubTarget == name or hubTarget not in states \
                                or not isinstance(hubCondition, (str, unicode)):  # noqa: F821
                            continue
                        # 目标本身也是空状态(作者剪掉死引用后变空的一串状态)不插: 旁路进空状态
                        # 没有收益, 且下一遍会把复合转移再展开一层, 链式增长(幂等性守护逮到)
                        if hubTarget in hubs:
                            continue
                        # X→hub 的条件是 !A 而 hub 出边要求 A(如落地后 → jump_up), 或反过来
                        # (进 hub 要求飞行, 出边是 !飞行): 恒假, 不插
                        if negatedCore and _RequiresPositively(hubCondition, negatedCore):
                            continue
                        hubNegatedCore = _NegatedCore(hubCondition)
                        if hubNegatedCore and _RequiresPositively(condition, hubNegatedCore):
                            continue
                        # X 自己已有同目标同条件的直达转移(在前面先判): 旁路是纯冗余
                        if (hubTarget, hubCondition) in existing:
                            continue
                        composite = u"({})&&({})".format(condition, hubCondition)
                        if (hubTarget, composite) in existing:
                            continue
                        existing.add((hubTarget, composite))
                        rebuilt.append(OrderedDict([(hubTarget, composite)]))
                        added += 1
            # 指向空中转状态的转移一律排到**最后**: 它是"其他都不匹配"的兜底。基岩按列表
            # 顺序取第一个条件成立的转移, 原序里作者常把兜底写在中间(凋灵娘 idle 的
            # →cache 排在 →jump_up 之前) —— 起跳时 cache 先命中, 于是"起跳一瞬间直立"。
            (hubEntries if any(t in hubs for t, _c in items) else rebuilt).append(entry)
        newTransitions = rebuilt + hubEntries
        if newTransitions != transitions:
            state["transitions"] = newTransitions
            added = added or 1     # 仅重排也要落盘
    return added


# ---- Java 控制器语义(AnimationProtoMapper / BedrockAnimationController)与基岩的差异 ----
# Java 只把**通道名**匹配的控制器挂到实体上(PlayerControllerCollection / FPArmControllerCollection
# 的 simple/multi/parallel/armor 发现规则): 不在此列的控制器(builtin 16_tactics 注释掉的
# "#player.post_main2")Java 根本不加载; 主包会把文件里每个控制器都挂成常开条目, 移植期同样跳过。
_JAVA_CHANNEL_CONTROLLER_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"^player\.(?:pre_parallel|parallel)_.+$",
    r"^player\.(?:pre_main|post_main|pre_hold|post_hold|pre_swing|post_swing|pre_use|post_use)(?:_.+)?$",
    r"^player\.(?:vehicle|main|hold_offhand|hold_mainhand|fire|swing|use|passenger|carry_on|parcool)$",
    r"^player\.armor_(?:head|chest|legs|feet|mainhand|offhand)$",
    r"^fp\.arm\.(?:misc|parallel_.+|armor_(?:head|chest|legs|feet|mainhand|offhand))$",
))
# Java 跨态过渡是四元数 nlerp(BlendBoneAnimationQueue → MathUtil.lerpRotationValues), 天然走
# 最短路径且不看 blend_via_shortest_path 字段; 基岩缺省逐分量插值, 该字段为 true 才是最短路径
_JAVA_SHORTEST_PATH_BLEND = True
_BOOLEAN_EXPRESSION_HINT = re.compile(r"==|!=|<|>|&&|\|\||!|\?")
_NUMBER_LITERAL = re.compile(r"^-?\d+(?:\.\d+)?$")
# Java pojo State 解析了、AnimationProtoMapper 却传空/不读的状态级字段(基岩会执行)
_JAVA_DROPPED_STATE_KEYS = ("sound_effects", "particle_effects")


def IsJavaChannelControllerName(rawName):
    """控制器名是否会被 Java 挂到某条通道上(已是基岩注册名 controller.animation.* 视为放行)"""
    name = str(rawName)
    if name.startswith("controller.animation."):
        return True
    return any(pattern.match(name) for pattern in _JAVA_CHANNEL_CONTROLLER_PATTERNS)


def _NumericBlend(value):
    """blend_transition 取值 → 秒: 数字原样(负数归 0); 对象形态(Java SegmentedBlendTransition
    点集曲线, 基岩只认数字)取最大时间键 = 曲线总长(Blockbench 导入同一处理); 其他 → 0"""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else 0.0
    if isinstance(value, dict):
        times = []
        for key in value:
            try:
                times.append(float(key))
            except (TypeError, ValueError):
                continue
        return max(times) if times else 0.0
    return 0.0


def _JavaApplyCondition(expression):
    """状态里 {动画: 表达式} 在 Java 是**布尔 apply 条件**(BedrockAnimationController.ConditionHolder
    → evalAsBoolean: 非零即播且全权重), 基岩是**混合权重**。布尔式两边同值, 非布尔式包成 `!=0`;
    数字字面量按 Java: 非零 = 恒播(去掉条件), 零 = 恒不播。返回 (新表达式 | None=恒播, 是否改写)"""
    if isinstance(expression, bool):
        return (None if expression else "0"), True
    if isinstance(expression, (int, float)):
        return (None if expression != 0 else "0"), True
    if not isinstance(expression, (str, unicode)):  # noqa: F821
        return expression, False
    text = expression.strip()
    if not text:
        return None, True                        # Java: 空白条件 = 恒播
    if _NUMBER_LITERAL.match(text):
        if float(text) != 0:
            return None, True
        return "0", expression != "0"            # 已是 "0" 则不算改写(幂等)
    if _BOOLEAN_EXPRESSION_HINT.search(text):
        return expression, False
    return "(({}))!=0".format(text), True


def NormalizeJavaControllerStates(body):
    """控制器状态按 Java 解析器的实际语义规范化(幂等); 返回改动数。

    - blend_transition 对象形态 → 数字(曲线总长);
    - 状态级 sound_effects / particle_effects: Java 传空, 删掉(基岩会播);
    - animations / transitions 里的多键字典: Java Adapter 只取第一个键, 其余丢弃;
    - animations 的 {动画: 表达式}: Java 是布尔 apply 条件, 非布尔式包 `!=0`(见 _JavaApplyCondition)。
    """
    states = body.get("states") if isinstance(body, dict) else None
    if not isinstance(states, dict):
        return 0
    fixes = 0
    for state in states.values():
        if not isinstance(state, dict):
            continue
        blend = state.get("blend_transition")
        if isinstance(blend, dict):
            numeric = _NumericBlend(blend)
            if numeric > 0:
                state["blend_transition"] = numeric
            else:
                del state["blend_transition"]
            fixes += 1
        for key in _JAVA_DROPPED_STATE_KEYS:
            if key in state:
                del state[key]
                fixes += 1
        animations = state.get("animations")
        if isinstance(animations, list):
            rebuilt = []
            for item in animations:
                if not isinstance(item, dict):
                    rebuilt.append(item)
                    continue
                if not item:
                    fixes += 1
                    continue
                keys = list(item.keys())
                if len(keys) > 1:
                    fixes += 1                    # Java AnimationEntry.Adapter: break 于首键
                animKey = keys[0]
                condition, changed = _JavaApplyCondition(item[animKey])
                if changed:
                    fixes += 1
                if condition is None:
                    rebuilt.append(animKey)
                else:
                    rebuilt.append(OrderedDict([(animKey, condition)]))
            state["animations"] = rebuilt
        transitions = state.get("transitions")
        if isinstance(transitions, list):
            rebuilt = []
            for entry in transitions:
                if isinstance(entry, dict) and len(entry) > 1:
                    target = list(entry.keys())[0]
                    rebuilt.append(OrderedDict([(target, entry[target])]))
                    fixes += 1                    # Java Transition.Adapter: break 于首键
                else:
                    rebuilt.append(entry)
            state["transitions"] = rebuilt
    return fixes


def RemapBlendTransitions(body):
    """blend_transition 的归属从 Java 语义换成基岩语义; 返回改写的状态数。**只在移植期跑一次**。

    Java(BedrockAnimationController.transition): 进入新状态时把**新状态**的 blend 设成播放器的
    起始过渡 = "被进入状态的淡入时长"; 基岩/Blockbench(animation_controllers.js 用 last_state)
    按**被离开**的状态取值。同一控制器各态同值时两种解读等价; 不同值时(warden parallel_3 /
    wither pre_parallel_1)必须换算: 基岩每个源态只有一个值, 取该态**各出边目标(去重)里出现次数
    最多的 Java 值, 平局取较小值**。
    早先取最大值, 会把 Java 里的硬切改成淡化: 凋灵娘飞行循环 flyA → fly_tranlate_A(目标 blend 0)
    只因另一条出边 jump_down 是 0.1 就被补成 0.1 秒淡化。平局取小保住作者刻意写的硬切, 代价是
    少数出边丢掉一段 0.1 秒量级的淡化 —— 基岩单值归属下两者不可兼得, 这是误差更小的一侧。
    """
    states = body.get("states") if isinstance(body, dict) else None
    if not isinstance(states, dict):
        return 0
    javaBlend = dict((name, _NumericBlend(state.get("blend_transition")))
                     for name, state in states.items() if isinstance(state, dict))
    changed = 0
    for name, state in states.items():
        if not isinstance(state, dict):
            continue
        targets = []
        for entry in state.get("transitions") or []:
            if isinstance(entry, dict):
                targets += [target for target in entry if target in javaBlend and target not in targets]
        if not targets:
            continue
        votes = Counter(round(javaBlend[target], 6) for target in targets)
        desired = sorted(votes.items(), key=lambda item: (-item[1], item[0]))[0][0]
        if abs(desired - javaBlend.get(name, 0.0)) <= 1e-9:
            continue
        if desired > 0:
            state["blend_transition"] = desired
        else:
            state.pop("blend_transition", None)
        changed += 1
    return changed


def ApplyShortestPathBlend(body):
    """带正 blend 的状态补 blend_via_shortest_path: true(Java 过渡恒为四元数 nlerp); 返回补写数。幂等。"""
    if not _JAVA_SHORTEST_PATH_BLEND:
        return 0
    states = body.get("states") if isinstance(body, dict) else None
    if not isinstance(states, dict):
        return 0
    fixes = 0
    for state in states.values():
        if not isinstance(state, dict) or _NumericBlend(state.get("blend_transition")) <= 0:
            continue
        if state.get("blend_via_shortest_path") is not True:
            state["blend_via_shortest_path"] = True
            fixes += 1
    return fixes


def RewriteControllers(srcPath, dstPath, namespace, molangDefaults=None, molangReport=None,
                       nameMapper=None, knownAnimKeys=None, foundVars=None, physics=None):
    """动画控制器文件 → controller.animation.<namespace>.<名称>, 动画引用同步转义。

    返回 (控制器数, 接管的直播动画键列表, 动画引用改名数, 剪掉的死引用列表,
    剪掉的非法转移目标列表)。
    Java 的控制器名就是**通道名**(player.parallel_0 / player.main / fp.arm.misc),
    网易侧没有通道, 统一注册为常开控制器; parallel 系通道另摘掉直播条目(见
    _CHANNEL_TAKEOVER_PATTERN / _PARALLEL_SHORT_PATTERN 注释)。非通道名的控制器与
    初始状态不存在的控制器 Java 不会动作, 移植期跳过(molangReport 留痕)。
    knownAnimKeys: 本包动画文件产出的短键全集(死引用剪枝判据; None = 不剪)。
    """
    data = LoadJson(srcPath)
    controllers = data.get("animation_controllers")
    if not isinstance(controllers, dict):
        return 0, [], 0, [], []

    def _Note(label, count=1):
        if molangReport is not None and count:
            molangReport[label] += count

    renamed = OrderedDict()
    takeovers = []
    refFixes = 0
    prunedRefs = []
    prunedTransitions = []
    for rawName in controllers:
        body = controllers[rawName]
        if not IsJavaChannelControllerName(rawName):
            _Note(u"skip:控制器 {}(非 Java 通道名, Java 不挂载, 移植同样跳过)".format(rawName))
            continue
        matched = _CHANNEL_TAKEOVER_PATTERN.match(rawName)
        if matched:
            takeovers.append("{}{}".format(matched.group(1), matched.group(2)))
        newName = rawName if rawName.startswith("controller.animation.") \
            else u"controller.animation.{}.{}".format(namespace, _ControllerRegisterName(rawName))
        if isinstance(body, dict):
            body = _RewriteControllerStates(body, nameMapper)
            states = body.get("states")
            initial = body.get("initial_state")
            if initial is None:
                initial = "default"               # Java pojo AnimationController 缺省 "default"
                body["initial_state"] = initial
            if not isinstance(states, dict) or initial not in states:
                # Java updateState: 初始状态查无 → return false, 该控制器永远不动作
                _Note(u"skip:控制器 {}(initial_state {} 不存在, Java 侧永不动作)".format(
                    rawName, initial))
                continue
            _Note(u"norm:控制器状态按 Java 语义规范化(blend 曲线->数字 / 状态级音效粒子删除 / "
                  u"多键条目取首键 / apply 条件布尔化)", NormalizeJavaControllerStates(body))
            if physics is not None:
                physics.RewriteTree(body)   # 转移条件/权重/on_entry 里的物理调用
            for state in states.values():
                if isinstance(state, dict):
                    refFixes += _RewriteControllerAnimRefs(
                        state.get("animations"), nameMapper)
            _PruneControllerBody(body, knownAnimKeys, prunedRefs, prunedTransitions)
            takeovers += _CollectParallelRefs(body)
            _Note(u"norm:blend_transition 归属 Java(目标态淡入) -> 基岩(离开态)重映射",
                  RemapBlendTransitions(body))
            _Note(u"norm:blend_via_shortest_path=true(Java 跨态过渡是四元数 nlerp)",
                  ApplyShortestPathBlend(body))
        renamed[newName] = body
    data["animation_controllers"] = renamed
    finalText = PortMolangText(
        json.dumps(data, ensure_ascii=False, indent=2), molangDefaults, molangReport)
    # 最后一道: 基岩解析不了的表达式按 Java 口径处置(转移条件永不成立 / 动画条目与 on_entry 删除),
    # 一个坏值不再拖垮整份控制器文件(见 devtools/molang_syntax.py 注)
    reparsed = json.loads(finalText, object_pairs_hook=OrderedDict)
    guardFixes = precedenceFixes = 0
    for ctlId, ctlBody in (reparsed.get("animation_controllers") or {}).items():
        for path, badText, problem in GuardControllerMolang(ctlBody):
            guardFixes += 1
            _Note(u"zero:基岩解析不了的表达式按 Java 口径处置(Java 同样解析失败): {} {}: {} <- {}".format(
                ctlId.split(".", 3)[-1], FormatSlotPath(path), problem, badText[:60]))
        precedenceFixes += len(ExplicitControllerPrecedence(ctlBody))
    _Note(u"map:新旧 Molang 语义可能分叉处补括号(资源包按 min_engine_version 1.18.0 走旧语义)",
          precedenceFixes)
    if guardFixes or precedenceFixes:
        finalText = json.dumps(reparsed, ensure_ascii=False, indent=2)
    if foundVars is not None:
        foundVars.update(_MOLANG_VAR_SCAN.findall(finalText))
    WriteBytes(dstPath, finalText.encode("utf-8"))
    return len(renamed), takeovers, refFixes, prunedRefs, prunedTransitions


def _NormalizeVarName(name):
    """v.xxx / variable.xxx → variable.xxx; 其他形态返回 None"""
    if not isinstance(name, (str, unicode)):  # noqa: F821
        return None
    name = name.strip()
    if name.startswith("v."):
        return "variable." + name[2:]
    if name.startswith("variable."):
        return name
    return None


def _RangeInitialValue(form):
    """config_forms 表单变量初值(与 packParser._BuildInitialize 同一规则):
    Java 未定义变量读 0, 故取 0 并夹进 range 的 [min, max]; checkbox/radio 取 0。
    (旧规则"range 取 min"会把 [-50,50] 的位置滑条初始化成 -50 —— 实机: 凋灵娘整体
    平移 50 单位出画面。)"""
    value = 0
    if form.get("type") == "range":
        low, high = form.get("min"), form.get("max")
        if isinstance(low, (int, float)) and value < low:
            value = low
        if isinstance(high, (int, float)) and value > high:
            value = high
    return value


def BuildPackVariableDefaults(manifest, foundVars):
    """包变量默认值表 [\"variable.名 = 值;\", ...](值语义与 packParser._BuildInitialize 一致)。

    两个落盘去处, 各对其渲染实例类型用引擎原生手段: ① 玩家渲染实例(世界/纸娃娃) →
    BuildVariableInitController 的每实例初始化控制器(玩家实体定义是全局覆盖点, 不能
    往里写); ② GUI 预览实体(ysm_pack:<包>) → 其自身实体定义的 scripts.initialize
    (我们自己的实体, 原生 initialize 就是正解; 实机 2026-09: 运行期给预览实体挂
    初始化控制器不生效, 缩略图逐通道刷 unknown variable)。二者同源同口径, 由同一次
    工具运行生成, 手写面只有 ysm.json。

    值优先级: initialize 显式表达式(原样, 保默认外观如 Breast_Outfit=1)
    > config_forms 的 value 变量(range 取 min, checkbox/radio 取 0)
    > 文件扫描到的其余变量补 0.0(Java "未定义读 0" 语义)。
    initialize 的标准位置是**顶层**(与 packParser 同一口径), netease 段为兼容形态 ——
    只读 netease 会在包精简后静默丢掉全部显式默认值(踩过: 萨赫梅特 33 条换装件默认值
    变成 0.0, 纸娃娃上换装件全消失)。
    """
    lines = []
    seen = set(_ENTITY_BASE_VARS)

    netease = manifest.get("netease") or {}
    declaredInitialize = list(manifest.get("initialize") or []) or list(
        netease.get("initialize") or [])
    for entry in declaredInitialize:
        if not isinstance(entry, (str, unicode)) or "=" not in entry:  # noqa: F821
            continue
        expr = entry.strip()
        if not expr.endswith(";"):
            expr += ";"
        lines.append(expr)
        head = _NormalizeVarName(expr.split("=", 1)[0])
        if head:
            seen.add(head[len("variable."):].lower())

    properties = manifest.get("properties") or {}
    for button in properties.get("extra_animation_buttons") or []:
        if not isinstance(button, dict):
            continue
        for form in button.get("config_forms") or []:
            if not isinstance(form, dict):
                continue
            name = _NormalizeVarName(form.get("value"))
            if name is None or name[len("variable."):].lower() in seen:
                continue
            seen.add(name[len("variable."):].lower())
            initValue = _RangeInitialValue(form)
            lines.append("{} = {};".format(name, float(initValue)))

    for shortName in sorted(foundVars or ()):
        if shortName.lower() in seen or _OWNERSHIP_VARIABLE_PATTERN.match(shortName):
            continue     # 占用变量(ApplyChannelOwnership)由控制器 on_entry 维护, 读取处带 ??0
        if _INPUT_STATE_VARIABLE_PATTERN.match(shortName):
            continue     # 输入状态锁存/挥动序号(主包 java_input_state + 挥击状态机), 读取处带 ?? 回落
        seen.add(shortName.lower())
        lines.append("variable.{} = 0.0;".format(shortName))
    return lines


VARIABLE_INIT_KEY = "ysm_variable_init"
VARIABLE_INIT_FILE = "ysm_variable_init.json"
ONESHOT_FILE = "ysm_oneshot.json"
STATE_FILE = "ysm_state.json"
STATE_RESET_FILE = "ysm_reset.animation.json"
STATE_RESET_CONTROLLER_FILE = "ysm_reset.json"
GUI_BASE_FILE = "ysm_gui_base.animation.json"


def PackManifestPath(packName):
    """包的 BP 声明文件: ysm_models/<包>/ysm.json, 合集成员在 ysm_models/<合集>/<包>/ysm.json。
    都不存在时返回平铺路径(调用方按 isfile 判)。早先各处直接拼平铺路径, 合集成员(酒狐 22 包)的
    channel_ownership / 轮盘键 / 修复工具的 ysm.json 步骤全部落空(2026-09-17 排查小小酒狐伴生权重发现)。"""
    flat = os.path.join(BP_MODELS, packName, "ysm.json")
    if os.path.isfile(flat) or not os.path.isdir(BP_MODELS):
        return flat
    for name in sorted(os.listdir(BP_MODELS)):
        nested = os.path.join(BP_MODELS, name, packName, "ysm.json")
        if os.path.isfile(nested):
            return nested
    return flat


def PackStateBlend(packName):
    """包的主链过渡时长: netease.state_blend 覆盖, 缺省 = Java main 通道的 0.1s"""
    path = PackManifestPath(packName)
    if os.path.isfile(path):
        try:
            value = (LoadJson(path).get("netease") or {}).get("state_blend")
        except ValueError:
            value = None
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    return _STATE_ENTER_BLEND


def BuildStateChainControllerFile(packName, blend=None, ownership=None):
    """包的 Java 主链状态机文件体(packParser.BuildStateChainController); 无主链成员 → None。

    直挂 animate 条目零过渡硬切换(转换包"两段动画衔接不流畅"的根因), 主链动画改由
    状态机驱动, 状态切换经 blend_transition 交叉淡化 0.1s(Java main/vehicle 通道的
    起始过渡)。主包在资源索引发现 controller.animation.<包>.ysm_state 即用它替换
    包自有主链成员的直挂条目; 无文件的旧产物维持直挂。
    ownership: ApplyChannelOwnership 的规划(主链伴生动画与占用变量); 缺省按磁盘现状只读规划。
    """
    if ownership is None:
        ownership = PlanChannelOwnership(packName)
    body = BuildStateChainController(
        packName, _MainChainKeys(packName), blend if blend is not None else PackStateBlend(packName),
        companions=ownership.mainCompanions, ownershipVariable=ownership.mainVariable,
        ownershipStatements=ownership.HostStatements(_STATE_CHAIN_KEY))
    if body is None:
        return None
    return OrderedDict([
        ("format_version", "1.19.0"),
        ("animation_controllers", OrderedDict([
            ("controller.animation.{}.{}".format(packName, _STATE_CHAIN_KEY), body)])),
    ])


def CollectPackAnimationBodies(packName):
    """RP 动画产物 → 主域注册键 → 动画体(口径同 CollectPackAnimationLoops)"""
    animDir = os.path.join(RP, "animations", packName)
    bodies, armBodies = OrderedDict(), OrderedDict()
    if not os.path.isdir(animDir):
        return bodies
    mainPrefix = "animation.{}.".format(packName)
    armPrefix = "animation.{}_arm.".format(packName)
    for name in sorted(os.listdir(animDir)):
        if not name.endswith(".json"):
            continue
        try:
            data = LoadJson(os.path.join(animDir, name))
        except ValueError:
            continue
        for animId, body in (data.get("animations") or {}).items():
            if not isinstance(body, dict):
                continue
            if animId.startswith(mainPrefix):
                bodies[str(animId[len(mainPrefix):])] = body
            elif animId.startswith(armPrefix):
                shortKey = str(animId[len(armPrefix):])
                if not _PARALLEL_SHORT_PATTERN.match(shortKey):
                    armBodies[shortKey] = body
    for shortKey, body in armBodies.items():
        bodies.setdefault(shortKey, body)
    return bodies


_PRE_CHANNEL_CONTROLLER_PREFIXES = ("player_pre_parallel", "player_pre_main", "player_vehicle")


def _LoadPackAnimationFiles(packName):
    """RP 动画目录 → [(路径, 文件数据)], 跳过本工具生成的派生文件"""
    animDir = os.path.join(RP, "animations", packName)
    out = []
    if not os.path.isdir(animDir):
        return out
    for name in sorted(os.listdir(animDir)):
        if not name.endswith(".json") or name in (STATE_RESET_FILE, GUI_BASE_FILE):
            continue
        path = os.path.join(animDir, name)
        try:
            out.append((path, LoadJson(path)))
        except ValueError:
            continue
    return out


def _HasMolangAssignment(value):
    """通道取值里是否含 molang 赋值(假骨骼承载的副作用求值) —— 这类通道不能删"""
    return bool(_ASSIGNMENT_PATTERN.search(json.dumps(value, ensure_ascii=False)))


def CollectPreChannelAnimationKeys(packName):
    """pre 通道(Java 里排在 main 之前)所播动画 → {"always_on": set, "gated": set}。

    always_on = 控制器**初始态**所播(以及裸 pre_parallelN 动画) —— 恒在播;
    gated = 非初始态所播(jump_up / jump_fall / fly / <作者自己的状态机>) —— 按条件才播。
    """
    bodies = CollectPackAnimationBodies(packName)
    groups = {"always_on": set(k for k in bodies
                               if _PARALLEL_SHORT_PATTERN.match(k) and k.startswith("pre_")),
              "gated": set()}
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    if os.path.isdir(ctlDir):
        for name in sorted(os.listdir(ctlDir)):
            if not name.endswith(".json") or name in (
                    STATE_FILE, ONESHOT_FILE, VARIABLE_INIT_FILE, STATE_RESET_CONTROLLER_FILE):
                continue
            try:
                data = LoadJson(os.path.join(ctlDir, name))
            except ValueError:
                continue
            for ctlName, body in (data.get("animation_controllers") or {}).items():
                if not str(ctlName).split(".")[-1].startswith(_PRE_CHANNEL_CONTROLLER_PREFIXES):
                    continue
                if not isinstance(body, dict):
                    continue
                states = body.get("states") or {}
                initName = body.get("initial_state") or "default"
                for stateName, state in states.items():
                    if not isinstance(state, dict):
                        continue
                    target = "always_on" if stateName == initName else "gated"
                    for item in (state.get("animations") or []):
                        for ref in (item.keys() if isinstance(item, dict) else [item]):
                            if isinstance(ref, (str, unicode)):  # noqa: F821
                                groups[target].add(str(ref))
    known = set(bodies)
    groups["always_on"] &= known
    groups["gated"] = (groups["gated"] & known) - groups["always_on"]
    return groups


_PRE_CHANNEL_RANK = (("player_pre_parallel", 0), ("player_vehicle", 1), ("player_pre_main", 2))


def PreLayerOrder(packName):
    """常驻 pre 层动画按 **Java 通道顺序**排列(后者覆盖前者)。

    Java 注册序: pre_parallel_0..7 → vehicle → pre_main_*(见 PlayerControllerCollection);
    同一状态的 animations 列表内也是后者覆盖前者。裸 pre_parallelN 动画按 N 排。
    """
    ranked = []
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    if os.path.isdir(ctlDir):
        for name in sorted(os.listdir(ctlDir)):
            if not name.endswith(".json") or name in (
                    STATE_FILE, ONESHOT_FILE, VARIABLE_INIT_FILE, STATE_RESET_CONTROLLER_FILE):
                continue
            try:
                data = LoadJson(os.path.join(ctlDir, name))
            except ValueError:
                continue
            for ctlName, body in (data.get("animation_controllers") or {}).items():
                last = str(ctlName).split(".")[-1]
                rank = next((value for prefix, value in _PRE_CHANNEL_RANK
                             if last.startswith(prefix)), None)
                if rank is None or not isinstance(body, dict):
                    continue
                suffix = re.findall(r"(\d+)$", last)
                channelIndex = int(suffix[0]) if suffix else 0
                initName = body.get("initial_state") or "default"
                state = (body.get("states") or {}).get(initName)
                if not isinstance(state, dict):
                    continue
                for position, item in enumerate(state.get("animations") or []):
                    # **只收裸字符串**: dict 形态是带权重/条件的条目(如凋灵娘的三套眼型
                    # {"yanshena": "v.roaming_eyes_transition==0"}), 它们本就互斥,
                    # 参与去重会把另外两套的通道删掉 —— 换装/眼型会失效(踩过)
                    if isinstance(item, (str, unicode)):  # noqa: F821
                        ranked.append(((rank, channelIndex, position), str(item)))
    for key in CollectPackAnimationBodies(packName):
        if _PARALLEL_SHORT_PATTERN.match(key) and key.startswith("pre_"):
            suffix = re.findall(r"(\d+)$", key)
            ranked.append(((0, int(suffix[0]) if suffix else 0, -1), key))
    seen, order = set(), []
    for _rank, key in sorted(ranked):
        if key not in seen:
            seen.add(key)
            order.append(key)
    return order


def PreLayerEntries(packName):
    """pre 层全部条目(常驻 + 门控) → [(序, 动画键, 控制器短名, 状态名, 是否 dict 条目)], 按序升序。

    序 = (通道等级, 通道序号, 状态内位置), 与 Java 注册序一致(后者覆盖前者): pre_parallel_0..7
    → vehicle → pre_main; 裸 pre_parallelN 记作 (0, N, -1)、控制器 None。dict 条目(带权重/
    条件, 如凋灵娘的三套眼型)也收, 同一状态内的两个 dict 条目视为互斥(见 _PreEntriesCoplay)。
    """
    entries = []
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    if os.path.isdir(ctlDir):
        for name in sorted(os.listdir(ctlDir)):
            if not name.endswith(".json") or name in (
                    STATE_FILE, ONESHOT_FILE, VARIABLE_INIT_FILE, STATE_RESET_CONTROLLER_FILE):
                continue
            try:
                data = LoadJson(os.path.join(ctlDir, name))
            except ValueError:
                continue
            for ctlName, body in (data.get("animation_controllers") or {}).items():
                last = str(ctlName).split(".")[-1]
                rank = next((value for prefix, value in _PRE_CHANNEL_RANK
                             if last.startswith(prefix)), None)
                if rank is None or not isinstance(body, dict):
                    continue
                suffix = re.findall(r"(\d+)$", last)
                channelIndex = int(suffix[0]) if suffix else 0
                for stateName, state in (body.get("states") or {}).items():
                    if not isinstance(state, dict):
                        continue
                    for position, item in enumerate(state.get("animations") or []):
                        refs = item.keys() if isinstance(item, dict) else [item]
                        for ref in refs:
                            if isinstance(ref, (str, unicode)):  # noqa: F821
                                entries.append(((rank, channelIndex, position), str(ref),
                                                last, str(stateName), isinstance(item, dict)))
    # 裸 pre_parallelN 只在**没被任何 pre 控制器引用**时才算直播条目: 被引用即通道接管
    # (移植工具把它摘出 animate 表), 它实际的播放序是控制器状态里那一条; 再记一条 (0, N, -1)
    # 幻影会排在控制器条目之后, 让真正在播的那条与被它压住的门控动画(sahmet 的 jump_up/
    # jump_fall 各 24 对)双双被剥 —— 踩过。
    referenced = set(entry[1] for entry in entries)
    for key in CollectPackAnimationBodies(packName):
        if _PARALLEL_SHORT_PATTERN.match(key) and key.startswith("pre_") and key not in referenced:
            suffix = re.findall(r"(\d+)$", key)
            entries.append(((0, int(suffix[0]) if suffix else 0, -1), key, None, None, False))
    entries.sort(key=lambda entry: entry[0])
    return entries


def _PreEntriesCoplay(first, second):
    """两条 pre 层条目能否同时在播: 不同控制器(含裸动画)可以; 同一控制器只有同一状态内可以,
    且同一状态内的两个 dict 条目(带条件/权重, 通常互斥 —— 凋灵娘的三套眼型)视为不能"""
    if first[2] is None or second[2] is None or first[2] != second[2]:
        return True
    if first[3] != second[3]:
        return False
    return not (first[4] and second[4])


def DedupPreLayer(packName):
    """pre 层内部按 Java 通道顺序去重: 能同时在播的两条动画共写一个 (骨骼, 通道) 时, 只留
    **序靠后**的那条(Java 后写覆盖, 基岩相加); 返回删除对数。含 molang 赋值的通道不动。幂等。

    覆盖三种叠加: ① 常驻 pre 之间(实测 ref_wither 的常驻 pre 有 65 对重叠); ② 门控 pre(控制器
    非初始态, 如凋灵娘整套悬浮/地面步态)压住更早通道的常驻 pre(眼光/眉毛定位 37 对) —— 这类
    动画原先靠 override_previous_animation 掩盖, 撤掉标志后必须在数据层解决; ③ 跨控制器的
    门控 pre 之间。代价: 靠前那条在靠后那条**不在播**时, 该通道回到绑定姿态(静态剥离的固有
    局限; 常驻 pre 对门控 pre 的让位只在门控全部空闲时可见)。
    同一控制器的不同状态不会同时在播, 不互剥; 同一状态内的 dict 条目互斥, 也不互剥(去重会
    删掉另外几套眼型/换装件 —— 踩过)。
    """
    files = _LoadPackAnimationFiles(packName)
    bodies, owners = {}, {}
    prefix = "animation.{}.".format(packName)
    for path, data in files:
        for animId, body in (data.get("animations") or {}).items():
            if isinstance(body, dict) and animId.startswith(prefix):
                key = str(animId[len(prefix):])
                bodies.setdefault(key, body)
                owners.setdefault(key, path)
    entries = [entry for entry in PreLayerEntries(packName) if entry[1] in bodies]
    pairsByKey = dict((key, AnimationChannelPairs(bodies[key]))
                      for key in set(entry[1] for entry in entries))
    removed, dirty = 0, set()
    for index, entry in enumerate(entries):
        key = entry[1]
        laterPairs = set()
        for other in entries[index + 1:]:
            if other[1] != key and other[0] > entry[0] and _PreEntriesCoplay(entry, other):
                laterPairs |= pairsByKey[other[1]]
        if not laterPairs:
            continue
        bones = bodies[key].get("bones") or {}
        for boneName in list(bones.keys()):
            channels = bones[boneName]
            if not isinstance(channels, dict):
                continue
            for channel in list(channels.keys()):
                if channel not in ("rotation", "position", "scale"):
                    continue
                if (boneName, channel) not in laterPairs:
                    continue
                if _HasMolangAssignment(channels[channel]):
                    continue
                del channels[channel]
                removed += 1
                dirty.add(owners.get(key))
            if not channels:
                del bones[boneName]
        # 删空了就连 bones 键一起去掉: 空 bones 节点会让引擎拒载整份文件(见 SanitizeAnimationBody)
        SanitizeAnimationBody(bodies[key])
    for path, data in files:
        if path in dirty:
            DumpJson(path, data)
    return removed


def DropPreControllerMainChainRefs(packName):
    """pre 通道控制器状态里对**主链成员**的引用(如 feixing 态播 fly)一律摘掉;
    返回摘掉的 [(控制器短名, 状态, 动画键)]。幂等。

    Java 里 pre 通道与 main 通道同时播同一条 fly 时 main 逐通道覆盖 pre, 净效果就是一份;
    基岩相加 → 飞行姿态叠成两倍。主链成员由 ysm_state 状态机负责播放(带交叉淡化), pre
    侧那份是纯冗余。空掉的 animations 列表必须删键(空数组整份控制器文件拒载)。
    """
    dropped = []
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    if not os.path.isdir(ctlDir):
        return dropped
    for name in sorted(os.listdir(ctlDir)):
        if not name.endswith(".json") or name in (
                STATE_FILE, ONESHOT_FILE, VARIABLE_INIT_FILE, STATE_RESET_CONTROLLER_FILE):
            continue
        path = os.path.join(ctlDir, name)
        try:
            data = LoadJson(path)
        except ValueError:
            continue
        changed = False
        for ctlName, body in (data.get("animation_controllers") or {}).items():
            last = str(ctlName).split(".")[-1]
            if not last.startswith(_PRE_CHANNEL_CONTROLLER_PREFIXES) or not isinstance(body, dict):
                continue
            for stateName, state in (body.get("states") or {}).items():
                if not isinstance(state, dict) or not isinstance(state.get("animations"), list):
                    continue
                kept = []
                for item in state["animations"]:
                    if isinstance(item, dict):
                        keptItem = OrderedDict()
                        for ref, weight in item.items():
                            if isinstance(ref, (str, unicode)) \
                                    and JavaStateLoopType(ref) is not None:  # noqa: F821
                                dropped.append((last, str(stateName), str(ref)))
                            else:
                                keptItem[ref] = weight
                        if keptItem:
                            kept.append(keptItem)
                        continue
                    if isinstance(item, (str, unicode)) and JavaStateLoopType(item) is not None:  # noqa: F821
                        dropped.append((last, str(stateName), str(item)))
                    else:
                        kept.append(item)
                if kept != state["animations"]:
                    if kept:
                        state["animations"] = kept
                    else:
                        del state["animations"]
                    changed = True
        if changed:
            DumpJson(path, data)
    return dropped


def BypassEmptyHubStatesInPack(packName):
    """包内全部作者控制器文件跑一遍 BypassEmptyHubStates(见其注); 返回新增旁路数。

    必须排在 DropPreControllerMainChainRefs 之后: 被摘空的状态(feixing 播 fly → 空)才会
    被识别为空中转状态, 移植与修复两条链同序, 第一遍产物即终态(幂等性守护)。
    """
    added = 0
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    if not os.path.isdir(ctlDir):
        return added
    for name in sorted(os.listdir(ctlDir)):
        if not name.endswith(".json") or name in (
                STATE_FILE, ONESHOT_FILE, VARIABLE_INIT_FILE, STATE_RESET_CONTROLLER_FILE):
            continue
        path = os.path.join(ctlDir, name)
        try:
            data = LoadJson(path)
        except ValueError:
            continue
        count = 0
        for body in (data.get("animation_controllers") or {}).values():
            if isinstance(body, dict):
                count += BypassEmptyHubStates(body)
        if count:
            DumpJson(path, data)
            added += count
    return added


def _ConstantVector(value):
    """[x, y, z] 三个数字字面量 → 列表; 其他(关键帧字典/含 molang 的字符串)→ None"""
    if isinstance(value, list) and len(value) == 3 \
            and all(isinstance(item, (int, float)) for item in value):
        return list(value)
    return None


def _CompactNumber(value):
    text = "{:.6g}".format(value)
    return text


def CollectParallelVariantWriters(packName):
    """parallel 层(Java 里排在 pre 之后)动画 → [(序, 动画键, 条件)], 按序升序。

    序 = (控制器通道号, 状态内位置); 裸 parallelN 直播动画记 (N, -1) 条件恒真。
    dict 条目的条件即其权重表达式(`{"emotions_1": "v.Emotions==1"}`), 裸字符串条件为 "1"。
    同一控制器的不同状态各记一条 —— 折叠只接受"全包只出现一次"的动画(见 ReconcileConditionalVariants)。
    """
    writers = []
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    if os.path.isdir(ctlDir):
        for name in sorted(os.listdir(ctlDir)):
            if not name.endswith(".json") or name in (
                    STATE_FILE, ONESHOT_FILE, VARIABLE_INIT_FILE, STATE_RESET_CONTROLLER_FILE):
                continue
            try:
                data = LoadJson(os.path.join(ctlDir, name))
            except ValueError:
                continue
            for ctlName, body in (data.get("animation_controllers") or {}).items():
                last = str(ctlName).split(".")[-1]
                if not re.match(r"^player_parallel_\d+$", last) or not isinstance(body, dict):
                    continue
                channel = int(re.findall(r"(\d+)$", last)[0])
                for state in (body.get("states") or {}).values():
                    if not isinstance(state, dict):
                        continue
                    for position, item in enumerate(state.get("animations") or []):
                        entries = item.items() if isinstance(item, dict) else [(item, "1")]
                        for ref, condition in entries:
                            if isinstance(ref, (str, unicode)):  # noqa: F821
                                writers.append(((channel, position), str(ref),
                                                str(condition) if isinstance(
                                                    condition, (str, unicode)) else "1"))  # noqa: F821
    for key in CollectPackAnimationBodies(packName):
        if _ADDITIVE_PARALLEL_PATTERN.match(key):
            writers.append(((int(re.findall(r"(\d+)$", key)[0]), -1), key, "1"))
    writers.sort(key=lambda entry: entry[0])
    return writers


def ReconcileConditionalVariants(packName):
    """pre 层的**静态显隐**与 parallel 层的**条件变体**共写同一 (骨骼, position/scale) 时,
    折叠成单一所有者; 返回 (折叠对数, 放弃的非常量对数)。

    背景(2026-09-03 实机): 凋灵娘的火焰是几何骨骼 —— `pre_parallel0` 把四个火焰容器
    `scale` 设 0(装饰件隐藏基线), 当前样式的火焰动画(`huoyandonghuaa`, parallel 通道,
    条件 `v.roaming_fire_transition==0`)再设回 1。Java 的 parallel 通道对 position/scale
    是**覆盖**(仅 rotation 相加), 所以火焰可见; 基岩两条动画共写同一通道时按引擎自己的
    规则合成(0 与 1 的合成结果实测为不可见), 于是火焰、表情、嘴型全都出不来。
    做法: 把变体的条件折进 pre 那一份, 变体动画里删掉该通道 ——
    `scale: [0,0,0]` + (`v.Emotions==3` → `[1,1,1]`) ⇒ `scale: ["(v.Emotions==3)?1:0", ...]`。
    单一所有者与引擎的合成规则无关(这一点至今没有可靠实机结论, 不能依赖)。
    限制: 只折叠**双方都是常量向量**且变体动画**全包只被引用一次**(条件唯一)的对;
    rotation 不动(Java parallel 本就相加, 基岩同语义); 变体值是关键帧的(凋灵娘攻击族对
    pre 的摆位偏移)放弃并计数 —— 那属 Java 覆盖 vs 基岩相加的残留差异, 表现为攻击期姿态
    带一份 pre 偏移, 无法用常量折叠表达。
    幂等: 折叠后 pre 的值不再是常量向量, 变体也不再写该通道。
    """
    files = _LoadPackAnimationFiles(packName)
    bodies, owners = {}, {}
    prefix = "animation.{}.".format(packName)
    for path, data in files:
        for animId, body in (data.get("animations") or {}).items():
            if isinstance(body, dict) and animId.startswith(prefix):
                key = str(animId[len(prefix):])
                bodies.setdefault(key, body)
                owners.setdefault(key, path)
    variantWriters = CollectParallelVariantWriters(packName)
    seen = Counter(key for _rank, key, _condition in variantWriters)
    folded, skipped, dirty = 0, 0, set()
    for preEntry in PreLayerEntries(packName):
        preKey = preEntry[1]
        preBody = bodies.get(preKey)
        if preBody is None:
            continue
        bones = preBody.get("bones") or {}
        for boneName in list(bones.keys()):
            channels = bones[boneName]
            if not isinstance(channels, dict):
                continue
            for channel in list(channels.keys()):
                if channel not in ("position", "scale"):
                    continue
                baseline = _ConstantVector(channels[channel])
                if baseline is None:
                    continue
                writers = []
                for _rank, key, condition in variantWriters:
                    variant = bodies.get(key)
                    if variant is None or seen[key] != 1:
                        continue
                    variantChannels = (variant.get("bones") or {}).get(boneName)
                    if not isinstance(variantChannels, dict) or channel not in variantChannels:
                        continue
                    writers.append((key, condition, _ConstantVector(variantChannels[channel])))
                if not writers:
                    continue
                if any(value is None for _key, _condition, value in writers):
                    skipped += len(writers)
                    continue
                components = [_CompactNumber(value) for value in baseline]
                for _key, condition, value in writers:      # 后者胜出 → 条件包在外层
                    if condition in ("1", "1.0", "true"):
                        components = [_CompactNumber(item) for item in value]
                        continue
                    # 否分支已是上一个变体折出的三元时加括号: 资源包按旧版 Molang 语义解析, 三元左结合
                    # (`a?1:b?1:0` 会算成 `(a?1:b)?1:0`, 见 molang_syntax.ExplicitPrecedence 注)
                    components = [u"({})?{}:{}".format(
                        condition, _CompactNumber(value[index]),
                        components[index] if re.match(r"^-?\d+(?:\.\d+)?$", components[index])
                        else u"({})".format(components[index]))
                        for index in range(3)]
                channels[channel] = [
                    float(text) if re.match(r"^-?\d+(?:\.\d+)?$", text) else text
                    for text in components]
                dirty.add(owners.get(preKey))
                for key, _condition, _value in writers:
                    variantBones = bodies[key].get("bones") or {}
                    variantChannels = variantBones.get(boneName) or {}
                    variantChannels.pop(channel, None)
                    if not variantChannels:
                        variantBones.pop(boneName, None)
                    SanitizeAnimationBody(bodies[key])
                    dirty.add(owners.get(key))
                folded += 1
    for path, data in files:
        if path in dirty:
            DumpJson(path, data)
    return folded, skipped


def LoopPreviewAnimation(packName, manifest=None):
    """GUI 展示动画(properties.preview_animation)按 Java 口径强制循环; 返回改写的 [(动画键, 原 loop)]。

    Java 的 GUI 实体在 cap 通道用 playLoopAnimation(LoopType.LOOP)播展示动画(CapPredicate), 无视动画文件
    自己的 loop: 官方酒狐 15 号"选择动画"(无 loop, 5 秒)、12/20 号(hold_on_last_frame)在 Java 卡片上照样循环。
    基岩按文件的 loop 播, 播完一次就停在绑定姿态(2026-09-18 用户报告 15 号卡片动画不重播, 直接变成站立)。
    展示动画同时在别处播放(主链成员 / 轮盘 / 控制器状态引用 / 并行族)时不改 —— 那里的循环语义归对应通道。
    伴生动画 <键>__own<N> 一并改(与原动画同步计时)。幂等。
    """
    if manifest is None:
        manifestPath = PackManifestPath(packName)
        manifest = LoadJson(manifestPath) if os.path.isfile(manifestPath) else {}
    previewKey = _PreviewAnimationKey(manifest)
    if previewKey is None or _PARALLEL_SHORT_PATTERN.match(previewKey):
        return []
    animFiles = _LoadPackAnimationFiles(packName)
    bodyIndex = _OwnershipBodyIndex(packName, animFiles)
    if previewKey not in bodyIndex:
        return []
    # 主链成员口径与 _CollectOwnershipOccurrences 一致(_MainChainKeys 是候选全集, 过一遍状态判定才是成员)
    members = set(key for key, _condition in _BuildJavaStateAnimates(list(_MainChainKeys(packName))))
    if previewKey in members or previewKey in _RouletteAnimationKeys(manifest):
        return []
    for _path, data in _OwnershipControllerFiles(packName):
        for body in (data.get("animation_controllers") or {}).values():
            if not isinstance(body, dict):
                continue
            for state in (body.get("states") or {}).values():
                if not isinstance(state, dict):
                    continue
                for _item, entries in _StateAnimationItems(state):
                    if any(ref == previewKey for ref, _condition in entries):
                        return []
    companionPattern = re.compile(r"^{}__own\d+$".format(re.escape(previewKey)))
    changed, dirty = [], set()
    for key, (fileIndex, _animId, body) in bodyIndex.items():
        if key != previewKey and not companionPattern.match(key):
            continue
        if body.get("loop") is True:
            continue
        changed.append((key, body.get("loop")))
        body["loop"] = True
        dirty.add(fileIndex)
    for fileIndex in sorted(dirty):
        DumpJson(animFiles[fileIndex][0], animFiles[fileIndex][1])
    return changed


# ============ Java 逐通道覆盖 → 伴生动画 + 占用变量(ApplyChannelOwnership) ============
# Java(geckolib3/core/processor/AnimationProcessor.tickAnimation): 通道按注册顺序处理, 每个
# (骨骼, 通道) 的最终值 = **最后一个写它的通道**的值(AnimationVec3.apply 直接 set)。唯一例外是
# **内置**并行通道(CodedAnimationController, 裸 parallelN 恒播)的旋转相加; 作者用动画控制器
# 接管的通道 blendRotation 恒 false(IAnimationController.blendRotation 注原文: "如果使用动画
# 控制器，那么将永远返回 false"), 旋转同样覆盖。同一通道(控制器状态)内多条动画: 位移/旋转
# 加权求和, 缩放按 1+(s-1)w 相乘(MathUtil.computeWeightedScale, Blockbench displayScale 同式)。
# 基岩: 全部动画逐通道相加、缩放相乘(2026-09-16 实机骨骼缩放探针: pre_parallel0 的 0 × death
# 的放大 = 0)。于是:
# - 坚守者娘 pre_parallel0 把攻击特效骨骼 scale 置 0(隐藏基线), player_parallel_1/2 的攻击动画再
#   放出来 —— Java 覆盖可见, 基岩 0×1 永远不可见("持剑攻击特效没了");
# - 持剑/持镰类动画是**整套姿态**(sword_walk 的腿部数值与 walk 逐帧相同), Java 覆盖主链与
#   前置步态, 基岩相加 → 持剑时全身旋转叠成两倍。
# 静态剥离(DedupPreLayer)只适用于"两者总是同时在播"的情形; 晚层是
# 控制器里的**条件状态**时, 早层的值在晚层不活跃时必须照常生效。做法:
# 1. 早层动画 E 里会被晚层覆盖的 (骨骼, 通道) 搬进伴生动画 `<E>__own<N>`(同文件、同顶层字段,
#    只含搬出的通道), 在 E 出现的每个状态里紧跟 E 播放, 权重 = "该通道的晚层写入者都不活跃";
# 2. 晚层写入者所在控制器的每个状态 on_entry 写占用变量 `variable.ysm_own_<控制器> = <状态序号>`
#    (从 1 起; 0/未定义 = 无状态); 同一组"晚层状态集合"只在这些状态**进入时**预算一次, 存进
#    `variable.ysm_ownset_<n>`, 伴生权重只读它 —— 逐帧求值不随状态数增长(凋灵娘持镰状态机
#    有十几个状态, 内联判据单条上千字符);
# 3. 状态里带条件的 {动画: 条件}(Java 布尔 apply 条件)条件随时会变, 不能预算, 内联进权重;
# 4. 晚层恒活跃(裸 parallelN 的位移/缩放、单状态无转移控制器的无条件条目)时直接删掉早层通道。
# 权重 0 时位移/旋转贡献 0、缩放贡献 1, 正好等于"让出该通道"; 权重 1 时与原动画逐通道一致。
# **基岩权重 0 的动画暂停计时, 且让所在状态的 all_animations_finished 永不成立**(2026-09-17 实机:
# 状态里挂一条权重 0 的 3 秒动画, 原动画 0.46 秒播完后 5 秒不出态, 权重改 1 后从头播满 3 秒才出态;
# 直挂条目同样暂停, 恢复后从暂停处接着播)。故不带 override 的伴生权重下限取 1e-4(实机: 1e-4 照常
# 计时、timeline 照常触发): 让出通道时残留万分之一(旋转 360° 余 0.036°、缩放 0 余 0.9999), 时间轴
# 始终与原动画同步, 也不卡作者状态的"播完"转移。
# **带 override 的早层同样拆**: 条件动画(hold_offhand/hold_mainhand/passenger/carry_on 直挂条目)、
# 一次性通道状态机成员(挥击/使用)、作者 post_main 之类控制器里带标志的动画, 与更晚的非 override
# 写入者(并行控制器/裸 parallel 的位移缩放)同写的通道 —— Java 里晚层胜出(持盾 hold 早于持剑状态机)。
# override 的实机语义(2026-09-17 骨骼矩阵探针): 权重大于 0 就把排在它前面的条目在同一 (骨骼, 通道)
# 上的贡献整个清空再叠自己(0.5 与 1e-4 都清空), 权重 0 不清空。所以这类伴生只能精确 0 让出(不加
# 下限), 接受暂停: 直挂条目让出后从暂停处接着播; 挥击成员出口改 any_animation_finished(原动画恒为
# 1); 作者状态的"播完"判据由 RewriteFinishedQueries 改成按进入时刻计时。直挂条件动画的伴生核心
# 权重写进 ysm.json 顶级 channel_ownership(主包解析时紧跟原条目挂 `原条件 && 核心`), 一次性通道
# 成员的伴生直接写进生成的状态机(BuildOneShotControllerFile)。
# **animate 求值顺序**: 带伴生的非 override 宿主在表里按 Java 通道序倒序排在最前
# (packParser._OrderJavaAnimates) —— 晚层先切状态写占用变量, 早层当帧读到新值; 顺排时切状态那
# 一帧通道没人写或两边都写(凋灵娘持剑跑跳衔接闪一帧, 2026-09-17 用户报告); override 层仍排在
# 它们之后才能清空被覆盖的层, 与晚层并行控制器同写的通道靠上面的精确 0 伴生让位。
# 伴生动画与原动画同状态起播, 时间轴同步; 让出条件相同的通道合并成一条伴生动画。
# **裸 pre_parallelN/parallelN**(没有同名作者控制器、没被状态引用, 主包直挂恒播)同样拆: 伴生不在
# 任何控制器里, 由主包紧跟裸条目直挂(packParser._OrderJavaAnimates), 不含渲染域门的完整权重写进
# channel_ownership(不带 override 的带下限), 主包合成 `门?权重:0`。实例: 小小酒狐 pre_parallel4 按表情
# 变量写 Face/biyan 缩放, 奔跑/潜行把 Face 置 0、biyan(><眼)置 1 —— Java 主链覆盖出 >< 眼, 基岩相乘两种
# 眼睛都是 0(2026-09-17 用户报告"跳跃/潜行/奔跑不显示眼睛和眉毛")。纸娃娃/GUI 预览照旧同条件恒播伴生。
# **晚层把缩放恒写成 0 不算冲突**(_GroupOwnershipPairs / _IsConstantZeroVector): 0 × 早层 = 0 就是 Java 的结果,
# 让出反而在晚层状态淡入的 0.1s 里漏出被藏的骨骼(小小酒狐起跑闪出整架飞机)。同类的过渡期近似见本注末尾。
# **GUI 展示动画(cap 通道)晚于 pre_parallel、早于 parallel**: pre_parallel 与展示动画同写的通道同样拆伴生, 让出条件
# 读卡片纸娃娃的 variable.ysm_show(_PlanChannelOwnership ①); 预览实体按 channel_ownership 的同一份权重注册伴生。
# 不处理(保持原样): 轮盘动画(经 /playanimation 播在最上层)与甲槽条件动画(armor 是最晚的通道);
# 不带 override 的条件动画键/兜底键/第一人称控制器引用的键(它们还在别处直接播放, 拆走通道会
# 让那一处缺通道)。与静态剥离的分工: 本步排在 DedupPreLayer / ReconcileConditionalVariants 之后,
# 只处理它们剩下的冲突。主链(ysm_state)压住 pre 层的通道同样在这里按状态让位 —— 早先的
# ApplyJavaMainOverride 按"常驻 pre 与 idle 总在同播"静态删掉 pre 层通道, 走路/奔跑/卡片预览(不播 idle)时
# 被删的隐藏通道就没了: 大酒狐 pre_parallel0/1 把 heart/ysmGlowZZZ 缩成 0, idle 按时间轴放出来,
# 删掉后卡片上一直顶着爱心和 ZZZ(2026-09-18 用户报告), 已撤。
# 过渡期近似: 占用变量在进入状态的那一帧就切换, 晚层动画的淡入/淡出(blend_transition)期间早层
# 通道不跟着渐变(Java 是从当前姿态插值过去)。
# 幂等: 每次先在内存里把已有伴生并回原动画、删掉伴生条目与占用赋值, 再从头规划, 只写内容变化
# 的文件; 同一输入产出逐字节相同(伴生内骨骼按名字排序, 通道按 rotation/position/scale)。
OWNERSHIP_VARIABLE_PREFIX = "variable.ysm_own_"
OWNERSHIP_SET_PREFIX = "variable.ysm_ownset_"
_OWNERSHIP_STATEMENT_PATTERN = re.compile(
    r"^\s*variable\.(?:ysm_own_[a-z0-9_]+\s*=\s*\d+|ysm_ownset_\d+\s*=\s*.+)\s*;\s*$")
_OWNERSHIP_CHANNELS = ("rotation", "position", "scale")
# 伴生动画不复制的顶层字段(事件类只该触发一次, 骨骼单独构造)
_OWNERSHIP_COMPANION_SKIPPED_FIELDS = ("bones", "timeline", "particle_effects", "sound_effects")
# Java 玩家通道注册顺序(PlayerControllerCollection.init) → 族序; 同族多条按控制器名排序
# (ParallelControllerDiscovery / MultiControllerDiscovery 用 RB 树, 名字序)
_JAVA_CHANNEL_FAMILIES = tuple((rank, re.compile(pattern)) for rank, pattern in (
    (0, r"^player_pre_parallel_.+$"),
    (1, r"^player_parcool$"),
    (2, r"^player_vehicle$"),
    (3, r"^player_pre_main(?:_.+)?$"),
    (4, r"^player_main$"),
    (5, r"^player_post_main(?:_.+)?$"),
    (6, r"^player_pre_hold(?:_.+)?$"),
    (7, r"^player_hold_offhand$"),
    (8, r"^player_hold_mainhand$"),
    (9, r"^player_post_hold(?:_.+)?$"),
    (10, r"^player_fire$"),
    (11, r"^player_pre_swing(?:_.+)?$"),
    (12, r"^player_swing$"),
    (13, r"^player_post_swing(?:_.+)?$"),
    (14, r"^player_pre_use(?:_.+)?$"),
    (15, r"^player_use$"),
    (16, r"^player_post_use(?:_.+)?$"),
    (17, r"^player_passenger$"),
    (18, r"^player_carry_on$"),
    (22, r"^player_parallel_.+$"),
    (23, r"^player_armor_(?:head|chest|legs|feet|mainhand|offhand)$"),
))
_MAIN_CHANNEL_RANK = (4, "player_main")
_BARE_PARALLEL_PATTERN = re.compile(r"^(pre_)?parallel(\d+)$")


def JavaChannelRank(controllerName):
    """控制器注册短名 → Java 通道处理序 (族序, 名字); 非第三人称玩家通道(fp.arm 等)→ None"""
    name = str(controllerName)
    for rank, pattern in _JAVA_CHANNEL_FAMILIES:
        if pattern.match(name):
            return (rank, name)
    return None


def OwnershipVariable(host):
    """宿主(控制器注册短名 / ysm_state)的占用变量名: 当前状态序号"""
    return OWNERSHIP_VARIABLE_PREFIX + re.sub(r"[^a-z0-9_]", "_", str(host).lower())


def _IsOwnershipCompanionEntry(item):
    """控制器状态 animations 里的伴生条目(字符串或单键字典)"""
    refs = item.keys() if isinstance(item, dict) else [item]
    return any(isinstance(ref, (str, unicode)) and _OWNERSHIP_COMPANION_PATTERN.match(ref)  # noqa: F821
               for ref in refs)


def _IsOwnershipStatement(line):
    return isinstance(line, (str, unicode)) and bool(_OWNERSHIP_STATEMENT_PATTERN.match(line))  # noqa: F821


def _OwnershipControllerFiles(packName):
    """作者控制器文件 [(路径, 数据)](跳过本工具生成的派生文件)"""
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    out = []
    if not os.path.isdir(ctlDir):
        return out
    for name in sorted(os.listdir(ctlDir)):
        if not name.endswith(".json") or name in (
                STATE_FILE, ONESHOT_FILE, VARIABLE_INIT_FILE, STATE_RESET_CONTROLLER_FILE):
            continue
        path = os.path.join(ctlDir, name)
        try:
            out.append((path, LoadJson(path)))
        except ValueError:
            continue
    return out


def _SerializeJson(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


def _UndoChannelOwnershipData(packName, animFiles, ctlFiles):
    """内存里撤销 ApplyChannelOwnership: 伴生动画的通道并回原动画后删除, 控制器里的伴生条目与
    占用赋值删掉; 返回撤销的伴生动画数。原动画已有同名通道时保留原动画的(不应出现)。"""
    prefixes = ("animation.{}.".format(packName), "animation.{}_arm.".format(packName))
    undone = 0
    for _path, data in animFiles:
        animations = data.get("animations")
        if not isinstance(animations, dict):
            continue
        for animId in list(animations.keys()):
            prefix = next((p for p in prefixes if animId.startswith(p)), None)
            matched = _OWNERSHIP_COMPANION_PATTERN.match(animId[len(prefix):]) if prefix else None
            if not matched:
                continue
            companion = animations.pop(animId)
            undone += 1
            base = animations.get(prefix + matched.group(1))
            if not isinstance(base, dict) or not isinstance(companion, dict):
                continue
            for boneName, channels in (companion.get("bones") or {}).items():
                if not isinstance(channels, dict):
                    continue
                baseBones = base.get("bones")
                if not isinstance(baseBones, dict):
                    baseBones = base["bones"] = OrderedDict()
                target = baseBones.get(boneName)
                if not isinstance(target, dict):
                    target = baseBones[boneName] = OrderedDict()
                for channel, value in channels.items():
                    target.setdefault(channel, value)
    for _path, data in ctlFiles:
        for body in (data.get("animation_controllers") or {}).values():
            if not isinstance(body, dict):
                continue
            for state in (body.get("states") or {}).values():
                if not isinstance(state, dict):
                    continue
                items = state.get("animations")
                if isinstance(items, list) and any(_IsOwnershipCompanionEntry(item) for item in items):
                    state["animations"] = [item for item in items
                                           if not _IsOwnershipCompanionEntry(item)]
                onEntry = state.get("on_entry")
                if isinstance(onEntry, list) and any(_IsOwnershipStatement(line) for line in onEntry):
                    kept = [line for line in onEntry if not _IsOwnershipStatement(line)]
                    if kept:
                        state["on_entry"] = kept
                    else:
                        del state["on_entry"]
    return undone


def _RouletteAnimationKeys(manifest):
    """ysm.json 轮盘(extra_animation 及其分类)里直接播放的动画键集合"""
    properties = (manifest or {}).get("properties") or {}
    keys = set()
    pending = [properties.get("extra_animation")]
    for classify in properties.get("extra_animation_classify") or []:
        if isinstance(classify, dict):
            pending.append(classify.get("extra_animation"))
    for animDict in pending:
        if not isinstance(animDict, dict):
            continue
        for key in animDict:
            if isinstance(key, (str, unicode)) and not key.startswith("#"):  # noqa: F821
                keys.add(str(key.split(" ")[0]))
    return keys


class _OwnershipOccurrence(object):
    """一条动画在某个宿主(作者控制器 / ysm_state 主链 / 裸并行直挂)里的一次出现"""
    __slots__ = ("key", "rank", "host", "state", "stateIndex", "condition",
                 "additiveRotation", "alwaysActive")

    def __init__(self, key, rank, host, state=None, stateIndex=None, condition=None,
                 additiveRotation=False, alwaysActive=False):
        self.key = key
        self.rank = rank
        self.host = host
        self.state = state
        self.stateIndex = stateIndex
        self.condition = condition
        self.additiveRotation = additiveRotation
        self.alwaysActive = alwaysActive

    def Activity(self):
        """该出现"正在写通道"的判据 → (纯状态项 (宿主, 序号) | None, 逐帧条件项 | None);
        恒活跃且无条件 → (None, None)"""
        if self.alwaysActive:
            return None, (None if self.condition is None else u"({})".format(self.condition))
        if self.condition is None:
            return (self.host, self.stateIndex), None
        return None, u"(({}??0)=={}&&({}))".format(
            OwnershipVariable(self.host), self.stateIndex, self.condition)


def _StateSetExpression(stateTerms):
    """[(宿主, 序号)] → "任一状态活跃" 的 molang 表达式(同宿主连续序号 ≥3 个压成区间)"""
    parts = []
    byHost = OrderedDict()
    for host, index in sorted(stateTerms):
        byHost.setdefault(host, []).append(index)
    for host, indices in byHost.items():
        variable = u"({}??0)".format(OwnershipVariable(host))
        runs = []
        start = previous = indices[0]
        for index in indices[1:]:
            if index != previous + 1:
                runs.append((start, previous))
                start = index
            previous = index
        runs.append((start, previous))
        for low, high in runs:
            if high - low >= 2:
                parts.append(u"({v}>={low}&&{v}<={high})".format(v=variable, low=low, high=high))
            else:
                parts.extend(u"{}=={}".format(variable, index) for index in range(low, high + 1))
    return u"({})".format(u"||".join(parts))


class ChannelOwnershipPlan(object):
    """ApplyChannelOwnership 的规划结果(只读规划与落盘共用)"""

    def __init__(self):
        self.companions = OrderedDict()     # 原动画键 → [(伴生键, [(骨骼, 通道)], {宿主: 核心权重})]
        self.strips = OrderedDict()         # 原动画键 → [(骨骼, 通道)](晚层恒活跃, 直接删)
        self.stateSets = OrderedDict()      # 集合变量 → (表达式, 涉及宿主集)
        self.indexedHosts = set()           # 需要写状态序号的宿主(作者控制器短名 / ysm_state)
        self.bareKeys = set()               # 拆出伴生的裸 pre_parallelN/parallelN 直挂早层
        self.previewCompanions = set()      # 给 GUI 展示动画让位的 pre_parallel 伴生键(预览实体按权重注册)
        self.previewStrips = None           # (GUI 展示动画键, 被裸 parallel 覆盖而删掉的通道数)
        self.mainCompanions = OrderedDict()  # 主链键 → [(伴生键, 权重)](BuildStateChainController)
        self.overrideKeys = set()           # 带 override 的早层原动画键(伴生精确 0 让出, 不加下限)
        # 直挂伴生键 → 权重(ysm.json 顶级 channel_ownership): 带 override 的直挂条件动画记核心权重
        # (主包合成 原条件&&核心); 裸并行记不含渲染域门的完整权重(非 override 已带下限, 主包合成 门?权重:0)
        self.directWeights = OrderedDict()
        self.oneShotCompanions = OrderedDict()  # 一次性通道成员键 → [(伴生键, 权重或 None)]

    @property
    def mainVariable(self):
        return OwnershipVariable(_STATE_CHAIN_KEY) if _STATE_CHAIN_KEY in self.indexedHosts else None

    def OneShotCompanions(self):
        """BuildOneShotControllers 的 companions: 挥击/使用成员的伴生 + 受击/死亡(主链成员)的伴生"""
        merged = OrderedDict()
        for source in (self.mainCompanions, self.oneShotCompanions):
            for key, items in source.items():
                merged.setdefault(key, []).extend(items)
        return merged

    def OverrideCompanionCount(self):
        return sum(len(items) for key, items in self.companions.items() if key in self.overrideKeys)

    def HostStatements(self, host):
        """宿主每个状态 on_entry 追加的集合预算语句(状态序号赋值另写)"""
        return [u"{} = {};".format(variable, expression)
                for variable, (expression, hosts) in self.stateSets.items() if host in hosts]

    def CompanionCount(self):
        return sum(len(items) for items in self.companions.values())

    def MovedPairCount(self):
        return sum(len(pairs) for items in self.companions.values() for _k, pairs, _w in items)

    def StrippedPairCount(self):
        return sum(len(pairs) for pairs in self.strips.values())


def _OwnershipBodyIndex(packName, animFiles):
    """注册键 → (文件序, 动画ID, 动画体); 主命名空间优先, arm 命名空间补非并行键(与解析器一致)"""
    mainPrefix = "animation.{}.".format(packName)
    armPrefix = "animation.{}_arm.".format(packName)
    index = OrderedDict()
    for prefix in (mainPrefix, armPrefix):
        for fileIndex, (_path, data) in enumerate(animFiles):
            for animId, body in (data.get("animations") or {}).items():
                if not isinstance(body, dict) or not animId.startswith(prefix):
                    continue
                key = str(animId[len(prefix):])
                if prefix == armPrefix and _PARALLEL_SHORT_PATTERN.match(key):
                    continue
                index.setdefault(key, (fileIndex, animId, body))
    return index


def _StateAnimationItems(state):
    """控制器状态的 animations 条目 → [(条目, [(动画键, 条件或 None)])]"""
    out = []
    for item in state.get("animations") or []:
        entries = []
        for ref, condition in (item.items() if isinstance(item, dict) else [(item, None)]):
            if not isinstance(ref, (str, unicode)):  # noqa: F821
                continue
            if condition is not None and not isinstance(condition, (str, unicode)):  # noqa: F821
                condition = None if condition else "0"
            entries.append((str(ref), condition))
        out.append((item, entries))
    return out


def _CollectOwnershipOccurrences(bodyIndex, ctlFiles, mainKeys):
    """全部宿主里的动画出现 → (出现列表, 被第一人称/非通道控制器引用的键集)"""
    occurrences, blocked, driven, controllerNames = [], set(), set(), set()
    for _path, data in ctlFiles:
        for ctlId, body in (data.get("animation_controllers") or {}).items():
            if not isinstance(body, dict):
                continue
            name = str(ctlId).split(".")[-1]
            controllerNames.add(name)
            rank = JavaChannelRank(name)
            states = body.get("states") or {}
            stateNames = [stateName for stateName in states if isinstance(states[stateName], dict)]
            single = len(stateNames) == 1 and not states[stateNames[0]].get("transitions")
            for stateIndex, stateName in enumerate(stateNames, 1):
                for _item, entries in _StateAnimationItems(states[stateName]):
                    for ref, condition in entries:
                        driven.add(ref)
                        if rank is None:
                            blocked.add(ref)
                            continue
                        # Java PLAY_ONCE 尾过渡结束即转空闲、不再写通道(见 FadeControllerOneShots 注)
                        base, fade = SplitPlayOnceFade(condition)
                        if fade is not None:
                            active = PlayOnceActiveCondition(fade)
                            condition = active if base is None else u"({})&&({})".format(base, active)
                        occurrences.append(_OwnershipOccurrence(
                            ref, rank, name, stateName, stateIndex, condition,
                            alwaysActive=single))
    for name in controllerNames:
        matched = re.match(r"^player_(pre_)?parallel_(\d+)$", name)
        if matched:
            driven.add("{}parallel{}".format(matched.group(1) or "", matched.group(2)))
    for key in bodyIndex:
        matched = _BARE_PARALLEL_PATTERN.match(key)
        if not matched or key in driven:
            continue
        pre = bool(matched.group(1))
        channel = "player_{}parallel_{}".format("pre_" if pre else "", matched.group(2))
        occurrences.append(_OwnershipOccurrence(
            key, (0 if pre else 22, channel), BARE_HOST_PREFIX + key,
            additiveRotation=not pre, alwaysActive=True))
    members = [key for key, _condition in _BuildJavaStateAnimates(list(mainKeys))]
    for stateIndex, key in enumerate(members, 1):
        occurrences.append(_OwnershipOccurrence(
            key, _MAIN_CHANNEL_RANK, _STATE_CHAIN_KEY, key, stateIndex))
    return occurrences, blocked


JAVA_BASELINE_PACK = "java_default"


def BaselineAnimationLoops(packName):
    """Java 默认模型基线(java_default 移植产物)的主域注册键 → loop 字段; 基线自身 / 基线缺席返回空表。

    Java 模型缺某动画键时回落内置 default 模型的同名动画(AnimationStore fallback), 拉弓/举盾/三叉戟蓄力
    这 57 条手持条件动画酒狐各包都没写, 全靠它。口径与 packParser._JavaDefaultBaseline 一致: 主命名空间 +
    arm 命名空间的非并行键, 伴生键除外 —— Java 的并行通道只发现模型**自有**的 parallelN
    (ParallelControllerDiscovery 遍历本地键集, 回落不算)。
    """
    if packName == JAVA_BASELINE_PACK:
        return OrderedDict()
    mainLoops, _fpLoops = CollectPackAnimationLoops(JAVA_BASELINE_PACK, root=REF_RP)
    return OrderedDict((key, loop) for key, loop in mainLoops.items()
                       if not _PARALLEL_SHORT_PATTERN.match(key)
                       and not _OWNERSHIP_COMPANION_PATTERN.match(key))


def CollectBaselineVariables(packName):
    """默认模型基线动画引用的 molang 变量短名(包自身是基线时为空集)。

    基线动画并进各包动画表后会在这些包上播放(拉弓/挥剑/游泳 ...), 里面读的 v.qh / v.random / v.bv 等
    变量要进该包的初始化表 —— 否则基岩按未定义变量报错(Java 读作 0)。运行层 packParser._JavaDefaultBaseline
    同样把基线命名空间的变量并进文件扫描, 两边口径一致。
    """
    if packName == JAVA_BASELINE_PACK:
        return set()
    folder = os.path.join(REF_RP, "animations", JAVA_BASELINE_PACK)
    names = set()
    if not os.path.isdir(folder):
        return names
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(folder, name), "rb") as handle:
            names.update(_MOLANG_VAR_SCAN.findall(handle.read().decode("utf-8")))
    return names


def _MainChainKeys(packName):
    """主链状态机的成员口径(与 BuildStateChainControllerFile 一致, 伴生键除外); 并入默认模型基线键:
    Java 主链按标准状态名取动画, 缺的回落默认模型, 状态机只按自有键生成时基线状态(游泳/爬行等)进不了状态机"""
    mainLoops, _fpLoops = CollectPackAnimationLoops(packName)
    own = [key for key in mainLoops if not _OWNERSHIP_COMPANION_PATTERN.match(key)]
    return own + [key for key in BaselineAnimationLoops(packName) if key not in mainLoops]


# 带 override 的早层直挂条件动画按条件前缀取 Java 通道序(PlayerControllerCollection.init);
# 挥击/使用成员走一次性通道状态机, 甲槽是最晚的通道不必让位, 骑乘/死亡/爬梯归主链
_OVERRIDE_CONDITION_RANKS = {
    "hold_offhand": (7, "player_hold_offhand"),
    "hold_mainhand": (8, "player_hold_mainhand"),
    "passenger": (17, "player_passenger"),
}
_CARRY_ON_RANK = (18, "player_carry_on")
_ONESHOT_SWING_RANK = (12, "player_swing")
_ONESHOT_USE_RANK = (15, "player_use")
DIRECT_HOST_PREFIX = "direct:"
# 裸 pre_parallelN/parallelN(没有同名作者控制器、也没被控制器状态引用 → 主包直挂恒播)的宿主名前缀
BARE_HOST_PREFIX = "bare:"
# GUI 展示动画的宿主名前缀与 Java 通道序(cap 通道: carry_on 之后、gui_hover/gui_focus/parallel 之前)
CAP_HOST_PREFIX = "cap:"
_CAP_CHANNEL_RANK = (19, "player_cap")
# "卡片正在播展示动画"的判据: 选择界面卡片的纸娃娃经 molang_dict 置 variable.ysm_show = 1(大预览窗是 ysm_preview,
# 播预览动作表不播展示动画); 世界里的玩家没有这个变量, ??0 读作 0
PREVIEW_SHOW_TERM = u"(variable.ysm_show??0)>0"
_PRE_PARALLEL_SHORT_PATTERN = re.compile(r"^pre_parallel\d+$")


def _PreviewAnimationKey(manifest):
    """ysm.json properties.preview_animation(移植期已规整成资源键)→ 键; 未声明 → None"""
    preview = ((manifest or {}).get("properties") or {}).get("preview_animation")
    return str(preview) if isinstance(preview, (str, unicode)) and preview else None  # noqa: F821
# 不带 override 的伴生让出通道时保留的权重(见本节长注: 权重 0 暂停计时并卡住 all_animations_finished)
_OWNERSHIP_WEIGHT_FLOOR = u"0.0001"
_OWNERSHIP_WEIGHT_SPAN = u"0.9999"


def _DirectConditionRank(key):
    """直挂条件动画键 → 需要给晚层让位时的 Java 通道序; 不归直挂 override 规划的键 → None"""
    parsed = _SplitConditionKey(key)
    if parsed is not None:
        return _OVERRIDE_CONDITION_RANKS.get(parsed[0][0])
    if _UnescapeConditionKey(key).startswith("carryon:") or key.startswith("carryon_"):
        return _CARRY_ON_RANK
    return None


def _CollectOverrideOccurrences(bodyIndex, mainKeys):
    """早层在直挂条件条目 / 一次性通道状态机里的出现(作者控制器里的出现由
    _CollectOwnershipOccurrences 收集)。口径与主包一致: 直挂条目取 packParser._BuildConditionalAnimates
    的 Java 模式产出, 挥击/使用成员取 BuildOneShotControllers 的成员判定。"""
    keys = [key for key in mainKeys if not _OWNERSHIP_COMPANION_PATTERN.match(key)]
    occurrences = []
    for key, _condition in _BuildConditionalAnimates(keys, [], withFallbacks=True, javaMode=True):
        rank = _DirectConditionRank(key)
        if rank is not None:
            occurrences.append(_OwnershipOccurrence(key, rank, DIRECT_HOST_PREFIX + key))
    channels = [(_OneShotSwingMembers(keys), _ONESHOT_SWING_RANK, _ONESHOT_SWING_KEY)]
    for prefix, ctlKey, slot, handIndex, _trigger in _ONESHOT_USE_CHANNELS:
        channels.append((_OneShotConditionMembers(keys, prefix, slot, handIndex, prefix),
                         _ONESHOT_USE_RANK, ctlKey))
    for (members, fallback), rank, host in channels:
        for key in [member for member, _test in members] + ([fallback] if fallback else []):
            occurrences.append(_OwnershipOccurrence(key, rank, host))
    return [occurrence for occurrence in occurrences if occurrence.key in bodyIndex]


def _IsConstantZeroVector(value):
    """通道值恒为零向量: 常量 [0,0,0], 或每个关键帧的 pre/post 都是数值零向量(表达式一律不算)"""
    def ZeroList(vector):
        return (isinstance(vector, list) and len(vector) == 3
                and all(isinstance(c, (int, float)) and not isinstance(c, bool) and c == 0 for c in vector))
    if ZeroList(value):
        return True
    if not isinstance(value, dict) or not value:
        return False
    for frame in value.values():
        if isinstance(frame, dict):
            sides = [frame[side] for side in ("pre", "post") if side in frame]
            if not sides or not all(ZeroList(side) for side in sides):
                return False
        elif not ZeroList(frame):
            return False
    return True


def _GroupOwnershipPairs(plan, key, body, keyOccurrences, writers, extraWriters=None):
    """一条早层动画的 (骨骼, 通道) 按"让出签名"分组 → (分组, 宿主列表) 或 None。
    晚层恒活跃的通道记进 plan.strips。writers / extraWriters: (骨骼, 通道) → [(出现, 该出现的通道值)]。

    **晚层把缩放恒写成 0 不算冲突**: 基岩缩放相乘, 0 × 早层任意值 = 0 = Java 晚层胜出的结果, 早层不必让出。
    让出反而出错 —— 状态机进入晚层状态有 blend_transition 交叉淡化(晚层权重 0→1, 缩放贡献从 1 渐变到 0),
    伴生当帧让出后这 0.1s 里没人把骨骼压在 0 上: 小小酒狐 pre_parallel2 把飞机骨骼 vv 藏成 0, run 同样写 0,
    起跑瞬间整架飞机闪出来(2026-09-17 用户实机); Java 的过渡从骨骼当前值(早层的 0)插到 0, 始终不可见。
    """
    hosts = sorted(set(o.host for o in keyOccurrences))
    hostRank = dict((o.host, o.rank) for o in keyOccurrences)
    groups = OrderedDict()
    strips = []
    for boneName, channels in (body.get("bones") or {}).items():
        if not isinstance(channels, dict):
            continue
        for channel in channels:
            if channel not in _OWNERSHIP_CHANNELS or _HasMolangAssignment(channels[channel]):
                continue
            laterWriters = list(writers.get((boneName, channel), ()))
            if extraWriters:
                laterWriters += list(extraWriters.get((boneName, channel), ()))
            signature = []
            for host in hosts:
                stateTerms, dynamicTerms, full = set(), set(), False
                for later, laterValue in laterWriters:
                    if later.host == host or not later.rank > hostRank[host]:
                        continue
                    if channel == "rotation" and later.additiveRotation:
                        continue
                    if channel == "scale" and _IsConstantZeroVector(laterValue):
                        continue
                    stateTerm, dynamicTerm = later.Activity()
                    if stateTerm is None and dynamicTerm is None:
                        full = True
                        break
                    if stateTerm is not None:
                        stateTerms.add(stateTerm)
                    if dynamicTerm is not None:
                        dynamicTerms.add((dynamicTerm, None if later.alwaysActive else later.host))
                if full:
                    signature.append("0")
                elif stateTerms or dynamicTerms:
                    signature.append((tuple(sorted(stateTerms)), tuple(sorted(dynamicTerms))))
                else:
                    signature.append(None)
            if all(core is None for core in signature):
                continue
            if all(core == "0" for core in signature):
                strips.append((boneName, channel))     # 晚层恒活跃: 无需宿主, 直接删
                continue
            groups.setdefault(tuple(signature), []).append((boneName, channel))
    if strips:
        plan.strips[key] = strips
    return (groups, hosts) if groups else None


def _PlanChannelOwnership(packName, animFiles, ctlFiles, mainKeys, manifest):
    """在已撤销的内存数据上规划(见本节长注); 返回 ChannelOwnershipPlan"""
    plan = ChannelOwnershipPlan()
    bodyIndex = _OwnershipBodyIndex(packName, animFiles)
    occurrences, blocked = _CollectOwnershipOccurrences(bodyIndex, ctlFiles, mainKeys)
    mainMembers = set(o.key for o in occurrences if o.host == _STATE_CHAIN_KEY)
    writers = {}
    byKey = OrderedDict()           # 不带 override 的早层(同时是晚层写入者)
    overrideByKey = OrderedDict()   # 带 override 的早层(不当写入者: 排在被覆盖层之后, 靠标志清空它们)
    for occurrence in occurrences:
        entry = bodyIndex.get(occurrence.key)
        if entry is None:
            continue
        if entry[2].get("override_previous_animation"):
            overrideByKey.setdefault(occurrence.key, []).append(occurrence)
            continue
        byKey.setdefault(occurrence.key, []).append(occurrence)
        for boneName, channels in (entry[2].get("bones") or {}).items():
            if not isinstance(channels, dict):
                continue
            for channel in channels:
                if channel in _OWNERSHIP_CHANNELS:
                    writers.setdefault((boneName, channel), []).append((occurrence, channels[channel]))
    for occurrence in _CollectOverrideOccurrences(bodyIndex, mainKeys):
        if bodyIndex[occurrence.key][2].get("override_previous_animation"):
            overrideByKey.setdefault(occurrence.key, []).append(occurrence)
    fallbackKeys = set(key for key, _prefix, _gate in _CONDITION_FALLBACK_KEYS)
    rouletteKeys = _RouletteAnimationKeys(manifest)
    # GUI 展示动画(properties.preview_animation)播在 Java cap 通道: carry_on 之后、parallel 之前
    # (PlayerControllerCollection.init)。展示动画同时是主链成员/轮盘键/控制器引用时它在别处还有别的通道序, 不另算。
    previewKey = _PreviewAnimationKey(manifest)
    hasCap = (previewKey in bodyIndex and previewKey not in mainMembers and previewKey not in rouletteKeys
              and previewKey not in blocked and previewKey not in byKey and previewKey not in overrideByKey
              and not _PARALLEL_SHORT_PATTERN.match(previewKey))
    # ① cap 晚于 pre_parallel: 卡片上展示动画覆盖 pre_parallel 同写的通道(pre 族不是"内置并行旋转相加", 连旋转也覆盖)。
    #    官方酒狐各包 pre_parallel4 把舞台骨骼(gui / Background1 / Backgrounds)缩成 0 藏起来, gui 动画再写回 1 —— 基岩
    #    0 × 1 = 0, 卡片上幕布/背景板全不显示(2026-09-17 用户实机; gui 动画自带的 override 标志没把它们清掉,
    #    只有 gui 缩放写成关键帧的莫莫酒狐显示出来, 疑似常量单位缩放在加载期被引擎丢弃)。做法同世界里的早层: 同写的通道
    #    拆进伴生, 让出条件 = "卡片在播展示动画" = variable.ysm_show; 世界里该变量恒 0, 伴生恒播 = 原动画。
    #    预览实体注册伴生时套同一份权重(channel_ownership → packParser preview_parallel_weights → previewRender)。
    capWriters = {}
    if hasCap:
        capShown = _OwnershipOccurrence(previewKey, _CAP_CHANNEL_RANK, CAP_HOST_PREFIX + previewKey,
                                        condition=PREVIEW_SHOW_TERM, alwaysActive=True)
        for boneName, channels in (bodyIndex[previewKey][2].get("bones") or {}).items():
            if not isinstance(channels, dict):
                continue
            for channel in channels:
                if channel in _OWNERSHIP_CHANNELS:
                    capWriters[(boneName, channel)] = [(capShown, channels[channel])]
    pending = []     # (键, {签名: [通道对]}, 宿主列表)
    for key, keyOccurrences in byKey.items():
        if key in blocked or key in rouletteKeys:
            continue
        if key not in mainMembers and (_SplitConditionKey(key) is not None or key in fallbackKeys):
            continue
        grouped = _GroupOwnershipPairs(plan, key, bodyIndex[key][2], keyOccurrences, writers,
                                       capWriters if _PRE_PARALLEL_SHORT_PATTERN.match(key) else None)
        if grouped:
            pending.append((key,) + grouped)
    for key, keyOccurrences in overrideByKey.items():
        if key in blocked or key in rouletteKeys:
            continue
        grouped = _GroupOwnershipPairs(plan, key, bodyIndex[key][2], keyOccurrences, writers)
        if grouped:
            plan.overrideKeys.add(key)
            pending.append((key,) + grouped)
    # ② parallel 晚于 cap: 卡片上裸 parallel 恒播覆盖展示动画的位移/缩放(旋转相加不动)。实例: 小小酒狐
    #    gui 把 Root 放大 1.6, parallel1 又按体型变量写 Root 缩放 —— Java 取后者, 基岩 1.6 × 1 卡片偏大
    #    (2026-09-17 用户报告)。只删不拆: 预览实体只叠并行族动画(作者控制器不在预览里跑), 只对照裸并行。
    if hasCap:
        bareWriters = dict((pair, [(o, v) for o, v in items if o.host.startswith(BARE_HOST_PREFIX)])
                           for pair, items in writers.items())
        capOccurrence = _OwnershipOccurrence(previewKey, _CAP_CHANNEL_RANK, CAP_HOST_PREFIX + previewKey)
        _GroupOwnershipPairs(plan, previewKey, bodyIndex[previewKey][2], [capOccurrence], bareWriters)
        if plan.strips.get(previewKey):
            plan.previewStrips = (previewKey, len(plan.strips[previewKey]))
    # 纯状态集合编号: 表达式字典序(输入相同则编号相同, 幂等)
    setExpressions = set()
    for _key, groups, _hosts in pending:
        for signature in groups:
            for core in signature:
                if isinstance(core, tuple) and core[0]:
                    setExpressions.add((_StateSetExpression(core[0]), core[0]))
    setVariables = {}
    for number, (expression, stateTerms) in enumerate(sorted(setExpressions), 1):
        variable = u"{}{}".format(OWNERSHIP_SET_PREFIX, number)
        involved = set(host for host, _index in stateTerms)
        plan.stateSets[variable] = (expression, involved)
        plan.indexedHosts |= involved
        setVariables[stateTerms] = variable
    oneShotHosts = set([_ONESHOT_SWING_KEY] + [ctlKey for _p, ctlKey, _s, _h, _t in _ONESHOT_USE_CHANNELS])
    armPrefix = "animation.{}_arm.".format(packName)
    for key, groups, hosts in pending:
        companions = []
        ordered = sorted(groups.items(), key=lambda item: repr(item[0]))
        for number, (signature, pairs) in enumerate(ordered, 1):
            cores = OrderedDict()
            for host, core in zip(hosts, signature):
                if isinstance(core, tuple):
                    stateTerms, dynamicTerms = core
                    parts = []
                    if stateTerms:
                        parts.append(u"({}??0)".format(setVariables[stateTerms]))
                    for term, termHost in dynamicTerms:
                        parts.append(term)
                        if termHost is not None:
                            plan.indexedHosts.add(termHost)
                    core = u"!({})".format(u"||".join(parts))
                cores[host] = core
            companions.append(("{}__own{}".format(key, number), sorted(
                pairs, key=lambda pair: (pair[0], _OWNERSHIP_CHANNELS.index(pair[1]))), cores))
        plan.companions[key] = companions
        if key in mainMembers:
            plan.mainCompanions[key] = [
                (companionKey, _CompanionWeight(None, cores[_STATE_CHAIN_KEY], floor=True))
                for companionKey, _pairs, cores in companions
                if cores.get(_STATE_CHAIN_KEY) != "0"]
        # 裸并行直挂早层(小小酒狐 pre_parallel4 的表情缩放 × 奔跑/潜行的脸部缩放): 伴生由主包紧跟裸条目
        # 直挂(packParser._OrderJavaAnimates), 权重不含渲染域门; 不带 override 的带下限(直挂条目权重 0
        # 同样暂停计时), 带 override 的精确 0
        bareHost = BARE_HOST_PREFIX + key
        if bareHost in hosts:
            plan.bareKeys.add(key)
            for companionKey, _pairs, cores in companions:
                core = cores.get(bareHost)
                if core is not None and core != "0":
                    plan.directWeights[companionKey] = _CompanionWeight(
                        None, core, floor=key not in plan.overrideKeys)
        # 给展示动画让位的 pre_parallel 伴生: 预览实体要按权重注册(见上 ①)。裸键的权重上面已记;
        # 作者控制器接管的 pre_parallel(世界里伴生挂在控制器状态里)另记一份供预览用 —— 主包直挂时只认
        # 直挂条目表里的键, 这份权重不会被世界侧误用
        for companionKey, _pairs, cores in companions:
            shown = [core for core in cores.values()
                     if isinstance(core, (str, unicode)) and PREVIEW_SHOW_TERM in core]  # noqa: F821
            if not shown:
                continue
            plan.previewCompanions.add(companionKey)
            plan.directWeights.setdefault(companionKey, _CompanionWeight(
                None, shown[0], floor=key not in plan.overrideKeys))
        if key not in plan.overrideKeys:
            continue
        # arm 命名空间的动画同时注册成 fp_ 键: 第一人称挥击状态机成员也要带上伴生(那里没有晚层, 恒播)
        armBody = bodyIndex[key][1].startswith(armPrefix)
        for companionKey, _pairs, cores in companions:
            for host, core in cores.items():
                if core == "0":
                    continue
                if host.startswith(DIRECT_HOST_PREFIX):
                    if core is not None:
                        plan.directWeights[companionKey] = core
                elif host in oneShotHosts:
                    plan.oneShotCompanions.setdefault(key, []).append((companionKey, core))
                    if armBody and host == _ONESHOT_SWING_KEY:
                        plan.oneShotCompanions.setdefault("fp_" + key, []).append(
                            ("fp_" + companionKey, None))
    plan.directWeights = OrderedDict(sorted(plan.directWeights.items()))
    return plan


def _CompanionWeight(condition, core, floor=False):
    """伴生条目权重: 原条目条件 && 核心权重; None = 无条件恒播。

    floor: 不带 override 的伴生让出通道时保留 1e-4(`核心*0.9999+0.0001`), 原条件为假时仍是 0 ——
    与原动画一起暂停/计时, 见本节长注。带 override 的伴生必须精确 0 才不清空晚层(不加下限)。
    """
    if core is None:
        return condition
    if floor:
        core = u"(({})*{}+{})".format(core, _OWNERSHIP_WEIGHT_SPAN, _OWNERSHIP_WEIGHT_FLOOR)
        if condition is None:
            return core
        return u"(({})?{}:0)".format(condition, core)
    if condition is None:
        return core
    return u"({})&&{}".format(condition, core)


def _ApplyOwnershipPlan(packName, plan, animFiles, ctlFiles):
    """把规划写进内存数据(动画体/作者控制器状态); 直挂条件动画与一次性通道成员的伴生条目不在
    作者控制器里, 分别由 ysm.json channel_ownership 与 BuildOneShotControllerFile 落地"""
    bodyIndex = _OwnershipBodyIndex(packName, animFiles)
    for key, pairs in plan.strips.items():
        body = bodyIndex[key][2]
        for boneName, channel in pairs:
            channels = (body.get("bones") or {}).get(boneName)
            if isinstance(channels, dict):
                channels.pop(channel, None)
        SanitizeAnimationBody(body)
    for key, companions in plan.companions.items():
        fileIndex, animId, body = bodyIndex[key]
        prefix = animId[:len(animId) - len(key)]
        built = []
        for companionKey, pairs, _cores in companions:
            companion = OrderedDict((field, copy.deepcopy(value)) for field, value in body.items()
                                    if field not in _OWNERSHIP_COMPANION_SKIPPED_FIELDS)
            bones = OrderedDict()
            for boneName, channel in pairs:
                bones.setdefault(boneName, OrderedDict())[channel] = body["bones"][boneName][channel]
            companion["bones"] = bones
            built.append((prefix + companionKey, companion))
        for _companionKey, pairs, _cores in companions:
            for boneName, channel in pairs:
                body["bones"][boneName].pop(channel, None)
        SanitizeAnimationBody(body)
        animations = animFiles[fileIndex][1]["animations"]
        rebuilt = OrderedDict()
        for existingId, existing in animations.items():
            rebuilt[existingId] = existing
            if existingId == animId:
                for companionId, companion in built:
                    rebuilt[companionId] = companion
        animations.clear()
        animations.update(rebuilt)
    for _path, data in ctlFiles:
        for ctlId, body in (data.get("animation_controllers") or {}).items():
            if not isinstance(body, dict):
                continue
            name = str(ctlId).split(".")[-1]
            ranked = JavaChannelRank(name) is not None
            statements = plan.HostStatements(name)
            states = body.get("states") or {}
            stateNames = [stateName for stateName in states if isinstance(states[stateName], dict)]
            for stateIndex, stateName in enumerate(stateNames, 1):
                state = states[stateName]
                if ranked and isinstance(state.get("animations"), list):
                    rebuilt, grew = [], False
                    for item, entries in _StateAnimationItems(state):
                        rebuilt.append(item)
                        for ref, condition in entries:
                            base, fade = SplitPlayOnceFade(condition)
                            for companionKey, _pairs, cores in plan.companions.get(ref, ()):
                                core = cores.get(name)
                                if core == "0":
                                    continue
                                weight = _CompanionWeight(base, core, floor=ref not in plan.overrideKeys)
                                if fade is not None:
                                    # 伴生跟原条目同步淡出(淡出式的下限已按 override 取好)
                                    fadeWeight = PlayOnceFadeWeight(fade)
                                    weight = fadeWeight if weight is None else u"({})*{}".format(weight, fadeWeight)
                                rebuilt.append(companionKey if weight is None
                                               else OrderedDict([(companionKey, weight)]))
                                grew = True
                    if grew:
                        state["animations"] = rebuilt
                if name in plan.indexedHosts:
                    onEntry = state.get("on_entry")
                    if isinstance(onEntry, (str, unicode)):  # noqa: F821
                        onEntry = [onEntry]
                    state["on_entry"] = list(onEntry or []) + [
                        u"{} = {};".format(OwnershipVariable(name), stateIndex)] + statements


def PlanChannelOwnership(packName):
    """只读规划(不落盘): 供 BuildStateChainControllerFile / BuildOneShotControllerFile 取伴生与占用变量"""
    animFiles = _LoadPackAnimationFiles(packName)
    ctlFiles = _OwnershipControllerFiles(packName)
    _UndoChannelOwnershipData(packName, animFiles, ctlFiles)
    manifestPath = PackManifestPath(packName)
    manifest = LoadJson(manifestPath) if os.path.isfile(manifestPath) else {}
    return _PlanChannelOwnership(packName, animFiles, ctlFiles, _MainChainKeys(packName), manifest)


OWNERSHIP_MANIFEST_KEY = "channel_ownership"


def _WriteOwnershipManifest(packName, weights, manifest=None):
    """ysm.json 顶级 channel_ownership = 直挂条件动画伴生的核心权重(packParser._AttachOwnershipCompanions
    读取); 无内容时删键。manifest: 调用方持有的内存副本, 同步改写。返回磁盘文件是否改动。"""
    if manifest is not None:
        if weights:
            manifest[OWNERSHIP_MANIFEST_KEY] = OrderedDict(weights)
        else:
            manifest.pop(OWNERSHIP_MANIFEST_KEY, None)
    path = PackManifestPath(packName)
    if not os.path.isfile(path):
        return False
    data = LoadJson(path)
    before = _SerializeJson(data)
    if weights:
        data[OWNERSHIP_MANIFEST_KEY] = OrderedDict(weights)
    else:
        data.pop(OWNERSHIP_MANIFEST_KEY, None)
    if _SerializeJson(data) == before:
        return False
    DumpJson(path, data)
    return True


def ApplyChannelOwnership(packName, manifest=None):
    """Java 逐通道覆盖 → 伴生动画 + 占用变量(见本节长注); 返回 (规划, 改写的文件数)。幂等。

    一次性通道状态机的伴生条目不在这里写(那份文件是生成物): 调用方随后用返回的规划重建
    BuildOneShotControllerFile。
    """
    animFiles = _LoadPackAnimationFiles(packName)
    ctlFiles = _OwnershipControllerFiles(packName)
    before = dict((path, _SerializeJson(data)) for path, data in animFiles + ctlFiles)
    _UndoChannelOwnershipData(packName, animFiles, ctlFiles)
    if manifest is None:
        manifestPath = PackManifestPath(packName)
        manifest = LoadJson(manifestPath) if os.path.isfile(manifestPath) else {}
        memoryManifest = None
    else:
        memoryManifest = manifest
    plan = _PlanChannelOwnership(packName, animFiles, ctlFiles, _MainChainKeys(packName), manifest)
    _ApplyOwnershipPlan(packName, plan, animFiles, ctlFiles)
    written = 0
    for path, data in animFiles + ctlFiles:
        if _SerializeJson(data) != before.get(path):
            DumpJson(path, data)
            written += 1
    if _WriteOwnershipManifest(packName, plan.directWeights, memoryManifest):
        written += 1
    return plan, written


def OwnershipReportLines(plan):
    """规划结果 → 移植/修复报告行(无内容时空列表)"""
    lines = []
    if plan.CompanionCount() or plan.StrippedPairCount():
        lines.append(u"Java 逐通道覆盖: 伴生动画 {} 条(搬出 {} 个骨骼通道, 晚层状态活跃时让出), "
                     u"晚层恒活跃直接删 {} 个; 占用变量宿主 {}, 预算状态集合 {} 组".format(
                         plan.CompanionCount(), plan.MovedPairCount(), plan.StrippedPairCount(),
                         u", ".join(sorted(plan.indexedHosts)) or u"无", len(plan.stateSets)))
    if plan.overrideKeys:
        lines.append(u"其中带 override 的早层 {} 条动画拆出伴生 {} 条(精确 0 让位给晚层并行控制器): "
                     u"直挂条件动画核心权重 {} 条写入 ysm.json {}, 一次性通道成员 {} 个".format(
                         len(plan.overrideKeys), plan.OverrideCompanionCount(),
                         len(plan.directWeights), OWNERSHIP_MANIFEST_KEY,
                         len([key for key in plan.oneShotCompanions if not key.startswith("fp_")])))
    if plan.previewStrips:
        lines.append(u"GUI 展示动画 {}: 被裸 parallel 恒播覆盖的位移/缩放 {} 个通道已删(Java cap 通道早于 parallel, "
                     u"预览里 parallel 族注册在展示动画之后)".format(*plan.previewStrips))
    if plan.previewCompanions:
        lines.append(u"GUI 展示动画覆盖 pre_parallel: 伴生 {} 条在卡片上(variable.ysm_show)让出同写的通道 —— Java cap 通道晚于 "
                     u"pre_parallel, 舞台骨骼(幕布/背景板)由展示动画放出来; 权重写入 ysm.json {}".format(
                         len(plan.previewCompanions), OWNERSHIP_MANIFEST_KEY))
    if plan.bareKeys:
        bareCompanions = [companionKey for key in sorted(plan.bareKeys)
                          for companionKey, _pairs, _cores in plan.companions.get(key, ())]
        lines.append(u"其中裸 pre_parallel/parallel 直挂早层 {} 条({})拆出伴生 {} 条: 主包紧跟裸条目直挂, "
                     u"权重写入 ysm.json {}".format(
                         len(plan.bareKeys), u", ".join(sorted(plan.bareKeys)), len(bareCompanions),
                         OWNERSHIP_MANIFEST_KEY))
    return lines


def UndoChannelOwnership(packName):
    """把 ApplyChannelOwnership 的改写从磁盘上撤掉(修复工具在所有步骤之前调用, 让中间步骤
    看到与移植期相同的数据, 含 ysm.json 的 channel_ownership); 返回撤销的伴生动画数。"""
    animFiles = _LoadPackAnimationFiles(packName)
    ctlFiles = _OwnershipControllerFiles(packName)
    before = dict((path, _SerializeJson(data)) for path, data in animFiles + ctlFiles)
    undone = _UndoChannelOwnershipData(packName, animFiles, ctlFiles)
    for path, data in animFiles + ctlFiles:
        if _SerializeJson(data) != before.get(path):
            DumpJson(path, data)
    _WriteOwnershipManifest(packName, None)
    return undone


# ---- 作者状态机的"动画播完"判据: Java 口径改写(RewriteFinishedQueries) ----
# Java(BedrockAnimationController.updateState): 状态里每条动画无论 apply 条件真假都在走时钟;
# all_animations_finished = "条件为真的动画都播完了"(没有条件为真的动画 → 真), any_animation_finished
# = "有条件为真的动画播完了"(没有条件为真的动画 → 真)。基岩: 权重 0 的动画**暂停计时**, 并且算作
# 没播完 —— all_animations_finished 在"状态里有权重为 0 的条目"时永不成立(2026-09-17 实机, 见
# ApplyChannelOwnership 长注)。实例: 凋灵娘 player_pre_parallel_1 的 jump_up 状态挂着
# {jump_up_hover: 悬浮} / {jump_up_ground: 落地} 两条互斥条件动画, 必有一条权重 0 → 跳跃中永远
# 进不了 jump_down(Java 里上升段播完就切下落); 萨赫梅特推进器展开/收纳两态同理(v.Boost 1/2)。
# 改写: 状态进入时记 `variable.ysm_t0_<控制器> = query.life_time`, 判据换成按条件与动画长度的显式式子。
# 只改真正会分叉的状态 —— 有带条件的作者条目, 或挂了精确 0 让位的 override 伴生; 其余状态保留原生
# 查询(二者等价)。计时口径取基岩(状态进入即开始), 不计 Java 起始过渡(blend)的推迟。引用了子控制器、
# 缺 animation_length 或自定义 anim_time_update 的状态保持原样。幂等: 改写后不再含原生查询。
STATE_CLOCK_PREFIX = "variable.ysm_t0_"
_FINISHED_QUERY_PATTERN = re.compile(r"\b(?:q|query)\.(all_animations_finished|any_animation_finished)\b")


def StateClockVariable(controllerName):
    """控制器(注册短名)的状态进入时刻变量"""
    return STATE_CLOCK_PREFIX + re.sub(r"[^a-z0-9_]", "_", str(controllerName).lower())


def _FormatSeconds(value):
    text = "%.4f" % float(value)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _FinishedQueryExpressions(items, bodyIndex, clock):
    """[(动画键, 条件或 None)] → (all 式, any 式); 无法静态展开 → None"""
    elapsed = u"(query.life_time-({}??0))".format(clock)
    allParts, anyParts, conditions = [], [], []
    alwaysApplied = False
    for ref, condition in items:
        entry = bodyIndex.get(ref)
        if entry is None:
            return None                      # 子控制器/缺动画: 保持原生判据
        body = entry[2]
        if body.get("anim_time_update") is not None:
            return None
        length = body.get("animation_length")
        if not isinstance(length, (int, float)) or isinstance(length, bool):
            return None
        finished = u"0" if length >= JAVA_INFINITE_LENGTH else u"{}>={}".format(
            elapsed, _FormatSeconds(length))
        if condition is None:
            alwaysApplied = True
            allParts.append(u"({})".format(finished))
            anyParts.append(u"({})".format(finished))
        else:
            allParts.append(u"(!({})||{})".format(condition, finished))
            anyParts.append(u"(({})&&{})".format(condition, finished))
            conditions.append(u"({})".format(condition))
    if not alwaysApplied:
        anyParts.append(u"!({})".format(u"||".join(conditions)))
    return u"({})".format(u"&&".join(allParts)), u"({})".format(u"||".join(anyParts))


def _RewriteFinishedQueriesData(ctlFiles, bodyIndex, overrideKeys):
    """RewriteFinishedQueries 的内存版: 就地改写 ctlFiles 数据; 返回 (改写的状态数, 改动的文件路径集)"""
    rewritten, dirtyPaths = 0, set()
    for path, data in ctlFiles:
        for ctlId, body in (data.get("animation_controllers") or {}).items():
            if not isinstance(body, dict):
                continue
            clock = StateClockVariable(str(ctlId).split(".")[-1])
            for state in (body.get("states") or {}).values():
                if not isinstance(state, dict):
                    continue
                transitions = [item for item in state.get("transitions") or [] if isinstance(item, dict)]
                if not any(isinstance(value, (str, unicode))  # noqa: F821
                           and _FINISHED_QUERY_PATTERN.search(value)
                           for item in transitions for value in item.values()):
                    continue
                items, diverges = [], False
                for _item, entries in _StateAnimationItems(state):
                    for ref, condition in entries:
                        companion = _OWNERSHIP_COMPANION_PATTERN.match(ref)
                        if companion:
                            # 不带 override 的伴生有权重下限、与原动画同步计时, 不影响判据
                            diverges = diverges or companion.group(1) in overrideKeys
                            continue
                        # PLAY_ONCE 淡出式不是 apply 条件: 下限 1e-4 照常计时, 精确 0(override)才会卡住原生判据
                        base, fade = SplitPlayOnceFade(condition)
                        items.append((ref, base))
                        diverges = diverges or base is not None or (fade is not None and fade[2] == u"0")
                if not diverges or not items:
                    continue
                expressions = _FinishedQueryExpressions(items, bodyIndex, clock)
                if expressions is None:
                    continue
                allExpression, anyExpression = expressions
                for item in transitions:
                    for target, value in list(item.items()):
                        if isinstance(value, (str, unicode)):  # noqa: F821
                            item[target] = _FINISHED_QUERY_PATTERN.sub(
                                lambda m: allExpression if m.group(1) == "all_animations_finished"
                                else anyExpression, value)
                onEntry = state.get("on_entry")
                onEntry = [onEntry] if isinstance(onEntry, (str, unicode)) else list(onEntry or [])  # noqa: F821
                statement = u"{} = query.life_time;".format(clock)
                if statement not in onEntry:
                    onEntry.insert(0, statement)
                state["on_entry"] = onEntry
                rewritten += 1
                dirtyPaths.add(path)
    return rewritten, dirtyPaths


def RewriteFinishedQueries(packName, ownership=None):
    """作者控制器里会与 Java 分叉的"播完"判据改写成显式计时(见本节注); 返回改写的状态数。
    ownership: ApplyChannelOwnership 的规划(识别带 override 的伴生); 缺省按磁盘现状只读规划。"""
    if ownership is None:
        ownership = PlanChannelOwnership(packName)
    bodyIndex = _OwnershipBodyIndex(packName, _LoadPackAnimationFiles(packName))
    ctlFiles = _OwnershipControllerFiles(packName)
    rewritten, dirtyPaths = _RewriteFinishedQueriesData(ctlFiles, bodyIndex, ownership.overrideKeys)
    for path, data in ctlFiles:
        if path in dirtyPaths:
            DumpJson(path, data)
    return rewritten


# ---- Java PLAY_ONCE 的尾过渡: 作者状态里的一次性动画(FadeControllerOneShots) ----
# Java(geckolib3 AnimationPlayer.process / setupEndingTransition): PLAY_ONCE 播到 animationLength 即
# "播完"(all/any_animations_finished 当帧成立), 随后用 3 tick(AnimationData.DEFAULT_ENDING_TRANSITION_LENGTH)
# 从末帧姿态插值到 0, 再转空闲 —— 骨骼队列停用, 不再写任何通道, 早层姿态重新露出。状态机一般在播完那一帧
# 就切走(下一状态的起始过渡接住末帧), 平时看不出差别; 状态**停留**时就露出来了: 萨赫梅特连段中切换物品
# (连段_N 的出口全要求持剑, 2026-09-17 用户实测 Java 攻击姿态自动收回, 基岩一直定格)、攻击后起跳等。
# 基岩侧 HoldControllerOneShots 为防出态闪帧补了 hold_on_last_frame(末帧无限保持)。本步排在它之前, 给
# 这些条目乘淡出权重 `clamp(1-(t-L)/0.15, 下限, 1)`(t = 进入状态后的秒数, 状态进入时写
# variable.ysm_t0_<控制器>), 逐通道覆盖规划把 `t < L+0.15` 当作条目仍在写通道(_CollectOwnershipOccurrences):
# 淡出期间早层照旧让出(姿态淡向 0, 同 Java 尾过渡), 结束后早层伴生收回通道(同 Java 转空闲)。
# 下限: 不带 override 的 1e-4(照常计时, 原生播完判据不受影响); 带 override 的精确 0(大于 0 就清空前序条目),
# 由 RewriteFinishedQueries 改成按计时判定。只处理 Java JSON 没写循环(PLAY_ONCE)、长度有限的动画 ——
# 显式 hold_on_last_frame 是作者要的保持(凋灵娘 攻击A/B/C), 不动。淡出式本身就是标记: 移植之后 loop 已被
# 改成 hold, 修复工具靠它识别并保持幂等。
PLAY_ONCE_FADE_SECONDS = 0.15
_PLAY_ONCE_FADE_BODY = (r"math\.clamp\(1-\(\(query\.life_time-\((variable\.ysm_t0_[a-z0-9_]+)\?\?0\)\)-([0-9.]+)\)"
                        r"/0\.15,(0\.0001|0),1\)")
_PLAY_ONCE_FADE_PATTERN = re.compile(r"^" + _PLAY_ONCE_FADE_BODY + r"$")
_PLAY_ONCE_GATED_FADE_PATTERN = re.compile(r"^\(\((.*)\)\?" + _PLAY_ONCE_FADE_BODY + r":0\)$")
_PLAY_ONCE_FLOOR = u"0.0001"


def PlayOnceFadeWeight(fade):
    """(时钟变量, 长度秒, 下限) → 淡出权重式"""
    clock, length, floor = fade
    return u"math.clamp(1-((query.life_time-({}??0))-{})/0.15,{},1)".format(
        clock, _FormatSeconds(length), floor)


def PlayOnceActiveCondition(fade):
    """淡出条目"仍在写通道"的判据(尾过渡结束前)"""
    clock, length, _floor = fade
    return u"(query.life_time-({}??0))<{}".format(clock, _FormatSeconds(length + PLAY_ONCE_FADE_SECONDS))


def SplitPlayOnceFade(condition):
    """状态条目权重 → (原条件或 None, (时钟变量, 长度秒, 下限) 或 None); 不带淡出式的原样返回"""
    if isinstance(condition, (str, unicode)):  # noqa: F821
        matched = _PLAY_ONCE_FADE_PATTERN.match(condition)
        if matched:
            return None, (matched.group(1), float(matched.group(2)), matched.group(3))
        matched = _PLAY_ONCE_GATED_FADE_PATTERN.match(condition)
        if matched:
            return matched.group(1), (matched.group(2), float(matched.group(3)), matched.group(4))
    return condition, None


def JoinPlayOnceFade(condition, fade):
    """SplitPlayOnceFade 的逆: 原条件 × 淡出(条件为假时精确 0, 与 Java apply 条件同义)"""
    if fade is None:
        return condition
    weight = PlayOnceFadeWeight(fade)
    return weight if condition is None else u"(({})?{}:0)".format(condition, weight)


def _FadeControllerOneShotsData(ctlFiles, bodyIndex):
    """FadeControllerOneShots 的内存版; 返回 ([(控制器短名, 状态名, 动画键)], 改动的文件路径集)"""
    faded, dirtyPaths = [], set()
    for path, data in ctlFiles:
        for ctlId, body in (data.get("animation_controllers") or {}).items():
            if not isinstance(body, dict):
                continue
            name = str(ctlId).split(".")[-1]
            clock = StateClockVariable(name)
            for stateName, state in (body.get("states") or {}).items():
                if not isinstance(state, dict) or not isinstance(state.get("animations"), list):
                    continue
                rebuilt, changed = [], False
                for item, entries in _StateAnimationItems(state):
                    replaced = OrderedDict(item) if isinstance(item, dict) else None
                    for ref, condition in entries:
                        entry = bodyIndex.get(ref)
                        base, fade = SplitPlayOnceFade(condition)
                        if (entry is None or fade is not None or base == "0"
                                or _OWNERSHIP_COMPANION_PATTERN.match(ref)
                                or _NormalizeLoopValue(entry[2].get("loop")) not in (None, False)):
                            continue
                        length = entry[2].get("animation_length")
                        if (not isinstance(length, (int, float)) or isinstance(length, bool)
                                or not 0 < length < JAVA_INFINITE_LENGTH):
                            continue
                        floor = u"0" if entry[2].get("override_previous_animation") else _PLAY_ONCE_FLOOR
                        if replaced is None:
                            replaced = OrderedDict()
                        replaced[ref] = JoinPlayOnceFade(base, (clock, float(length), floor))
                        faded.append((name, str(stateName), ref))
                        changed = True
                    rebuilt.append(item if replaced is None or replaced == item else replaced)
                if not changed:
                    continue
                state["animations"] = rebuilt
                onEntry = state.get("on_entry")
                onEntry = [onEntry] if isinstance(onEntry, (str, unicode)) else list(onEntry or [])  # noqa: F821
                statement = u"{} = query.life_time;".format(clock)
                if statement not in onEntry:
                    onEntry.insert(0, statement)
                state["on_entry"] = onEntry
                dirtyPaths.add(path)
    return faded, dirtyPaths


def FadeControllerOneShots(packName):
    """作者控制器状态里的 Java PLAY_ONCE 动画乘尾过渡淡出权重(见本节注); 返回 [(控制器, 状态, 动画键)]。
    幂等。必须排在 HoldControllerOneShots 之前(之后 loop 已是 hold, 认不出 PLAY_ONCE)。"""
    bodyIndex = _OwnershipBodyIndex(packName, _LoadPackAnimationFiles(packName))
    ctlFiles = _OwnershipControllerFiles(packName)
    faded, dirtyPaths = _FadeControllerOneShotsData(ctlFiles, bodyIndex)
    for path, data in ctlFiles:
        if path in dirtyPaths:
            DumpJson(path, data)
    return faded


def CollectPackAnimationLoops(packName, root=None):
    """RP 动画产物 → (主域注册键 → loop 字段, fp_ 键 → loop 字段)。

    口径与解析器注册一致(packParser._LoadArmAnimations): arm 命名空间的非 parallel 键
    同时是主域键(第三人称条件动画), fp_ 键 = arm 全部键加前缀; 主命名空间同名键优先。
    root = 资源包根(缺省产物资源包; 基线读 REF_RP)。
    """
    animDir = os.path.join(root or RP, "animations", packName)
    mainLoops, fpLoops = OrderedDict(), OrderedDict()
    if not os.path.isdir(animDir):
        return mainLoops, fpLoops
    mainPrefix = "animation.{}.".format(packName)
    armPrefix = "animation.{}_arm.".format(packName)
    armMain = OrderedDict()
    for name in sorted(os.listdir(animDir)):
        if not name.endswith(".json"):
            continue
        try:
            data = LoadJson(os.path.join(animDir, name))
        except ValueError:
            continue
        for animId, body in (data.get("animations") or {}).items():
            loop = body.get("loop") if isinstance(body, dict) else None
            if animId.startswith(mainPrefix):
                mainLoops[str(animId[len(mainPrefix):])] = loop
            elif animId.startswith(armPrefix):
                shortKey = str(animId[len(armPrefix):])
                if not _PARALLEL_SHORT_PATTERN.match(shortKey):
                    armMain[shortKey] = loop
                fpLoops["fp_" + shortKey] = loop
    for shortKey, loop in armMain.items():
        mainLoops.setdefault(shortKey, loop)
    return mainLoops, fpLoops


def CollectControllerAnimationRefs(packName):
    """RP 控制器产物里被 states[*].animations 引用的动画注册键集合。"""
    refs = set()
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    if not os.path.isdir(ctlDir):
        return refs
    for name in sorted(os.listdir(ctlDir)):
        if not name.endswith(".json"):
            continue
        try:
            data = LoadJson(os.path.join(ctlDir, name))
        except ValueError:
            continue
        for _ctlId, body in (data.get("animation_controllers") or {}).items():
            if not isinstance(body, dict):
                continue
            for _stateName, state in (body.get("states") or {}).items():
                if not isinstance(state, dict):
                    continue
                for entry in (state.get("animations") or []):
                    if isinstance(entry, dict):
                        for key in entry.keys():
                            refs.add(str(key))
                    elif isinstance(entry, (str, unicode)):  # noqa: F821
                        refs.add(str(entry))
    return refs


def HoldControllerOneShots(packName):
    """控制器状态引用的一次性动画补 `hold_on_last_frame`(幂等); 返回改写的注册键列表。

    **基岩的 loop:false 播完即撤。** 状态机把一次性动画挂在状态里、靠 `q.all_animations_finished`
    出态时会露出一个**空窗**: 动画播完的那一刻它立即不再作用于骨骼, 而转移要到下一帧才生效 ——
    骨骼掉回底层姿态一帧再被下个状态接手。实机(2026-09-05 warden 持剑攻击): punch_left /
    punch_right 在挥击收回的瞬间闪一下。Java 侧不会: PLAY_ONCE 播完的当帧判据成立、状态切走,
    末帧姿态由 3 tick 尾过渡接住。但 Java 的尾过渡**只有 3 tick**, 状态停留时姿态会收回 —— 补 hold
    之后的"无限保持"由 FadeControllerOneShots 的淡出权重复刻回 Java(作者控制器; 挥击通道的 cooldown、
    主链的 <键>_done 另有淡出)。⚠️ 未复刻: 一次性通道状态机的**使用**成员在触发期间一直保持 ——
    三个参考包的兜底 use_mainhand/use_offhand(0.375s)在 Java 是 PLAY_ONCE, 持续使用时 Java 会收回姿态。

    `hold_on_last_frame` 不影响出态: 它同样计入 `all_animations_finished`(仓库的挥击/
    受击/死亡通道一直这么用)。判据旁证: CSM 全库 578 条动画里 loop 为 true(473)或
    hold_on_last_frame(101), **裸的不循环动画只有 4 条**且都不挂在控制器状态上。

    只动"被控制器状态引用"的那些 —— 直挂 animate 条目的动画不受此约束(它们由播放条件
    决定去留, 补 hold 反而会让条件消失后姿态赖着不走)。
    一次性通道状态机按内存里的成员骨架收引用(那份文件在逐通道覆盖之后才生成, 磁盘上可能还没有)。
    """
    refs = CollectControllerAnimationRefs(packName)
    oneShot = BuildOneShotControllerFile(packName, withCompanions=False)
    for body in ((oneShot or {}).get("animation_controllers") or {}).values():
        for state in (body.get("states") or {}).values():
            for entry in state.get("animations") or []:
                refs.update(str(key) for key in (entry.keys() if isinstance(entry, dict) else [entry]))
    if not refs:
        return []
    animDir = os.path.join(RP, "animations", packName)
    if not os.path.isdir(animDir):
        return []
    mainPrefix = "animation.{}.".format(packName)
    armPrefix = "animation.{}_arm.".format(packName)
    changed = []
    for name in sorted(os.listdir(animDir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(animDir, name)
        try:
            data = LoadJson(path)
        except ValueError:
            continue
        dirty = False
        for animId, body in (data.get("animations") or {}).items():
            if not isinstance(body, dict):
                continue
            if _NormalizeLoopValue(body.get("loop")) not in (None, False):
                continue
            if animId.startswith(mainPrefix):
                keys = [str(animId[len(mainPrefix):])]
            elif animId.startswith(armPrefix):
                shortKey = str(animId[len(armPrefix):])
                keys = [shortKey, "fp_" + shortKey]
            else:
                continue                      # 替换实体(弹射物/载具)的动画不属玩家域
            if not [k for k in keys if k in refs]:
                continue
            body["loop"] = "hold_on_last_frame"
            changed.append(keys[0])
            dirty = True
        if dirty:
            DumpJson(path, data)
    return sorted(changed)


def BuildOneShotControllerFile(packName, ownership=None, withCompanions=True):
    """包的一次性通道控制器文件体(packParser.BuildOneShotControllers); 无成员 → None。

    基岩直挂 animate 条目不重放不循环的定时动画(实机: 第三人称挥手只播第一次), 挥击/
    受击/死亡改由生成式状态机驱动 —— 进入状态才会重置动画时钟。主包在资源索引发现
    controller.animation.<包>.ysm_swing 等 ID 即替换直挂条目, 无文件的旧产物维持直挂。
    ownership: ApplyChannelOwnership 的规划(成员的通道覆盖伴生随成员进同一状态); 缺省按磁盘现状
    只读规划。withCompanions=False 只要成员骨架(HoldControllerOneShots 收集引用用, 不必规划)。
    """
    mainLoops, fpLoops = CollectPackAnimationLoops(packName)
    # Java 缺键回落默认模型: 基线的拉弓/举盾/三叉戟等条件动画同样是挥击/使用通道的成员 —— 状态机只按包自有键
    # 生成时, 运行层把整族直挂条目换成状态机(packParser._ApplyOneShotControllers), 基线成员就再也播不出来
    for key, loop in BaselineAnimationLoops(packName).items():
        mainLoops.setdefault(key, loop)
    loopFlags = dict(mainLoops)
    loopFlags.update(fpLoops)
    companions = None
    if withCompanions:
        if ownership is None:
            ownership = PlanChannelOwnership(packName)
        companions = ownership.OneShotCompanions()
    controllers = BuildOneShotControllers(
        packName, [key for key in mainLoops if not _OWNERSHIP_COMPANION_PATTERN.match(key)],
        [key for key in fpLoops if not _OWNERSHIP_COMPANION_PATTERN.match(key)], loopFlags,
        companions)
    if not controllers:
        return None
    return OrderedDict([
        ("format_version", "1.19.0"),
        ("animation_controllers", controllers),
    ])


def _NullCoalescedAssignment(line):
    """`variable.x = 表达式;` → `variable.x = variable.x ?? (表达式);`; 非赋值行返回 None"""
    text = line.strip()
    if text.endswith(";"):
        text = text[:-1]
    head, sep, tail = text.partition("=")
    if not sep or tail.startswith("="):  # 无赋值 / 是比较(==)
        return None
    name, value = head.strip(), tail.strip()
    if not name or not value:
        return None
    return "{} = {} ?? ({});".format(name, name, value)


def BuildVariableInitController(packName, initLines):
    """每实例变量初始化控制器 controller.animation.<包>.ysm_variable_init(文件 VARIABLE_INIT_FILE)。

    玩家的纸娃娃(原版背包/模型设置界面的网易触屏控件与 PC live_player_renderer)是
    独立渲染实例, 有自己的 molang 作用域 —— 主包对世界实体做的 SetPlayerVariable
    到不了它, 包变量未定义按 0 求值: warden Root 缩放归零整模消失 / wither 没眼睛 /
    sahmet 换装件全亮并逐通道刷 unknown variable(2026-09 实机)。实体定义的
    scripts.initialize 虽能覆盖每个实例, 但 entity/player.entity.json 是全局唯一
    覆盖点(与其他覆盖玩家实体的组件互斥), 故改用**接口注册的动画控制器**:
    主包在资源索引发现此 ID 即以保留键 ysm_variable_init、条件恒 "1" 注册
    (packParser._VARIABLE_INIT_KEY), 每个渲染实例创建时各跑一次 on_entry。
    赋值写成 `v.x = v.x ?? 默认值;`(空值合并, 基岩原生): 世界实体上已由
    SetPlayerVariable/轮盘写入的值原样保留, 纸娃娃实例取到默认值 —— 幂等, 渲染
    重建再跑也不会把玩家的选择顶回默认。initLines 由 BuildPackVariableDefaults 提供
    (与预览实体 scripts.initialize 同源同口径; 预览实体不挂此控制器 —— 实机在其上
    不生效, 它用自身定义的 initialize); 无可用赋值时返回 None(不产文件)。单状态、
    不写 animations 键(空数组会让整份文件拒载, 见 _PruneControllerBody)。
    """
    onEntry = []
    for line in initLines:
        wrapped = _NullCoalescedAssignment(line)
        if wrapped:
            onEntry.append(wrapped)
    if not onEntry:
        return None
    # 单状态无转移: on_entry 在状态机启动(每个渲染实例创建)时跑一次即停。
    # 不写第二个"空状态"作转移目标 —— 网易引擎对控制器状态的形状挑剔(显式空
    # animations 数组会整份文件拒载, 见 _PruneControllerBody), 无谓的空状态是
    # 同一类风险面, 而单状态已足够。
    states = OrderedDict([("init", OrderedDict([("on_entry", onEntry)]))])
    body = OrderedDict([("initial_state", "init"), ("states", states)])
    return OrderedDict([
        ("format_version", "1.19.0"),
        ("animation_controllers", OrderedDict([
            ("controller.animation.{}.{}".format(packName, VARIABLE_INIT_KEY), body),
        ])),
    ])


PROJECTILE_ROOT_BONE = "ysm_projectile_root"
PROJECTILE_FIX_BONE = "ysm_projectile_fix"


def WrapProjectileGeometry(geoPath):
    """投射物几何外包一层朝向根骨骼; 返回是否改动(幂等)。

    Java(GeoProjectilesRenderer): 绕 Y 转 (yRot-90)、绕 Z 转 xRot, 再按模型宽高缩放(缺省 0.7) —— 模型空间的
    "前方"是 +X。基岩原版箭矢靠 `body` 骨骼上的 animation.arrow.move 转向(旧版内置箭矢几何的根骨骼就叫 body),
    Java 模型的根骨骼五花八门(Root / ysmGlowRoot / bow+crossbow 两个并列根 / bone2 / trident ...), 原版动画够不着,
    箭矢不随射击方向与下坠转向(2026-09-17 用户反馈)。照 CSM(.ref/csm 投射物几何 root → CSM_ROOT_90_FIX)外包:
    新根 ysm_projectile_root(运行层在它上面播朝向与缩放, packParser._PROJECTILE_ORIENT_ANIMATIONS) → 修正骨骼
    ysm_projectile_fix(Y -90°: Java 的 +X 前方转到基岩的前方) → 原来的全部根骨骼。
    """
    data = LoadJson(geoPath)
    changed = False
    for geo in data.get("minecraft:geometry") or []:
        bones = geo.get("bones") if isinstance(geo, dict) else None
        if not isinstance(bones, list):
            continue
        names = set(bone.get("name") for bone in bones if isinstance(bone, dict))
        if PROJECTILE_ROOT_BONE in names:
            continue
        for bone in bones:
            if isinstance(bone, dict) and (not bone.get("parent") or bone.get("parent") not in names):
                bone["parent"] = PROJECTILE_FIX_BONE
        geo["bones"] = [
            OrderedDict([("name", PROJECTILE_ROOT_BONE), ("pivot", [0, 0, 0])]),
            OrderedDict([("name", PROJECTILE_FIX_BONE), ("parent", PROJECTILE_ROOT_BONE),
                         ("pivot", [0, 0, 0]), ("rotation", [0, -90, 0])]),
        ] + bones
        changed = True
    if changed:
        DumpJson(geoPath, data)
    return changed


VEHICLE_ROOT_BONE = "ysm_vehicle_root"


def WrapVehicleGeometry(geoPath):
    """载具几何外包一层缩放根骨骼; 返回是否改动(幂等)。

    Java(CustomVehicleEntity/GeoEntityRenderer): YP(180 - 偏航) 后硬编码缩放 0.7, 不读 ysm.json 的缩放; 朝向约定与
    基岩实体相同, 不用补旋转。模型根骨骼五花八门(Ship / bone6 / Car ...), 运行层够不着 —— 外包新根 ysm_vehicle_root,
    主包在它上面播 animation.ysm.vehicle_root(packParser._WithVehicleRoot)。已带投射物朝向根的几何(同一模型两用)不再包。
    """
    data = LoadJson(geoPath)
    changed = False
    for geo in data.get("minecraft:geometry") or []:
        bones = geo.get("bones") if isinstance(geo, dict) else None
        if not isinstance(bones, list):
            continue
        names = set(bone.get("name") for bone in bones if isinstance(bone, dict))
        if VEHICLE_ROOT_BONE in names or PROJECTILE_ROOT_BONE in names:
            continue
        for bone in bones:
            if isinstance(bone, dict) and (not bone.get("parent") or bone.get("parent") not in names):
                bone["parent"] = VEHICLE_ROOT_BONE
        geo["bones"] = [OrderedDict([("name", VEHICLE_ROOT_BONE), ("pivot", [0, 0, 0])])] + bones
        changed = True
    if changed:
        DumpJson(geoPath, data)
    return changed


GLIDE_ROOT_BONE = "ysm_glide_root"


def WrapGlideRootGeometry(geoPath):
    """玩家主几何外包一层滑翔根骨骼; 返回是否改动(幂等)。

    基岩在 query.is_gliding 时对**整个模型**额外转 (90 + 玩家 pitch) 度(2026-09-18 实机: 锁死侧视相机扫
    9 个俯仰角逐张量主轴; 与 Java 原版 PlayerRenderer.setupRotations 的 XP.rotationDegrees(-90 - xRot) 同式),
    这层旋转在实体层(ActorRenderData::getDamageOrGlidingXYRotation), 骨骼矩阵里查不到。Java 版 YSM 的渲染器
    继承 LivingEntityRenderer 而非 PlayerRenderer, 实体层不转 —— 鞘翅姿态全写在模型的 elytra_fly 动画里
    (12_little 把 Root 转 90 让身体躺平), 两者叠加就是用户看到的"水平飞行却头朝下"。
    模型自己的根骨骼名五花八门(Root / MRoot / GuiRoot / Player_Size / RootAll ..., 且常有多根并列), 主包够不着,
    故照投射物/载具的先例外包一个固定名的新根, 主包在它上面播 animation.ysm.java_glide_fix 反向旋转
    (packParser._JAVA_GLIDE_FIX_KEY)。骨骼本身不带任何变换, 不滑翔时是恒等。
    """
    data = LoadJson(geoPath)
    changed = False
    for geo in data.get("minecraft:geometry") or []:
        bones = geo.get("bones") if isinstance(geo, dict) else None
        if not isinstance(bones, list):
            continue
        names = set(bone.get("name") for bone in bones if isinstance(bone, dict))
        if GLIDE_ROOT_BONE in names:
            continue
        for bone in bones:
            if isinstance(bone, dict) and (not bone.get("parent") or bone.get("parent") not in names):
                bone["parent"] = GLIDE_ROOT_BONE
        geo["bones"] = [OrderedDict([("name", GLIDE_ROOT_BONE), ("pivot", [0, 0, 0])])] + bones
        changed = True
    if changed:
        DumpJson(geoPath, data)
    return changed


def ExplicitPackPrecedence(packName):
    """包的 RP 动画/控制器文件全部表达式显式化运算符优先级(见 molang_syntax.ExplicitPrecedence 注); 返回改动处数。

    移植期各步骤(逐通道覆盖/淡出/播完判据改写 ...)都在 RewriteAnimations/RewriteControllers 之后改文件,
    收尾再整体过一遍; 幂等(括号本身就是分组, 第二遍零改动)。
    """
    total = 0
    for sub, func in (("animations", ExplicitAnimationPrecedence),
                      ("animation_controllers", ExplicitControllerPrecedence)):
        folder = os.path.join(RP, sub, packName)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder, name)
            try:
                data = LoadJson(path)
            except ValueError:
                continue
            bodies = data.get(sub)
            if not isinstance(bodies, dict):
                continue
            count = 0
            for body in bodies.values():
                if isinstance(body, dict):
                    count += len(func(body))
            if count:
                DumpJson(path, data)
                total += count
    return total


def PortBaselinePack(javaDir, packName=JAVA_BASELINE_PACK, withMods=False):
    """Java 内置 default 模型 → 缺键回落基线(只产出资源包动画, 不注册成可选模型)。

    Java 模型缺动画键时回落 default 的同名动画(DefaultAnimationRuntime / AnimationStore fallback, 域
    player/main 与 player/first-person), 主包解析器按 animation.java_default(_arm).* 命名空间把它们并进
    Java 模式包的动画表(packParser._JavaDefaultBaseline)。只要资源包里的动画: 不写 BP ysm.json、预览实体、
    几何贴图, 也不跑逐通道覆盖/主链/一次性状态机 —— 基线动画体被多个包共用, 不能按某一个包拆伴生。
    一次性成员(挥击/使用)照常补 hold_on_last_frame(HoldControllerOneShots 按基线自身键判定, 与各包口径一致)。
    """
    manifest = LoadJson(os.path.join(javaDir, "ysm.json"))
    player = ((manifest.get("files") or {}).get("player") or {})
    rpAnims = os.path.join(RP, "animations", packName)
    if os.path.isdir(rpAnims):
        for name in os.listdir(rpAnims):
            if name.endswith(".json"):
                os.remove(os.path.join(rpAnims, name))
    report = []
    nameMapper = AsciiNameMapper(EscapeConditionKey)
    physics = PhysicsRewriter()
    molangDefaults, molangReport, packVars = {}, Counter(), set()
    for key in sorted((player.get("animation") or {}).keys()):
        if not withMods and key in MOD_ANIMATION_KEYS:
            continue
        relPath = player["animation"][key]
        src = os.path.join(javaDir, _FsPath(relPath, javaDir).replace("/", os.sep))
        if not os.path.isfile(src):
            report.append(u"[WARN] 动画缺失: {}".format(relPath))
            continue
        namespace = "{}_arm".format(packName) if key in ARM_ANIMATION_KEYS else packName
        count, _dropped, _fixes, _ids = RewriteAnimations(
            src, os.path.join(rpAnims, "{}.animation.json".format(key)), namespace,
            molangDefaults, molangReport, nameMapper, packVars, set(),
            physics=physics, forceStateLoops=True, preKeys=set(), previewGate=True)
        report.append("基线动画 {} → animation.{}.* ({} 条)".format(key, namespace, count))
    held = HoldControllerOneShots(packName)
    if held:
        report.append(u"一次性通道成员补 hold_on_last_frame {} 条".format(len(held)))
    precedence = ExplicitPackPrecedence(packName)
    if precedence:
        report.append(u"运算符优先级显式化 {} 处".format(precedence))
    if molangDefaults:
        report.append(u"[!] 基线动画用到 ?? 默认值 {} 个(各包初始化表不含, 读作 0): {}".format(
            len(molangDefaults), u", ".join(sorted(molangDefaults))))
    def _AsText(value):
        return value.decode("utf-8") if isinstance(value, bytes) else value

    for label in sorted(molangReport, key=_AsText):
        textLabel = _AsText(label)
        if textLabel.startswith((u"zero:", u"warn:")):
            report.append(u"  [!] {} x{}".format(textLabel, molangReport[label]))
    return report


GUI_IMAGE_KEYS = ("gui_background", "gui_foreground")


def PortGuiImages(manifest, srcOf, rpTextures, packName, report):
    """properties.gui_background / gui_foreground(GUI 卡片背景/前景图)→ RP 贴图; 返回拷贝成功的键列表。

    Java 模型选择卡片(CatalogModelButton.renderWidget): 底色 → 背景图铺满卡片 → 纸娃娃 → 前景图铺满卡片
    → 名字 → 选中框; 主包卡片模板同序叠两层 image(ysmCommonControls.ysmSelectModelButton, 运行层
    client/ui/modelCard.py 按模型配置置图)。文件名统一成 gui_<角色>.<扩展名>(官方 21 号包用中文名
    前景.png/背景.png, 基岩贴图路径只收 ASCII), 声明改写成 textures/gui/<新名>, 解析器按文件名映射到
    textures/entity/<包>/<新名>(与 icon 同一规则)。Java 端图片走 GUI_IMAGE 有损压缩(上限 260×450), 这里原图拷贝。
    缺文件: Java 加载失败不显示, 删声明并告警。srcOf: 包内相对路径 → 源文件路径。
    """
    properties = manifest.get("properties") if isinstance(manifest, dict) else None
    copied = []
    if not isinstance(properties, dict):
        return copied
    for guiKey in GUI_IMAGE_KEYS:
        guiRel = properties.get(guiKey)
        if not isinstance(guiRel, (str, unicode)) or not guiRel:  # noqa: F821
            continue
        guiSrc = srcOf(guiRel)
        if not os.path.isfile(guiSrc):
            report.append(u"[WARN] {} 图片缺失: {}(Java 同样加载失败不显示), 已删除声明".format(guiKey, guiRel))
            properties.pop(guiKey, None)
            continue
        guiExt = os.path.splitext(BaseName(guiRel))[1].lower() or ".png"
        guiName = "{}{}".format(guiKey, guiExt)
        CopyBinary(guiSrc, os.path.join(rpTextures, guiName))
        properties[guiKey] = "textures/gui/" + guiName
        report.append(u"{} {} → textures/entity/{}/{}".format(guiKey, guiRel, packName, guiName))
        copied.append(guiKey)
    return copied


def PortPack(javaDir, packName, collection=None, withMods=False, molangSink=None):
    """移植一个 Java 包; 返回汇总行列表。molangSink 传入 Counter 时把 molang 替换/降级计数原样并入
    (宿主据此做结构化告警, 汇总文本里的 "[!] label xN" 行是同一份数据的可读形态)。"""
    manifestPath = os.path.join(javaDir, "ysm.json")
    if not os.path.isfile(manifestPath):
        raise SystemExit(u"[ERROR] 不是 Java 模型包目录(缺 ysm.json): {}".format(_DisplayPath(javaDir)))
    manifest = LoadJson(manifestPath)
    player = ((manifest.get("files") or {}).get("player") or {})

    bpDir = os.path.join(BP_MODELS, collection, packName) if collection \
        else os.path.join(BP_MODELS, packName)
    rpModels = os.path.join(RP, "models", "entity", packName)
    rpAnims = os.path.join(RP, "animations", packName)
    rpTextures = os.path.join(RP, "textures", "entity", packName)
    rpControllers = os.path.join(RP, "animation_controllers", packName)
    report = []

    def _Src(relPath):
        # 中文文件名(15_kluonoa 的 controller/主动画修改.json)不能 str(): 按文件系统编码转字节
        return os.path.join(javaDir, _FsPath(relPath, javaDir).replace("/", os.sep))

    # ---- 几何: 主模型 / 手臂 / 替换实体 ----
    modelDecl = player.get("model") or {}
    # fp_arm 动画的骨骼改名表(arm 几何重建时产出; 没有 arm 几何时为空)
    fpArmBoneRenames = {}
    for key in modelDecl:
        src = _Src(modelDecl[key])
        if not os.path.isfile(src):
            report.append("[WARN] 几何缺失: {}".format(modelDecl[key]))
            continue
        identifier = "geometry.{}".format(packName) if key == "main" \
            else "geometry.{}_{}".format(packName, BaseName(modelDecl[key]))
        rpOut = os.path.join(rpModels, "{}.geo.json".format(key))
        report.append("几何 {} → {}".format(key, identifier))
        RewriteGeometry(src, rpOut, identifier, report)
        if key == "arm":
            # 第一人称手臂: 重建成"原版手臂骨架包装 + 移位后的 Java 子树"
            # (见 BuildFirstPersonArmGeometry 长注); 改名表要带到 fp_arm 动画改写
            geometryRenames, armNotes = BuildFirstPersonArmGeometry(rpOut)
            fpArmBoneRenames = FirstPersonArmBoneRenames(geometryRenames)
            report.extend(armNotes)

    # ---- 动画: 主命名空间 / 手臂命名空间 ----
    # 模组联动动画默认整键跳过: 既不生成资源, 也从声明里摘掉 —— 留着声明会让
    # 主包按命名空间查索引落空, 白刷一轮坏引用告警。
    animationDecl = player.get("animation") or {}
    skippedMods = []
    molangDefaults = {}
    molangReport = Counter()
    # 动画短名与控制器引用共用一张改名表 —— 分开建会让控制器引用改名前的键
    nameMapper = AsciiNameMapper(EscapeConditionKey)
    # 物理函数键 → 状态变量槽位, 同样全包共用(main/arm/控制器引用同一个键共享一份状态)
    physics = PhysicsRewriter()
    # 本包产出的动画短键全集(主/arm 命名空间的注册短键 = ID 去前缀), 供控制器
    # 死引用剪枝判据 —— 控制器引用的短键在此集合与基线兜底名单都查无, 即死引用
    knownAnimKeys = set()
    # 玩家侧产物(动画+控制器)引用的 molang 变量短名全集 → 预览实体 scripts.initialize
    packVars = set()
    # 预扫控制器源: parallel 通道控制器(player.parallel_N)状态引用的动画在 Java 是
    # 旋转相加, 动画改写时不能加 override 标志(见 _ShouldOverridePrevious)
    additiveKeys = set()
    # 粒子汇: 玩家侧动画 timeline 里的 ysm.particle 调用 → 关键帧 + 根骨骼 locator + 资源登记
    particleSink = {"effects": OrderedDict(), "locators": OrderedDict()}
    # 音频汇: 玩家侧动画 sound_effects 关键帧 → 效果键 + ogg 拷贝 + sound_definitions + 注册
    soundSink = SoundSink(packName)
    # 同理预扫 pre 通道控制器(player.pre_parallel_N/pre_main/vehicle)状态引用的动画:
    # 排在主链之前、同处淡化链路, 也不带 override
    preKeys = set()
    for relPath in (player.get("animation_controllers") or []):
        if isinstance(relPath, (str, unicode)) and os.path.isfile(_Src(relPath)):  # noqa: F821
            controllerData = LoadJson(_Src(relPath))
            additiveKeys |= CollectParallelChannelAnimKeys(controllerData, nameMapper)
            preKeys |= CollectPreChannelAnimKeys(controllerData, nameMapper)
    # Java 脚本控制器(functions/*@player_ctrl_<通道>.molang) → 基岩式控制器(见 script_controller.py 注);
    # 与声明的控制器同样参与 pre/parallel 通道的 override 预扫
    declaredControllerNames = set()
    for relPath in (player.get("animation_controllers") or []):
        if isinstance(relPath, (str, unicode)) and os.path.isfile(_Src(relPath)):  # noqa: F821
            declaredControllerNames |= set((LoadJson(_Src(relPath)).get("animation_controllers") or {}).keys())
    scriptControllers, skippedScripts = ConvertPackScripts(javaDir, declaredControllerNames)
    for _channel, scriptData, _notes in scriptControllers:
        additiveKeys |= CollectParallelChannelAnimKeys(scriptData, nameMapper)
        preKeys |= CollectPreChannelAnimKeys(scriptData, nameMapper)
    for key in list(animationDecl.keys()):
        if not withMods and key in MOD_ANIMATION_KEYS:
            del animationDecl[key]
            skippedMods.append(key)
            continue
        relPath = animationDecl[key]
        src = _Src(relPath)
        if not os.path.isfile(src):
            report.append(u"[WARN] 动画缺失: {}".format(relPath))
            continue
        namespace = "{}_arm".format(packName) if key in ARM_ANIMATION_KEYS else packName
        count, dropped, (vectorFixes, stmtFixes, loopFixes, physicsFixes, sanitizeFixes,
                         lerpFixes), \
            animIds = RewriteAnimations(
                src, os.path.join(rpAnims, "{}.animation.json".format(key)), namespace,
                molangDefaults, molangReport, nameMapper, packVars, additiveKeys,
                physics=physics, forceStateLoops=True, preKeys=preKeys, particles=particleSink,
                sounds=soundSink, previewGate=True,
                # 第一人称手臂动画独占改名表: arm 文件(第三人称手部条件动画)不能改 ——
                # 它按短键并进主域, 作用在**主几何**的 RightArm 上
                boneRenames=fpArmBoneRenames if key == "fp_arm" else None)
        nsPrefix = u"animation.{}.".format(namespace)
        for animId in animIds:
            if animId.startswith(nsPrefix):
                knownAnimKeys.add(str(animId[len(nsPrefix):]))
        note = " (跳过 {} 个分组标题条目)".format(len(dropped)) if dropped else ""
        if vectorFixes:
            note += " (标量通道展开为向量 {} 处)".format(vectorFixes)
        if stmtFixes:
            note += " (语句规范化: 补分号/补 return/去尾分号 {} 处)".format(stmtFixes)
        if loopFixes:
            note += " (loop 按 Java 主链/一次性通道语义改写 {} 条)".format(loopFixes)
        if physicsFixes:
            note += " (物理函数改写为 molang 状态积分 {} 处)".format(physicsFixes)
        if sanitizeFixes:
            note += " (清理引擎拒载的空节点 {} 处)".format(sanitizeFixes)
        if lerpFixes:
            note += " (表达式关键帧 catmullrom→linear {} 处)".format(lerpFixes)
        report.append("动画 {} → animation.{}.* ({} 条){}".format(key, namespace, count, note))
    baselineVars = CollectBaselineVariables(packName)
    if baselineVars:
        packVars |= baselineVars
        report.append(u"默认模型基线动画的变量 {} 个并进本包初始化表(拉弓/挥剑等回落动画在本包上播放时要读)".format(
            len(baselineVars)))
    for geoFile, action, heldItemBones in EnsureHeldItemBones(rpModels):
        report.append(u"手持物品定位骨骼[{}]: {} {} (基岩认这两个固定骨骼名, Java 用 "
                      u"<Left|Right>HandLocator)".format(
                          geoFile, u"补" if action == "added" else u"挪到新挂点",
                          u", ".join(u"{}→{}".format(bone, parent)
                                     for bone, parent in heldItemBones)))
    # 滑翔根骨骼排在粒子 locator 之后包: locator 要打在**模型自己的**根骨骼上(跟着身体动),
    # 打到恒等的外包根上就不跟随了(见 AddGeometryLocators 的根骨骼挑选)
    if WrapGlideRootGeometry(os.path.join(rpModels, "main.geo.json")):
        report.append(u"主几何外包滑翔根骨骼 {}: 主包在它上面播 animation.ysm.java_glide_fix, "
                      u"抵消基岩滑翔时实体层的 (90+pitch) 原生旋转(Java 版渲染器不转, 姿态全在 "
                      u"elytra_fly 动画里)".format(GLIDE_ROOT_BONE))
    # 资源登记表的标准位置是 files.player(基岩扩展字段与 Java 原生声明同居, 见 pack-guide);
    # 早先写在 netease 段的旧产物一并迁到 files.player
    playerDeclNode = manifest.setdefault("files", OrderedDict()).setdefault("player", OrderedDict())
    legacyNetease = manifest.get("netease") if isinstance(manifest.get("netease"), dict) else {}
    if particleSink["effects"]:
        mainGeo = os.path.join(rpModels, "main.geo.json")
        addedLocators = AddGeometryLocators(mainGeo, particleSink["locators"])
        registered = list(playerDeclNode.get("particle_effect")
                          or legacyNetease.pop("particle_effect", None) or [])
        known = set(entry[0] for entry in registered if isinstance(entry, list) and entry)
        for effectKey, bedrockId in particleSink["effects"].items():
            if effectKey not in known:
                registered.append([effectKey, bedrockId])
        playerDeclNode["particle_effect"] = registered
        report.append(u"粒子: ysm.particle 关键帧 → 登记 {} 种粒子({}), 根骨骼 locator {} 个(新增 {})".format(
            len(particleSink["effects"]),
            u", ".join(u"{}={}".format(k, v) for k, v in particleSink["effects"].items()),
            len(particleSink["locators"]), addedLocators))
    # 音频资源排在替换实体(载具)动画之后落盘: 载具动画的 sound_effects 关键帧(01 狐狸车 car_run/car_idle)同进音频汇
    if skippedMods:
        report.append("跳过模组联动动画 {} 组: {} (需要时加 --with-mods)".format(
            len(skippedMods), ", ".join(skippedMods)))
        for key in skippedMods:
            stale = os.path.join(rpAnims, "{}.animation.json".format(key))
            if os.path.isfile(stale):
                os.remove(stale)
                report.append("  清理上次移植遗留: animations/{}/{}.animation.json".format(
                    packName, key))

    # ---- 动画控制器(Java 2.3.0+ 的基岩式状态机, 声明位置 files.player.animation_controllers) ----
    # Java 的控制器名即通道名(player.parallel_0 / player.main ...), 网易侧没有通道,
    # 统一注册成常开控制器; parallel 系另摘掉同名动画的直播条目还原"接管"语义。
    controllerDecl = player.get("animation_controllers")
    channelTakeovers = []
    if isinstance(controllerDecl, list):
        for declIndex, relPath in enumerate(controllerDecl):
            if not isinstance(relPath, (str, unicode)):  # noqa: F821
                continue
            src = _Src(relPath)
            if not os.path.isfile(src):
                report.append(u"[WARN] 控制器缺失: {}".format(relPath))
                continue
            srcName = relPath.replace("\\", "/").rsplit("/", 1)[-1]
            outName = AsciiFileName(srcName)
            if outName != srcName:
                # 资源包文件名保持 ASCII; 声明路径同步改名(主包按文件名定位控制器文件)
                controllerDecl[declIndex] = relPath[:len(relPath) - len(srcName)] + outName
            dst = os.path.join(rpControllers, outName)
            count, takeovers, refFixes, prunedRefs, prunedTransitions = \
                RewriteControllers(src, dst, packName, molangDefaults, molangReport,
                                   nameMapper, knownAnimKeys, packVars, physics)
            channelTakeovers += takeovers
            note = " (动画引用转义 {} 处)".format(refFixes) if refFixes else ""
            shownName = srcName if outName == srcName else u"{}(文件名转为 {})".format(srcName, outName)
            if isinstance(shownName, unicode):  # noqa: F821 — py2: note 是含中文的字节串, 不能与 unicode 混拼
                shownName = shownName.encode("utf-8")
            report.append("控制器 {} → controller.animation.{}.* ({} 个){}".format(
                shownName, packName, count, note))
            if prunedRefs:
                uniquePruned = sorted(set(prunedRefs))
                report.append(u"  剪掉 {} 处死引用(动画哪都不存在, Java 静默容忍/"
                              u"基岩会刷 can't find): {}{}".format(
                                  len(prunedRefs), u", ".join(uniquePruned[:8]),
                                  u" ..." if len(uniquePruned) > 8 else u""))
            if prunedTransitions:
                report.append(u"  剪掉 {} 处非法状态转移(目标未定义/条件为空): {}".format(
                    len(prunedTransitions),
                    u", ".join(sorted(set(prunedTransitions))[:6])))
    # 脚本控制器落盘, 声明登记进 files.player.animation_controllers(主包按声明文件名定位控制器文件)
    scriptDecls = []
    for channel, scriptData, notes in scriptControllers:
        outName = "ysm_script_{}.json".format(channel)
        tempDir = tempfile.mkdtemp()
        try:
            tempPath = os.path.join(tempDir, "script.json")
            DumpJson(tempPath, scriptData)
            _count, takeovers, _refFixes, prunedRefs, _prunedTransitions = RewriteControllers(
                tempPath, os.path.join(rpControllers, outName), packName, molangDefaults, molangReport,
                nameMapper, knownAnimKeys, packVars, physics)
        finally:
            shutil.rmtree(tempDir, ignore_errors=True)
        channelTakeovers += takeovers
        scriptDecls.append("controller/{}".format(outName))
        stateCount = len(list(scriptData["animation_controllers"].values())[0]["states"])
        report.append(u"脚本控制器 @player_ctrl_{} → controller.animation.{}.player_{} ({} 个状态{}): Java 每帧脚本的"
                      u"决策树展开成有序规则, 首个命中者播放{}".format(
                          channel, packName, channel, stateCount,
                          u", 剪掉死引用 {} 处".format(len(prunedRefs)) if prunedRefs else u"",
                          (u"; " + u"; ".join(notes)) if notes else u""))
    for fileName, reason in skippedScripts:
        report.append(u"[!] 脚本控制器 {} 未转换: {}".format(fileName, reason))
    if os.path.isdir(rpControllers):
        keepNames = set(decl.rsplit("/", 1)[-1] for decl in scriptDecls)
        for name in os.listdir(rpControllers):
            if name.startswith("ysm_script_") and name.endswith(".json") and name not in keepNames:
                os.remove(os.path.join(rpControllers, name))
    if scriptDecls:
        declList = player.get("animation_controllers")
        if not isinstance(declList, list):
            declList = []
            player["animation_controllers"] = declList
        for decl in scriptDecls:
            if decl not in declList:
                declList.append(decl)
    if channelTakeovers:
        # Java: 声明了 player.parallel_N 控制器(或自有控制器引用了 parallelN) = 该通道
        # 不再自动播同名动画。**不写进 ysm.json** —— 主包解析器按控制器名与状态引用自动
        # 推导(packParser._DrivenParallelKeys), 创作者无需维护删除清单
        report.append("  通道接管(主包自动识别, 无需声明): {} 由控制器驱动".format(
            ", ".join(sorted(set(channelTakeovers)))))
    droppedPreRefs = DropPreControllerMainChainRefs(packName)
    if droppedPreRefs:
        report.append(u"  pre 通道控制器摘掉对主链成员的冗余引用 {} 处(主链由 ysm_state 状态机播, "
                      u"两边同播会叠成两倍): {}".format(
                          len(droppedPreRefs),
                          u", ".join(u"{}.{}:{}".format(*item) for item in droppedPreRefs[:6])))

    # ---- 替换实体(弹射物/载具, 含 Java 废弃字段 files.arrow)的几何与动画 ----
    # 命名与主包解析器共用 packParser.ReplacedTargets: 几何 geometry.<包名>_<模型段>,
    # 动画命名空间 <包名>_<动画命名空间段>(同模型配不同动画文件时带动画段区分, 01 酒狐的马/骡子)。
    # 动画产物落 <命名空间段>.animation.json, 声明路径同步改名 —— 主包按文件名定位替换实体动画,
    # 名字对不上就一条都注册不到(早先 horse.animation.json 落成 foxcar.animation.json 即如此)。
    for section, _entityIds, entry, modelSegment, namespaceSegment in ReplacedTargets(
            manifest.get("files")):
        modelRel = entry.get("model")
        if not isinstance(modelRel, (str, unicode)) or not modelRel \
                or modelRel.startswith("geometry.") or not os.path.isfile(_Src(modelRel)):  # noqa: F821
            continue
        identifier = "geometry.{}_{}".format(packName, modelSegment)
        RewriteGeometry(_Src(modelRel),
                        os.path.join(rpModels, "{}.geo.json".format(modelSegment)), identifier)
        if section == "projectiles" and WrapProjectileGeometry(
                os.path.join(rpModels, "{}.geo.json".format(modelSegment))):
            report.append(u"  投射物几何外包朝向根骨骼 {} → {}(Y -90°): 运行层在根上播 Java 的朝向与 0.7 缩放".format(
                PROJECTILE_ROOT_BONE, PROJECTILE_FIX_BONE))
        if section == "vehicles" and WrapVehicleGeometry(
                os.path.join(rpModels, "{}.geo.json".format(modelSegment))):
            report.append(u"  载具几何外包缩放根骨骼 {}: 运行层在根上播 Java 硬编码的 0.7 缩放".format(
                VEHICLE_ROOT_BONE))
        animRel = entry.get("animation")
        if isinstance(animRel, (str, unicode)) and animRel and os.path.isfile(_Src(animRel)):  # noqa: F821
            # 多个实体共用同一动画文件时(如 boat/chest_boat)按各自命名空间各生成一份;
            # 音频关键帧与玩家侧共用音频汇(Java 载具动画的 sound_effects 照播)
            RewriteAnimations(
                _Src(animRel),
                os.path.join(rpAnims, "{}.animation.json".format(namespaceSegment)),
                "{}_{}".format(packName, namespaceSegment),
                molangDefaults, molangReport, nameMapper, physics=physics, sounds=soundSink)
            srcName = animRel.replace(chr(92), "/").rsplit("/", 1)[-1]
            outName = u"{}.animation.json".format(namespaceSegment)
            if srcName != outName:
                entry["animation"] = animRel[:len(animRel) - len(srcName)] + outName
        texRel = entry.get("texture")
        texPath = texRel.get("uv") if isinstance(texRel, dict) else texRel
        if texPath and os.path.isfile(_Src(texPath)):
            CopyBinary(_Src(texPath),
                       os.path.join(rpTextures, os.path.basename(str(texPath))))
        report.append("{} {} → {}".format(section, modelSegment, identifier))

    # ---- 音频三件套(玩家侧与替换实体动画的 sound_effects 关键帧都已进音频汇) ----
    if soundSink.entries:
        copied, missing = WriteSoundResources(packName, javaDir, _SoundSourceDir(manifest), soundSink)
        playerDeclNode["sound_effect"] = soundSink.registrations
        legacyNetease.pop("sound_effect", None)
        report.append(u"音频: sound_effects 关键帧 {} 种 → ogg {} 个拷到 sounds/ysm/{}/ + "
                      u"sound_definitions 登记, files.player.sound_effect 注册 {} 条{}".format(
                          len(soundSink.entries), copied, packName, len(soundSink.registrations),
                          (u"; [!] 源音频缺失: " + u", ".join(missing)) if missing else u""))
        molangReport.update(soundSink.report)
    else:
        WriteSoundResources(packName, javaDir, "sounds", soundSink)    # 清掉上次移植遗留的音频
        stale = playerDeclNode.get("sound_effect")
        if isinstance(stale, list) and stale and all(
                isinstance(entry, list) and entry
                and str(entry[0]).startswith(_SOUND_EFFECT_KEY_PREFIX) for entry in stale):
            del playerDeclNode["sound_effect"]

    # ---- 贴图(玩家皮肤) ----
    for texEntry in player.get("texture") or []:
        texPath = texEntry.get("uv") if isinstance(texEntry, dict) else texEntry
        src = _Src(texPath)
        if not os.path.isfile(src):
            report.append(u"[WARN] 贴图缺失: {}".format(texPath))
            continue
        CopyBinary(src, os.path.join(rpTextures, os.path.basename(str(texPath))))
        report.append("贴图 {} → textures/entity/{}/{}".format(
            texPath, packName, BaseName(texPath)))

    # ---- GUI 卡片前景/背景图(properties.gui_background / gui_foreground) ----
    PortGuiImages(manifest, _Src, rpTextures, packName, report)

    # ---- ysm.json: 原样搬运(推导全部由主包 parser 承担) ----
    # 例外: ?? 的默认值必须落到初始化表 —— 基岩未初始化变量读作 0, 而 Java 的
    # v.x??1 语义是"未定义时取 1", 不补初值会让默认状态反转(如头饰默认消失)。
    # Java 包的状态动画(idle/walk/run/...)必须由 Java 语义驱动: 主包旧版状态机引用
    # idle_0/idle_timer 等旧键名, Java 包不提供 → 不开这个开关状态动画一条都不播。
    # (Java 模式无需声明: 主包按 files.player.animation 是 dict(Java 语义槽位)自动判定)

    if molangDefaults:
        # 网易扩展字段的标准位置是**顶层**(与 spec/metadata/properties/files 同级);
        # netease 段只是兼容形态, 移植产物不再生成
        initialize = list(manifest.get("initialize") or [])
        declared = set()
        for entry in initialize:
            if isinstance(entry, (str, unicode)):  # noqa: F821
                declared.add(entry.split("=")[0].strip())
        for name in sorted(molangDefaults):
            full = "variable." + name.split(".", 1)[1]
            if name in declared or full in declared:
                continue
            initialize.append("{} = {};".format(full, molangDefaults[name]))
        manifest["initialize"] = initialize
        report.append("molang ?? 默认值 {} 个 → initialize(顶层): {}".format(
            len(molangDefaults),
            ", ".join("{}={}".format(k, molangDefaults[k]) for k in sorted(molangDefaults))))
    # ---- 多语言显示文本(排在下面的轮盘键改名之前: 语言表按 Java 原名索引) ----
    langCount = ApplyJavaLanguage(manifest, LoadJavaLanguage(manifest, javaDir))
    if langCount:
        report.append(u"多语言: lang/{}.json 的显示文本烘进 ysm.json {} 处(模型名/作者/轮盘/"
                      u"配置表单; 网易版只有中文界面)".format(JAVA_LANG_LOCALE, langCount))
    # ---- ysm.json 内的动画名引用与改名表对齐 ----
    # 轮盘键/预览动画写的是动画短名; 动画侧改了名(转拼音、统一小写)这里不同步,
    # 轮盘就会播一个不存在的键(实机无报错, 只是不动)。"#" 开头的是分类/按钮引用, 不是动画名。
    renamedRefs = []

    def _SyncAnimRefs(extraDict):
        if not isinstance(extraDict, dict):
            return extraDict
        synced = OrderedDict()
        for key in extraDict:
            # 非 "#" 键会被解析器注册成动画键并进 /playanimation 指令, 必须 ASCII 化;
            # 用 Convert 而非 Lookup —— 纯占位的按钮宿主键(如 "模型配置": "#模型配置")
            # 在动画文件里查不到, Lookup 会原样放行, 留下非法标识符
            newKey = key if key.startswith("#") else nameMapper.Convert(key)
            if newKey != key:
                renamedRefs.append((key, newKey))
            synced[newKey] = extraDict[key]
        return synced

    properties = manifest.get("properties")
    if isinstance(properties, dict):
        if "extra_animation" in properties:
            properties["extra_animation"] = _SyncAnimRefs(properties["extra_animation"])
        for classify in (properties.get("extra_animation_classify") or []):
            if isinstance(classify, dict) and "extra_animation" in classify:
                classify["extra_animation"] = _SyncAnimRefs(classify["extra_animation"])
        preview = properties.get("preview_animation")
        if isinstance(preview, (str, unicode)):  # noqa: F821
            newPreview = nameMapper.Convert(preview)
            if newPreview != preview:
                properties["preview_animation"] = newPreview
                renamedRefs.append((preview, newPreview))
    if renamedRefs:
        report.append(u"ysm.json 动画名引用同步 {} 处: {}".format(
            len(renamedRefs),
            ", ".join(u"{}→{}".format(a, b) for a, b in renamedRefs[:4])
            + (u" ..." if len(renamedRefs) > 4 else u"")))
    if nameMapper.converted:
        cjk = [(a, b) for a, b in nameMapper.converted if any(ord(c) > 127 for c in a)]
        if cjk:
            report.append(u"非 ASCII 标识符转拼音 {} 个{}: {}".format(
                len(cjk),
                u"" if _LazyPinyin is not None else u"(未装 pypinyin, 退化为码点形式)",
                ", ".join(u"{}→{}".format(a, b) for a, b in cjk[:4])
                + (u" ..." if len(cjk) > 4 else u"")))

    # ---- properties 子树的 v.roaming.* 同步扁平化 ----
    # 动画/控制器文件里的 v.roaming.<名> 已由 PortMolangText 扁平化为 v.roaming_<名>
    # (基岩无自定义 struct, 嵌套名求值恒失败); ysm.json 的 config_forms(value/labels
    # 表达式)引用同一批变量, 不同步改写的话表单写的是死变量, 动画读的是另一个 ——
    # 实机表现: 模型配置表单全体无效(凋灵娘"隐藏悬浮头颅"等)。
    flattenCount = _FlattenRoamingStrings(manifest.get("properties"))
    if flattenCount:
        report.append(u"ysm.json 表单变量 v.roaming.* 扁平化 {} 处(与动画侧同步)".format(
            flattenCount))

    if not manifest.get("netease"):
        manifest.pop("netease", None)     # 空的兼容段不落盘
    DumpJson(os.path.join(bpDir, "ysm.json"), manifest)
    report.append(u"声明 → {}".format(_DisplayPath(os.path.join(bpDir, "ysm.json"))))

    # ---- 物理函数键 → 状态变量留痕(创作者调试 v.ysm_so_<键>_y 时对照) ----
    molangReport.update(physics.report)
    if molangSink is not None:
        molangSink.update(molangReport)
    if physics.mapping:
        report.append(u"物理函数键 → 状态变量 {} 个: {}".format(
            len(physics.mapping),
            u", ".join(u"{}→v.ysm_so_{}_y".format(key, slug) for key, slug in physics.mapping[:6])
            + (u" ..." if len(physics.mapping) > 6 else u"")))

    # ---- molang 批量替换汇总(降级/置零项必须人工过目: 引擎侧无声作废没有第二次机会) ----
    if molangReport:
        report.append("molang 批量替换汇总:")
        def _AsText(value):
            # py2: 标签里 str/unicode 混排, 直接 sorted 会对含中文的 str 触发
            # ascii 隐式解码而崩(野外包的动画名/物品名带中文即命中) —— 先统一
            return value.decode("utf-8") if isinstance(value, str) else value

        for label in sorted(molangReport, key=_AsText):
            count = molangReport[label]
            label = _AsText(label)
            marker = u"  [!]" if (label.startswith((u"zero:", u"warn:", u"lower:"))
                                  or (label.startswith(u"func:") and u"未知" in label)) \
                else u"     "
            report.append(u"{} {} x{}".format(marker, label, count))

    # ---- GUI 预览实体定义 ----
    defaultTexture = (manifest.get("properties") or {}).get("default_texture")
    textures = player.get("texture") or []
    firstTexture = textures[0] if textures else None
    firstName = BaseName(firstTexture.get("uv") if isinstance(firstTexture, dict) else firstTexture)
    skinName = defaultTexture or firstName or "default"
    # 包变量默认值烘进预览实体定义(见 BuildPackVariableDefaults 注): 预览并行动画
    # 引用的换装/定位/表情变量在纸娃娃实例上未初始化会把整个预览渲染打死; 预览
    # 实体是我们自己的定义, 用原生 scripts.initialize —— 运行期挂初始化控制器在
    # 预览实体上不生效(实机 2026-09)
    packInitLines = BuildPackVariableDefaults(manifest, packVars)
    entity = OrderedDict([
        ("format_version", "1.10.0"),
        ("minecraft:client_entity", OrderedDict([
            ("description", OrderedDict([
                ("identifier", "ysm_pack:{}".format(packName)),
                # default 用 bloom_nocull(entity_nocull + bloom 着色器): Java 侧是
                # entityCutoutNoCull, 不剔除背面; saury 是剔除版, 预览里火焰/飘带会缺面
                ("materials", OrderedDict([("default", "bloom_nocull"), ("tohru", "tohru")])),
                ("textures", OrderedDict([
                    ("default", "textures/entity/{}/{}".format(packName, skinName))])),
                ("geometry", OrderedDict([("default", "geometry.{}".format(packName))])),
                # 统一 GUI 换肤控制器(与 packParser.DEFAULT_GUI_RENDER_CONTROLLER 同名):
                # 运行层把多皮肤的贴图/几何数组追加到**这个**控制器上, 实体定义引用
                # 别的同构控制器(历史 compat 名)会让换肤数组注入落空 —— 多皮肤预览失效
                ("render_controllers", ["controller.render.ysm_pack_gui"]),
                ("scripts", OrderedDict([("initialize", [
                    "variable.ysm_skin = 0.0;", "variable.ysm_gui = 0.0;",
                    "variable.ysm_show = 0.0;", "variable.ysm_preview = 0.0;",
                    "variable.ysm_light = 0.0;",
                ] + packInitLines)])),
            ])),
        ])),
    ])
    DumpJson(os.path.join(RP, "entity", "{}.entity.json".format(packName)), entity)
    report.append("预览实体 → ysm_rp/entity/{}.entity.json (包变量初始化 {} 条)".format(
        packName, len(packInitLines)))

    # ---- 每实例变量初始化控制器(玩家侧纸娃娃, 见 BuildVariableInitController 注) ----
    initController = BuildVariableInitController(packName, packInitLines)
    initControllerPath = os.path.join(rpControllers, VARIABLE_INIT_FILE)
    if initController is not None:
        DumpJson(initControllerPath, initController)
        report.append("变量初始化控制器 → animation_controllers/{}/{} ({} 条, 主包按保留键 {} "
                      "恒开注册, 每渲染实例执行一次)".format(
                          packName, VARIABLE_INIT_FILE, len(packInitLines), VARIABLE_INIT_KEY))
    elif os.path.isfile(initControllerPath):
        os.remove(initControllerPath)

    # ---- 废弃产物清理: 归零动画/控制器(依赖 override_previous_animation)、GUI 预览副本 ----
    for stale in (os.path.join(rpAnims, STATE_RESET_FILE),
                  os.path.join(rpAnims, GUI_BASE_FILE),
                  os.path.join(rpControllers, STATE_RESET_CONTROLLER_FILE)):
        if os.path.isfile(stale):
            os.remove(stale)
            report.append("清理废弃的归零产物: {}".format(os.path.basename(stale)))
    dedupPairs = DedupPreLayer(packName)
    if dedupPairs:
        report.append("pre 层内部去重: 按 Java 通道顺序(后者覆盖前者)删掉 {} 个重复的"
                      "(骨骼,通道)对 —— 基岩是相加".format(dedupPairs))
    # 主链压住 pre 层的通道不在这里静态删(见 ApplyChannelOwnership 注末尾), 由逐通道覆盖按状态让位
    loopedPreview = LoopPreviewAnimation(packName, manifest)
    if loopedPreview:
        report.append(u"GUI 展示动画按 Java 强制循环(CapPredicate playLoopAnimation): {}".format(
            u", ".join(u"{}(原 loop={})".format(key, loop) for key, loop in loopedPreview)))

    # 折叠与旁路排在删通道步骤之后: 折出来的条件通道(scale: ["(v.x==1)?1:0", ...])
    # 若先折叠, 后面的去重会把它当普通通道删掉
    foldedVariants, skippedVariants = ReconcileConditionalVariants(packName)
    if foldedVariants:
        report.append(u"条件变体折叠: pre 层静态显隐与 parallel 层条件变体合并为单一所有者 "
                      u"{} 对(火焰/表情/嘴型: 隐藏基线 + 条件显示), 放弃非常量 {} 对".format(
                          foldedVariants, skippedVariants))
    hubBypasses = BypassEmptyHubStatesInPack(packName)
    if hubBypasses:
        report.append(u"空中转状态旁路转移 {} 条: 进/出不播动画的中转状态改为一步到位, "
                      u"落地/切换不再经绑定姿态".format(hubBypasses))
    # 淡出排在补 hold 之前: 之后 loop 已是 hold, 认不出 Java PLAY_ONCE(见 FadeControllerOneShots 注)
    fadedOneShots = FadeControllerOneShots(packName)
    if fadedOneShots:
        report.append(u"作者状态里的 PLAY_ONCE 动画补尾过渡淡出 {} 处: Java 播完后 3 tick 淡出并停止写通道, "
                      u"状态停留时姿态收回(萨赫梅特连段中切换物品)".format(len(fadedOneShots)))
    heldOneShots = HoldControllerOneShots(packName)
    if heldOneShots:
        report.append(u"控制器状态的一次性动画补 hold_on_last_frame {} 条: "
                      u"基岩 loop:false 播完即撤, 出态前会漏一帧底层姿态"
                      u"(实机: 持剑挥击收回时 punch 骨骼闪一下); Java 的尾过渡由上一步的淡出权重复刻"
                      .format(len(heldOneShots)))
    # 逐通道覆盖排在所有删通道/改状态步骤之后, 只处理它们剩下的冲突(见 ApplyChannelOwnership 注)
    ownership, _ownershipFiles = ApplyChannelOwnership(packName, manifest=manifest)
    report.extend(OwnershipReportLines(ownership))

    # ---- 一次性通道控制器(挥击/使用/受击/死亡重放, 见 BuildOneShotControllerFile 注) ----
    # 排在逐通道覆盖之后: 挥击/使用成员拆出的伴生动画要跟成员进同一状态
    oneShot = BuildOneShotControllerFile(packName, ownership=ownership)
    oneShotPath = os.path.join(rpControllers, ONESHOT_FILE)
    if oneShot is not None:
        DumpJson(oneShotPath, oneShot)
        report.append("一次性通道控制器 → animation_controllers/{}/{} ({}): 直挂不重放的挥击/"
                      "使用/受击/死亡改由状态机驱动".format(
                          packName, ONESHOT_FILE,
                          ", ".join(k.split(".")[-1] for k in oneShot["animation_controllers"])))
    elif os.path.isfile(oneShotPath):
        os.remove(oneShotPath)
    finishedRewrites = RewriteFinishedQueries(packName, ownership=ownership)
    if finishedRewrites:
        report.append(u"作者状态机\"动画播完\"判据按 Java 口径改写 {} 个状态: 基岩权重 0 的条目暂停计时、"
                      u"判据永不成立(互斥条件动画/让位伴生会让状态卡死), 改成按进入时刻计时".format(
                          finishedRewrites))

    # ---- Java 主链状态机(状态动画交叉淡化, 见 BuildStateChainControllerFile 注) ----
    stateChain = BuildStateChainControllerFile(packName, ownership=ownership)
    stateChainPath = os.path.join(rpControllers, STATE_FILE)
    if stateChain is not None:
        DumpJson(stateChainPath, stateChain)
        stateNames = list(stateChain["animation_controllers"].values())[0]["states"]
        report.append("主链状态机 → animation_controllers/{}/{} ({} 个状态, 切换交叉淡化 0.1s): "
                      "状态动画不再零过渡硬切".format(packName, STATE_FILE, len(stateNames)))
    elif os.path.isfile(stateChainPath):
        os.remove(stateChainPath)

    # ---- 运算符优先级显式化(收尾整体过一遍, 覆盖上面各步骤新写入的表达式) ----
    precedenceFixes = ExplicitPackPrecedence(packName)
    if precedenceFixes:
        report.append(u"运算符优先级显式化(收尾) {} 处: 资源包按 min_engine_version 1.18.0 走旧版 Molang 语义"
                      u"(三元左结合、&& 不比 || 紧), 两种语义可能分叉处补括号".format(precedenceFixes))

    # ---- 合集清单(存在则一并搬运, 触发文件夹分组) ----
    collectionSrc = os.path.join(os.path.dirname(javaDir), "ysm-pack.json")
    if collection and os.path.isfile(collectionSrc):
        DumpJson(os.path.join(BP_MODELS, collection, "ysm-pack.json"), LoadJson(collectionSrc))
        report.append("合集清单 → ysm_models/{}/ysm-pack.json".format(collection))
    return report


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--list":
        if os.path.isdir(JAVA_BUILTIN_DIR):
            print("子模块自带的官方 Java 包(可直接作为 <Java包目录>):")
            for name in sorted(os.listdir(JAVA_BUILTIN_DIR)):
                sub = os.path.join(JAVA_BUILTIN_DIR, name)
                if os.path.isfile(os.path.join(sub, "ysm.json")):
                    print("  {}".format(sub))
                elif os.path.isdir(sub):
                    for child in sorted(os.listdir(sub)):
                        if os.path.isfile(os.path.join(sub, child, "ysm.json")):
                            print("  {}".format(os.path.join(sub, child)))
        print(__doc__.split("用法:")[1].rstrip())
        return
    javaDir = args[0]
    packName = None
    collection = None
    withMods = False
    baseline = False
    index = 1
    while index < len(args):
        if args[index] == "--with-mods":
            withMods = True
            index += 1
            continue
        if args[index] == "--baseline":
            baseline = True
            index += 1
            continue
        if index + 1 >= len(args):
            break
        if args[index] == "--name":
            packName = args[index + 1]
        elif args[index] == "--collection":
            collection = args[index + 1]
        index += 2
    if baseline:
        packName = packName or JAVA_BASELINE_PACK
        for line in PortBaselinePack(javaDir, packName, withMods):
            if isinstance(line, unicode):  # noqa: F821
                line = line.encode("utf-8")
            print("  " + line)
        print("[DONE] {} 基线动画移植完成 —— 记得重启游戏(资源包改动需重载)".format(packName))
        return
    packName = packName or os.path.basename(os.path.normpath(javaDir))
    for line in PortPack(javaDir, packName, collection, withMods):
        if isinstance(line, unicode):  # noqa: F821 — py2 stdout 是字节流
            line = line.encode("utf-8")
        print("  " + line)
    print("[DONE] {} 移植完成 —— 记得重启游戏(资源包改动需重载)".format(packName))


if __name__ == "__main__":
    main()
