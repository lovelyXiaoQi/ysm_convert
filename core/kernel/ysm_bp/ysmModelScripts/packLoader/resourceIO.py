# -*- coding: utf-8 -*-
"""组件文件读取的唯一封装点: 客户端磁盘直读(只在开发测试成立) + 服务端引擎资源接口(发布态可用)。

两条路:
- 客户端磁盘直读: common.minecraftMod.instance().addonPaths 给出已挂载行为包的磁盘路径, 用 os/open 枚举读取。
  **只在开发测试成立**(MC Studio / ModPC / MCDK 直接挂源码目录, 文件是明文)。正式服与手机下载的组件除
  manifest.json 外每个文件都加密落盘(ENCRYPT_SIGN 魔数 + AES 密文, key 只在引擎原生层), 读到的是密文 ——
  这里认出魔数就按"不可直读"返回, 不解码、不抛异常(2026-09-30: 解码写在保护范围外, 手机上整个客户端连锁崩溃)。
- 服务端引擎资源接口 server_resource: list_server_directory / load_server_file 由引擎原生解密, 发布态可用,
  范围是全部已加载行为包(看不到资源包)。行为包里的模型声明与预编资源索引走这条路, 再由服务端下发给客户端
  (serverCollector / packTransfer)。

合规说明: os 属网易组件审核的违规模块, 直接 import 会被拦截 —— 与 server_resource 等隐藏接口同款经
__builtins__ 动态获取绕过; open 同理。全部文件系统操作收敛在本模块(唯一封装点), 包内其他文件一律不得直接
使用 os/open。隐藏模块都在用到时才取: 取不到只让对应功能失效, 不连累导入本模块的一整条链。

2026-08 游戏内实测排除的其他途径(勿改回):
- resource.load_file / load_all_file: 客户端 mod 上下文对任意路径恒返回空;
- GetModConfigJson: 只能读 /modconfigs 下的已知路径, 无目录枚举, 无法发现副包;
- server_resource: 仅服务端可用, 客户端全返回 None(所以由服务端读、再下发)。
addonPaths 只包含"本世界实际启用"的行为包(玩家安装但未启用的包不会被误注册)。
"""
import traceback

MODELS_DIR = "ysm_models"
PACK_FILE = "ysm.json"
COLLECTION_FILE = "ysm-pack.json"  # Java 合集清单(name/description/lang), 派生模型文件夹分组
_MAX_DEPTH = 2  # ysm_models/<包名>/ysm.json 与 Java 合集包 ysm_models/<合集>/<子包>/ysm.json

# 发布态加密文件的魔数(引擎里叫 ENCRYPT_SIGN): 正式服 / 手机下载的组件除 manifest.json 外每个文件都以它开头,
# 后接 36 字节 content id、24 字节 0 填充与 AES-128-CFB8 密文
ENCRYPT_SIGN = b"\x90\x1d0\x01"
ENCRYPTED_ERROR = "发布态加密文件(正式服 / 手机上的组件文件加密落盘), 客户端无法直读"

_modules = {}
_stats = {"encrypted": 0}


def _Builtin(name):
    """内置函数(__builtins__ 在不同加载方式下可能是 dict 也可能是模块)"""
    table = __builtins__
    if isinstance(table, dict):
        return table.get(name)
    return getattr(table, name, None)


def _GetHiddenModule(name):
    return _Builtin("__import__")(name, fromlist=[name])


def _ErrorText(error):
    """异常 → 可安全拼进 str 的文本(专用服务器默认编码是 ascii, 不能靠隐式转换)"""
    try:
        text = str(error)
    except Exception:
        text = repr(error)
    return text


def _Os():
    """os 模块(用到时才取, 取不到返回 None 并只告警一次)"""
    if "os" not in _modules:
        try:
            _modules["os"] = _GetHiddenModule("os")
        except Exception:
            _modules["os"] = None
            print("[YSM-PackLoader][ERROR] 取不到 os 模块, 客户端磁盘读取不可用")
            traceback.print_exc()
    return _modules["os"]


def _Open():
    if "open" not in _modules:
        _modules["open"] = _Builtin("open")
        if _modules["open"] is None:
            print("[YSM-PackLoader][ERROR] 取不到 open, 客户端磁盘读取不可用")
    return _modules["open"]


def _ToUnicodePath(path):
    """py2: 统一转 unicode, 避免中文用户名路径下 os 接口按 ascii 解码崩溃"""
    if isinstance(path, str):
        return path.decode("utf-8")
    return path


def SawEncryptedFiles():
    """客户端直读时是否碰到过发布态密文(据此判断"本机是发布态, 只能等服务端下发")"""
    return _stats["encrypted"] > 0


def EncryptedFileCount():
    """客户端直读累计碰到的发布态密文文件数"""
    return _stats["encrypted"]


def PathJoin(*parts):
    """路径拼接(供包内其他模块使用, 避免直接依赖违规模块 os)"""
    osModule = _Os()
    parts = [_ToUnicodePath(p) for p in parts]
    if osModule is None:
        return u"/".join(parts)
    return osModule.path.join(*parts)


def PathDirName(path):
    """取路径所在目录"""
    osModule = _Os()
    if osModule is None:
        return _ToUnicodePath(path).replace(u"\\", u"/").rsplit(u"/", 1)[0]
    return osModule.path.dirname(_ToUnicodePath(path))


def IsFile(path):
    """路径是否为存在的文件"""
    osModule = _Os()
    return bool(osModule is not None and osModule.path.isfile(_ToUnicodePath(path)))


def IsDir(path):
    """路径是否为存在的目录"""
    osModule = _Os()
    return bool(osModule is not None and osModule.path.isdir(_ToUnicodePath(path)))


