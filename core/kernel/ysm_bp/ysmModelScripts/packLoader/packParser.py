# -*- coding: utf-8 -*-
"""ysm.json 模型包解析器 —— Java 版 spec2 原生格式优先, netease 段仅作可选覆盖。

目标: Java 版模型包的 ysm.json **原样拷贝**即可用, 创作者只需完成资源侧的机械转换:
1. 动画文件内的键加前缀:  idle → animation.<包名>.idle  (基岩动画为全局命名, 必须唯一)
2. 贴图放置:              textures/entity/<包名>/<原文件名>.png
3. 几何体命名:            geometry.<包名>
4. 预览实体定义:          resource_pack/entity/<包名>.entity.json (identifier = ysm_pack:<包名>)

推导规则(netease 段同名字段可逐项覆盖):
- model_id  = "ysm_pack:" + 包名
- geometry  = "geometry." + 包名
- 动画命名空间 = "animation." + 包名
- 皮肤序列  = files.player.texture 列表(str 或 {uv} 均可), 皮肤名=文件名去扩展,
  properties.default_texture 指定者(缺省第一项)作为网易 "default" 皮肤
- 网易↔Java 动画键名差异由 NETEASE_TO_JAVA_ANIM_KEYS 自动映射(use_righthand→use_mainhand 等)
- properties.preview_animation(字符串) → 网易 gui_animation(GUI 展示动画)

纯数据变换, 无引擎依赖, 可离线单测。字段参考: .ref/ysm-java-wiki(项目结构篇)。
"""
import json
import re
from collections import OrderedDict

from ..data.baseAnimations import (
    BASE_ANIMATE,
    BASE_ANIMATIONS,
    BASE_ANIMATION_CONTROLLERS,
    BASE_RENDER_CONTROLLERS,
)
from ..data.molangBase import BASE_INITIALIZE
from ..data.skinBuilder import BuildEntries

# molang 变量引用(v.xxx / variable.xxx); \b 防 uv.xxx 等误匹配
_MOLANG_VAR_PATTERN = re.compile(r'\b(?:variable|v)\.([A-Za-z_][A-Za-z0-9_]*)')


def CollectInitializedVariableNames(expressions):
    """初始化表达式列表 → 被赋值变量全名集(variable.xxx, v. 短前缀归一)。

    modelInstaller 装载时用它取共享 initialize 列表的"在场变量名",
    过滤文件扫描来源的自动初始化条目(见 InstallPacks)。
    """
    names = set()
    for expr in expressions or []:
        if not isinstance(expr, str) or "=" not in expr:
            continue
        matched = _MOLANG_VAR_PATTERN.search(expr.split("=", 1)[0])
        if matched:
            names.add("variable." + matched.group(1))
    return names


_BASE_VARIABLE_NAMES = CollectInitializedVariableNames(BASE_INITIALIZE)

NULL_ICON = "textures/ui/ysm_default_icon"
DEFAULT_MODEL_NAMESPACE = "ysm_pack"

# 轮盘动画默认停止条件(系统维护, 对齐 Java 版"配置零感知"):
# 玩家移动/跳跃/潜行时自动停掉轮盘动画。netease.extra_stop_expression 可覆盖, 置空关闭。
DEFAULT_EXTRA_STOP_EXPRESSION = 'query.vertical_speed>0.3||query.ground_speed>0.3||q.is_sneaking'

# Java ↔ 基岩实体 ID 差异映射(files.projectiles/vehicles 用), 未列出的原样沿用
JAVA_TO_BEDROCK_ENTITY_IDS = {
    "minecraft:trident": "minecraft:thrown_trident",
    "minecraft:fishing_bobber": "minecraft:fishing_hook",
}

# ---- 条件动画(Java ConditionManager 家族) → 基岩原生 molang 条件 ----
# Java 把条件编码进动画名(hold_mainhand$minecraft:diamond_sword), 逐 tick 由
# python/java 侧匹配后播放; 基岩有等价的物品查询 query, 故直接合成 animate 条件
# 表达式交给引擎求值 —— 零运行时开销, 语义与 Java 一致。
# 每项: 动画名前缀 → (装备槽位, 手序号(空手判定用, 非手部为 None), 额外门控条件)
# "正在使用物品"的判据: **不能用 query.is_using_item** —— 网易 3.8.0 的原版资源包
# (vanilla + vanilla_netease)对它零引用, 实测(2026-09-05 凋灵娘)整族 use_* 条件动画
# 一条都不播: 拉弓时身体没有侧身瞄准姿态, 只剩 hold_mainhand:bow 的持弓姿, 弓指向左侧。
# 换成原版自己在用的两个量(player.entity.json pre_animation / attachable 动画):
#   query.main_hand_item_use_duration —— 主手使用中从 max 递减, 未使用为 0;
#   query.blocking —— 举盾格挡, 补上主手计时器不动的格挡情形。
# **query.blocking 必须叠"该手持盾"**(2026-09-16 实机逐帧采样): 网易引擎潜行 0.1 秒后就把
# query.blocking 置 1, 与手里拿什么无关(主手下界合金剑、无盾照样为 1)。早先的
# `(use_duration>0||blocking)` 因此把潜行当成"主手在使用物品", ysm_use_mainhand 走进裸兜底
# use_mainhand —— 用户看到"按 Shift 潜行触发一次挥手"。Java isUsingItem() 潜行时为假。
# 原版 player.entity.json 的 shield_block_main_hand / shield_block_off_hand 同样额外要求持盾。
# Java UsePredicate 按 entity.getUsedItemHand() 二选一只播一条, 基岩这两个量同样不分手;
# 基岩副手槽只收盾/图腾/地图, **唯一会被"使用"的副手物品是盾**, 故用原版
# shield.entity.json pre_animation 的同一套判据分手: 正在格挡且副手是盾 = 副手在用
# (原版那段还给副手盾优先权), 主手格挡要求主手是盾, 其余使用计时归主手。
# **Java 使用物品期间攻击键无效**(Minecraft.handleKeybinds: isUsingItem 时只处理松开使用键,
# 攻击点击被吞掉 —— 不挥手、不打断使用); 基岩照常出手。实机逐 tick 采样(副手盾 + 潜行格挡,
# 调用 Swing): 出手时 `query.blocking` 掉成 0 并持续 2~4 tick, 期间 `query.is_sneaking` 保持 1;
# 掉线**可能比挥动进度起跳早 2 tick**(2026-09-17: t=21 掉线, t=23 attack_time 才离开 0)。
# 坚守者娘的格挡状态据此退出、进持剑攻击态再回来 = 用户看到的"举盾时按左键, 悬浮拳重置再恢复"。
# 复刻: 主包共享动画 `animation.ysm.java_input_state`(_JAVA_INPUT_STATE_KEY)逐帧维护锁存 ——
#   variable.ysm_block_hold: 持盾格挡。基岩举盾靠潜行, 所以格挡掉线时只要**还在潜行、手里还有盾**,
#     并且刚出过手(0.1s 内)或掉线不足 0.2s, 就沿用上一帧的值。松开潜行立即失效 —— 紧接着的
#     出手是真挥动(早先的"掉线后 0.1s 宽限"既桥不住早 2 tick 的掉线, 又把松盾后立刻出手误判成
#     使用中); 盾被斧头打掉时 0.2s 后失效。持盾判定在骨骼通道里只能用 get_equipped_item_name;
#   variable.ysm_use_hold: 主手正在使用后保持 0.1s, 刚出过手时沿用上一帧的值。"正在使用"读
#     variable.ysm_item_in_use —— 由共享控制器 controller.animation.ysm.java_use_state(_JAVA_USE_STATE_KEY,
#     排在 animate 表最前)在**转移**里判 query.main_hand_item_use_duration、进入状态时写入。
#     **骨骼通道里不能查物品使用**(2026-09-17 实机): 锁存曾加读 item_remaining_use_duration('main_hand',
#     1.0), 持剑、没在使用时它在骨骼通道里恒大于 0(EvalMolangExpression 求值却是 0, 日志无报错) ——
#     use_hold 恒 1, 每次挥动都被静音(凋灵娘持剑攻击全不播), 使用状态机也一直停在弓的成员态(换物品后
#     手部定位骨骼仍是拉弓的 2 倍); 更早的拉弓排查也表明骨骼通道读 main_hand_item_use_duration 不可靠;
#   variable.ysm_swing_muted: 挥动起始时正处于使用中 → 整个这次挥动静音(Java 根本没有这次挥动);
#   variable.ysm_swing_serial: 未静音的挥动每开始一次 +1。起始 = attack_time 从 0 起跳或落差 >0.05:
#     引擎 attack_time 与 Java getAttackAnim 同为回卷插值(实测有 5/6 以上的值, 结尾升到 1.0 再归零),
#     一次挥动内单调上升, 挥动中再挥是"升到 1 后落回低位"; 挥动前半程(swingTime<3)再挥引擎不重启。
# 格挡的读取带 ?? 回落到原始 query, 共享动画缺席时退化为旧行为。
# **使用计时必须与原始 query 取或**, 不能只读锁存: 锁存变量一旦定义, `??` 就不再回落, 锁存出错时
# 门控跟着错(2026-09-17 用户对照 Java 截图: 坚守者娘拉弓行走没有侧身)。控制器转移里的原始 query 是
# 2026-09-05 实机验证过的口径, 锁存只负责出手瞬间的桥接与挥动静音。
_BLOCKING_SIGNAL = "(variable.ysm_block_hold??query.blocking)"
_USE_DURATION_SIGNAL = "((variable.ysm_use_hold??0)>0||query.main_hand_item_use_duration>0)"
# Java ysm.attack_time / swinging / ctrl.swing 的基岩对应: 静音的挥动读作 0
_JAVA_ATTACK_TIME = "(variable.attack_time*(1-(variable.ysm_swing_muted??0)))"
_SWING_ACTIVE_TEST = _JAVA_ATTACK_TIME + ">0.0"
_OFFHAND_IN_USE_TEST = ("(" + _BLOCKING_SIGNAL + "&&query.is_item_name_any("
                        "'slot.weapon.offhand','minecraft:shield'))")
_MAINHAND_BLOCKING_TEST = ("(" + _BLOCKING_SIGNAL + "&&query.is_item_name_any("
                           "'slot.weapon.mainhand','minecraft:shield'))")
_ITEM_IN_USE_TEST = ("(" + _USE_DURATION_SIGNAL + "||" + _MAINHAND_BLOCKING_TEST
                     + "||" + _OFFHAND_IN_USE_TEST + ")")
_USE_MAINHAND_GATE = ("(" + _USE_DURATION_SIGNAL + "||" + _MAINHAND_BLOCKING_TEST
                      + ")&&!" + _OFFHAND_IN_USE_TEST)
_USE_OFFHAND_GATE = _OFFHAND_IN_USE_TEST
# 历代烧进产物的门控原文(**冻结字面量**, 修复工具按整串迁移到上面的现行门控):
# v1 = 2026-09-16 之前(裸 blocking, 潜行即"使用中"); v2 = 2026-09-16 白天(blocking 配持盾, 无锁存)
_V1_OFFHAND_IN_USE_TEST = "(query.blocking&&query.is_item_name_any('slot.weapon.offhand','minecraft:shield'))"
_V1_ITEM_IN_USE_TEST = "(query.main_hand_item_use_duration>0||query.blocking)"
_V1_USE_MAINHAND_GATE = _V1_ITEM_IN_USE_TEST + "&&!" + _V1_OFFHAND_IN_USE_TEST
_V1_USE_OFFHAND_GATE = _V1_ITEM_IN_USE_TEST + "&&" + _V1_OFFHAND_IN_USE_TEST
_V2_MAINHAND_BLOCKING_TEST = "(query.blocking&&query.is_item_name_any('slot.weapon.mainhand','minecraft:shield'))"
_V2_ITEM_IN_USE_TEST = ("(query.main_hand_item_use_duration>0||" + _V2_MAINHAND_BLOCKING_TEST
                        + "||" + _V1_OFFHAND_IN_USE_TEST + ")")
_V2_USE_MAINHAND_GATE = ("(query.main_hand_item_use_duration>0||" + _V2_MAINHAND_BLOCKING_TEST
                         + ")&&!" + _V1_OFFHAND_IN_USE_TEST)
_V2_USE_OFFHAND_GATE = _V1_OFFHAND_IN_USE_TEST
# v3 = 2026-09-17 凌晨(使用计时只读锁存, 锁存缺席才回落原始 query): 子串整体替换即可迁移
_V3_USE_DURATION_SIGNAL = "(variable.ysm_use_hold??(query.main_hand_item_use_duration>0))"
# 兼容旧名(修复工具与测试早先按这组名字导入)
_LEGACY_ITEM_IN_USE_TEST = _V1_ITEM_IN_USE_TEST
_LEGACY_USE_MAINHAND_GATE = _V1_USE_MAINHAND_GATE
_LEGACY_USE_OFFHAND_GATE = _V1_USE_OFFHAND_GATE

_CONDITION_PREFIXES = [
    # 长前缀必须排在短前缀之前(swing_offhand 先于 swing)
    ("hold_mainhand", "slot.weapon.mainhand", 0, None),
    ("hold_offhand", "slot.weapon.offhand", 1, None),
    ("swing_offhand", "slot.weapon.offhand", 1, _SWING_ACTIVE_TEST),
    ("swing", "slot.weapon.mainhand", 0, _SWING_ACTIVE_TEST),
    ("use_mainhand", "slot.weapon.mainhand", 0, _USE_MAINHAND_GATE),
    ("use_offhand", "slot.weapon.offhand", 1, _USE_OFFHAND_GATE),
    ("head", "slot.armor.head", None, None),
    ("chest", "slot.armor.chest", None, None),
    ("legs", "slot.armor.legs", None, None),
    ("feet", "slot.armor.feet", None, None),
]

# Java 通道注册顺序(PlayerControllerCollection.init)即应用顺序, 同骨骼**后者覆盖前者**:
#   pre_parallel → vehicle → main → hold_offhand → hold_mainhand → swing → use
#   → passenger → carry_on → cap → parallel → armor
# 同一通道内 Java 只播一条(条件互斥), 故只有**通道之间**的先后需要复刻 —— animate 表
# 按此排序, 通道内顺序无关。缺席者(自定义前缀)排在已知通道之后, 保持原有相对次序。
_CONDITION_CHANNEL_ORDER = {
    "hold_offhand": 30,
    "hold_mainhand": 31,
    "swing_offhand": 40,
    "swing": 41,
    "use_offhand": 50,
    "use_mainhand": 51,
    "head": 90, "chest": 90, "legs": 90, "feet": 90,   # 甲槽另由排序函数摘到末位
}

# 基岩没有副手挥击信号(variable.attack_time 是主手挥击) —— swing_offhand 族只能挂在
# 主手挥击上, 于是"主手攻击时副手动画乱播"。Java 里它由 entity.swingingArm==OFF_HAND
# 驱动(副手用物品时那条手臂挥), 基岩无等价量, 宁缺勿错: 整族不合成, 解析时告警一次。
_UNSUPPORTED_CONDITION_PREFIXES = ("swing_offhand",)

# Java InnerClassify/UseAnim 的物品分类(动画名 "前缀:分类") → 基岩判定。
# ("tag", 名) 走 equipped_item_any_tag; ("name", 名...) 走 is_item_name_any。
_CLASSIFY_TESTS = {
    "sword": ("tag", "minecraft:is_sword"),
    "axe": ("tag", "minecraft:is_axe"),
    "pickaxe": ("tag", "minecraft:is_pickaxe"),
    "shovel": ("tag", "minecraft:is_shovel"),
    "hoe": ("tag", "minecraft:is_hoe"),
    "eat": ("tag", "minecraft:is_food"),
    "shield": ("name", "minecraft:shield"),
    "block": ("name", "minecraft:shield"),
    "bow": ("name", "minecraft:bow"),
    "crossbow": ("name", "minecraft:crossbow"),
    "fishing_rod": ("name", "minecraft:fishing_rod"),
    "spear": ("name", "minecraft:trident"),
    "spyglass": ("name", "minecraft:spyglass"),
    "toot_horn": ("name", "minecraft:goat_horn"),
    "brush": ("name", "minecraft:brush"),
    "throwable_potion": ("name", "minecraft:splash_potion", "minecraft:lingering_potion"),
    "drink": ("name", "minecraft:potion", "minecraft:milk_bucket", "minecraft:honey_bottle"),
    "fishing": ("name", "minecraft:fishing_rod"),
    "charged_crossbow": ("charged", "minecraft:crossbow"),
}

# Java 的分类判定**顺序**(低优先级要给高优先级让位):
# ① 代码级(MainhandPredicate/OffhandPredicate 在整条条件链之前直接判, 故连 $id/#tag
#    都压得住): 装填好的弩 hold_*:charged_crossbow、抛竿中 hold_mainhand:fishing;
# ② InnerClassify 链(逐项 return, 一个物品只归一类);
# ③ UseAnim 兜底(InnerClassify 未命中才看 item.getUseAnimation())。
# Java 侧"一个物品只归一类"天然互斥; 基岩判据是**近似**, 可能同时命中(装填好的弩同时
# 满足 item_is_charged 与 name==crossbow), 故低优先级分类显式否定判定域重叠的高优先级项。
# drink 排在 eat 之前是刻意的: eat 走 is_food tag(宽), drink 走具名表(准), 蜜/奶这类
# 两边都可能算进去时按 Java 语义(getUseAnimation()==DRINK)归 drink。
_CLASSIFY_PRIORITY = (
    "charged_crossbow", "fishing",
    "slashblade", "sword", "gohei", "axe", "pickaxe", "shovel", "hoe",
    "shield", "crossbow", "bow", "fishing_rod", "spear", "throwable_potion",
    "drink", "eat", "block", "spyglass", "toot_horn", "brush",
)
# 只有 hold 通道有代码级判定(Java 的特判写在 Mainhand/OffhandPredicate 里);
# swing/use 通道这两个名字在 Java 走不到(既非 InnerClassify 分类也非 UseAnim),
# 本实现让它们退回普通分类照常合成 —— 见 docs/ysm-java-animation-mechanism.md。
_CODE_LEVEL_CLASSIFIES = ("charged_crossbow", "fishing")

# 基岩内置物品 tag 的成员表无法离线枚举, 只登记会与具名判据打架的那几个,
# 供分类互斥判定用(登记了也只是多一条 && 否定, 判错方向也不会漏播)。
_TAG_NAME_MEMBERS = {
    "minecraft:is_food": ("minecraft:potion", "minecraft:milk_bucket",
                          "minecraft:honey_bottle"),
}

# Java 原版物品 tag → 基岩内置 tag。**两边的原版 tag 命名根本不同**(Java 复数
# minecraft:pickaxes / 基岩单数 minecraft:is_pickaxe), Java 包的 #tag 条件原样落到
# 基岩就是恒假 —— 动画名条件(hold_mainhand#minecraft:pickaxes)与控制器里的
# ctrl.hold('mainhand','#minecraft:pickaxes') 两条路都中招。
# 实测(2026-09-05 warden): 悬浮臂状态机 player.parallel_1/2 的全部转移都吃这几个
# tag, 恒假 → 状态机永远停在"缓冲"态, 持镐/斧/锹/锄的拳击姿态与持盾防御姿态一条都
# 不播(Java 版正常)。取值与 _CLASSIFY_TESTS 的 :分类 判定同源, 保证同一把剑无论
# 写 `:sword` 还是 `#minecraft:swords` 都命中同一个基岩 tag。
# 非 minecraft: 命名空间(模组/包自定义 tag)原样透传: 基岩侧可由 addon 自行定义同名
# tag, 我们无从判断, 换掉反而错。
_JAVA_TO_BEDROCK_ITEM_TAGS = {
    "minecraft:swords": "minecraft:is_sword",
    "minecraft:axes": "minecraft:is_axe",
    "minecraft:pickaxes": "minecraft:is_pickaxe",
    "minecraft:shovels": "minecraft:is_shovel",
    "minecraft:hoes": "minecraft:is_hoe",
    "minecraft:tools": "minecraft:is_tool",
}


# 曾经在这里给工具类 tag 补过 `|| query.is_item_name_any(六档材质ID)` 的"保险丝"
# (担心基岩 tag 覆盖稀疏), **已撤销**, 两条理由:
# ① 游戏内实测(2026-09-05)基岩工具 tag 本来就好用:
#    query.equipped_item_any_tag(mainhand,'minecraft:is_sword') 手持下界合金剑返回 1.0,
#    而 Java 名 'minecraft:pickaxes' 返回 0.0 —— 需要修的只是命名映射;
# ② 具名兜底会把 query.is_item_name_any 带进**动画通道**表达式, 而那里没有实体上下文:
#    引擎实测报 "query.is_item_name_any called without a specified entity", 整条通道作废
#    (ref_wither pre_parallel1 的 HandSickle 缩放因此失效)。animate 播放条件里可以用,
#    动画文件的骨骼通道里不行 —— 两处求值上下文不同, 别混。
def MapJavaItemTag(tag):
    """Java 原版物品 tag → 基岩内置 tag; 未登记者原样返回(模组 tag / 已是基岩名)。"""
    return _JAVA_TO_BEDROCK_ITEM_TAGS.get(tag, tag)


def IsUnmappedJavaItemTag(tag):
    """是 minecraft: 命名空间但没有基岩对应名 —— 判定恒假, 值得告警。"""
    return tag.startswith("minecraft:") and tag not in _JAVA_TO_BEDROCK_ITEM_TAGS \
        and not tag.startswith("minecraft:is_")


def _ClassifyRank(classify):
    """分类在 Java 判定顺序里的位次; 未登记者排在末尾(保持原相对次序)"""
    if classify in _CLASSIFY_PRIORITY:
        return _CLASSIFY_PRIORITY.index(classify)
    return len(_CLASSIFY_PRIORITY)


def _ClassifyTestsOverlap(testA, testB):
    """两个分类的基岩判定域是否可能同时命中(需要互斥否定时返回 True)"""
    kindA, kindB = testA[0], testB[0]
    if "charged" in (kindA, kindB):
        charged, other = (testA, testB) if kindA == "charged" else (testB, testA)
        if other[0] == "charged":
            return charged[1] == other[1]
        return other[0] == "name" and charged[1] in other[1:]
    if kindA == "name" and kindB == "name":
        return bool(set(testA[1:]) & set(testB[1:]))
    if kindA == "tag" and kindB == "tag":
        return testA[1] == testB[1]
    nameTest, tagTest = (testA, testB) if kindA == "name" else (testB, testA)
    return bool(set(nameTest[1:]) & set(_TAG_NAME_MEMBERS.get(tagTest[1], ())))

# 实体类条件(骑乘): Java vehicle$<实体ID> → 基岩按类型判定骑乘
_VEHICLE_CONDITION_PREFIX = "vehicle"

# CarryOn 搬运条件动画(Java carryon:block/entity/player/princess): 主包下发的
# query.mod.ysm_carryon 取值 1=实体 2=方块 3=玩家(与旧基线 controller.animation.ysm.
# carryon 同一取值表), princess=被玩家抱起=骑乘玩家(主包 riding 值表 5)。
# 旧通道由基线 carryon_ctl 驱动 carryon_block 等改名键; Java 模式零旧版控制器,
# 移植产物的转义键(carryon.cls.*)必须由这里合成播放条件, 否则四条搬运动画全哑。
_CARRYON_CONDITIONS = OrderedDict([
    ("entity", "query.mod.ysm_carryon==1.0"),
    ("block", "query.mod.ysm_carryon==2.0"),
    ("player", "query.mod.ysm_carryon==3.0"),
    ("princess", "query.mod.ysm_riding==5"),
])

# 合成的游戏内状态条件统一**前置**纸娃娃门: 网易 UI 纸娃娃是无装备上下文的
# Custom 实体, 物品/骑乘/使用类 query 在其上求值会持续刷
# "Error: query.is_item_name_any called without a specified entity"(实机日志)。
# 放最前让 && 短路, 实体类 query 在纸娃娃上根本不求值。GUI 预览动画不受影响
# (它们不经这条合成链路)。
_NON_GUI_GUARD = "!variable.is_paperdoll"

# 第一人称门: 主域自动播放条目统一再加 !v.is_first_person —— 基岩 FP 手臂与主
# 模型同实体、同骨骼名(arm 几何嫁接主模型父链), 主域动画在第一人称继续求值会
# 驱动手臂几何乱摆(Java 的 FP 是独立渲染域, 无此问题, 与 Java 版的行为差异点)。
# fp_ 通道条目自带正向 FP 门(_FP_ARM_GATE), 不叠加此门。
_THIRD_PERSON_GUARD = "!variable.is_first_person"

# 模型自有动画控制器的 animate 门(替代裸 "1"): Java geckolib 控制器加载即常开,
# 但其状态转移普遍含物品/骑乘 query —— 纸娃娃上求值刷错(同上), 第一人称域会
# 驱动手臂 —— 与条件动画同一双门。替换实体(弹射物/载具)的控制器不经此门
# (独立实体, 无纸娃娃/第一人称语境, 且这两个 variable 在其上未初始化)。
_CONTROLLER_ANIMATE_GATE = _NON_GUI_GUARD + "&&" + _THIRD_PERSON_GUARD

# 每实例变量初始化控制器的保留注册键(移植/修复工具生成
# controller.animation.<包>.ysm_variable_init, 见 devtools/port_java_pack.py
# BuildVariableInitController): 玩家的纸娃娃(原版背包/设置界面)是独立渲染实例,
# 有自己的 molang 作用域 —— 主包对世界实体做的 SetPlayerVariable 到不了它, 包变量
# 未定义按 0 求值(实机 2026-09: warden Root 缩放归零整模消失 / wither 没眼睛 /
# sahmet 换装件全亮并逐通道刷 unknown variable)。该控制器每个渲染实例创建时各跑一次
# on_entry(`v.x = v.x ?? 默认值;`, 世界实体已有值原样保留)。条件恒 "1": 必须在所有
# 渲染域生效, 不经双门。资源索引发现即自动注册, 不需要 ysm.json 声明。
_VARIABLE_INIT_KEY = "ysm_variable_init"
_VARIABLE_INIT_CONDITION = "1"

# ---- 一次性(Java PLAY_ONCE)动画通道 → 生成式动画控制器 ----
# 基岩直挂 animate 条目对**不循环的定时动画**不做重放: 条件 false→true 只是再次
# 应用, 动画时钟不回零 —— 实机(2026-09): 第三人称挥手只播第一次, 之后挖方块/攻击
# 再不播。Java SwingPredicate 每次挥击 indicateReload 重置时钟; 旧版网易通道靠 fight
# 状态机的 any/all_animations_finished 转移天然重放。基岩里唯一能重置动画时钟的是
# **进入控制器状态**, 故移植/修复工具按包生成 controller.animation.<包>.ysm_swing /
# ysm_fp_swing / ysm_attacked / ysm_death(BuildOneShotControllers): idle -(触发 &&
# 物品判据)-> 每成员一个独占状态(播完 all_animations_finished 才走 —— Java "挥动不
# 中断直至播完") -> cooldown -(触发消失)-> idle, 每次触发都重新进状态 = 重放。
# 解析器在资源索引发现即**替换**对应直挂条目(位置不变, 双门不变); 没有该文件的
# 旧产物维持直挂(只播一次)。
_ONESHOT_SWING_KEY = "ysm_swing"
_ONESHOT_FP_SWING_KEY = "ysm_fp_swing"
_ONESHOT_SWING_TRIGGER = _SWING_ACTIVE_TEST
# 挥击通道的"新一次挥动"判据(2026-09-16 起): Java SwingPredicate 在每次挥动开始
# (swingTime==0)都 indicateReload —— **挥动中再挥会打断并重播**; 基岩实测同样如此:
# 中途再挥时 variable.attack_time 回落到 ≈0 重新上升。旧状态机"播完 → cooldown → 触发
# 消失才回 idle"会吞掉中途的再挥。现按共享动画维护的挥动序号 variable.ysm_swing_serial
# 判定: 序号 ≠ 本状态机记下的序号即有新挥动; 成员状态成对(<键> / <键>__re), 新挥动在两者
# 之间来回切 = 重新进入状态 = 动画从头播。第一/第三人称各记各的已见序号。
_SWING_SERIAL_VARIABLE = "variable.ysm_swing_serial"
_SWING_SEEN_VARIABLES = {_ONESHOT_SWING_KEY: "variable.ysm_swing_seen",
                         _ONESHOT_FP_SWING_KEY: "variable.ysm_fp_swing_seen"}
