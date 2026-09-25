# YSM 扩展接口（Extension API）对接指南

给其他模组作者的联动接口：**监听 YSM 的事件**，并通过事件里带的 **facade 对象** 调用 YSM 的能力。

- 服务端：玩家换模型之前（可以拦下）、换模型之后、重置之后都有事件；可以查、换、重置玩家的模型，给实体套模型，管模型权限。
- 客户端：YSM 把模型应用到玩家渲染之后、卸下之后、运行期有新模型注册时都有事件；可以查模型表、查玩家模型、重建玩家渲染、打开选择界面。

> 枪械模组的动作联动走玩家实体上的 molang 变量，见 [ysm-tac-protocol.md](ysm-tac-protocol.md)。

---

## 1. 快速开始

### 1.1 服务端

```python
# 你的模组的服务端系统
import mod.server.extraServerApi as serverApi

ServerSystem = serverApi.GetServerSystemCls()


class MyServerSystem(ServerSystem):
    def __init__(self, namespace, systemName):
        ServerSystem.__init__(self, namespace, systemName)
        self.ysm = None
        # 没装 YSM 时这个事件永远不会来, 不需要 ImportModule 探测, 也不需要 try
        self.ListenForEvent("ysm", "ExtensionApi", "ServerExtensionApiReady", self, self.OnYsmReady)
        self.ListenForEvent("ysm", "ExtensionApi", "PlayerModelChangedServerEvent", self, self.OnYsmModelChanged)

    def OnYsmReady(self, args):
        self.ysm = args["extension"]        # 服务端 facade, 整局有效, 可以存起来
        print("YSM 扩展接口版本", args["apiVersion"])

    def OnYsmModelChanged(self, args):
        print("玩家 {} 换成了 {} 的 {} 皮肤".format(args["playerId"], args["modelId"], args["skinId"]))
```

### 1.2 客户端

```python
import mod.client.extraClientApi as clientApi

ClientSystem = clientApi.GetClientSystemCls()


class MyClientSystem(ClientSystem):
    def __init__(self, namespace, systemName):
        ClientSystem.__init__(self, namespace, systemName)
        self.ListenForEvent("ysm", "ExtensionApi", "ClientExtensionApiReady", self, self.OnYsmReady)
        self.ListenForEvent("ysm", "ExtensionApi", "PlayerModelAppliedClientEvent", self, self.OnYsmModelApplied)

    def OnYsmReady(self, args):
        self.ysm = args["extension"]        # 客户端 facade
        print("已注册的 YSM 模型", self.ysm.GetModelIds())

    def OnYsmModelApplied(self, args):
        if args["renderReset"]:
            # YSM 换模型时整体清空了这个玩家身上的附加渲染资源, 你挂的动画 / 几何 / 渲染控制器要重新挂
            self.AddMyPlayerRender(args["playerId"])
```

### 1.3 其他框架的写法

事件是引擎的普通自定义事件，任何能按 `(namespace, systemName, eventName)` 监听的写法都行：

```python
# MODSDKSpring(Eplus 等在用)
@ListenEvent.Client(eventName="PlayerModelAppliedClientEvent", namespace="ysm", systemName="ExtensionApi")
def OnYsmModelApplied(self, args):
    ...
```

QuModLibs 的 `@Listen` 只监听引擎事件，要借任意一个系统对象代为注册（引擎按"实例 + 方法名"回调）：

```python
class _YsmListener(object):
    def OnYsmReady(self, args):
        ...

_listener = _YsmListener()
serverApi.GetSystem("Minecraft", "game").ListenForEvent(
    "ysm", "ExtensionApi", "ServerExtensionApiReady", _listener, _listener.OnYsmReady)
```

事件名、命名空间建议用常量而不是手抄：ready 之后 `ext.events.PLAYER_MODEL_CHANGED_SERVER_EVENT` 这类属性就是事件名（§4.1）。

---

