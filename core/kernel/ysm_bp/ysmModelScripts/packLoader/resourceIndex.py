# -*- coding: utf-8 -*-
"""资源包动画/动画控制器 ID 索引 —— 渲染注册前剔除指向不存在资源的引用。

索引的两个来源(2026-09-30):
- 预编索引(发布态唯一可用的来源): 打包 / 转换时把资源包里运行期要查的信息(动画 / 控制器 ID、文件归属、molang 概况、
  时长、几何事实)写进行为包 ysm_models/_rp_index/<资源包 uuid>.json(BuildPackIndex / SerializeIndex), 服务端经
  server_resource 读到后随模型包下发, 客户端 LoadPrecompiled 装进来。正式服 / 手机上的资源包文件是密文、联机时加入者
  本机也未必有资源包目录, 运行期扫描在那里拿不到任何东西。
- 运行期扫描(兜底, 只在开发测试有效): 读用户资源包目录的磁盘文件(下面各节)。托管模式(主包 compatClientSystem)下
  只在"某个模型包在预编索引里完全找不到"时扫一次(EnsureCoverage); 非托管(离线工具、女仆自带内核)保持首次查询即扫描。

背景: 注册进 ActorRender 的 (键, 资源ID) 若资源不存在, 引擎每次渲染重建都会
逐条刷 "Error: can't find animation <键>"(不致崩但刷屏, 且掩盖真问题)。

判定域(2026-08 游戏内实测校准, 勿放宽): 只对 `animation.<JSON模型包名>[_*].*` /
`controller.animation.<JSON模型包名>[_*].*` 做存在性判定 —— 即"模型自己命名空间
下的引用, 磁盘资源全集里确认缺失"才剔除。其他一律放行, 因为存在性对引擎侧
资源不可判定: 引擎内置 ID(animation.actor.* 等)不落盘、原版 json 带注释解析
失败、`.local` 是引用期修饰语法、历史条目有空值、controller.animation.fight
等命名空间由引擎与 YSM 共用。

best-effort: 索引不可用(联机大厅无本地目录等)时不过滤; json 解析失败的控制器文件
用正则兜底提取 ID(宽松方向, 只会多收不会误滤)。

只扫**用户资源包目录**, 不扫游戏安装目录的原版资源包(2026-09-19 去掉): 判定域只含 JSON 模型包
自己的命名空间, 原版 ID 本就不参与判定, 几何缩放/变量/控制器引用/轮盘时长也都只查包自己的资源 ——
而原版 vanilla_netease/models 有 1 万多个赛季皮肤 mesh(2.8GB), 逐个读全文要 7s, 进世界卡在这里。
动画文件不做 json 解析(见 _IndexAnimationText), 同样是加载耗时的大头。
"""
import json
import re
import traceback
from collections import OrderedDict

from .resourceIO import (
    EncryptedFileCount,
    GetResourcePacksRoot,
    IsDir,
    ListDir,
    PathJoin,
    ReadTextFile,
)

_SCAN_DIRS = (("animations", "animations"), ("animation_controllers", "animation_controllers"))
_MAX_SCAN_DEPTH = 4
_ID_PATTERN = re.compile(r'"((?:controller\.)?animation\.[^"\s]+)"')


def _WordToken(literal, escapes=False):
    """正则片段: literal 且前一个字符不是标识符字符(= 以 \\b 打头的写法); escapes 时另放行字符串里的 \\n \\t \\r 转义。

    边界判断放在字面量**之后**的定宽回看里: 以断言打头的模式用不上正则引擎的字面量快速定位, 在 180MB 动画文本上
    慢 10~40 倍(2026-09-19 实测 `\\b(?:variable|v)\\.` 0.92s → 本写法 0.07s, 结果逐项相同)。
    """
    checks = [r'(?<=(?<![A-Za-z0-9_]){0})'.format(literal)]
    if escapes:
        checks.append(r'(?<=\\[nrt]{0})'.format(literal))
    return r'{0}(?:{1})'.format(literal, "|".join(checks))


_MOLANG_VAR_PATTERN = re.compile(
    r'(?:{0}|{1})\.([A-Za-z_][A-Za-z0-9_]*)'.format(_WordToken("variable"), _WordToken("v")))
# 单条动画的 molang 概况(纸娃娃安全判定用, 见 QueryAnimationMolangProfile): 在动画体的原始 json 文本上取。
# 字符串里的换行/制表在原文里写作 \n \t, 紧跟其后的 v.x 前面是字母 n/t(没有词边界) —— 放行转义后的边界,
# 与按解析后的字符串取值同口径
_SPAN_VAR_PATTERN = re.compile(
    r'(?:{0}|{1})\.([A-Za-z_][A-Za-z0-9_]*)'.format(_WordToken("variable", True), _WordToken("v", True)))
_SPAN_QUERY_NAME_PATTERN = re.compile(
    r'(?:{0}|{1})\.([A-Za-z_][A-Za-z0-9_]*)'.format(_WordToken("query", True), _WordToken("q", True)))
