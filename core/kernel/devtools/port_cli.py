# -*- coding: utf-8 -*-
"""转换器宿主入口(Python 2.7): 批处理移植 + 结构化事件流(JSON Lines)。

供独立转换器 ysm_convert(C# 壳: GUI / CLI / MCP 服务器)调用; 人工命令行用法仍见 port_java_pack.py。

    python port_cli.py --job <job.json>            按任务单执行(convert / validate / fix / baseline)
    python port_cli.py --discover <目录 | 列表.json>  发现 Java 模型包(单包 / 合集目录 / 上级目录)
    python port_cli.py --version

任务单(UTF-8 JSON):
    {
      "action": "convert",                       # convert | validate | fix | baseline
      "layout": {"root": "<组件根>", "rp": "<资源包根>", "bpModels": "<行为包>/ysm_models",
                 "refRp": "<java_default 基线所在资源包, 可省>"},
      "packs": [{"javaDir": "<Java 包目录>", "name": "<包名>", "collection": "<合集目录名或 null>"}],
      "collections": {"<合集目录名>": {"name": "...", "description": "...",
                                     "lang": {"zh_cn": {"name": "...", "description": "..."}},
                                     "folder_texture": "<可省: 文件夹封面的资源包纹理路径; 省略时沿用
                                                        Java 源旁 ysm-pack.png 拷进资源包的那张>"}},
      "options": {"withMods": false, "validate": true,
                  "compactJson": false,                  # 产物 JSON 压成一行(缺省缩进; 转换器壳默认 true)
                  "writeCollections": true}              # 末尾写合集清单(按包并行的子任务关掉, 交给 finalize)
    }
    action = finalize: 按包并行转换的收尾, packs 只列成功的包(name / collection), 写合集清单 + 体检。

事件流(stdout, 每行一个 JSON 对象, ASCII 安全; 内核自己的 print 全部改道 stderr 不会混进来):
    start / pack_start / log(level=info|detail|notice|warn|error) / molang(kind, label, count, attention)
    / collection / pack_done / validate_item / validate_done / done
    molang.kind: map(等价替换) / const(中性常量: 基岩无对应) / zero(置零) / func(函数改写) / warn(退化)
    / lower(动画名转小写) / norm(按 Java 语义规范化) / skip(Java 同样不挂载) / tick(每 tick 脚本) / other

路径约定: 任务单里的路径**保持 unicode** 交给内核(声明文件里读出的文件名全是 unicode, 与字节串路径
os.path.join 时 py2 会按 ascii 解码字节串, 输出目录含中文即炸 —— 2026-09-18 用户在 D:\桌面 下转换实证)。
内核里少数 "字节模板".format(路径) 的汇总行已改成 u"" 模板; 临时目录也统一成 unicode。
"""
import gc
import io
import json
import os
import re
import sys
import tempfile
import time
import traceback
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import port_java_pack as port  # noqa: E402
import fix_ported_controllers as fix  # noqa: E402
import validate_rp_animations as validate  # noqa: E402

VERSION = "1.1"    # 1.1: molang 事件新增 const / norm / skip / tick 类别
_FS_ENCODING = sys.getfilesystemencoding() or "mbcs"
_PY2 = sys.version_info[0] < 3

# 汇总行 → 级别(与 port_java_pack 的文本标记约定一致)。molang 汇总行("  [!] zero:... xN")与结构化
# molang 事件是同一份数据, 降为 detail 免得宿主把同一条提醒展示两次
_LEVEL_RULES = (
    (re.compile(r"^\s*\[ERROR\]"), "error"),
    (re.compile(r"^\s*\[WARN\]"), "warn"),
    (re.compile(r"^\s*\[!\]\s+(?:map|zero|func|warn|lower):"), "detail"),
    (re.compile(r"^\s*\[!\]"), "notice"),
    (re.compile(r"^\s+"), "detail"),
)
# molang 计数标签前缀 → 类别; attention = 汇总文本里带 [!] 的那类(必须人工过目)
_MOLANG_KINDS = ("map", "zero", "func", "warn", "lower", "norm", "skip", "tick")
# 内核把"Java 专有量基岩无对应、按语义中性常量处理"与等价替换记在同一类里: 映射表行 `map:名字 -> 字面量`
# (is_maid -> 0.0、ctrl.tac_hold_gun -> 0.0 ...)与函数策略表的 `func:ysm.名字(置常量)`(perlin_noise、bone_pos ...)。
# 它们正是"基岩版没有的 molang" —— 动画里依赖它变化的效果在基岩不会出现(docs/ysm-java-molang-mapping.md 第五节),
# 宿主单列成 const 提醒开发者。替换体整个是数字或单引号字符串即算常量; 映射表其余行的替换体都含查询/变量
# (devtools/test_port_cli.py 按内核映射表逐行守护: 常量行全判 const、运行层行一个都不判)
_CONST_REPLACEMENT = re.compile(r"^(?:-?[0-9]+(?:\.[0-9]+)?|'[^']*')$")
_CONST_FUNCTION_MARK = u"(置常量)"