## 2. 事件协议

| 常量 | 值 |
|---|---|
| 命名空间 `EXTENSION_API_NAMESPACE` | `"ysm"` |
| 系统名 `EXTENSION_API_SYSTEM_NAME` | `"ExtensionApi"` |
| 版本 `EXTENSION_API_VERSION` | `1` |

| 端 | 事件 | 说明 | 小节 |
|---|---|---|---|
| 服务端 | `ServerExtensionApiReady` | 服务端扩展接口就绪，`args["extension"]` 是服务端 facade | §3 |
| 服务端 | `PlayerTryChangeModelServerEvent` | 玩家即将换模型/皮肤，**可取消** | §5.1 |
| 服务端 | `PlayerModelChangedServerEvent` | 玩家已换模型/皮肤 | §5.2 |
| 服务端 | `PlayerModelResetServerEvent` | 玩家的 YSM 模型已重置（恢复原版/网易皮肤） | §5.3 |
| 客户端 | `ClientExtensionApiReady` | 客户端扩展接口就绪，`args["extension"]` 是客户端 facade | §3 |
| 客户端 | `PlayerModelAppliedClientEvent` | YSM 把模型应用到了某个玩家的渲染上 | §6.1 |
| 客户端 | `PlayerModelClearedClientEvent` | YSM 卸下了某个玩家的模型 | §6.2 |
| 客户端 | `ModelRegisteredClientEvent` | 运行期（ready 之后）有新模型注册 | §6.3 |

- 事件由 YSM 的专用系统 `("ysm", "ExtensionApi")` **本地广播**：服务端事件只有服务端系统收得到，客户端事件只有客户端系统收得到。
- 广播是**同步**的：你的监听返回后 YSM 才往下走；所有监听者拿到的是**同一个 dict**（可取消事件靠这个读回 `cancel`）。
- 监听优先级：`ListenForEvent` 的第 6 个参数 `priority`（0～10，越大越先执行）。
- 你的监听抛异常由引擎隔离并打印堆栈，不会打断 YSM，也不影响别的监听者。
- 载荷只有普通类型（str / int / bool / None），`extension` 字段是同进程的对象引用，不要经网络转发。

---

## 3. 生命周期

服务端：

```text
T0 各模组服务端系统 __init__        第三方 ListenForEvent("ysm", "ExtensionApi", "ServerExtensionApiReady", ...)
T1 引擎 LoadServerAddonScriptsAfter
T2 YSM 发布: IsReady() 变 True → 执行 OnReady 回调 → 广播 ServerExtensionApiReady
T3 之后随玩法: PlayerTryChangeModelServerEvent → 写存档(全体客户端同步) → PlayerModelChangedServerEvent ...
```

客户端：

```text
T0 各模组客户端系统 __init__        第三方 ListenForEvent("ysm", "ExtensionApi", "ClientExtensionApiReady", ...)
T1 引擎 LoadClientAddonScriptsAfter → YSM 广播 ClientExtensionApiReady
T2 之后随玩法: 存档模型下发 / 玩家进出视野 / 换模型 → PlayerModelAppliedClientEvent / PlayerModelClearedClientEvent
```

- 监听要在系统 `__init__` 里注册（早于 T1），否则会错过 ready：ready 每端只广播一次。
- facade 整局有效，可以存起来随时用。YSM 的注册面都是运行时查表，没有"ready 之后注册不进去"的窗口。

---

## 4. facade 公共面

### 4.1 标识与能力探测（两端都有）

```python
ext.side             # "server" / "client"
ext.apiVersion       # EXTENSION_API_VERSION, 当前 1
ext.namespace        # "ysm"
ext.systemName       # "ExtensionApi"
ext.events           # 协议常量: 事件名 / source / reason, 如 ext.events.PLAYER_MODEL_CHANGED_SERVER_EVENT
ext.HasCapability(n) # 能力探测(按端区分)
ext.GetCapabilities()  # 该端全部能力(排序后的列表)
ext.IsReady()        # 该端 ready 是否已发布
ext.OnReady(cb)      # 该端发布时执行 cb(); 已发布则立即执行
```