# 原版界面纸娃娃(暂停界面 / 背包的 paper_doll_renderer)上求值安全的 query 短名(小写; query.mod.* 记作 "mod")。
# 纸娃娃是独立渲染实例: 没有物品栏(is_item_name_any 报 "called without a specified entity")、不在世界里
# (relative_block_* 报 "Scope requires an Actor"), YSM 选择界面的预览上 position / position_delta 也报"没有实体";
# 时间、朝向、运动量、姿态标志这类实体自身的量照常求值(原版纸娃娃动画 look_at_target_ui / move.arms 读的就是
# 这些)。白名单之外的一律按不安全 —— 宁可纸娃娃上少一条动画, 也不在界面打开期间逐帧刷错
PAPERDOLL_SAFE_QUERIES = frozenset([
    "mod",
    "anim_time", "life_time", "time_stamp", "frame_alpha", "delta_time", "time_of_day",
    "target_x_rotation", "target_y_rotation", "head_x_rotation", "head_y_rotation",
    "body_x_rotation", "body_y_rotation", "eye_target_x_rotation", "eye_target_y_rotation", "yaw_speed",
    "vertical_speed", "ground_speed", "modified_move_speed", "modified_distance_moved", "walk_distance",
    "is_on_ground", "is_in_water", "is_in_water_or_rain", "is_sneaking", "is_sprinting", "is_swimming",
    "is_crawling", "is_gliding", "is_riding", "is_sleeping", "is_alive", "is_moving", "is_on_fire",
    "is_spectator", "is_jumping", "swim_amount", "cape_flap_amount",
    "health", "max_health", "hurt_time", "death_ticks",
    "all_animations_finished", "any_animation_finished",
])
# 动画文件的顶层动画 ID: 只认 "键": 形态(值里出现同名字符串时后面不跟冒号)
_ANIMATION_KEY_PATTERN = re.compile(r'"(animation\.[^"\s]+)"\s*:')
# 动画体的顶层标量字段(骨骼/通道/时间戳里没有同名键; 叫这个名字的骨骼值是对象, 不会命中)
_JSON_SCALAR = r'(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|"(?:[^"\\]|\\.)*"|true|false|null)'
_ANIMATION_LENGTH_PATTERN = re.compile(r'"animation_length"\s*:\s*' + _JSON_SCALAR)
_ANIMATION_LOOP_PATTERN = re.compile(r'"loop"\s*:\s*' + _JSON_SCALAR)
# 关键帧时间戳键("1.25": 形态): 动画体里只有关键帧/timeline/音效/粒子的时间用数字字符串作键。
# animation_length 缺省时按它推 Java 的隐含时长(见 QueryAnimationLength)
_KEYFRAME_TIME_PATTERN = re.compile(r'"(\d+(?:\.\d+)?)"\s*:')
# Java 无限长(pojo Animation.calculateLength 无关键帧 → Float.MAX_VALUE)的表示, 与移植工具
# port_java_pack.JAVA_INFINITE_LENGTH 同值
JAVA_INFINITE_LENGTH = 1000000.0
_GEOMETRY_DIR = "models"
# 几何文件里要的三样(一遍扫完, 各分支都以字面量打头): identifier、Java 缩放声明、本工程生成的 ysm_* 骨骼名
_GEOMETRY_FACT_PATTERN = re.compile(
    r'"identifier"\s*:\s*"([^"]+)"|"(ysm_(?:height|width)_scale)"\s*:\s*(-?[0-9.]+)'
    r'|"name"\s*:\s*"(ysm_[A-Za-z0-9_]+)"')

def _NewIndex():
    return {
        "built": False, "available": False,
        "anims": set(), "controllers": set(),
        # 命名空间 → 该命名空间资源文件里引用的 molang 变量短名(变量零声明初始化用)
        "nsVars": {},
        # 几何 identifier → (ysm_height_scale, ysm_width_scale)(Java 缩放回读用)
        "geoScales": {},
        # 几何 identifier → 本工程生成的 ysm_* 骨骼名集合(小写)。用途: 按主几何带没带滑铲外包骨骼挑基线里的滑铲写法
        # (packParser.SelectPostureVariants)
        "geoYsmBones": {},
        # 文件级索引(路径解析模式): 包根相对路径(小写归一) → 有序资源 ID 列表 / 变量集
        # ysm.json 声明 "animations/xxx.animation.json" 时按文件精确注册其中全部动画
        "fileAnims": {},
        "fileControllers": {},
        "fileVars": {},
        # 动画 ID → (引用的 variable 短名 frozenset(小写), 引用了 query.*?, 引用了纸娃娃上不安全的 query?)
        # (按动画体在文件里的区段取; 查不到的 ID 查询方视为"未知", 保守处理; 旧预编文档只有前两项, 见 _MergeDocument)
        "animMolang": {},
        # 控制器 ID → 其状态引用的动画短键集合。用途: 模型包的并行族动画若已被自己的
        # 控制器驱动, 主包就不再合成直挂 animate 条目(通道接管), 无需 ysm.json 里写
        # 任何删除清单 —— 见 packParser._OrderJavaAnimates。正则兜底路径拿不到状态子树,
        # 不登记(查询方按"没有引用"处理, 与旧行为一致: 该包本来也没有可读的状态数据)
        "ctlAnimRefs": {},
        # 动画 ID → (animation_length 或 None, loop 原值)。用途: 轮盘动画播完复位 query.mod.ysm_wheel_anim
        # (Java ctrl.playing_extra_animation 在 PLAY_ONCE 播完即假)
        "animPlayback": {},
        # 动画 ID → 没写 animation_length 时的 Java 隐含时长(秒; 只登记缺省的那些, 移植产物都显式写了)
        "animImpliedLength": {},
    }


_index = _NewIndex()
_jsonPackNames = set()
_warnedModels = set()

