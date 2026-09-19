# -*- coding: utf-8 -*-
"""对**已移植落盘**的 JSON 模型包做一次性就地修复(port_java_pack 修复规则的补跑)。

port_java_pack.py 后补的三条转换规则只对"再跑一次移植"生效, 而已交付的包
(ref_warden / ref_sahmet / ref_wither 等)的 Java 源不一定还在本机 —— 此脚本
按同一套规则直接修 RP/BP 产物, 幂等可重跑:

1. 控制器条件里 ctrl.idle/walk/run/jump 的旧展开(裸 query.is_on_ground)
   → 稳定地面判据(_STABLE_ON_GROUND): 走路中 is_on_ground 每秒翻转数次,
   旧展开会让状态机行走时高频抖切(实机"动作鬼畜"元凶之一);
2. 控制器状态 animations 的死引用(包内动画全集+基线兜底名单都查无)与
   非法转移(目标状态未定义/条件为空白)剪掉 —— Java 静默容忍, 基岩逐帧刷
   "can't find animation" 且有连坐控制器失效的风险;
3. 控制器状态里引用的 parallel/pre_parallel 直播键并入 ysm.json 的
   ysm.json 精简迁移: initialize 提到顶层, 删掉可自动推导的字段(见 _OBSOLETE_NETEASE_KEYS);
4. ysm.json properties 子树的 v.roaming.* → v.roaming_*(与动画侧扁平化同步,
   否则 config_forms 表单写死变量, 动画读的是另一个);
5. 预览实体 scripts.initialize 回填包变量(port.BuildPackVariableDefaults):
   GUI 纸娃娃是独立实例, 玩家侧初始化到不了它 —— 预览并行动画引用的换装/定位/
   表情变量未初始化, 引擎逐骨骼通道刷 "unhandled request for unknown variable",
   量大时预览实体整个不渲染(实机: 转换包缩略图空白)。预览实体是我们自己的定义,
   用原生 initialize(运行期挂初始化控制器在其上不生效, 实机 2026-09);
6. 动画通道表达式的返回值规范化(port._NormalizeBoneChannels): 含 ";" 而无 return
   的复杂表达式在基岩返回 0 —— Root 缩放写成 "v.player_size=v.player_size;" 即
   整个模型不可见(sahmet/wither 真因), 纯表达式带尾分号则姿态归零; 去尾分号/补
   return 后与 Java 求值一致;
7. 非 parallel 动画加 override_previous_animation(port._ShouldOverridePrevious):
   基岩逐通道相加、Java 后者覆盖前者 —— idle 与 pre_parallel 同骨骼相加成两倍
   (实机: sahmet 尾巴过卷贴腿), 手持条件动画与 idle 手臂相加; 标志让动画只重置
   自身骨骼再应用, 配合主包按 Java 通道顺序排 animate 表即复刻分层覆盖。
8. 每实例变量初始化控制器 animation_controllers/<包>/ysm_variable_init.json
   (port.BuildVariableInitController): 玩家纸娃娃(原版背包/设置界面)是独立渲染实例,
   主包对世界实体的 SetPlayerVariable 到不了它 —— 包变量未定义按 0 求值(warden 整模
   消失/wither 没眼睛/sahmet 换装件全亮刷 unknown variable)。不改玩家实体定义
   (全局唯一覆盖点), 由主包发现该控制器后以保留键恒开注册, on_entry 逐条
   `v.x = v.x ?? 默认值;` 每实例跑一次。与第 5 条同源同口径, 一次运行同时生成。
9. 一次性通道控制器 animation_controllers/<包>/ysm_oneshot.json
   (port.BuildOneShotControllerFile → packParser.BuildOneShotControllers): 基岩直挂
   animate 条目对**不循环的定时动画不重放**(条件再次成立只是再应用, 时钟不回零 ——
   实机: 第三人称挥手只播第一次, 之后挖方块/攻击不再播), 挥击(主域+第一人称)/受击/
   死亡改由生成式状态机驱动: idle → 每成员独占状态(播完才走) → cooldown → idle,
   进入状态即重置时钟。主包在资源索引发现 ysm_swing/ysm_fp_swing/ysm_attacked/
   ysm_death 即替换对应直挂条目(位置、双门不变)。
10. Java 主链状态机 animation_controllers/<包>/ysm_state.json
   (port.BuildStateChainControllerFile → packParser.BuildStateChainController): 直挂
   条目是零过渡硬切换("两段动画衔接不流畅"的根因), 主链动画(idle/walk/run/jump/
   sneak/骑乘/泳姿/受击/死亡...)改由状态机驱动, 状态切换交叉淡化 0.1s = Java main/
   vehicle 通道的起始过渡; death/attacked 播完经 _done 空状态淡出 0.15s = Java 尾过渡。
11. 动画 loop 字段按 Java 运行语义改写(port.ApplyJavaLoopSemantics): 主链成员强制
   循环(Java 忽略 JSON 的 loop —— Java 包常把 sleep/sit/boat 写成不循环, 基岩听
   JSON 播一遍就停), death/attacked 与未写循环的主手挥击族改 hold_on_last_frame
   (保住可供淡出的末姿态)。
12. (2026-09-16) 动画字段按 Java 解析器语义归一(port.NormalizeLoopField /
   DropJavaIgnoredAnimationFields / StripStringStatements / ClampTimelineToLength),
   控制器状态按 Java 语义规范化 + blend_via_shortest_path(port.NormalizeJavaControllerStates /
   ApplyShortestPathBlend)。
   注意: second_order/first_order 物理函数的 molang 积分改写(port.PhysicsRewriter)
   需要 Java 源文件里的原始调用, 已落盘产物里调用早被剥成输入值 —— 无法就地修复,
   要恢复"Q 弹"随动请用 port_java_pack.py 从 Java 包重新移植。同理**只能在移植期做**的还有:
   animation_length 缺省 → Java 推导(末关键帧/无限长)、blend_transition 的 Java→基岩归属重映射、
   sound_effects 关键帧的音频拷贝与登记 —— 这三条产物里看不出原始形态, 请重新移植。

用法: python fix_ported_controllers.py [包名 ...]   (缺省修全部 JSON 包)
"""