**先探测再调用**：新能力只加进能力集合、不动 `apiVersion`，所以别拿版本号猜功能面。

```python
def OnYsmReady(self, args):
    ext = args["extension"]
    if ext.HasCapability("player_model_set"):
        ...
```

### 4.2 能力清单

权威来源是 `ysmModelCoreScripts/api/extension/events.py` 的 `SERVER_CAPABILITIES` / `CLIENT_CAPABILITIES`，本表随它更新。

| capability | 服务端 | 客户端 | 内容 | 小节 |
|---|:---:|:---:|---|---|
| `player_model_events` | ✅ | ✅ | 服务端：尝试切换 / 已切换 / 已重置；客户端：已应用 / 已卸下 | §5 §6 |
| `player_model_query` | ✅ | ✅ | `GetPlayerModel` / `GetPlayerIsUsingYsmModel` | §7.1 §8 |
| `player_model_set` | ✅ | | `SetPlayerModel`：服务端让玩家换成指定模型 | §7.1 |
| `player_model_reset` | ✅ | | `ResetPlayerModel` | §7.1 |
| `entity_model_render` | ✅ | | `SetEntityModelRender` / `ResetEntityModelRender` / `GetEntityModelData` | §7.2 |
| `model_select_screen` | ✅ | ✅ | `OpenModelSelectScreen`（服务端版回调在服务端，客户端版回调在客户端） | §7.3 §8 |
| `model_permissions` | ✅ | | `ext.permissions`：模型使用权限 | §7.4 |
| `model_registered_event` | | ✅ | `ModelRegisteredClientEvent` | §6.3 |
| `model_registry_query` | | ✅ | `GetModelIds` / `HasModel` / `GetYsmModelDetails` | §8 |
| `player_model_rebuild` | | ✅ | `RebuildRenderPlayerModel` | §8 |
| `tac_protocol` | ✅ | ✅ | 玩家实体上的 `v.tac.*` 枪械联动协议（[ysm-tac-protocol.md](ysm-tac-protocol.md)） | — |

### 4.3 返回值约定

`SetPlayerModel`、`ResetPlayerModel`、服务端与客户端的 `OpenModelSelectScreen` 返回 `{"ok": bool, "error": str | None, ...}`：参数不对返回错误码，不抛业务异常。
其余查询与实体渲染方法的返回值见各小节。

---

## 5. 服务端事件

玩家模型统一摘要成三项：`modelId`（模型 ID，如 `ysm_pack:wine_fox_01_taisho_maid`）、`skinIndex`（皮肤序号，0 是 default）、`skinId`（皮肤键，如 `"default"`）。没装模型时这三项都是 `None`。

`source` 表示谁发起的：

| 值 | 含义 |
|---|---|
| `player` | 玩家客户端发起：选择界面换模型 / 换皮肤、重置按钮、局内换肤后的自动重置、存档模型所在的包已卸载时的自动重置 |
| `api` | 服务端模组经扩展接口发起：`SetPlayerModel` / `ResetPlayerModel` |

### 5.1 `PlayerTryChangeModelServerEvent`（可取消）

玩家即将换成另一个模型，或同一模型的另一个皮肤。在权限检查通过之后、写存档之前触发。

| 字段 | 类型 | 说明 |
|---|---|---|
| `playerId` | str | 玩家 ID（取自调用者，客户端伪造不了别人） |
| `modelId` / `skinIndex` / `skinId` | | 要换成的模型 |
| `oldModelId` / `oldSkinIndex` / `oldSkinId` | | 现在的模型（没装为 `None`） |
| `source` | str | `player` / `api` |
| `cancel` | bool | **可写**：置 `True` 拦下这次切换 |
| `reason` | str | **可写**：拦下的原因。玩家自己选的会在选择界面提示这句话（空串时提示"该模型当前不可用"）；接口发起的会写进 `SetPlayerModel` 的回调结果 |