# 预编索引的文档格式版本(BuildPackIndex 写, LoadPrecompiled 认): 字段含义变了就加一, 旧文档按"没有预编索引"处理
INDEX_FORMAT = 1
# managed: 宿主托管(主包 compatClientSystem) —— 查询时不隐式扫描, 等预编索引下发后由宿主 FinishManagedIndex 定稿;
# scanned: 运行期扫描做过没有(每个进程最多一次); precompiled: 已装载的预编索引名;
# coverNamespaces / coverFiles: 预编索引里出现过的命名空间与文件(判断模型包是否被预编索引覆盖)
_control = {"managed": False, "scanned": False, "precompiled": [], "coverNamespaces": set(), "coverFiles": set()}


def _NamespaceOf(resId):
    """animation.<ns>.<短名> / controller.animation.<ns>.<短名> → ns"""
    body = resId[len("controller.animation."):] if resId.startswith("controller.animation.") \
        else resId[len("animation."):] if resId.startswith("animation.") else None
    if not body:
        return None
    return body.split(".", 1)[0] or None


def SetJsonPackNames(packNames):
    """登记 JSON 模型包名(clientScanner 扫描完成时调用) —— 划定存在性判定域"""
    for name in packNames or []:
        if isinstance(name, str) and name:
            _jsonPackNames.add(name)


def _NormalizeRelPath(relPath):
    """声明/索引两侧共用的路径归一: 反斜杠→斜杠、去 "./" 前缀、小写(Windows 大小写不敏感)"""
    path = str(relPath or "").replace("\\", "/").lower()
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _IndexAnimationText(text):
    """动画文件文本 → OrderedDict{动画 ID: (动画体区段起点, 终点)}(书写序)。

    不做 json 解析: 用 OrderedDict 保序解析全部动画(180MB)要 7.7s, 再逐条遍历 molang 2.2s(2026-09-19 离线实测,
    进世界卡在这里)。动画 ID 只作为 "animations" 段的键出现(键后面紧跟冒号), 相邻两个键之间就是前一条动画的全部内容。
    同一 ID 写了两次时顺序取首次出现、内容取最后一次(同 json 解析的结果)。
    """
    matches = list(_ANIMATION_KEY_PATTERN.finditer(text))
    spans = OrderedDict()
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        spans[match.group(1)] = (match.end(), end)
    return spans


def _SpanMolangProfile(text, start, end):
    """动画体区段的 molang 概况 → (引用的 variable 短名 frozenset(小写), 是否引用 query.*,
    是否引用纸娃娃上不安全的 query(PAPERDOLL_SAFE_QUERIES 之外))"""
    names = frozenset(name.lower() for name in _SPAN_VAR_PATTERN.findall(text, start, end))
    queries = set(name.lower() for name in _SPAN_QUERY_NAME_PATTERN.findall(text, start, end))
    return names, bool(queries), any(name not in PAPERDOLL_SAFE_QUERIES for name in queries)


def _SpanScalar(pattern, text, start, end):
    """动画体区段里某个顶层标量字段的值(与 json 解析同型: 数字/字符串/布尔/None); 没写该字段返回 None"""
    match = pattern.search(text, start, end)
    if match is None:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def _SpanImpliedLength(text, start, end):
    """没写 animation_length 的动画体 → Java 隐含时长(秒): 最后一个关键帧的时间, 没有关键帧 = 无限长。

    Java(pojo Animation.calculateLength)只看骨骼关键帧; 这里按区段里全部数字时间戳键取最大值, timeline/音效/粒子
    的时间晚于最后一个骨骼关键帧时会偏长 —— 只有手写包会走到这里(移植产物都显式写了 animation_length)。
    """
    times = [float(value) for value in _KEYFRAME_TIME_PATTERN.findall(text, start, end)]
    latest = max(times) if times else 0.0
    return latest if latest > 0 else JAVA_INFINITE_LENGTH


def _IndexAnimationFile(text, target, fileIds, index=None):
    """动画文件 → 登记全部动画 ID 及其 molang 概况/播放时长; 一条都认不出时退回宽松正则(同控制器文件的兜底)"""
    index = _index if index is None else index
    spans = _IndexAnimationText(text)
    if not spans:
        _IndexLooseIds(text, target, fileIds)
        return
    for resId, (start, end) in spans.items():
        resId = resId.encode("utf-8") if not isinstance(resId, str) else resId
        target.add(resId)
        fileIds.append(resId)
        index["animMolang"][resId] = _SpanMolangProfile(text, start, end)
        length = _SpanScalar(_ANIMATION_LENGTH_PATTERN, text, start, end)
        declared = float(length) if isinstance(length, (int, float)) and not isinstance(length, bool) else None
        index["animPlayback"][resId] = (declared, _SpanScalar(_ANIMATION_LOOP_PATTERN, text, start, end))
        if declared is None:
            index["animImpliedLength"][resId] = _SpanImpliedLength(text, start, end)


def _IndexControllerFile(text, target, fileIds, index=None):
    """控制器文件(体量小, 状态引用要结构)→ 保序 json 解析; 带注释/非严格 json(原版常见)退回宽松正则"""
    index = _index if index is None else index
    parsed = None
    try:
        parsed = json.loads(text, object_pairs_hook=OrderedDict)
    except ValueError:
        pass
    section = parsed.get("animation_controllers") if isinstance(parsed, dict) else None
    if not isinstance(section, dict):
        _IndexLooseIds(text, target, fileIds)
        return
    for resId, body in section.items():
        resId = resId.encode("utf-8") if not isinstance(resId, str) else resId
        target.add(resId)
        fileIds.append(resId)
        index["ctlAnimRefs"][resId] = _ControllerAnimRefs(body)