import io
import os
import re
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import java_runtime_bindings as runtime_bindings  # noqa: E402  主包运行层声明(java_state)
import port_java_pack as port  # noqa: E402  复用同一套规则实现
from ysmModelScripts.packLoader.packParser import (  # noqa: E402
    _ITEM_IN_USE_TEST,
    _JAVA_ATTACK_TIME,
    _LEGACY_ITEM_IN_USE_TEST,
    _LEGACY_USE_MAINHAND_GATE,
    _LEGACY_USE_OFFHAND_GATE,
    _MAINHAND_BLOCKING_TEST,
    _OFFHAND_IN_USE_TEST,
    _USE_DURATION_SIGNAL,
    _USE_MAINHAND_GATE,
    _USE_OFFHAND_GATE,
    _V1_OFFHAND_IN_USE_TEST,
    _V2_ITEM_IN_USE_TEST,
    _V2_MAINHAND_BLOCKING_TEST,
    _V2_USE_MAINHAND_GATE,
    _V3_USE_DURATION_SIGNAL,
    _ItemTagTest,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RP = os.path.join(ROOT, "ysm_rp")
BP_MODELS = os.path.join(ROOT, "ysm_bp", "ysm_models")


def SetLayout(rp=None, bpModels=None, refRp=None, root=None):
    """重定向产物目录(转换器宿主用), 同时转给 port_java_pack.SetLayout —— 本脚本复用它的规则实现,
    两边的 RP / BP_MODELS 必须指向同一套产物。"""
    global RP, BP_MODELS, ROOT
    if rp is not None:
        RP = rp
    if bpModels is not None:
        BP_MODELS = bpModels
    if root is not None:
        ROOT = root
    port.SetLayout(rp=rp, bpModels=bpModels, refRp=refRp, root=root)


# ctrl.* 旧展开(修复前落盘形态) → 新展开(当前 _CTRL_NAME_MAP)。精确整串替换:
# 只有这些展开是移植器合成的、模式唯一的文本, 不会误伤作者手写条件(其中
# swim_stand 等水域语境的 is_on_ground 语义正确, 不在替换之列)。
_CTRL_NOW = dict(port._CTRL_NAME_MAP)
_OLD_TO_NEW_EXPANSIONS = [
    (
        "(query.is_on_ground&&query.modified_move_speed<=0.05&&!query.is_riding&&!query.is_sneaking)",
        _CTRL_NOW["idle"],
    ),
    (
        "(query.is_on_ground&&query.modified_move_speed>0.05&&!query.is_sprinting&&!query.is_sneaking)",
        _CTRL_NOW["walk"],
    ),
    ("(query.is_on_ground&&query.is_sprinting)", _CTRL_NOW["run"]),
    ("(!query.is_on_ground&&!query.is_in_water)", _CTRL_NOW["jump"]),
    # 2026-09-03 之前的"稳定地面"死区版展开 → 闩锁版(见 port._AIRBORNE_LATCH 注)
    (
        "(!(query.is_on_ground||(query.vertical_speed<=0&&query.vertical_speed>-4))&&!query.is_in_water)",
        _CTRL_NOW["jump"],
    ),
    (
        "((query.is_on_ground||(query.vertical_speed<=0&&query.vertical_speed>-4))"
        "&&query.modified_move_speed<=0.05&&!query.is_riding&&!query.is_sneaking)",
        _CTRL_NOW["idle"],
    ),
    (
        "((query.is_on_ground||(query.vertical_speed<=0&&query.vertical_speed>-4))"
        "&&query.modified_move_speed>0.05&&!query.is_sprinting&&!query.is_sneaking)",
        _CTRL_NOW["walk"],
    ),
    (
        "((query.is_on_ground||(query.vertical_speed<=0&&query.vertical_speed>-4))&&query.is_sprinting)",
        _CTRL_NOW["run"],
    ),
    # 2026-09-18 之前逐个独立映射的展开 → Java 互斥语义(读 variable.ysm_ctrl_main, 见 port._CTRL_MAIN_PRIORITY 注)。
    # 闩锁系 idle/walk/run 含 jump 的展开串, 必须先于 jump 替换; 裸查询形态(query.is_gliding / is_sleeping /
    # is_sneaking)与 attacked 的 (query.hurt_time>0) 分不清是否作者手写, 不迁(语义也几乎一致)
    (
        "((!((variable.ysm_airborne??0)>0.5))&&query.modified_move_speed<=0.05"
        "&&!query.is_riding&&!query.is_sneaking)",
        _CTRL_NOW["idle"],
    ),
    (
        "((!((variable.ysm_airborne??0)>0.5))&&query.modified_move_speed>0.05"
        "&&!query.is_sprinting&&!query.is_sneaking)",
        _CTRL_NOW["walk"],
    ),
    ("((!((variable.ysm_airborne??0)>0.5))&&query.is_sprinting)", _CTRL_NOW["run"]),
    ("((variable.ysm_airborne??0)>0.5)", _CTRL_NOW["jump"]),
    ("(query.is_sneaking&&query.modified_move_speed>0.05)", _CTRL_NOW["sneak"]),
    ("(query.is_in_water&&!query.is_swimming&&!query.is_on_ground)", _CTRL_NOW["swim_stand"]),
    ("(query.swim_amount>0)", _CTRL_NOW["swim"]),
    ("(query.mod.ysm_is_on_ladder>0.5&&query.mod.ysm_climbing_vector>0)", _CTRL_NOW["ladder_up"]),
    ("(query.mod.ysm_is_on_ladder>0.5&&query.mod.ysm_climbing_vector==0)", _CTRL_NOW["ladder_stillness"]),
    ("(query.mod.ysm_is_on_ladder>0.5&&query.mod.ysm_climbing_vector<0)", _CTRL_NOW["ladder_down"]),
    ("(query.is_crawling&&query.modified_move_speed>0.05)", _CTRL_NOW["climb"]),
    ("(query.is_crawling&&query.modified_move_speed<=0.05)", _CTRL_NOW["climbing"]),
    ("(query.death_ticks>0)", _CTRL_NOW["death"]),
    ("query.mod.ysm_is_flying", _CTRL_NOW["fly"]),
    # ground_speed 替换式: 早年裸式 → 死区式 → 2026-09-18 起主包按 Java 口径逐帧计算的摩擦后速度
    # (见 port._GROUND_SPEED_EXPR 注)。旧产物分不出原文是 query.ground_speed 还是 ysm.ground_speed2, 一律按前者
    # (后者只有 36 处, 重新移植可得精确映射); 动画文本同样迁移
    ("(query.modified_move_speed*1.9)", port._GROUND_SPEED_EXPR),
    (port._LEGACY_GROUND_SPEED_EXPR, port._GROUND_SPEED_EXPR),
    # Java query.is_jumping = 腾空且不飞行不骑乘(QueryBinding); 2026-09-18 前原样透传成基岩同名 query, 而那是
    # "跳跃键按住"(实机: 点按只有 2 帧为 1, 松键后仍在空中归 0, 纯下落全程 0) —— 各包挥击时间线拿它选跳劈。
    # 本工具只处理移植包, 产物里的 is_jumping 都来自 Java 原文; 新式不含旧串, 迁移幂等
    ("query.is_jumping", runtime_bindings.IS_JUMPING_EXPR),
    ("q.is_jumping", runtime_bindings.IS_JUMPING_EXPR),
]
# Java ysm.input_vertical/horizontal 是位移方向(不是按键), 旧产物映射成了按键向量 query.mod.ysm_input_* → 迁到主包
# 逐帧计算的位移方向量。Java 的 xxa/zza 才是真按键量, 移植工具给它们带 `1*` 前缀(port._JAVA_NAME_MAP), 这里跳过
_LEGACY_INPUT_VECTOR_PATTERN = re.compile(r"(?<!1\*)query\.mod\.ysm_input_(vertical|horizontal)")


def _CollectPackAnimKeys(packName):
    """RP 动画目录里该包(主/arm 命名空间)的全部注册短键"""
    keys = set()
    animDir = os.path.join(RP, "animations", packName)
    if not os.path.isdir(animDir):
        return keys
    prefixes = ("animation.{}.".format(packName), "animation.{}_arm.".format(packName))
    for name in os.listdir(animDir):
        if not name.endswith(".json"):
            continue
        try:
            data = port.LoadJson(os.path.join(animDir, name))
        except ValueError:
            continue
        for animId in data.get("animations") or {}:
            for prefix in prefixes:
                if animId.startswith(prefix):
                    shortKey = str(animId[len(prefix) :])
                    keys.add(shortKey)
                    if prefix.endswith("_arm."):
                        # arm 键同时以 fp_ 前缀注册(解析器 _LoadArmAnimations), 一次性
                        # 通道控制器的 FP 状态引用的正是 fp_ 键, 剪枝不能当死引用
                        keys.add("fp_" + shortKey)
                    break
    return keys


def _FixControllers(packName, knownKeys, report):
    """控制器文件: 剪枝(死引用/非法转移) + 稳定地面替换; 返回引用到的并行直播键。

    **跳过本工具/移植工具自己生成的控制器**(ysm_state / ysm_oneshot / ysm_variable_init):
    它们由各自的生成步骤整份重建, 再对其做旧版判据的文本替换只会打架 —— 主链状态机的
    jump 粘滞判据 `!query.is_on_ground&&!query.is_in_water` 正好是 ctrl.jump 的旧展开形态,
    被替换后下一步又按生成器重建, 两步无限对刷(幂等性守护逮到)。
    """
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    referencedParallels = []
    if not os.path.isdir(ctlDir):
        return referencedParallels
    generated = (
        port.STATE_FILE,
        port.ONESHOT_FILE,
        port.VARIABLE_INIT_FILE,
        port.STATE_RESET_CONTROLLER_FILE,
    )
    for name in sorted(os.listdir(ctlDir)):
        if not name.endswith(".json") or name in generated:
            continue
        path = os.path.join(ctlDir, name)
        data = port.LoadJson(path)
        controllers = data.get("animation_controllers")
        if not isinstance(controllers, dict):
            continue
        prunedRefs, prunedTransitions, emptiedStates = [], [], []
        javaFixes = 0
        for body in controllers.values():
            if not isinstance(body, dict):
                continue
            # 控制器状态按 Java 解析器语义规范化(blend 曲线→数字 / 状态级音效粒子删除 /
            # 多键条目取首键 / apply 条件布尔化) + Java 四元数过渡 → blend_via_shortest_path。
            # blend_transition 的 Java→基岩归属重映射**不在此**(非幂等, 只在移植期做一次)
            javaFixes += port.NormalizeJavaControllerStates(body)
            port._PruneControllerBody(body, knownKeys, prunedRefs, prunedTransitions, emptiedStates)
            javaFixes += port.ApplyShortestPathBlend(body)
        # 表达式迁移(旧展开 → 闩锁/死区)与残缺前缀清理**先于**旁路转移: 旁路的复合条件要基于
        # 最终形态, 否则第二遍会按新形态再生成一批(幂等性守护逮到)。残缺前缀实例: 凋灵娘
        # "q.all_animations_finishedctrl." 让引擎报 unrecognized token, 整个控制器作废
        stableFixes = _MigrateExpressionText(data)
        danglingFixes = _NeutralizeDanglingInData(data)
        # 最后一道: 基岩解析不了的表达式按 Java 口径处置(见 devtools/molang_syntax.py 注)
        guardHits = []
        for body in controllers.values():
            if isinstance(body, dict):
                guardHits += port.GuardControllerMolang(body)
        for body in controllers.values():
            if not isinstance(body, dict):
                continue
            referencedParallels += port._CollectParallelRefs(body)
        text = port.SerializeForDisk(data)  # 落盘格式跟随 port.JSON_COMPACT(转换器可切成压一行)
        port.WriteBytes(path, text.encode("utf-8"))
        notes = []
        if javaFixes:
            notes.append(
                "控制器状态按 Java 语义规范化 {} 处(blend 曲线→数字 / 状态级音效粒子删除 / "
                "多键条目取首键 / apply 条件布尔化 / blend_via_shortest_path)".format(javaFixes)
            )
        if danglingFixes:
            notes.append("清理残缺 Java 前缀 {} 处(引擎 unrecognized token)".format(danglingFixes))
        if guardHits:
            notes.append(
                "基岩解析不了的表达式按 Java 口径处置 {} 处(整份文件拒载): {}".format(
                    len(guardHits),
                    "; ".join(
                        "{} {}".format(port.FormatSlotPath(path), problem)
                        for path, _text, problem in guardHits[:4]
                    ),
                )
            )
        if emptiedStates:
            # 空 animations 数组 = 整份控制器文件被网易引擎拒载(见
            # port._PruneControllerBody 注释), 单独高亮报告
            notes.append(
                "删空数组键 {} 处(animations {} / transitions {}) —— 空 animations 会让整份文件拒载".format(
                    len(emptiedStates), emptiedStates.count("animations"), emptiedStates.count("transitions")
                )
            )
        if prunedRefs:
            notes.append(
                "剪死引用 {} 处({})".format(
                    len(prunedRefs),
                    ", ".join(sorted(set(prunedRefs))[:6]) + (" ..." if len(set(prunedRefs)) > 6 else ""),
                )
            )
        if prunedTransitions:
            notes.append(
                "剪非法转移 {} 处({})".format(
                    len(prunedTransitions), ", ".join(sorted(set(prunedTransitions))[:4])
                )
            )
        if stableFixes:
            notes.append(
                "表达式整串迁移 {} 处(稳定地面判据 / ctrl.use 分手门 / "
                "is_using_item→原版使用判据 / Java 物品 tag→基岩内置 tag / "
                "无 else 三元补 `: 0`)".format(stableFixes)
            )
        report.append("  控制器 {}: {}".format(name, "; ".join(notes) if notes else "无需修改"))
    return referencedParallels


# 已由主包自动推导、不该再出现在 ysm.json 里的字段(见 packParser._DrivenParallelKeys /
# Java 模式的声明形态判定): 迁移时直接删掉
_OBSOLETE_NETEASE_KEYS = ("animate_remove", "java_state_driver")


def _FixManifest(packName, referencedParallels, report):
    """ysm.json 精简迁移: initialize 提到顶层, 删掉可自动推导的字段与空 netease 段,
    properties 里的 v.roaming.* 扁平化"""
    path = port.PackManifestPath(packName)
    if not os.path.isfile(path):
        return
    manifest = port.LoadJson(path)
    changed = False
    netease = manifest.get("netease")
    if isinstance(netease, dict):
        # initialize: netease 段 → 顶层(标准位置); 两处都有时顶层优先, 缺的补齐
        neteaseInit = list(netease.pop("initialize", None) or [])
        if neteaseInit:
            topInit = list(manifest.get("initialize") or [])
            declared = set(
                entry.split("=")[0].strip() for entry in topInit if isinstance(entry, (str, unicode))
            )  # noqa: F821
            merged = topInit + [
                entry
                for entry in neteaseInit
                if not (
                    isinstance(entry, (str, unicode))  # noqa: F821
                    and entry.split("=")[0].strip() in declared
                )
            ]
            manifest["initialize"] = merged
            changed = True
            report.append("  ysm.json: initialize {} 条 netease → 顶层".format(len(merged)))
        dropped = [key for key in _OBSOLETE_NETEASE_KEYS if key in netease]
        for key in dropped:
            del netease[key]
        if dropped:
            changed = True
            report.append("  ysm.json: 删掉可自动推导的字段 {}".format(", ".join(dropped)))
        if not netease:
            del manifest["netease"]
            changed = True
            report.append("  ysm.json: 删掉空的 netease 兼容段")
    flattened = port._FlattenRoamingStrings(manifest.get("properties"))
    if flattened:
        changed = True
        report.append("  ysm.json: 表单变量 v.roaming.* 扁平化 {} 处".format(flattened))
    if changed:
        port.DumpJson(path, manifest)
    else:
        report.append("  ysm.json: 无需修改")


_GUI_RENDER_CONTROLLER = "controller.render.ysm_pack_gui"
_ENTITY_BASE_INIT = [
    "variable.ysm_skin = 0.0;",
    "variable.ysm_gui = 0.0;",
    "variable.ysm_show = 0.0;",
    "variable.ysm_preview = 0.0;",
    "variable.ysm_light = 0.0;",
]


def _ScanPackVars(packName):
    """RP 动画+控制器产物文本里引用的 molang 变量短名全集(玩家侧文件)"""
    variables = set()
    for subDir in ("animations", "animation_controllers"):
        dirPath = os.path.join(RP, subDir, packName)
        if not os.path.isdir(dirPath):
            continue
        for name in os.listdir(dirPath):
            if not name.endswith(".json") or name == port.VARIABLE_INIT_FILE:
                continue  # 变量初始化控制器是本工具产物, 不回扫
            with io.open(os.path.join(dirPath, name), "r", encoding="utf-8-sig") as f:
                variables.update(port._MOLANG_VAR_SCAN.findall(f.read()))
    # 默认模型基线动画在本包上播放时读的变量(与移植工具 PortPack 同口径, 见 port.CollectBaselineVariables)
    variables |= port.CollectBaselineVariables(packName)
    return variables


def _FixEntityInitialize(packName, report):
    """预览实体 scripts.initialize = ysm_* 五件套 + 包变量默认值(幂等重建)。

    与玩家侧初始化控制器同源同口径(port.BuildPackVariableDefaults); 预览实体是
    我们自己的实体定义, 用原生 initialize —— 运行期挂初始化控制器在预览实体上
    不生效(实机 2026-09, 缩略图逐通道刷 unknown variable)。
    """
    entityPath = os.path.join(RP, "entity", "{}.entity.json".format(packName))
    manifestPath = port.PackManifestPath(packName)
    if not os.path.isfile(entityPath) or not os.path.isfile(manifestPath):
        return
    manifest = port.LoadJson(manifestPath)
    newInit = _ENTITY_BASE_INIT + port.BuildPackVariableDefaults(manifest, _ScanPackVars(packName))
    entity = port.LoadJson(entityPath)
    description = (entity.get("minecraft:client_entity") or {}).get("description")
    if not isinstance(description, dict):
        report.append("  [WARN] 预览实体定义结构异常, 跳过 initialize 回填")
        return
    scripts = description.setdefault("scripts", OrderedDict())
    changed = False
    # GUI 换肤控制器统一为 ysm_pack_gui(运行层的换肤数组注入目标), 历史模板写的
    # compat 同构控制器收不到数组 —— 多皮肤预览失效
    controllers = description.get("render_controllers")
    # 预览实体的 default 材质迁移到不剔除背面版(Java 是 entityCutoutNoCull, 见 materialResolver)
    materials = description.get("materials")
    if isinstance(materials, dict) and materials.get("default") in ("saury", "bloom", "entity"):
        materials["default"] = "bloom_nocull"
        report.append("  预览实体材质 default → bloom_nocull(不剔除背面, 对齐 Java)")
        changed = True
    if isinstance(controllers, list) and controllers != [_GUI_RENDER_CONTROLLER]:
        description["render_controllers"] = [_GUI_RENDER_CONTROLLER]
        report.append("  预览实体 GUI 控制器 {} → {}".format(", ".join(controllers), _GUI_RENDER_CONTROLLER))
        changed = True
    if list(scripts.get("initialize") or []) != newInit:
        scripts["initialize"] = newInit
        report.append(
            "  预览实体 initialize 回填包变量 {} 条(共 {} 条)".format(
                len(newInit) - len(_ENTITY_BASE_INIT), len(newInit)
            )
        )
        changed = True
    if not changed:
        report.append("  预览实体定义: 无需修改")
        return
    port.DumpJson(entityPath, entity)


def _FixVariableInitController(packName, report):
    """每实例变量初始化控制器(port.BuildVariableInitController)幂等重建 —— 玩家纸娃娃
    的包变量初始化不改 entity/player.entity.json, 走此控制器由主包接口注册"""
    manifestPath = port.PackManifestPath(packName)
    if not os.path.isfile(manifestPath):
        return
    manifest = port.LoadJson(manifestPath)
    initLines = port.BuildPackVariableDefaults(manifest, _ScanPackVars(packName))
    controller = port.BuildVariableInitController(packName, initLines)
    path = os.path.join(RP, "animation_controllers", packName, port.VARIABLE_INIT_FILE)
    if controller is None:
        if os.path.isfile(path):
            os.remove(path)
            report.append("  变量初始化控制器: 包无可初始化变量, 删除遗留文件")
        return
    if os.path.isfile(path) and port.LoadJson(path) == controller:
        report.append("  变量初始化控制器: 已是最新({} 条变量)".format(len(initLines)))
        return
    port.DumpJson(path, controller)
    report.append(
        "  变量初始化控制器 {}: 重建({} 条变量, 每渲染实例执行一次)".format(
            port.VARIABLE_INIT_FILE, len(initLines)
        )
    )


def _FixOneShotControllers(packName, report, ownership=None):
    """一次性通道控制器(port.BuildOneShotControllerFile)幂等重建 —— 直挂 animate 条目
    不重放不循环的定时动画(实机: 第三人称挥手只播第一次), 挥击/使用/受击/死亡改走状态机;
    ownership: 逐通道覆盖规划(成员的伴生动画随成员进同一状态)"""
    path = os.path.join(RP, "animation_controllers", packName, port.ONESHOT_FILE)
    fileBody = port.BuildOneShotControllerFile(packName, ownership=ownership)
    if fileBody is None:
        if os.path.isfile(path):
            os.remove(path)
            report.append("  一次性通道控制器: 包无挥击/受击/死亡动画, 删除遗留文件")
        return
    names = ", ".join(k.split(".")[-1] for k in fileBody["animation_controllers"])
    if os.path.isfile(path) and port.LoadJson(path) == fileBody:
        report.append("  一次性通道控制器: 已是最新({})".format(names))
        return
    port.DumpJson(path, fileBody)
    report.append(
        "  一次性通道控制器 {}: 重建({}) —— 直挂条目不重放, 改由状态机驱动".format(port.ONESHOT_FILE, names)
    )


def _OwnershipSnapshot(packName):
    """伴生动画涉及的内容(玩家动画 + 作者控制器 + ysm.json 的 channel_ownership)的序列化文本"""
    files = port._LoadPackAnimationFiles(packName) + port._OwnershipControllerFiles(packName)
    snapshot = dict((path, port._SerializeJson(data)) for path, data in files)
    manifestPath = port.PackManifestPath(packName)
    if os.path.isfile(manifestPath):
        snapshot[port.OWNERSHIP_MANIFEST_KEY] = port._SerializeJson(
            port.LoadJson(manifestPath).get(port.OWNERSHIP_MANIFEST_KEY)
        )
    return snapshot


def _FixStateChainController(packName, report, ownership=None):
    """Java 主链状态机(port.BuildStateChainControllerFile)幂等重建 —— 直挂条目零过渡硬切,
    主链动画改由状态机驱动, 状态切换交叉淡化 0.1s(Java main 通道起始过渡)"""
    path = os.path.join(RP, "animation_controllers", packName, port.STATE_FILE)
    fileBody = port.BuildStateChainControllerFile(packName, ownership=ownership)
    if fileBody is None:
        if os.path.isfile(path):
            os.remove(path)
            report.append("  主链状态机: 包无主链动画, 删除遗留文件")
        return
    stateCount = len(list(fileBody["animation_controllers"].values())[0]["states"])
    if os.path.isfile(path) and port.LoadJson(path) == fileBody:
        report.append("  主链状态机: 已是最新({} 个状态)".format(stateCount))
        return
    port.DumpJson(path, fileBody)
    report.append(
        "  主链状态机 {}: 重建({} 个状态, 切换交叉淡化 0.1s) —— 直挂条目零过渡硬切, 改由状态机驱动".format(
            port.STATE_FILE, stateCount
        )
    )


def _CollectPreKeys(packName):
    """RP 控制器里 pre 通道控制器(player_pre_parallel_*/pre_main/vehicle)引用的动画短键
    (排在主链之前、同处淡化链路 → 不带 override, 见 port._ShouldOverridePrevious)"""
    groups = port.CollectPreChannelAnimationKeys(packName)
    return set(groups.get("always_on") or ()) | set(groups.get("gated") or ())


def _NeutralizeDanglingInData(node):
    """控制器/动画数据里所有字符串值清理残缺的 ysm./ctrl./fn. 前缀; 返回删除数"""
    fixes = 0
    if isinstance(node, dict):
        for key in list(node.keys()):
            value = node[key]
            if isinstance(value, (str, unicode)):  # noqa: F821
                newValue, count = port.NeutralizeDanglingJavaPrefixes(value)
                if count:
                    node[key] = newValue
                    fixes += count
            else:
                fixes += _NeutralizeDanglingInData(value)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, (str, unicode)):  # noqa: F821
                newValue, count = port.NeutralizeDanglingJavaPrefixes(value)
                if count:
                    node[index] = newValue
                    fixes += count
            else:
                fixes += _NeutralizeDanglingInData(value)
    return fixes