def _ToText(value):
    """任意 str/unicode → unicode(报告行在 py2 里 str/unicode 混排, str 里还可能混着文件系统编码的路径)"""
    if isinstance(value, bytes):
        for encoding in ("utf-8", _FS_ENCODING):
            try:
                return value.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return value.decode("utf-8", "replace")
    return value


def _FsToText(value):
    """文件系统来源的字节串(__file__ / os.listdir(bytes) / gettempdir)→ unicode: 先按文件系统编码, 再退 utf-8"""
    if isinstance(value, bytes):
        for encoding in (_FS_ENCODING, "utf-8"):
            try:
                return value.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return value.decode("utf-8", "replace")
    return value


def _ToPath(value):
    """任务单里的路径 → unicode(内核全程用 unicode 路径, 见模块注)"""
    if value is None:
        return None
    return _FsToText(value) if isinstance(value, bytes) else value


# Windows 长路径: 传统 API 的完整路径上限 260 字符(MAX_PATH)。2026-09-18 末影龙娘的替换实体控制器
# <rp>\animation_controllers\<包>\replace_entities\ender_sword.animation_controllers.json 在稍深的输出目录下就拼到
# 271 字符, Python 2.7 报 "No such file or directory"(更深时报 WindowsError 206)。绝对路径加 \\?\ 前缀后走长路径:
# open / makedirs / listdir / stat / shutil / os.walk / relpath 实测 290 字符含中文目录全部正常。前缀路径不做
# "/" 规范化, 所以只给产物目录加(内核拼产物路径一律 os.path.join); Java 源目录不加(声明里的相对路径带 "/")。
_LONG_PREFIX = u"\\\\?\\"
_LONG_UNC_PREFIX = u"\\\\?\\UNC\\"


def _LongPath(path):
    if path is None or os.name != "nt":
        return path
    path = _ToPath(path)
    if path.startswith(_LONG_PREFIX):
        return path
    full = os.path.abspath(path).replace(u"/", u"\\")
    if full.startswith(u"\\\\"):             # UNC: \\server\share\... → \\?\UNC\server\share\...
        return _LONG_UNC_PREFIX + full[2:]
    return _LONG_PREFIX + full


def _Unprefix(text):
    """给人看的文本: 去掉长路径前缀(路径可能出现在日志 / 异常 / 体检文本的任意位置)"""
    text = _ToText(text)
    if not text:
        return text
    return text.replace(_LONG_UNC_PREFIX, u"\\\\").replace(_LONG_PREFIX, u"")


def _ExceptionText(error):
    """异常 → unicode 文本; SystemExit/Exception 带 unicode 中文参数时 str(error) 本身会抛 UnicodeEncodeError"""
    args = getattr(error, "args", None) or ()
    if args and isinstance(args[0], (bytes, type(u""))):
        return _ToText(args[0])
    try:
        return _ToText(str(error))
    except UnicodeError:
        return _ToText(repr(error))


# 临时目录统一成 unicode: tempfile.mkdtemp() 返回值与 gettempdir() 同型, 字节串形态在中文用户名下会与 unicode 文件名拼接失败
tempfile.tempdir = _FsToText(tempfile.gettempdir())


class EventSink(object):
    """事件写到真正的 stdout; 内核期间 sys.stdout 改道 stderr, 保证 stdout 只有 JSON 行"""

    def __init__(self):
        self._out = sys.stdout
        sys.stdout = sys.stderr

    def Emit(self, event, **fields):
        payload = {"event": event}
        for key, value in fields.items():
            payload[key] = value
        line = json.dumps(payload, ensure_ascii=True)
        self._out.write(line + "\n")
        self._out.flush()


def ClassifyLine(text):
    for pattern, level in _LEVEL_RULES:
        if pattern.match(text):
            return level
    return "info"