def _IndexLooseIds(text, target, fileIds):
    """宽松兜底: 文本里所有带引号的动画/控制器 ID 都算存在(只会多收, 不会误滤)"""
    for resId in _ID_PATTERN.findall(text):
        resId = resId.encode("utf-8") if not isinstance(resId, str) else resId
        target.add(resId)
        fileIds.append(resId)


def _ControllerAnimRefs(body):
    """控制器体 → 其全部状态引用的动画短键集(裸字符串与 {键: 权重} 两种条目形态)"""
    refs = set()
    if not isinstance(body, dict):
        return refs
    for state in (body.get("states") or {}).values():
        if not isinstance(state, dict):
            continue
        for item in state.get("animations") or []:
            names = item.keys() if isinstance(item, dict) else [item]
            for name in names:
                if isinstance(name, (str, unicode)):  # noqa: F821 — py2 运行时
                    refs.add(name.encode("utf-8") if not isinstance(name, str) else name)
    return refs


def _CollectJsonIds(dirPath, sectionKey, target, relDir, depth=1, index=None):
    index = _index if index is None else index
    fileTable = index["fileAnims"] if sectionKey == "animations" else index["fileControllers"]
    for name in ListDir(dirPath):
        path = PathJoin(dirPath, name)
        relPath = "{}/{}".format(relDir, name)
        if IsDir(path):
            if depth < _MAX_SCAN_DEPTH:
                _CollectJsonIds(path, sectionKey, target, relPath, depth + 1, index)
            continue
        if not name.endswith(".json"):
            continue
        text, err = ReadTextFile(path)
        if err:
            continue
        # 文件级注册顺序 = 文件书写顺序(路径解析模式的注册序依据), 两种文件都按书写序登记
        fileIds = []
        if sectionKey == "animations":
            _IndexAnimationFile(text, target, fileIds, index)
        else:
            _IndexControllerFile(text, target, fileIds, index)
        if not fileIds:
            continue
        # 文件级索引: 同名路径(不同资源包)先见者胜, 后见不覆盖
        normalized = _NormalizeRelPath(relPath)
        if normalized not in fileTable:
            fileTable[normalized] = fileIds
        variables = set(_MOLANG_VAR_PATTERN.findall(text))
        if not variables:
            continue  # 必须 continue: return 会中断整个目录的遍历
        index["fileVars"].setdefault(normalized, set()).update(variables)
        # 文件内 molang 变量按命名空间归组: 同一资源文件的变量属于其 ID 的命名空间
        # (模型包"变量零声明"的初始化来源, 见 packParser._BuildInitialize)
        for namespace in set(ns for ns in (_NamespaceOf(i) for i in fileIds) if ns):
            index["nsVars"].setdefault(namespace, set()).update(variables)


def _CollectGeometryFacts(dirPath, depth=1, index=None):
    """几何文件 → identifier: (ysm_height_scale, ysm_width_scale)(Java 缩放回读)与生成的 ysm_* 骨骼名"""
    index = _index if index is None else index
    for name in ListDir(dirPath):
        path = PathJoin(dirPath, name)
        if IsDir(path):
            if depth < _MAX_SCAN_DEPTH:
                _CollectGeometryFacts(path, depth + 1, index)
            continue
        if not name.endswith(".json"):
            continue
        text, err = ReadTextFile(path)
        if err or "ysm_" not in text:
            continue  # 绝大多数几何文件没有 ysm 缩放声明 / 生成骨骼, 提前跳过省解析
        # 顺序扫描: identifier 之后、下一个 identifier 之前的缩放 / 骨骼归属该几何体
        current = None
        for identifier, scaleKey, scaleValue, boneName in _GEOMETRY_FACT_PATTERN.findall(text):
            if identifier:
                current = identifier
                continue
            if not current:
                continue
            if boneName:
                index["geoYsmBones"].setdefault(current, set()).add(boneName.lower())
                continue
            height, width = index["geoScales"].get(current, (None, None))
            try:
                value = float(scaleValue)
            except (TypeError, ValueError):
                continue
            if scaleKey == "ysm_height_scale":
                height = value
            else:
                width = value
            index["geoScales"][current] = (height, width)


def _ScanPackRoot(packRoot, index):
    """扫一个资源包目录(animations / animation_controllers / models)进 index"""
    for subDir, sectionKey in _SCAN_DIRS:
        target = index["anims"] if sectionKey == "animations" else index["controllers"]
        dirPath = PathJoin(packRoot, subDir)
        if IsDir(dirPath):
            _CollectJsonIds(dirPath, sectionKey, target, subDir, 1, index)
    geoDir = PathJoin(packRoot, _GEOMETRY_DIR)
    if IsDir(geoDir):
        _CollectGeometryFacts(geoDir, 1, index)


