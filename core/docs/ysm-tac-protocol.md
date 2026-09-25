# YSM 枪械模组对接协议（v.tac）

YSM 在基岩版上按 **Java 版 YSM 的 TACZ 兼容逻辑**播放玩家的第三人称枪械动作：手持、瞄准、疾跑、射击、换弹、近战、下蹲、趴下，另加基岩独有的滑铲。枪械模组不用碰 YSM 的动画：只要在玩家实体上写一组 molang 结构体变量 `v.tac.*`，YSM 通过这些变量播放动画。

本文面向两类读者：

- **枪械模组开发者**：第 1～3 节。说明写哪些变量、什么时候写、怎么同步给其他玩家。
- **模型作者**：第 4～5 节。说明这些变量触发哪些动画（按枪种分），以及怎么用自己的动画替换主包默认的动画。

> Java 版 YSM 直接读 TACZ 模组本体，不走这套变量；`v.tac` 是基岩版专用的协议。旧版（网易 Python 版）YSM 已经定义过这套变量，这里沿用同一套字段名。
>
> YSM 的模型分两类：从 Java 版转换来的模型（下称 **Java 模式模型**）按第 4 节的规则播；**旧版模型**用旧版自带的枪械控制器，读的是同一套变量。枪械模组只写一套，两类模型都能用。

---

## 1. 对接清单

1. **判断 YSM 在不在**：监听 YSM 的 ready 事件 `ListenForEvent("ysm", "ExtensionApi", "ClientExtensionApiReady", ...)`（服务端 `ServerExtensionApiReady`），没装 YSM 时事件不会来，`args["extension"]` 就是 YSM 的扩展接口（[ysm-extension-api.md](ysm-extension-api.md)）。
2. **按玩家判断他在不在用 YSM 模型**：扩展接口的 `GetPlayerIsUsingYsmModel(playerId)` / `GetPlayerModel(playerId)`（两端都有）。正在用 YSM 模型的玩家，**不要再挂你自己的第三人称玩家动画或控制器**，否则会和 YSM 叠在一起。第一人称照常。
   玩家换 / 卸 YSM 模型时，YSM 会清掉**所有模组**挂在该玩家身上的渲染资源（动画、控制器、几何、渲染控制器）：监听 `PlayerModelAppliedClientEvent` / `PlayerModelClearedClientEvent`，`renderReset` 为真时把你挂的重新挂上（见 ysm-extension-api.md §6、§9.1）。
3. **在玩家实体上写 `v.tac.*`**（第 2 节）：本机写自己，服务端把同样的语句转发给附近玩家的客户端，各客户端写在"那个玩家的实体"上。
4. **新进入视野的玩家要补发电平**（第 3 节）。
5. **脉冲只写不清**：`is_fire`、`is_melee` 由 YSM 读到后清零。你们自己的第一人称控制器如果也读它们，本机第一人称时由你们清；这时 YSM 不碰本机的脉冲。
6. **第三人称特效绑在手持物骨骼上**：枪口火光、烟、弹道、手电、刀光这类跟着枪走的特效，用 `CreateBindEntityNew` 绑到玩家的 `rightitem` / `leftitem` 骨骼，位置用偏移参数给（骨骼局部坐标系，单位是格）。第三人称的枪是附着物，本来就挂在这两根骨骼上，所以换成什么模型特效都跟着枪。不要绑在自己额外挂上去的辅助几何的定位点上：玩家用 YSM 模型时你们的第三人称动画不挂（第 2 条），那副骨架一直是静止姿态，定位点会停在原地（比如枪口点落在脚边）。辅助几何里现成的定位点可以直接换算成偏移：取它相对原版手持物骨骼枢轴（右手 `[-6, 15, 1]`、左手 `[6, 15, 1]`）的差 (Δx, Δy, Δz)，偏移为 (Δx, Δy, −Δz) / 16，z 要反号。

---

## 2. 变量表

在**玩家实体**的 molang 上写（`CreateQueryVariable(playerId).EvalMolangExpression(...)`）。电平 = 状态持续期间保持；脉冲 = 事件发生时写一次。