def SplitMolangLabel(label):
    """'zero:ctrl.xxx(...)' → (kind, 正文, attention); map/func 里的中性常量改记 const(见 _CONST_REPLACEMENT 注)"""
    label = _ToText(label)
    kind, _sep, body = label.partition(u":")
    if not _sep or kind not in _MOLANG_KINDS:
        return "other", label, False
    if kind == "map":
        _name, arrow, replacement = body.partition(u" -> ")
        if arrow and _CONST_REPLACEMENT.match(replacement.strip()):
            return "const", body, True
    elif kind == "func" and body.endswith(_CONST_FUNCTION_MARK):
        return "const", body, True
    attention = kind in ("zero", "warn", "lower") or (kind == "func" and u"未知" in body)
    return kind, body, attention


def SuggestPackName(folder, collectionFolder=None):
    """Java 包目录名 → 建议包名: 拼音化 + 小写 + 只留 [a-z0-9_]; 合集成员带合集前缀防资源 ID 冲突"""
    def _Slug(text):
        text = port.Pinyinize(_ToText(text)).lower()
        text = re.sub(u"[^a-z0-9_]+", u"_", text)
        return re.sub(u"_+", u"_", text).strip(u"_")

    name = _Slug(folder)
    if collectionFolder:
        prefix = _Slug(collectionFolder)
        if prefix and not name.startswith(prefix + u"_"):
            name = prefix + u"_" + name
    return name or u"pack"


def _ReadJsonSafe(path):
    try:
        return port.LoadJson(path), None
    except Exception as error:  # noqa: BLE001 — 野外包什么都可能坏, 发现阶段只报不抛
        return None, _ToText(str(error))


def _PackInfo(javaDir, collectionDir):
    manifest, error = _ReadJsonSafe(os.path.join(javaDir, "ysm.json"))
    folder = os.path.basename(javaDir)
    collectionFolder = os.path.basename(collectionDir) if collectionDir else None
    info = {
        "javaDir": _ToText(javaDir),
        "folder": _ToText(folder),
        "suggestedName": SuggestPackName(folder, collectionFolder),
        "collectionDir": _ToText(collectionDir) if collectionDir else None,
        "collectionFolder": _ToText(collectionFolder) if collectionFolder else None,
        "error": error,
    }
    if manifest is not None:
        info["spec"] = manifest.get("spec")
        info["metadata"] = manifest.get("metadata") or {}
        files = manifest.get("files") or {}
        player = files.get("player") or {}
        info["hasArm"] = bool((player.get("model") or {}).get("arm")) if isinstance(player.get("model"), dict) else False
        info["animationFiles"] = len(player.get("animation") or {})
        info["projectiles"] = len(files.get("projectiles") or {})
        info["vehicles"] = len(files.get("vehicles") or {})
    return info


def _IsPackDir(path):
    return os.path.isfile(os.path.join(path, "ysm.json"))


def _IsCollectionDir(path):
    return os.path.isfile(os.path.join(path, "ysm-pack.json"))


def Discover(dirs):
    """目录 → Java 包信息列表。接受: 单包目录 / 合集目录(带 ysm-pack.json) / 装着若干包或合集的上级目录(深 2 层)"""
    packs = []
    seen = set()

    def _Add(javaDir, collectionDir):
        key = os.path.normcase(os.path.abspath(javaDir))
        if key in seen:
            return
        seen.add(key)
        packs.append(_PackInfo(javaDir, collectionDir))

    for raw in dirs:
        base = os.path.abspath(_ToPath(raw))
        if not os.path.isdir(base):
            packs.append({"javaDir": _ToText(raw), "error": u"目录不存在"})
            continue
        if _IsPackDir(base):
            _Add(base, None)
            continue
        baseIsCollection = _IsCollectionDir(base)
        for child in sorted(os.listdir(base)):
            childPath = os.path.join(base, child)
            if not os.path.isdir(childPath):
                continue
            if _IsPackDir(childPath):
                _Add(childPath, base if baseIsCollection else None)
            elif not baseIsCollection:
                childIsCollection = _IsCollectionDir(childPath)
                for grand in sorted(os.listdir(childPath)):
                    grandPath = os.path.join(childPath, grand)
                    if os.path.isdir(grandPath) and _IsPackDir(grandPath):
                        _Add(grandPath, childPath if childIsCollection else None)
    return packs