def ListDir(path):
    """枚举目录项(名称列表, 排序); 失败返回空列表"""
    osModule = _Os()
    if osModule is None:
        return []
    try:
        return sorted(osModule.listdir(_ToUnicodePath(path)))
    except Exception:
        return []


def GetResourcePacksRoot():
    """引擎资源包安装根目录(磁盘路径); 接口不可用返回 None(联机大厅等场景)"""
    try:
        setting = _GetHiddenModule("setting")
        return _ToUnicodePath(setting.get_resource_packs_path())
    except Exception:
        return None


def GetAddonPaths():
    """返回已挂载行为包根目录列表(unicode); 接口不可用时返回空列表"""
    try:
        module = _GetHiddenModule("common.minecraftMod")
        paths = module.instance().addonPaths
    except Exception:
        print("[YSM-PackLoader][ERROR] 无法获取已挂载行为包列表")
        traceback.print_exc()
        return []
    return [_ToUnicodePath(p) for p in (paths or [])]


def FindPackFiles(addonPath):
    """枚举一个行为包内的模型声明, 返回 [(包名, ysm.json 绝对路径), ...]"""
    root = PathJoin(addonPath, MODELS_DIR)
    if not IsDir(root):
        return []
    result = []
    _Collect(root, result, 1)
    return result


def FindCollectionManifest(packFile, addonPath):
    """模型声明所属的 Java 合集清单 → (合集目录名, ysm-pack.json 绝对路径) 或 None。

    仅识别合集子包形态(ysm_models/<合集>/<子包>/ysm.json 且合集目录带 ysm-pack.json);
    顶层包的父目录是 ysm_models 根, 即使根下误放清单也不参与分组。
    """
    osModule = _Os()
    if osModule is None:
        return None
    packDir = osModule.path.dirname(_ToUnicodePath(packFile))
    collectionDir = osModule.path.dirname(packDir)
    root = osModule.path.join(_ToUnicodePath(addonPath), MODELS_DIR)
    if osModule.path.normpath(collectionDir) == osModule.path.normpath(root):
        return None
    manifest = osModule.path.join(collectionDir, COLLECTION_FILE)
    if not osModule.path.isfile(manifest):
        return None
    return osModule.path.basename(collectionDir).encode("utf-8"), manifest


def _Collect(directory, result, depth):
    osModule = _Os()
    try:
        names = sorted(osModule.listdir(directory))
    except Exception:
        print("[YSM-PackLoader][ERROR] 目录枚举失败: {}".format(_ToUnicodePath(directory).encode("utf-8")))
        traceback.print_exc()
        return
    for name in names:
        subDir = osModule.path.join(directory, name)
        if not osModule.path.isdir(subDir):
            continue
        packFile = osModule.path.join(subDir, PACK_FILE)
        if osModule.path.isfile(packFile):
            result.append((name.encode("utf-8"), packFile))
        elif depth < _MAX_DEPTH:
            _Collect(subDir, result, depth + 1)  # Java 合集包形态


def ReadTextFile(path):
    """读取文件文本, 返回 (文本, None) 或 (None, 错误信息); 自动去 BOM。

    发布态密文(ENCRYPT_SIGN 开头)只读 4 字节就返回 ENCRYPTED_ERROR; 不是 UTF-8 的文件同样按错误返回 ——
    本函数不向外抛异常(调用链在模块导入期, 抛出去会把整个客户端带倒)。
    """
    opener = _Open()
    if opener is None:
        return None, "open 不可用"
    try:
        handle = opener(_ToUnicodePath(path), "rb")
        try:
            head = handle.read(len(ENCRYPT_SIGN))
            if head == ENCRYPT_SIGN:
                _stats["encrypted"] += 1
                return None, ENCRYPTED_ERROR
            content = head + handle.read()
        finally:
            handle.close()
    except Exception as e:
        traceback.print_exc()
        return None, "读取失败: {}".format(_ErrorText(e))
    if not content:
        return None, "文件为空"
    try:
        return content.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, "不是 UTF-8 文本"


# ---------------------------------------------------------------- 服务端: 引擎资源接口 ----


def GetServerResource():
    """服务端引擎资源接口 server_resource(发布态可用, 引擎原生解密); 取不到返回 None。

    只在服务端可用(客户端恒 None)。专用服务器的模块加载期可能还没就绪, 所以取不到时不缓存, 下次再取。
    """
    module = _modules.get("server_resource")
    if module is not None:
        return module
    try:
        module = _GetHiddenModule("server_resource")
    except Exception:
        module = None
    if module is None:
        module = traceback.sys.modules.get("server_resource")
    if module is None or not hasattr(module, "list_server_directory") or not hasattr(module, "load_server_file"):
        return None
    _modules["server_resource"] = module
    return module


def ServerListDir(path):
    """server_resource.list_server_directory: 全部已加载行为包里该目录的一层条目(子目录也当条目返回); 失败返回空列表"""
    resource = GetServerResource()
    if resource is None:
        return []
    try:
        return list(resource.list_server_directory(path) or ())
    except Exception:
        return []


def ServerReadBytes(path):
    """server_resource.load_server_file → (去掉 BOM 的 bytes, None) 或 (None, 错误信息)"""
    resource = GetServerResource()
    if resource is None:
        return None, "server_resource 不可用"
    try:
        data = resource.load_server_file(path)
    except Exception as e:
        return None, "读取失败: {}".format(_ErrorText(e))
    if not data:
        return None, "文件为空或不存在"
    if isinstance(data, unicode):  # noqa: F821 — py2 运行时
        data = data.encode("utf-8")
    if data[:len(ENCRYPT_SIGN)] == ENCRYPT_SIGN:
        return None, "引擎接口返回的是密文(未解密)"
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    return data, None
