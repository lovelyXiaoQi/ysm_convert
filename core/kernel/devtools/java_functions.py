# -*- coding: utf-8 -*-
"""Java 自定义函数(functions/*.molang, YSM 2.5.0+) → 基岩 molang(Python 2.7 / 3)。设计与实测见 docs/ysm-java-functions.md。

Java 语义(源码 + Wiki《自定义函数》):
- 函数文件 <files.function_path, 缺省 functions>/<名>.molang(递归), 名字大小写不敏感; `名@事件.molang` 既是可调用
  函数 fn.名 又订阅事件, `@事件.molang` 只订阅(ModelRenderTargetAssembler.buildUserFunctionMap / buildEventHandlers)。
  事件: player_init(模型装载后第一次动画更新前), player_update(每次动画更新前, args[0] = 本帧是否逻辑 tick),
  sync(ysm.sync 经服务器广播), defer(ysm.defer 排队, 本轮动画求值后倒序), player_ctrl_<通道>(脚本控制器)。
- 调用 fn.名(实参...)(无参可省括号): 每次调用压一个栈帧(StackMemory) —— t.* 各帧独立、v.* 共享, args[i] 取实参
  (负下标按 0、越界 null), 调用链上限 32 层(超出返回 null); return 穿透 {…} 与循环直接结束本次调用(Wiki
  "闭包返回"); 没有执行到 return 时返回最后一条语句的值; 函数不存在 / 文件解析失败返回 null / 0。
- ysm.play_sound(实例id, 音效名[, 模式[, 音量[, 音高]]]): 模式位 1 = 同 id 强制替换(否则旧实例还在响就不播),
  2 = 全局管理器, 4 = 循环; 音量音高夹在 [0.001, 1000]。ysm.stop_sound(id[, 全局]) / stop_all_sounds([全局])。

基岩没有用户函数(2026-09-23 实机: 基岩的 return 穿透块与循环结束**整个表达式**, loop 里 break / continue 可用,
loop(2000) 照跑, **读未定义的 t./v. 让整段表达式中止**返回 0)。编译策略:
1. 调用处内联。能折成纯表达式的函数(临时变量赋值 + if/else 链上的 return, 无副作用)直接换成等价表达式 ——
   宿主表达式形态与短路语义不变(官方酒狐 15 号 halo_battery_indicator(n) 折成饱食度比较);
2. 其余按语句内联: 实参与临时变量改名 t.ysm_f<K>_*, 调用连同所在的短路/三元分支条件一起提到宿主语句之前;
   函数体里不在末尾的 return 改写成"写结果变量 + break", 函数体包进 loop(1, {…}) —— 否则基岩的 return 会连
   宿主表达式一起结束; 循环里的 return 另置完成标记逐层 break; 函数体里的 second_order / first_order 在这里
   就逐语句改写(积分语句紧贴所在语句, 与 Java 的求值顺序一致);
3. 宿主服务(ysm.play_sound / stop_sound / stop_all_sounds)改写成"请求计数器 + 参数变量"写进实体 molang 变量,
   主包 Python 运行层(ysmModelCoreScripts/client/javaFunctionHost.py)逐帧轮询后用网易音频接口播放 / 停止;
   调用点的静态信息写进 ysm.json 顶级 java_functions 声明;
4. player_update 事件体编进逐帧执行体动画 animation.<包>.ysm_fx_frame(主包 packParser 在 Java 模式 animate 表的
   共享状态动画之后恒开挂载); player_init 事件体追加进每实例变量初始化控制器的 on_entry。
5. 脚本控制器(@player_ctrl_<通道>, 见 ScriptChannel 注): 脚本编进执行体(排在 player_update 之后, 按 Java 通道序),
   连同 Java 动画播放器的状态机一起逐帧复刻, 生成的控制器 player.<通道> 按执行体算出的播放码切状态。
"""
import io
import os
import re
import struct
from collections import Counter, OrderedDict

import java_molang as jm

EXECUTOR_KEY = "ysm_fx_frame"
EXECUTOR_FILE = "ysm_functions.animation.json"
EXECUTOR_HOST_BONE = "Head"          # 与主包 animation.ysm.java_input_state 同一根宿主骨骼(第一人称的原版体型里也有)
DECLARATION_KEY = "java_functions"
MAX_CALL_DEPTH = 32                  # Java StackMemory.MAX_STACK_DEPTH
MAX_LOOP_ROUND = 1024                # Java StandardBindings.MAX_LOOP_ROUND

_NEEDS_REWRITE = re.compile(
    r"\bfn\s*\.\s*[A-Za-z_]|\bysm\s*\.\s*(?:play_sound|stop_sound|stop_all_sounds|sync|defer|keyboard|mouse)\b"
    r"|\b(?:q|query)\s*\.\s*debug_output\b"
    r"|\bctrl\s*\.\s*(?:set_animation|set_beginning_transition_length|reset|indicate_reload"
    r"|state_(?:continue|stop|pause|bypass)|loop|play_once|hold_on_last_frame)\b", re.IGNORECASE)
_JAVA_NAMESPACES = frozenset(("ysm", "ctrl", "q", "query", "math", "fn", "tlm"))
_TEMP_NAMESPACES = frozenset(("t", "temp"))
_HOST_PLAY = ("ysm", "play_sound")
_HOST_STOP = ("ysm", "stop_sound")
_HOST_STOP_ALL = ("ysm", "stop_all_sounds")
_HOST_SYNC = ("ysm", "sync")
_HOST_DEFER = ("ysm", "defer")
_DEBUG_CALLS = frozenset((("q", "debug_output"), ("query", "debug_output")))
# 会"发出"东西的宿主调用: Java 只在 allowEmitting 的求值里生效(事件体 / 脚本控制器 / 控制器 on_entry·on_exit /
# timeline 指令帧); 骨骼通道、转移条件、动画权重求值时 allowEmitting = false, 调用直接返回 null(参数都不求值)
_HOST_CALLS = frozenset((_HOST_PLAY, _HOST_STOP, _HOST_STOP_ALL, _HOST_SYNC, _HOST_DEFER))
_INPUT_KEYBOARD = ("ysm", "keyboard")
_INPUT_MOUSE = ("ysm", "mouse")
_INPUT_CALLS = frozenset((_INPUT_KEYBOARD, _INPUT_MOUSE))
_PHYSICS_CALL = re.compile(r"\b(?:ysm\.)?(?:second_order|first_order)\s*\(")

# 主包运行层维护的变量(读取不带 ??, 由文件扫描补 0 初始化即可): 执行体的逐帧守卫 / tick 相位, 宿主请求计数器与参数
FRAME_GUARD_VARIABLE = "v.ysm_fx_t"
TICK_PHASE_VARIABLE = "v.ysm_fx_tk"
SOUND_COUNTER = "v.ysm_hs_{}"
SOUND_VOLUME = "v.ysm_hs_{}_v"
SOUND_PITCH = "v.ysm_hs_{}_p"
STOP_COUNTER = "v.ysm_hx_{}"
STOP_ALL_COUNTER = "v.ysm_hxa_{}"
# ysm.sync 请求(执行体写, 宿主只轮询本机玩家): 每个调用点一个槽位的计数器 + 实参
SYNC_COUNTER = u"v.ysm_sy_{}"
SYNC_ARGUMENT = u"v.ysm_sy_{}_{}"
MAX_SYNC_ARGUMENTS = 16                   # Java Sync.MAX_ARGS_SIZE
# 运行层写的数值走主包注册的 query.mod(同 Java 运行层状态的口径: 没 Set 过的实体读注册默认值 0, 不会让表达式中止;
# 实体变量带 ?? 的写法过不了 PortMolangText —— ② 把 `v.x ?? 数字` 当作者默认值收走、其余 ?? 改成 ||): 本机玩家的键鼠
# 状态(GLFW 码, Java ysm.keyboard / ysm.mouse)、服务端转发来的同步事件(计数 / 实参 / 个数)。执行体写已处理的计数
# v.ysm_si_k(包变量, 初始化补 0)。支持的键码表与业务包 config/functionHost.GLFW_TO_NETEASE_KEYS 一致(网易有对应键的),
# devtools/test_function_host.py 守护
KEY_QUERY = u"query.mod.ysm_kb_{}"
MOUSE_QUERY = u"query.mod.ysm_ms_{}"
SUPPORTED_GLFW_KEYS = frozenset(
    [32, 39, 44, 45, 46, 47, 59, 61, 91, 92, 93, 96, 256, 257, 258, 259, 260, 261, 262, 263, 264, 265, 266, 267,
     268, 269, 280, 281, 282, 283, 284, 330, 331, 332, 333, 334, 335, 340, 341, 342, 343, 344, 345, 346, 347, 348]
    + list(range(48, 58)) + list(range(65, 91)) + list(range(290, 314)) + list(range(320, 330)))
SUPPORTED_GLFW_MOUSE = frozenset((0, 1, 2))   # 左 / 右 / 中
SYNC_EVENT_COUNTER = u"query.mod.ysm_si_c"
SYNC_EVENT_ARGUMENT = u"query.mod.ysm_si_{}"
SYNC_EVENT_COUNT = u"query.mod.ysm_si_n"
SYNC_EVENT_SEEN = u"v.ysm_si_k"

# ---- 脚本控制器(见 ScriptChannel 注) ----
# 第 2 阶段的通道: 内置谓词为空(pre_/post_ 的 main/hold/swing/use 族)或是并行动画(pre_parallel_* / parallel_*)。
# main / use / swing 等通道的内置谓词是主链 / 一次性状态机, 在第 3 阶段
SCRIPT_EVENT_PREFIX = u"player_ctrl_"
SCRIPT_CHANNEL_PATTERN = re.compile(r"^(?:pre_parallel_.+|parallel_.+|(?:pre|post)_(?:main|hold|swing|use)(?:_.+)?)$")
_BUILTIN_PARALLEL_CHANNEL = re.compile(r"^(pre_)?parallel_([0-7])$")   # ParallelControllerDiscovery: 后缀 0~7 且包里有该动画
SCRIPT_CONTROLLER_PREFIX = "ysm_fx_"      # 生成的控制器文件 ysm_fx_<通道>.json(决策树转换的是 ysm_script_)
SCRIPT_VARIABLE = u"v.ysm_sc{}_{}"
SCRIPT_TEMP = u"t.ysm_sc{}_{}"
ENDING_TRANSITION_SECONDS = 0.15          # AnimationData.DEFAULT_ENDING_TRANSITION_LENGTH = 3 tick
INFINITE_LENGTH_SECONDS = 1e6             # Java 无关键帧时长 = Float.MAX_VALUE(同移植工具 JAVA_INFINITE_LENGTH)
LOOP_TYPE_LOOP, LOOP_TYPE_ONCE, LOOP_TYPE_HOLD = 1, 2, 3
_LOOP_OVERRIDE_CODES = {10: LOOP_TYPE_LOOP, 11: LOOP_TYPE_ONCE, 12: LOOP_TYPE_HOLD}   # CtrlBinding.LOOP/PLAY_ONCE/HOLD
_CTRL_CONSTANTS = {"state_continue": 2, "state_stop": 3, "state_pause": 4, "state_bypass": 5,
                   "loop": 10, "play_once": 11, "hold_on_last_frame": 12}