# ctrl.use(...) 的门控 2026-09-04 起按手分支(基岩 query.is_using_item 不分手, 见
# packParser._OFFHAND_IN_USE_TEST)。早先移植产物里烧进去的是裸 query.is_using_item,
# 按同一括号里判据的槽位补回分手门。迁移后形态以 "(query.is_using_item&&!(" / "&&(("
# 开头, 不再匹配本正则 —— 幂等。
_OLD_USE_GATE_PATTERN = re.compile(
    r"\(query\.is_using_item&&(query\.[A-Za-z_]+\('slot\.weapon\.(mainhand|offhand)'[^()]*\))\)"
)


def _MigrateUseGate(text):
    """裸 is_using_item 门 → 按槽位分手的门; 返回 (新串, 替换数)"""

    def _Replace(match):
        gate = port._USE_OFFHAND_GATE if match.group(2) == "offhand" else port._USE_MAINHAND_GATE
        return "({}&&{})".format(gate, match.group(1))

    return _OLD_USE_GATE_PATTERN.subn(_Replace, text)


# equipped_item_any_tag(...) 的参数里烧着 Java 原版 tag 名(#minecraft:pickaxes 直通)
# —— 基岩内置 tag 是另一套命名(minecraft:is_pickaxe), 判定恒假。实测(2026-09-05 warden):
# 悬浮臂状态机 player.parallel_1/2 的转移全吃这几个 tag, 于是永远停在"缓冲"态,
# 持镐/斧/锹/锄的拳击姿态与持盾防御姿态一条都不播。
# 整个调用交给 packParser._ItemTagTest 重建 —— tag 改名与原版具名兜底一次到位, 且与
# 运行期条件动画走同一套合成(基岩 tag 覆盖稀疏且不认时静默恒假, 不能只靠它一条腿)。
# **只重建 equipped_item_any_tag**: is_item_name_any 里同形串是物品 ID, 不能动。
# 已展开形态 `(tag||具名)` 单列一条分支同样按参数重建 —— 重建结果一致, 故幂等。
# 剑判定的 Java 口径形态 `(is_sword 调用&&!(get_equipped_item_name(手)=='mace'))`(packParser._NON_JAVA_SWORD_ITEMS:
# 重锤不是 Java 的剑)同理整段吃掉再按参数重建, 否则里面的裸调用下一遍又被套一层。2026-09-18 当天短暂用过的
# 否决变量形态 `&&!(variable.ysm_sword_veto_*??0)`(共享控制器的转移在无实体实例上刷错, 已撤)同样认, 重建成现行形态。
_TAG_CALL_PATTERN = re.compile(
    r"\(query\.equipped_item_any_tag\((?P<wrapped>[^()]*)\)"
    r"\|\|query\.is_item_name_any\([^()]*\)\)"
    r"|\(query\.equipped_item_any_tag\((?P<vetoed>[^()]*)\)"
    r"(?:&&!\(variable\.ysm_sword_veto_(?:main|off)\?\?0\)"
    r"|(?:&&!\(query\.get_equipped_item_name\('(?:main_hand|off_hand)'\)=='[A-Za-z0-9_.]+'\))+)\)"
    r"|query\.equipped_item_any_tag\((?P<bare>[^()]*)\)"
)