def _CollectionManifest(spec, dirName, existing=None):
    """任务单里的合集描述 → ysm-pack.json 内容。

    以 existing(PortPack 从 Java 源旁搬来的那份)为底: 名字 / 描述 / 多语言 / 封面键都沿用, 任务单给了哪个
    就覆盖哪个 —— 只换封面时文件夹名不会退化成目录名。任务单给了显示名时连源里的 lang 一起去掉: 网易版
    只显示一个名字, 而运行时 lang.zh_cn.name 优先于 name(clientScanner._ParseCollectionName), 不去掉就盖不住。
    两边都没有显示名时用目录名, 保证运行时识别为合集。
    """
    base = existing if isinstance(existing, dict) else {}
    manifest = OrderedDict()
    for key in ("name", "description", "lang", port.COLLECTION_TEXTURE_KEY):
        if base.get(key):
            manifest[key] = base[key]
    if isinstance(spec, dict):
        if spec.get("name"):
            manifest["name"] = spec["name"]
            manifest.pop("lang", None)
        if spec.get("description"):
            manifest["description"] = spec["description"]
        lang = spec.get("lang")
        if isinstance(lang, dict) and lang:
            manifest["lang"] = lang
        if spec.get(port.COLLECTION_TEXTURE_KEY):
            manifest[port.COLLECTION_TEXTURE_KEY] = spec[port.COLLECTION_TEXTURE_KEY]
    if not manifest.get("name"):
        manifest = OrderedDict([("name", dirName)] + [(k, v) for k, v in manifest.items() if k != "name"])
    return manifest


def _InstallCollectionCover(dirName, coverImage):
    """任务单给的合集封面图 → 资源包 textures/ui/ysm_packs/<合集>.png; 返回 (纹理路径, 问题说明)。
    口径与移植工具搬 Java 合集的 ysm-pack.png 相同(port.PortCollectionManifest): PNG、不超过 1MB(Java 同样拒收);
    游戏里文件夹卡片按 Java 52x90 的比例等比铺满, 所以图最好也是这个比例。"""
    src = _ToPath(coverImage)
    if not src or not os.path.isfile(src):
        return None, u"封面图不存在: {}".format(_ToText(src))
    if not src.lower().endswith(".png"):
        return None, u"封面图必须是 PNG: {}".format(_ToText(src))
    if os.path.getsize(src) > port.COLLECTION_COVER_MAX_BYTES:
        return None, u"封面图超过 1MB(Java 同样拒收), 已跳过: {}".format(_ToText(src))
    texture = port.CollectionCoverTexture(dirName)
    port.CopyBinary(src, os.path.join(port.RP, *texture.split("/")) + ".png")
    return texture, None


def _ApplyLayout(layout):
    layout = layout or {}
    rp = _LongPath(layout.get("rp"))
    bpModels = _LongPath(layout.get("bpModels"))
    refRp = _LongPath(layout.get("refRp"))
    root = _LongPath(layout.get("root"))
    if rp is None or bpModels is None:
        raise SystemExit("[ERROR] 任务单 layout 缺 rp / bpModels")
    if refRp is None and not os.path.isdir(os.path.join(rp, "animations", port.JAVA_BASELINE_PACK)):
        # 产物资源包没有基线 → 用内核自带快照(仓库形态 = 本仓库 ysm_rp; 分发形态 = 打包时同步的快照)
        bundled = _LongPath(_FsToText(port.RP))
        if os.path.isdir(os.path.join(bundled, "animations", port.JAVA_BASELINE_PACK)):
            refRp = bundled
    port.SetLayout(rp=rp, bpModels=bpModels, refRp=refRp, root=root)
    fix.SetLayout(rp=rp, bpModels=bpModels, root=root)
    validate.SetLayout(rp=rp, bpModels=bpModels, root=root)
    return {"rp": _Unprefix(rp), "bpModels": _Unprefix(bpModels), "refRp": _Unprefix(refRp or rp),
            "baselinePresent": os.path.isdir(os.path.join(refRp or rp, "animations", port.JAVA_BASELINE_PACK))}