_CTRL_SET_ANIMATION = ("ctrl", "set_animation")
_CTRL_SET_BLEND = ("ctrl", "set_beginning_transition_length")
_CTRL_RESET = ("ctrl", "reset")
_CTRL_RELOAD = ("ctrl", "indicate_reload")
_CTRL_HOOKS = frozenset((_CTRL_SET_ANIMATION, _CTRL_SET_BLEND, _CTRL_RESET, _CTRL_RELOAD))
_FINISHED_QUERIES = frozenset((("q", "all_animations_finished"), ("query", "all_animations_finished"),
                               ("q", "any_animation_finished"), ("query", "any_animation_finished")))


# ======================================================================
# 函数目录
# ======================================================================
class FunctionDef(object):
    """一个函数文件"""

    def __init__(self, fileName, name, events, statements, error=None, text=u""):
        self.fileName = fileName      # 相对函数目录的路径(不带扩展名, / 分隔)
        self.name = name              # 可调用名(小写); "" = 只订阅事件
        self.events = events          # 订阅的事件名(小写)
        self.statements = statements  # 语句列表; 解析失败为 []
        self.error = error
        self.text = text              # 原文(脚本控制器先试决策树转换, 它自己解析)


class FunctionSet(object):
    """一个包的全部函数文件"""

    def __init__(self):
        self.functions = OrderedDict()   # 可调用名 → FunctionDef
        self.handlers = OrderedDict()    # 事件名 → [FunctionDef]
        self.files = []

    def Add(self, definition):
        self.files.append(definition)
        if definition.name and definition.name not in self.functions:
            self.functions[definition.name] = definition
        for event in definition.events:
            self.handlers.setdefault(event, []).append(definition)

    def Handlers(self, event):
        return list(self.handlers.get(event, ()))

    def __len__(self):
        return len(self.files)


def _SplitFunctionName(relative):
    """相对路径(无扩展名) → (可调用名, [事件名]); 规则同 Java buildUserFunctionMap / buildEventHandlers"""
    at = relative.find(u"@")
    if at == -1:
        return relative.lower(), []
    name = u"" if at == 0 else relative[:at].lower()
    event = relative[at + 1:].lower()
    return name, ([event] if event else [])


def ParseFunctionText(text):
    """函数文件文本 → (语句列表, 错误); Java 解析失败整个函数落成 0(MolangParser.parseExpression)"""
    try:
        return jm.ParseProgram(jm.StripComments(text)), None
    except jm.MolangParseError as error:
        return [], error.args[0] if error.args else u"解析失败"


def LoadFunctionSet(javaDir, functionPath=None):
    """Java 包的函数目录 → FunctionSet(目录不存在返回空集合)"""
    functionSet = FunctionSet()
    relativeRoot = (functionPath or u"functions").strip(u"/").strip(u"\\") or u"functions"
    root = os.path.join(javaDir, relativeRoot.replace(u"/", os.sep))
    if not os.path.isdir(root):
        return functionSet
    for base, dirs, files in sorted(os.walk(root)):
        dirs.sort()
        for fileName in sorted(files):
            if not fileName.lower().endswith(u".molang"):
                continue
            path = os.path.join(base, fileName)
            relative = os.path.relpath(path, root).replace(os.sep, u"/")
            relative = relative[:-len(u".molang")]
            if isinstance(relative, bytes):
                relative = relative.decode("utf-8")
            with io.open(path, encoding="utf-8-sig") as handle:
                text = handle.read()
            statements, error = ParseFunctionText(text)
            name, events = _SplitFunctionName(relative)
            functionSet.Add(FunctionDef(relative, name, events, statements, error, text))
    return functionSet


def ScriptHandlers(functionSet):
    """脚本控制器事件 → [(通道名, 处理函数)]; 同一事件多个文件时 Java 只用第一个(CodedAnimationController.updateModel)"""
    out = []
    for event, handlers in functionSet.handlers.items():
        if event.startswith(SCRIPT_EVENT_PREFIX) and handlers and len(event) > len(SCRIPT_EVENT_PREFIX):
            out.append((event[len(SCRIPT_EVENT_PREFIX):], handlers[0]))
    return out


def JavaLoopType(value):
    """Java 动画 JSON 的 loop 字段 → LOOP_TYPE_*(LoopType.fromJson: 缺省 / false / 认不出 = PLAY_ONCE)"""
    if value is True:
        return LOOP_TYPE_LOOP
    if isinstance(value, jm._STRING_TYPES):
        lowered = value.strip().lower()
        if lowered in (u"true", u"loop"):
            return LOOP_TYPE_LOOP
        if lowered == u"hold_on_last_frame":
            return LOOP_TYPE_HOLD
    return LOOP_TYPE_ONCE


def AnimationLengthSeconds(body):
    """Java Animation.animationLength(秒): animation_length 数值优先, 否则骨骼关键帧的最大时间戳(calculateLength),
    没有时间戳关键帧 = 无限长"""
    length = body.get("animation_length") if isinstance(body, dict) else None
    if isinstance(length, (int, float)) and not isinstance(length, bool):
        return float(length)
    longest = 0.0
    for channels in ((body or {}).get("bones") or {}).values():
        if not isinstance(channels, dict):
            continue
        for channel in ("rotation", "position", "scale"):
            value = channels.get(channel)
            if not isinstance(value, dict) or any(side in value for side in ("pre", "post", "lerp_mode")):
                continue
            for stamp in value:
                try:
                    longest = max(longest, float(stamp))
                except (TypeError, ValueError):
                    continue
    return longest if longest > 0 else INFINITE_LENGTH_SECONDS


# ======================================================================
# 音频时长(非强制模式"同 id 旧实例还在响"要靠它估算: 网易场景音效没有自然播完的事件)
# ======================================================================
def OggDurationSeconds(path):
    """Ogg Vorbis 文件时长(秒); 读不出返回 None。首个识别头取采样率, 末页取 granule position"""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except (IOError, OSError):
        return None
    marker = data.find(b"\x01vorbis")
    if marker < 0 or marker + 16 > len(data):
        return None
    rate = struct.unpack("<I", data[marker + 12:marker + 16])[0]
    last = data.rfind(b"OggS")
    if rate <= 0 or last < 0 or last + 14 > len(data):
        return None
    granule = struct.unpack("<q", data[last + 6:last + 14])[0]
    if granule <= 0:
        return None
    return round(float(granule) / rate, 3)


# ======================================================================
# 脚本控制器(@player_ctrl_<通道>)
# ======================================================================
class ScriptChannelError(Exception):
    """脚本控制器转换不了(整份跳过并留痕)"""


def _SafeChannel(channel):
    return re.sub(r"[^a-z0-9_]", "_", channel.lower())


class ScriptChannel(object):
    """一个脚本控制器通道。

    Java(CodedAnimationController.process + AnimationPlayer): 每帧先用"上一帧末的播完标记"设好
    all/any_animations_finished, 跑脚本(ctrl.set_animation 等直接改播放器), 返回值取整: 2 继续 / 3 停止 / 4 暂停,
    其余交回通道内置谓词(空谓词 = 停止; 并行通道且包里有 pre_parallelN / parallelN = 循环播放它)。播放器状态:
    上次设置(名 + 循环覆盖; 同名同覆盖再设是空操作)、待载入、当前动画、状态(空闲 / 起始过渡 / 播放 / 尾过渡)、
    计时起点、播完标记。设置不同的动画会立刻硬撤当前动画, 待载入的动画在下一次"继续"时才开始播;
    "停止"只让播放中的动画进尾过渡(并清掉上次设置), 空闲时什么都不做 —— 眨眼脚本就是靠
    "设置 + 返回停止、下一帧返回继续"来触发一次性动画的。

    编译: 执行体逐帧跑脚本并照抄这套状态机(实体变量 v.ysm_sc<序号>_<字段>, 字段见 FIELDS), 最后算出播放码
    ps(起始过渡 / 播放中 = 当前动画序号*2 + 载入次数的奇偶, 否则 0)。生成的控制器 player.<通道> 每个动画两个状态
    (孪生态: 重新载入 = 换到另一个 = 基岩从头播), 全部按播放码切换。基岩按**离开**的状态取 blend: 空闲态取起始过渡
    (脚本里 set_beginning_transition_length 的常量实参), 播放态取 Java 尾过渡 3 tick(停止 / 播完都是从当前姿态淡回);
    两个动画直接切换时 Java 是"硬撤 + 起始过渡", 这里是尾过渡时长的交叉淡化(近似)。
    """

    FIELDS = (
        "ls",                    # 上次设置码: 0 = 无; 名序号*4 + 循环覆盖码
        "nx", "nl", "nt", "nr",  # 待载入: 动画序号(0 = 无) / 长度秒 / 循环类型 / 循环时要不要让基岩重播
        "cu", "ln", "lt", "rs",  # 当前: 同上
        "st",                    # 0 空闲 / 1 起始过渡 / 2 播放 / 3 尾过渡
        "of",                    # 计时起点(q.life_time)
        "nf",                    # 播完标记取反(Java 初值"已播完" = 0, 实体变量扫描补 0 即对)
        "bt",                    # 起始过渡秒数(通道缺省 0)
        "pa",                    # 暂停(骨骼不应用)
        "sr",                    # 载入次数
        "ps",                    # 播放码(控制器读)
    )

    def __init__(self, index, channel, handler, builtin=None):
        self.index = index
        self.channel = channel
        self.handler = handler
        self.builtin = builtin           # 内置谓词循环播放的动画原名(并行通道); None = 空谓词
        self.names = []                  # set_animation 用到的动画原名(含包里没有的): 上次设置码的名序号
        self.animations = []             # 其中包里有的: 状态序号 = 下标 + 1
        self.loopTypes = {}              # 动画原名 → Java 自身循环类型
        self.blends = []                 # set_beginning_transition_length 的常量实参(秒, 文本序)
        self.pausable = False
        self.notes = []
        self.info = {}                   # 动画原名 → (长度秒, 基岩文件自己循环); 动画改写之后由移植工具填

    def VariableText(self, field):
        return SCRIPT_VARIABLE.format(self.index, field)

    def TempText(self, field):
        return SCRIPT_TEMP.format(self.index, field)

    @property
    def fileName(self):
        return u"{}{}.json".format(SCRIPT_CONTROLLER_PREFIX, _SafeChannel(self.channel))

    @property
    def controllerName(self):
        return u"player.{}".format(self.channel)

    @property
    def staticBlend(self):
        """起始过渡的静态近似: 脚本里最后一个常量实参; 没设过 = 通道缺省 0"""
        return self.blends[-1] if self.blends else 0.0

    def LastSetCode(self, name, override):
        return (self.names.index(name) + 1) * 4 + override

    def AnimationIndex(self, name):
        return self.animations.index(name) + 1 if name in self.animations else 0

    @staticmethod
    def StateName(position, parity):
        return u"a{}{}".format(position, u"_r" if parity else u"")

    def _StateCodes(self):
        return [(self.StateName(position, parity), position * 2 + parity)
                for position in range(1, len(self.animations) + 1) for parity in (0, 1)]

    def BuildController(self):
        """Java 格式的控制器文件体(动画引用用原名, 交给移植工具的控制器流水线); 脚本从不播放包里的动画 → None"""
        if not self.animations:
            return None
        playing = self.VariableText("ps")
        codes = self._StateCodes()
        states = OrderedDict()
        states["idle"] = OrderedDict([("transitions", [
            OrderedDict([(name, u"{}=={}".format(playing, code))]) for name, code in codes])])
        for position, animation in enumerate(self.animations, 1):
            # 暂停: Java 照常计时、骨骼不应用 → apply 条件(基岩权重 0 时计时也停, 近似)
            entry = OrderedDict([(animation, u"{}==0".format(self.VariableText("pa")))]) if self.pausable \
                else animation
            for parity in (0, 1):
                name = self.StateName(position, parity)
                transitions = [OrderedDict([(other, u"{}=={}".format(playing, code))])
                               for other, code in codes if other != name]
                transitions.append(OrderedDict([("idle", u"{}==0".format(playing))]))
                states[name] = OrderedDict([("animations", [entry]), ("transitions", transitions)])
        return OrderedDict([
            ("format_version", "1.19.0"),
            ("animation_controllers", OrderedDict([(self.controllerName, OrderedDict([
                ("initial_state", "idle"), ("states", states)]))])),
        ])

    def BedrockBlends(self):
        """基岩口径(按离开的状态取值)的 blend_transition"""
        blends = OrderedDict([("idle", self.staticBlend)])
        for name, _code in self._StateCodes():
            blends[name] = ENDING_TRANSITION_SECONDS
        return blends

    def ResetStatement(self):
        """模型装载时播放器复位(Java updateModel → clear + 起始过渡回通道缺省)的语句"""
        return u"".join(u"{}=0;".format(self.VariableText(field))
                        for field in ("ls", "nx", "cu", "st", "nf", "bt", "pa", "ps"))