def _MigrateItemTags(text):
    """equipped_item_any_tag 调用按当前规则重建(Java tag 改名 + 剑判定排除重锤)"""
    counter = [0]

    def _Replace(match):
        args = match.group("wrapped")
        if args is None:
            args = match.group("vetoed")
        if args is None:
            args = match.group("bare")
        parts = [part.strip().strip("'\"") for part in args.split(",")]
        if len(parts) < 2 or not all(parts):
            return match.group(0)  # 形态不认识就原样留着
        rebuilt = _ItemTagTest(parts[0], parts[1:])
        if rebuilt != match.group(0):
            counter[0] += 1
        return rebuilt

    return _TAG_CALL_PATTERN.sub(_Replace, text), counter[0]


def _MigrateItemUseSignal(text):
    """query.is_using_item → 原版自用的使用判据; 返回 (新串, 替换数)。

    网易 3.8.0 的原版资源包对 query.is_using_item 零引用, 实测整族 use_* 条件动画
    一条都不播(拉弓没有瞄准姿态) —— 换成 main_hand_item_use_duration/blocking
    (原版 player.entity.json 与 attachable 动画在用的量, 见 packParser 同注)。
    新串里不再含 query.is_using_item —— 幂等。
    """
    if "query.is_using_item" not in text:
        return text, 0
    return text.replace("query.is_using_item", _ITEM_IN_USE_TEST), text.count("query.is_using_item")


