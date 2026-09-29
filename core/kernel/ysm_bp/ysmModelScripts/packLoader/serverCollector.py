# -*- coding: utf-8 -*-
"""服务端收集全部已加载行为包里的 JSON 模型包(ysm_models/**), 组成下发给客户端的载荷(纯逻辑, 读目录 / 读文件由调用方注入)。

为什么在服务端读: 正式服 / 手机上的组件文件除 manifest.json 外都加密落盘, 客户端 os/open 读到的是密文;
引擎资源接口 server_resource 原生解密, 发布态可用, 但只在服务端可用(见 resourceIO.Server*)。
list_server_directory 只返回一层, 子目录也当条目返回: 非 .json 的条目当子目录再列一次(对普通文件再列得到空表),
同女仆 / 机械动力的读法。范围是全部已加载行为包; 同一相对路径在几个行为包里都有时引擎只给一份(车万女仆自带的
内置包副本与主包同路径, 内容也相同)。

载荷(文本一律 unicode, 编码见 packTransfer):
    {"format": 1,
     "packs": [{"name": 包名, "dir": ysm_models 下的相对目录, "text": ysm.json 文本,
                "files": {包目录内相对路径: 文本}, "collection": 合集目录名或 None}, ...],
     "collections": {合集目录名: ysm-pack.json 文本},
     "indexes": {索引名: 预编资源索引文本},        # ysm_models/_rp_index/<资源包 uuid>.json, 见 resourceIndex
     "errors": [读取失败等说明, ...],
     "ok": 数据是否可信}                          # 一个模型包都没读到 = 不可信(YSM 自己就带内置包: 读到 0 个只能是
                                                   # 资源还没就绪 / 读取失败), 客户端不据此判定"存档模型已卸载", 过后再要
包目录的判定与客户端直读(resourceIO._Collect)同口径: ysm_models/<包>/ysm.json, 或合集形态
ysm_models/<合集>/<子包>/ysm.json(合集目录自己不是包); 包名 = 包目录名, 同名只收先到者。
"""
from .packTransfer import FORMAT_VERSION
from .resourceIO import COLLECTION_FILE, MODELS_DIR, PACK_FILE

# 预编资源索引的目录(ysm_models 下): 每个组件一份, 文件名取资源包 manifest 的 header uuid, 多个组件互不覆盖
RP_INDEX_DIR = "_rp_index"
_MAX_PACK_DEPTH = 2
# 递归列目录的防御上限(条目数 / 相对 ysm_models 的深度)
_MAX_ENTRIES = 20000
_MAX_DEPTH = 8
# 包目录里 ysm.json 之外的 json(旧产物的行为包副本)合计上限, 超出的不下发(解析器查不到按缺席处理, 与资源索引兜底)
_MAX_AUX_BYTES = 4 * 1024 * 1024


def _ToText(value):
    """引擎给的路径 / 读到的内容 → unicode(不依赖默认编码)"""
    if isinstance(value, unicode):  # noqa: F821 — py2 运行时
        return value
    if isinstance(value, str):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("latin-1")
    return u"{}".format(value)


def _Normalize(path):
    text = _ToText(path).replace(u"\\", u"/")
    while text.startswith(u"./"):
        text = text[2:]
    return text.strip(u"/")


def _RelativeToModels(path):
    """引擎给的条目路径 → ysm_models 下的相对路径(不在 ysm_models 下 → None)"""
    norm = _Normalize(path)
    head = _ToText(MODELS_DIR) + u"/"
    lowered = norm.lower()
    if lowered.startswith(head):
        return norm[len(head):]
    position = lowered.find(u"/" + head)
    if position >= 0:
        return norm[position + len(head) + 1:]
    return None


def _Children(listDir, parent):
    """列一层 → [(交给引擎的路径, ysm_models 下相对路径)]。引擎给包内完整相对路径(机械动力 / 女仆的用法);
    万一只给条目名, 按父目录拼成完整路径再用"""
    children = []
    for entry in listDir(parent) or ():
        if not entry:
            continue
        relative = _RelativeToModels(entry)
        if relative is None:
            name = _Normalize(entry).rsplit(u"/", 1)[-1]
            entry = _Normalize(parent) + u"/" + name
            relative = _RelativeToModels(entry)
        if relative:
            children.append((entry, relative))
    return children