# 播放器状态机模板(Java 形态): @字段 = 通道实体变量, %字段 = 通道临时变量, $参数 由调用方代入。
# 与 AnimationPlayer 逐句对应: setAnimation / reset / CodedAnimationController.process + AnimationPlayer.process;
# 多出的一句"计时起点晚于当前时刻就拉回": 实体重建后 q.life_time 可能从头算、变量却还在, 不拉回会整段冻住
_SCRIPT_SET_ANIMATION = u"@ls != $code ? { @st != 0 ? { @st = 0; @cu = 0; @nf = 0; }; @ls = $code; $next };"
_SCRIPT_NEXT = u"@nx = $position; @nl = $length; @nt = $type; @nr = $restart;"
_SCRIPT_RESET = u"@ls = 0; @nx = 0; @st != 0 ? { @st = 0; @cu = 0; @nf = 0; };"
_SCRIPT_PROCESS = u"""
%s = math.trunc($result);
(%s != 2 && %s != 3 && %s != 4) ? { $builtin };
%p = 1;
%s == 3 ? {
    (@st == 1 || @st == 2) ? { @st = 3; @of = q.life_time; @nf = 0; @ls = 0; %p = 0; } : { %p = @st == 3; };
};
%p ? {
    @of > q.life_time ? { @of = q.life_time; };
    %a = q.life_time - @of;
    (@st == 3 && %a >= $ending) ? { @st = 0; @cu = 0; @nf = 0; };
    (@st == 2 && @lt == $once && %a >= @ln) ? { @st = 3; @of = q.life_time; @nf = 0; %a = 0; };
    (@st == 0 && @nx > 0) ? {
        @cu = @nx; @ln = @nl; @lt = @nt; @rs = @nr; @nx = 0; @nf = 1;
        @of = q.life_time; %a = 0; @st = @bt > 0 ? 1 : 2; @sr = @sr + 1;
    };
    (@st == 1 && %a >= @bt) ? { %a = %a - @bt; @of = q.life_time - %a; @st = 2; };
    (@st == 2 && %a > @ln) ? {
        @nf = 0;
        @lt == $loop ? { %a = @ln > 0 ? math.mod(%a, @ln) : 0; @of = q.life_time - %a; @rs ? { @sr = @sr + 1; }; };
    };
};
@pa = %s == 4;
@ps = (@st == 1 || @st == 2) ? @cu * 2 + math.mod(@sr, 2) : 0;
"""


# ======================================================================
# 编译
# ======================================================================
class _Frame(object):
    """内联作用域。kind: top(原文所在表达式, 临时变量不改名) / handler(整段事件体, 可用原生 return) /
    call(内联的一次调用)"""

    def __init__(self, kind, prefix=None, argNames=None, function=None, depth=0):
        self.kind = kind
        self.prefix = prefix
        self.argNames = list(argNames or [])
        self.function = function
        self.depth = depth               # 调用链深度
        self.loopDepth = 0               # 本帧内的循环嵌套
        self.wrapped = False             # 函数体包了 loop(1, {…})
        self.returnInLoop = False        # 当前循环体里出现过本帧的 return(需要逐层 break)
        self.argCount = None             # 实参个数是运行期值时的表达式(同步事件: 服务端转来的个数); None = len(argNames)

    def TempName(self, name):
        if self.kind != "call":
            return None
        return jm.Name(u"t", u"{}{}".format(self.prefix, name.lower()))

    @property
    def resultName(self):
        return jm.Name(u"t", self.prefix + u"r")

    @property
    def doneName(self):
        return jm.Name(u"t", self.prefix + u"d")


def _Assign(target, value):
    return jm.Node("assign", target=target, value=value)


def _Block(statements):
    return jm.Node("block", body=list(statements))


def _Cond(test, then):
    return jm.Node("cond", test=test, then=then)


def _Increment(name):
    return _Assign(name, jm.Node("binary", op="+", left=name, right=jm.Num("1")))


def _VariableName(text):
    namespace, member = text.split(u".", 1)
    return jm.Name(namespace, member)


def _QueryName(text):
    """query.mod.<名> → 三段的 name 节点"""
    return jm.Name(*text.split(u"."))