def _ScanUserResourcePacks(reason=None):
    """运行期扫描用户资源包目录, 结果并入全局索引(每个进程最多一次; 已扫过返回 False)。

    只在开发测试有效: 正式服 / 手机上的资源包文件是发布态密文(ReadTextFile 认出魔数即跳过, 这里汇总报一句),
    联机时加入者本机也未必有这个资源包的目录。
    """
    if _control["scanned"]:
        return False
    _control["scanned"] = True
    suffix = "(兜底扫描: {})".format(reason) if reason else ""
    try:
        # 只扫用户资源包目录(原版资源包不扫, 见模块注)
        userRoot = GetResourcePacksRoot()
        roots = [userRoot] if userRoot and IsDir(userRoot) else []
        if not roots:
            print("[YSM-PackLoader] 资源索引不可用(无本地资源包目录), 跳过坏引用过滤" + suffix)
            return True
        encryptedBefore = EncryptedFileCount()
        packCount = 0
        for root in roots:
            for packName in ListDir(root):
                packRoot = PathJoin(root, packName)
                if not IsDir(packRoot):
                    continue
                packCount += 1
                _ScanPackRoot(packRoot, _index)
        _index["available"] = bool(_index["anims"] or _index["controllers"])
        encrypted = EncryptedFileCount() - encryptedBefore
        if encrypted:
            print("[YSM-PackLoader] 资源包里 {} 个文件是发布态密文, 运行期扫描读不到(资源信息要靠预编索引){}".format(
                encrypted, suffix))
        if _index["available"]:
            print("[YSM-PackLoader] 资源索引就绪: 动画 {} 个, 控制器 {} 个(扫描 {} 个资源包){}".format(
                len(_index["anims"]), len(_index["controllers"]), packCount, suffix))
        else:
            print("[YSM-PackLoader] 资源索引为空, 跳过坏引用过滤" + suffix)
    except Exception:
        print("[YSM-PackLoader][ERROR] 资源索引构建失败, 跳过坏引用过滤")
        traceback.print_exc()
    return True


def EnsureIndex():
    """返回索引 dict {built, available, anims, controllers, ...}。

    非托管(离线工具 / 女仆自带内核): 首次调用时扫描用户资源包目录(惰性, 一次)。
    托管(主包): 不隐式扫描 —— 预编索引随模型包下发、由宿主装载并 FinishManagedIndex 定稿; 定稿前按"索引不可用"处理
    (各查询给保守缺省, 坏引用过滤不滤)。
    """
    if _index["built"] or _control["managed"]:
        return _index
    _index["built"] = True
    _ScanUserResourcePacks()
    return _index


# ---------------------------------------------------------------- 预编索引 / 托管 ----

_JSON_COMMENT_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/')


def _Str(value):
    """json 读回的 unicode → UTF-8 str(与扫描登记的键同型: 集合 / 字典查找不能混类型)"""
    if isinstance(value, unicode):  # noqa: F821 — py2 运行时
        return value.encode("utf-8")
    return value


def SetManaged(managed=True):
    """宿主托管索引(主包 compatClientSystem 导入期调用): 查询不再隐式扫描, 等预编索引下发"""
    _control["managed"] = bool(managed)


def IsManaged():
    return _control["managed"]


def FinishManagedIndex():
    """宿主定稿: 预编索引装载(与需要时的兜底扫描)已完成, 之后的查询直接用当前内容"""
    _index["built"] = True
    _index["available"] = bool(_index["anims"] or _index["controllers"])
    return _index


def RunFallbackScan(reason):
    """显式兜底扫描用户资源包目录(托管模式下由宿主决定何时扫); 已扫过返回 False"""
    return _ScanUserResourcePacks(reason)


def PrecompiledSources():
    """已装载的预编索引名(诊断用)"""
    return list(_control["precompiled"])


def SerializeIndex(index):
    """索引 dict → 可 json 序列化的预编文档: 集合排序、元组转列表, 键与运行期索引一一对应(LoadPrecompiled 还原)"""
    def _Table(key, convert):
        return OrderedDict((k, convert(v)) for k, v in sorted(index[key].items()))

    return OrderedDict([
        ("format", INDEX_FORMAT),
        ("anims", sorted(index["anims"])),
        ("controllers", sorted(index["controllers"])),
        ("nsVars", _Table("nsVars", sorted)),
        ("geoScales", _Table("geoScales", list)),
        ("geoYsmBones", _Table("geoYsmBones", sorted)),
        ("fileAnims", _Table("fileAnims", list)),
        ("fileControllers", _Table("fileControllers", list)),
        ("fileVars", _Table("fileVars", sorted)),
        ("animMolang", _Table("animMolang", lambda profile: [sorted(profile[0]), bool(profile[1]),
                                                             bool(_ProfileUnsafeQuery(profile))])),
        ("ctlAnimRefs", _Table("ctlAnimRefs", sorted)),
        ("animPlayback", _Table("animPlayback", list)),
        ("animImpliedLength", _Table("animImpliedLength", lambda value: value)),
    ])


def BuildPackIndex(packRoot):
    """单个资源包目录 → 预编索引文档(打包 / 转换时调用, 见 devtools/build_rp_index.py); 与运行期扫描同一套读法"""
    index = _NewIndex()
    _ScanPackRoot(packRoot, index)
    return SerializeIndex(index)


def _ParseDocumentText(text):
    if isinstance(text, str):
        text = text.decode("utf-8-sig")
    try:
        return json.loads(text)
    except ValueError:
        # 发布脚本可能给 json 插 /* */ 声明头: 字符串感知地剥掉注释再解析
        return json.loads(_JSON_COMMENT_TOKEN.sub(lambda m: m.group(0) if m.group(0)[:1] == '"' else "", text))