_SWING_RETRIGGER_SUFFIX = "__re"
# 逐帧输入状态共享动画(见 _BLOCKING_SIGNAL 上方长注): 主包资源包 animations/java_mode/
# input_state.animation.json, 挂在 Head 旋转通道上做副作用求值, 通道返回 0 不改姿态。
# 条件恒 "1": 第一人称的挥击状态机同样依赖挥动序号。同一语句开头还维护腾空闩锁
# variable.ysm_airborne(移植工具 ctrl.jump/idle/walk/run 的"在地"判据, 见 port_java_pack
# _AIRBORNE_LATCH): 2026-09-17 前它挂在 animate 表末尾的 java_head_look 上, 作者控制器读到的是
# 上一帧的值, 起跳/落地总比主链状态机(直接读 query)晚一帧切换; 排在表首后同帧切换。
_JAVA_INPUT_STATE_KEY = "ysm_java_input_state"
_JAVA_INPUT_STATE_ANIMATION = "animation.ysm.java_input_state"
_JAVA_INPUT_STATE_CONDITION = "1"
# 使用状态共享控制器(见 _BLOCKING_SIGNAL 上方长注 ysm_use_hold 条): 主包资源包 animation_controllers/
# java_mode/use_state.animation_controllers.json。idle/using 两态, 转移判 query.main_hand_item_use_duration
# (控制器转移上下文, 实机可靠), on_entry 写 variable.ysm_item_in_use。排在输入状态动画之前: 同一帧里
# 锁存语句读到的就是本帧的使用状态。与输入状态动画同条件恒 "1"、同时挂上。
_JAVA_USE_STATE_KEY = "ysm_java_use_state"
_JAVA_USE_STATE_CONTROLLER = "controller.animation.ysm.java_use_state"
_JAVA_USE_STATE_CONDITION = "1"
# 鞘翅滑翔: 抵消引擎实体层的原生俯仰旋转(2026-09-18 实机定量)。基岩在 query.is_gliding 时给
# **整个模型**额外转 (90 + 玩家 pitch) 度(相机锁死在侧面扫 9 个俯仰角、逐张量模型主轴:
# -60°→-31、-45°→-46、0°→-89、+45°→+42、+60°→+31; 与 Java 原版 PlayerRenderer.setupRotations 的
# XP.rotationDegrees(-90 - xRot) 同式), 且**不进骨骼矩阵** —— GetQueryableBoneOrientation 全程不变,
# 它在实体层(ActorRenderData::getDamageOrGlidingXYRotation)。而 Java 版 YSM 的渲染器继承
# LivingEntityRenderer 而非 PlayerRenderer(还显式清掉死亡翻转与激流旋转): 实体层一点不转, 鞘翅姿态
# **全写在模型自己的 elytra_fly 动画里**(12_little 把 Root 转 90 让身体躺平、17_mini 把 Root 转 pitch)。
# 两者叠加 = 水平飞行多转 90°(用户反馈"头朝下")。故移植/修复工具给主几何外包一层 ysm_glide_root
# (port_java_pack.WrapGlideRootGeometry), 主包在它上面播反向旋转还原 Java 语义; 没有该骨骼的旧产物
# 就是空转(动画找不到骨骼 = 不生效), 不会更坏。
# 渐入: Java 的系数 f = clamp(fallFlyingTicks²/100, 0, 1)(10 tick = 0.5s 到满), 基岩同源 —— 动画时钟
# 只有"进入控制器状态"才回零(直挂条目不回零, 见 _ONESHOT_SWING_KEY 上方注), 故挂共享控制器。
# ⚠️ 引擎是否在旋转之外还带平移(CSM 的 animation.csm.elytra_fix 给 Root 写了 position [0,-24,0])未实测,
# 游戏崩在测量前 —— 重启后要看的第一件事: 抬头 90°(此时旋转角为 0)下开/关滑翔, 模型是否整体上移 1.5 格。
_JAVA_GLIDE_FIX_KEY = "ysm_java_glide_fix"
_JAVA_GLIDE_FIX_ANIMATION = "animation.ysm.java_glide_fix"
_JAVA_GLIDE_STATE_KEY = "ysm_java_glide_state"
_JAVA_GLIDE_STATE_CONTROLLER = "controller.animation.ysm.java_glide_state"
# 输入状态动画逐帧维护的变量, 加上挥击状态机 on_entry 记下的已见序号: 不参与"文件扫描变量补 0"
# 初始化(与占用变量同理)。读取处都带 ?? 回落; 补 0 会让回落失效, 且移植工具只扫作者文件、
# 修复工具连生成的挥击状态机一起扫, 两条路径的初始化表对不上
_INPUT_STATE_VARIABLE_PATTERN = re.compile(
    r"^ysm_(?:input_attack|attack_prev|attack_last|attack_recent|shield_held|block_last|block_hold"
    r"|item_in_use|use_last|use_hold|swing_edge|swing_muted|swing_serial|(?:fp_)?swing_seen)$", re.IGNORECASE)
# 状态链里的一次性成员(Java AnimationRegister: death/attacked 为 PLAY_ONCE, 其余 LOOP)
_ONESHOT_STATE_KEYS = OrderedDict([("attacked", "ysm_attacked"), ("death", "ysm_death")])

# use 通道(拉弓/吃喝/举盾/望远镜…)同样必须走状态机: 基岩直挂 animate 条目对不循环的
# 定时动画**不重放** —— 条件再次成立只是继续应用, 时钟不回零。实机(2026-09-05 warden):
# 第一次拉弓正常, 射出后再拉, 动画直接从末帧起步(弓一上来就是放大 2 倍的终态)。
# CSM 同样把 use 动画塞进控制器状态而非直挂(controller.animation.csm.player.custom_use
# 的 on_use 态, 见 render/player_bb_model_render.__add_use_animations)。
# 与挥击通道的关键差别: 成员状态在**触发期间一直停留**(use 动画多为 hold_on_last_frame,
# 用 all_animations_finished 出态会在拉满弓/举着盾时提前掉回 idle, 姿态当场归位)。
_ONESHOT_USE_CHANNELS = (
    # (条件前缀, 控制器/注册键, 槽位, 手序号, 触发判据)
    ("use_mainhand", "ysm_use_mainhand", "slot.weapon.mainhand", 0, _USE_MAINHAND_GATE),
    ("use_offhand", "ysm_use_offhand", "slot.weapon.offhand", 1, _USE_OFFHAND_GATE),
)

# Java AnimationRegister 的标准状态里, 主包基础控制器未驱动的几个(死亡/爬梯三态):
# 模型自己提供了同名动画时才合成播放条件(没提供的模型无任何影响)。
# 判据与 Java 一一对应: death=isDeadOrDying; ladder_*=onClimbable()+垂直速度正负零,
# 攀爬判定复用主包已下发的 query.mod.ysm_is_on_ladder / ysm_climbing_vector。
# (riptide=isAutoSpinAttack 基岩无对应 query, 暂缺)
_STANDARD_STATE_CONDITIONS = OrderedDict([
    ("death", "query.death_ticks>0"),
    ("ladder_up", "query.mod.ysm_is_on_ladder>0.5&&query.mod.ysm_climbing_vector>0"),
    ("ladder_stillness", "query.mod.ysm_is_on_ladder>0.5&&query.mod.ysm_climbing_vector==0"),
    ("ladder_down", "query.mod.ysm_is_on_ladder>0.5&&query.mod.ysm_climbing_vector<0"),
])

# ---- Java 模式的通道分层(对齐 PlayerControllerCollection.init 的注册顺序) ----
# Java: pre_parallel → vehicle → pre_main → main → post_main → hold → swing → use →
# passenger → carry_on → cap → parallel → armor, 同骨骼**后者覆盖前者**, 仅 parallel
# 通道旋转做加法。基岩(微软《Animations Overview》): "The channels (x, y, and z) are
# added separately across animations first" —— 默认**逐通道相加**, 按 animate 顺序应用。
# 复刻方式: 跨通道条件动画(hold/swing/use/carry_on/armor)带 override_previous_animation
# (权重大于 0 时清空排在它前面的条目在同一 (骨骼, 通道) 上的贡献 —— 2026-09-17 实机: 权重
# 0.5 与 1e-4 都整通道清空, 权重 0 不清空; 原版 swim/sleeping 同法), 它们必须排在被覆盖的层
# 之后; pre/main/parallel 三层不带标志, 覆盖关系由移植工具烘成伴生动画 + 占用变量, 这三层
# 在 animate 表里按 Java 通道序**倒序**排(晚层先求值, 占用变量当帧生效), 见 _OrderJavaAnimates。
# 不分层的实机后果: idle 与 pre_parallel 同骨骼相加成两倍(sahmet 尾巴过卷), 手持
# 条件动画与 idle 的手臂相加(持物姿势翻倍)。
_JAVA_PRE_CONTROLLER_PATTERN = re.compile(r"^player_pre_(?:parallel|main)")
_JAVA_PARALLEL_CONTROLLER_PATTERN = re.compile(r"^player_parallel_")
_PRE_PARALLEL_KEY_PATTERN = re.compile(r"^pre_parallel\d+$")
_ARMOR_CONDITION_PREFIXES = ("head", "chest", "legs", "feet")

# 原版玩家 animate 表只有一条 root(controller.animation.player.root), 其 third_person
# 状态挂着 move.arms/legs、look_at_target、bob、attack 等全部原版人形动画 —— 这些叠在
# Java 动画上就是第三人称鬼畜的主因; 旧通道用 controller.animation.actor.root 顶掉它。
# **但它的 first_person 状态是基岩侧第一人称手臂的唯一摆位来源**(first_person_base_pose
# /empty_hand/swap_item 作用于 rightarm/leftarm/body, 骨骼名大小写不敏感 → 命中 YSM 的
# RightArm/LeftArm; Java 的 FP 是引擎独立渲染域, 无对应物)。整体置 "0" 关停会让手臂停在
# 建模原位 = 贯穿屏幕的巨柱(实机验证: 置 0 复现, 改本条件即恢复)。
# 故只在**第三人称/纸娃娃域关停**, 第一人称放行 —— 模型自带 fp_arm 摆位动画时它也只是
# 与之相加(Java 的 FP 手臂本就是姿势叠摆位), 不冲突。
_VANILLA_ROOT_KEY = "root"
_VANILLA_ROOT_CONDITION = "variable.is_first_person"
# Java 版在代码层给**头部定位组**叠加 headPitch/netHeadYaw(CustomHumanoidEntity.
# codeAnimation: `head.setRotationX(rotX + headPitch)`) —— 定位组名固定为 "Head"
# (PlayerLocator.head = register("Head")), 且是在骨骼**局部空间做加法**。
#
# 不能直接借原版 animation.humanoid.look_at_target.default: 它写着
# `"relative_to": {"rotation": "entity"}` —— 按**实体世界朝向**摆 head 骨骼。原版
# humanoid 的 head 是根骨骼直接子级, 二者等价; YSM 模型的 Head 埋在十层父级下
# (Root→…→AllHead→Head_Molang→Head), 实体空间会顶掉沿途所有父骨骼的旋转 ——
# 实机(ref_wither)表现为"看正前方头却是歪的", 关掉即正(A/B 已验证)。
# 故自带一条等价动画: 同一根 Head 骨骼、同样的原版跟踪量, 但**不写 relative_to**
# (退回父级局部空间) 且不带 override 标志(逐通道相加) = Java 的局部加法语义。
_JAVA_HEAD_LOOK_KEY = "ysm_java_head_look"
_JAVA_HEAD_LOOK_ANIMATION = "animation.ysm.java_head_look"

# ---- 手持物挂点修正(对齐 CSM, 2026-09-17) ----
# 基岩由引擎把手持物画在 rightItem/leftItem 骨骼上, 套的是基岩自己的持物变换; Java YSM 在手部定位骨骼上
# 平移 (0,-1px,-1.6px)、绕 X 转 -90° 后再套物品模型的 THIRD_PERSON_*_HAND 显示变换
# (renderer/layer/CustomPlayerItemInHandLayer)。两套变换没法解析等价, 采用 CSM(同样把 Java YSM 模型搬到
# 网易基岩的模组, .ref/csm)按实际效果调出的修正: 移植工具把 rightItem/leftItem 的 pivot 放在定位骨骼上
# (port_java_pack._HELD_ITEM_FORWARDS, 作者自制的基岩版凋灵娘同样如此), 主包再按物品类别叠位移 ——
# 工具类[0,2,1]、其余非挂载物[0,1,2]、挂载物(attachable)不动; 主手弓另叠 [0,2.5,-1] 绕 z 4.5°,
# 主手弩另叠 [-0.5,0.5,1.5] 转 [0,3,-3.25]。数值取自 CSM rp/animations/csm.fix.animation.json
# (right_item_fix/left_item_fix/bow_fix/crossbow_fix), 工具类名单取自其 csm.tick_system 的
# csm_main_hand_is_hand_equipped。物品名/tag 判定放在 animate 条件里(骨骼通道里 is_item_name_any 不可用);
# **挂载物判定照 CSM 留在骨骼通道**(`equipped_item_is_attachable(...) ? 0 : n`): 2026-09-17 实机
# EvalMolangExpression 对主手弓(原版 attachable)求值恒 0, 该查询依赖渲染上下文, 挪进 animate 条件
# 没有证据可用 —— 照抄 CSM 的求值位置, 它调出的常量才成立。
# 只在第三人称主域生效: 第一人称手持物挂在原版体型锚点几何上, 由原版第一人称动画摆位。
_HAND_EQUIPPED_ITEM_NAMES = (
    "minecraft:trident", "minecraft:bow", "minecraft:crossbow", "minecraft:spyglass", "minecraft:shield",
    "minecraft:brush", "minecraft:fishing_rod", "minecraft:shears", "minecraft:flint_and_steel",
    "minecraft:goat_horn", "minecraft:mace")
_HAND_EQUIPPED_ITEM_TAGS = (
    "minecraft:is_sword", "minecraft:is_axe", "minecraft:is_pickaxe", "minecraft:is_shovel", "minecraft:is_hoe")


def _HandEquippedItemTest(slot):
    """CSM 口径的"工具类手持物"判据(animate 条件上下文)"""
    return "({}||{})".format(_ItemNameTest(slot, _HAND_EQUIPPED_ITEM_NAMES),
                             _ItemTagTest(slot, _HAND_EQUIPPED_ITEM_TAGS))


def _JavaItemFixAnimates():
    """手持物挂点修正条目 [(注册键, 动画 ID, 播放条件)](见上方注释)"""
    entries = []
    for side, slot, index in (("right", "slot.weapon.mainhand", 0), ("left", "slot.weapon.offhand", 1)):
        base = "{}&&query.is_item_equipped({})".format(_CONTROLLER_ANIMATE_GATE, index)
        tool = _HandEquippedItemTest(slot)
        entries.append(("ysm_java_item_fix_{}_tool".format(side),
                        "animation.ysm.java_item_fix_{}_tool".format(side), "{}&&{}".format(base, tool)))
        entries.append(("ysm_java_item_fix_{}_other".format(side),
                        "animation.ysm.java_item_fix_{}_other".format(side), "{}&&!{}".format(base, tool)))
    for item in ("bow", "crossbow"):
        entries.append(("ysm_java_{}_fix".format(item), "animation.ysm.java_{}_fix".format(item),
                        "{}&&{}".format(_CONTROLLER_ANIMATE_GATE, _ItemNameTest(
                            "slot.weapon.mainhand", ["minecraft:{}".format(item)]))))
    return entries

# 模型私有标准动画键(网易注册键), 与内置模型/kid 注册先例对齐。
# extra0-7 不在此表: 轮盘动画键按 properties.extra_animation 的**声明**注册
# (extraAnimKeys 机制), 未声明的轮盘位不再盲注册 —— 这使 female 系的
# animations_remove: [extra4..7] 之类"删掉多余盲注册"的配置不再需要
STANDARD_MODEL_ANIM_KEYS = [
    "idle", "jump", "run", "walk", "attacked", "sneak", "sneaking", "sneak_arm",
    "sneaking_arm", "swim", "sit", "ride", "ride_pig", "boat", "sleep", "fly",
    "elytra_fly", "swim_stand", "climbing", "use_righthand", "use_lefthand",
    "parallel1", "parallel2",
]

# 统一 GUI 换肤渲染控制器(多皮肤预览): 皮肤贴图/几何由运行层按预览实体动态
# 追加进数组(AddActorRenderControllerArray), 控制器本身只是种子 —— 所有模型
# 共用一份, 无需逐模型声明(gui_render_controller 仍可覆盖)
DEFAULT_GUI_RENDER_CONTROLLER = "controller.render.ysm_pack_gui"

# 网易注册键 → Java 版动画短名(创作者只做加前缀的机械重命名, 语义映射由此表承担)
NETEASE_TO_JAVA_ANIM_KEYS = {
    "use_righthand": "use_mainhand",
    "use_lefthand": "use_offhand",
}

# 标准预览动画表(网易键, Java 动画短名, 显示名)
STANDARD_PREVIEW_ANIMS = [
    ("idle", "idle", "待机"),
    ("walk", "walk", "走路"),
    ("run", "run", "跑步"),
    ("jump", "jump", "跳跃"),
    ("attacked", "attacked", "受击"),
    ("sneak", "sneak", "潜行"),
    ("swim", "swim", "游泳"),
    ("sit", "sit", "坐"),
    ("ride", "ride", "骑乘(马、驴、骡)"),
    ("boat", "boat", "坐船"),
    ("sleep", "sleep", "睡觉"),
    ("fly", "fly", "飞行(创造)"),
    ("elytra_fly", "elytra_fly", "飞行(鞘翅)"),
    ("swim_stand", "swim_stand", "水面飘浮"),
    ("climbing", "climbing", "爬行"),
    ("use_righthand", "use_mainhand", "使用右手"),
    ("use_lefthand", "use_offhand", "使用左手"),
]

# 抱起动画为共享资源(不属于模型私有命名空间)
# JSONC 剥离(StripJsonComments 用): 字符串 | 行注释 | 块注释 单遍匹配;
# 字符串分支必须排最前 —— 先整段消费字符串, 串内的注释符号才不会被误删
_JSON_STRING_SOURCE = r'"(?:\\.|[^"\\])*"'
_JSON_TOKEN_PATTERN = re.compile(
    _JSON_STRING_SOURCE + r"|//[^\r\n]*" + r"|/\*.*?\*/", re.DOTALL)
_JSON_STRING_PATTERN = re.compile(
    _JSON_STRING_SOURCE + r'|[^"]+', re.DOTALL)
_NON_NEWLINE_PATTERN = re.compile(r"[^\r\n]")
_TRAILING_COMMA_PATTERN = re.compile(r",(\s*[}\]])")

CARRYON_PREVIEW_ANIMS = [
    ("carryon_block", "animation.carryon_block", "抱起方块"),
    ("carryon_entity", "animation.carryon_entity", "抱起生物"),
    ("carryon_player", "animation.carryon_player", "抱起玩家"),
    ("carryon_princess", "animation.carryon_princess", "被抱着"),
]


def StripJsonComments(text):
    """JSONC -> JSON: 剥除 // 与 /* */ 注释, 并清掉随之出现的尾逗号。

    Java 侧用 Gson 宽容模式解析 ysm.json, 野外模型包普遍带注释与尾逗号(官方
    Wiki 的 ysm.json 示例本身即标注为 jsonc); py 的 json.loads 不接受, 装载会
    整包失败。做法沿用同工作室 arrisJei 的实现: 单遍正则, **字符串分支排在最前**
    —— 扫到引号时整段字符串优先被消费, 串里的 // 与 /* */ 不会被误删
    (metadata.link.home 的 "https://..." 正是此例); 注释替换为**等长空白并保留
    换行**, 解析报错的行列号仍与原文件对齐。尾逗号清理同样只作用于字符串之外。
    """
    def _Blank(match):
        token = match.group(0)
        if token[:1] == '"':
            return token
        return _NON_NEWLINE_PATTERN.sub(" ", token)

    stripped = _JSON_TOKEN_PATTERN.sub(_Blank, text)

    def _Comma(match):
        token = match.group(0)
        if token[:1] == '"':
            return token
        return _TRAILING_COMMA_PATTERN.sub(r"\1", token)

    return _JSON_STRING_PATTERN.sub(_Comma, stripped)

def ByteifyJson(data):
    """py2: 递归把 unicode 转 utf-8 str(引擎 C 接口只收 str), 保持输入 dict 类型(含 OrderedDict)"""
    if isinstance(data, unicode):  # noqa: F821
        return data.encode("utf-8")
    if isinstance(data, list):
        return [ByteifyJson(item) for item in data]
    if isinstance(data, dict):
        return type(data)((ByteifyJson(k), ByteifyJson(v)) for k, v in data.items())
    return data


def _AsEntryList(value):
    """netease 段的 kv 字段 → [(k, v), ...]。

    标准形态是 **{} 键值对**(解析经 object_pairs_hook 保序, 书写序 = 注册序);
    [[k, v], ...] 列表为兼容形态。非法条目跳过。
    """
    result = []
    if isinstance(value, dict):
        for key in _OrderedKeys(value):
            result.append((key, value[key]))
        return result
    if not isinstance(value, list):
        return result
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            result.append((item[0], item[1]))
    return result


def _AsEntryListWithBare(value, expandFunc):
    """kv + 裸键混排: 值留空(""/None/true)或裸 "键" 按约定展开为 (键, expandFunc(键))。

    引擎动画/控制器注册只收全局资源 ID(无法像 Java 按文件路径声明), 但按约定
    改名后 ID 可由键名机械展开 —— 留空值/裸字符串形态即为此设计, 消掉键+ID
    双份冗余。标准形态 {}: {"brewing": "", "charging": "animation.action.sneak_arms"}。
    """
    result = []
    if isinstance(value, dict):
        for key in _OrderedKeys(value):
            item = value[key]
            if isinstance(item, str) and item:
                result.append((key, item))
            elif item in ("", None, True):
                result.append((key, expandFunc(key)))
        return result
    if not isinstance(value, list):
        return result
    for item in value:
        if isinstance(item, str) and item:
            result.append((item, expandFunc(item)))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            result.append((item[0], item[1]))
    return result


def _AsPreviewEntries(value):
    """预览动画表条目 → [(键, 动画ID, 显示名), ...]。

    标准形态 {}: {"键": ["动画ID", "显示名"]}; [[键, ID, 名], ...] 为兼容形态。
    """
    result = []
    if isinstance(value, dict):
        for key in _OrderedKeys(value):
            item = value[key]
            if isinstance(item, (list, tuple)) and len(item) == 2:
                result.append((key, item[0], item[1]))
        return result
    if not isinstance(value, list):
        return result
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) == 3:
            result.append(tuple(item))
    return result


# replace_entities 补丁条目里按 kv 语义解析的字段(dict 形态归一为列表, 供运行层消费)
_REPLACE_ENTRY_KV_FIELDS = ("animations", "animate", "animation_controllers")


def _NormalizeReplacePatch(patch):
    """netease.replace_entities/arrow 补丁: 内层 kv 字段 dict 形态 → [[k, v], ...] 归一"""
    if not isinstance(patch, dict):
        return patch
    normalized = OrderedDict()
    for key in _OrderedKeys(patch):
        value = patch[key]
        if key in _REPLACE_ENTRY_KV_FIELDS and isinstance(value, dict):
            normalized[key] = [[k, v] for k, v in _AsEntryList(value)]
        else:
            normalized[key] = value
    return normalized


def _RemoveEntries(entries, removeKeys):
    """按键剔除注册条目(netease.*_remove 精确对齐用); removeKeys 为空时原样返回"""
    if not removeKeys:
        return entries
    removeSet = set(removeKeys)
    return [entry for entry in entries if entry[0] not in removeSet]


# Java 包内贴图路径的扩展名(区分"包内文件路径"与"基岩真实引用路径"的判据)
_IMAGE_EXT_PATTERN = re.compile(r"\.(png|jpg|jpeg|tga)$", re.I)


def _TextureEntryPath(entry):
    """files.player.texture 条目(str 或 {uv: path}) → 归一化路径(去扩展名)"""
    path = entry
    if isinstance(entry, dict):
        path = entry.get("uv", "")
    if not isinstance(path, str) or not path:
        return None
    path = path.replace("\\", "/")
    dot = path.rfind(".")
    if dot > path.rfind("/"):
        path = path[:dot]
    return path or None


def _DeriveSkins(jsonDict, netease, packName, warnings):
    """皮肤序列推导: netease.textures 简式优先, 否则从 files.player.texture 推导。

    返回 (有序皮肤名列表, 皮肤名→贴图路径), 网易侧首个皮肤恒为 "default"。

    files.player.texture 为**列表混排**(与 animation 声明同构):
    - 字符串 / {uv: 路径}(Java 原生, 含 PBR 形态): 皮肤名 = 文件名去扩展,
      贴图按约定拼 textures/entity/<包名>/<文件名>;
    - {皮肤名: 贴图路径}(基岩扩展): 皮肤名显式给出、路径**直通**(基岩真实
      引用路径) —— 皮肤名与文件名不一致/共享贴图目录的场景, 同名覆盖前者。
    """
    textures = netease.get("textures")
    if isinstance(textures, dict) and textures:
        if "default" not in textures:
            return None, None
        skinSort = [s for s in (netease.get("skin_sort") or []) if s in textures]
        for skinName in textures:
            if skinName not in skinSort:
                skinSort.append(skinName)
        if skinSort[0] != "default":
            skinSort.remove("default")
            skinSort.insert(0, "default")
        return skinSort, dict(textures)

    # ---- Java 原生: files.player.texture(+基岩扩展 dict 条目) ----
    playerFiles = (jsonDict.get("files") or {}).get("player") or {}
    textureList = playerFiles.get("texture") or []
    javaEntries = []
    javaNames = []
    directEntries = []
    for entry in textureList:
        # {皮肤名: 路径} 直通条目(不含 "uv" 键 —— 含 uv 的是 Java PBR 形态)
        if isinstance(entry, dict) and "uv" not in entry:
            for skinName in _OrderedKeys(entry):
                if isinstance(entry[skinName], str) and entry[skinName]:
                    directEntries.append((skinName, entry[skinName]))
            continue
        path = _TextureEntryPath(entry)
        if not path:
            continue
        name = path.split("/")[-1]
        if not name or name in javaNames:
            continue
        javaNames.append(name)
        javaEntries.append((name, path))
    if not javaNames and directEntries:
        # 纯直通形态: 皮肤名/路径全部显式, 无需 default_texture 改名链
        skinSort = []
        textureMap = {}
        for skinName, texturePath in directEntries:
            if skinName not in skinSort:
                skinSort.append(skinName)
            textureMap[skinName] = texturePath
        if "default" not in textureMap:
            return None, None
        if skinSort[0] != "default":
            skinSort.remove("default")
            skinSort.insert(0, "default")
        return skinSort, textureMap
    if not javaNames:
        return None, None

    properties = jsonDict.get("properties") or {}
    defaultName = properties.get("default_texture")
    if defaultName not in javaNames:
        if defaultName:
            warnings.append("default_texture={} 不在贴图列表中, 以首项 {} 为默认皮肤".format(
                defaultName, javaNames[0]))
        defaultName = javaNames[0]

    # Java 的 files.player.texture 是"包内相对路径"(真实文件写作 textures/skin.png),
    # 与基岩资源包路径形似但语义不同, 无法凭字面区分 —— 一律按 <前缀>/<文件名> 改写。
    # 需要指定基岩真实路径时用 netease.textures(逐皮肤)或 netease.texture_path_prefix。
    texturePrefix = netease.get("texture_path_prefix") or "textures/entity/{}/".format(packName)
    skinSort = []
    textureMap = {}
    for name, _path in javaEntries:
        skinKey = "default" if name == defaultName else name
        skinSort.append(skinKey)
        textureMap[skinKey] = texturePrefix + name
    # 混排的直通条目在改名链之后并入: 同名覆盖推导路径, 新名追加
    for skinName, texturePath in directEntries:
        if skinName not in textureMap:
            skinSort.append(skinName)
        textureMap[skinName] = texturePath
    if skinSort[0] != "default":
        skinSort.remove("default")
        skinSort.insert(0, "default")
    return skinSort, textureMap