```python
def OnYsmTryChangeModel(self, args):
    if args["modelId"] in self.lockedModels and not self.HasBought(args["playerId"], args["modelId"]):
        args["cancel"] = True
        args["reason"] = "先去商店买下这个模型"
```

- 同一个模型的同一个皮肤重复下发（例如接口重复请求）不算切换，不发三个服务端事件。
- 玩家没有 YSM 的模型使用权限时直接被拒，不会走到这个事件。

### 5.2 `PlayerModelChangedServerEvent`

已经写入存档（全体客户端随后同步渲染）。字段同 §5.1，没有 `cancel` / `reason`。此时 `ext.GetPlayerModel(playerId)` 已经是新模型。

### 5.3 `PlayerModelResetServerEvent`

玩家的 YSM 模型被清掉（各客户端恢复原版 / 网易资源中心皮肤）。原来就没有模型时不发。

| 字段 | 说明 |
|---|---|
| `playerId` | 玩家 ID |
| `oldModelId` / `oldSkinIndex` / `oldSkinId` | 重置前的模型 |
| `source` | `player` / `api` |

---

## 6. 客户端事件

### 6.1 `PlayerModelAppliedClientEvent`

YSM 把模型整套应用到了某个玩家的渲染上（本机玩家与其他玩家都会发）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `playerId` | str | 被应用的玩家 |
| `modelId` / `skinIndex` / `skinId` | | 应用的模型 |
| `isLocalPlayer` | bool | 是不是本机玩家 |
| `reason` | str | 见下表 |
| `renderReset` | bool | **这次应用前 YSM 整体清空过该玩家的附加渲染资源** |

`renderReset` 是最重要的字段：YSM 换模型时调用引擎的 `ResetEntityExtraSkin`，它会把**所有模组**经 ActorRender 挂在这个玩家身上的动画、动画控制器、几何、贴图、渲染控制器、材质一并清掉（不这样做，模型切换后会残留上一个模型的资源）。所以：

> 你的模组如果往玩家身上挂了渲染资源，收到 `renderReset` 为 `True` 的 §6.1 或 §6.2 事件时，要把它们重新挂上并 `RebuildPlayerRender()`。

| reason | 含义 | renderReset |
|---|---|---|
| `change` | 模型数据变了：切换模型 / 皮肤（含服务端接口换的），也包括进世界时存档模型的下发 | True |
| `enterView` | 这个玩家进入视野（新建的实体，本来就是干净的） | False |
| `created` | 引擎重建了这个玩家的渲染（皮肤异步加载完成，`AddPlayerCreatedClientEvent`） | False |
| `pending` | 进世界时补应用：属性回调注册之前就已到达的模型数据 | False |
| `rebuild` | 有模组调用了 `RebuildRenderPlayerModel` | False |

- **按 `renderReset` 决定要不要重挂，不要按 `reason` 猜**，reason 只用于日志和调试。
- 引擎在 `AddPlayerCreatedClientEvent` 时会清掉此前挂在该玩家身上的全部资源（引擎行为，与 YSM 无关）：这种情况请在你自己的 `AddPlayerCreatedClientEvent` 监听里处理（本机玩家也会收到这个事件）。
- 进世界时本机玩家可能先后收到 `change`、`created` 等多次应用，属正常现象。

### 6.2 `PlayerModelClearedClientEvent`

YSM 卸下了某个玩家的模型（恢复原版 / 网易资源中心皮肤）。卸模型同样调用 `ResetEntityExtraSkin`，`renderReset` 恒为 `True`。

| 字段 | 说明 |
|---|---|
| `playerId` | 玩家 ID |
| `oldModelId` / `oldSkinIndex` / `oldSkinId` | 卸下前的模型（取不到时为 `None`） |
| `isLocalPlayer` | 是不是本机玩家 |
| `renderReset` | 恒为 `True` |