class FunctionCompiler(object):
    """一个移植包的函数编译器: 内联调用、宿主服务调用改写、逐帧执行体与初始化语句、ysm.json 声明"""

    def __init__(self, functionSet, packName, physics=None, sounds=None, report=None,
                 soundPath=None):
        self.functionSet = functionSet if functionSet is not None else FunctionSet()
        self.packName = packName
        self.physics = physics
        self.sounds = sounds
        self.report = report if report is not None else Counter()
        self.soundPath = soundPath       # 音频源文件定位: 音效名 → 路径(算时长用); None = 不算
        self._frameCount = 0
        self._stack = []
        self.soundSlots = []
        self.stopSlots = []
        self.stopAllSlots = []
        self._pureCache = {}
        self.scriptChannels = []         # 登记序 = Java 通道处理序(PlanScriptChannel 由移植工具按序调用)
        self._script = None              # 正在编译的脚本控制器通道(ctrl.* / 播完查询只在它里面生效)
        self._emitting = True            # 当前求值上下文 Java 是否 allowEmitting(见 _HOST_CALLS 注)
        self.syncSlots = []
        self.keyCodes = set()            # ysm.keyboard 用到的 GLFW 键码
        self.mouseButtons = set()        # ysm.mouse 用到的 GLFW 鼠标键
        self.syncHandlers = []           # 编进执行体的 @sync 事件体(文件名)

    # ------------------------------------------------------------------
    # 对外: 文本 / 动画体 / 控制器体
    # ------------------------------------------------------------------
    @staticmethod
    def NeedsRewrite(text):
        return isinstance(text, jm._STRING_TYPES) and bool(_NEEDS_REWRITE.search(text))

    def RewriteValueText(self, text):
        """值位置的 molang(骨骼通道 / 转移条件 / 动画权重): 返回改写后的文本(无需改写原样返回)。
        Java 求这些值时 allowEmitting = false: 音效 / 同步 / defer 调用什么都不做"""
        if not self.NeedsRewrite(text):
            return text
        statements = self._Parse(text)
        if statements is None:
            return text
        emitting, self._emitting = self._emitting, False
        try:
            top = _Frame("top")
            if len(statements) == 1 and statements[0].kind not in ("assign", "ret", "block", "loop", "foreach"):
                pre = []
                value = self._LowerExpr(statements[0], top, pre)
                if not pre:
                    return jm.Print(value)
                return jm.PrintStatements(pre + [jm.Node("ret", value=value)])
            lowered = self._LowerStatements(statements, top)
            return jm.PrintStatements(self._JavaValueTail(lowered))
        finally:
            self._emitting = emitting

    def RewriteStatementText(self, text):
        """语句位置的 molang(timeline 指令帧 / on_entry / on_exit, Java 都 allowEmitting): 返回改写后的文本"""
        if not self.NeedsRewrite(text):
            return text
        statements = self._Parse(text)
        if statements is None:
            return text
        emitting, self._emitting = self._emitting, True
        try:
            return jm.PrintStatements(self._LowerStatements(statements, _Frame("top")))
        finally:
            self._emitting = emitting

    def RewriteAnimationBody(self, body):
        """动画体里全部 molang 就地改写(骨骼通道 = 值; timeline / 粒子 pre_effect_script = 语句); 返回改写处数"""
        count = [0]

        def _Value(container, key):
            value = container[key]
            if isinstance(value, jm._STRING_TYPES):
                rewritten = self.RewriteValueText(value)
                if rewritten != value:
                    container[key] = rewritten
                    count[0] += 1
            elif isinstance(value, list):
                for index in range(len(value)):
                    _Value(value, index)
            elif isinstance(value, dict):
                for side in ("pre", "post"):
                    if side in value:
                        _Value(value, side)

        def _Statements(container, key):
            value = container[key]
            if isinstance(value, jm._STRING_TYPES):
                rewritten = self.RewriteStatementText(value)
                if rewritten != value:
                    container[key] = rewritten
                    count[0] += 1
            elif isinstance(value, list):
                for index in range(len(value)):
                    _Statements(value, index)

        for channels in (body.get("bones") or {}).values():
            if not isinstance(channels, dict):
                continue
            for channel in ("rotation", "position", "scale"):
                if channel not in channels:
                    continue
                value = channels[channel]
                if isinstance(value, dict) and not any(side in value for side in ("pre", "post")):
                    for stamp in list(value.keys()):
                        _Value(value, stamp)
                else:
                    _Value(channels, channel)
        timeline = body.get("timeline")
        if isinstance(timeline, dict):
            for stamp in list(timeline.keys()):
                _Statements(timeline, stamp)
        for field in ("blend_weight", "anim_time_update"):
            if field in body:
                _Value(body, field)
        effects = body.get("particle_effects")
        if isinstance(effects, dict):
            for entries in effects.values():
                for entry in (entries if isinstance(entries, list) else [entries]):
                    if isinstance(entry, dict) and "pre_effect_script" in entry:
                        _Statements(entry, "pre_effect_script")
        return count[0]

    def RewriteControllerBody(self, body):
        """控制器体: 转移条件 / 动画条件 = 值, on_entry / on_exit = 语句; 返回改写处数"""
        count = 0
        for state in (body.get("states") or {}).values():
            if not isinstance(state, dict):
                continue
            for transition in state.get("transitions") or []:
                if isinstance(transition, dict):
                    for target in list(transition.keys()):
                        value = transition[target]
                        rewritten = self.RewriteValueText(value)
                        if rewritten != value:
                            transition[target] = rewritten
                            count += 1
            for item in state.get("animations") or []:
                if isinstance(item, dict):
                    for animation in list(item.keys()):
                        value = item[animation]
                        rewritten = self.RewriteValueText(value)
                        if rewritten != value:
                            item[animation] = rewritten
                            count += 1
            for field in ("on_entry", "on_exit"):
                lines = state.get(field)
                if isinstance(lines, list):
                    for index, line in enumerate(lines):
                        rewritten = self.RewriteStatementText(line)
                        if rewritten != line:
                            lines[index] = rewritten
                            count += 1
        return count

    # ------------------------------------------------------------------
    # 对外: 事件体
    # ------------------------------------------------------------------
    def BuildExecutorAnimation(self):
        """player_update 事件体 + 脚本控制器 → 逐帧执行体动画体(Java 形态的 molang, 交给移植工具的动画流水线);
        都没有返回 None。脚本控制器用到的动画信息(ScriptChannel.info)要先填好"""
        handlers = [handler for handler in self.functionSet.Handlers("player_update") if handler.statements]
        syncHandlers = [handler for handler in self.functionSet.Handlers("sync") if handler.statements]
        if not handlers and not self.scriptChannels and not syncHandlers:
            return None
        self._emitting = True
        tick = jm.Name(u"t", u"ysm_fx_tick")
        body = [
            _Assign(_VariableName(FRAME_GUARD_VARIABLE), jm.Name(u"q", u"life_time")),
            _Assign(tick, jm.Node("binary", op="!=", left=self._TickPhase(), right=_VariableName(TICK_PHASE_VARIABLE))),
            _Assign(_VariableName(TICK_PHASE_VARIABLE), self._TickPhase()),
        ]
        top = _Frame("top")
        for handler in handlers:
            pre = []
            self._InlineDefinition(handler, [tick], top, pre, statement=True)
            body.extend(pre)
            self.report[u"map:@player_update {} -> 逐帧执行体 ysm_fx_frame".format(handler.fileName)] += 1
        if syncHandlers:
            body.append(self._SyncEventBlock(syncHandlers, top))
        # Java: player_update 在每次动画更新前跑, 之后各控制器按通道序处理(脚本在各自控制器处理时跑)
        for script in self.scriptChannels:
            body.extend(self._CompileScriptChannel(script))
            self.report[u"map:@player_ctrl_{} {} -> 逐帧执行体 ysm_fx_frame(脚本 + 动画播放器状态机) + 控制器".format(
                script.channel, script.handler.fileName)] += 1
        guard = _Cond(jm.Node("binary", op="!=", left=_VariableName(FRAME_GUARD_VARIABLE),
                              right=jm.Name(u"q", u"life_time")), _Block(body))
        text = jm.PrintStatements([guard, jm.Node("ret", value=jm.Num("0"))])
        return OrderedDict([
            ("loop", True),
            ("bones", OrderedDict([(EXECUTOR_HOST_BONE, OrderedDict([("rotation", [text, 0, 0])]))])),
        ])

    def BuildInitStatements(self):
        """player_init 事件体 → 每条一段复杂表达式(Java 形态, 调用方再过 PortMolangText); 顺序同 Java 事件表"""
        lines = []
        self._emitting = True
        for handler in self.functionSet.Handlers("player_init"):
            if not handler.statements:
                continue
            frame = _Frame("handler")
            lowered = self._LowerStatements(handler.statements, frame)
            if lowered:
                lines.append(jm.PrintStatements(lowered))
                self.report[u"map:@player_init {} -> 变量初始化控制器 on_entry".format(handler.fileName)] += 1
        # 脚本控制器的播放器状态存在实体变量里, 换模型不会自己清: 照 Java updateModel 在模型装载时复位
        for script in self.scriptChannels:
            lines.append(script.ResetStatement())
        return lines

    def ReportUnsupportedEvents(self):
        """尚未编译的事件订阅留痕(脚本控制器的去向由移植工具逐个报告, 这里不重复)"""
        for event, handlers in self.functionSet.handlers.items():
            if event in ("player_init", "player_update", "sync") or event.startswith(u"player_ctrl_"):
                continue
            for handler in handlers:
                self.report[u"skip:@{} {}(事件尚未支持, Java 侧照常触发)".format(event, handler.fileName)] += 1
        for definition in self.functionSet.files:
            if definition.error:
                self.report[u"warn:函数 {} 解析失败(Java 同样整份落成 0): {}".format(
                    definition.fileName, definition.error)] += 1

    def Declaration(self, executorId=None, initStatements=None):
        """ysm.json 顶级 java_functions 段(空时返回 None)"""
        declaration = OrderedDict()
        if executorId:
            declaration["executor"] = executorId
        if initStatements:
            declaration["init"] = list(initStatements)
        if self.soundSlots:
            declaration["sounds"] = [OrderedDict(slot) for slot in self.soundSlots]
        if self.stopSlots:
            declaration["stops"] = [OrderedDict(slot) for slot in self.stopSlots]
        if self.stopAllSlots:
            declaration["stop_all"] = [OrderedDict(slot) for slot in self.stopAllSlots]
        if self.syncSlots:
            declaration["syncs"] = [OrderedDict(slot) for slot in self.syncSlots]
        if self.syncHandlers:
            declaration["sync_handlers"] = list(self.syncHandlers)
        if self.keyCodes:
            declaration["keys"] = sorted(self.keyCodes)
        if self.mouseButtons:
            declaration["mouse"] = sorted(self.mouseButtons)
        return declaration or None

    def _SyncEventBlock(self, handlers, top):
        """@sync 事件体: 主包把服务端转来的同步事件逐个写进实体的 query.mod(计数 +1、实参、个数), 执行体看到计数变了就跑
        一遍全部处理函数(Java molangSync: 按订阅顺序, 实参是 ysm.sync 的浮点值列表)"""
        counter = _QueryName(SYNC_EVENT_COUNTER)
        seen = _VariableName(SYNC_EVENT_SEEN)
        args = [_QueryName(SYNC_EVENT_ARGUMENT.format(index)) for index in range(MAX_SYNC_ARGUMENTS)]
        count = _QueryName(SYNC_EVENT_COUNT)
        inner = [_Assign(seen, counter)]
        for handler in handlers:
            self._InlineDefinition(handler, args, top, inner, statement=True, argCount=count)
            self.syncHandlers.append(handler.fileName)
            self.report[u"map:@sync {} -> 逐帧执行体 ysm_fx_frame(主包转发的同步事件)".format(handler.fileName)] += 1
        return _Cond(jm.Node("binary", op="!=", left=counter, right=seen), _Block(inner))

    # ------------------------------------------------------------------
    # 解析 / 语句
    # ------------------------------------------------------------------
    def _Parse(self, text):
        try:
            return jm.ParseProgram(text)
        except jm.MolangParseError:
            return None           # 原样留给后面的守卫按 Java 口径处置

    @staticmethod
    def _TickPhase():
        return jm.Node("call", path=(u"math", u"floor"), args=[
            jm.Node("binary", op="*", left=jm.Name(u"q", u"life_time"), right=jm.Num("20"))])

    def _JavaValueTail(self, statements):
        """值位置的语句序列补 return: Java 返回最后一条语句的值(赋值取左值)"""
        if not statements or any(node.kind == "ret" for node in statements):
            return statements
        last = statements[-1]
        if last.kind == "assign":
            return statements + [jm.Node("ret", value=last.target)]
        if last.kind in ("block", "loop", "foreach", "brk", "cont") or (
                last.kind in ("cond", "ternary") and last.then is not None and last.then.kind == "block"):
            return statements + [jm.Node("ret", value=jm.Num("0"))]
        return statements[:-1] + [jm.Node("ret", value=last)]

    def _LowerStatements(self, statements, frame):
        out = []
        for statement in statements:
            out.extend(self._LowerStatement(statement, frame))
            if statement.kind == "ret":
                break             # Java: 同一层 return 之后的语句不可达; 基岩还会整份拒载
        return out

    def _LowerStatement(self, node, frame):
        kind = node.kind
        if kind == "str":
            return []                                        # Java: 字符串语句是空操作(作者拿它当注释)
        if kind == "ret":
            pre = []
            value = self._LowerExpr(node.value, frame, pre)
            if frame.kind != "call":
                return pre + [jm.Node("ret", value=value)]
            out = pre + [_Assign(frame.resultName, value)]
            if frame.loopDepth > 0:
                out.append(_Assign(frame.doneName, jm.Num("1")))
                frame.returnInLoop = True
            return out + [jm.Node("brk")]
        if kind in ("brk", "cont"):
            if frame.loopDepth == 0:
                return []                                    # Java: 循环外的 break / continue 什么都不做
            return [node]
        if kind == "block":
            return [_Block(self._LowerStatements(node.body, frame))]
        if kind == "loop":
            return self._LowerLoop(node, frame)
        if kind == "foreach":
            return self._LowerForEach(node, frame)
        if kind in ("cond", "ternary") and self._IsStatementBranch(node.then) \
                or (kind == "ternary" and self._IsStatementBranch(node.other)):
            pre = []
            test = self._LowerExpr(node.test, frame, pre)
            then = _Block(self._LowerBranch(node.then, frame))
            if kind == "cond":
                return pre + [_Cond(test, then)]
            other = _Block(self._LowerBranch(node.other, frame))
            return pre + [jm.Node("ternary", test=test, then=then, other=other)]
        if kind == "call":
            path = self._CallKey(node)
            if path is not None and path[0] == "fn":
                pre = []
                self._InlineCall(path[1], node.args, frame, pre, statement=True)
                return pre
            if path in _HOST_CALLS:
                pre = []
                self._LowerHostCall(node, path, frame, pre)
                return pre
            if path in _CTRL_HOOKS:
                pre = []
                self._LowerCtrlHook(node, path, frame, pre)
                return pre
            if path in _DEBUG_CALLS:
                self.report[u"skip:query.debug_output(调试输出, 基岩无对应)"] += 1
                return []
            if path in _INPUT_CALLS:
                return []                                    # 纯查询当语句用: 没有效果
        if kind == "name" and self._IsZeroArgCall(node):
            return self._LowerStatement(jm.Node("call", path=node.path, args=[]), frame)
        pre = []
        if kind == "assign":
            target = self._LowerTarget(node.target, frame)
            value = self._LowerExpr(node.value, frame, pre)
            return pre + self._PhysicsStatement([_Assign(target, value)])
        value = self._LowerExpr(node, frame, pre)
        if value.kind in ("num", "str") and kind not in ("num", "str"):
            return pre
        return self._PhysicsStatement(pre + [value])

    def _IsStatementBranch(self, node):
        return node is not None and node.kind in ("block", "ret", "brk", "cont", "assign", "loop", "foreach")

    def _LowerBranch(self, node, frame):
        if node.kind == "block":
            return self._LowerStatements(node.body, frame)
        return self._LowerStatement(node, frame)

    def _LowerLoop(self, node, frame):
        pre = []
        count = self._LowerExpr(node.operand, frame, pre)
        number = jm.NumberValue(count)
        if number is not None:
            count = jm.Num(jm.FormatNumber(min(int(round(number)), MAX_LOOP_ROUND)))
        else:
            count = jm.Node("call", path=(u"math", u"min"), args=[jm.Node("call", path=(u"math", u"floor"), args=[
                jm.Node("binary", op="+", left=count, right=jm.Num("0.5"))]), jm.Num(str(MAX_LOOP_ROUND))])
        return pre + self._WrapLoop(lambda: jm.Node("loop", operand=count, body=_Block(
            self._LowerStatements(node.body.body, frame))), frame)

    def _WrapLoop(self, build, frame):
        """构造一个循环语句(build 在循环深度 +1 下产出节点); 本帧的 return 出现在循环里时补逐层 break"""
        outer = frame.returnInLoop
        frame.returnInLoop = False
        frame.loopDepth += 1
        try:
            loop = build()
        finally:
            frame.loopDepth -= 1
        inner = frame.returnInLoop
        frame.returnInLoop = outer or inner
        out = [loop]
        if inner and frame.kind == "call":
            out.append(_Cond(frame.doneName, jm.Node("brk")))
        return out

    def _LowerForEach(self, node, frame):
        iterable = node.iterable
        variable = self._LowerTarget(node.target, frame)
        if jm.NamePath(iterable) == ("args",):
            args = frame.argNames
            if not args:
                return []                                    # Java: 空列表不进循环体
            index = jm.Name(u"t", u"ysm_fe{}_i".format(self._NextFrameId()))
            select = self._SelectArg(index, args, threshold=False)
            count = frame.argCount if frame.argCount is not None else jm.Num(str(len(args)))

            def _Build():
                body = [_Assign(variable, select), _Increment(index)] + self._LowerStatements(node.body.body, frame)
                return jm.Node("loop", operand=count, body=_Block(body))
            return [_Assign(index, jm.Num("0"))] + self._WrapLoop(_Build, frame)
        pre = []
        iterable = self._LowerExpr(iterable, frame, pre)
        return pre + self._WrapLoop(lambda: jm.Node("foreach", target=variable, iterable=iterable, body=_Block(
            self._LowerStatements(node.body.body, frame))), frame)

    def _PhysicsStatement(self, statements):
        """语句里的 second_order / first_order 就地改写(积分语句紧贴所在语句, 与 Java 求值顺序一致)"""
        if self.physics is None:
            return statements
        out = []
        for statement in statements:
            text = jm.Print(statement)
            if not _PHYSICS_CALL.search(text):
                out.append(statement)
                continue
            prefix, rewritten = self.physics._RewriteExpression(text)
            try:
                for line in prefix:
                    out.extend(jm.ParseProgram(line))
                out.extend(jm.ParseProgram(rewritten))
            except jm.MolangParseError:
                out.append(statement)
        return out

    # ------------------------------------------------------------------
    # 表达式
    # ------------------------------------------------------------------
    def _CallKey(self, node):
        path = jm.CallPath(node)
        if path is None or len(path) != 2:
            return None
        return path

    def _IsZeroArgCall(self, node):
        path = jm.NamePath(node)
        if path is None or len(path) != 2:
            return False
        return path[0] == "fn" or path in _HOST_CALLS or path in _CTRL_HOOKS

    def _HookName(self, node):
        """按名字就能定值的 Java 绑定: ctrl.state_* / 循环类型常量; 脚本控制器里的播完查询 → 本帧开头记下的播完标记。
        不是这些返回 None"""
        path = jm.NamePath(node)
        if path is None or len(path) != 2:
            return None
        if path[0] == "ctrl" and path[1] in _CTRL_CONSTANTS:
            return jm.Num(str(_CTRL_CONSTANTS[path[1]]))
        if path in _FINISHED_QUERIES and self._script is not None:
            return _VariableName(self._script.TempText("f"))
        return None

    def _LowerTarget(self, node, frame):
        path = jm.NamePath(node)
        if path is not None and len(path) == 2 and path[0] in _TEMP_NAMESPACES:
            renamed = frame.TempName(node.path[1])
            if renamed is not None:
                return renamed
        return node

    def _NeedsHoist(self, node):
        """子树里有必须提成语句的东西(宿主调用 / 不能折成表达式的函数调用 / 赋值)"""
        for child in jm.Walk(node):
            if child.kind == "assign":
                return True
            path = self._CallKey(child) if child.kind == "call" else (
                jm.NamePath(child) if child.kind == "name" and self._IsZeroArgCall(child) else None)
            if path is None:
                continue
            if path in _HOST_CALLS or path in _CTRL_HOOKS:
                return True
            if path[0] == "fn":
                definition = self.functionSet.functions.get(path[1])
                if definition is not None and not definition.error and self._PureBody(definition) is None:
                    return True
        return False

    def _LowerExpr(self, node, frame, pre):
        kind = node.kind
        if kind in ("num", "str", "brk", "cont"):
            return node
        if kind == "name":
            path = jm.NamePath(node)
            hooked = self._HookName(node)
            if hooked is not None:
                return hooked
            if self._IsZeroArgCall(node):
                return self._LowerExpr(jm.Node("call", path=node.path, args=[]), frame, pre)
            if path == ("args",):
                self.report[u"zero:args(整个参数表只能用于 for_each / 下标)"] += 1
                return jm.Num("0")
            return self._LowerTarget(node, frame)
        if kind == "index":
            if jm.NamePath(node.target) == ("args",):
                return self._ArgAccess(node.index, frame, pre)
            return node.Copy(target=self._LowerExpr(node.target, frame, pre),
                             index=self._LowerExpr(node.index, frame, pre))
        if kind == "member":
            return node.Copy(target=self._LowerExpr(node.target, frame, pre))
        if kind == "call":
            path = self._CallKey(node)
            if path is not None and path[0] == "fn":
                return self._InlineCall(path[1], node.args, frame, pre)
            if path in _HOST_CALLS:
                return self._LowerHostCall(node, path, frame, pre)
            if path in _CTRL_HOOKS:
                self._LowerCtrlHook(node, path, frame, pre)
                return jm.Num("0")                           # Java: 这几个函数都返回 null
            if path in _DEBUG_CALLS:
                self.report[u"skip:query.debug_output(调试输出, 基岩无对应)"] += 1
                return jm.Num("0")
            if path in _INPUT_CALLS:
                return self._InputQuery(path, [self._LowerExpr(arg, frame, pre) for arg in node.args])
            args = [self._LowerExpr(arg, frame, pre) for arg in node.args]
            callPath = node.path
            if path is not None and path[0] in _JAVA_NAMESPACES:
                callPath = tuple(part.lower() for part in node.path)
            return node.Copy(path=callPath, args=args)
        if kind == "unary":
            return node.Copy(operand=self._LowerExpr(node.operand, frame, pre))
        if kind == "binary":
            if node.op in ("&&", "||", "??") and self._NeedsHoist(node.right):
                return self._LowerShortCircuit(node, frame, pre)
            left = self._LowerExpr(node.left, frame, pre)
            right = self._LowerExpr(node.right, frame, pre)
            return node.Copy(left=left, right=right)
        if kind in ("cond", "ternary"):
            branches = [node.then] + ([node.other] if kind == "ternary" else [])
            if any(self._NeedsHoist(branch) for branch in branches):
                return self._LowerConditional(node, frame, pre)
            test = self._LowerExpr(node.test, frame, pre)
            then = self._LowerExpr(node.then, frame, pre)
            if kind == "cond":
                return node.Copy(test=test, then=then)
            return node.Copy(test=test, then=then, other=self._LowerExpr(node.other, frame, pre))
        if kind == "assign":
            target = self._LowerTarget(node.target, frame)
            value = self._LowerExpr(node.value, frame, pre)
            pre.extend(self._PhysicsStatement([_Assign(target, value)]))
            return target
        if kind == "block":
            # Java 执行域作值: 返回最后一条语句的值
            if not node.body:
                return jm.Num("0")
            for statement in node.body[:-1]:
                pre.extend(self._LowerStatement(statement, frame))
            return self._LowerExpr(node.body[-1], frame, pre)
        if kind in ("loop", "foreach", "ret"):
            pre.extend(self._LowerStatement(node, frame))
            return jm.Num("0")
        return node

    def _Temp(self):
        return jm.Name(u"t", u"ysm_fx{}".format(self._NextFrameId()))

    def _Truthy(self, value):
        return jm.Node("ternary", test=value, then=jm.Num("1"), other=jm.Num("0"))

    def _LowerShortCircuit(self, node, frame, pre):
        """右操作数要提语句的 && / || / ??: 按 Java 短路求值展开成条件块"""
        temp = self._Temp()
        left = self._LowerExpr(node.left, frame, pre)
        inner = []
        right = self._LowerExpr(node.right, frame, inner)
        if node.op == "??":
            pre.append(_Assign(temp, left))
            inner.append(_Assign(temp, right))
            pre.append(_Cond(jm.Node("binary", op="==", left=temp, right=jm.Num("0")), _Block(inner)))
            return temp
        pre.append(_Assign(temp, self._Truthy(left)))
        inner.append(_Assign(temp, self._Truthy(right)))
        test = temp if node.op == "&&" else jm.Node("unary", op="!", operand=temp)
        pre.append(_Cond(test, _Block(inner)))
        return temp

    def _LowerConditional(self, node, frame, pre):
        """分支要提语句的三元 / 条件: 结果变量 + 条件块(Java 无 else 的条件为假时是 null → 0)"""
        temp = self._Temp()
        test = self._LowerExpr(node.test, frame, pre)
        thenPre = []
        thenValue = self._LowerExpr(node.then, frame, thenPre)
        thenBlock = _Block(thenPre + [_Assign(temp, thenValue)])
        pre.append(_Assign(temp, jm.Num("0")))
        if node.kind == "cond":
            pre.append(_Cond(test, thenBlock))
        else:
            otherPre = []
            otherValue = self._LowerExpr(node.other, frame, otherPre)
            pre.append(jm.Node("ternary", test=test, then=thenBlock,
                               other=_Block(otherPre + [_Assign(temp, otherValue)])))
        return temp

    # ------------------------------------------------------------------
    # 实参
    # ------------------------------------------------------------------
    def _ArgAccess(self, indexNode, frame, pre):
        args = frame.argNames
        number = jm.NumberValue(indexNode)
        if number is not None:
            index = int(number) if number >= 0 else 0        # Java: (int) 截断, 负数按 0
            return args[index] if index < len(args) else jm.Num("0")
        if not args:
            return jm.Num("0")
        temp = self._Temp()
        pre.append(_Assign(temp, self._LowerExpr(indexNode, frame, pre)))
        return self._SelectArg(temp, args, threshold=True)

    @staticmethod
    def _SelectArg(index, args, threshold):
        """按下标选实参的三元链; threshold: 按 Java (int) 截断取区间(i < 1 → 0 号 …), 否则精确相等"""
        result = jm.Num("0")
        for position in range(len(args) - 1, -1, -1):
            if threshold:
                test = jm.Node("binary", op="<", left=index, right=jm.Num(str(position + 1)))
            else:
                test = jm.Node("binary", op="==", left=index, right=jm.Num(str(position)))
            result = jm.Node("ternary", test=test, then=args[position], other=result)
        return result

    def _NextFrameId(self):
        self._frameCount += 1
        return self._frameCount

    # ------------------------------------------------------------------
    # 内联
    # ------------------------------------------------------------------
    def _InlineCall(self, name, args, frame, pre, statement=False):
        definition = self.functionSet.functions.get(name.lower())
        if definition is None:
            self.report[u"zero:fn.{}(函数不存在, Java 同样返回 null)".format(name)] += 1
            return jm.Num("0")
        return self._InlineDefinition(definition, args, frame, pre, statement)

    def _InlineDefinition(self, definition, args, frame, pre, statement=False, argCount=None):
        label = definition.name or definition.fileName
        if definition.error:
            self.report[u"zero:fn.{}(函数文件解析失败, Java 同样返回 0)".format(label)] += 1
            return jm.Num("0")
        if definition in self._stack or frame.depth + 1 > MAX_CALL_DEPTH:
            self.report[u"zero:fn.{}(递归 / 调用链超过 {} 层, 基岩不能内联)".format(label, MAX_CALL_DEPTH)] += 1
            return jm.Num("0")
        if not statement:
            pure = self._PureBody(definition)
            if pure is not None:
                loweredArgs = [self._LowerExpr(arg, frame, pre) for arg in args]
                self._stack.append(definition)
                try:
                    folded = self._FoldPure(pure, loweredArgs, pre)
                finally:
                    self._stack.pop()
                self.report[u"map:fn.{} -> 调用处内联(纯表达式)".format(label)] += 1
                return folded
        frameId = self._NextFrameId()
        callFrame = _Frame("call", prefix=u"ysm_f{}_".format(frameId), function=definition, depth=frame.depth + 1)
        callFrame.argCount = argCount
        # Java push(ctx, args): 实参在调用方的作用域里依次求值, 再压栈
        for position, arg in enumerate(args):
            argName = jm.Name(u"t", u"ysm_f{}_a{}".format(frameId, position))
            pre.append(_Assign(argName, self._LowerExpr(arg, frame, pre)))
            callFrame.argNames.append(argName)
        self._stack.append(definition)
        try:
            pre.extend(self._LowerFunctionBody(definition, callFrame, frame))
        finally:
            self._stack.pop()
        self.report[u"map:fn.{} -> 调用处内联(语句)".format(label)] += 1
        return callFrame.resultName

    def _LowerFunctionBody(self, definition, callFrame, caller):
        statements = definition.statements
        returns = [node for node in jm.WalkAll(statements) if node.kind == "ret"]
        result = callFrame.resultName
        out = [_Assign(result, jm.Num("0"))]
        # 每次调用都是新栈帧, Java 的临时变量从 null 开始; 基岩读未赋值的变量会让**整段表达式中止**(2026-09-23 实机:
        # `t.a = t.never_set; return 7;` 得 0) —— 函数用到的临时变量入口先置 0
        for temp in self._FunctionTemps(statements):
            out.append(_Assign(callFrame.TempName(temp), jm.Num("0")))
        if not returns or (len(returns) == 1 and statements and statements[-1] is returns[0]):
            body = statements[:-1] if returns else statements
            lowered = self._LowerStatements(body, callFrame)
            out.extend(lowered)
            if returns:
                tailPre = []
                value = self._LowerExpr(statements[-1].value, callFrame, tailPre)
                out.extend(tailPre + [_Assign(result, value)])
            else:
                out.extend(self._ResultFromLast(body, lowered, result))
            return out
        callFrame.wrapped = True
        lowered = self._LowerStatements(statements, callFrame)
        lastTopReturn = statements and statements[-1].kind == "ret"
        if not lastTopReturn:
            lowered = lowered + self._ResultFromLast(statements, lowered, result)
        done = callFrame.doneName
        if any(jm.NamePath(node) == jm.NamePath(done) for node in jm.WalkAll(lowered)):
            out.append(_Assign(done, jm.Num("0")))           # 完成标记同样要先定义才能读
        out.append(jm.Node("loop", operand=jm.Num("1"), body=_Block(lowered)))
        return out

    @staticmethod
    def _ResultFromLast(original, lowered, result):
        """Java: 没执行到 return 时返回最后一条语句的值"""
        if not original or not lowered:
            return []
        last = original[-1]
        if last.kind == "assign":
            loweredLast = lowered[-1]
            if loweredLast.kind == "assign":
                return [_Assign(result, loweredLast.target)]
            return []
        if last.kind in ("num", "name", "binary", "unary", "call", "index", "member", "ternary") \
                and lowered[-1].kind not in ("assign", "block", "loop", "foreach", "cond", "ternary", "brk"):
            value = lowered.pop()
            return [_Assign(result, value)]
        return []

    @staticmethod
    def _FunctionTemps(statements):
        names = OrderedDict()
        for node in jm.WalkAll(statements):
            path = jm.NamePath(node)
            if path is not None and len(path) == 2 and path[0] in _TEMP_NAMESPACES:
                names[path[1]] = True
        return list(names)

    # ------------------------------------------------------------------
    # 纯表达式折叠
    # ------------------------------------------------------------------
    def _PureBody(self, definition):
        """函数体能否折成纯表达式: 返回 (临时变量赋值列表, 结果表达式树) 或 None。
        形态: 顶层若干 `t.x = 纯表达式;`(每个临时变量只赋一次, 不在分支里) + 一条 return / if-else 链,
        链上每个分支只有 return(或嵌套的同形链); 纯 = 不含赋值 / 宿主调用 / 不纯的函数调用 / 循环 / break。"""
        if definition in self._pureCache:
            return self._pureCache[definition]
        self._pureCache[definition] = None      # 递归防护
        statements = definition.statements
        assigns = []
        index = 0
        assigned = set()
        while index < len(statements) and statements[index].kind == "assign":
            node = statements[index]
            path = jm.NamePath(node.target)
            if path is None or len(path) != 2 or path[0] not in _TEMP_NAMESPACES or path[1] in assigned \
                    or not self._IsPure(node.value):
                return None
            assigned.add(path[1])
            assigns.append((path[1], node.value))
            index += 1
        rest = statements[index:]
        if len(rest) != 1:
            return None
        tree = self._PureTail(rest[0])
        if tree is None:
            return None
        result = (assigns, tree)
        self._pureCache[definition] = result
        return result

    def _PureTail(self, node):
        """return X / cond ? {链} : {链} → 值树; 不成形返回 None(无 else 的条件为假时 Java 返回 null → 0)"""
        if node.kind == "ret":
            return node.value if self._IsPure(node.value) else None
        if node.kind in ("cond", "ternary") and self._IsPure(node.test):
            then = self._PureBranch(node.then)
            if then is None:
                return None
            other = jm.Num("0") if node.kind == "cond" else self._PureBranch(node.other)
            if other is None:
                return None
            return jm.Node("ternary", test=node.test, then=then, other=other)
        return None

    def _PureBranch(self, node):
        if node.kind == "block":
            return self._PureTail(node.body[0]) if len(node.body) == 1 else None
        return self._PureTail(node)

    def _IsPure(self, node):
        stack = [node]
        while stack:
            child = stack.pop()
            if child.kind in ("assign", "block", "ret", "brk", "cont", "loop", "foreach"):
                return False
            if child.kind == "index" and jm.NamePath(child.target) == ("args",):
                if jm.NumberValue(child.index) is None:
                    return False              # 动态下标要提语句
                continue                      # 常量下标 = 取实参, 不再看它的目标 args
            if child.kind == "name" and jm.NamePath(child) == ("args",):
                return False                  # 整个参数表当值用(for_each 之外)
            stack.extend(jm.Children(child))
            path = self._CallKey(child) if child.kind == "call" else (
                jm.NamePath(child) if child.kind == "name" and self._IsZeroArgCall(child) else None)
            if path is None:
                continue
            if path in _HOST_CALLS or path in _DEBUG_CALLS or path in _CTRL_HOOKS:
                return False
            if path[0] == "fn":
                definition = self.functionSet.functions.get(path[1])
                if definition is None or definition.error:
                    continue                  # 调用不存在的函数 = 常量 0, 纯
                if definition in self._stack or self._PureBody(definition) is None:
                    return False
            elif _PHYSICS_CALL.search(u".".join(path) + u"("):
                return False                  # 物理函数带状态
        return True

    def _FoldPure(self, pure, args, pre):
        """纯函数体在调用处展开(args 是调用方已求值形态的实参)。Java 实参与临时变量都只求值一次: 不是字面量 /
        变量名、又被引用不止一次的先求值进临时变量(math.random 这类代入多处会变成多次抽样)"""
        assigns, tree = pure
        uses = Counter()
        for node in [tree] + [value for _name, value in assigns]:
            for child in jm.Walk(node):
                if child.kind == "index" and jm.NamePath(child.target) == ("args",):
                    number = jm.NumberValue(child.index)
                    uses[("arg", int(number) if number is not None and number >= 0 else 0)] += 1
                path = jm.NamePath(child)
                if path is not None and len(path) == 2 and path[0] in _TEMP_NAMESPACES:
                    uses[("temp", path[1])] += 1
        bound = []
        for position, arg in enumerate(args):
            bound.append(self._Bind(arg, uses[("arg", position)], pre))
        substitutions = {}
        for name, value in assigns:
            replaced = _ConstantFold(self._Substitute(value, bound, substitutions, pre))
            substitutions[name] = self._Bind(replaced, uses[("temp", name)], pre)
        return _ConstantFold(self._Substitute(tree, bound, substitutions, pre))

    def _Bind(self, value, useCount, pre):
        if useCount <= 1 or value.kind in ("num", "str", "name"):
            return value
        temp = self._Temp()
        pre.append(_Assign(temp, value))
        return temp

    def _Substitute(self, node, args, temps, pre):
        def _Replace(current):
            if current.kind == "name":
                hooked = self._HookName(current)
                if hooked is not None:
                    return hooked
            if current.kind == "index" and jm.NamePath(current.target) == ("args",):
                number = jm.NumberValue(current.index)
                index = int(number) if number is not None and number >= 0 else 0
                return args[index] if index < len(args) else jm.Num("0")
            path = jm.NamePath(current)
            if path is not None and len(path) == 2 and path[0] in _TEMP_NAMESPACES:
                return temps.get(path[1], jm.Num("0"))
            if current.kind == "call" or (current.kind == "name" and self._IsZeroArgCall(current)):
                call = current if current.kind == "call" else jm.Node("call", path=current.path, args=[])
                key = self._CallKey(call)
                if key is not None and key[0] == "fn":
                    replacedArgs = [_Replace(arg) for arg in call.args]
                    definition = self.functionSet.functions.get(key[1])
                    if definition is None or definition.error:
                        return jm.Num("0")
                    self._stack.append(definition)
                    try:
                        inner = self._PureBody(definition)
                        return self._FoldPure(inner, replacedArgs, pre) if inner else jm.Num("0")
                    finally:
                        self._stack.pop()
                if key in _INPUT_CALLS:
                    return self._InputQuery(key, [_Replace(arg) for arg in call.args])
                if key is not None and key[0] in _JAVA_NAMESPACES:
                    return call.Copy(path=tuple(part.lower() for part in call.path),
                                     args=[_Replace(arg) for arg in call.args])
            return _MapChildren(current, _Replace)
        return _Replace(node)

    # ------------------------------------------------------------------
    # 脚本控制器
    # ------------------------------------------------------------------
    def PlanScriptChannel(self, channel, handler, javaAnimations):
        """登记一个脚本控制器通道(序号 = 登记序, 调用方按 Java 通道处理序登记); 转换不了抛 ScriptChannelError。
        javaAnimations: 包里的 Java 动画原名 → Java 自身循环类型(LOOP_TYPE_*)"""
        if not SCRIPT_CHANNEL_PATTERN.match(channel):
            raise ScriptChannelError(u"通道 {} 的内置逻辑是主链 / 一次性状态机, 尚未支持".format(channel))
        if handler.error:
            raise ScriptChannelError(u"脚本解析失败(Java 同样整份作废): {}".format(handler.error))
        builtin = None
        matched = _BUILTIN_PARALLEL_CHANNEL.match(channel)
        if matched:
            candidate = u"{}parallel{}".format(matched.group(1) or u"", matched.group(2))
            if candidate in javaAnimations:
                builtin = candidate
        script = ScriptChannel(len(self.scriptChannels), channel, handler, builtin)
        self._CollectCtrlCalls(handler.statements, script, javaAnimations, set([handler]))
        if builtin is not None:
            self._RegisterScriptAnimation(script, builtin, javaAnimations)
        self.scriptChannels.append(script)
        return script

    def ScriptNeedsExecutor(self, handler):
        """脚本里有决策树转换会丢掉的东西(音效 / ctrl.reset / indicate_reload)或调用了函数 → 只能编进执行体"""
        for node in jm.WalkAll(handler.statements):
            if node.kind == "call":
                path = self._CallKey(node)
            elif node.kind == "name" and self._IsZeroArgCall(node):
                path = jm.NamePath(node)
            else:
                continue
            if path is not None and (path in _HOST_CALLS or path in (_CTRL_RESET, _CTRL_RELOAD) or path[0] == "fn"):
                return True
        return False

    def _CollectCtrlCalls(self, statements, script, javaAnimations, visited):
        """脚本(连同它调用的函数)里的 ctrl.* 调用 → 动画表 / 起始过渡常量 / 是否可能暂停"""
        for node in jm.WalkAll(statements):
            if node.kind == "ret" and not self._IsStaticState(node.value):
                script.pausable = True                    # 返回值不是字面状态: 可能是 4(暂停)
            if node.kind == "call":
                path = self._CallKey(node)
            elif node.kind == "name":
                path = jm.NamePath(node)
            else:
                continue
            if path is None or len(path) != 2:
                continue
            if path == ("ctrl", "state_pause"):
                script.pausable = True
            elif path == _CTRL_SET_ANIMATION and node.kind == "call":
                name, _override = self._SetAnimationArgs(node)
                if name:
                    self._RegisterScriptAnimation(script, name, javaAnimations)
            elif path == _CTRL_SET_BLEND and node.kind == "call" and len(node.args) == 1:
                number = self._CtrlConstant(node.args[0])
                if number is None:
                    script.notes.append(u"起始过渡时长不是常量(基岩侧空闲态按其余常量或 0 淡入)")
                elif number >= 0:
                    script.blends.append(float(number))
            elif path[0] == "fn":
                definition = self.functionSet.functions.get(path[1])
                if definition is not None and definition not in visited and not definition.error:
                    visited.add(definition)
                    self._CollectCtrlCalls(definition.statements, script, javaAnimations, visited)

    @staticmethod
    def _IsStaticState(node):
        if node is None or jm.NumberValue(node) is not None:
            return True
        path = jm.NamePath(node)
        return path is not None and len(path) == 2 and path[0] == "ctrl" and path[1] in _CTRL_CONSTANTS \
            and path[1] != "state_pause"

    @staticmethod
    def _CtrlConstant(node):
        """数字字面量 / ctrl 常量的值; 其余 None"""
        number = jm.NumberValue(node)
        if number is not None:
            return number
        path = jm.NamePath(node)
        if path is not None and len(path) == 2 and path[0] == "ctrl" and path[1] in _CTRL_CONSTANTS:
            return float(_CTRL_CONSTANTS[path[1]])
        return None

    def _SetAnimationArgs(self, node):
        """ctrl.set_animation(名[, 循环类型]) → (名, 循环覆盖码); 名为空串 = Java 什么都不做"""
        args = node.args
        if not args or len(args) > 2:
            raise ScriptChannelError(u"ctrl.set_animation 参数个数不对: {}".format(jm.Print(node)))
        if args[0].kind != "str":
            raise ScriptChannelError(u"ctrl.set_animation 的动画名不是字符串字面量: {}".format(jm.Print(args[0])))
        override = 0
        if len(args) == 2:
            value = self._CtrlConstant(args[1])
            if value is None:
                raise ScriptChannelError(u"ctrl.set_animation 的循环类型不是常量: {}".format(jm.Print(args[1])))
            override = _LOOP_OVERRIDE_CODES.get(int(value), 0)
        return args[0].value, override

    @staticmethod
    def _RegisterScriptAnimation(script, name, javaAnimations):
        if name not in script.names:
            script.names.append(name)
        if name in javaAnimations and name not in script.animations:
            script.animations.append(name)
            script.loopTypes[name] = javaAnimations[name]

    @staticmethod
    def _ScriptTemplate(template, script, **params):
        text = template
        for key in sorted(params, key=len, reverse=True):   # 参数先代入: 内置谓词段本身也带 @ / % 占位
            text = text.replace(u"$" + key, params[key])
        text = re.sub(r"@([a-z]+)", lambda matched: script.VariableText(matched.group(1)), text)
        text = re.sub(r"%([a-z]+)", lambda matched: script.TempText(matched.group(1)), text)
        return jm.ParseProgram(text)

    @staticmethod
    def _SetAnimationText(script, name, override):
        """AnimationPlayer.setAnimation 的模板文本: 与上次设置(名 + 循环覆盖)相同则空操作, 否则硬撤当前动画、
        记下上次设置, 包里有该动画才登记待载入(没有时 Java 保留原来的待载入)"""
        nextText = u""
        position = script.AnimationIndex(name)
        if position:
            length, bedrockLoops = script.info.get(name, (INFINITE_LENGTH_SECONDS, False))
            loopType = override or script.loopTypes.get(name, LOOP_TYPE_ONCE)
            restart = 1 if loopType == LOOP_TYPE_LOOP and not bedrockLoops else 0   # 基岩文件不循环: 每轮换孪生态重播
            nextText = _SCRIPT_NEXT.replace(u"$position", str(position)).replace(
                u"$length", jm.FormatNumber(length)).replace(u"$type", str(loopType)).replace(
                u"$restart", str(restart))
        code = str(script.LastSetCode(name, override))
        return _SCRIPT_SET_ANIMATION.replace(u"$code", code).replace(u"$next", nextText)

    def _CompileScriptChannel(self, script):
        """脚本 + 播放器状态机 → 执行体语句(Java 形态)"""
        out = self._ScriptTemplate(u"%f = @nf == 0;", script)   # Java: 脚本前按上一帧末的播完标记设好查询
        frameId = self._NextFrameId()
        callFrame = _Frame("call", prefix=u"ysm_f{}_".format(frameId), function=script.handler, depth=1)
        self._script = script
        self._stack.append(script.handler)
        try:
            out.extend(self._LowerFunctionBody(script.handler, callFrame, None))
        finally:
            self._stack.pop()
            self._script = None
        if script.builtin is not None:
            # ParallelPredicate: playLoopAnimation(并行动画) = setAnimation(名, LOOP) + 继续
            builtin = self._SetAnimationText(script, script.builtin, LOOP_TYPE_LOOP) + u" %s = 2;"
        else:
            builtin = u"%s = 3;"                                     # EmptyPredicate: 停止
        out.extend(self._ScriptTemplate(
            _SCRIPT_PROCESS, script, result=jm.Print(callFrame.resultName), builtin=builtin,
            ending=jm.FormatNumber(ENDING_TRANSITION_SECONDS), once=str(LOOP_TYPE_ONCE), loop=str(LOOP_TYPE_LOOP)))
        return out

    def _LowerCtrlHook(self, node, path, frame, pre):
        """ctrl.set_animation / set_beginning_transition_length / reset / indicate_reload → 播放器状态语句"""
        script = self._script
        if script is None:
            # Java: 拿不到当前脚本控制器(不在 @player_ctrl_ 里)时什么都不做
            self.report[u"skip:{}(不在脚本控制器里, Java 同样什么都不做)".format(u".".join(path))] += 1
            return
        if path == _CTRL_SET_ANIMATION:
            try:
                name, override = self._SetAnimationArgs(node)
            except ScriptChannelError as error:
                self.report[u"warn:{}".format(error)] += 1
                return
            if name:
                pre.extend(self._ScriptTemplate(self._SetAnimationText(script, name, override), script))
            return
        if path == _CTRL_SET_BLEND:
            if len(node.args) != 1:
                return
            blend = _VariableName(script.VariableText("bt"))
            value = self._LowerExpr(node.args[0], frame, pre)
            number = jm.NumberValue(value)
            if number is not None:
                if number >= 0:                                 # Java: 负数忽略
                    pre.append(_Assign(blend, jm.Num(jm.FormatNumber(number))))
                return
            temp = _VariableName(script.TempText("b"))
            pre.append(_Assign(temp, value))
            pre.append(_Cond(jm.Node("binary", op=">=", left=temp, right=jm.Num("0")), _Block([_Assign(blend, temp)])))
            return
        if path == _CTRL_RESET:
            pre.extend(self._ScriptTemplate(_SCRIPT_RESET, script))
            return
        pre.append(_Assign(_VariableName(script.VariableText("ls")), jm.Num("0")))   # indicate_reload

    # ------------------------------------------------------------------
    # 宿主服务调用
    # ------------------------------------------------------------------
    def _LowerHostCall(self, node, path, frame, pre):
        """宿主调用 → 请求语句(进 pre); 返回调用本身的值(Java: 音效族发出时 true、不发出时 false, sync / defer 为 null)"""
        label = u".".join(path)
        if not self._emitting:
            # Java: 骨骼通道 / 转移条件 / 权重求值时 allowEmitting = false, 调用直接返回(参数都不求值)
            self.report[u"skip:{}(骨骼通道 / 条件里 Java 不发出)".format(label)] += 1
            return jm.Num("0")
        if path == _HOST_PLAY:
            self._LowerPlaySound(node, frame, pre)
        elif path == _HOST_STOP:
            self._LowerStopSound(node, frame, pre)
        elif path == _HOST_SYNC:
            self._LowerSync(node, frame, pre)
            return jm.Num("0")
        elif path == _HOST_DEFER:
            self.report[u"skip:ysm.defer(延迟事件尚未支持, 本次调用删除)"] += 1
            return jm.Num("0")
        else:
            slot = len(self.stopAllSlots) + 1
            globalFlag = self._ConstantFlag(node.args[0]) if node.args else False
            self.stopAllSlots.append(OrderedDict([("slot", slot), ("global", bool(globalFlag))]))
            pre.append(_Increment(_VariableName(STOP_ALL_COUNTER.format(slot))))
            self.report[u"map:ysm.stop_all_sounds -> 主包音效宿主(请求计数器)"] += 1
        return jm.Num("1")

    def _LowerSync(self, node, frame, pre):
        """ysm.sync(数值...): 调用点槽位的实参变量 + 计数器 +1; 主包只轮询本机玩家的(Java: 远程玩家上调用什么都不做,
        等服务端转发), 发给服务端, 服务端转发给全体客户端的同一玩家"""
        args = node.args
        if len(args) > MAX_SYNC_ARGUMENTS:
            self.report[u"zero:ysm.sync(参数超过 {} 个, Java 同样解析失败)".format(MAX_SYNC_ARGUMENTS)] += 1
            return
        slot = len(self.syncSlots) + 1
        for position, arg in enumerate(args):
            pre.append(_Assign(_VariableName(SYNC_ARGUMENT.format(slot, position)), self._LowerExpr(arg, frame, pre)))
        pre.append(_Increment(_VariableName(SYNC_COUNTER.format(slot))))
        self.syncSlots.append(OrderedDict([("slot", slot), ("count", len(args))]))
        self.report[u"map:ysm.sync -> 主包同步宿主(请求计数器, 经服务端转发)"] += 1

    def _InputQuery(self, path, args):
        """ysm.keyboard(GLFW 键码...) / ysm.mouse(GLFW 鼠标键) → 主包写在本机玩家上的键鼠状态 query.mod(远程玩家与纸娃娃上
        没人写, 读注册默认值 0 —— Java 在它们的动画里读的是本机键盘, 只拿来决定要不要 sync, 而 sync 在它们身上本就不发)"""
        label = u".".join(path)
        codes = []
        for arg in args:
            number = jm.NumberValue(arg)
            if number is None:
                self.report[u"zero:{}(键码不是常量, 运行层不知道要跟踪哪个键)".format(label)] += 1
                return jm.Num("0")
            codes.append(int(number))
        if path == _INPUT_KEYBOARD:
            supported, template, table = SUPPORTED_GLFW_KEYS, KEY_QUERY, self.keyCodes
            valid = codes
        else:
            supported, template, table = SUPPORTED_GLFW_MOUSE, MOUSE_QUERY, self.mouseButtons
            valid = codes if len(codes) == 1 else []
        dropped = [code for code in valid if code not in supported]
        valid = [code for code in valid if code in supported]
        if dropped:
            self.report[u"zero:{}({} 超出范围或网易没有对应的键, 按没按下处理)".format(
                label, u", ".join(str(code) for code in dropped))] += 1
        if not valid:
            return jm.Num("0")
        result = None
        for code in valid:
            table.add(code)
            term = _QueryName(template.format(code))
            result = term if result is None else jm.Node("binary", op="||", left=result, right=term)
        self.report[u"map:{} -> 主包运行层按键状态 query.mod(本机玩家)".format(label)] += 1
        return result

    @staticmethod
    def _ConstantFlag(node):
        number = jm.NumberValue(node)
        return bool(number) if number is not None else None

    @staticmethod
    def _InstanceId(node):
        """Java 实例 id: 字符串 → 该串; 非负数字 n → n=0 不跟踪、否则 '#n'; 负数 / 非字面量 → None(不播)"""
        if node.kind == "str":
            return node.value
        number = jm.NumberValue(node)
        if number is None:
            return None
        integer = int(number)
        if integer < 0:
            return None
        return u"" if integer == 0 else u"#{}".format(integer)

    def _LowerPlaySound(self, node, frame, pre):
        args = node.args
        if len(args) < 2 or len(args) > 5:
            self.report[u"zero:ysm.play_sound(参数个数不对, Java 同样解析失败)"] += 1
            return
        instanceId = self._InstanceId(args[0])
        if instanceId is None:
            self.report[u"warn:ysm.play_sound 实例 id 不是字面量或为负数(已丢弃): {}".format(jm.Print(args[0]))] += 1
            return
        if args[1].kind != "str" or not args[1].value.strip():
            self.report[u"warn:ysm.play_sound 音效名不是字符串字面量(已丢弃): {}".format(jm.Print(args[1]))] += 1
            return
        soundName = args[1].value.strip()
        mode = 0
        if len(args) >= 3:
            number = jm.NumberValue(args[2])
            if number is None:
                self.report[u"warn:ysm.play_sound 模式不是常量(按 0): {}".format(jm.Print(args[2]))] += 1
            else:
                mode = int(number)
        if mode < 0 or mode > 7:
            self.report[u"zero:ysm.play_sound(模式 {} 超出 0~7, Java 同样不播)".format(mode)] += 1
            return
        if self.sounds is None:
            self.report[u"warn:ysm.play_sound({})(没有音频汇, 未登记)".format(soundName)] += 1
            return
        self.sounds.Register(soundName)
        _key, definitionName, vanilla = self.sounds.entries[soundName]
        slot = len(self.soundSlots) + 1
        entry = OrderedDict([
            ("slot", slot), ("sound", definitionName), ("id", instanceId),
            ("force", bool(mode & 1)), ("global", bool(mode & 2)), ("loop", bool(mode & 4)),
        ])
        statements = [_Increment(_VariableName(SOUND_COUNTER.format(slot)))]
        for position, field, variable, default in ((3, "volume", SOUND_VOLUME, 1.0), (4, "pitch", SOUND_PITCH, 1.0)):
            if len(args) <= position:
                entry[field] = default
                continue
            value = self._LowerExpr(args[position], frame, pre)
            number = jm.NumberValue(value)
            if number is not None:
                entry[field] = min(max(number, 0.001), 1000.0)
            else:
                entry[field] = None
                statements.insert(0, _Assign(_VariableName(variable.format(slot)), value))
        if not vanilla and self.soundPath is not None:
            duration = OggDurationSeconds(self.soundPath(soundName))
            if duration is not None:
                entry["duration"] = duration
        self.soundSlots.append(entry)
        pre.extend(statements)
        self.report[u"map:ysm.play_sound({}) -> 主包音效宿主(请求计数器)".format(soundName)] += 1

    def _LowerStopSound(self, node, frame, pre):
        args = node.args
        if not args or len(args) > 2:
            self.report[u"zero:ysm.stop_sound(参数个数不对, Java 同样解析失败)"] += 1
            return
        instanceId = self._InstanceId(args[0])
        if not instanceId:
            self.report[u"warn:ysm.stop_sound 实例 id 不是字面量(已丢弃): {}".format(jm.Print(args[0]))] += 1
            return
        globalFlag = self._ConstantFlag(args[1]) if len(args) == 2 else False
        slot = len(self.stopSlots) + 1
        self.stopSlots.append(OrderedDict([("slot", slot), ("id", instanceId), ("global", bool(globalFlag))]))
        pre.append(_Increment(_VariableName(STOP_COUNTER.format(slot))))
        self.report[u"map:ysm.stop_sound({}) -> 主包音效宿主(请求计数器)".format(instanceId)] += 1