def _BuildAuthors(metadata):
    authors = []
    for author in metadata.get("authors") or []:
        if not isinstance(author, dict) or not author.get("name"):
            continue
        avatar = author.get("avatar") or NULL_ICON
        if not avatar.startswith("textures/"):
            # Java 版 avatar 为包内相对路径, 网易侧须为资源包贴图路径, 否则回退默认图标
            avatar = NULL_ICON
        authors.append({
            "avatar": avatar,
            "name": author.get("name"),
            "role": author.get("role", ""),
            "comment": author.get("comment", ""),
        })
    return authors


def _QueryControllerAnimRefs(namespace):
    """该命名空间下自有控制器引用的动画短键集(索引不可用时空集)"""
    index = _GetResourceIndex()
    if index is None or not hasattr(index, "QueryNamespaceControllerAnimRefs"):
        return set()
    try:
        return index.QueryNamespaceControllerAnimRefs(namespace)
    except Exception:
        return set()


def _GetResourceIndex():
    """惰性取资源索引模块; 离线/无引擎环境返回 None(调用方回落行为包副本)"""
    try:
        from . import resourceIndex
    except ImportError:
        return None
    return resourceIndex


def _NamespaceName(animNs):
    """完整动画命名空间 animation.<ns> → 索引查询用的 <ns>(自定义前缀原样返回)"""
    prefix = "animation."
    return animNs[len(prefix):] if animNs.startswith(prefix) else animNs


def _QueryIndexAnimations(namespace, foundVars=None):
    """资源包索引里该命名空间的动画 [(短键, ID), ...]; 顺带收集其 molang 变量"""
    index = _GetResourceIndex()
    if index is None:
        return []
    entries = index.QueryNamespaceAnimations(namespace)
    if entries and foundVars is not None:
        foundVars.update(index.QueryNamespaceVariables(namespace))
    return entries


def _AppendVariableInitController(namespace, ctlEntries, animateEntries):
    """资源索引里有本包的变量初始化控制器(_VARIABLE_INIT_KEY 注)时补进注册表(恒开)。

    声明文件里已带该控制器(路径解析/命名空间回落已注册同键)则不重复; 索引不可用
    (离线/无引擎)或包没有该文件(旧版移植产物)时不注册 —— 纸娃娃退化为变量按 0 求值,
    重跑 devtools/fix_ported_controllers.py 生成后即恢复。
    """
    if any(key == _VARIABLE_INIT_KEY for key, _ctlId in ctlEntries):
        return ctlEntries, animateEntries
    index = _GetResourceIndex()
    if index is None:
        return ctlEntries, animateEntries
    ctlId = "controller.animation.{}.{}".format(namespace, _VARIABLE_INIT_KEY)
    if ctlId not in set(resId for _key, resId in index.QueryNamespaceControllers(namespace)):
        return ctlEntries, animateEntries
    return (ctlEntries + [(_VARIABLE_INIT_KEY, ctlId)],
            animateEntries + [(_VARIABLE_INIT_KEY, _VARIABLE_INIT_CONDITION)])


def _ShortAnimKey(animId):
    """动画/控制器 ID → 注册短键: 去掉 "animation.<第一段>." 前缀的剩余部分。

    路径解析模式的短键约定(对齐 Java 文件内短名): animation.xxx.abc.def → abc.def。
    不带标准前缀的 ID 原样作键。
    """
    for head in ("controller.animation.", "animation."):
        if animId.startswith(head):
            body = animId[len(head):]
            if "." in body:
                return body.split(".", 1)[1]
            return body
    return animId


def _QueryIndexFileAnimations(declPath, nsHint, foundVars=None):
    """路径解析模式: 声明文件 → [(短键, 动画ID), ...](文件书写序)。

    ysm.json 的 files.player.animation 值指向资源包动画文件时, 该文件内**全部**
    动画注册进模型(与 Java 按文件加载一致), 短键 = ID 去 "animation.<命名空间>."。
    未命中(索引缺席/路径无法定位)返回 None, 由调用方回落。
    """
    index = _GetResourceIndex()
    if index is None or not hasattr(index, "QueryFileAnimations"):
        return None
    got = index.QueryFileAnimations(declPath, nsHint)
    if got is None:
        return None
    fileIds, fileVars = got
    if foundVars is not None:
        foundVars.update(fileVars)
    return [(_ShortAnimKey(animId), animId) for animId in fileIds]


def _QueryIndexFileControllers(declPath, nsHint, foundVars=None):
    """路径解析模式(控制器版): 声明文件 → (控制器条目, 常开 animate 条目, 是否命中)。

    键取 ID 末段, 常开 "1"(Java geckolib 控制器加载即常开), 与
    _ReadControllerFileEntries 的 BP 副本语义一致。
    """
    index = _GetResourceIndex()
    if index is None or not hasattr(index, "QueryFileControllers"):
        return [], [], False
    got = index.QueryFileControllers(declPath, nsHint)
    if got is None:
        return [], [], False
    fileIds, fileVars = got
    if foundVars is not None:
        foundVars.update(fileVars)
    ctlEntries = [(ctlId.split(".")[-1], ctlId) for ctlId in fileIds]
    return ctlEntries, [(key, "1") for key, _ctlId in ctlEntries], True


def _ReadAnimFileEntries(relPath, animNs, readTextFunc, warnings, foundVars=None):
    """读取 BP 模型目录内的动画文件, 返回 [(短键, 动画ID), ...]。

    只收 animation.<命名空间>.* 的键(去前缀作短键); 其余键跳过并告警
    (Java 原文件短键名漏改名的诊断信号)。文件缺席时静默返回空。
    foundVars(可选 set): 顺带收集文件内引用的 molang 变量短名(初始化推导用)。
    """
    if not isinstance(relPath, str) or not relPath or readTextFunc is None:
        return []
    text = readTextFunc(relPath)
    if not text:
        return []
    try:
        if not isinstance(text, str):
            text = text.encode("utf-8")
        # 保序: 动画注册顺序(及由此合成的条件表达式)不随字典哈希漂移
        data = ByteifyJson(json.loads(text.decode("utf-8-sig"), object_pairs_hook=OrderedDict))
    except ValueError as e:
        warnings.append("{} JSON 语法错误, 无法自动注册动画: {}".format(relPath, e))
        return []
    if foundVars is not None:
        foundVars.update(_MOLANG_VAR_PATTERN.findall(text))
    animMap = data.get("animations") if isinstance(data, dict) else None
    if not isinstance(animMap, dict):
        warnings.append("{} 缺少 animations 段, 无法自动注册动画".format(relPath))
        return []
    entries = []
    skipped = []
    prefix = animNs + "."
    for animId in _OrderedKeys(animMap):
        if animId.startswith(prefix):
            entries.append((animId[len(prefix):], animId))
        else:
            skipped.append(animId)
    if skipped:
        warnings.append(
            "{} 内 {} 个动画键未以 {} 开头(如 {}), 未自动注册 —— Java 原文件的短键名"
            "需按约定重命名, 共享/跨命名空间动画请经显式声明注册".format(
                relPath, len(skipped), prefix, skipped[0]))
    return entries


def _LoadDeclaredAnimations(jsonDict, animNs, readTextFunc, warnings, foundVars=None,
                            skipKeys=None, expandFunc=None):
    """命名空间下的动画 → 自动注册条目 [(短键, 动画ID), ...]。

    **主来源是资源包索引**(resourceIndex 扫描磁盘 RP 得到的 animation.<命名空间>.*
    全集) —— 创作者只需把动画放进资源包一份, 无需任何行为包副本。
    索引不可用(联机大厅等无本地资源目录)时, 回落到读行为包内与声明路径同名的
    副本文件(历史方案, 副本存在才生效)。
    skipKeys: 不在主命名空间处理的声明键(第一人称手臂通道单独装载)。

    两种声明形态:
    - dict(Java 原生): {"main": 路径, "arm": 路径, ...} —— 语义槽位(arm/fp_arm
      走手臂通道);
    - list(基岩扩展): ["路径", {"短名": "动画ID", ...}, ...] —— 字符串按路径解析
      导入**整个文件**, dict 逐条注册单个动画(值留空按约定展开), 书写序即注册序。
    """
    animationDecl = ((jsonDict.get("files") or {}).get("player") or {}).get("animation")
    nsName = _NamespaceName(animNs)
    if isinstance(animationDecl, list) and animationDecl:
        entries = []
        for item in animationDecl:
            if isinstance(item, dict):
                entries += _AsEntryListWithBare(
                    item, expandFunc or (lambda key: "{}.{}".format(animNs, key)))
                continue
            if not isinstance(item, str) or not item:
                continue
            got = _QueryIndexFileAnimations(item, nsName, foundVars)
            if got is None:
                # 索引未命中: BP 副本回落(历史通道), 再不行明确告警
                got = _ReadAnimFileEntries(item, animNs, readTextFunc, warnings, foundVars)
                if not got:
                    warnings.append(
                        "files.player.animation 声明的文件 {} 未在资源包中找到, "
                        "该文件的动画未注册".format(item))
            entries += got or []
        return entries
    if not isinstance(animationDecl, dict) or not animationDecl:
        # 未声明动画文件的包(如只用标准键的内置模型)保持原样: 不做全量自动注册
        return []
    # 路径解析模式优先: 逐声明键按文件精确定位(声明写资源包真实路径, 或 Java 原
    # 包内路径按文件名匹配), 命中文件的**全部**动画按书写序注册。
    fileEntries = []
    missedKeys = []
    for declKey in _OrderedKeys(animationDecl):
        if skipKeys and declKey in skipKeys:
            continue
        declPath = animationDecl[declKey]
        got = _QueryIndexFileAnimations(declPath, nsName, foundVars)
        if got is None:
            missedKeys.append(declKey)
        else:
            fileEntries += got
    if fileEntries:
        if missedKeys:
            # 命中过文件说明索引可用 —— 未命中的键是真实的路径问题, 明确告警
            # (不再混入命名空间全量, 防止两种来源重复注册)
            for declKey in missedKeys:
                warnings.append(
                    "files.player.animation.{} 声明的文件 {} 未在资源包中找到, "
                    "该文件的动画未注册".format(declKey, animationDecl[declKey]))
        return fileEntries
    entries = _QueryIndexAnimations(nsName, foundVars)
    if entries:
        return entries
    for declKey in _OrderedKeys(animationDecl):
        if skipKeys and declKey in skipKeys:
            continue
        entries += _ReadAnimFileEntries(
            animationDecl[declKey], animNs, readTextFunc, warnings, foundVars)
    return entries


# 第一人称手臂通道(Java files.player.model.arm + 动画声明键 fp_arm/arm):
# - fp_arm 文件 = 并行动画 parallel0-7(Wiki《第一人称动画》), 仅 FP 域;
# - arm 文件   = 手持/使用/挥击条件动画(hold_mainhand:sword 等 57 条)。
#   Java dev/1.20 源码定论(RawModelAssembler L47-50): 除 fp_arm 外全部动画集
#   ——含 arm——合并进**主域**, 供第三人称条件动画; 基岩侧因此双注册:
#   ① 主域短键(参与第三人称条件合成, parallel 系除外——那是 FP 专属空壳);
#   ② fp_ 前缀键(注册进 FP 白名单通道的动画本体, 供模型自带控制器/animate_extra 引用)。
#   **但 FP 域的自动 animate 只有 parallel 与甲槽两类** —— Java 第一人称手臂只有
#   fp.arm.misc(EmptyPredicate, 自身不播)/fp.arm.parallel_0-7/fp.arm.armor_<槽位>
#   三路通道, 没有 hold/swing/use, 详见 _BuildFpArmAnimates 注。
# 基岩侧: arm 几何以 "arm" 键注册进玩家, 由 first_person_ysm_arm 渲染控制器在
# 第一人称渲染(替代旧的主模型剔除法); 动画命名空间 animation.<包名>_arm.*。
_FP_ARM_ANIMATION_KEYS = ("fp_arm", "arm")
_FP_ARM_KEY_PREFIX = "fp_"
_PARALLEL_KEY_PATTERN = re.compile(r"^(?:pre_)?parallel\d+$")
# 移植工具拆出的"通道覆盖伴生动画"键: <原动画键>__own<N>(port_java_pack.ApplyChannelOwnership)。
# Java 逐 (骨骼, 通道) 由最后处理的通道决定, 基岩逐通道相加/相乘: 原动画里会被**更晚通道**
# 覆盖的通道搬进伴生动画, 与原动画同状态播放, 权重 = "晚层写入者都不活跃"(占用变量)。
# 伴生键不参与任何合成规则(并行族/条件动画/主链成员/一次性通道成员的解析都先跳过它);
# 带 override 的直挂条件动画(hold/passenger/carryon)的伴生由 _AttachOwnershipCompanions 紧跟
# 原条目挂上, 权重 = 原条件 && ysm.json 顶级 channel_ownership 给的核心权重; 裸 pre_parallel/parallel
# 直挂条目的伴生同样紧跟(_OrderJavaAnimates), 权重 = 门 ? channel_ownership 给的完整权重 : 0; 挥击/使用成员的
# 伴生由移植工具直接写进一次性通道状态机。纸娃娃与 GUI 预览没有晚层控制器, 按原动画处理
# (同条件恒播)才等价原动画的完整效果。
_OWNERSHIP_COMPANION_PATTERN = re.compile(r"^(.+)__own(\d+)$")
# 运行期维护的状态变量短名: 占用变量(variable.ysm_own_<宿主> 状态序号 / variable.ysm_ownset_<n>
# 状态集合)与状态计时(variable.ysm_t0_<控制器> 进入状态的 query.life_time, 移植工具改写的
# "动画播完"判据读它)。不参与"文件扫描变量补 0"初始化 —— 它们只由控制器 on_entry 写,
# 读取处都带 ??0
_OWNERSHIP_VARIABLE_PATTERN = re.compile(r"^ysm_(?:own(?:set)?|t0)_", re.IGNORECASE)


_KEY_STRING_TYPES = (str, type(u""))


def _IsOwnershipCompanionKey(key):
    """通道覆盖伴生动画键(<原键>__own<N>)"""
    return isinstance(key, _KEY_STRING_TYPES) and bool(_OWNERSHIP_COMPANION_PATTERN.match(key))


def _CompanionBaseKey(key):
    """伴生键 → 原动画键; 其他键原样返回"""
    matched = _OWNERSHIP_COMPANION_PATTERN.match(key) if isinstance(key, _KEY_STRING_TYPES) else None
    return matched.group(1) if matched else key


def _AttachOwnershipCompanions(entries, animKeys, weights=None, fractional=False):
    """直挂条目表里每个原动画条目后面紧跟它的伴生动画条目(见 _OWNERSHIP_COMPANION_PATTERN 注)。

    伴生权重 = 原条件 && 核心权重(ysm.json 顶级 channel_ownership: {伴生键: 核心表达式}, 移植工具
    ApplyChannelOwnership 写入)。核心缺席时只用原条件 —— 与拆分前的原动画等价(通道不让出, 但也
    不丢)。伴生必须**紧跟**原条目: 带 override 的条件动画按 animate 顺序清空前序通道, 位置一致
    才等价原动画。条目键不在注册表(animKeys)里的伴生不挂。
    fractional: 表里记的是可带小数的完整权重(裸 pre_parallel/parallel 的伴生: 让出时保留 1e-4 下限,
    权重 0 的直挂动画会暂停计时), 合成 `((原条件)?(权重):0)` —— `&&` 会把 1e-4 抹成 1。
    """
    byBase = OrderedDict()
    for key in animKeys:
        matched = _OWNERSHIP_COMPANION_PATTERN.match(key) if isinstance(key, _KEY_STRING_TYPES) else None
        if matched:
            byBase.setdefault(matched.group(1), []).append((int(matched.group(2)), key))
    if not byBase:
        return list(entries)
    weights = weights if isinstance(weights, dict) else {}
    out = []
    for key, condition in entries:
        out.append((key, condition))
        for _index, companionKey in sorted(byBase.get(key) or []):
            core = weights.get(companionKey)
            if isinstance(core, _KEY_STRING_TYPES) and core:
                if not condition:
                    weight = core
                elif fractional:
                    weight = "(({})?({}):0)".format(condition, core)
                else:
                    weight = "({})&&({})".format(condition, core)
            else:
                weight = condition
            out.append((companionKey, weight))
    return out
# FP 域动画门(fp_parallel*/fp_<甲槽>$…/ysm_fp_swing): 第一人称 + 非旁观 + **主手空手**。
# 空手门与渲染控制器同源: 第一人称下 default 键换成原版体型几何作附着物锚点(见
# playerRender.ApplyItemAnchorGeometry), 它与模型手臂共用 rightarm/leftarm 等骨骼名
# (引擎匹配不分大小写), 而 Java 的 fp_arm 动画正是只动 LeftArm/RightArm —— 手持物品
# 时模型手臂本就不渲染, 再播只会把附着物从原版位置带偏。Java 语义一致: FP 手臂
# 实体只在空手渲染路径上出现(Forge RenderArmEvent)。
_FP_ARM_GATE = "variable.is_first_person&&!query.is_spectator&&!query.is_item_equipped(0)"
# 第一人称手臂**渲染控制器**的条件: 手持物品时整个控制器关掉, 而不是只靠
# part_visibility 把骨骼藏起来。依据 CSM 源码(bp/CustomSteveModel/render/
# player_bb_model_render.py:__add_render_controllers):
#   fp_render_condition = 'v.is_first_person && !query.mod.csm_better_first_person
#                          && !v.is_paperdoll && !q.is_item_equipped'
# 语义上与 Java 一致(手持物品走 renderArmWithItem, 模型手臂根本不参与); 且**手持
# 物品时第一人称只剩全隐藏的原版体型 pass**(first_person_ysm_fix + 锚点换几何),
# 原版第一人称动画原样驱动锚点骨架, 基岩附着物(弓/弩/盾/三叉戟)落在原版位置。
_FP_ARM_CONTROLLER_CONDITION = (
    "variable.is_first_person && !query.is_spectator && !query.is_item_equipped")
_FIRST_PERSON_CARVE_CONTROLLER = "controller.render.player.first_person_ysm"
# 声明了 model.arm 的包: arm 几何是移植工具重建过的"原版手臂骨架包装 + Java 子树",
# part_visibility 全放行(Java 渲染整个 RightArm 子树, 按骨骼名前缀过滤会掉件 ——
# 官方酒狐 22 个包里 8 个的大臂/手/装饰件不叫 Right*)
_FIRST_PERSON_ARM_CONTROLLER = "controller.render.player.first_person_ysm_arm"
# 未声明 model.arm 的包: "arm" 键回落主几何(整个身体), 仍走主手侧白名单剔除法
_FIRST_PERSON_ARM_MAIN_CONTROLLER = "controller.render.player.first_person_ysm_arm_main"
# 新版模型的第三人称主体控制器(渲染 default 键上的模型贴图/几何; 卸模型由
# 引擎 ResetEntityExtraSkin 整体还原, 见 playerRender)
_YSM_MAIN_CONTROLLER = "controller.render.player.ysm_main"
_YSM_MAIN_CONDITION = "!variable.is_first_person && !variable.map_face_icon && !query.is_spectator"


def _LoadArmAnimations(jsonDict, packName, readTextFunc, warnings, foundVars=None):
    """手臂动画(animation.<包名>_arm.* 命名空间) → (fp条目, 主域条目)。

    fp 条目 = 全部键加 fp_ 前缀(FP 白名单通道); 主域条目 = 同键裸短名再注册一份
    (Java dev/1.20: arm 集并入主域, 条件动画对第三人称生效), parallel 系除外
    (FP 专属空壳, 并入会覆盖 main 文件的同名键)。
    索引路径按命名空间整取, 无法分辨 arm/fp_arm 文件来源 —— 同一规则批量适用
    (fp_arm 仅含 parallel 系, 恰好被排除, 两路径结果一致)。
    与主命名空间同样以资源包索引为主来源, 行为包副本为回落。
    """
    armNsName = "{}_arm".format(packName)
    animationDecl = ((jsonDict.get("files") or {}).get("player") or {}).get("animation")
    # 路径解析模式优先(与主命名空间同规则): 按声明文件精确注册
    indexed = []
    if isinstance(animationDecl, dict):
        for declKey in _FP_ARM_ANIMATION_KEYS:
            if declKey not in animationDecl:
                continue
            got = _QueryIndexFileAnimations(animationDecl[declKey], armNsName, foundVars)
            if got is not None:
                indexed += got
    if not indexed:
        indexed = _QueryIndexAnimations(armNsName, foundVars)
    if not indexed:
        indexed = []
        if isinstance(animationDecl, dict):
            armNs = "animation.{}".format(armNsName)
            for declKey in _FP_ARM_ANIMATION_KEYS:
                indexed += _ReadAnimFileEntries(
                    animationDecl.get(declKey), armNs, readTextFunc, warnings, foundVars)
    fpEntries = [(_FP_ARM_KEY_PREFIX + shortKey, animId) for shortKey, animId in indexed]
    mainEntries = [
        (shortKey, animId) for shortKey, animId in indexed
        if not _PARALLEL_KEY_PATTERN.match(shortKey)
    ]
    return fpEntries, mainEntries


def _BuildFpArmAnimates(fpArmEntries, warnings):
    """手臂动画条目 → animate 条件(对齐 Java 第一人称手臂的**三路通道**)。

    Java(client/controller/collections/FPArmControllerCollection.init)第一人称手臂只有:
    `fp.arm.misc`(EmptyPredicate, 自身什么都不播, 留给模型自带控制器接管)、
    `fp.arm.parallel_0-7`(恒播加法)、`fp.arm.armor_<槽位>`。**没有 hold/swing/use 通道**
    —— wiki《第一人称动画》的动画清单同样只列 parallel0-7。
    故这里只合成: parallel 恒开 + 甲槽条件; 其余键不自动 animate(供控制器/animate_extra 引用)。

    早先版本把 hold/swing/use 条件动画也合到 FP 域(fp_hold_mainhand.cls.bow 之类),
    是纯负作用: 第一人称手臂**只在空手时可见**(渲染控制器 first_person_ysm_arm 的白名单,
    对齐 Java —— Forge RenderArmEvent 只在空手臂渲染路径上触发, 手持物品时走的是原版
    renderArmWithItem, 模型手臂根本不参与), 手持物品时这些动画只是在挪一条看不见的手臂;
    而 arm 几何补上 rightItem/leftItem 之后, 它们会**连带把手持物品挪走/缩放掉**
    (拉弓时 use_mainhand:bow 把手部定位骨骼放大到 2 倍 → 第一人称弓变大且偏移;
    hold_mainhand:shield 缩放到 0 → 第一人称盾直接不显示)。
    """
    entries = []
    armorKeys = []
    for key, _animId in fpArmEntries:
        if _IsOwnershipCompanionKey(key):
            continue
        shortKey = key[len(_FP_ARM_KEY_PREFIX):]
        if shortKey.startswith("parallel"):
            entries.append((key, _FP_ARM_GATE))
            continue
        parsed = _SplitConditionKey(shortKey)
        if parsed is not None and parsed[0][0] in _ARMOR_CONDITION_PREFIXES:
            armorKeys.append(shortKey)
    entries += _BuildConditionalAnimates(
        armorKeys, warnings, keyPrefix=_FP_ARM_KEY_PREFIX, extraGate=_FP_ARM_GATE,
        personGuard=None)
    return entries


def _OneShotConditionMembers(animKeys, prefix, slot, handIndex, fallbackKey, keyPrefix=""):
    """某条件通道的成员 → ([(注册键, 自身物品判据)...] 按 Java 优先级 id > tag > 分类,
    兜底键或 None)。

    模组专属分类(基岩无判定)不收 —— 与直挂条目同样不播。
    """
    groups = OrderedDict([("$", []), ("#", []), (":", [])])
    fallback = None
    for key in animKeys:
        if keyPrefix and not key.startswith(keyPrefix):
            continue
        if _IsOwnershipCompanionKey(key):
            continue        # 伴生动画随原成员进同一状态(BuildOneShotControllers companions)
        shortKey = key[len(keyPrefix):]
        if shortKey == fallbackKey:
            fallback = key
            continue
        parsed = _SplitConditionKey(shortKey)
        if parsed is None or parsed[0][0] != prefix:
            continue
        groups[parsed[1]].append((key, parsed[2]))
    members = []
    for key, itemId in groups["$"]:
        members.append((key, _ItemNameTest(slot, [itemId])))
    for key, tag in groups["#"]:
        members.append((key, _ItemTagTest(slot, [tag])))
    classified = []
    for key, classify in groups[":"]:
        if classify == "empty":
            if handIndex is not None:
                members.append((key, "!query.is_item_equipped({})".format(handIndex)))
            continue
        test = _CLASSIFY_TESTS.get(classify)
        if test is None:
            continue
        classified.append((_ClassifyRank(classify), key, test))
    # 控制器状态按序判定、先命中者胜 —— 与直挂条目的显式否定等价, 只需排对顺序
    classified.sort(key=lambda entry: entry[0])
    for _rank, key, test in classified:
        members.append((key, _ClassifySelfTest(test, slot, handIndex)))
    return members, fallback


def _OneShotSwingMembers(animKeys, keyPrefix=""):
    """挥击通道成员(见 _OneShotConditionMembers)。

    只收主手 swing 族: 基岩无副手挥击信号, swing_offhand 族收进来会在主手挥击时误播
    副手动画(其直挂条目沿用旧行为不动)。
    """
    return _OneShotConditionMembers(
        animKeys, "swing", "slot.weapon.mainhand", 0, "swing_hand", keyPrefix)