def _MergeDocument(doc, index):
    """预编文档并入 index: 集合求并; 文件表与逐 ID 的数据先到者胜(与扫描"同名路径先见者胜"同口径)"""
    for key in ("anims", "controllers"):
        index[key].update(_Str(resId) for resId in doc.get(key) or ())
    for key in ("nsVars", "fileVars", "geoYsmBones"):
        for name, values in (doc.get(key) or {}).items():
            index[key].setdefault(_Str(name), set()).update(_Str(v) for v in values or ())
    for identifier, scales in (doc.get("geoScales") or {}).items():
        identifier = _Str(identifier)
        height, width = (list(scales or []) + [None, None])[:2]
        oldHeight, oldWidth = index["geoScales"].get(identifier, (None, None))
        index["geoScales"][identifier] = (oldHeight if oldHeight is not None else height,
                                          oldWidth if oldWidth is not None else width)
    for key in ("fileAnims", "fileControllers"):
        for path, ids in (doc.get(key) or {}).items():
            index[key].setdefault(_Str(path), [_Str(resId) for resId in ids or ()])
    for resId, profile in (doc.get("animMolang") or {}).items():
        # 两项 = 纸娃娃安全判定之前的旧文档(转换器/打包脚本早先写的): 不安全项按"引用了 query"保守取
        if isinstance(profile, list) and len(profile) in (2, 3):
            hasQuery = bool(profile[1])
            unsafe = bool(profile[2]) if len(profile) == 3 else hasQuery
            index["animMolang"].setdefault(
                _Str(resId), (frozenset(_Str(n) for n in profile[0] or ()), hasQuery, unsafe))
    for ctlId, refs in (doc.get("ctlAnimRefs") or {}).items():
        index["ctlAnimRefs"].setdefault(_Str(ctlId), set(_Str(r) for r in refs or ()))
    for resId, playback in (doc.get("animPlayback") or {}).items():
        if isinstance(playback, list) and len(playback) == 2:
            index["animPlayback"].setdefault(_Str(resId), (playback[0], _Str(playback[1])))
    for resId, value in (doc.get("animImpliedLength") or {}).items():
        index["animImpliedLength"].setdefault(_Str(resId), value)


def _NoteCoverage(doc):
    for key in ("anims", "controllers"):
        for resId in doc.get(key) or ():
            namespace = _NamespaceOf(_Str(resId))
            if namespace:
                _control["coverNamespaces"].add(namespace)
    for key in ("fileAnims", "fileControllers"):
        for path in doc.get(key) or {}:
            _control["coverFiles"].add(_NormalizeRelPath(_Str(path)))


def LoadPrecompiled(documents):
    """装载预编索引 {名字: 文档文本或 dict}(服务端随模型包下发); 返回装进来的份数。格式不认识 / 解析失败的跳过并告警"""
    loaded = 0
    for name in sorted(documents or {}):
        try:
            doc = documents[name]
            if not isinstance(doc, dict):
                doc = _ParseDocumentText(doc)
            if not isinstance(doc, dict) or doc.get("format") != INDEX_FORMAT:
                print("[YSM-PackLoader][WARN] 预编资源索引 {} 的格式不认识, 已跳过(需要 format={})".format(
                    _Str(name), INDEX_FORMAT))
                continue
            _MergeDocument(doc, _index)
            _NoteCoverage(doc)
        except Exception:
            print("[YSM-PackLoader][ERROR] 预编资源索引 {} 装载失败, 已跳过".format(_Str(name)))
            traceback.print_exc()
            continue
        _control["precompiled"].append(_Str(name))
        loaded += 1
    if loaded:
        _index["available"] = bool(_index["anims"] or _index["controllers"])
    return loaded


def IsPackCovered(packName, declaredPaths=()):
    """预编索引里有没有这个模型包的资源: 命名空间 animation.<包名>[_*] / controller.animation.<包名>[_*],
    或 ysm.json 里声明的任一资源文件(旧版写法的包, 动画在共享命名空间里, 靠声明的文件认)"""
    name = _Str(packName)
    if not name:
        return True
    head = name + "_"
    for namespace in _control["coverNamespaces"]:
        if namespace == name or namespace.startswith(head):
            return True
    for path in declaredPaths or ():
        if _NormalizeRelPath(_Str(path)) in _control["coverFiles"]:
            return True
    return False


def EnsureCoverage(packs):
    """托管模式: packs = [(包名, 声明的资源文件路径列表), ...]。有包在预编索引里完全找不到, 就兜底扫描一次本地资源包
    (开发测试的明文目录能补上; 发布态是密文, 扫描只会报一句"读不到")。返回没被覆盖的包名列表"""
    missing = [name for name, paths in packs if not IsPackCovered(name, paths)]
    if missing:
        RunFallbackScan("{} 个模型包没有预编索引: {}".format(len(missing), ", ".join(_Str(n) for n in missing[:8])))
    return missing


def _QueryNamespaceEntries(namespace, knownIds, prefix):
    """命名空间下的全部资源 → [(短键, 完整ID), ...](短键 = 去掉命名空间前缀, 按 ID 排序)"""
    head = "{}{}.".format(prefix, namespace)
    return [(resId[len(head):], resId) for resId in sorted(knownIds) if resId.startswith(head)]


def QueryNamespaceAnimations(namespace):
    """资源包内该命名空间的全部动画 —— 模型包动画的自动注册来源(无需行为包副本)"""
    index = EnsureIndex()
    if not index["available"]:
        return []
    return _QueryNamespaceEntries(namespace, index["anims"], "animation.")


def QueryNamespaceControllers(namespace):
    """资源包内该命名空间的全部动画控制器"""
    index = EnsureIndex()
    if not index["available"]:
        return []
    return _QueryNamespaceEntries(namespace, index["controllers"], "controller.animation.")