# 网易按有没有 entities 文件夹识别行为包: 没有就不挂载(MC Studio 测试与正式游戏都只启用资源包), ysm_models 里的
# ysm.json 扫不到; MCDK 按 manifest 建目录联接, 测不出来。与转换器壳 OutputTarget.EnsureBehaviorPackMarker 同一口径:
# 缺就补一个带 .gitkeep 的空文件夹(git 与打包脚本都不保留空目录), 已有的不动
_BEHAVIOR_PACK_MARKER_DIR = "entities"
_BEHAVIOR_PACK_MARKER_FILE = ".gitkeep"
# "位置: 说明" 与其他体检条目同形(宿主取第一个冒号前的文字作位置列, 所以位置用行为包目录名, 带盘符的完整路径放后面)
BEHAVIOR_PACK_MARKER_ERROR = (u"行为包 {name}: 缺 entities 文件夹, 网易按它识别行为包 —— 没有它 MC Studio 测试与正式游戏都不挂载"
                              u"这个行为包, 模型不会出现在选择界面(MCDK 按 manifest 建联接, 测不出来); 修复(fix)或重新转换会补上"
                              u" entities/.gitkeep({path})")


def _BehaviorPackRoot(bpModels):
    return os.path.dirname(bpModels.rstrip("\\/"))


def EnsureBehaviorPackMarker(bpModels):
    """产物行为包(bpModels 的上一级)缺 entities 文件夹就补上; 返回是否新建"""
    entities = os.path.join(_BehaviorPackRoot(bpModels), _BEHAVIOR_PACK_MARKER_DIR)
    if os.path.isdir(entities):
        return False
    os.makedirs(entities)
    io.open(os.path.join(entities, _BEHAVIOR_PACK_MARKER_FILE), "wb").close()
    return True


def BehaviorPackMarkerProblems(bpModels):
    """体检(只读): 行为包缺 entities 文件夹 → 一条错误文本"""
    root = _BehaviorPackRoot(bpModels)
    if os.path.isdir(os.path.join(root, _BEHAVIOR_PACK_MARKER_DIR)):
        return []
    shown = _Unprefix(root)
    return [BEHAVIOR_PACK_MARKER_ERROR.format(name=os.path.basename(shown), path=shown)]


def _EmitReport(sink, packName, lines):
    counts = Counter()
    for line in lines:
        text = _Unprefix(line)
        level = ClassifyLine(text)
        counts[level] += 1
        sink.Emit("log", pack=packName, level=level, text=text)
    return counts


def _RunValidation(sink, packNames):
    errors, warnings, animCount, ctlCount = validate.Run(packNames)
    errors = list(errors) + BehaviorPackMarkerProblems(port.BP_MODELS)
    for line in warnings:
        sink.Emit("validate_item", level="warn", text=_Unprefix(line))
    for line in errors:
        sink.Emit("validate_item", level="error", text=_Unprefix(line))
    sink.Emit("validate_done", errors=len(errors), warnings=len(warnings),
              animations=animCount, controllers=ctlCount)
    return len(errors)


def _WriteCollections(sink, usedCollections, collections):
    """合集清单: 任务单给了描述就在 PortPack 搬来的那份上覆盖(见 _CollectionManifest); 没给且源里也没有 →
    写最小清单保证成组。任务单的 coverImage(自定义封面图)先拷进资源包, 换成 folder_texture 键再写清单。"""
    for collection in usedCollections:
        target = os.path.join(port.BP_MODELS, collection, "ysm-pack.json")
        dirName = _ToText(collection)
        spec = collections.get(dirName)
        cover = warning = None
        if isinstance(spec, dict) and spec.get("coverImage"):
            cover, warning = _InstallCollectionCover(dirName, spec["coverImage"])
            spec = dict((k, v) for k, v in spec.items() if k != "coverImage")
            if cover:
                spec[port.COLLECTION_TEXTURE_KEY] = cover
        if spec is not None or not os.path.isfile(target):
            existing = port.LoadJson(target) if os.path.isfile(target) else None
            port.DumpJson(target, _CollectionManifest(spec, dirName, existing))
            written = True
        else:
            written = False
        sink.Emit("collection", dir=dirName, path=_Unprefix(target), written=written, cover=cover, warning=warning)