def ListModelFiles(listDir):
    """ysm_models 下全部 .json → {ysm_models 下相对路径(unicode): 交给引擎读的路径}(同一相对路径只留一份)"""
    files = {}
    pending = _Children(listDir, MODELS_DIR)
    seen = set()
    position = 0
    while position < len(pending) and position < _MAX_ENTRIES:
        entry, relative = pending[position]
        position += 1
        if relative in seen:
            continue
        seen.add(relative)
        if relative.lower().endswith(u".json"):
            files.setdefault(relative, entry)
            continue
        if relative.count(u"/") >= _MAX_DEPTH:
            continue
        pending.extend(_Children(listDir, entry))
    return files


def _PackDirs(relativePaths):
    """全部相对路径 → 包目录列表(排序; 包目录不嵌套: 父目录本身是包的, 子目录里的 ysm.json 不算)"""
    candidates = []
    for relative in relativePaths:
        parts = relative.split(u"/")
        if parts[-1].lower() == PACK_FILE and 2 <= len(parts) <= _MAX_PACK_DEPTH + 1:
            candidates.append(u"/".join(parts[:-1]))
    candidates = sorted(set(candidates))
    return [d for d in candidates if not any(d.startswith(other + u"/") for other in candidates if other != d)]


def CollectPayload(listDir, readBytes):
    """→ (载荷 dict, 摘要 dict)。readBytes(引擎路径) → (bytes, None) 或 (None, 错误); 本函数不抛异常。"""
    payload = {"format": FORMAT_VERSION, "packs": [], "collections": {}, "indexes": {}, "errors": [], "ok": False}
    summary = {"packs": 0, "collections": 0, "indexes": 0, "auxFiles": 0, "bytes": 0}
    errors = payload["errors"]

    def _Read(relative, raw):
        data, err = readBytes(raw)
        if err:
            errors.append(u"{}/{}: {}".format(_ToText(MODELS_DIR), relative, _ToText(err)))
            return None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(u"{}/{}: 不是 UTF-8 文本".format(_ToText(MODELS_DIR), relative))
            return None
        summary["bytes"] += len(data)
        return text

    try:
        files = ListModelFiles(listDir)
    except Exception as error:  # noqa: BLE001 — 引擎接口异常时仍要给客户端回一份(空的)载荷
        errors.append(u"列目录失败: {!r}".format(error))
        return payload, summary

    # 预编资源索引: ysm_models/_rp_index/<名>.json
    indexHead = _ToText(RP_INDEX_DIR).lower() + u"/"
    for relative in sorted(files):
        lowered = relative.lower()
        if lowered.startswith(indexHead) and lowered.count(u"/") == 1:
            text = _Read(relative, files[relative])
            if text is not None:
                payload["indexes"][relative.split(u"/", 1)[1][:-len(u".json")]] = text
    summary["indexes"] = len(payload["indexes"])

    packDirs = _PackDirs(files.keys())
    byLower = dict((relative.lower(), relative) for relative in files)
    seenNames = {}
    collectionFile = _ToText(COLLECTION_FILE).lower()
    for packDir in packDirs:
        parts = packDir.split(u"/")
        name = parts[-1]
        if name in seenNames:
            errors.append(u"模型包名 {} 重复({} 与 {}), 已跳过后来者".format(name, seenNames[name], packDir))
            continue
        rawPackFile = byLower.get((packDir + u"/" + _ToText(PACK_FILE)).lower())
        text = _Read(rawPackFile, files[rawPackFile]) if rawPackFile else None
        if text is None:
            continue
        seenNames[name] = packDir
        collection = None
        manifest = byLower.get((parts[0] + u"/" + collectionFile).lower()) if len(parts) == 2 else None
        if manifest:
            collection = parts[0]
            if collection not in payload["collections"]:
                manifestText = _Read(manifest, files[manifest])
                if manifestText is not None:
                    payload["collections"][collection] = manifestText
        # 包目录里其余的 json(旧产物的行为包副本): 解析器按 ysm.json 里的相对路径读
        aux = {}
        auxBytes = 0
        prefix = packDir.lower() + u"/"
        for relative in sorted(files):
            if relative == rawPackFile or not relative.lower().startswith(prefix):
                continue
            auxText = _Read(relative, files[relative])
            if auxText is None:
                continue
            auxBytes += len(auxText)
            if auxBytes > _MAX_AUX_BYTES:
                errors.append(u"模型包 {} 的附带文件超过 {} 字节, 其余不下发: {}".format(name, _MAX_AUX_BYTES, relative))
                break
            aux[relative[len(prefix):]] = auxText
        summary["auxFiles"] += len(aux)
        payload["packs"].append({"name": name, "dir": packDir, "text": text, "files": aux, "collection": collection})
    summary["packs"] = len(payload["packs"])
    summary["collections"] = len(payload["collections"])
    payload["ok"] = bool(payload["packs"])
    return payload, summary