# ---- 生成式状态机的过渡时长: Java geckolib 通道过渡的基岩等价物 ----
# Java(PlayerControllerCollection): main/vehicle/hold/use 通道起始过渡 0.1s(2 tick,
# 从当前姿态线性插到新动画首帧), swing 通道 0; PLAY_ONCE 动画播完后的尾过渡全局
# 3 tick(AnimationData.DEFAULT_ENDING_TRANSITION_LENGTH, 末姿态淡出到底层姿态)。
# 基岩直挂 animate 条目是**零过渡硬切换** —— 转换包"两段动画衔接不流畅"的根因;
# 引擎唯一的交叉淡化通道是控制器状态的 blend_transition, 故 Java 模式的主链走生成式
# 状态机。blend_transition 写在**被离开**的状态上(Mojang 文档原注 "when transitioning
# away from this state"; 原版 camel 把只可能被离开的初始态显式写成 0.0 同证), 主链各态
# 同值故与另一种解读也等价。
#
# **override_previous_animation 与交叉淡化互斥(2026-09-03 游戏内多轮核实)**: 淡化期间
# 引擎同时应用出态与入态两份动画, 入态带该标志就会抹掉出态的贡献, 于是 0.1s 里模型是
# "从绑定姿态淡入新动画", 观感就是硬切+弹一下。实证: 原版资源包 73 个带 blend_transition
# 的状态里, 播 override 动画的**一个都没有**; 玩家实体的控制器更是通篇不写 blend_transition。
# 而早先给全部非 parallel 动画都加了 override(复刻 Java 的通道覆盖语义) → 状态机装上了也
# 照旧硬切。**解法**: 主链动画与它前面的 pre 通道(pre_parallelN / player_pre_* 控制器所播)
# 一律不带 override; Java "main 逐通道覆盖 pre_parallel"的语义(geckolib3
# AnimationProcessor: 非 parallel 通道逐通道后写覆盖, parallel 通道旋转相加)改由移植工具
# 在**数据层**剥离(port_java_pack.ApplyJavaMainOverride), 运行层不再插任何归零动画。
# 曾经试过的"归零动画 + override 挂在主链之前"方案(2026-09-03 第四轮)已废弃: 它的观察
# 结论被同期一次整份动画文件遭引擎拒载(空 bones 节点)的事故污染, override 在 animate
# 条目之间的确切语义至今没有可靠实机结论, 运行层设计不再依赖它。
_STATE_ENTER_BLEND = 0.1     # 进入主链/骑乘动画: Java main/vehicle 通道起始过渡 2 tick
_ENDING_BLEND = 0.15         # 备用: 尾过渡 3 tick(仅用于不带 override 的动画淡出)
_STATE_CHAIN_KEY = "ysm_state"
_STATE_CHAIN_EMPTY = "empty"
# Java AnimationRegister 对主链动画强制循环类型: death/attacked PLAY_ONCE, 其余 LOOP
# (动画 JSON 的 loop 字段在主链上被忽略; 手部/轮盘动画才听 JSON)
_JAVA_PLAY_ONCE_STATES = ("death", "attacked")
# 空中组的进入判据(带防抖死区)与"腾空"判据(粘滞用, 无死区)分开两个常量:
# 死区让 jump 在最高点附近判假, 只适合作进入门; 状态机粘滞要用无死区版。
_JUMP_AIRBORNE_TEST = "!query.is_on_ground&&!query.is_in_water"
_JUMP_GROUP_TEST = (_JUMP_AIRBORNE_TEST
                    + "&&(query.vertical_speed>0||query.vertical_speed<-4)")

# 状态"粘滞"判据: 进入判据可以严(防抖), 但**留在该状态**要宽 —— 状态机天然支持
# 进/出条件不对称, 这是直挂 animate 条目做不到的。
# jump: 进入要求上升沿或真下落(挡住走路时 query.is_on_ground 的每秒数次抖动翻转,
# 见 _JAVA_STATE_GROUPS jump 组注), 但**整个腾空期都必须留在 jump** —— 否则最高点
# 附近垂直速度穿过 (0, -4) 的死区(实测 vertical_speed 是格/秒, 重力 32 格/秒² →
# 死区约 3 tick)时 jump 判据变假, 低优先级的 walk/idle 抢过去, 落地前 jump 又重新
# 进入一次: 玩家看到的就是"跳一次触发两遍动画"(用户 2026-09-03 实测报告)。
# 粘滞只压制**更低优先级**的状态与 empty 兜底; 更高优先级(死亡/受击/入水/骑乘…)照旧可打断。
_JAVA_STATE_HOLDS = {
    "jump": _JUMP_AIRBORNE_TEST,
}


def _MemberStateAnimations(key, companions):
    """生成式状态机成员状态的 animations: 原动画 + 它的通道覆盖伴生动画(权重 None → 裸条目)。

    companions: {原动画键: [(伴生键, 权重表达式或 None), ...]}(port_java_pack.ApplyChannelOwnership 规划)。
    """
    animations = [key]
    for companionKey, weight in (companions or {}).get(key, ()):
        animations.append(OrderedDict([(companionKey, weight)]) if weight else companionKey)
    return animations


def _BuildOneShotControllerBody(trigger, members, fallback, loopFlags, enterBlend=None,
                                exitBlend=None, holdWhileTriggered=False, companions=None):
    """一次性通道状态机体: idle → 成员独占状态(播完才走) → cooldown → idle。

    转移按列表顺序取首个成立项 = Java 单通道互斥(id > tag > 分类 > 兜底), 成员判据
    不需要否定链。成员状态出口: 不循环动画 all_animations_finished(Java "挥动不中断
    直至播完", hold_on_last_frame 也算播完); loop:true 动画永不 finished → 触发消失
    即走。cooldown 等触发消失再回 idle: 动画短于触发期时不会二次进入(一次挥击只播
    一次)。状态不写空 animations 数组(整份文件拒载, 见 devtools _PruneControllerBody)。
    enterBlend/exitBlend: 默认都不写 —— 挥击/受击族动画保留 override(Java 跨通道覆盖),
    而 override 会吃掉交叉淡化(见上方长注): 写了淡出反而让手臂先塌到绑定姿态再弹回主链
    姿态, 比直接切更难看。Java 侧 swing 通道的起始过渡本来也是 0。
    """
    idleTransitions = []
    for key, selfTest in members:
        idleTransitions.append(OrderedDict([(key, "{}&&{}".format(trigger, selfTest))]))
    if fallback:
        idleTransitions.append(OrderedDict([(fallback, trigger)]))
    if not idleTransitions:
        return None
    released = _Negate(trigger)
    idleState = OrderedDict([("transitions", idleTransitions)])
    if enterBlend:
        idleState["blend_transition"] = enterBlend
    states = OrderedDict([("idle", idleState)])
    memberKeys = [key for key, _selfTest in members] + ([fallback] if fallback else [])
    # holdWhileTriggered(use 通道): 触发消失才出态 —— 拉满弓/举着盾时动画已
    # all_animations_finished, 按播完出态会当场掉回 idle 让姿态归位。出态**直接回 idle**,
    # 不经 cooldown: cooldown 只在触发消失时才放行, 触发只要闪断一帧又立刻恢复(锁存与原始
    # query 交接、出手瞬间掉线), 状态机就卡在 cooldown 里, 整段使用都没有姿态 —— 2026-09-17
    # 实机逐 tick 采样到"使用中判据为 1、身上却是持物姿态"。停留语义本身已保证不会二次进入。
    afterExit = "idle" if holdWhileTriggered else "cooldown"
    selfTests = dict(members)
    for key in memberKeys:
        exitCondition = released if (holdWhileTriggered
                                     or (loopFlags or {}).get(key) is True) \
            else "query.all_animations_finished"
        if holdWhileTriggered and selfTests.get(key):
            # 停留期间手里的物品不再是本成员(切物品/换手)也出态, 回 idle 按当前物品重新选成员 ——
            # Java UsePredicate 每 tick 按当前物品选动画; 只看触发消失的话, 触发一旦被别的来源顶住
            # (如格挡), 换物品后仍停在旧成员态(实机: 从弓切走后手部定位骨骼仍是拉弓的 2 倍)
            exitCondition = _Negate("{}&&{}".format(trigger, selfTests[key]))
        states[key] = OrderedDict([
            ("animations", _MemberStateAnimations(key, companions)),
            ("transitions", [OrderedDict([(afterExit, exitCondition)])]),
        ])
        if exitBlend:
            states[key]["blend_transition"] = exitBlend
    if not holdWhileTriggered:
        states["cooldown"] = OrderedDict([("transitions", [OrderedDict([("idle", released)])])])
    return OrderedDict([("initial_state", "idle"), ("states", states)])


def _BuildSwingControllerBody(members, fallback, loopFlags, seenVariable, companions=None):
    """挥击通道状态机体(见 _SWING_SERIAL_VARIABLE 注): 新挥动即重播, 使用物品期间开始的挥动静音。

    idle 与每个成员状态(成对: <键> / <键>__re)共用同一组"新挥动"转移, 按列表顺序取首个成立项
    = Java 单通道互斥(id > tag > 分类 > 兜底)。成员状态 on_entry 记下当前挥动序号; 在 <键> 里
    遇到新挥动转去对应成员的 __re 孪生态(反之亦然) —— 进入状态即重置动画时钟 = Java 的
    indicateReload。出口: 不循环动画 **any_animation_finished**(播完即回 idle, Java PLAY_ONCE),
    loop:true 动画在挥动结束后回 idle。idle 的 on_entry 同样记序号, 渲染重建/切视角时不补播旧挥动。
    不写 blend_transition(Java swing 通道过渡 0, 且挥击动画带 override 会吃掉淡化)。
    companions: 成员的通道覆盖伴生动画(带 override, 晚层并行控制器占用该通道时权重精确为 0)。
    **权重 0 的动画暂停计时、且让 all_animations_finished 永不成立**(2026-09-17 实机: 状态里挂一条
    权重 0 的 3 秒动画, 原动画 0.46 秒播完后状态 5 秒不出态, 权重改 1 后从头播满 3 秒才出态) ——
    出口若用 all 会被暂停的伴生卡住。原动画在成员状态里恒为裸条目(权重 1), 伴生与它同长, 只会
    晚播完不会早播完, 故 any_animation_finished 恰等于"原动画播完"。
    """
    memberKeys = [key for key, _selfTest in members] + ([fallback] if fallback else [])
    if not memberKeys:
        return None
    newSwing = "({}??0)!=({}??0)".format(_SWING_SERIAL_VARIABLE, seenVariable)
    record = ["{} = {}??0;".format(seenVariable, _SWING_SERIAL_VARIABLE)]
    selfTests = dict(members)

    def _SwingTransitions(twinSuffix):
        transitions = []
        for key in memberKeys:
            test = selfTests.get(key)
            condition = "{}&&{}".format(newSwing, test) if test else newSwing
            transitions.append(OrderedDict([(key + twinSuffix, condition)]))
        return transitions

    states = OrderedDict([("idle", OrderedDict([
        ("on_entry", list(record)),
        ("transitions", _SwingTransitions("")),
    ]))])
    for key in memberKeys:
        exitCondition = _Negate(_SWING_ACTIVE_TEST) if (loopFlags or {}).get(key) is True \
            else "query.any_animation_finished"
        for suffix, twinSuffix in (("", _SWING_RETRIGGER_SUFFIX), (_SWING_RETRIGGER_SUFFIX, "")):
            states[key + suffix] = OrderedDict([
                ("on_entry", list(record)),
                ("animations", _MemberStateAnimations(key, companions)),
                ("transitions", _SwingTransitions(twinSuffix)
                 + [OrderedDict([("idle", exitCondition)])]),
            ])
    return OrderedDict([("initial_state", "idle"), ("states", states)])


_STATE_CHAIN_GUARD_PREFIX = _NON_GUI_GUARD + "&&!variable.is_first_person&&"


def _StripStateChainGuards(condition):
    """状态链条件去掉主域双门(纸娃娃/第一人称) → 控制器内的触发判据(旁观门保留)"""
    if condition.startswith(_STATE_CHAIN_GUARD_PREFIX):
        return condition[len(_STATE_CHAIN_GUARD_PREFIX):]
    return condition


def BuildOneShotControllers(namespace, mainKeys, fpKeys=(), loopFlags=None, companions=None):
    """移植/修复工具入口: 包的一次性通道控制器 {控制器ID: 控制器体}(见 _ONESHOT_SWING_KEY 注)。

    mainKeys: 主域注册短键全集(主命名空间 + arm 命名空间非 parallel 键, 与解析器注册
    口径一致); fpKeys: fp_ 前缀键全集; loopFlags: 注册键 → 动画 loop 字段值。没有任何
    成员的通道不产控制器(解析器只替换存在的直挂条目)。受击/死亡的触发判据取自状态链
    (含更高优先级状态的否定), 只按本包自有键合成 —— 与运行期(可能并入基线键)在极端
    情况下否定链略有出入, 可接受。
    companions: {成员键: [(伴生键, 权重或 None)]} —— 通道覆盖伴生动画随成员进同一状态
    (port_java_pack.ApplyChannelOwnership 规划; 伴生键本身不当成员, 见 _OneShotConditionMembers)。
    """
    controllers = OrderedDict()

    def _ControllerId(key):
        return "controller.animation.{}.{}".format(namespace, key)

    members, fallback = _OneShotSwingMembers(mainKeys)
    body = _BuildSwingControllerBody(members, fallback, loopFlags,
                                     _SWING_SEEN_VARIABLES[_ONESHOT_SWING_KEY], companions)
    if body:
        controllers[_ControllerId(_ONESHOT_SWING_KEY)] = body
    members, fallback = _OneShotSwingMembers(fpKeys, keyPrefix=_FP_ARM_KEY_PREFIX)
    body = _BuildSwingControllerBody(members, fallback, loopFlags,
                                     _SWING_SEEN_VARIABLES[_ONESHOT_FP_SWING_KEY], companions)
    if body:
        controllers[_ControllerId(_ONESHOT_FP_SWING_KEY)] = body
    for prefix, ctlKey, slot, handIndex, trigger in _ONESHOT_USE_CHANNELS:
        members, fallback = _OneShotConditionMembers(
            mainKeys, prefix, slot, handIndex, prefix)
        body = _BuildOneShotControllerBody(trigger, members, fallback, loopFlags,
                                           holdWhileTriggered=True, companions=companions)
        if body:
            controllers[_ControllerId(ctlKey)] = body
    stateConditions = dict(_BuildJavaStateAnimates(mainKeys))
    for stateKey, ctlKey in _ONESHOT_STATE_KEYS.items():
        condition = stateConditions.get(stateKey)
        if not condition:
            continue
        # 受击/死亡属 Java main 通道: 进入带 0.1s 起始过渡(Java 模式下通常由
        # ysm_state 主链状态机接管, 此处保留给旧通道/无主链文件的包)
        body = _BuildOneShotControllerBody(
            _StripStateChainGuards(condition), [], stateKey, loopFlags,
            enterBlend=_STATE_ENTER_BLEND, companions=companions)
        if body:
            controllers[_ControllerId(ctlKey)] = body
    return controllers


def BuildStateChainController(namespace, mainKeys, blend=_STATE_ENTER_BLEND, companions=None,
                              ownershipVariable=None, ownershipStatements=None):
    """移植/修复工具入口: Java 主链状态机 controller.animation.<ns>.ysm_state 的控制器体。

    把 _BuildJavaStateAnimates 合成的互斥条件表搬进一个状态机: 每个状态动画一个独占
    状态, 转移条件即该动画的互斥判据(去掉纸娃娃/第一人称双门 —— 那是控制器条目的
    animate 门), 每个状态 blend_transition=blend(缺省 0.1s = Java main/vehicle 通道的
    起始过渡); 直挂条目做不到的交叉淡化由引擎在状态切换时完成 —— 前提是状态动画**不带**
    override_previous_animation(见 _STATE_ENTER_BLEND 上方长注: 覆盖标志会吃掉淡化)。
    initial_state 是空状态 empty(Java 播放器 IDLE 态: 无动画命中时不输出骨骼), 各状态
    末尾转移 empty(自身判据失效且无他态命中, 如骑乘未知载具而包无 sit)。
    death/attacked(Java 强制 PLAY_ONCE)由移植工具改为 hold_on_last_frame: 判据成立期间
    停在末帧不重播(等价 Java setAnimation 同名不重启), 判据翻转后直接交叉淡化进下一状态
    —— 即 Java 的尾过渡, 无需额外的 _done 空状态(那会让姿态先淡到绑定姿态再淡进新状态)。
    _JAVA_STATE_HOLDS 里的状态带粘滞: 只压制更低优先级成员与 empty 兜底(见该常量注)。
    mainKeys 口径与 BuildOneShotControllers 一致(包自有主域键); 无主链成员 → None。
    companions: {主链键: [(伴生动画键, 权重表达式或 None)]} —— 移植工具拆出的"通道覆盖伴生动画"
    (port_java_pack.ApplyChannelOwnership), 与主链动画同状态、同时起播, 权重为"晚层写入者不活跃"。
    ownershipVariable: 主链作为晚层写入者时的占用变量(如 variable.ysm_own_ysm_state) —— 每个
    状态 on_entry 写入自己的序号(empty 为 0, 成员按状态序从 1 起), 早层伴生动画的权重据此判断
    主链当前在哪个状态。序号口径必须与移植工具一致(同一 _BuildJavaStateAnimates 顺序)。
    ownershipStatements: 紧随序号赋值的集合预算语句(移植工具按"晚层状态集合"生成, 进入状态时
    重算一次, 伴生权重只读结果)。
    """
    members = _BuildJavaStateAnimates(list(mainKeys))
    if not members:
        return None
    stripped = [(key, _StripStateChainGuards(condition)) for key, condition in members]

    states = OrderedDict()
    states[_STATE_CHAIN_EMPTY] = OrderedDict([
        ("transitions", [OrderedDict([(key, condition)]) for key, condition in stripped]),
        ("blend_transition", blend),
    ])
    for index, (key, condition) in enumerate(stripped):
        hold = _JAVA_STATE_HOLDS.get(key)
        transitions = []
        for otherIndex, (otherKey, otherCondition) in enumerate(stripped):
            if otherKey == key:
                continue
            if hold and otherIndex > index:
                # 粘滞: 更低优先级的状态要等粘滞判据失效(落地/入水)才能接手
                otherCondition = "{}&&{}".format(_Negate(hold), otherCondition)
            transitions.append(OrderedDict([(otherKey, otherCondition)]))
        exitCondition = _Negate(condition)
        if hold:
            exitCondition = "{}&&{}".format(_Negate(hold), exitCondition)
        transitions.append(OrderedDict([(_STATE_CHAIN_EMPTY, exitCondition)]))
        states[key] = OrderedDict([
            ("animations", _MemberStateAnimations(key, companions)),
            ("transitions", transitions),
            ("blend_transition", blend),
        ])
    if ownershipVariable:
        for stateIndex, state in enumerate(states.values()):
            state["on_entry"] = (["{} = {};".format(ownershipVariable, stateIndex)]
                                 + list(ownershipStatements or []))
    return OrderedDict([("initial_state", _STATE_CHAIN_EMPTY), ("states", states)])


# 骨骼通道名及其"无偏移"值(rotation/position 零, scale 一)
_BONE_CHANNEL_NEUTRAL = OrderedDict([("rotation", [0.0, 0.0, 0.0]),
                                     ("position", [0.0, 0.0, 0.0]),
                                     ("scale", [1.0, 1.0, 1.0])])


def AnimationChannelPairs(body):
    """一条动画体 → 它写过的 {(骨骼, 通道)} 集合(只认 rotation/position/scale)"""
    pairs = set()
    if not isinstance(body, dict):
        return pairs
    for boneName, channels in (body.get("bones") or {}).items():
        if not isinstance(channels, dict):
            continue
        for channel in channels:
            if channel in _BONE_CHANNEL_NEUTRAL:
                pairs.add((boneName, channel))
    return pairs


def JavaStateLoopType(key):
    """Java 主链对状态动画强制的循环类型(AnimationRegister, 动画 JSON 的 loop 被忽略):
    death/attacked → "once"; 其余主链成员(含 vehicle$ 条件键) → "loop"; 非成员 → None。
    移植工具据此改写动画文件的 loop 字段(Java 包常把 sleep/sit 等写成不循环, 基岩
    听 JSON 会播一遍就停)。"""
    escaped = EscapeConditionKey(key)
    if escaped in _JAVA_PLAY_ONCE_STATES:
        return "once"
    if escaped in _JAVA_STATE_MEMBER_KEYS:
        return "loop"
    javaKey = _UnescapeConditionKey(escaped)
    if javaKey.startswith(_VEHICLE_CONDITION_PREFIX + "$") and len(javaKey) > len(_VEHICLE_CONDITION_PREFIX) + 1:
        return "loop"
    return None


def _IsMainSwingKey(key):
    """主手挥击族注册键(swing$/#/: 两种形态或兜底 swing_hand); 副手族不算"""
    if key == "swing_hand":
        return True
    parsed = _SplitConditionKey(key)
    return parsed is not None and parsed[0][0] == "swing"


def _SubstituteOneShot(entries, isTarget, ctlKey, gate):
    """直挂条目里 isTarget 命中者整体换成一条控制器条目(落在首个命中的位置) → (新表, 是否命中)"""
    kept, hit = [], False
    for key, condition in entries:
        if isTarget(key):
            if not hit:
                kept.append((ctlKey, gate))
            hit = True
            continue
        kept.append((key, condition))
    return kept, hit


def _ApplyOneShotControllers(namespace, ownKeys, ctlEntries, conditionalAnimates,
                             fpArmAnimates, stateAnimates):
    """资源索引里有本包的一次性通道控制器时, 用它替换对应直挂条目(见 _ONESHOT_SWING_KEY 注)。

    返回 (控制器条目, 条件动画条目, FP 手臂条目, 状态链条目); 索引不可用/无文件时原样返回。
    兜底 swing_hand 只在**本包自有**时才被替换: 生成器只按本包键合成, 基线借来的
    swing_hand 不在控制器里, 替掉就没兜底了。
    """
    index = _GetResourceIndex()
    if index is None:
        return ctlEntries, conditionalAnimates, fpArmAnimates, stateAnimates
    available = set(resId for _key, resId in index.QueryNamespaceControllers(namespace))
    if not available:
        return ctlEntries, conditionalAnimates, fpArmAnimates, stateAnimates
    ctlEntries = list(ctlEntries)

    def _ControllerId(key):
        return "controller.animation.{}.{}".format(namespace, key)

    def _Covered(key, ctlId):
        """直挂条目能否交给该状态机: 包自有键, 或状态机确实引用了它(Java 默认模型基线键由移植工具
        并进状态机成员; 旧产物的状态机不认识基线键, 那些键留作直挂, 至少还能播)"""
        if key in ownKeys:
            return True
        refs = index.QueryControllerAnimRefs(ctlId) if hasattr(index, "QueryControllerAnimRefs") else None
        return refs is not None and key in refs

    # Java 主链状态机(ysm_state): 包自有的主链成员整体交给状态机(带 0.1s 交叉淡化),
    # 直挂条目只保留基线借来的成员(状态机文件按包自有键生成, 不认识基线键);
    # 标准状态直挂(death/ladder 族, _STANDARD_STATE_CONDITIONS)与主链同键者一并摘除,
    # 否则直挂与状态机双驱。受击/死亡随主链进状态机, 下方的 ysm_attacked/ysm_death
    # 因条目已被摘走而不再注册。
    stateId = _ControllerId(_STATE_CHAIN_KEY)
    if stateAnimates and stateId in available:
        stateRefs = index.QueryControllerAnimRefs(stateId) if hasattr(index, "QueryControllerAnimRefs") else None
        chainKeys = set(ownKeys) | set(key for key in (stateRefs or ()) if not _IsOwnershipCompanionKey(key))
        ownChainKeys = set(key for key, _cond in _BuildJavaStateAnimates(sorted(chainKeys)))
        if ownChainKeys:
            # 伴生动画(<键>__own<N>)随原键一起交给状态机(状态机文件里与原动画同状态播放)
            stateAnimates = [(_STATE_CHAIN_KEY, _CONTROLLER_ANIMATE_GATE)] + [
                entry for entry in stateAnimates
                if _CompanionBaseKey(entry[0]) not in ownChainKeys]
            conditionalAnimates = [
                entry for entry in conditionalAnimates
                if _CompanionBaseKey(entry[0]) not in ownChainKeys]
            ctlEntries.append((_STATE_CHAIN_KEY, stateId))

    # 以下各通道的判定都按原动画键: 伴生条目与原条目一起换成控制器条目(伴生已在状态机状态里)
    swingId = _ControllerId(_ONESHOT_SWING_KEY)

    def _IsOwnMainSwing(key):
        key = _CompanionBaseKey(key)
        return _IsMainSwingKey(key) and _Covered(key, swingId)
    if swingId in available:
        conditionalAnimates, hit = _SubstituteOneShot(
            conditionalAnimates, _IsOwnMainSwing, _ONESHOT_SWING_KEY, _CONTROLLER_ANIMATE_GATE)
        if hit:
            ctlEntries.append((_ONESHOT_SWING_KEY, swingId))
    fpSwingId = _ControllerId(_ONESHOT_FP_SWING_KEY)
    if fpSwingId in available:
        fpArmAnimates, hit = _SubstituteOneShot(
            fpArmAnimates,
            lambda key: key.startswith(_FP_ARM_KEY_PREFIX)
            and _IsMainSwingKey(_CompanionBaseKey(key)[len(_FP_ARM_KEY_PREFIX):]),
            _ONESHOT_FP_SWING_KEY, _NON_GUI_GUARD + "&&" + _FP_ARM_GATE)
        if hit:
            ctlEntries.append((_ONESHOT_FP_SWING_KEY, fpSwingId))
    # use 通道: 直挂条目不重放(见 _ONESHOT_USE_CHANNELS 注), 整族交给状态机
    for prefix, ctlKey, _slot, _handIndex, _trigger in _ONESHOT_USE_CHANNELS:
        useId = _ControllerId(ctlKey)
        if useId not in available:
            continue

        def _IsUseChannelKey(key, target=prefix, ctlId=useId):
            key = _CompanionBaseKey(key)
            if key == target:                    # 无命中兜底键(use_mainhand/use_offhand)
                return _Covered(key, ctlId)
            parsed = _SplitConditionKey(key)
            return parsed is not None and parsed[0][0] == target and _Covered(key, ctlId)

        conditionalAnimates, hit = _SubstituteOneShot(
            conditionalAnimates, _IsUseChannelKey, ctlKey, _CONTROLLER_ANIMATE_GATE)
        if hit:
            ctlEntries.append((ctlKey, useId))
    for stateKey, ctlKey in _ONESHOT_STATE_KEYS.items():
        ctlId = _ControllerId(ctlKey)
        if ctlId not in available or stateKey not in ownKeys:
            continue
        isState = (lambda key, target=stateKey: _CompanionBaseKey(key) == target)
        stateAnimates, hitState = _SubstituteOneShot(
            stateAnimates, isState, ctlKey, _CONTROLLER_ANIMATE_GATE)
        conditionalAnimates, hitCond = _SubstituteOneShot(
            conditionalAnimates, isState, ctlKey, _CONTROLLER_ANIMATE_GATE)
        if hitState and hitCond:
            # death 在状态链与标准状态两处都有(状态链带完整互斥且更靠前), 控制器条目只留那份
            conditionalAnimates = [entry for entry in conditionalAnimates if entry[0] != ctlKey]
        if hitState or hitCond:
            ctlEntries.append((ctlKey, ctlId))
    return ctlEntries, conditionalAnimates, fpArmAnimates, stateAnimates