def _MatchIndexedFile(declPath, fileTable, nsHint=None):
    """声明路径 → 索引文件键; 未命中/歧义返回 None。给了 nsHint(包命名空间)时按下面顺序认:

    ① 精确路径命中, 且文件里有本包命名空间的 ID;
    ② 按文件名匹配(Java 原包声明 "animations/main.animation.json" 而资源实际放 "animations/<包名>/..."),
       候选文件须含本包命名空间的 ID, 唯一才采信;
    ③ 精确路径命中但文件里没有本包命名空间的 ID —— 内置包按资源包真实路径声明的共享文件(指挥官系共用的箭矢动画)。
    精确命中不先过命名空间会被别的资源包里的同名文件劫持: 原版 animations/arrow.animation.json 曾让 10 个酒狐包的
    投射物一直绑着原版动画、自己的动画从没注册(2026-09-19); 第三方平铺布局包里一个 animations/main.animation.json
    就能劫持全部 Java 包。命名空间用带尾点的完整段前缀, 防 wine_fox 误配 wine_fox_jk。
    没给 nsHint 时精确命中直接采信, 文件名匹配不做命名空间校验(旧行为)。
    """
    normalized = _NormalizeRelPath(declPath)
    if not normalized:
        return None
    exact = normalized if normalized in fileTable else None
    baseName = normalized.rsplit("/", 1)[-1]
    candidates = [key for key in fileTable if key.rsplit("/", 1)[-1] == baseName]
    if not nsHint:
        if exact:
            return exact
        return candidates[0] if len(candidates) == 1 else None
    prefixes = ("animation.{}.".format(nsHint), "controller.animation.{}.".format(nsHint))

    def _HasNamespace(key):
        return any(resId.startswith(prefixes) for resId in fileTable[key])

    if exact and _HasNamespace(exact):
        return exact
    candidates = [key for key in candidates if _HasNamespace(key)]
    if len(candidates) == 1:
        return candidates[0]
    return exact


def QueryFileAnimations(declPath, nsHint=None):
    """路径解析模式: 声明文件内的全部动画 ID(文件书写序)。未命中返回 None。

    ysm.json 的 files.player.animation 值为路径时按此精确注册 —— "此文件内的
    所有动画都注册到这个模型", 与 Java 按文件加载语义一致。
    """
    index = EnsureIndex()
    if not index["available"]:
        return None
    matched = _MatchIndexedFile(declPath, index["fileAnims"], nsHint)
    if matched is None:
        return None
    return list(index["fileAnims"][matched]), index["fileVars"].get(matched) or set()


def QueryFileControllers(declPath, nsHint=None):
    """路径解析模式: 声明文件内的全部动画控制器 ID(文件书写序)。未命中返回 None。"""
    index = EnsureIndex()
    if not index["available"]:
        return None
    matched = _MatchIndexedFile(declPath, index["fileControllers"], nsHint)
    if matched is None:
        return None
    return list(index["fileControllers"][matched]), index["fileVars"].get(matched) or set()


def QueryNamespaceControllerAnimRefs(namespace):
    """该命名空间下全部控制器引用的动画短键并集(通道接管判定, 见 _index["ctlAnimRefs"])"""
    index = EnsureIndex()
    if not index["available"]:
        return set()
    head = "controller.animation.{}.".format(namespace)
    refs = set()
    for ctlId, names in index["ctlAnimRefs"].items():
        if ctlId.startswith(head):
            refs |= names
    return refs


def QueryControllerAnimRefs(controllerId):
    """单个控制器各状态引用的动画短键集(索引不可用 / 未登记返回 None, 调用方按"未知"处理)"""
    index = EnsureIndex()
    if not index["available"]:
        return None
    refs = index["ctlAnimRefs"].get(controllerId)
    return set(refs) if refs is not None else None


def QueryNamespaceVariables(namespace):
    """该命名空间资源文件里引用的 molang 变量短名集合(变量零声明初始化用)"""
    index = EnsureIndex()
    if not index["available"]:
        return set()
    return set(index["nsVars"].get(namespace) or ())


def QueryGeometryScales(identifier):
    """几何体的 (ysm_height_scale, ysm_width_scale); 未声明返回 (None, None)"""
    index = EnsureIndex()
    if not index["available"]:
        return None, None
    return index["geoScales"].get(identifier, (None, None))


def QueryGeometryYsmBones(identifier):
    """几何体里本工程生成的 ysm_* 骨骼名(小写 frozenset); 索引不可用 / 没登记返回空集"""
    index = EnsureIndex()
    if not index["available"]:
        return frozenset()
    return frozenset(index["geoYsmBones"].get(identifier) or ())


def _InJudgeDomain(resId, prefix):
    """资源 ID 是否落在存在性判定域(JSON 模型包自己的命名空间)内"""
    if not resId.startswith(prefix):
        return False
    firstSegment = resId[len(prefix):].split(".", 1)[0]
    if not firstSegment:
        return False
    if firstSegment in _jsonPackNames:
        return True
    # 替换实体命名空间形态: animation.<包名>_<模型基名>.*
    for packName in _jsonPackNames:
        if firstSegment.startswith(packName + "_"):
            return True
    return False