| 变量 | 类型 | 含义 | 什么时候写 |
|---|---|---|---|
| `v.tac.gun_type` | 字符串 | 手里枪的枪种 | 切到枪时写枪种名（见下表）；收枪、切到非枪物品时写 `''` |
| `v.tac.is_aim` | 电平 | 瞄准中 | 开镜写大于 0 的值，关镜写 0 |
| `v.tac.is_reload` | 电平 | 换弹中 | 开始换弹写大于 0 的值（可以用 1 战术 / 2 空仓区分，YSM 只看是否大于 0），结束写 0 |
| `v.tac.is_fire` | 脉冲 | 开了一枪 | 每发子弹写 `1`；拉栓写 `2`（YSM 不算开火）。不用清 |
| `v.tac.is_melee` | 脉冲 | 枪托 / 刺刀近战 | 每次近战写大于 0 的值。不用清 |
| `v.tac.is_draw` | 电平（可选） | 拔枪中 | 拔枪动作期间写 1，结束写 0。不写时 YSM 在切到枪之后自己算 0.5 秒 |
| `v.tac.is_crawling` | 电平 | 趴下 | 趴下写 1，起身写 0 |
| `v.tac.slide` | 电平 | 滑铲 | 滑铲期间写 1，结束写 0 |
| `v.tac.is_sneaking` | 电平（可选） | 下蹲 | **只有下蹲不走原版潜行时才写**。走原版潜行（玩家处于 sneaking）的不用写 |
| `v.tac.gun_id` | 字符串（预留） | 枪的 ID | Java 版逐枪动画的数据源，YSM 暂未读取 |
| `v.tac.peek_type` | 数值（旧版） | 探头：-1 右 / 1 左 / 0 无 | 只有旧版模型读；Java 模式的模型不读 |

**枪种取值**（与 TACZ 的枪械分类同名）：

| `gun_type` | Java 模式模型选动画时归到 | 旧版模型 |
|---|---|---|
| `pistol` | `pistol` | 手枪 |
| `rpg` | `rpg` | 火箭筒 |
| `rifle` `sniper` `shotgun` `smg` `mg` 以及其它任何非空值 | `rifle` | 各自一类 |

**不需要写变量的动作**：

- **疾跑**：读原版疾跑。战术冲刺这类强化疾跑，只要玩家处于原版疾跑，就按疾跑播。
- **下蹲**：读原版潜行。下蹲不走原版潜行时才写 `is_sneaking`。

**写法示例**：

```python
comp = clientApi.GetEngineCompFactory().CreateQueryVariable(playerId)
comp.EvalMolangExpression("v.tac.gun_type='rifle';")           # 切到步枪
comp.EvalMolangExpression("v.tac.is_aim=1;")                    # 开镜
comp.EvalMolangExpression("v.tac.is_fire=1;")                   # 开一枪
comp.EvalMolangExpression("v.tac.is_crawling=1;v.tac.slide=0;") # 趴下
```

注意：每条语句以 `;` 结尾；字符串用单引号；不要把 `v.tac.xxx` 放在 `??` 左边（引擎不认）。

---

## 3. 同步要求

YSM 在每个客户端上读"那个玩家的实体"上的值，所以**每个客户端上都要写**：

| 场景 | 做法 |
|---|---|
| 本机玩家 | 本机 `EvalMolangExpression` 写自己（开火脉冲同样要写：第三人称 F5 看自己时 YSM 靠它播开火动作。YSM 第一人称不读 `is_fire`，模组自己的第一人称控制器也读它的话，可以只在第三人称写） |
| 其他玩家看到你 | 服务端把同样的语句转发给附近玩家，各客户端写在你的实体上 |
| 新进入视野 | 客户端收到 `AddPlayerCreatedClientEvent`（别人的实体刚在本机创建）时，向服务端要一次对方当前的**电平**，写在对方实体上。需要补的：`gun_type`、`is_aim`、`is_reload`、`is_crawling`、`slide`，以及写了的 `is_sneaking`、`is_draw` |
| YSM 切换 / 重载模型 | 不用管。YSM 应用模型时会重新初始化 `v.tac.*`，但会先保存已写好的电平，初始化完再写回 |

- 脉冲不用补发，过去的开火对新看到你的人没有意义。
- 电平只在变化时写即可，不必每 tick 写。
- 一个 tick 内连开多枪只算一枪。YSM 每 tick 读一次、清一次；射速超过 30 发/秒时会少计几发。

---

## 4. 动作 → 动画（按枪种）

YSM 照 Java 版分三个通道播，互不打断：