def RunConvert(sink, job):
    options = job.get("options") or {}
    withMods = bool(options.get("withMods"))
    packs = job.get("packs") or []
    collections = job.get("collections") or {}
    okCount = failCount = 0
    doneNames = []
    usedCollections = []
    EnsureBehaviorPackMarker(port.BP_MODELS)
    for index, spec in enumerate(packs):
        if index:
            # 同一进程连续转多个包时内存只涨不回: 2026-09-18 30 个包串行跑到第 28 个(凋灵娘)时 MemoryError,
            # 而它单独跑峰值只有 717MB。每包之间强制回收一次; 转换器壳另外按包起独立进程, 不依赖这里
            gc.collect()
        javaDir = _ToPath(spec.get("javaDir"))
        packName = _ToPath(spec.get("name"))
        collection = _ToPath(spec.get("collection")) or None
        sink.Emit("pack_start", pack=_ToText(packName), javaDir=_ToText(javaDir),
                  collection=_ToText(collection) if collection else None, index=index, total=len(packs))
        started = time.time()
        molang = Counter()
        try:
            lines = port.PortPack(javaDir, packName, collection, withMods, molangSink=molang)
        except SystemExit as error:  # 内核用 SystemExit 报"不是 Java 包"之类的输入错误
            failCount += 1
            sink.Emit("pack_done", pack=_ToText(packName), ok=False, seconds=round(time.time() - started, 2),
                      error=_Unprefix(_ExceptionText(error)), errors=1, warnings=0, notices=0)
            continue
        except Exception:  # noqa: BLE001 — 一个包崩了不影响后面的包
            failCount += 1
            sink.Emit("pack_done", pack=_ToText(packName), ok=False, seconds=round(time.time() - started, 2),
                      error=_Unprefix(traceback.format_exc()), errors=1, warnings=0, notices=0)
            continue
        counts = _EmitReport(sink, _ToText(packName), lines)
        for label in sorted(molang, key=_ToText):
            kind, body, attention = SplitMolangLabel(label)
            sink.Emit("molang", pack=_ToText(packName), kind=kind, label=body,
                      count=molang[label], attention=attention)
        okCount += 1
        doneNames.append(_ToText(packName))
        if collection and collection not in usedCollections:
            usedCollections.append(collection)
        sink.Emit("pack_done", pack=_ToText(packName), ok=True, seconds=round(time.time() - started, 2),
                  errors=counts["error"], warnings=counts["warn"], notices=counts["notice"])
    # 按包并行时(转换器壳每包一个子任务)各子任务关掉 writeCollections / validate, 由收尾任务 finalize 统一做
    if options.get("writeCollections", True):
        _WriteCollections(sink, usedCollections, collections)
    validationErrors = 0
    if options.get("validate", True) and doneNames:
        validationErrors = _RunValidation(sink, doneNames)
    sink.Emit("done", ok=(failCount == 0 and validationErrors == 0), packsOk=okCount, packsFailed=failCount,
              validationErrors=validationErrors)
    return 0 if failCount == 0 else 1


def RunFinalize(sink, job):
    """按包并行转换的收尾: 写合集清单 + 体检。转换器壳在全部子任务结束后调用, packs 只列**成功**的包
    (只用 name 与 collection); 各包的移植已由子任务完成, 它们的任务单里 writeCollections / validate 都关着。"""
    options = job.get("options") or {}
    collections = job.get("collections") or {}
    names, usedCollections = [], []
    for spec in job.get("packs") or []:
        name = _ToPath(spec.get("name"))
        if name:
            names.append(name)
        collection = _ToPath(spec.get("collection")) or None
        if collection and collection not in usedCollections:
            usedCollections.append(collection)
    _WriteCollections(sink, usedCollections, collections)
    validationErrors = 0
    if options.get("validate", True) and names:
        validationErrors = _RunValidation(sink, names)
    sink.Emit("done", ok=(validationErrors == 0), packsOk=0, packsFailed=0, validationErrors=validationErrors)
    return 0 if validationErrors == 0 else 1


def RunValidate(sink, job):
    names = [_ToPath(p.get("name")) for p in (job.get("packs") or []) if p.get("name")]
    errors = _RunValidation(sink, names)
    sink.Emit("done", ok=(errors == 0), packsOk=0, packsFailed=0, validationErrors=errors)
    return 0 if errors == 0 else 1