### 6.3 `ModelRegisteredClientEvent`

ready 之后才装载的模型（JSON 模型包迟到装载）。ready 时已注册的模型不会逐个发这个事件，请在 ready 里用 `GetModelIds()` 取全量。

| 字段 | 说明 |
|---|---|
| `modelId` | 新注册的模型 ID |

---

## 7. 服务端接口

### 7.1 玩家模型

```python
ext.GetPlayerModel(playerId)            # → {"modelId", "skinIndex", "skinId"}; 没用 YSM 模型 → None
ext.GetPlayerIsUsingYsmModel(playerId)  # → bool
ext.SetPlayerModel(playerId, modelId, skin=0, callback=None, checkPermission=False)
ext.ResetPlayerModel(playerId)          # → {"ok", "error"}
```

#### `SetPlayerModel`：让玩家换成指定模型（capability: `player_model_set`）

模型表只在客户端（模型包由客户端扫描装载），服务端组装不出模型数据：请求先发给目标玩家的客户端，由它按本机的模型表组装，再走与玩家自己在界面里选模型**同一条链路**（§5.1 可取消事件 → 写存档 → 全体客户端同步 → §5.2），所以结果是**异步**的。

```python
def StartRound(self, playerIds):
    for playerId in playerIds:
        self.ysm.SetPlayerModel(playerId, "ysm_pack:wine_fox_07_jk", skin="default", callback=self.OnModelSet)

def OnModelSet(self, result):
    if not result["ok"]:
        print("换模型失败", result["playerId"], result["error"], result["reason"])
```

参数：

| 参数 | 说明 |
|---|---|
| `playerId` | 在线玩家 |
| `modelId` | 模型 ID（`entityIdentifier`） |
| `skin` | 皮肤序号（int，0 = default）或皮肤键（str，如 `"default"`） |
| `callback` | 可选，`callback(result)` 在结果出来时调用 |
| `checkPermission` | `True` = 像玩家自己选模型那样检查模型使用权限；缺省不查（服务端模组说了算） |

同步返回 `{"ok": bool, "error": str | None, "requestId": str | None}`，参数错误码：

| error | 含义 |
|---|---|
| `invalid_player` | 玩家不在线 |
| `invalid_model_id` | 模型 ID 为空或不是字符串 |
| `invalid_skin` | `skin` 既不是非负整数也不是非空字符串 |
| `invalid_callback` | `callback` 不可调用 |

回调结果 `result`：

| 字段 | 说明 |
|---|---|
| `ok` | 是否换成功（或本来就是这个模型的这个皮肤） |
| `error` | 失败原因，成功为 `None` |
| `reason` | `cancelled` 时监听方给的原因 |
| `requestId` / `playerId` / `modelId` / `skin` | 请求原样带回 |

| error | 含义 |
|---|---|
| `model_not_found` | 目标玩家的客户端没装这个模型 |
| `skin_not_found` | 模型没有这个皮肤 |
| `permission_denied` | `checkPermission=True` 且玩家没有该模型的使用权限 |
| `cancelled` | 被 §5.1 的监听方取消，`reason` 是原因 |
| `timeout` | 10 秒内没有结果（例如玩家还在加载；客户端等模型表就绪最多 8 秒，等不到就先报这个） |
| `player_left` | 结果回来之前玩家退出了 |

- 接口发起的切换被拒时**不会**在玩家的界面上弹提示（玩家什么都没点），只回调请求方。
- 服务端对回传的数据做了校验（调用者、模型、皮肤都要对得上请求），客户端伪造请求号拿不到"跳过权限"。
- 玩家刚进世界、客户端还没初始化完模型表时，客户端会等它初始化完再组装（仍受 10 秒超时约束）。
- 一个请求只会有一个结果：已经报过 `timeout` 等失败的请求，客户端之后才交回的数据会被丢弃，不会再换模型，也不会被当成玩家自己的选择。