def _IsMissing(resId, knownIds, prefix):
    """判定域内且索引确认缺失才算 missing; 其他情况一律视为存在"""
    if not isinstance(resId, str) or not resId:
        return False  # 空值/非法值为历史怪条目, 原样保留
    normId = resId[:-6] if resId.endswith(".local") else resId  # .local 为引用期修饰
    if normId in knownIds:
        return False
    return _InJudgeDomain(normId, prefix)


def QueryAnimationMolangProfile(animId):
    """动画 ID 的 molang 概况 {"vars": set(小写短名), "hasVar": bool, "hasQuery": bool, "hasUnsafeQuery": bool};
    索引不可用/ID 未登记(正则兜底解析的文件、引擎内置 ID)返回 None —— 调用方按
    "未知"保守处理。

    用途: 纸娃娃域安全判定(见 packParser._BuildPaperdollParallels) —— 纸娃娃是玩家
    的独立渲染实例, 物品/方块/位置类 query 在其上求值会刷错(hasUnsafeQuery, 口径见
    PAPERDOLL_SAFE_QUERIES); 变量由每实例初始化控制器(packParser._VARIABLE_INIT_KEY)在纸娃娃
    实例上补齐, 不再作为准入条件。
    """
    index = EnsureIndex()
    if not index["available"]:
        return None
    if not isinstance(animId, str) or not animId:
        return None
    normId = animId[:-6] if animId.endswith(".local") else animId
    profile = index["animMolang"].get(normId)
    if profile is None:
        return None
    return {"vars": set(profile[0]), "hasVar": bool(profile[0]), "hasQuery": profile[1],
            "hasUnsafeQuery": _ProfileUnsafeQuery(profile)}


def _ProfileUnsafeQuery(profile):
    """概况里的"引用了纸娃娃上不安全的 query"; 只有两项的概况(旧预编文档 / 测试直接写入)按"引用了 query"保守取"""
    return bool(profile[2]) if len(profile) > 2 else bool(profile[1])


def QueryAnimationOnceDuration(animId):
    """动画播一遍就结束时的时长(秒): 显式 animation_length > 0 且 loop 缺省/为假; 循环、
    hold_on_last_frame(停在末帧, Java 同样一直算"播放中")、时长未声明或索引查不到返回 None。

    用途: 轮盘动画播完复位 query.mod.ysm_wheel_anim(Java ctrl.playing_extra_animation 读
    cap 控制器状态, PLAY_ONCE 播完即回 IDLE)。移植产物都显式写了 animation_length。
    """
    index = EnsureIndex()
    if not index["available"] or not isinstance(animId, str) or not animId:
        return None
    playback = index["animPlayback"].get(animId[:-6] if animId.endswith(".local") else animId)
    if playback is None:
        return None
    length, loop = playback
    if loop is True or loop == "hold_on_last_frame" or not length or length <= 0:
        return None
    return length


def QueryAnimationLength(animId):
    """动画时长(秒), Java 口径(pojo Animation): 显式 animation_length, 缺省取最后一个关键帧的时间, 都没有 = 无限长
    (JAVA_INFINITE_LENGTH)。与 loop 无关。索引不可用/ID 未登记返回 None。

    用途: 选择卡片的 hover_fadeout 播放时长(Java CatalogModelCardState: fadeout.animationLength ticks × 50 ms)。
    """
    index = EnsureIndex()
    if not index["available"] or not isinstance(animId, str) or not animId:
        return None
    normId = animId[:-6] if animId.endswith(".local") else animId
    playback = index["animPlayback"].get(normId)
    if playback is None:
        return None
    if playback[0] is not None:
        return playback[0]
    return index["animImpliedLength"].get(normId, JAVA_INFINITE_LENGTH)


def IsAnimationMissing(animId):
    """单个动画 ID 是否"JSON 包判定域内且索引确认缺失"(GUI 预览注册等单点校验)。

    与 FilterRenderEntries 同一判定域与 best-effort 语义: 索引不可用/无 JSON 包/
    域外 ID 一律视为存在, 绝不误滤。
    """
    index = EnsureIndex()
    if not index["available"] or not _jsonPackNames:
        return False
    return _IsMissing(animId, index["anims"], "animation.")


def FilterRenderEntries(modelId, animations, controllers, animate):
    """剔除"JSON 模型包自己命名空间下、磁盘资源确认缺失"的注册条目及其 animate。

    返回 (animations, controllers, animate); 索引不可用或无 JSON 包时原样返回。
    同一模型只汇总告警一次。
    """
    index = EnsureIndex()
    if not index["available"] or not _jsonPackNames:
        return animations, controllers, animate
    droppedKeys = []
    keptAnimations = []
    for entry in animations or []:
        if _IsMissing(entry[1], index["anims"], "animation."):
            droppedKeys.append(entry[0])
        else:
            keptAnimations.append(entry)
    keptControllers = []
    for entry in controllers or []:
        if _IsMissing(entry[1], index["controllers"], "controller.animation."):
            droppedKeys.append(entry[0])
        else:
            keptControllers.append(entry)
    if not droppedKeys:
        return keptAnimations, keptControllers, animate
    droppedSet = set(droppedKeys)
    keptAnimate = [entry for entry in (animate or []) if entry[0] not in droppedSet]
    if modelId not in _warnedModels:
        _warnedModels.add(modelId)
        print("[YSM-PackLoader][WARN] 模型 {} 引用了 {} 个不存在的动画/控制器资源, "
              "已跳过注册(键): {}".format(modelId, len(droppedKeys), droppedKeys[:20]))
    return keptAnimations, keptControllers, keptAnimate