def _ReadControllerFileEntries(relPath, readTextFunc, warnings, foundVars=None):
    """读取单个 BP 副本控制器文件 → ([(键, 控制器ID)...], [(键, "1")...], 是否读到)。

    控制器 ID 从文件顶层 animation_controllers 段提取, 注册键取 ID 末段;
    Java geckolib 控制器加载即常开 → animate 条件默认 "1"。文件缺席时静默。
    玩家侧(files.player.animation_controllers)与替换实体侧(entry.controller)共用。
    """
    if not isinstance(relPath, str) or not relPath or readTextFunc is None:
        return [], [], False
    text = readTextFunc(relPath)
    if not text:
        return [], [], False
    try:
        if not isinstance(text, str):
            text = text.encode("utf-8")
        data = ByteifyJson(json.loads(text.decode("utf-8-sig"), object_pairs_hook=OrderedDict))
    except ValueError as e:
        warnings.append("{} JSON 语法错误, 无法自动注册控制器: {}".format(relPath, e))
        return [], [], False
    section = data.get("animation_controllers") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        warnings.append("{} 缺少 animation_controllers 段".format(relPath))
        return [], [], False
    if foundVars is not None:
        foundVars.update(_MOLANG_VAR_PATTERN.findall(text))
    ctlEntries = []
    animateEntries = []
    for ctlId in _OrderedKeys(section):
        key = ctlId.split(".")[-1]
        ctlEntries.append((key, ctlId))
        animateEntries.append((key, "1"))
    return ctlEntries, animateEntries, True


def _LoadDeclaredControllers(jsonDict, animNs, readTextFunc, warnings, foundVars=None):
    """模型自有的动画控制器 → 自动注册(常开)。

    **主来源是资源包索引**(controller.animation.<命名空间>.* 全集), 创作者把控制器
    放进资源包一份即可; 索引不可用时回落读行为包内声明路径的副本文件。
    返回 (控制器条目, animate 条目, 是否取到任一控制器)。
    """
    ctlEntries = []
    animateEntries = []
    loadedAny = False
    decl = ((jsonDict.get("files") or {}).get("player") or {}).get("animation_controllers")
    if not isinstance(decl, list) or not decl:
        # 未声明控制器文件的包保持原样(基线控制器由 BASE_ANIMATION_CONTROLLERS 提供)
        return ctlEntries, animateEntries, loadedAny

    # 列表混排(Java 原生 = 纯路径列表; 基岩扩展 = 额外接受 dict 逐条注册):
    # dict {"注册键": "控制器ID"} —— 显式键名, 用于共享控制器/键名兼容场景
    pathItems = []
    for item in decl:
        if isinstance(item, dict):
            for key, ctlId in _AsEntryList(item):
                ctlEntries.append((key, ctlId))
                animateEntries.append((key, "1"))
            loadedAny = True
        elif isinstance(item, str) and item:
            pathItems.append(item)

    index = _GetResourceIndex()
    if index is not None and pathItems:
        # 路径解析模式优先: 声明文件内全部控制器精确注册(键=ID 末段, 常开)
        pathLoaded = False
        for declPath in pathItems:
            fileCtls, fileAnimates, loaded = _QueryIndexFileControllers(
                declPath, animNs, foundVars)
            ctlEntries += fileCtls
            animateEntries += fileAnimates
            pathLoaded = pathLoaded or loaded
        if pathLoaded:
            return ctlEntries, animateEntries, True
        indexed = index.QueryNamespaceControllers(animNs)
        if indexed:
            if foundVars is not None:
                foundVars.update(index.QueryNamespaceVariables(animNs))
            # Java geckolib 控制器加载即常开 → animate 条件默认 "1"
            ctlEntries += [(key, ctlId) for key, ctlId in indexed]
            animateEntries += [(key, "1") for key, _ctlId in indexed]
            return ctlEntries, animateEntries, True

    if readTextFunc is None or not pathItems:
        return ctlEntries, animateEntries, loadedAny
    for relPath in pathItems:
        fileCtls, fileAnimates, loaded = _ReadControllerFileEntries(
            relPath, readTextFunc, warnings, foundVars)
        ctlEntries += fileCtls
        animateEntries += fileAnimates
        loadedAny = loadedAny or loaded
    return ctlEntries, animateEntries, loadedAny


def _ItemNameTest(slot, names):
    """query.is_item_name_any(槽位, 名...) 表达式"""
    return "query.is_item_name_any('{}',{})".format(
        slot, ",".join(["'{}'".format(n) for n in names]))


def _ItemTagTest(slot, tags):
    """query.equipped_item_any_tag(槽位, tag...) 表达式。

    本函数是全部 equipped_item_any_tag 合成的唯一出口(条件动画 / 挥击控制器 /
    移植工具的 ctrl.* 展开都经它), Java 原版 tag 名在此换成基岩内置名
    (见 _JAVA_TO_BEDROCK_ITEM_TAGS)。**不要在这里掺 query.is_item_name_any 兜底** ——
    动画通道没有实体上下文, 掺进去整条通道作废(见上方长注)。
    """
    return "query.equipped_item_any_tag('{}',{})".format(
        slot, ",".join(["'{}'".format(MapJavaItemTag(t)) for t in tags]))


def _ClassifySelfTest(test, slot, handIndex):
    """_CLASSIFY_TESTS 表项 → 基岩判定表达式(三处合成共用: 直挂条目/挥击控制器/ctrl.*)

    charged: Java 是 item.is(CROSSBOW) && CrossbowItem.isCharged(item) 两个条件,
    照抄成 具名判定 && item_is_charged —— 单看 item_is_charged 判定域偏宽。
    """
    if test[0] == "tag":
        return _ItemTagTest(slot, [test[1]])
    if test[0] == "charged":
        return "({}&&query.item_is_charged({}))".format(
            _ItemNameTest(slot, [test[1]]), handIndex or 0)
    return _ItemNameTest(slot, list(test[1:]))


def _ChannelSortKey(prefixItem):
    """条件动画分组 → Java 通道排序键(见 _CONDITION_CHANNEL_ORDER)"""
    return _CONDITION_CHANNEL_ORDER.get(prefixItem[0], 80)


# 条件动画名的**引擎安全转义**形态(实测: 基岩动画解析器拒绝 ID 里的 $ 与 :,
# 且一个非法 ID 会让整份动画文件的 animations 段 "node parse failed" 全废):
#   hold_mainhand$minecraft:diamond_sword → hold_mainhand.id.minecraft.diamond_sword
#   hold_mainhand#minecraft:swords        → hold_mainhand.tag.minecraft.swords
#   hold_mainhand:sword                   → hold_mainhand.cls.sword
# 移植工具按此改写动画 ID; 解析时还原回 Java 语义再走同一套条件合成。
_ESCAPED_KIND_SEPARATORS = (("id", "$"), ("tag", "#"), ("cls", ":"))


def EscapeConditionKey(key):
    """Java 条件动画短名 → 引擎安全形态(移植工具与解析器共用同一套规则)。

    前缀段的冒号也必须折叠(TACZ 键形如 tac:hold:fire$tacz:minigun, 前缀天生带
    冒号) —— 残留一个冒号就触发引擎红线: 该 ID 非法 → 整份动画文件 node parse
    failed 全废, 且这种短键经 AddPlayerAnimation 注册会毒化玩家实体的动画表。
    这类前缀不在条件表里, 还原后仅作普通键注册, 前缀冒号无需可逆。

    统一转小写: 引擎红线之二 —— 动画\控制器等资源 key 不接受英文大写(骨骼名除外,
    骨骼按大小写不敏感匹配)。Java 允许大写短名(wine_fox/18_wedding 有 22 个,
    如 PefectDef\walkBack), 移植落盘与解析合成必须走同一小写形态。
    """
    key = key.lower()
    for kind, sep in _ESCAPED_KIND_SEPARATORS:
        index = key.find(sep)
        if index <= 0:
            continue
        return "{}.{}.{}".format(
            key[:index].replace(":", "."), kind, key[index + 1:].replace(":", ".")
        )
    return key


def _UnescapeConditionKey(key):
    """引擎安全形态 → Java 条件动画短名; 非转义键原样返回"""
    for kind, sep in _ESCAPED_KIND_SEPARATORS:
        marker = ".{}.".format(kind)
        index = key.find(marker)
        if index <= 0:
            continue
        prefix, rest = key[:index], key[index + len(marker):]
        if kind == "cls":
            return prefix + sep + rest
        # id/tag: 首个 . 还原为资源命名空间分隔符 :
        parts = rest.split(".", 1)
        return prefix + sep + (parts[0] + ":" + parts[1] if len(parts) > 1 else parts[0])
    return key


def _SplitConditionKey(key):
    """条件动画短键 → (前缀项, 分隔符, 参数); 非条件键返回 None。

    同时接受 Java 原形态($ # :)与引擎安全转义形态(.id. .tag. .cls.)。
    """
    key = _UnescapeConditionKey(key)
    for prefix, slot, handIndex, gate in _CONDITION_PREFIXES:
        for sep in ("$", "#", ":"):
            head = prefix + sep
            if key.startswith(head) and len(key) > len(head):
                return (prefix, slot, handIndex, gate), sep, key[len(head):]
    return None


# swing/use 通道的"无条件命中"兜底动画键(Java SwingPredicate/UsePredicate:
# 条件动画全不命中时主手挥击播 swing_hand、使用物品播 use_mainhand)。
# swing_offhand/use_offhand 不合成: 基岩无副手挥击事件, is_using_item 也不分手。
_CONDITION_FALLBACK_KEYS = [
    # (兜底动画键, 所属条件前缀, 兜底自身门控)
    ("swing_hand", "swing", _SWING_ACTIVE_TEST),
    ("use_mainhand", "use_mainhand", _USE_MAINHAND_GATE),
    ("use_offhand", "use_offhand", _USE_OFFHAND_GATE),
]


def _BuildConditionalAnimates(animKeys, warnings, keyPrefix="", extraGate=None,
                              withFallbacks=False, quietKeys=None,
                              personGuard=_THIRD_PERSON_GUARD, javaMode=False):
    """条件动画键 → animate 条件条目 [(键, molang条件), ...](对齐 Java ConditionManager)。

    Java 把条件编码在动画名里(hold_mainhand$minecraft:diamond_sword / #tag / :分类),
    每 tick 由代码匹配当前装备后播放对应动画, 同一通道内**互斥**且优先级
    id($) > tag(#) > 分类(:); 空手回落 "<前缀>:empty"。
    基岩有等价物品查询, 故按同一优先级合成互斥条件表达式, 交由引擎逐帧求值。

    keyPrefix/extraGate 供第一人称手臂通道复用: animKeys 传入剥前缀后的短键,
    产出条目键补回 keyPrefix, 条件末尾统一 && extraGate(如 FP 门控),
    并以 personGuard=None 关掉主域的 !is_first_person 门(FP 通道是正向门)。
    withFallbacks(Java 模式): swing/use 条件全不命中时兜底 swing_hand/use_mainhand/
    use_offhand(Java SwingPredicate / UsePredicate 末尾的 playCompatAnimation)。
    quietKeys: 来自共享基线的键集 —— 其未映射分类只在基线包自己解析时告警一次,
    不随每个引用基线的模型重复刷。
    """
    guards = [_NON_GUI_GUARD] + ([personGuard] if personGuard else [])
    entries = []
    unmappedClassifies = set()   # 基岩无对应的分类(多为模组物品), 末尾汇总告警一次
    unmappedTags = set()         # minecraft: 命名空间但无基岩对应名的 tag(判定恒假)
    groups = OrderedDict()   # (前缀,槽位,手序,门控) → {"$": [参数...], "#": [...], ":": [...]}
    # 伴生动画(<键>__own<N>)不是条件键: 由 _AttachOwnershipCompanions 紧跟原条目挂上
    animKeys = [key for key in animKeys if not _IsOwnershipCompanionKey(key)]
    for key in animKeys:
        parsed = _SplitConditionKey(key)
        if parsed is None:
            continue
        prefixItem, sep, param = parsed
        group = groups.setdefault(prefixItem, OrderedDict([("$", []), ("#", []), (":", [])]))
        group[sep].append((key, param))

    availableKeys = set(animKeys)
    fallbackNegations = {}   # 条件前缀 → [该通道全部自测试](兜底否定链)

    skippedUnsupported = []
    for prefixItem in sorted(groups, key=_ChannelSortKey):
        prefix, slot, handIndex, gate = prefixItem
        group = groups[prefixItem]
        if prefix in _UNSUPPORTED_CONDITION_PREFIXES:
            if not keyPrefix:            # 主域告警一次, FP 手臂通道不重复刷
                skippedUnsupported += [key for sepEntries in group.values()
                                       for key, _param in sepEntries]
            continue
        allIds = [param for _key, param in group["$"]]
        allTags = [param for _key, param in group["#"]]
        unmappedTags.update(tag for tag in allTags if IsUnmappedJavaItemTag(tag))
        # 高优先级项的否定(Java 单通道互斥: id 命中则 tag/分类 不播)
        notIds = "!" + _ItemNameTest(slot, allIds) if allIds else None
        notTags = "!" + _ItemTagTest(slot, allTags) if allTags else None
        channelTests = fallbackNegations.setdefault(prefix, [])
        if allIds:
            channelTests.append(_ItemNameTest(slot, allIds))
        if allTags:
            channelTests.append(_ItemTagTest(slot, allTags))

        def _Emit(key, parts):
            expr = "&&".join(guards + [p for p in parts if p])
            entries.append((keyPrefix + key, expr))

        # 分类项先整体解析, 再按 Java 判定顺序排序: 代码级层(仅 hold 通道)压住整条
        # 条件链, 其余分类互相之间只否定"判定域真的重叠"的更高优先级项(避免给每条
        # 条件都挂上十几个 query 否定 —— 逐帧求值的开销与可读性都受不了)。
        codeLevel, classified, emptyKeys = [], [], []
        for key, classify in group[":"]:
            if classify == "empty":
                if handIndex is None:
                    warnings.append("{}:empty 仅适用于手部条件动画, 已跳过".format(prefix))
                    continue
                emptyKeys.append(key)
                continue
            test = _CLASSIFY_TESTS.get(classify)
            if test is None:
                if not (quietKeys and key in quietKeys):
                    unmappedClassifies.add(classify)  # 汇总告警, 不逐条刷屏
                continue
            item = (key, classify, test, _ClassifySelfTest(test, slot, handIndex))
            if prefix.startswith("hold_") and classify in _CODE_LEVEL_CLASSIFIES:
                codeLevel.append(item)
            else:
                classified.append(item)
        codeLevel.sort(key=lambda entry: _ClassifyRank(entry[1]))
        classified.sort(key=lambda entry: _ClassifyRank(entry[1]))
        notCodeLevel = ["!" + item[3] for item in codeLevel]

        for index, (key, _classify, test, selfTest) in enumerate(codeLevel):
            higher = ["!" + other[3] for other in codeLevel[:index]
                      if _ClassifyTestsOverlap(other[2], test)]
            channelTests.append(selfTest)
            _Emit(key, [selfTest] + higher + [gate, extraGate])
        for key, itemId in group["$"]:
            _Emit(key, [_ItemNameTest(slot, [itemId])] + notCodeLevel
                  + [gate, extraGate])
        for key, tag in group["#"]:
            _Emit(key, [_ItemTagTest(slot, [tag]), notIds] + notCodeLevel
                  + [gate, extraGate])
        for key in emptyKeys:
            emptyTest = "!query.is_item_equipped({})".format(handIndex)
            channelTests.append(emptyTest)
            _Emit(key, [emptyTest, gate, extraGate])   # 空手与物品判据天然互斥
        for index, (key, _classify, test, selfTest) in enumerate(classified):
            higher = ["!" + other[3] for other in classified[:index]
                      if _ClassifyTestsOverlap(other[2], test)]
            channelTests.append(selfTest)
            _Emit(key, [selfTest, notIds, notTags] + notCodeLevel + higher
                  + [gate, extraGate])

    # 共享基线(Java 默认模型)自带的 swing_offhand 族不告警: 每个 Java 模式包都会并入, 基线自身解析时已告警过
    skippedUnsupported = [key for key in skippedUnsupported if not (quietKeys and key in quietKeys)]
    if skippedUnsupported:
        warnings.append("基岩无副手挥击信号, 已跳过 {} 条 swing_offhand 条件动画({}) "
                        "—— 挂在主手挥击上会在主手攻击时乱播".format(
                            len(skippedUnsupported), ", ".join(sorted(skippedUnsupported)[:4])))

    if withFallbacks:
        for fallbackKey, channelPrefix, fallbackGate in _CONDITION_FALLBACK_KEYS:
            if fallbackKey not in availableKeys:
                continue
            parts = guards + [fallbackGate]
            parts += ["!" + test for test in fallbackNegations.get(channelPrefix, [])]
            if extraGate:
                parts.append(extraGate)
            entries.append((keyPrefix + fallbackKey, "&&".join(parts)))

    # vehicle$<实体ID>: 按骑乘实体类型判定(Java ConditionalVehicle)
    head = _VEHICLE_CONDITION_PREFIX + "$"
    for key in animKeys:
        javaKey = _UnescapeConditionKey(key)
        if javaKey.startswith(head) and len(javaKey) > len(head):
            entityId = JAVA_TO_BEDROCK_ENTITY_IDS.get(javaKey[len(head):], javaKey[len(head):])
            condition = "&&".join(guards + [
                "query.is_riding_any_entity_of_type('{}')".format(entityId)])
            if extraGate:
                condition = "{}&&{}".format(condition, extraGate)
            entries.append((keyPrefix + key, condition))

    # CarryOn 搬运条件动画: 转义形态(carryon.cls.*)恒合成 —— 旧基线 carryon 控制器
    # 引用的是 carryon_block 命名, 不会驱动它; 下划线形态(教程 5.6 的改名法)仅 Java
    # 模式合成(旧通道由基线 carryon_ctl 驱动, 再合成即双驱)。princess 在 Java 模式由
    # 状态链的骑乘组带互斥驱动(carryon:princess 成员), 转义形态此处不重复合成。
    for key in animKeys:
        javaKey = _UnescapeConditionKey(key)
        carryKind = None
        underscoreForm = False
        if javaKey.startswith("carryon:"):
            carryKind = javaKey[len("carryon:"):]
        elif javaMode and key.startswith("carryon_"):
            carryKind = key[len("carryon_"):]
            underscoreForm = True
        if carryKind not in _CARRYON_CONDITIONS:
            continue
        if carryKind == "princess" and javaMode and not underscoreForm:
            continue
        condition = "&&".join(guards + [_CARRYON_CONDITIONS[carryKind]])
        if extraGate:
            condition = "{}&&{}".format(condition, extraGate)
        entries.append((keyPrefix + key, condition))

    # 主包基础控制器未驱动的标准状态(死亡/爬梯): 模型提供了同名动画才补条件
    seenKeys = set(animKeys)
    for stateKey in _STANDARD_STATE_CONDITIONS:
        if stateKey in seenKeys:
            condition = "&&".join(guards + [_STANDARD_STATE_CONDITIONS[stateKey]])
            if extraGate:
                condition = "{}&&{}".format(condition, extraGate)
            entries.append((keyPrefix + stateKey, condition))
    if unmappedClassifies:
        warnings.append(
            "{} 个物品分类在基岩无对应判定(模组专属), 相关条件动画不会自动播放: {}".format(
                len(unmappedClassifies), ", ".join(sorted(unmappedClassifies))))
    if unmappedTags:
        warnings.append(
            "{} 个 Java 原版物品 tag 在基岩没有对应内置 tag, 判定恒假(相关条件动画不会播放): "
            "{} —— 需要的话在 _JAVA_TO_BEDROCK_ITEM_TAGS 补映射".format(
                len(unmappedTags), ", ".join(sorted(unmappedTags))))
    return entries


# ============ Java 状态动画驱动(按 files.player.animation 的声明形态自动判定) ============
# Java 的 AnimationRegister 每 tick 按优先级选中**唯一**状态动画播放, 状态名即动画
# 短名(idle/walk/run/...)。网易旧版是另一套约定: 播放由 controller.animation.ysm.*
# 状态机驱动, 引用 idle_0/idle_1/idle_timer 等旧键 —— 这些键 Java 包不提供
# (实机核实: 注册表里全为 None, 连内置模型与旧 py 副包也没有), 于是 Java 包的
# idle 永远不播, 只剩共享 base_pose 站姿, 观感"还是旧版"。
# 这里按 Java 优先级合成**互斥的 molang 条件**驱动动画 —— 与本项目条件动画同一
# 思路: 零运行时开销, 创作者可用 animate_extra 同键覆盖。条件既可直挂 animate
# (零过渡硬切换, 无主链文件的旧产物), 也是 BuildStateChainController 生成
# ysm_state 状态机的转移判据(移植工具产出, 状态切换带 Java 同款 0.1s 交叉淡化)。
#
# 结构: (组前提, [(动画短键, 组内附加判据), ...]); 组内最后一项判据 None = 组兜底。
# 合成: 组条件 = 公共门 && 组前提 && !(所有更高优先级组前提)
#       项条件 = 组条件 && 附加判据 && !(同组更高优先级附加判据)
# 只为模型**实际提供**的动画键生成条件, 否定链也只含模型提供的项(缺 walk 的模型
# 移动时不会被 walk 判据挡住 idle)。
#
# 顺序/判据严格对齐 Java AnimationRegister + PlayerMainPredicate(dev/1.20 源码):
# death > 骑乘链(Java 载具上 main 整体让位 vehicle 控制器) > sleep > swim(泳姿)
# > climb/climbing(SWIMMING pose 落地=爬行, 基岩 is_crawling) > ladder 三态
# > fly(创造飞行, 主包 ysm_is_flying) > elytra_fly > swim_stand(踩水, NORMAL)
# > attacked(受击 hurtTime>0, NORMAL) > jump(!onGround&&!inWater) > sneak(潜行移动)
# /sneaking(潜行兜底) > run(isSprinting)/walk(移动量>0.05)/idle。
# "移动中"阈值 0.05 对齐 Java MIN_SPEED(limbSwingAmount 域 ≈ modified_move_speed)。
# riptide 不纳入: 基岩无 is_auto_spin_attack(实测 expression not valid)。
_RIDE_SADDLE_TEST = ("query.is_riding_any_entity_of_type('minecraft:horse',"
                     "'minecraft:donkey','minecraft:mule','minecraft:skeleton_horse',"
                     "'minecraft:zombie_horse')")
_JAVA_RIDING_GROUP_TEST = "query.is_riding"
_JAVA_STATE_GROUPS = [
    ("query.death_ticks>0", [("death", None)]),
    # 骑乘链(Java VehiclePredicate): vehicle$ 条件动画(动态成员, 见
    # _BuildJavaStateAnimates)> ride_pig(猪/炽足兽) > ride(鞍乘马族) > boat
    # > carryon:princess(被玩家抱起=骑玩家, 主包 riding 值 5) > sit 万用兜底
    (_JAVA_RIDING_GROUP_TEST, [
        ("ride_pig", "query.is_riding_any_entity_of_type('minecraft:pig',"
                     "'minecraft:strider')"),
        ("ride", _RIDE_SADDLE_TEST),
        ("boat", "query.is_riding_any_entity_of_type('minecraft:boat',"
                 "'minecraft:chest_boat')"),
        ("carryon:princess", "query.mod.ysm_riding==5"),
        ("sit", None),
    ]),
    ("query.is_sleeping", [("sleep", None)]),
    ("query.is_swimming", [("swim", None)]),
    ("query.is_crawling", [
        ("climb", "query.modified_move_speed>0.05"),
        ("climbing", None),
    ]),
    ("query.mod.ysm_is_on_ladder>0.5", [
        ("ladder_up", "query.mod.ysm_climbing_vector>0"),
        ("ladder_stillness", "query.mod.ysm_climbing_vector==0"),
        ("ladder_down", None),
    ]),
    ("query.mod.ysm_is_flying>0.5", [("fly", None)]),
    ("query.is_gliding", [("elytra_fly", None)]),
    ("query.is_in_water&&!query.is_on_ground", [("swim_stand", None)]),
    ("query.hurt_time>0", [("attacked", None)]),
    # 空中(Java: !onGround && !inWater)。**必须叠垂直速度门**: 基岩的
    # query.is_on_ground 在走路时会每秒翻转数次(游戏内实测 40 帧翻 10 次),
    # 裸用会让 jump 与 walk 每秒对切数次 —— 零过渡硬切换下就是剧烈抽搐。
    # 判据取自旧版 controller.animation.ysm.jump 的实战写法(上升沿 >0),
    # 另放行真实下落(实测走路误触帧的垂直速度约 -1.6, 故阈值取 -4)。
    # 该死区只作**进入**判据; 留在空中靠状态机粘滞(见 _JAVA_STATE_HOLDS)。
    (_JUMP_GROUP_TEST, [("jump", None)]),
    ("query.is_sneaking", [
        ("sneak", "query.modified_move_speed>0.05"),
        ("sneaking", None),
    ]),
    (None, [   # 地面默认组(前面各组已排除)
        ("run", "query.is_sprinting"),
        ("walk", "query.modified_move_speed>0.05"),
        ("idle", None),
    ]),
]


# 主链固定成员的转义键全集(JavaStateLoopType 判成员; vehicle$ 动态成员另判前缀)
_JAVA_STATE_MEMBER_KEYS = frozenset(
    EscapeConditionKey(key) for _test, members in _JAVA_STATE_GROUPS for key, _t in members)


def _Negate(expression):
    """molang 判据取反(带括号防优先级出错)"""
    return "!({})".format(expression)


def _VehicleStateMembers(animKeys):
    """模型的 vehicle$<实体ID> 条件键 → 骑乘组动态成员 [(转义键, 判据), ...]。

    Java VehiclePredicate 里 vehicle$ 条件优先于 ride_pig/ride/boat/sit 硬编码链,
    故插在骑乘组头部, 与固定成员共享组互斥否定链。
    """
    members = []
    head = _VEHICLE_CONDITION_PREFIX + "$"
    for key in animKeys:
        if _IsOwnershipCompanionKey(key):
            continue
        javaKey = _UnescapeConditionKey(key)
        if javaKey.startswith(head) and len(javaKey) > len(head):
            entityId = JAVA_TO_BEDROCK_ENTITY_IDS.get(javaKey[len(head):], javaKey[len(head):])
            members.append(
                (key, "query.is_riding_any_entity_of_type('{}')".format(entityId)))
    return members


def _BuildJavaStateAnimates(animKeys, keyPrefix="", extraGate=None, premiseOverrides=None):
    """Java 状态动画 → 互斥 molang 条件 [(键, 条件), ...]。

    组成员表写 Java 原短名(可含 : 条件形态), 与注册键匹配/发射条目时统一走
    EscapeConditionKey 的引擎安全形态。
    premiseOverrides: {组前提: 替换前提} —— 预留: 整体替换某组的进入前提(例如把空中组
    的防抖死区换成纯"腾空"判据), 当前主链路不使用。
    """
    available = set(animKeys)
    entries = []
    priorGroupTests = []
    vehicleMembers = _VehicleStateMembers(animKeys)
    for groupTest, members in _JAVA_STATE_GROUPS:
        if premiseOverrides and groupTest in premiseOverrides:
            groupTest = premiseOverrides[groupTest]
        if groupTest == _JAVA_RIDING_GROUP_TEST and vehicleMembers:
            members = vehicleMembers + members
        present = [
            (EscapeConditionKey(key), test) for key, test in members
            if EscapeConditionKey(key) in available
        ]
        if present:
            parts = [_NON_GUI_GUARD, "!variable.is_first_person", "!query.is_spectator"]
            if groupTest:
                parts.append(groupTest)
            parts.extend(_Negate(prior) for prior in priorGroupTests)
            groupCondition = "&&".join(parts)

            priorMemberTests = []
            for key, memberTest in present:
                memberParts = [groupCondition]
                if memberTest:
                    memberParts.append(memberTest)
                memberParts.extend(_Negate(prior) for prior in priorMemberTests)
                condition = "&&".join(memberParts)
                if extraGate:
                    condition = "{}&&{}".format(condition, extraGate)
                entries.append((keyPrefix + key, condition))
                if memberTest:
                    priorMemberTests.append(memberTest)
        # 组前提参与后续否定链, 与该组是否有可用动画无关 —— 否则缺 sleep 动画的
        # 模型会在睡觉时播 idle(Java 语义是"该状态无动画则不播", 不是回落)
        if groupTest:
            priorGroupTests.append(groupTest)
    return entries