def _MigrateLegacyBlockingGates(text):
    """历代使用门控 → 现行门控; 返回 (新串, 替换数)。

    v1(2026-09-16 之前): query.blocking 不看是否持盾, 潜行就会命中;
    v2(2026-09-16 白天): blocking 配持盾, 但没有输入状态锁存 —— 格挡中出手时 blocking 掉线
    2~3 tick, 作者的格挡状态被打断(坚守者娘悬浮拳"重置再恢复");
    v3(2026-09-17 凌晨): 使用计时只读锁存(`??` 仅在锁存缺席时回落), 骨骼通道读不到计时就整段
    不出拉弓姿态 —— 现行口径与原始 query 取或(packParser._USE_DURATION_SIGNAL 注)。
    先换整条门控, 再换剩余的"使用中"判据与单手格挡判据; 现行串不含任何旧串 —— 幂等。
    """
    fixes = 0
    for oldExpr, newExpr in (
        (_V3_USE_DURATION_SIGNAL, _USE_DURATION_SIGNAL),
        (_LEGACY_USE_MAINHAND_GATE, _USE_MAINHAND_GATE),
        (_LEGACY_USE_OFFHAND_GATE, _USE_OFFHAND_GATE),
        (_V2_USE_MAINHAND_GATE, _USE_MAINHAND_GATE),
        (_V2_ITEM_IN_USE_TEST, _ITEM_IN_USE_TEST),
        (_LEGACY_ITEM_IN_USE_TEST, _ITEM_IN_USE_TEST),
        (_V2_MAINHAND_BLOCKING_TEST, _MAINHAND_BLOCKING_TEST),
        (_V1_OFFHAND_IN_USE_TEST, _OFFHAND_IN_USE_TEST),
    ):
        if oldExpr in text:
            fixes += text.count(oldExpr)
            text = text.replace(oldExpr, newExpr)
    return text, fixes


# Java ysm.attack_time/swinging/ctrl.swing 早先直译成裸 variable.attack_time; Java 使用物品期间攻击
# 无效, 现行口径读主包输入状态的静音标记(packParser._JAVA_ATTACK_TIME)。已包装的不再匹配 —— 幂等。
_RAW_ATTACK_TIME_PATTERN = re.compile(r"variable\.attack_time(?!\*\(1-\(variable\.ysm_swing_muted\?\?0\)\))")


def _MigrateAttackTime(text):
    """产物里裸 variable.attack_time → 静音感知的 Java 挥动进度; 返回 (新串, 替换数)"""
    return _RAW_ATTACK_TIME_PATTERN.subn(_JAVA_ATTACK_TIME, text)


def _MigrateOneString(value):
    """单个表达式串的整串迁移; 返回 (新串, 替换数)"""
    fixes = 0
    for oldExpr, newExpr in _OLD_TO_NEW_EXPANSIONS:
        if oldExpr != newExpr and oldExpr in value:
            fixes += value.count(oldExpr)
            value = value.replace(oldExpr, newExpr)
    value, inputFixes = _LEGACY_INPUT_VECTOR_PATTERN.subn(r"query.mod.ysm_move_\1", value)
    fixes += inputFixes
    # 顺序要紧: 分手门迁移的正则认的是裸 is_using_item 形态, 必须排在信号替换之前
    value, gateFixes = _MigrateUseGate(value)
    value, useFixes = _MigrateItemUseSignal(value)
    value, blockingFixes = _MigrateLegacyBlockingGates(value)
    useFixes += blockingFixes
    value, attackFixes = _MigrateAttackTime(value)
    useFixes += attackFixes
    value, tagFixes = _MigrateItemTags(value)
    # 无 else 三元补全排最后: 前面几步会生成/改写表达式, 补全要基于最终形态
    value, ternaryFixes = port.NormalizeBareTernary(value)
    return value, fixes + gateFixes + useFixes + tagFixes + ternaryFixes


def _MigrateExpressionText(node):
    """动画体里所有字符串值按 _OLD_TO_NEW_EXPANSIONS 整串迁移(ground_speed 死区等); 返回替换数"""
    fixes = 0
    if isinstance(node, dict):
        for key in list(node.keys()):
            value = node[key]
            if isinstance(value, (str, unicode)):  # noqa: F821
                newValue, added = _MigrateOneString(value)
                fixes += added
                if newValue != value:
                    node[key] = newValue
            else:
                fixes += _MigrateExpressionText(value)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, (str, unicode)):  # noqa: F821
                newValue, added = _MigrateOneString(value)
                fixes += added
                if newValue != value:
                    node[index] = newValue
            else:
                fixes += _MigrateExpressionText(value)
    return fixes


def _RewriteTimelineStatements(body):
    """timeline 里的 Java 声明默认值语句(v.x || v.x = 0;)改写为合法形态; 返回改写数"""
    timeline = body.get("timeline")
    if not isinstance(timeline, dict):
        return 0
    fixes = 0
    for stamp in list(timeline.keys()):
        value = timeline[stamp]
        if isinstance(value, (str, unicode)):  # noqa: F821
            newValue, count = port.RewriteDeclareDefaultStatements(value)
            if count:
                timeline[stamp] = newValue
                fixes += count
        elif isinstance(value, list):
            newList = []
            for line in value:
                if isinstance(line, (str, unicode)):  # noqa: F821
                    line, count = port.RewriteDeclareDefaultStatements(line)
                    fixes += count
                newList.append(line)
            if fixes:
                timeline[stamp] = newList
    return fixes


def _CollectAdditiveKeys(packName):
    """RP 控制器里 parallel 通道控制器(player_parallel_N)引用的动画短键(Java 相加语义)"""
    keys = set()
    ctlDir = os.path.join(RP, "animation_controllers", packName)
    if not os.path.isdir(ctlDir):
        return keys
    for name in os.listdir(ctlDir):
        if name.endswith(".json"):
            keys |= port.CollectParallelChannelAnimKeys(port.LoadJson(os.path.join(ctlDir, name)))
    return keys