#### `ResetPlayerModel`：恢复原版 / 网易皮肤（capability: `player_model_reset`）

立即生效，发 §5.3 事件（`source` 为 `api`）。返回 `{"ok": bool, "error": str | None}`，`error` 取值 `invalid_player`；玩家本来就没用 YSM 模型也返回 `ok`（不发事件）。

### 7.2 实体模型渲染（capability: `entity_model_render`）

给任意实体（女仆、NPC……）套 YSM 模型，自动同步到所有客户端并随实体存档：

```python
ext.SetEntityModelRender(entityId, modelId, skinId)   # skinId 是皮肤键, 如 "default"
ext.ResetEntityModelRender(entityId)
ext.GetEntityModelData(entityId)                       # → {"modelId", "skinId"} 或 None
```

### 7.3 模型选择界面（capability: `model_select_screen`）

```python
ext.OpenModelSelectScreen(playerId, callbackFunc, customData=None, generalFormat=False)
```

给玩家打开模型选择界面，选定后在**服务端**调 `callbackFunc`。`generalFormat=False` 时回调收到 `{"customData", "modelId", "skinId"}`；`True` 时收到 `(模型渲染数据, molang 初始化表)`，渲染数据里另带 `customData`。

返回 `{"ok": bool, "error": str | None, "requestId": str | None}`，`error` 取值 `invalid_player` / `invalid_callback`。`customData` 留在服务端、原样放进回调数据（不经客户端往返，客户端改不了）；每次打开一个请求号，结果按"调用者 + 请求号"认领，多个模组先后给同一玩家打开也各拿各的结果；回调抛异常不影响别的请求（堆栈照常打印）。

### 7.4 模型使用权限（capability: `model_permissions`）

`ext.permissions` 上是 README §3.4 列出的全部权限接口与常量（权限模式、白 / 黑名单、标签、权限等级），例如：

```python
perms = ext.permissions
perms.SetModelMode("ysm_pack:wine_fox_01_taisho_maid", perms.MODE_TAG)
perms.SetModelTags("ysm_pack:wine_fox_01_taisho_maid", ["vip"])
allowed, reason = perms.CheckModelPermission(playerId, "ysm_pack:wine_fox_01_taisho_maid")
```

只放行公开名字，访问内部实现会得到 `AttributeError`。

---

## 8. 客户端接口

```python
ext.GetModelIds()                        # 已注册的全部模型 ID(选择界面的顺序)
ext.HasModel(modelId)                    # → bool
ext.GetYsmModelDetails(modelId)          # → (模型配置 dict, molang 初始化表) 或 None
ext.GetPlayerModel(playerId)             # → {"modelId", "skinIndex", "skinId"} 或 None
ext.GetPlayerIsUsingYsmModel(playerId)   # → bool
ext.RebuildRenderPlayerModel(playerId)   # → bool, 见下
ext.OpenModelSelectScreen(callbackFunc, generalFormat=True, customData=None)  # → {"ok", "error"}
```

- `RebuildRenderPlayerModel`（capability: `player_model_rebuild`）：按玩家当前的模型数据整套重新应用渲染（变量、资源、表单回灌、重建渲染），**不清**其他模组挂的资源，发 §6.1（`reason` 为 `rebuild`，`renderReset` 为 `False`）。玩家没用 YSM 模型返回 `False`。
- `OpenModelSelectScreen`（客户端版）：本机打开选择界面，选定后在**客户端**回调，参数含义同 §7.3；`callbackFunc` 不可调用时返回 `{"ok": False, "error": "invalid_callback"}`。

---

## 9. 典型联动

### 9.1 往玩家身上挂渲染资源的模组（枪械、挂件、特效）

YSM 换模型会清掉你挂的资源（§6.1）。监听两个事件，`renderReset` 为真就重挂：