_CHANNEL_CONTROLLER_KEY_PATTERN = re.compile(r"^player_(pre_)?parallel_(\d+)$")


def _DrivenParallelKeys(controllerKeys, controllerRefs):
    """并行族里**已由模型自己的控制器驱动**的动画短键集 —— 这些不再合成直挂 animate 条目。

    Java(ParallelControllerDiscovery + HybridAnimationController): 并行通道有两种被接管方式,
    两者都让内置的 ParallelPredicate 不再自动播 `parallelN`/`pre_parallelN`:
    - **通道接管**: 包声明了 `player_parallel_N` / `player_pre_parallel_N` 控制器 → 第 N 条
      通道整条交给它(即便该控制器压根没引用 parallelN);
    - **引用驱动**: 任何自有控制器的状态里引用了该动画(实测常见写法: 作者把 parallel0..7
      全列进 player_parallel_0 的单一状态, 集中到一条通道上播)。
    两条规则的并集正是旧版要求创作者手写的 `netease.animate_remove`, 现在自动推导 ——
    创作者只管增删自己的动画与控制器, 不必再维护删除清单。
    """
    driven = set()
    for key in controllerKeys or ():
        matched = _CHANNEL_CONTROLLER_KEY_PATTERN.match(key)
        if matched:
            driven.add("{}parallel{}".format(matched.group(1) or "", matched.group(2)))
    for name in controllerRefs or ():
        if _PARALLEL_KEY_PATTERN.match(name):
            driven.add(name)
    return driven


def _OrderJavaAnimates(controllerAnimates, conditionalAnimates, stateAnimates, animKeys,
                       drivenParallels=None, ownershipWeights=None):
    """Java 模式 animate 表的求值顺序(Java 通道注册顺序见 _JAVA_PRE_CONTROLLER_PATTERN 注)。

    **基岩逐条目交错求值**: 轮到控制器条目时先走转移(执行 on_exit/on_entry), 紧接着就按当前
    状态求它的动画权重, 再轮到下一条。移植工具的逐通道覆盖(port_java_pack.ApplyChannelOwnership)
    让早层伴生动画的权重读晚层控制器 on_entry 写的占用变量 —— 晚层排在后面时, 切状态那一帧
    早层读到的是上一帧的占用, 同一通道要么没人写(闪回绑定姿态)要么两边都写(翻倍)。实机:
    凋灵娘持剑跑动起跳, idle/跑/跳衔接处闪一帧(2026-09-17 用户报告)。
    故带伴生的宿主按 Java 通道序**倒序**排在最前: player_parallel_*(名字倒序) → [骑乘条件动画
    (vehicle)] → 状态驱动(ysm_state, main) → player_pre_main*/player_pre_parallel_*(族序、名字倒序)
    → pre_parallel 直播。这些动画都不带 override_previous_animation, 逐通道相加与顺序无关, 倒过来
    只改变占用变量的新鲜度。骑乘条件动画夹在中间是为了**同键合并**: 条目表按键合并时位置取首次、
    值取末次(skinBuilder.BuildEntries), vehicle$ 键在状态驱动里还有一份带完整组互斥的版本, 必须后写。
    其后是会清空前序通道的层(override 只作用于排在它前面的条目, 权重大于 0 即整通道清空):
    其余自定义控制器(post_main/pre_hold/...) → 条件动画(hold_offhand→hold_mainhand→swing→use→
    carry_on/死亡爬梯, 通道内顺序由 _BuildConditionalAnimates 按 _CONDITION_CHANNEL_ORDER 排好;
    死亡/爬梯同键在状态驱动之后, 保持旧有的合并胜负) → parallel 直播 → 甲槽条件动画。
    条件动画排到了并行控制器后面: 它们与晚层控制器同写的通道由移植工具拆进伴生动画, 晚层
    占用该通道时权重精确为 0(权重 0 不清空前序通道, 见 _BuildSwingControllerBody 注)。
    drivenParallels: 已被自有控制器驱动的并行族键(见 _DrivenParallelKeys), 不再直挂。
    ownershipWeights: ysm.json 顶级 channel_ownership。裸 pre_parallel/parallel 直挂早层的伴生
    (<键>__own<N>, 其通道会被主链/作者控制器等晚层覆盖)紧跟原条目, 权重 `门?权重:0`。晚层的占用变量
    必须先于它求值: 主链与 pre/并行控制器已排在 preParallel 前面; 其余自定义控制器排在后面(内含
    override 条目要清空前序), 它们切状态那一帧伴生读到上一帧的占用(与作者控制器宿主的伴生同样近似)。
    """
    preControllers, parallelControllers, otherControllers = [], [], []
    for key, condition in controllerAnimates:
        if _JAVA_PRE_CONTROLLER_PATTERN.match(key):
            preControllers.append((key, condition))
        elif _JAVA_PARALLEL_CONTROLLER_PATTERN.match(key):
            parallelControllers.append((key, condition))
        else:
            otherControllers.append((key, condition))
    # Java 同族按控制器名排序(RB 树, 后者胜出) → 求值倒序; pre 族里 pre_main(族序 3)晚于 pre_parallel(0)
    parallelControllers.sort(key=lambda entry: entry[0], reverse=True)
    preControllers.sort(key=lambda entry: (
        0 if entry[0].startswith("player_pre_parallel") else 1, entry[0]), reverse=True)
    armorConditions, postConditions, vehicleConditions = [], [], []
    for key, condition in conditionalAnimates:
        parsed = _SplitConditionKey(key)
        if parsed is not None and parsed[0][0] in _ARMOR_CONDITION_PREFIXES:
            armorConditions.append((key, condition))
        elif _UnescapeConditionKey(key).startswith(_VEHICLE_CONDITION_PREFIX + "$"):
            vehicleConditions.append((key, condition))   # Java: vehicle 通道在 main 之前
        else:
            postConditions.append((key, condition))
    # 结构 animate: parallel/pre_parallel 恒播(Java ParallelPredicate 恒 LOOP)。主域
    # 条目与控制器同双门: 第一人称门(主域并行动画在 FP 会驱动同名骨骼的手臂几何),
    # 纸娃娃门(并行动画常含物品判定 query —— 悬浮武器/鞘位切换族, 纸娃娃上刷错)。
    # 纸娃娃域的装饰件隐藏改由 _BuildPaperdollParallels 以独立键补齐: 只放
    # query-free 的并行族, 且不受通道接管(自有控制器驱动)影响 —— 控制器在
    # 纸娃娃域被门挡住后, 这是纸娃娃唯一的隐藏装饰件来源(实机: ref_wither 纸娃娃
    # 糊满 Box/Backgrounds/表情面片)。
    driven = drivenParallels or set()
    preParallel = [(key, _CONTROLLER_ANIMATE_GATE) for key in animKeys
                   if _PRE_PARALLEL_KEY_PATTERN.match(key) and key not in driven]
    postParallel = [(key, _CONTROLLER_ANIMATE_GATE) for key in animKeys
                    if _PARALLEL_KEY_PATTERN.match(key)
                    and not _PRE_PARALLEL_KEY_PATTERN.match(key) and key not in driven]
    preParallel = _AttachOwnershipCompanions(preParallel, animKeys, ownershipWeights, fractional=True)
    postParallel = _AttachOwnershipCompanions(postParallel, animKeys, ownershipWeights, fractional=True)
    return (parallelControllers + vehicleConditions + list(stateAnimates) + preControllers
            + preParallel + otherControllers + postConditions + postParallel + armorConditions)


_PAPERDOLL_KEY_PREFIX = "paperdoll."
_PAPERDOLL_CONDITION = "variable.is_paperdoll"
def _OwnershipCompanionIds(animEntries):
    """注册表里的通道覆盖伴生动画 → {原动画 ID: [伴生动画 ID, ...](按伴生序号)}。

    GUI 预览实体只播展示动画/预览动作表本身(没有作者控制器, 也就没有晚层写入者):
    播原动画时必须把它的伴生动画以同一条件一起播, 才等于原动画的完整效果。
    """
    byKey = dict(animEntries)
    grouped = OrderedDict()
    for key, animId in animEntries:
        matched = _OWNERSHIP_COMPANION_PATTERN.match(key)
        if not matched:
            continue
        baseId = byKey.get(matched.group(1))
        if baseId:
            grouped.setdefault(baseId, []).append((int(matched.group(2)), animId))
    return OrderedDict((baseId, [animId for _index, animId in sorted(items)])
                       for baseId, items in grouped.items())


def _ParallelFamilyBase(key):
    """并行族键(或并行族动画的伴生键) → 并行族原键; 其他返回 None"""
    matched = _OWNERSHIP_COMPANION_PATTERN.match(key)
    base = matched.group(1) if matched else key
    return base if _PARALLEL_KEY_PATTERN.match(base) else None


def _BuildPaperdollParallels(animEntries):
    """纸娃娃域的并行族直挂 → (追加动画条目 [(键, ID)], pre 侧 animate, post 侧 animate)。

    主域全部条目(状态链/条件动画/自定义控制器/并行族)都带 !is_paperdoll 门; 引擎
    给纸娃娃置位 is_paperdoll 时它们全被挡住, 而并行族正是模型隐藏装饰件的唯一来源
    (pre_parallel0 之类 scale 置 0 的动画) —— 故以独立键 `paperdoll.<短键>` 复用同一
    动画 ID 单独补一份, 条件仅 is_paperdoll: 与主域键错开, 不被控制器接管通道时的
    通道接管波及, 也不与主域条目双播。对应 Java: ParallelPredicate 恒 LOOP 含
    一切渲染域; 旧版内置模型的等价物是 paperdoll_ctl 播一条纯字面量的静态动画。

    准入: **query-free** —— 纸娃娃是无装备上下文的实体, 物品/骑乘 query 在其上逐帧
    刷错(悬浮武器/鞘位切换族属此类, 纸娃娃上不播是可接受的静态降级)。引用变量不再
    是准入条件: 变量由每实例初始化控制器(_VARIABLE_INIT_KEY)在纸娃娃实例上补齐。
    索引没有概况的 ID(索引不可用/正则兜底解析)视为未知, 保守不放。

    实机(2026-09): 网易触屏纸娃娃与原版背包纸娃娃上 variable.is_paperdoll 均为 0,
    主域条目照常运行 —— 本域目前不触发, 保留为引擎日后置位时的兜底。
    """
    index = _GetResourceIndex()
    if index is None or not hasattr(index, "QueryAnimationMolangProfile"):
        return [], [], []
    animations, preAnimates, postAnimates = [], [], []
    for key, animId in animEntries:
        base = _ParallelFamilyBase(key)
        if base is None:
            continue
        profile = index.QueryAnimationMolangProfile(animId)
        if profile is None or profile.get("hasQuery"):
            continue
        paperdollKey = _PAPERDOLL_KEY_PREFIX + key
        animations.append((paperdollKey, animId))
        entry = (paperdollKey, _PAPERDOLL_CONDITION)
        if _PRE_PARALLEL_KEY_PATTERN.match(base):
            preAnimates.append(entry)
        else:
            postAnimates.append(entry)
    return animations, preAnimates, postAnimates

# ============ Java 模式动画基线(Java 格式包的 fallback 域) ============
# Java 权威语义(AnimationStore "local-first default fallback", dev/1.20):
# 模型缺某动画键时回落**内置 default 模型**的同名动画 —— 含全部标准状态键、
# swing_hand/use_* 兜底、57 条手持条件动画、parallel 空壳; 默认模型的条件动画
# 对所有模型生效(ConditionManager 用合并 keySet 注册)。
# 基岩侧对应物 = java_default 模型包(即官方 default 的移植产物, 随主包内置):
# Java 模式包的动画底表从旧版共享基线(BASE_ANIMATIONS, fight/tacz/actor 族)
# 换血为 java_default 命名空间 —— 缺键回落官方基线动画, 而非旧版观感资源。
_JAVA_BASELINE_NS = "java_default"


def _JavaDefaultBaseline(packName, foundVars=None):
    """java_default 包的 (短键, 动画ID) 基线表(主域 + arm 域条件键, parallel 系除外)。

    资源索引不可用或基线包缺席时返回空表并由调用方告警 —— 模型自有动画照常,
    只是缺键不再有官方基线兜底(等价于 Java 删掉内置 default 的降级形态)。
    基线包自身解析时不回落自己。
    """
    if packName == _JAVA_BASELINE_NS:
        return []
    # 并行族(parallelN / pre_parallelN)两个命名空间都排除: Java 的并行通道只发现模型**自有**的并行动画
    # (ParallelControllerDiscovery 遍历 AnimationStore 的本地键集, 回落不算) —— 默认模型的 pre_parallel1 会
    # 动 LeftEyebrow/RightEyebrow, 并进来就是给没写眨眼的包凭空加一套眨眉。伴生键(<键>__own<N>)只属于基线
    # 自身的状态机, 同样排除
    entries = []
    for namespace in (_JAVA_BASELINE_NS, _JAVA_BASELINE_NS + "_arm"):
        entries += [
            (shortKey, animId)
            for shortKey, animId in (_QueryIndexAnimations(namespace, foundVars) or [])
            if not _PARALLEL_KEY_PATTERN.match(shortKey) and not _IsOwnershipCompanionKey(shortKey)
        ]
    return entries


def _IsDefaultJavaScale(value):
    """是否为 Java 缩放缺省值 0.7(properties 层与几何文件层共用同一缺省)"""
    return isinstance(value, (int, float)) and abs(value - 0.7) < 1e-6


def _ReadModelFileScales(jsonDict, readTextFunc, geometry=None):
    """主几何的 (ysm_height_scale, ysm_width_scale)(Java RawModelAssembler 语义:
    properties 缩放为缺省 0.7 时回读几何文件声明)。

    主来源同样是资源包索引(按几何 identifier 查), 行为包副本为回落。
    """
    index = _GetResourceIndex()
    if index is not None and geometry:
        height, width = index.QueryGeometryScales(geometry)
        if height is not None or width is not None:
            return height, width
    modelDecl = ((jsonDict.get("files") or {}).get("player") or {}).get("model")
    mainPath = modelDecl.get("main") if isinstance(modelDecl, dict) else None
    if not isinstance(mainPath, str) or not mainPath or readTextFunc is None:
        return None, None
    text = readTextFunc(mainPath)
    if not text:
        return None, None
    if not isinstance(text, str):
        text = text.encode("utf-8")
    height = width = None
    try:
        data = json.loads(text.decode("utf-8-sig"))
        for geo in data.get("minecraft:geometry") or []:
            desc = (geo or {}).get("description") or {}
            height = desc.get("ysm_height_scale")
            width = desc.get("ysm_width_scale")
            if height is not None or width is not None:
                break
    except (ValueError, AttributeError):
        pass
    if height is None and width is None:
        # 几何文件解析失败(如带注释)时正则兜底
        matched = re.search(r'"ysm_height_scale"\s*:\s*(-?[0-9.]+)', text)
        if matched:
            height = float(matched.group(1))
        matched = re.search(r'"ysm_width_scale"\s*:\s*(-?[0-9.]+)', text)
        if matched:
            width = float(matched.group(1))
    height = height if isinstance(height, (int, float)) else None
    width = width if isinstance(width, (int, float)) else None
    return height, width


def _IterReplacedEntries(section):
    """files.projectiles/vehicles 的两种 Java 形态 → [([实体ID...], entry), ...]。

    对象形态 {"minecraft:arrow": {...}} 键即实体 ID; 数组形态 [{"match": [...], ...}]
    (对应 ModelFilesWarper 的两种反序列化)。
    """
    result = []
    if isinstance(section, dict):
        for entityId in _OrderedKeys(section):
            entry = section[entityId]
            if isinstance(entry, dict):
                result.append(([entityId], entry))
    elif isinstance(section, list):
        for entry in section:
            if not isinstance(entry, dict):
                continue
            # 移植工具(py2 读 JSON 得 unicode)与运行层(str)共用本函数, 两种字符串都要认
            match = [m for m in (entry.get("match") or []) if isinstance(m, _STRING_TYPES)]
            if match:
                result.append((match, entry))
    return result


# ---- 替换实体(弹射物/载具)的 Java 通道语义 ----
# Java 只播"谓词状态键 + 并行键", 其余动画仅供控制器引用(ProjectileControllerCollection /
# VehicleControllerCollection); 每个谓词控制器只在其 ANIM_LIST 任一动画存在时才创建
# (SingleControllerDiscovery)。条目: (发现名单, ((状态键, 条件), ...))。
# - 弹射物: ProjectileMainPredicate 按 水>火>地>空 播 water/fire/ground/air, 发现名单却是
#   water/ground/fly/fire(Java 源码如此) —— fly 从不播放; 只有 air 的包在 Java 里不建控制器、air 不播。
#   ground 在 Java 只对箭类(inGround)成立, 基岩用在地判据近似。
# - 载具: VehicleMainPredicate(水>地>空 fly) + VehicleMovePredicate(水平位移 >0.05 格/tick
#   ≈ 1.0 米/秒 = query.ground_speed 单位) + VehicleRidePredicate(乘客非空 has_ride / 否则 not_ride)。
#   Java 下车不撤替换(模型留在空载具上直到下一个第一乘客上车), not_ride 就是空载具的状态。
_PROJECTILE_CHANNELS = (
    (("water", "ground", "fly", "fire"), (
        ("water", "query.is_in_water"),
        ("fire", "query.is_on_fire&&!query.is_in_water"),
        ("ground", "query.is_on_ground&&!query.is_in_water&&!query.is_on_fire"),
        ("air", "!query.is_on_ground&&!query.is_in_water&&!query.is_on_fire"),
    )),
)
_VEHICLE_CHANNELS = (
    (("water", "ground", "fly"), (
        ("water", "query.is_in_water"),
        ("ground", "query.is_on_ground&&!query.is_in_water"),
        ("fly", "!query.is_on_ground&&!query.is_in_water"),
    )),
    (("forward", "idle"), (
        ("forward", "query.ground_speed>1.0"),
        ("idle", "query.ground_speed<=1.0"),
    )),
    (("has_ride", "not_ride"), (
        ("has_ride", "query.has_rider"),
        ("not_ride", "!query.has_rider"),
    )),
)
# 并行键强制常开(ParallelControllerDiscovery): 弹射物只有 parallel0-7, 载具另有 pre_parallel0-7
_REPLACE_PARALLEL_KEYS = {
    "projectiles": re.compile(r"^parallel[0-7]$"),
    "vehicles": re.compile(r"^(?:pre_)?parallel[0-7]$"),
}
_STRING_TYPES = (str, type(u""))


def _ReplaceAnimates(sectionKey, animKeys):
    """替换实体动画短键 → [[键, 条件], ...], 按 Java 通道序: pre_parallel → 谓词通道 → parallel"""
    present = set(animKeys)
    parallel = _REPLACE_PARALLEL_KEYS["vehicles" if sectionKey == "vehicles" else "projectiles"]
    channels = _VEHICLE_CHANNELS if sectionKey == "vehicles" else _PROJECTILE_CHANNELS
    pre = [[key, "1"] for key in animKeys if parallel.match(key) and key.startswith("pre_")]
    post = [[key, "1"] for key in animKeys if parallel.match(key) and not key.startswith("pre_")]
    states = []
    for discovery, conditions in channels:
        if not present.intersection(discovery):
            continue
        for stateKey, condition in conditions:
            if stateKey in present:
                states.append([stateKey, condition])
    return pre + states + post


_REPLACED_SEGMENT_UNSAFE = re.compile(r"[^a-z0-9_]+")
_REPLACED_FILE_SUFFIX = re.compile(r"(?:[.]animation|[.]geo)?[.](?:json|png|jpg|jpeg|tga)$", re.I)


def ReplacedResourceSegment(path):
    """替换实体的模型/动画声明 → 资源 ID 段: 文件基名去扩展, 小写, 非 [a-z0-9_] 折成下划线。

    资源 ID 不认大写, 点号会切碎 animation.<包名>_<段>.<短键> 的命名空间 —— 18_wedding 的
    models/GMA_T.50.json 实测短键变成 50.forward、整份动画文件被引擎拒载。
    "geometry.xxx" 形态(直接写资源 ID 的共享几何)取末段。
    """
    text = path if isinstance(path, _STRING_TYPES) else ""
    if text.startswith("geometry."):
        text = text.split(".")[-1]
    else:
        text = _REPLACED_FILE_SUFFIX.sub("", text.replace(chr(92), "/").rsplit("/", 1)[-1])
    return str(_REPLACED_SEGMENT_UNSAFE.sub("_", text.lower()).strip("_")) or "entity"


def _ReplacedModelDecl(entry):
    model = entry.get("model")
    if isinstance(model, dict):
        model = model.get("uv", "")
    return model if isinstance(model, _STRING_TYPES) else ""


def ReplacedTargets(files):
    """files.projectiles / vehicles / arrow → [(段名, 实体ID表, 条目, 模型段, 动画命名空间段), ...]。

    声明序: projectiles → vehicles → arrow(Java 废弃字段 files.arrow, RawModelAssembler 把它
    装配成排在 projectiles 之后的一个 minecraft:arrow 弹射物目标, 官方内置酒狐系仍在用)。
    动画命名空间段默认等于模型段(约定 animation.<包名>_<model文件基名>.*); 同一模型段在多个
    条目里配了**不同**动画文件时(01 酒狐的马/骡子共用 foxcar 模型)加上动画文件段区分 —— 基岩
    动画 ID 全局路由, 同一命名空间两套动画会互相覆盖。已带 "<模型段>_" 前缀的动画段原样使用,
    保证移植工具改写声明路径后解析器推导出同一命名空间(两边共用本函数)。
    """
    files = files if isinstance(files, dict) else {}
    targets = []
    for sectionKey in ("projectiles", "vehicles"):
        for entityIds, entry in _IterReplacedEntries(files.get(sectionKey)):
            targets.append((sectionKey, entityIds, entry))
    legacyArrow = files.get("arrow")
    if isinstance(legacyArrow, dict):
        targets.append(("projectiles", ["minecraft:arrow"], legacyArrow))
    animationsByModel = {}
    for _sectionKey, _entityIds, entry in targets:
        animation = entry.get("animation")
        if isinstance(animation, _STRING_TYPES) and animation:
            animationsByModel.setdefault(
                ReplacedResourceSegment(_ReplacedModelDecl(entry)), set()).add(
                ReplacedResourceSegment(animation))
    result = []
    for sectionKey, entityIds, entry in targets:
        modelSegment = ReplacedResourceSegment(_ReplacedModelDecl(entry))
        namespaceSegment = modelSegment
        animation = entry.get("animation")
        if len(animationsByModel.get(modelSegment) or ()) > 1 and isinstance(animation, _STRING_TYPES):
            animationSegment = ReplacedResourceSegment(animation)
            if animationSegment.startswith(modelSegment + "_"):
                namespaceSegment = animationSegment
            else:
                namespaceSegment = "{}_{}".format(modelSegment, animationSegment)
        result.append((sectionKey, entityIds, entry, modelSegment, namespaceSegment))
    return result


# 投射物朝向(对齐 Java GeoProjectilesRenderer 的 Y(yRot-90)/Z(xRot) 旋转与模型宽高缩放 0.7): 移植工具给投射物几何
# 外包 ysm_projectile_root → ysm_projectile_fix(Y -90°)(port_java_pack.WrapProjectileGeometry), 这里在根骨骼上
# 播朝向。箭矢同 CSM(.ref/csm animation.csm.arrow.rot): 原版箭矢实体的 pre_animation 算 variable.shake_power(中靶抖动),
# 原版 move 动画写 body 骨骼(旧版内置箭矢几何的根就叫 body) —— 条目键 move 置 "0" 关掉, 免得模型里恰好有 body 骨骼
# 被转两遍。三叉戟(硬编码渲染实体)同 CSM animation.csm.projectiles_rot。几何里没有这根骨骼(直写 geometry.* 的共享
# 几何 / 旧产物)时动画空转, 不影响渲染。
_PROJECTILE_ORIENT_KEY = "ysm_projectile_orient"
_PROJECTILE_ORIENT_ANIMATIONS = {
    "minecraft:arrow": "animation.ysm.projectile_orient_arrow",
    "minecraft:thrown_trident": "animation.ysm.projectile_orient_trident",
}
_PROJECTILE_VANILLA_MOVE_KEYS = {"minecraft:arrow": "move"}


def _WithProjectileOrientation(entityId, replace):
    """投射物替换数据 → 带朝向动画的副本(同一目标匹配多个实体时各自一份); 无需朝向的原样返回"""
    animationId = _PROJECTILE_ORIENT_ANIMATIONS.get(entityId)
    if animationId is None:
        return replace
    oriented = OrderedDict(replace)
    oriented["animations"] = [[_PROJECTILE_ORIENT_KEY, animationId]] + [
        list(item) for item in (replace.get("animations") or [])]
    animate = [[_PROJECTILE_ORIENT_KEY, "1"]]
    moveKey = _PROJECTILE_VANILLA_MOVE_KEYS.get(entityId)
    if moveKey:
        animate.append([moveKey, "0"])
    oriented["animate"] = animate + [list(item) for item in (replace.get("animate") or [])]
    return oriented


# 载具缩放(对齐 Java CustomVehicleEntity 硬编码 0.7, 不读 ysm.json 缩放): 移植工具给载具几何外包 ysm_vehicle_root
# (port_java_pack.WrapVehicleGeometry), 这里在根骨骼上播缩放。朝向不用补 —— Java 载具与基岩实体同为 YP(180 - 偏航)
# 约定, 马这类数据驱动实体由引擎按身体朝向转, 船(硬编码渲染)的替身实体由运行层按 Java 口径转(vehicleRender)。
# 几何里没有这根骨骼(直写 geometry.* 的共享几何 / 旧产物)时动画空转, 按模型原尺寸渲染。
_VEHICLE_ROOT_KEY = "ysm_vehicle_root"
_VEHICLE_ROOT_ANIMATION = "animation.ysm.vehicle_root"


def _WithVehicleRoot(replace, soundEffects):
    """载具替换数据 → 带根骨骼缩放动画与音效登记的副本(Java 载具动画的 sound_effects 关键帧照播)"""
    rooted = OrderedDict(replace)
    rooted["animations"] = [[_VEHICLE_ROOT_KEY, _VEHICLE_ROOT_ANIMATION]] + [
        list(item) for item in (replace.get("animations") or [])]
    rooted["animate"] = [[_VEHICLE_ROOT_KEY, "1"]] + [list(item) for item in (replace.get("animate") or [])]
    if soundEffects:
        rooted["sound_effects"] = [list(item) for item in soundEffects]
    return rooted