1. **主链（全身）**：玩家当前的主状态（站立、走、跑、潜行、趴下……）。拿着枪时把状态 `X` 的动画换成 `tac:X`（模型或主包默认有这个动画才换）。骑乘时不换。
2. **手持通道（手臂，循环）**：拿着枪时一直播，按优先级取一个：趴着移动 > 趴着不动 > 滑铲（有动画才算） > 瞄准 > 在地疾跑 > 持枪。
3. **开火通道（一次性）**：每次开火、近战、换弹开始都从头播。优先级：换弹 > 近战 > 趴着不动开火 > 瞄准开火 > 持枪开火。挥手或使用物品时停。

下表的 `<枪种>` 取 `rifle` / `pistol` / `rpg`（归类见第 2 节）：

| 动作 | 枪械模组写 | 主链（全身） | 手持通道（手臂） | 开火通道 |
|---|---|---|---|---|
| 持枪站立 / 走 | `gun_type` | `tac:idle` / `tac:walk` | `tac:hold:<枪种>` | — |
| 疾跑 | （原版疾跑） | `tac:run` | `tac:run:<枪种>` | — |
| 瞄准 | `is_aim` | 随状态 | `tac:aim:<枪种>` | — |
| 射击 | `is_fire=1` | 随状态 | 随状态 | `tac:hold:fire:<枪种>`；瞄准时 `tac:aim:fire:<枪种>`；趴着不动时 `tac:climbing:fire:<枪种>` |
| 换弹 | `is_reload` | 随状态 | 随状态 | `tac:reload:<枪种>` |
| 近战 | `is_melee` | 随状态 | 随状态 | `tac:melee:<枪种>` |
| 下蹲 | （原版潜行）或 `is_sneaking` | `tac:sneak`（移动）/ `tac:sneaking`（不动） | `tac:hold:<枪种>` / `tac:aim:<枪种>` | 同上 |
| 趴下 | `is_crawling` | `tac:climb`（移动）/ `tac:climbing`（不动） | `tac:climb:<枪种>` / `tac:climbing:<枪种>` | 趴着不动开火 `tac:climbing:fire:<枪种>` |
| 滑铲（基岩扩展） | `slide` | `tac:slide` | `tac:slide:<枪种>`；模型没有时用持枪 / 瞄准，不按疾跑 | 同持枪开火 / 瞄准开火 |
| 拔枪 | （自动）或 `is_draw` | — | — | —（只给作者的 molang `ctrl.tac_is_draw` 读） |

- **没拿枪时**姿态照样生效：主链播不带 `tac:` 的原状态动画（`sneak`、`climb`、`slide`……），手持和开火通道不播。
- **主链的持枪版**不限于上表：主链任何状态（`jump`、`fly`、`swim`、`attacked`……）都可以有 `tac:<状态名>`，有就换，没有就播原状态。
- **滑铲是基岩扩展**：Java 版没有滑铲。它在主链里排在趴下前面，模型和主包都没有滑铲动画时，按其它状态照常走。

---

## 5. 模型作者：自定义与回落

- 枪械动画放在模型的 `tac` 槽位：`ysm.json` 的 `files.player.animation.tac` 指向的动画文件，动画名用上表的 Java 写法（`tac:hold:rifle`）。转换器会转成引擎允许的写法（`tac.cls.hold.rifle`），两者等价。
- **按动画名逐个回落**：模型有这个名字就用模型的，没有就用主包默认。主包默认是 Java 版默认模型的枪械动画（3 条主链 + 15 条手持 + 15 条开火，逐枪动画与手雷除外），外加滑铲的两条（`slide`、`tac:slide`，取自旧版的枪械兼容动画，姿态写在滑铲姿态骨骼上，见下一条）。与 Java 版"缺的动画回落默认模型"是同一口径。
- **只补你想改的**：只写了 `tac:hold:pistol` 的模型，拿手枪时用自己的持枪动画，其余动作（瞄准、射击、换弹……）仍是主包默认。
- **自己写滑铲时，整身姿态别放在 `AllBody` / `UpperBody` 上**：手持和开火通道的动画会覆盖它们（步枪的手持、瞄准、开火、换弹和全部近战都写 `AllBody`，近战与部分模型的持枪还写 `UpperBody`），放在这两根上的躺倒、抬上身会被清掉，拿枪滑铲时人就扑倒了。整身朝向写在 `Root`，或写在转换器给主模型补的滑铲姿态骨骼 `ysm_body_root`（套在 `AllBody` 外面）/ `ysm_torso_root`（套在 `UpperBody` 外面）上——主包默认的滑铲就是这么写的，除了滑铲没有动画会动这两根。
- **循环方式由通道决定**，文件里写的 `loop` 不算：手持通道循环；开火通道只播一遍，播完就收回（回到空闲，同 Java 的 PLAY_ONCE）；主链随状态。
- **转换器会跳过**这些枪械动画：逐枪动画（名字带 `$`）、Java 版不播的枪种后缀（如 `minigun`）、手雷，以及别的模组的动画。
- **作者 molang 可读**：`ctrl.tac_hold_gun`、`ctrl.tac_gun_type`、`ctrl.tac_is_fire`、`ctrl.tac_is_aim`、`ctrl.tac_is_reload`、`ctrl.tac_is_melee`、`ctrl.tac_is_draw`。`ctrl.tac_gun_id`、`ctrl.tac_fire_mode` 恒为空串（YSM 暂未读取协议里的 `gun_id`；射击模式协议里没有这一项）。