```python
def __init__(self, namespace, systemName):
    ClientSystem.__init__(self, namespace, systemName)
    self.ListenForEvent("ysm", "ExtensionApi", "PlayerModelAppliedClientEvent", self, self.OnYsmRenderChanged)
    self.ListenForEvent("ysm", "ExtensionApi", "PlayerModelClearedClientEvent", self, self.OnYsmRenderChanged)

def OnYsmRenderChanged(self, args):
    if args["renderReset"]:
        self.AddMyPlayerRender(args["playerId"])   # 重新 AddPlayerAnimation / AddPlayerRenderController ... + RebuildPlayerRender
```

Eplus 军械库就是这样接的（本机玩家重挂第一人称枪械资源，其他玩家重挂第三人称资源）。

### 9.2 商店 / 解锁类模组

在 §5.1 里拦下没解锁的模型；需要"买完直接换上"时调 `SetPlayerModel`：

```python
def OnYsmTryChangeModel(self, args):
    if not self.IsUnlocked(args["playerId"], args["modelId"]):
        args["cancel"] = True
        args["reason"] = "未解锁: 去商店看看"

def OnPurchased(self, playerId, modelId):
    self.ysm.SetPlayerModel(playerId, modelId)
```

同一个模组自己发起的 `SetPlayerModel` 也会经过 §5.1，按 `source == "api"` 区分即可放行。

### 9.3 小游戏 / 剧情：统一换模型，结束后恢复

```python
def OnRoundStart(self, playerIds):
    self.saved = dict((pid, self.ysm.GetPlayerModel(pid)) for pid in playerIds)
    for pid in playerIds:
        self.ysm.SetPlayerModel(pid, "ysm_pack:wine_fox_03_astronaut")

def OnRoundEnd(self):
    for pid, model in self.saved.items():
        if model:
            self.ysm.SetPlayerModel(pid, model["modelId"], model["skinIndex"])
        else:
            self.ysm.ResetPlayerModel(pid)
```

### 9.4 读模型表的模组（女仆等实体渲染）

ready 时用 `GetModelIds()` / `GetYsmModelDetails()` 取全量，再监听 §6.3 补上迟到装载的模型；给实体套模型用 §7.2。

---

## 10. 版本承诺

| 范围 | 承诺 |
|---|---|
| 命名空间、系统名、事件名、载荷字段、能力名、错误码 | 只增不改；加字段、加能力不动 `apiVersion` |
| facade 公共方法与属性（§4、§7、§8） | 参数可加不可改 |
| `EXTENSION_API_VERSION` | 只在破坏兼容时 +1 |
| `ysmModelCoreScripts` 下的模块路径（`api/extension/*`、`client/*`、`server/*`） | **内部实现**，随时可能重构，不要直接 import；一律经事件拿 facade |

---

## 11. 常见问题

**Q：ready 事件没收到？**
A：监听要在系统 `__init__` 里注册；namespace 是 `"ysm"`、系统名是 `"ExtensionApi"`（区分大小写）；服务端事件要在服务端系统里听，客户端事件在客户端系统里听。

**Q：我能在 ready 里存下 facade 以后再用吗？**
A：可以，facade 整局有效。

**Q：拦截切换时玩家看到什么？**
A：玩家自己在界面里选的，会在选择界面看到你写的 `reason`；接口发起的不弹提示，只进 `SetPlayerModel` 的回调。

**Q：`PlayerModelAppliedClientEvent` 为什么一次进世界来好几次？**
A：进世界时存档模型下发、属性回调补应用、引擎重建玩家渲染各会应用一次；按 `renderReset` 决定要不要重挂即可。

**Q：多人游戏里别的玩家换模型，我这边收得到吗？**
A：收得到。服务端事件在服务端发一次；客户端事件在**每个**看得到该玩家的客户端上各发一次（`isLocalPlayer` 区分是不是本机）。