def _BindTimelineParticles(body):
    """移植工具生成的粒子关键帧(效果键 ysm_pt_*)补 bind_to_actor:false; 返回补齐数(幂等)。

    Java ParticleSpawner 把粒子生成在世界坐标后交给粒子引擎, 不随实体移动; 基岩缺省 true 发射器
    跟着玩家跑(见 port.ConvertTimelineParticles 注)。
    """
    effects = body.get("particle_effects") if isinstance(body, dict) else None
    if not isinstance(effects, dict):
        return 0
    fixes = 0
    for value in effects.values():
        for entry in value if isinstance(value, list) else [value]:
            if not isinstance(entry, dict):
                continue
            if (
                str(entry.get("effect", "")).startswith(port._PARTICLE_LOCATOR_PREFIX)
                and entry.get("bind_to_actor") is not False
            ):
                entry["bind_to_actor"] = False
                fixes += 1
    return fixes


def _FixAnimationChannels(packName, report):
    """RP 动画文件: 通道表达式规范化 + override 标志 + 收尾保持帧(全部幂等)"""
    animDir = os.path.join(RP, "animations", packName)
    if not os.path.isdir(animDir):
        return
    additiveKeys = _CollectAdditiveKeys(packName)
    preKeys = _CollectPreKeys(packName)
    # 玩家侧命名空间(主/arm): loop 语义改写只作用于它们, 替换实体(弹射物/载具)的
    # 动画文件同在此目录, 其 idle 等同名键不是玩家主链成员
    playerPrefixes = ("animation.{}.".format(packName), "animation.{}_arm.".format(packName))
    for name in sorted(os.listdir(animDir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(animDir, name)
        data = port.LoadJson(path)
        animations = data.get("animations")
        if not isinstance(animations, dict):
            continue
        vectorFixes = stmtFixes = overrideFixes = tailFixes = loopFixes = 0
        sanitizeFixes = timelineFixes = lerpFixes = gateFixes = particleFixes = 0
        guardHits = []
        for animId, body in animations.items():
            if not isinstance(body, dict):
                continue
            # Java 解析器层面的字段语义(loop 三态归一 / 运行时忽略的字段 / 字符串字面量语句)
            if port.NormalizeLoopField(body):
                loopFixes += 1
            stmtFixes += port.DropJavaIgnoredAnimationFields(body)
            timelineFixes += port.StripStringStatements(body)
            v, s = port._NormalizeBoneChannels(body)
            vectorFixes += v
            stmtFixes += s
            # 表达式关键帧形态规范化: catmullrom→linear + 仅 post 补 pre
            # 排在补收尾帧之前
            lerpFixes += port.NormalizeExpressionKeyframes(body)
            # 关键帧结束到 animation_length 之间的空档补"保持末值"帧
            # (基岩会对这段插值, catmullrom 下会把通道值拉塌; 见 port.SealAnimationTails)
            # 首帧之前同理补"保持首帧"帧(port.SealAnimationHeads, 幂等)。
            # port.ApplyJavaCatmullSegments 非幂等, 只在移植期跑, 这里不调用
            tailFixes += port.SealAnimationHeads(body)
            tailFixes += port.SealAnimationTails(body)
            # Java 分层覆盖语义 → override_previous_animation(parallel0-7 与主链以外)。
            # **必须用注册键**(去 animation.<ns>. 两段)而不是 ID 末段: 条件/骑乘键含点,
            # 末段取法会把 carryon.cls.princess 读成 princess → 主链判定落空, 移植工具与
            # 本工具产物打架(幂等性守护逮到)
            shortKey = (
                animId.split(".", 2)[-1] if animId.startswith(playerPrefixes) else animId.split(".")[-1]
            )
            # 玩家 parallel/pre 控制器的引用集按短键匹配, 只对玩家侧命名空间有意义: 替换实体(弹射物/载具)的
            # 同名键套上会误伤(末影龙娘玩家侧的表情 fire / 末影剑火焰与投射物动画同名), 与移植工具同口径不带
            wantOverride = (
                port._ShouldOverridePrevious(shortKey, additiveKeys, preKeys)
                if animId.startswith(playerPrefixes)
                else port._ShouldOverridePrevious(shortKey)
            )
            if wantOverride and body.get("override_previous_animation") is not True:
                body["override_previous_animation"] = True
                overrideFixes += 1
            elif not wantOverride and "override_previous_animation" in body:
                del body["override_previous_animation"]
                overrideFixes += 1
            # 主链/一次性通道成员的 loop 按 Java 运行语义(见 port.ApplyJavaLoopSemantics)
            if animId.startswith(playerPrefixes):
                registeredKey = animId.split(".", 2)[-1]  # 去 animation.<ns>. 两段
                if port.ApplyJavaLoopSemantics(registeredKey, body):
                    loopFixes += 1
            # 旧展开/替换式整串迁移(ground_speed 死区等), 通道与 timeline 文本一并
            stmtFixes += _MigrateExpressionText(body)
            # timeline 里的 Java 声明语句(v.x || v.x = 0;)基岩拒绝: 表达式内赋值
            timelineFixes += _RewriteTimelineStatements(body)
            # Java 每 tick 脚本动画(loop + 显式 0 长度 + timeline) → 0.05s 循环, timeline 才每 tick 触发
            # (无长度的动画 Java 是无限长、timeline 只跑一次, 由移植工具写成 JAVA_INFINITE_LENGTH,
            #  已落盘产物无法就地判断当初有没有长度, 不在此补)
            if port.ApplyTickTimelineLength(body):
                timelineFixes += 1
            # 超出 animation_length 的 timeline 条目按 Java 语义(循环/单次结尾补跑, hold 丢弃)
            timelineFixes += port.ClampTimelineToLength(body)
            # 引擎拒载的空节点(空 bones 让整份文件作废, 见 port.SanitizeAnimationBody)
            sanitizeFixes += port.SanitizeAnimationBody(body)
            # 纸娃娃没有实体: 玩家侧动画里依赖实体的查询包界面门控(见 port.GatePreviewEntityQueries 注)
            if animId.startswith(playerPrefixes):
                gateFixes += port.GatePreviewEntityQueriesInBody(body)
            # 移植期生成的粒子关键帧补 bind_to_actor:false(Java 粒子在世界坐标不随身)
            particleFixes += _BindTimelineParticles(body)
            # 最后一道: 基岩解析不了的表达式按 Java 口径处置(见 devtools/molang_syntax.py 注)
            guardHits += [(animId, hit) for hit in port.GuardAnimationMolang(body)]
        if not (
            vectorFixes
            or stmtFixes
            or overrideFixes
            or tailFixes
            or loopFixes
            or sanitizeFixes
            or timelineFixes
            or lerpFixes
            or gateFixes
            or particleFixes
            or guardHits
        ):
            continue
        port.DumpJson(path, data)
        notes = []
        if lerpFixes:
            notes.append(
                "表达式关键帧形态规范化 {} 处"
                "(catmullrom→linear: 样条要靠相邻帧值算切线; 仅 post 补 pre: "
                "缺 pre 的表达式帧是阶跃, 蓄力会跳变)".format(lerpFixes)
            )
        if stmtFixes:
            notes.append("通道表达式返回值规范化 {} 处".format(stmtFixes))
        if vectorFixes:
            notes.append("标量通道展开 {} 处".format(vectorFixes))
        if tailFixes:
            notes.append("补收尾保持帧 {} 处(封住末帧到 animation_length 的空档)".format(tailFixes))
        if overrideFixes:
            notes.append("override_previous_animation 标志 {} 处".format(overrideFixes))
        if loopFixes:
            notes.append("loop 按 Java 主链/一次性通道语义改写 {} 条".format(loopFixes))
        if timelineFixes:
            notes.append(
                "timeline 按 Java 语义规范化 {} 处(声明语句改写 / 每 tick 脚本长度 / "
                "超出长度的条目: 循环挪到下一圈 0.0 最前, 单次挪到结尾)".format(timelineFixes)
            )
        if sanitizeFixes:
            notes.append("清理引擎拒载的空节点 {} 处(空 bones 会作废整份文件)".format(sanitizeFixes))
        if gateFixes:
            notes.append(
                "纸娃娃无实体的查询包界面门控 {} 处(position / is_item_name_any 刷屏)".format(gateFixes)
            )
        if particleFixes:
            notes.append(
                "粒子关键帧补 bind_to_actor:false {} 处(Java 粒子在世界坐标不随身)".format(particleFixes)
            )
        if guardHits:
            notes.append(
                "基岩解析不了的表达式按 Java 口径处置 {} 处(整份文件拒载): {}".format(
                    len(guardHits),
                    "; ".join(
                        "{} {} {}".format(animId.split(".", 2)[-1], port.FormatSlotPath(path), problem)
                        for animId, (path, _text, problem) in guardHits[:4]
                    ),
                )
            )
        report.append("  动画 {}: {}".format(name, ", ".join(notes)))


def _WrapProjectileGeometries(packName, report):
    """已落盘投射物几何补朝向根骨骼(port.WrapProjectileGeometry, 幂等)"""
    manifestPath = port.PackManifestPath(packName)
    if not os.path.isfile(manifestPath):
        return
    manifest = port.LoadJson(manifestPath)
    wrapped = []
    for section, _entityIds, entry, modelSegment, _namespaceSegment in port.ReplacedTargets(
        manifest.get("files")
    ):
        if section != "projectiles":
            continue
        geoPath = os.path.join(RP, "models", "entity", packName, "{}.geo.json".format(modelSegment))
        if os.path.isfile(geoPath) and port.WrapProjectileGeometry(geoPath):
            wrapped.append(modelSegment)
    if wrapped:
        report.append(
            "  投射物几何外包朝向根骨骼 {}: {}(Java 模型前方 +X, 运行层在根上播朝向与缩放: Java 写死 0.7, 同玩家换算成 0.8)".format(
                port.PROJECTILE_ROOT_BONE, ", ".join(wrapped)
            )
        )


def _WrapVehicleGeometries(packName, report):
    """已落盘载具几何补缩放根骨骼(port.WrapVehicleGeometry, 幂等)。载具动画的音频关键帧要 Java 源音频, 只能重新移植"""
    manifestPath = port.PackManifestPath(packName)
    if not os.path.isfile(manifestPath):
        return
    manifest = port.LoadJson(manifestPath)
    wrapped = []
    for section, _entityIds, _entry, modelSegment, _namespaceSegment in port.ReplacedTargets(
        manifest.get("files")
    ):
        if section != "vehicles" or modelSegment in wrapped:
            continue
        geoPath = os.path.join(RP, "models", "entity", packName, "{}.geo.json".format(modelSegment))
        if os.path.isfile(geoPath) and port.WrapVehicleGeometry(geoPath):
            wrapped.append(modelSegment)
    if wrapped:
        report.append(
            "  载具几何外包缩放根骨骼 {}: {}(运行层在根上播缩放: Java 硬编码 0.7, 同玩家换算成 0.8)".format(
                port.VEHICLE_ROOT_BONE, ", ".join(wrapped)
            )
        )


def _WrapGlideRoot(packName, report):
    """已落盘玩家主几何补滑翔根骨骼(port.WrapGlideRootGeometry, 幂等)。

    主包在这根骨骼上播 animation.ysm.java_glide_fix, 抵消基岩滑翔时实体层的 (90+pitch) 原生旋转
    (Java 版渲染器不转, 姿态全在模型的 elytra_fly 动画里 —— 见 port.WrapGlideRootGeometry 注)。
    """
    geoPath = os.path.join(RP, "models", "entity", packName, "main.geo.json")
    if os.path.isfile(geoPath) and port.WrapGlideRootGeometry(geoPath):
        report.append(
            "  主几何外包滑翔根骨骼 {}(主包在它上面反向旋转, 还原 Java 的鞘翅姿态)".format(
                port.GLIDE_ROOT_BONE
            )
        )


def _RebuildFirstPersonArm(packName, report):
    """已落盘 arm 几何 → 原版手臂骨架包装(port.BuildFirstPersonArmGeometry, 幂等)。

    旧产物先摘掉嫁接进来的主几何父链(早先的 port.GraftArmParentChain 产物): 判据 = 手臂根骨骼的
    **祖先**里"在 arm 几何不带 cube、且在 main.geo.json 有同名骨骼且 pivot/rotation 逐值相同"的那些。
    只看祖先, 子树内的空骨骼(定位组/物品骨骼)一律不动。
    ⚠️ 手臂动画(fp_arm)里写在 RightArm/LeftArm 上的通道**这里改不了**(要改名表, 只有移植期
    从 Java 源才知道原名), 这类包请从 Java 源重新移植; 官方包的 fp_arm 只写子树内的装饰骨骼, 无影响。
    """
    geoPath = os.path.join(RP, "models", "entity", packName, "arm.geo.json")
    mainPath = os.path.join(RP, "models", "entity", packName, "main.geo.json")
    if not os.path.isfile(geoPath):
        return
    data = port.LoadJson(geoPath)
    geos = data.get("minecraft:geometry") or []
    bones = geos[0].get("bones") if geos and isinstance(geos[0], dict) else None
    if not isinstance(bones, list) or not bones:
        return
    byName = dict((bone.get("name"), bone) for bone in bones)
    mainBones = {}
    if os.path.isfile(mainPath):
        mainGeos = port.LoadJson(mainPath).get("minecraft:geometry") or []
        if mainGeos and isinstance(mainGeos[0], dict):
            mainBones = dict((bone.get("name"), bone) for bone in (mainGeos[0].get("bones") or []))
    ancestors = []
    for bone in bones:
        if str(bone.get("name", "")).lower() not in ("rightarm", "leftarm"):
            continue
        cursor = bone.get("parent")
        seen = set()
        while cursor in byName and cursor not in seen:
            seen.add(cursor)
            ancestors.append(cursor)
            cursor = byName[cursor].get("parent")
    grafted = []
    for name in ancestors:
        bone = byName[name]
        reference = mainBones.get(name)
        if bone.get("cubes") or reference is None:
            continue
        if bone.get("pivot") == reference.get("pivot") and bone.get("rotation") == reference.get("rotation"):
            grafted.append(name)
    if grafted:
        keep = [bone for bone in bones if bone.get("name") not in grafted]
        for bone in keep:
            if bone.get("parent") in grafted:
                bone.pop("parent", None)
        geos[0]["bones"] = keep
        port.DumpJson(geoPath, data)
        report.append(
            "  手臂几何摘掉嫁接的主几何父链 {} 根(第一人称用原版手臂骨架包装, 不再需要父链): {}".format(
                len(grafted), ", ".join(grafted[:6])
            )
        )
    renames, notes = port.BuildFirstPersonArmGeometry(geoPath)
    if notes:
        report.append("  手臂几何重建为原版手臂骨架包装(对齐 Java 的 ∓4/-4.8 纯平移映射):")
        report.extend(notes)
    if renames:
        report.append(
            "  [WARN] 手臂骨骼改名 {} —— 若该包 fp_arm 动画写过这些骨骼, "
            "请从 Java 源重新移植(修复工具拿不到原名表)".format(
                ", ".join("{}→{}".format(old, new) for old, new in sorted(renames.items()))
            )
        )


def _FixJavaStateDeclaration(packName, report):
    """主包运行层声明(ysm.json 顶层 java_state, 见 java_runtime_bindings.BuildDeclaration)按产物现状重算: 早先移植的
    包没有这个键, roaming 变量(Java v.roaming.*)就做不了存档与多人同步。探针表没有汇可查, 沿用 ysm.json 里已有的。幂等"""
    manifestPath = port.PackManifestPath(packName)
    if not manifestPath or not os.path.isfile(manifestPath):
        return
    manifest = port.LoadJson(manifestPath)
    declaration = runtime_bindings.BuildDeclaration(
        [os.path.join(RP, "animations", packName), os.path.join(RP, "animation_controllers", packName)],
        manifest,
    )
    if runtime_bindings.ApplyDeclaration(manifest, declaration):
        port.DumpJson(manifestPath, manifest)
        report.append(
            "  "
            + (
                runtime_bindings.DeclarationReportLine(declaration)
                or "java_state(顶层): 产物里已没有运行层落点, 已撤掉声明"
            )
        )


def FixPack(packName):
    report = ["== {}".format(packName)]
    # Java 逐通道覆盖的伴生动画/占用变量先整体撤掉, 让下面每一步看到与移植期相同的数据
    # (移植期它排在最后), 末尾再重新应用并与起始内容比对 —— 全流程幂等
    ownershipBefore = _OwnershipSnapshot(packName)
    port.UndoChannelOwnership(packName)
    _FixAnimationChannels(packName, report)
    knownKeys = _CollectPackAnimKeys(packName)
    if not knownKeys:
        report.append("  [WARN] RP 动画目录缺席或为空, 跳过(死引用剪枝需要动画全集)")
        return report
    referencedParallels = _FixControllers(packName, knownKeys, report)
    _FixManifest(packName, referencedParallels, report)
    _FixJavaStateDeclaration(packName, report)
    # 实体初始化读 ysm.json 的 initialize/config_forms, 必须在 _FixManifest
    # (roaming 扁平化)之后跑, 拿到的才是与动画侧同名的变量
    _FixEntityInitialize(packName, report)
    # 玩家纸娃娃的每实例变量初始化控制器(与预览实体同口径的变量默认值, 接口注册)
    _FixVariableInitController(packName, report)
    # 清掉废弃产物: 归零动画/控制器、GUI 预览副本
    for stale in (
        os.path.join(RP, "animations", packName, port.STATE_RESET_FILE),
        os.path.join(RP, "animations", packName, port.GUI_BASE_FILE),
        os.path.join(RP, "animation_controllers", packName, port.STATE_RESET_CONTROLLER_FILE),
    ):
        if os.path.isfile(stale):
            os.remove(stale)
            report.append("  清理废弃的归零产物: {}".format(os.path.basename(stale)))
    droppedPreRefs = port.DropPreControllerMainChainRefs(packName)
    if droppedPreRefs:
        report.append(
            "  pre 通道控制器摘掉对主链成员的冗余引用 {} 处(主链由状态机播, 同播叠成两倍): {}".format(
                len(droppedPreRefs), ", ".join("{}.{}:{}".format(*item) for item in droppedPreRefs[:6])
            )
        )
    # 主链与 pre 层内部的覆盖都由逐通道覆盖按状态让位(port.ApplyChannelOwnership 注末尾); 早先静态删掉的
    # pre 层通道(大酒狐爱心/ZZZ 的隐藏缩放、K 螺诺亚待机摆尾)补不回来 —— 这类包请从 Java 源重新移植
    loopedPreview = port.LoopPreviewAnimation(packName)
    if loopedPreview:
        report.append(
            "  GUI 展示动画按 Java 强制循环(CapPredicate playLoopAnimation): {}".format(
                ", ".join("{}(原 loop={})".format(key, loop) for key, loop in loopedPreview)
            )
        )
    foldedVariants, skippedVariants = port.ReconcileConditionalVariants(packName)
    if foldedVariants:
        report.append(
            "  条件变体折叠 {} 对(pre 静态显隐 + parallel 条件变体 → 单一所有者), 放弃非常量 {} 对".format(
                foldedVariants, skippedVariants
            )
        )
    for geoFile, action, heldItemBones in port.EnsureHeldItemBones(
        os.path.join(RP, "models", "entity", packName)
    ):
        if action == "added":
            report.append(
                "  手持物品定位骨骼[{}]: 补 {}(基岩靠 rightItem/leftItem 骨骼挂手持物; "
                "arm 几何缺了就是第一人称弓/弩/盾不显示或严重偏移)".format(
                    geoFile, ", ".join("{}→{}".format(bone, parent) for bone, parent in heldItemBones)
                )
            )
        else:
            report.append(
                "  手持物品定位骨骼[{}]: 旧生成物挪到新挂点 {}(主几何 pivot 放在定位骨骼上, "
                "对齐作者自制基岩版; 摆位由主包物品修正动画补)".format(
                    geoFile, ", ".join("{}→{}".format(bone, parent) for bone, parent in heldItemBones)
                )
            )
    hubBypasses = port.BypassEmptyHubStatesInPack(packName)
    if hubBypasses:
        report.append("  空中转状态旁路转移 {} 条(落地/切换不再经绑定姿态)".format(hubBypasses))
    # 作者状态里 Java PLAY_ONCE 动画的尾过渡淡出(见 port.FadeControllerOneShots 注): 排在补 hold 之前,
    # 之后 loop 已是 hold 认不出; 已移植产物里的淡出式本身就是标记, 这里只补新出现的
    fadedOneShots = port.FadeControllerOneShots(packName)
    if fadedOneShots:
        report.append(
            "  作者状态里的 PLAY_ONCE 动画补尾过渡淡出 {} 处: {}".format(
                len(fadedOneShots), ", ".join("{}.{}:{}".format(*item) for item in fadedOneShots[:8])
            )
        )
    heldOneShots = port.HoldControllerOneShots(packName)
    if heldOneShots:
        report.append(
            "  控制器状态的一次性动画补 hold_on_last_frame {} 条"
            "(基岩 loop:false 播完即撤 —— 出态前会漏一帧底层姿态; "
            "Java 的尾过渡由淡出权重复刻): {}".format(len(heldOneShots), ", ".join(heldOneShots))
        )
    # Java 逐通道覆盖(伴生动画 + 占用变量, 见 port.ApplyChannelOwnership 注)
    ownership, _written = port.ApplyChannelOwnership(packName)
    ownershipLines = port.OwnershipReportLines(ownership)
    if _OwnershipSnapshot(packName) == ownershipBefore:
        report.append("  逐通道覆盖伴生动画: 已是最新({} 条)".format(ownership.CompanionCount()))
    else:
        report.extend("  " + line for line in ownershipLines or ["逐通道覆盖伴生动画: 已清除"])
    # override 动画(含伴生)的恒等常量通道 → 微小值(见 port.EpsilonizeOverrideIdentities 注); 幂等
    epsilonized = port.EpsilonizeOverrideIdentities(packName)
    if epsilonized:
        report.append(
            "  override 动画的恒等常量通道换成微小值 {} 处(引擎把单位值通道当成不存在, "
            "清不掉前面的层)".format(epsilonized)
        )
    # 一次性通道(挥击/使用/受击/死亡)状态机: 直挂 animate 条目对不循环动画不重放; 排在逐通道覆盖
    # 之后 —— 挥击/使用成员拆出的伴生动画要跟成员进同一状态
    _FixOneShotControllers(packName, report, ownership=ownership)
    # 作者状态机里会与 Java 分叉的"动画播完"判据(见 port.RewriteFinishedQueries 注)
    finishedRewrites = port.RewriteFinishedQueries(packName, ownership=ownership)
    if finishedRewrites:
        report.append(
            '  "动画播完"判据按 Java 口径改写 {} 个状态(基岩权重 0 的条目暂停计时, 原生判据永不成立)'.format(
                finishedRewrites
            )
        )
    # Java 主链状态机: 状态动画切换的交叉淡化(直挂条目零过渡硬切)
    _FixStateChainController(packName, report, ownership=ownership)
    _WrapProjectileGeometries(packName, report)
    _WrapVehicleGeometries(packName, report)
    _WrapGlideRoot(packName, report)
    _RebuildFirstPersonArm(packName, report)
    # 收尾: 资源包按旧版 Molang 语义解析, 两种语义可能分叉处补括号(见 molang_syntax.ExplicitPrecedence 注)
    precedence = port.ExplicitPackPrecedence(packName)
    if precedence:
        report.append(
            "  运算符优先级显式化 {} 处(三元左结合 / && 与 || 同级的旧版 Molang 语义下与 Java 同值)".format(
                precedence
            )
        )
    return report


# 原版 player.entity.json 的 scripts.initialize 原文(网易 vanilla / vanilla_netease 同):
# 我方覆盖文件写了 initialize 键即按键替换原版列表, 必须把这四条原样带上
def _AllPackNames():
    """ysm_models 下全部 JSON 包名: 平铺包 + 合集文件夹(ysm_models/<合集>/<包>/ysm.json)成员"""
    names = []
    for name in sorted(os.listdir(BP_MODELS)):
        if os.path.isfile(os.path.join(BP_MODELS, name, "ysm.json")):
            names.append(name)
        elif os.path.isdir(os.path.join(BP_MODELS, name)):
            names.extend(
                child
                for child in sorted(os.listdir(os.path.join(BP_MODELS, name)))
                if os.path.isfile(os.path.join(BP_MODELS, name, child, "ysm.json"))
            )
    return names


def main():
    packs = sys.argv[1:]
    if not packs:
        allPacks = _AllPackNames()
        packs = [name for name in allPacks if os.path.isdir(os.path.join(RP, "animation_controllers", name))]
        # 无控制器目录的包也可能需要 ysm.json 扁平化 —— 单独补一轮
        flatOnly = [name for name in allPacks if name not in packs]
    else:
        flatOnly = []
    for packName in packs:
        for line in FixPack(packName):
            print(line.encode("utf-8") if isinstance(line, unicode) else line)  # noqa: F821
    for packName in flatOnly:
        report = ["== {}(仅 ysm.json 体检)".format(packName)]
        _FixManifest(packName, [], report)
        for line in report:
            print(line.encode("utf-8") if isinstance(line, unicode) else line)  # noqa: F821
    print("[DONE] 就地修复完成 —— 资源包改动需重启游戏生效")


if __name__ == "__main__":
    main()