---

## 6. 暂不支持与已知差异

| 项目 | 现状 |
|---|---|
| 逐枪动画 `tac:hold$tacz:ak47` | 不支持：YSM 暂未读取协议里预留的 `gun_id` |
| `ctrl.tac_fire_mode`（射击模式） | 恒为空串，协议里没有这一项 |
| 手雷 `tac:mainhand:grenade` | 不支持（Java 版也未完成） |
| 探头 `peek_type` | 只有旧版模型支持，Java 版没有探头 |
| 开火 / 近战的持续时间 | Java 版读 TACZ 的射击冷却；基岩按脉冲计，`ctrl.tac_is_fire` 持续 0.15 秒，`ctrl.tac_is_melee` 0.5 秒，拔枪 0.5 秒 |
| 滑铲 | 基岩扩展，Java 版没有对应动画 |

---

## 7. 自测

- **看 YSM 读到的值**：游戏内输入 `/ysm_debug add query.mod.ysm_tac_hold_gun`，调试面板会实时显示。可看的量：`query.mod.ysm_tac_hold_gun`（拿枪 1）、`ysm_tac_gun_kind`（1 步枪 / 2 手枪 / 3 火箭筒）、`ysm_tac_is_aim`、`ysm_tac_is_reload`、`ysm_tac_is_crawling`、`ysm_tac_is_slide`、`ysm_tac_is_sneaking`、`ysm_tac_action_serial`（开火、近战、换弹开始各加 1）。全部名字前缀 `query.mod.`；`/ysm_debug clear` 清空。
- **只有 Java 模式模型的玩家走这套逻辑**（转换过来的 Java 模型）。旧版模型用旧版的枪械控制器，同样读 `v.tac.*`。
- **多人**：让另一名玩家从远处走进视野，看他手里的枪、姿态有没有立刻对上（验证第 3 节的补发）。

---

## 附：参考实现（Eplus军械库）

Eplus军械库（EP_Gun_0）按本协议对接，供其它枪械模组参考：

- **姿态**：`modCommon/modConfig.YsmTacPostureMolang(moveState)` 生成 `v.tac.is_crawling` / `v.tac.slide`。凡是写移动姿态的地方都同时写它：本机、服务端纠正、远端姿态同步。新进视野的玩家经原有的姿态同步补发。
- **瞄准 / 换弹电平**：写入点分散，大多只写本机。`uiScript/epGun._SyncYsmTacLevels` 每 tick 读本机的值，变了才经 `SetPlayerListMolang_2` 广播。
- **补发**：服务端 `_CacheThirdTacState` 从转发的语句里记下 `gun_type` / `is_aim` / `is_reload`，客户端请求 `RequestPlayerThirdMolangStates` 时随 `tacStates` 一起下发。
- **近战**：枪托近战 `v.tac.is_melee=1` 本机写的同时广播。
- **第三人称特效挂点**：`modCommon/thirdAnchor` 把原来的辅助几何 `ep_third_parts` 的定位点（枪口 `ep_third_fire` / `ep_third_fire2`、手电 `third_flash_*`、刀光 `ep_third_knife_*`）换算成手持物骨骼加偏移，所有绑定处统一调 `BindThirdAnchorParticle`。开火广播和武器数据里仍然写定位点名，由各客户端绑定时换算，不用改协议。
- **YSM 换 / 卸模型后重挂渲染**：`EpJxkScriptClientSystem.OnYsmPlayerModelApplied` / `OnYsmPlayerModelCleared` 在 `renderReset` 为真时重挂。
  其他玩家只重挂第三人称资源（`_AddThirdPlayerRender`：不重跑 molang 默认值，那会把 `v.tac.*` 电平清零）；本机再补公共材质，并按手上物品重挂第一人称枪械资源（`Rest_cut_gun`）。