def _MapChildren(node, func):
    """按子节点逐个套 func 的浅拷贝"""
    kind = node.kind
    if kind == "call":
        return node.Copy(args=[func(arg) for arg in node.args])
    if kind == "member":
        return node.Copy(target=func(node.target))
    if kind == "index":
        return node.Copy(target=func(node.target), index=func(node.index))
    if kind == "unary":
        return node.Copy(operand=func(node.operand))
    if kind == "binary":
        return node.Copy(left=func(node.left), right=func(node.right))
    if kind == "cond":
        return node.Copy(test=func(node.test), then=func(node.then))
    if kind == "ternary":
        return node.Copy(test=func(node.test), then=func(node.then), other=func(node.other))
    return node


_FOLDABLE_COMPARE = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
}


def _ConstantFold(node):
    """常量比较与常量条件的三元化简(纯表达式折叠后实参常见为字面量: halo(4) 的 4 == 4)"""
    node = _MapChildren(node, _ConstantFold)
    if node.kind == "binary" and node.op in _FOLDABLE_COMPARE:
        left, right = jm.NumberValue(node.left), jm.NumberValue(node.right)
        if left is not None and right is not None:
            return jm.Num("1" if _FOLDABLE_COMPARE[node.op](left, right) else "0")
    if node.kind == "ternary":
        test = jm.NumberValue(node.test)
        if test is not None:
            return node.then if test != 0 else node.other
    if node.kind == "cond":
        test = jm.NumberValue(node.test)
        if test is not None:
            return node.then if test != 0 else jm.Num("0")
    return node