def _BuildReplaceEntities(jsonDict, packName, readTextFunc, warnings, foundVars=None):
    """Java files.projectiles / files.vehicles / files.arrow → 网易 replace_entities 替换表。

    每条: {geometry, texture, (声明文件可读时)animations/animate/animation_controllers}。
    推导约定(与移植工具共用 ReplacedTargets):
    - 实体 ID: Java↔基岩差异自动映射(JAVA_TO_BEDROCK_ENTITY_IDS), 其余原样; 同一实体被多个
               目标匹配时取声明序第一个(Java ClientCatalogManager.findRenderTarget);
    - 几何体: geometry.<包名>_<模型段>(模型段 = model 文件基名规整, 见 ReplacedResourceSegment);
    - 贴图:   textures/entity/<包名>/<texture文件基名>;
    - 动画:   animation 声明文件(资源包索引按路径定位, BP 副本回落)内的动画全部注册;
              无控制器时按 Java 通道语义合成 animate(_ReplaceAnimates: 谓词状态键 + 并行键,
              其余动画只注册不直播);
    - 控制器: controller 声明文件可读时自动注册并常开, 由控制器接管动画播放;
    - 变量:   替换实体的动画/控制器文件与玩家侧同样参与 molang 变量扫描
              (foundVars) —— 变量域全局共享, 漏收会导致替换动画表达式求值失败;
    - 载具:   根骨骼缩放动画打头(_WithVehicleRoot, Java 硬编码 0.7), 有动画时带上包的音效登记
              (files.player.sound_effect, 移植工具把载具动画的音频关键帧也登记在这里)。
    """
    result = OrderedDict()
    playerFiles = (jsonDict.get("files") or {}).get("player") if isinstance(jsonDict.get("files"), dict) else None
    packSounds = [item for item in ((playerFiles or {}).get("sound_effect") or [])
                  if isinstance(item, (list, tuple)) and len(item) == 2] if isinstance(playerFiles, dict) else []
    for sectionKey, entityIds, entry, modelSegment, namespaceSegment in ReplacedTargets(
            jsonDict.get("files")):
        # model 直通判据必须用**原始字符串**: _TextureEntryPath 会把
        # "geometry.default_arrow" 的 ".default_arrow" 误当扩展名裁掉
        rawModel = _ReplacedModelDecl(entry)
        if rawModel.startswith("geometry."):
            # 直接写资源 ID = 共享几何场景(Java 的包内文件路径不可能是这种形态)
            modelPath = rawModel
        else:
            modelPath = _TextureEntryPath(rawModel)
        if not modelPath:
            warnings.append("{} 条目缺少 model 声明, 已跳过: {}".format(sectionKey, entityIds))
            continue
        textureDecl = entry.get("texture")
        if isinstance(textureDecl, dict) and (textureDecl.get("normal") or textureDecl.get("specular")):
            warnings.append("{}({}) 的 PBR 贴图(normal/specular)不支持, 已忽略".format(
                sectionKey, modelSegment))
        replace = OrderedDict()
        if modelPath.startswith("geometry."):
            replace["geometry"] = modelPath
        else:
            replace["geometry"] = "geometry.{}_{}".format(packName, modelSegment)
        # texture 同理: Java 包内路径必带图片扩展名(textures/arrow.png),
        # 无扩展名 = 基岩真实引用路径(共享贴图场景), 原样直通
        rawTexture = textureDecl.get("uv") if isinstance(textureDecl, dict) else textureDecl
        texturePath = _TextureEntryPath(textureDecl)
        if texturePath:
            if isinstance(rawTexture, str) and not _IMAGE_EXT_PATTERN.search(rawTexture):
                replace["texture"] = texturePath
            else:
                replace["texture"] = "textures/entity/{}/{}".format(
                    packName, texturePath.split("/")[-1])
        # 动画声明: 路径解析模式优先(资源包文件精确注册, 与玩家侧同规则),
        # BP 副本回落(历史兼容)
        namespace = "{}_{}".format(packName, namespaceSegment)
        animEntries = _QueryIndexFileAnimations(entry.get("animation"), namespace, foundVars)
        if animEntries is None:
            animEntries = _ReadAnimFileEntries(
                entry.get("animation"), "animation." + namespace, readTextFunc, warnings, foundVars)
        ctlEntries, ctlAnimate, ctlLoaded = _QueryIndexFileControllers(
            entry.get("controller"), namespace, foundVars)
        if not ctlLoaded:
            ctlEntries, ctlAnimate, ctlLoaded = _ReadControllerFileEntries(
                entry.get("controller"), readTextFunc, warnings, foundVars)
        if animEntries:
            replace["animations"] = [list(item) for item in animEntries]
            if not ctlLoaded:
                replace["animate"] = _ReplaceAnimates(sectionKey, [key for key, _ in animEntries])
        if ctlEntries:
            replace["animation_controllers"] = [list(item) for item in ctlEntries]
            replace["animate"] = (replace.get("animate") or []) + [
                list(item) for item in ctlAnimate]
        if entry.get("controller") and not ctlLoaded:
            warnings.append(
                "{}({}) 声明的控制器文件不可读, 未自动注册 —— 把改名后的控制器文件"
                "复制一份进 BP 模型目录对应路径, 或经 netease.replace_entities "
                "显式声明".format(sectionKey, modelSegment))
        if sectionKey == "vehicles" and not modelPath.startswith("geometry."):
            replace = _WithVehicleRoot(replace, packSounds if animEntries else None)
        for entityId in entityIds:
            bedrockId = JAVA_TO_BEDROCK_ENTITY_IDS.get(entityId, entityId)
            if bedrockId in result:
                continue
            if sectionKey == "projectiles" and not modelPath.startswith("geometry."):
                result[bedrockId] = _WithProjectileOrientation(bedrockId, replace)
            else:
                result[bedrockId] = replace
    return result


def _NormalizeVariableName(name):
    """molang 变量名归一: v.xxx / variable.xxx → variable.xxx; 其他形态返回 None"""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if name.startswith("v."):
        return "variable." + name[2:]
    if name.startswith("variable."):
        return name
    return None


def _BuildInitialize(netease, buttonsList, fileMolangVars, warnings):
    """molang 变量初始化表达式合成 —— 还原 Java 的"变量零声明"体验。

    Java molang 未定义变量读取缺省为 0(MolangMemory.getScoped 即取即建),
    模型包无需声明; 基岩引擎读未初始化 variable 会求值失败, 必须显式初始化。
    自动来源: config_forms 的 value 变量(0 夹进 range 的 [min,max]; checkbox/radio
    为 0 —— 轮盘表单 UI 读的是变量现值, 与此一致) + BP 副本动画/控制器文件内扫描到的
    变量引用(排除主包基线已初始化者)。
    netease.initialize 显式声明优先: 完整表达式原样直通; 裸变量名展开为 "= 0.0;"。

    返回 (entries, scannedEntries) 两段: 文件扫描来源单列 —— 其变量名若已被
    他方初始化(主包基线之外还有旧版 py 副包/先装的 JSON 包, 装载时才可知),
    自动补 0 应放弃, 由 modelInstaller 对照共享列表在场变量名过滤后并入,
    防止迟到扫描(重试路径)把别人的显式初值踩成 0。
    """
    entries = []
    scannedEntries = []
    seenNames = set()

    def _Add(name, value):
        if name in seenNames:
            return
        seenNames.add(name)
        entries.append("{} = {};".format(name, value))

    # 显式声明优先(同名去重 first-wins): netease.initialize > config_forms > 文件扫描
    for item in netease.get("initialize") or []:
        if not isinstance(item, str) or not item.strip():
            continue
        if "=" in item:
            expr = item.strip()
            expr = expr if expr.endswith(";") else expr + ";"
            entries.append(expr)
            head = _NormalizeVariableName(expr.split("=", 1)[0])
            if head:
                seenNames.add(head)
            continue
        name = _NormalizeVariableName(item)
        if name is None:
            warnings.append("netease.initialize 含无法识别的变量名: {}".format(item))
            continue
        _Add(name, 0.0)

    for button in buttonsList:
        for form in button.get("config_forms") or []:
            if not isinstance(form, dict):
                continue
            name = _NormalizeVariableName(form.get("value"))
            if name is None:
                continue
            # Java 未定义变量读 0: 取 0 并夹进 range 的 [min, max](checkbox/radio 取 0)。
            # 旧规则"range 取 min"会把 [-50,50] 的位置滑条初始化成 -50 —— 实机: 凋灵娘
            # 整体被平移 50 单位, 玩家身上与 GUI 都看不见; [1,1.6] 的大小滑条夹后仍为 1
            initValue = 0
            if form.get("type") == "range":
                low, high = form.get("min"), form.get("max")
                if isinstance(low, (int, float)) and initValue < low:
                    initValue = low
                if isinstance(high, (int, float)) and initValue > high:
                    initValue = high
            _Add(name, float(initValue))

    # 文件扫描兜底(最弱): 动画/控制器文件里引用的变量, 排除主包基线已初始化的
    # —— 还原 Java "未定义变量缺省 0" 的语义(基岩引擎读未初始化变量会求值失败)
    for shortName in sorted(fileMolangVars or []):
        fullName = "variable." + shortName
        if fullName in _BASE_VARIABLE_NAMES or fullName in seenNames:
            continue
        if _OWNERSHIP_VARIABLE_PATTERN.match(shortName):
            continue   # 占用变量由控制器 on_entry 维护, 读取处自带 ??0; 模型应用时补 0 会把活跃状态清掉
        if _INPUT_STATE_VARIABLE_PATTERN.match(shortName):
            continue   # 输入状态锁存/挥动序号由共享动画与挥击状态机维护, 读取处自带 ?? 回落

        seenNames.add(fullName)
        scannedEntries.append("{} = 0.0;".format(fullName))
    return entries, scannedEntries


def _OrderedKeys(mapping):
    """保序 dict 的键序列; 非保序(理论上不出现, 解析走 object_pairs_hook)按键名排序兜底"""
    keys = list(mapping.keys())
    from collections import OrderedDict
    if not isinstance(mapping, OrderedDict):
        keys = sorted(keys)
    return keys


# 轮盘条目指令(extra 条目第二项; 第三项可选附加信息 dict)。"/..." 斜杠指令发服务端执行,
# "$分类id" 进入子轮盘, "#按钮id" 打开 config_forms 表单页, "@return" 返回上一级(根级关闭界面)。
# 界面侧同名常量在 ysmModelCoreScripts/client/ui/rouletteMenu.py(测试守护两边一致)
ROULETTE_RETURN_COMMAND = "@return"
# 按钮条目附加信息里"播放动画"的临时键: 解析期记下动画键, 定稿时按注册结果换成 play 指令或删掉
_ROULETTE_PLAY_KEY = "_play_key"


def _NormalizeRouletteButton(button):
    """按钮浅拷贝; radio 的 labels(Java 有序 dict)转成 [[显示名, 语句], ...]。

    dict 经 ModAttr 同步到客户端会丢序(2026-09-18 实机: 萨赫梅特"自定义/默认/战术"到客户端
    成了"默认/战术/自定义"), 而选项序号就是变量值 —— 必须在解析期定成列表。已是列表的原样保留
    (旧版转换器产物同形态)。
    """
    normalized = OrderedDict((key, value) for key, value in button.items() if key != "config_forms")
    forms = []
    for form in button.get("config_forms") or []:
        if not isinstance(form, dict):
            continue
        form = OrderedDict(form)
        labels = form.get("labels")
        if isinstance(labels, dict):
            form["labels"] = [[name, labels[name]] for name in _OrderedKeys(labels)]
        forms.append(form)
    normalized["config_forms"] = forms
    return normalized


def _BuildRoulette(properties, netease, warnings):
    """Java 轮盘三件套 → 网易皮肤级 extra / extra_animation_classify / extra_animation_buttons。

    **一个 Java 条目占一格**, 格序 = 声明序(权威定义 ysm-java-src pojo/manifest/settings +
    AnimationRouletteScreen; 翻页与根级 8 个热键都按这个序号):
    - "动画键": "显示名"  → (显示名, 播放指令); 显示名为空时 Java 显示条目序号
    - "#分类id": "显示名" → (显示名, "$分类id"); 分类未定义时保留格子(Java 点击无反应)
    - "#return": "显示名" → (显示名, "@return"): 2.3.1 固定名, 与返回按钮同效, 根级关闭界面
    - "动画键": "#按钮id" → Java 同一格外圈播动画、内圈开 config_forms 表单。基岩选择轮盘分不出
      内外圈 → (按钮名, "#按钮id", {"play": 播放指令}): 点击开表单页, 页顶"播放动画"(键没有注册
      动画时不带 play, 见 _FinalizeRouletteEntries); 按钮没有表单 → (按钮名, 播放指令);
      按钮未定义 → Java 显示原文并照常播动画
    - "#分类id": "#按钮id" → 进子轮盘, 显示按钮名(Java 同样只取名字)
    嵌套深度不在解析期截断(Java 点击时限 5 层, 界面同口径), 分类按引用可达性翻译, 成环安全。

    播放指令统一附加停止条件(DEFAULT_EXTRA_STOP_EXPRESSION, 系统维护 —— Java 配置
    无此概念, 创作者零感知); 键本身已带播放参数的旧指令形态原样直通不再注入。

    返回 (extra, classifyList, buttonsList, animKeys); animKeys 为需注册进
    animations 映射的非标准动画键(Java 短名, 由调用方拼命名空间)。按钮条目的附加信息
    还带着临时键, 调用方注册完动画后必须过 _FinalizeRouletteEntries。
    """
    stopExpr = netease.get("extra_stop_expression")
    if stopExpr is None:
        stopExpr = DEFAULT_EXTRA_STOP_EXPRESSION

    def _PlayCommand(key):
        if stopExpr and " " not in key and '"' not in key:
            return '/playanimation @s {} default 0 "{}"'.format(key, stopExpr)
        return "/playanimation @s {}".format(key)

    extraAnimation = properties.get("extra_animation")
    buttonsList = [_NormalizeRouletteButton(b) for b in (properties.get("extra_animation_buttons") or [])
                   if isinstance(b, dict) and b.get("id")]
    # Java 按 id 建 HashMap, 重名后者胜出(ModelProperties.transformExtraAnimationButton)
    buttonMap = dict((b["id"], b) for b in buttonsList)
    classifySrc = dict((c["id"], c) for c in (properties.get("extra_animation_classify") or [])
                       if isinstance(c, dict) and c.get("id"))
    classifyList = []
    builtClassifies = set()
    animKeys = []

    def _RegisterAnimKey(key):
        # 含空格/引号的键是"动画名+播放参数"的指令形态(转换器产物常见,
        # 如 'extra0 default 0 "q.xxx"'), 并非动画短名, 不注册
        if " " in key or '"' in key:
            return
        if key not in STANDARD_MODEL_ANIM_KEYS and key not in animKeys:
            animKeys.append(key)

    def _ButtonName(label):
        button = buttonMap.get(label[1:]) if label.startswith("#") else None
        return (button.get("name") or None) if button is not None else None

    def _BuildEntries(animDict):
        entries = []
        if not isinstance(animDict, dict):
            return entries
        for index, key in enumerate(_OrderedKeys(animDict)):
            label = animDict[key]
            if not isinstance(label, str):
                continue
            if key == "#return":
                entries.append((label or str(index), ROULETTE_RETURN_COMMAND))
                continue
            if key.startswith("#"):
                classifyId = key[1:]
                classify = classifySrc.get(classifyId)
                if classify is None:
                    warnings.append("extra_animation 引用了未定义的分类 #{}, 格子保留但点击无效"
                                    "(与 Java 一致)".format(classifyId))
                else:
                    _EnsureClassify(classifyId, classify)
                entries.append((_ButtonName(label) or label or str(index), "${}".format(classifyId)))
                continue
            _RegisterAnimKey(key)
            if label.startswith("#"):
                button = buttonMap.get(label[1:])
                if button is None:
                    warnings.append("extra_animation 引用了未定义的按钮 {}, 按普通动画格处理"
                                    "(与 Java 一致: 显示原文)".format(label))
                elif button["config_forms"]:
                    entries.append((button.get("name") or key, "#{}".format(button["id"]),
                                    {_ROULETTE_PLAY_KEY: key, "play": _PlayCommand(key)}))
                    continue
                else:
                    entries.append((button.get("name") or key, _PlayCommand(key)))
                    continue
            entries.append((label or str(index), _PlayCommand(key)))
        return entries

    def _EnsureClassify(classifyId, classify):
        if classifyId in builtClassifies:
            return
        builtClassifies.add(classifyId)
        item = {"id": classifyId, "extra_animation": []}
        classifyList.append(item)
        item["extra_animation"] = _BuildEntries(classify.get("extra_animation"))

    extra = _BuildEntries(extraAnimation)
    return extra, classifyList, buttonsList, animKeys


def _FinalizeRouletteEntries(entries, playableKeys):
    """按钮条目定稿: 键有注册动画才保留"播放动画"(Java 外圈对无动画的键静默无操作, 表单页不放
    点了没反应的按钮), 删掉临时键; 附加信息空了就退回二元组"""
    finalized = []
    for entry in entries or []:
        if len(entry) > 2 and isinstance(entry[2], dict) and _ROULETTE_PLAY_KEY in entry[2]:
            meta = OrderedDict((k, v) for k, v in entry[2].items() if k != _ROULETTE_PLAY_KEY)
            if entry[2][_ROULETTE_PLAY_KEY] not in playableKeys:
                meta.pop("play", None)
            entry = (entry[0], entry[1], meta) if meta else (entry[0], entry[1])
        finalized.append(entry)
    return finalized