def RunFix(sink, job):
    names = [_ToPath(p.get("name")) for p in (job.get("packs") or []) if p.get("name")]
    if not names:
        names = fix._AllPackNames()
    failCount = 0
    EnsureBehaviorPackMarker(port.BP_MODELS)
    for index, packName in enumerate(names):
        if index:
            gc.collect()        # 同 RunConvert: 连续处理多个包时内存只涨不回
        sink.Emit("pack_start", pack=_ToText(packName), javaDir=None, collection=None, index=index, total=len(names))
        started = time.time()
        try:
            lines = fix.FixPack(packName)
        except Exception:  # noqa: BLE001
            failCount += 1
            sink.Emit("pack_done", pack=_ToText(packName), ok=False, seconds=round(time.time() - started, 2),
                      error=_Unprefix(traceback.format_exc()), errors=1, warnings=0, notices=0)
            continue
        counts = _EmitReport(sink, _ToText(packName), lines)
        sink.Emit("pack_done", pack=_ToText(packName), ok=True, seconds=round(time.time() - started, 2),
                  errors=counts["error"], warnings=counts["warn"], notices=counts["notice"])
    validationErrors = 0
    if (job.get("options") or {}).get("validate", True):
        validationErrors = _RunValidation(sink, [n for n in names])
    sink.Emit("done", ok=(failCount == 0 and validationErrors == 0), packsOk=len(names) - failCount,
              packsFailed=failCount, validationErrors=validationErrors)
    return 0 if failCount == 0 else 1


def RunBaseline(sink, job):
    packs = job.get("packs") or []
    if not packs:
        raise SystemExit("[ERROR] baseline 需要一个 Java 默认模型目录(packs[0].javaDir)")
    javaDir = _ToPath(packs[0].get("javaDir"))
    packName = _ToPath(packs[0].get("name")) or port.JAVA_BASELINE_PACK
    withMods = bool((job.get("options") or {}).get("withMods"))
    sink.Emit("pack_start", pack=_ToText(packName), javaDir=_ToText(javaDir), collection=None, index=0, total=1)
    started = time.time()
    try:
        lines = port.PortBaselinePack(javaDir, packName, withMods)
    except Exception:  # noqa: BLE001
        sink.Emit("pack_done", pack=_ToText(packName), ok=False, seconds=round(time.time() - started, 2),
                  error=_Unprefix(traceback.format_exc()), errors=1, warnings=0, notices=0)
        sink.Emit("done", ok=False, packsOk=0, packsFailed=1, validationErrors=0)
        return 1
    counts = _EmitReport(sink, _ToText(packName), lines)
    sink.Emit("pack_done", pack=_ToText(packName), ok=True, seconds=round(time.time() - started, 2),
              errors=counts["error"], warnings=counts["warn"], notices=counts["notice"])
    sink.Emit("done", ok=True, packsOk=1, packsFailed=0, validationErrors=0)
    return 0


_ACTIONS = {"convert": RunConvert, "validate": RunValidate, "fix": RunFix, "baseline": RunBaseline,
            "finalize": RunFinalize}


def _LoadJobFile(path):
    with io.open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write(__doc__)
        return 2
    if argv[0] == "--version":
        sys.stdout.write(json.dumps({"kernel": VERSION, "python": sys.version.split()[0],
                                     "portTool": _FsToText(os.path.join(HERE, "port_java_pack.py")),
                                     "pinyin": port._LazyPinyin is not None}) + "\n")
        return 0
    sink = EventSink()
    if argv[0] == "--discover":
        dirs = []
        for arg in argv[1:]:
            if arg.lower().endswith(".json") and os.path.isfile(arg):
                dirs.extend(_LoadJobFile(arg).get("dirs") or [])
            else:
                dirs.append(_ToText(arg))
        for info in Discover(dirs):
            sink.Emit("pack", **info)
        sink.Emit("done", ok=True)
        return 0
    if argv[0] == "--job":
        if len(argv) < 2:
            raise SystemExit("[ERROR] --job 需要任务单路径")
        job = _LoadJobFile(argv[1])
        action = job.get("action") or "convert"
        if action not in _ACTIONS:
            raise SystemExit("[ERROR] 未知 action: {}".format(action))
        # 落盘格式: 缺省缩进(直接拿 port_cli 跑本仓库时不改产物形态), 转换器壳默认传 compactJson=true
        port.SetJsonStyle((job.get("options") or {}).get("compactJson", False))
        layout = _ApplyLayout(job.get("layout"))
        sink.Emit("start", action=action, packs=len(job.get("packs") or []), layout=layout,
                  compactJson=port.JSON_COMPACT,
                  kernel={"version": VERSION, "python": sys.version.split()[0],
                          "pinyin": port._LazyPinyin is not None})
        return _ACTIONS[action](sink, job)
    raise SystemExit("[ERROR] 未知参数: {}".format(argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