def ParseYsmJson(jsonDict, packName, readTextFunc=None):
    """解析一份 ysm.json, 返回 (configDict, warnings) 或 (None, [错误信息])。

    :param jsonDict: json.loads 产物(str 化、保序 dict), Java 原生或含 netease 覆盖段
    :param packName: 模型包文件夹名(推导 model_id/资源名的默认依据)
    :param readTextFunc: 可选 f(模型包内相对路径)→文本或 None; 提供时启用
        files.player.animation 声明文件的动画 ID 全量自动注册
    """
    warnings = []
    if not isinstance(jsonDict, dict):
        return None, ["ysm.json 根节点必须是对象"]

    metadata = jsonDict.get("metadata") or {}
    properties = jsonDict.get("properties") or {}
    # 网易扩展配置的标准位置是**顶级**(与 spec/metadata/properties/files 同级),
    # netease 段为兼容形态 —— 合并成统一视图(顶级优先), 下游全部经此读取
    netease = dict(jsonDict.get("netease") or {})
    for topKey in _OrderedKeys(jsonDict):
        if topKey not in ("spec", "metadata", "properties", "files", "netease"):
            netease[topKey] = jsonDict[topKey]

    # ---- 全字段可推导, netease 逐项覆盖 ----
    modelId = netease.get("model_id") or "{}:{}".format(DEFAULT_MODEL_NAMESPACE, packName)
    # 几何: netease.geometry > files.player.model.main 直通(值以 "geometry." 开头
    # = 直接写资源 ID, 与 projectiles 的 model 直通同一判据 —— Java 的包内文件
    # 路径不可能是这种形态) > 按包名推导
    playerFiles = (jsonDict.get("files") or {}).get("player") or {}
    playerModelDecl = playerFiles.get("model")
    mainModelRaw = playerModelDecl.get("main") if isinstance(playerModelDecl, dict) else None
    if isinstance(mainModelRaw, dict):
        mainModelRaw = mainModelRaw.get("uv", "")
    mainModelDirect = mainModelRaw if (
        isinstance(mainModelRaw, str) and mainModelRaw.startswith("geometry.")) else None
    geometry = netease.get("geometry") or mainModelDirect \
        or "geometry.{}".format(packName)
    animNs = netease.get("animation_namespace") or "animation.{}".format(packName)

    skinSort, textureMap = _DeriveSkins(jsonDict, netease, packName, warnings)
    if not skinSort:
        return None, [
            "无法确定皮肤列表: 需要 files.player.texture(Java 原生, 至少一张贴图)"
            " 或 netease.textures(须含 default)"
        ]

    def _JavaAnimName(neteaseKey):
        javaKey = NETEASE_TO_JAVA_ANIM_KEYS.get(neteaseKey, neteaseKey)
        return "{}.{}".format(animNs, javaKey)

    # ---- default 皮肤合成 ----
    extraButtons, classifyList, buttonsList, extraAnimKeys = _BuildRoulette(properties, netease, warnings)

    # 第一人称手臂通道(Java files.player.model.arm 声明时启用):
    # 几何推导 geometry.<包名>_<arm文件基名>("geometry." 开头 = 资源 ID 直通),
    # 渲染交给专用控制器(见 defaultSkin 合成);
    # 未声明的包一切照旧(旧主模型剔除法), 保证向下兼容零差异
    armModelRaw = playerModelDecl.get("arm") if isinstance(playerModelDecl, dict) else None
    if isinstance(armModelRaw, dict):
        armModelRaw = armModelRaw.get("uv", "")
    armGeometry = None
    if isinstance(armModelRaw, str) and armModelRaw.startswith("geometry."):
        armGeometry = armModelRaw
    else:
        armModelPath = _TextureEntryPath(armModelRaw)
        if armModelPath:
            armGeometry = "geometry.{}_{}".format(packName, armModelPath.split("/")[-1])

    # BP 副本文件里引用的 molang 变量(动画+控制器), 供变量初始化推导
    fileMolangVars = set()

    # Java 模式(按声明形态自动判定): 状态驱动 + **基线换血** —— 动画底表
    # 不再并入旧版共享基线(fight/tacz/actor 观感资源), 改为 java_default 包
    # (= Java 官方内置 default 模型, AnimationStore fallback 的基岩对应物)。
    # 缺键回落官方基线动画; 基线包缺席时缺键就是缺键, 不再有旧版动画顶上。
    # 内置模型不开此模式, 旧通道与等价性守护零波及。
    # Java 原生格式的判据 = files.player.animation 的**声明形态**(见 _LoadDeclaredAnimations):
    # dict 是 Java 的语义槽位({"main": 路径, "arm": 路径, ...}), list 是旧版/基岩扩展写法
    # (["路径", {"短名": "动画ID"}...], 依赖旧版共享基线动画表)。两代包的动画底表、控制器
    # 底表、animate 底表完全不同, 必须区分 —— 但不需要创作者显式声明。
    # netease.java_state_driver 仍可显式覆盖(两个方向都支持), 正常不必写。
    declaredAnimation = playerFiles.get("animation")
    javaMode = isinstance(declaredAnimation, dict) and bool(declaredAnimation)
    if netease.get("java_state_driver") is not None:
        javaMode = bool(netease.get("java_state_driver"))
    javaBaseline = []
    if javaMode:
        # 基线动画在本包上播放时读的变量(v.qh / v.bv ...)同样要初始化: 并进文件扫描(见移植工具
        # CollectBaselineVariables 注, 两边口径一致)
        javaBaseline = _JavaDefaultBaseline(packName, fileMolangVars)
        if not javaBaseline and packName != _JAVA_BASELINE_NS:
            warnings.append(
                "Java 格式包但 {} 基线包不可用(未装载或资源索引缺席), "
                "模型缺失的标准动画将没有官方基线兜底".format(_JAVA_BASELINE_NS))
    baselineKeys = set(key for key, _animId in javaBaseline)

    # 标准键盲注册仅限旧通道(内置模型/无声明文件的包): javaMode 的标准键全部来自
    # 声明文件扫描, 盲注册只会用坏 ID 顶掉官方基线兜底(sneak_arm 等网易专属键
    # Java 包必缺, 每个模型都白刷一轮坏引用告警)。
    if javaMode:
        modelAnimEntries = []
    else:
        modelAnimEntries = [(key, _JavaAnimName(key)) for key in STANDARD_MODEL_ANIM_KEYS]
    # files.player.animation 声明文件内的动画 ID 全量自动注册(BuildEntries 同键原位合并)
    modelAnimEntries += _LoadDeclaredAnimations(
        jsonDict, animNs, readTextFunc, warnings, fileMolangVars,
        skipKeys=_FP_ARM_ANIMATION_KEYS if armGeometry else None,
        expandFunc=_JavaAnimName)
    # 手臂动画(fp_arm/arm 声明文件, animation.<包名>_arm.* 命名空间): fp_ 前缀键
    # 走 FP 白名单通道; arm 文件条件键同时并入主域(Java dev/1.20 主域合并语义)
    if armGeometry:
        fpArmEntries, armMainEntries = _LoadArmAnimations(
            jsonDict, packName, readTextFunc, warnings, fileMolangVars)
    else:
        fpArmEntries, armMainEntries = [], []
    modelAnimEntries += armMainEntries + fpArmEntries
    # 轮盘用到的非标准动画键(Java 短名)自动注册, 免去手写; 已显式声明的键跳过
    # (声明列表 dict 条目可指向共享/跨命名空间动画, 自动推导不得覆盖);
    # javaMode 下基线已提供的键(extra0-7 等)不盲注册 —— 盲 ID 会顶掉基线兜底;
    # 资源索引在场时, 索引确认缺失的键直接跳过(Java 对无动画的轮盘键静默无操作,
    # 表单宿主键/纯文本签名键属此类, 盲注册只会刷坏引用告警)。
    declaredKeys = set(key for key, _animId in modelAnimEntries)
    indexAnimIds = None
    if extraAnimKeys:
        indexEntries = _QueryIndexAnimations(_NamespaceName(animNs))
        if indexEntries:
            indexAnimIds = set(animId for _key, animId in indexEntries)
    for key in extraAnimKeys:
        if key in declaredKeys or (javaMode and key in baselineKeys):
            continue
        rouletteAnimId = "{}.{}".format(animNs, key)
        if indexAnimIds is not None and rouletteAnimId not in indexAnimIds:
            continue
        modelAnimEntries.append((key, rouletteAnimId))
    modelAnimEntries += _AsEntryListWithBare(netease.get("animations_extra"), _JavaAnimName)

    # files.player.animation_controllers 声明文件(BP 副本)自动注册控制器
    autoCtlEntries, autoAnimateEntries, ctlLoaded = _LoadDeclaredControllers(
        jsonDict, _NamespaceName(animNs), readTextFunc, warnings, fileMolangVars)
    # Java geckolib 控制器"加载即常开"的裸 "1" 收敛为纸娃娃+第一人称双门
    # (_CONTROLLER_ANIMATE_GATE 注释); 显式声明的非 "1" 条件原样保留; 变量初始化
    # 控制器(_VARIABLE_INIT_KEY)必须在所有渲染域跑, 恒开不经双门
    autoAnimateEntries = [
        (key, _CONTROLLER_ANIMATE_GATE
         if condition == "1" and key != _VARIABLE_INIT_KEY else condition)
        for key, condition in autoAnimateEntries]
    autoCtlEntries, autoAnimateEntries = _AppendVariableInitController(
        _NamespaceName(animNs), autoCtlEntries, autoAnimateEntries)

    # 替换实体表(弹射物/载具): 其动画/控制器文件同样参与变量扫描, 故先于变量合成构建
    replaceEntities = _BuildReplaceEntities(
        jsonDict, packName, readTextFunc, warnings, fileMolangVars)

    # 条件动画(hold_mainhand$物品ID 等): 已注册动画键里识别 Java 条件命名, 合成播放
    # 条件(fp_ 前缀的手臂键不会命中主通道前缀, 其条件由 _BuildFpArmAnimates 单独合成)。
    # Java 模式用**合并键集**(基线+模型): Java 侧 ConditionManager 用合并 keySet
    # 注册条件 —— 官方基线的 57 条手持条件动画对每个模型都生效; swing/use 无命中
    # 兜底(swing_hand/use_mainhand)同为 Java 语义。
    mergedAnimEntries = BuildEntries(javaBaseline, modelAnimEntries) \
        if javaMode else list(modelAnimEntries)
    mergedAnimKeys = [key for key, _animId in mergedAnimEntries]
    modelOwnKeys = set(key for key, _animId in modelAnimEntries)
    conditionalAnimates = _BuildConditionalAnimates(
        mergedAnimKeys, warnings, withFallbacks=javaMode,
        quietKeys=set(k for k, _v in javaBaseline) - modelOwnKeys, javaMode=javaMode)
    if javaMode:
        # 带 override 的条件动画被移植工具拆出的通道覆盖伴生(hold/passenger/carryon 等直挂族)
        # 紧跟原条目, 核心权重取顶级 channel_ownership(见 _AttachOwnershipCompanions 注);
        # 挥击/使用族随后整体换成一次性通道状态机条目(伴生已写在状态里)
        conditionalAnimates = _AttachOwnershipCompanions(
            conditionalAnimates, mergedAnimKeys, netease.get("channel_ownership"))
    fpArmAnimates = _BuildFpArmAnimates(fpArmEntries, warnings) if fpArmEntries else []

    # Java 状态动画驱动: 旧版 controller.animation.ysm.* 状态机引用的是 idle_0/
    # idle_timer 等旧键名, Java 包不提供 → 状态动画永不播放。按 Java 优先级合成
    # 互斥条件直接驱动(死亡/骑乘链/泳姿/爬行/梯子/飞行/受击/潜行/地面)。
    if javaMode:
        # death/ladder 三态在两处都有: 状态链里带完整互斥, 优先用它(同键后写覆盖)
        stateAnimates = _BuildJavaStateAnimates(mergedAnimKeys)
        # 一次性通道(挥击/受击/死亡)有生成式控制器时替换直挂条目(位置与双门不变)
        autoCtlEntries, conditionalAnimates, fpArmAnimates, stateAnimates = \
            _ApplyOneShotControllers(_NamespaceName(animNs), modelOwnKeys, autoCtlEntries,
                                     conditionalAnimates, fpArmAnimates, stateAnimates)
        # animate 表按 Java 通道顺序排列(含 parallel/pre_parallel 恒播条目)
        playerAnimates = _OrderJavaAnimates(
            autoAnimateEntries, conditionalAnimates, stateAnimates, mergedAnimKeys,
            _DrivenParallelKeys([key for key, _id in autoCtlEntries],
                                _QueryControllerAnimRefs(_NamespaceName(animNs))),
            netease.get("channel_ownership"))
        # 纸娃娃域(引擎给纸娃娃置位 is_paperdoll 时主域条目被门挡住; 实机 2026-09 两类
        # 纸娃娃均未置位, 本段为兜底)以独立键补齐:
        # query-free 并行族按 Java 通道序夹着 idle —— pre_parallel(摆位/隐藏装饰件)
        # → idle_gui(站姿, 独立键避免覆盖状态驱动的 idle 条件) → parallel(叠加层)
        paperdollAnims, paperdollPre, paperdollPost = _BuildPaperdollParallels(mergedAnimEntries)
        mergedAnimEntries = mergedAnimEntries + paperdollAnims
        playerAnimates.extend(paperdollPre)
        mergedById = dict(mergedAnimEntries)
        if "idle" in mergedById:
            mergedAnimEntries = mergedAnimEntries + [("idle_gui", mergedById["idle"])]
            playerAnimates.append(("idle_gui", "variable.is_paperdoll"))
            # idle 的通道覆盖伴生动画同条件一起播(纸娃娃域没有晚层控制器, 见
            # _OWNERSHIP_COMPANION_PATTERN 注); 键 idle_gui__own<N> 对应 idle__own<N>
            for index, companionId in enumerate(
                    _OwnershipCompanionIds(mergedAnimEntries).get(mergedById["idle"]) or []):
                companionKey = "idle_gui__own{}".format(index + 1)
                mergedAnimEntries = mergedAnimEntries + [(companionKey, companionId)]
                playerAnimates.append((companionKey, "variable.is_paperdoll"))
        playerAnimates.extend(paperdollPost)
        # 手持物挂点修正(对齐 CSM, 见 _JavaItemFixAnimates 注): 只写 rightItem/leftItem, 包动画不碰这两根骨骼
        for fixKey, fixAnimation, fixCondition in _JavaItemFixAnimates():
            mergedAnimEntries = mergedAnimEntries + [(fixKey, fixAnimation)]
            playerAnimates.append((fixKey, fixCondition))
        # 鞘翅滑翔: 抵消引擎实体层的原生俯仰旋转(见 _JAVA_GLIDE_FIX_KEY 上方注)。动画不直挂,
        # 由共享控制器在进入滑翔态时播 —— 它的 q.anim_time 就是渐入系数的时钟
        mergedAnimEntries = mergedAnimEntries + [(_JAVA_GLIDE_FIX_KEY, _JAVA_GLIDE_FIX_ANIMATION)]
        autoCtlEntries = autoCtlEntries + [(_JAVA_GLIDE_STATE_KEY, _JAVA_GLIDE_STATE_CONTROLLER)]
        playerAnimates.append((_JAVA_GLIDE_STATE_KEY, _CONTROLLER_ANIMATE_GATE))
        # 原版动画栈整体让位 + Java 代码级头部跟踪的等价物(见常量注)
        mergedAnimEntries = mergedAnimEntries + [(_JAVA_HEAD_LOOK_KEY, _JAVA_HEAD_LOOK_ANIMATION)]
        playerAnimates.append((_VANILLA_ROOT_KEY, _VANILLA_ROOT_CONDITION))
        playerAnimates.append((_JAVA_HEAD_LOOK_KEY, _CONTROLLER_ANIMATE_GATE))
        # 逐帧输入状态(格挡/使用锁存 + 挥动序号, 见 _BLOCKING_SIGNAL 注): 排在最前, 同一帧里
        # 后面的状态机与条件读到的就是本帧的值; 全渲染域恒开(第一人称挥击同样依赖序号)
        mergedAnimEntries = mergedAnimEntries + [(_JAVA_INPUT_STATE_KEY, _JAVA_INPUT_STATE_ANIMATION)]
        playerAnimates.insert(0, (_JAVA_INPUT_STATE_KEY, _JAVA_INPUT_STATE_CONDITION))
        # 使用状态控制器排在输入状态动画之前(锁存同帧读到; 骨骼通道里查物品使用不可靠, 见其常量注)
        autoCtlEntries = autoCtlEntries + [(_JAVA_USE_STATE_KEY, _JAVA_USE_STATE_CONTROLLER)]
        playerAnimates.insert(0, (_JAVA_USE_STATE_KEY, _JAVA_USE_STATE_CONDITION))
    else:
        autoCtlEntries, conditionalAnimates, fpArmAnimates, _unusedStates = \
            _ApplyOneShotControllers(_NamespaceName(animNs), modelOwnKeys, autoCtlEntries,
                                     conditionalAnimates, fpArmAnimates, [])
        playerAnimates = autoAnimateEntries + conditionalAnimates
        if any(key in _SWING_SEEN_VARIABLES for key, _ctlId in autoCtlEntries):
            # 非 Java 模式的包若也带了挥击状态机(旧形态移植产物), 同样需要挥动序号
            modelAnimEntries.append((_JAVA_INPUT_STATE_KEY, _JAVA_INPUT_STATE_ANIMATION))
            playerAnimates.insert(0, (_JAVA_INPUT_STATE_KEY, _JAVA_INPUT_STATE_CONDITION))
            autoCtlEntries = autoCtlEntries + [(_JAVA_USE_STATE_KEY, _JAVA_USE_STATE_CONTROLLER)]
            playerAnimates.insert(0, (_JAVA_USE_STATE_KEY, _JAVA_USE_STATE_CONDITION))

    # Java height_scale/width_scale 是渲染缩放(poseStack.scale), 与网易实体缩放同为
    # 视觉缩放但基准不同: Java 默认 0.7 ↔ 网易默认 0.8(内置模型两侧对应), 按比例换算
    playerScale = netease.get("player_scale")
    if playerScale is None:
        heightScale = properties.get("height_scale")
        widthScale = properties.get("width_scale")
        # Java(RawModelAssembler): properties 为缺省 0.7 时回读 main 几何文件
        # description 内声明(文件声明非 0.7 者胜出), 按分量各自判定
        if (heightScale is None or _IsDefaultJavaScale(heightScale)
                or widthScale is None or _IsDefaultJavaScale(widthScale)):
            fileHeight, fileWidth = _ReadModelFileScales(jsonDict, readTextFunc, geometry)
            if (heightScale is None or _IsDefaultJavaScale(heightScale)) \
                    and fileHeight is not None and not _IsDefaultJavaScale(fileHeight):
                heightScale = fileHeight
            if (widthScale is None or _IsDefaultJavaScale(widthScale)) \
                    and fileWidth is not None and not _IsDefaultJavaScale(fileWidth):
                widthScale = fileWidth
        if isinstance(heightScale, (int, float)):
            playerScale = round(heightScale * 0.8 / 0.7, 3)
            if isinstance(widthScale, (int, float)) and abs(widthScale - heightScale) > 1e-6:
                warnings.append("width_scale({}) 与 height_scale({}) 不等, 网易仅支持等比缩放, "
                                "已按 height_scale 换算".format(widthScale, heightScale))
        else:
            playerScale = 0.8

    # 新版(JSON 包)模型渲染: 贴图/几何写入原版 default 键(卸模型由引擎
    # ResetEntityExtraSkin 整体还原), 第三人称走自有 ysm_main 控制器, 第一
    # 人称走 arm 控制器("arm" 键)。**两份 arm 控制器按是否声明 model.arm 二选一**:
    # 声明了的走 first_person_ysm_arm(part_visibility 全放行 —— 移植工具已把 arm 几何
    # 重建成"原版手臂骨架包装 + 移位后的 Java 子树", 几何里只有手臂, 而 Java 渲染整个
    # RightArm 子树, 按骨骼名前缀过滤会掉件); 未声明的 "arm" 键回落主几何(整个身体),
    # 只能继续走 first_person_ysm_arm_main 的主手侧白名单剔除法。旧剔除控制器
    # first_person_ysm 与 arm 控制器职责重复, 从基线去掉。旧版 py 副包的 config
    # 自带控制器表, 不经此基线, 历史通道不变。
    baseRenderControllers = [
        (_YSM_MAIN_CONTROLLER, _YSM_MAIN_CONDITION),
        (_FIRST_PERSON_ARM_CONTROLLER if armGeometry else _FIRST_PERSON_ARM_MAIN_CONTROLLER,
         _FP_ARM_CONTROLLER_CONDITION),
    ] + [
        (name, condition) for name, condition in BASE_RENDER_CONTROLLERS
        if name != _FIRST_PERSON_CARVE_CONTROLLER
    ]

    # Java 模式的底表: 动画=官方基线换血结果(mergedAnimEntries), 控制器/animate
    # 零旧版基线 —— 旧版共享观感资源(fight 战斗状态机/TACZ/CarryOn/actor 姿态族)
    # 一概不带, 模型动画失败时表现为失败, 不再被旧版动画顶替。
    defaultSkin = {
        "player_scale": playerScale,
        "gui_scale": netease.get("gui_scale", 1.0),
        "texture": textureMap["default"],
        "geometry": geometry,
        "animations": _RemoveEntries(
            mergedAnimEntries if javaMode
            else BuildEntries(BASE_ANIMATIONS, modelAnimEntries),
            netease.get("animations_remove"),
        ),
        "animation_controllers": _RemoveEntries(
            BuildEntries(
                [] if javaMode else BASE_ANIMATION_CONTROLLERS,
                autoCtlEntries + _AsEntryListWithBare(
                    netease.get("animation_controllers_extra"),
                    lambda key: "controller.{}.{}".format(animNs, key))),
            netease.get("animation_controllers_remove"),
        ),
        "animate": _RemoveEntries(
            BuildEntries(
                [] if javaMode else BASE_ANIMATE,
                playerAnimates + fpArmAnimates
                # files.player.animate = 播放条件声明(基岩扩展字段, 与注册声明
                # 同居 files.player); netease.animate_extra 兼容形态在后覆盖
                + _AsEntryList(playerFiles.get("animate"))
                + _AsEntryList(netease.get("animate_extra"))),
            # animate_remove 只对**旧版声明形态**有意义: 它是相对旧版共享基线 animate 表
            # (BASE_ANIMATE)的差量(gen_builtin_json.py 生成内置包时就在算这个差量)。
            # Java 格式包没有基线表, 全部条目由包自身内容合成, 通道接管也已自动推导
            # (见 _DrivenParallelKeys) —— 写它无意义, 一律忽略。
            None if javaMode else netease.get("animate_remove"),
        ),
        "render_controllers": _RemoveEntries(
            BuildEntries(baseRenderControllers,
                         _AsEntryList(playerFiles.get("render_controllers"))
                         + _AsEntryList(netease.get("render_controllers_extra"))),
            netease.get("render_controllers_remove"),
        ),
    }
    if javaMode:
        # 运行层豁免标记: normalizer 跳过旧版共享资源注入(injectRes)
        defaultSkin["_ysmJavaMode"] = True
    # 新版模型标记: 渲染层据此走 ysm_main/arm 控制器通道(并移除原版控制器),
    # 区别于旧版副包自带控制器表的历史通道
    # (normalizer 会把该标记与 geometry_arm 一起继承到 inherit 皮肤)
    defaultSkin["_ysmJsonModel"] = True
    if armGeometry:
        defaultSkin["geometry_arm"] = armGeometry
    # Java properties.preview_animation(字符串) 语义为 GUI 展示动画 → 网易 gui_animation
    guiAnimation = netease.get("gui_animation")
    if not guiAnimation:
        javaPreview = properties.get("preview_animation")
        if isinstance(javaPreview, str) and javaPreview:
            guiAnimation = "{}.{}".format(animNs, javaPreview)
    if guiAnimation:
        # Java 对 preview_animation 指向不存在动画的包静默不播(野外包常见: 声明了
        # stage 但动画文件里没有); 基岩 GUI 注册不带资源校验会逐帧刷 can't find
        # —— 索引确认缺失时回落 idle(同为注册表实际值, 缺 idle 则不注册)并告警
        index = _GetResourceIndex()
        if index is not None and hasattr(index, "IsAnimationMissing")                 and index.IsAnimationMissing(guiAnimation):
            registeredIdle = dict(defaultSkin["animations"]).get("idle")
            fallback = registeredIdle if (
                registeredIdle and not index.IsAnimationMissing(registeredIdle)) else None
            warnings.append(
                "GUI 展示动画 {} 在资源包中不存在(Java 对缺失的 preview_animation 静默"
                "不播), 已{}".format(guiAnimation, "回落 " + fallback if fallback else "跳过"))
            guiAnimation = fallback
    if guiAnimation:
        defaultSkin["gui_animation"] = guiAnimation
    if netease.get("material"):
        defaultSkin["material"] = netease.get("material")
    if isinstance(netease.get("arrow"), dict):
        defaultSkin["arrow"] = _NormalizeReplacePatch(netease.get("arrow"))
    # Java projectiles/vehicles → replace_entities(上方已构建; netease 同名字段逐 ID
    # 字段级浅合并: 推导覆盖 geometry/texture, 补声明 animations 等推导不出的字段)
    neteaseReplace = netease.get("replace_entities")
    if isinstance(neteaseReplace, dict):
        for entityId in _OrderedKeys(neteaseReplace):
            patch = _NormalizeReplacePatch(neteaseReplace[entityId])
            if not isinstance(patch, dict):
                continue
            if entityId in replaceEntities:
                merged = OrderedDict(replaceEntities[entityId])
                merged.update(patch)
                replaceEntities[entityId] = merged
            else:
                replaceEntities[entityId] = patch
    if replaceEntities:
        defaultSkin["replace_entities"] = replaceEntities
        # 箭矢沿用既有专用链路: 推导出的 minecraft:arrow 条目同步派生旧 arrow 键
        if "arrow" not in defaultSkin and "minecraft:arrow" in replaceEntities:
            defaultSkin["arrow"] = replaceEntities["minecraft:arrow"]
    # 按钮格的"播放动画"按最终动画表定稿(Java 模式含官方基线兜底的 extra0-7 等)
    playableKeys = set(key for key, _animId in defaultSkin["animations"])
    extraButtons = _FinalizeRouletteEntries(extraButtons, playableKeys)
    for classifyItem in classifyList:
        classifyItem["extra_animation"] = _FinalizeRouletteEntries(
            classifyItem["extra_animation"], playableKeys)
    if extraButtons:
        defaultSkin["extra"] = extraButtons
    if classifyList:
        defaultSkin["extra_animation_classify"] = classifyList
    if buttonsList:
        defaultSkin["extra_animation_buttons"] = buttonsList
    # 皮肤级简单字段直通(存在才写入, 与内置模型 schema 一致; netease 覆盖 Java 推导)
    # 音效/粒子亦可声明在 files.player 下(基岩扩展字段, 资源声明统一收进 files)
    for passKey in ("sound_effect", "particle_effect"):
        if playerFiles.get(passKey) is not None:
            defaultSkin[passKey] = playerFiles.get(passKey)
    for passKey in ("y_offset", "main_scale", "strum", "strum_dict", "sound_effect",
                    "particle_effect", "extra_animation_buttons", "extra_animation_classify"):
        if netease.get(passKey) is not None:
            defaultSkin[passKey] = netease.get(passKey)

    skinSwitch = {"default": defaultSkin}
    for skinName in skinSort[1:]:
        # 非 default 皮肤仅声明贴图, 其余资源经 inherit 走与内置模型相同的归一化链路
        skinSwitch[skinName] = {"inherit": True, "texture": textureMap[skinName]}

    # netease.skins: 皮肤级覆盖/补充({皮肤名: {字段...}}), 浅覆盖到合成结果上;
    # 用于表达推导无法覆盖的皮肤差异(如发光皮肤的 material、非继承的完整皮肤声明)
    skinOverrides = netease.get("skins")
    if isinstance(skinOverrides, dict):
        for skinName in skinOverrides:
            override = skinOverrides[skinName]
            if not isinstance(override, dict):
                continue
            if skinName not in skinSwitch:
                skinSwitch[skinName] = {}
                if skinName not in skinSort:
                    skinSort.append(skinName)
            skinSwitch[skinName].update(override)

    # ---- 预览动画列表(选择界面循环播放表) ----
    previewOverride = _AsPreviewEntries(netease.get("preview_animation"))
    if previewOverride:
        previewAnims = previewOverride
    else:
        # 动画 ID 优先取**注册表实际值**(同 key): 模型声明覆盖过的键(如网易键名
        # use_righthand 文件、male 的 run→run_man 变体)预览播真实注册的动画,
        # 不再需要 preview_animation_extra 手工纠偏; 未注册的键回落标准推导
        registeredAnims = dict(defaultSkin["animations"])
        previewAnims = [
            (key, registeredAnims.get(key) or "{}.{}".format(animNs, javaKey), label)
            for key, javaKey, label in STANDARD_PREVIEW_ANIMS
        ] + ([] if javaMode else list(CARRYON_PREVIEW_ANIMS))
        # Java 模式不带 CarryOn 共享预览(指挥官系动画, 旧版观感资源);
        # 模型自己声明的 carryon 动画仍可经 preview_animation_extra 登记
        # preview_animation_extra: {"键": ["动画ID", "显示名"]} 对标准表按键覆盖/追加
        # (整表偏离才用 preview_animation; 个别差异用增量, 如动画文件沿用网易键名
        # use_righthand 时覆盖标准推导拼出的 use_mainhand)
        extraPreview = _AsPreviewEntries(netease.get("preview_animation_extra"))
        if extraPreview:
            indexByKey = dict((entry[0], index) for index, entry in enumerate(previewAnims))
            for entry in extraPreview:
                if entry[0] in indexByKey:
                    previewAnims[indexByKey[entry[0]]] = entry
                else:
                    indexByKey[entry[0]] = len(previewAnims)
                    previewAnims.append(entry)
        removePreview = netease.get("preview_animation_remove")
        if removePreview:
            removeSet = set(removePreview)
            previewAnims = [entry for entry in previewAnims if entry[0] not in removeSet]

    config = {
        "text": metadata.get("name") or packName,
        "entityIdentifier": modelId,
        "tips": metadata.get("tips", ""),
        "authors": _BuildAuthors(metadata),
        "screen_sort": skinSort,
        "molang_bind_bones_list": list(netease.get("molang_bind_bones_list") or []),
        "skin_switch": skinSwitch,
        "preview_animation": previewAnims,
        "_ysmJsonPack": packName,  # 来源标记(调试/去重用)
        # 注册优先级(顶层字段): 越大越靠前, 缺省 0; 主包内置模型为 1000。
        # 注册时按此值插入列表, 保证 UI 顺序与存档 roleIndex 的稳定性。
        "_ysmPriority": int(jsonDict.get("priority", netease.get("priority", 0)) or 0),
    }
    # molang 变量初始化(netease.initialize 显式 + config_forms/文件扫描自动);
    # _ysm 前缀 = 运行期内部键, 由 modelInstaller 注册进共享 initialize 列表
    # (文件扫描来源单列, 装载时对照在场变量名过滤后并回 _ysmInitialize)
    initializeList, scannedInitList = _BuildInitialize(netease, buttonsList, fileMolangVars, warnings)
    if initializeList:
        config["_ysmInitialize"] = initializeList
    if scannedInitList:
        config["_ysmScannedInitialize"] = scannedInitList
    # GUI 预览的并行叠加层: Java 的 ParallelPredicate 恒 LOOP **含 GUI** —— 隐藏/
    # 摆位装饰部件的 parallel/pre_parallel 不叠上去, 预览就是零件摊开的杂乱状态。
    # javaMode 自动取模型自有的并行动画全表(列表形态, previewRender 逐条注册);
    # netease.preview_parallel 显式声明(字符串或列表)仍优先。
    previewParallel = netease.get("preview_parallel")
    if not previewParallel and javaMode:
        previewParallel = [animId for key, animId in modelAnimEntries
                           if _ParallelFamilyBase(key) is not None]
    if previewParallel:
        config["preview_parallel"] = previewParallel
    # 并行族伴生在预览实体上的权重({动画ID: 表达式}, 取自移植工具写的顶级 channel_ownership):
    # Java 的 GUI 展示动画播在 cap 通道, 晚于 pre_parallel —— 官方酒狐各包的 pre_parallel 把舞台骨骼
    # (幕布/背景板)缩成 0 藏起来, 展示动画再放出来; 基岩缩放相乘, 不让位卡片上就什么都没有。移植工具把
    # 同写的通道拆进伴生, 权重读 variable.ysm_show(卡片纸娃娃置 1; 世界与大预览窗恒 0 = 伴生恒播)。
    # 其余项(世界侧晚层的占用集合)在预览实体上读不到变量, ??0 后恒为"不让出", 等于无权重。
    ownershipWeights = netease.get("channel_ownership")
    if javaMode and isinstance(ownershipWeights, dict):
        previewWeights = OrderedDict(
            (animId, ownershipWeights[key]) for key, animId in modelAnimEntries
            if _IsOwnershipCompanionKey(key) and _ParallelFamilyBase(key) is not None
            and isinstance(ownershipWeights.get(key), _KEY_STRING_TYPES) and ownershipWeights.get(key))
        if previewWeights:
            config["preview_parallel_weights"] = previewWeights
    # Java properties.disable_preview_rotation: 卡片默认把模型转过 20° 并略俯视(RenderUtil.renderModelInGui:
    # yBodyRot 200、姿态栈绕 X -10°), 置 true 才正对镜头。展示动画是按各自的取景摆的(莫莫/小/大酒狐不带该标志),
    # 一律正对就歪了。旧版写法的 JSON 包与 py 模型按基岩原有取景(正对), 不受影响 —— 消费侧(client/ui/modelCard)
    # 缺键按正对处理, 这里只在"要带 Java 缺省转角"时才写键(False), 旧包配置与历史基线逐键一致
    if not bool(netease.get("disable_preview_rotation",
                            properties.get("disable_preview_rotation", not javaMode))):
        config["disable_preview_rotation"] = False
    # 选择界面卡片取景: Java 模式包按 Java 卡片视口(52x70 GUI px、每模型单位 1.3125 px、脚底位置)渲染,
    # 官方酒狐的舞台骨骼(Background1 边框 / 幕布)才对得上卡片边; 旧版模型保持原控件与 gui_scale 口径。
    # 消费侧 client/ui/modelCard.RenderModelCard(换算与实测的纸娃娃映射见其模块注释)
    if javaMode:
        config["gui_card_framing"] = netease.get("gui_card_framing", "java")
    # 通道覆盖伴生动画(见 _OWNERSHIP_COMPANION_PATTERN 注): GUI 预览播展示动画/预览动作表时
    # 连同伴生一起播(previewRender.RegisterPreviewForModel), 预览里才是原动画的完整姿态
    if javaMode:
        previewCompanions = _OwnershipCompanionIds(modelAnimEntries)
        if previewCompanions:
            config["preview_companions"] = previewCompanions
    if netease.get("icon"):
        config["icon"] = netease.get("icon")
    else:
        # Java properties.icon 为包内图片路径 → 取文件名, 按贴图放置约定映射
        javaIcon = properties.get("icon")
        if isinstance(javaIcon, str) and javaIcon:
            iconPath = _TextureEntryPath(javaIcon)
            if iconPath:
                config["icon"] = "textures/entity/{}/{}".format(packName, iconPath.split("/")[-1])
    # GUI 卡片前景/背景图(Java properties.gui_background / gui_foreground, CatalogModelButton.renderWidget:
    # 背景图铺满卡片在纸娃娃之下, 前景图在纸娃娃之上、名字之下)。与 icon 同一映射规则: 取文件名 →
    # textures/entity/<包>/<文件名>(移植工具已把文件名规整成 gui_<角色>); netease 同名键写资源包路径直接覆盖。
    # 卡片模板 ysmCommonControls.ysmSelectModelButton 的同名 image 由 client/ui/modelCard.py 置图
    for guiKey in ("gui_background", "gui_foreground"):
        explicitImage = netease.get(guiKey)
        if isinstance(explicitImage, _KEY_STRING_TYPES) and explicitImage:
            config[guiKey] = explicitImage
            continue
        javaImage = properties.get(guiKey)
        if isinstance(javaImage, _KEY_STRING_TYPES) and javaImage:
            imagePath = _TextureEntryPath(javaImage)
            if imagePath:
                config[guiKey] = "textures/entity/{}/{}".format(packName, imagePath.split("/")[-1])
    # GUI 换肤渲染控制器: 统一缺省(皮肤资源由运行层按预览实体动态追加进数组,
    # 控制器只是种子, 所有模型共用), 声明仅用于覆盖
    config["gui_render_controller"] = (
        netease.get("gui_render_controller") or DEFAULT_GUI_RENDER_CONTROLLER)

    # 提示暂未自动转换的 Java 高级特性
    files = jsonDict.get("files") or {}
    playerFiles = files.get("player") or {}
    if (playerFiles.get("animation_controllers") and not ctlLoaded
            and not netease.get("animation_controllers_extra")):
        warnings.append(
            "检测到自定义动画控制器文件声明但未能读取: 把控制器文件(ID 改为 "
            "controller.animation.{}.<名称>)复制一份进 BP 模型目录对应路径即可自动注册, "
            "或经 netease.animation_controllers_extra/animate_extra 显式声明".format(packName)
        )
    if files.get("arrow") and "arrow" not in defaultSkin:
        warnings.append("files.arrow(Java 已废弃字段)声明不完整未能转换(缺 model?), "
                        "请改用 files.projectiles 或 netease.arrow")
    return config, warnings
