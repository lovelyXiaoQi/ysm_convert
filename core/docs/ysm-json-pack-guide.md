# YSM 网易版 JSON 模型包制作指南

自本版本起，YSM 模型副包**不再需要任何 Python 代码**——副包里放资源文件和一份
`ysm.json`，主包启动时自动扫描注册。`ysm.json` **直接兼容 Java 版 spec 2 原生格式**
（`metadata`/`properties`/`files` 原样可用），网易特有内容写**顶级扩展字段**覆盖
（`netease` 段是兼容形态）。移植产物的 `ysm.json` 只有 `spec`/`metadata`/`properties`/
`files`，外加 molang `??` 默认值收敛出的顶级 `initialize` —— Java 模式与并行通道接管
都由主包自动判定，创作者只管增删自己的动画与控制器。

Java 版字段参考：本仓库 `.ref/ysm-java-wiki/docs/notes/wiki/`（项目结构篇）。

## 从 Java 版模型包迁移（核心场景）

`ysm.json` **原样拷贝**，创作者只需完成资源侧的机械转换：

| # | 转换项 | 规则 |
|---|---|---|
| 1 | **动画键加前缀** | Java 动画文件内的键是 `idle`/`gui`/`walk` 等短名，基岩动画是全局命名会直接报错。全部改为 `animation.<包名>.<原键名>`（**只加前缀，不改键名**——`use_mainhand` 等键名差异由主包自动映射）。动画文件**只放资源包一份**即可：`files.player.animation` 声明的路径按**文件**解析（该文件内所有动画自动注册进模型，短键=ID 去前两段），无需手写 `animations_extra`，也无需任何行为包副本 |
| 2 | **贴图放置** | `resource_pack/textures/entity/<包名>/<原文件名>.png`（保持 Java 原文件名）。Java 的 `files.player.texture` 写的是**包内相对路径**（如 `textures/skin.png`），主包只取文件名按约定拼装；想放到别的目录就用 `netease.texture_path_prefix` 换前缀，或用 `netease.textures` 逐皮肤写死完整基岩路径 |
| 3 | **几何体命名** | 模型 json 内几何体标识改为 `geometry.<包名>`，放 `resource_pack/models/entity/` |
| 4 | **预览实体定义** | 新增 `resource_pack/entity/<包名>.entity.json`（模板见下），`identifier` = `ysm_pack:<包名>` |

`<包名>` = `ysm_models/` 下的模型包文件夹名。**请带作者前缀起长名防冲突**（如
`zuozhe_wine_fox`），与 Java 版转换教程的建议一致。

## 资源 ID 命名规范（全局路由，防冲突红线）

基岩版所有资源引用都是全局 ID，Java 版的"包内路径"必须转换：

| 资源 | Java 版写法 | 网易版 ID 约定 |
|---|---|---|
| 模型 ID | 文件夹名即 ID | `ysm_pack:<包名>`（`netease.model_id` 可覆盖） |
| 几何体 | `models/main.json` 路径 | `geometry.<包名>` |
| 动画 | 文件内短名 `idle` | `animation.<包名>.idle` |
| 动画控制器 | `controller/xxx.json` 路径 | `controller.animation.<包名>.<名称>` |
| 贴图 | `textures/skin.png` 路径 | `textures/entity/<包名>/skin` 路径引用 |

## 副包目录结构（模型声明放行为包，资源放资源包，均无需任何 Python 代码）

```
你的副包组件/
├── behavior_pack/
│   └── ysm_models/
│       └── <包名>/
│           └── ysm.json               ← 只有声明文件(Java 版原样 + 可选 netease 段)
└── resource_pack/
    ├── models/entity/<包名>/*.json    ← geometry.<包名> / geometry.<包名>_arm
    ├── animations/<包名>/*.json       ← animation.<包名>.* (资源只此一份)
    ├── animation_controllers/<包名>/  ← controller.animation.<包名>.*
    ├── textures/entity/<包名>/*.png
    └── entity/<包名>.entity.json      ← 预览实体定义
```

> **资源只放资源包一份**：主包在启动时扫描资源包磁盘目录，按命名空间取得该模型的
> 全部动画 ID、控制器 ID、molang 变量引用与几何缩放声明——因此改了资源包不存在
> "忘了同步另一份"的问题。（历史版本要求在行为包放一份动画副本，现已不需要；
> 旧包里已有的副本仍可用，作为资源索引不可用时的回落。）

一个组件可放任意多个 `<包名>/`，**无需清单文件**——主包直接枚举目录发现。Java
合集包形态（`ysm_models/<合集>/<子包>/ysm.json`）同样自动命中，包名取直接父目录名。

**为什么声明文件在行为包**：客户端脚本能拿到引擎给出的"已挂载行为包磁盘路径"
（`addonPaths`）并用标准文件接口枚举读取，而资源包侧没有任何可用的客户端读取
能力（详见文末实现说明）。声明只是一张资源 ID 清单，真正的动画/贴图/几何体仍在
资源包里由引擎按 ID 加载。放行为包还有个好处：玩家**未启用**的组件不会被误扫描。

模型注册在客户端脚本 import 期即完成（早于一切消费），无服务端参与、无网络同步。

## 自动推导规则（netease 段全部可选）

| 内容 | 推导来源 | netease 覆盖字段 |
|---|---|---|
| 模型 ID | `ysm_pack:<包名>` | `model_id` |
| 显示名/描述/作者 | `metadata.name` / `tips` / `authors` | — |
| 几何体 | `geometry.<包名>`；`files.player.model.main`/`arm` 写 `geometry.*` 资源 ID 时**直通**（共享/改名几何场景，Java 包内路径不可能是该形态） | `geometry`（最高优先） |
| 动画命名空间 | `animation.<包名>` | `animation_namespace` |
| 皮肤列表 | `files.player.texture` **列表混排**：字符串/`{uv}`（Java 原生）皮肤名=文件名去扩展、`properties.default_texture` 指定者（缺省首项）作为网易 `default` 皮肤；`{"皮肤名": "真实路径"}`（基岩扩展，不含 `uv` 键）= 皮肤名显式 + 路径直通（皮肤名与文件名不一致/共享贴图场景，同名覆盖推导值）。非 default 皮肤自动继承 default 资源 | `textures`（兼容简式）+ `skin_sort` / `texture_path_prefix` |
| GUI 展示动画 | `properties.preview_animation`（字符串）→ `animation.<包名>.<该值>`；Java 的 GUI 实体**强制循环**播它（`CapPredicate` → `playLoopAnimation`，无视动画文件的 `loop`），移植工具把只在 GUI 播放的展示动画补 `loop: true`（`LoopPreviewAnimation`；主链成员/轮盘/控制器引用的不改） | `gui_animation` |
| 玩家缩放 | `properties.height_scale`（Java 渲染缩放）按官方默认基准换算：×0.8⁄0.7（Java 0.7 ↔ 网易 0.8）；`width_scale` 不等时告警并按 height 取值；properties 为缺省 0.7 时**回读几何文件** description 内的 `ysm_height_scale`/`ysm_width_scale`（资源包里那份即可，Java RawModelAssembler 同语义） | `player_scale` |
| 选择界面图标 | `properties.icon` 取文件名 → `textures/entity/<包名>/<文件名>` | `icon` |
| 选择界面卡片取景 | `properties.disable_preview_rotation`：缺省（false）= Java 的缺省取景（模型转过 20°、略俯视），`true` = 正对镜头；非 Java 模式的包缺省正对 | `disable_preview_rotation` |
| 选择界面卡片视口 | Java 模式包自动 `gui_card_framing: "java"`：卡片按 Java 卡片比例（52x90，上方 52x70 为模型视口）渲染，每模型单位的大小、脚底位置与 Java 一致，展示动画里的舞台骨骼（边框、幕布）对齐卡片边；`gui_scale` 仍会乘上去（绕模型 y=24 缩放，会让舞台偏离卡片边）。**皮肤卡片**同一开关：按 Java 皮肤卡片（`CatalogTextureButton` 54x102，模型视口 54x82，缩放 35，恒带 20° 转角与俯视）的构图等比缩到 55x80 卡片，播预览动作表第 0 项 idle、不播展示动画（舞台骨骼保持隐藏） | `gui_card_framing`（`"legacy"` = 旧版取景） |
| 选择界面卡片背景/前景图 | `properties.gui_background` / `gui_foreground` 取文件名 → `textures/entity/<包名>/<文件名>`（移植工具统一成 `gui_background.png` / `gui_foreground.png`） | `gui_background` / `gui_foreground` |
| 弹射物/载具替换 | `files.projectiles` / `files.vehicles`（对象或数组形态均可），以及 Java 废弃字段 `files.arrow`（按 `minecraft:arrow` 弹射物解析，排在 projectiles 之后；同一实体被多个条目命中时取声明序第一个）：实体 ID 自动映射 Java↔基岩差异（`minecraft:trident`→`minecraft:thrown_trident` 等）；几何体推导为 `geometry.<包名>_<模型段>`（模型段 = model 文件基名**转小写、非 `[a-z0-9_]` 折成下划线**，`GMA_T.50.json` → `gma_t_50`）、贴图 `textures/entity/<包名>/<文件名>`——**共享资源直通**：`model` 写 `geometry.*` 资源 ID、`texture` 写无扩展名的真实引用路径时不做推导原样使用（多模型共用一套箭矢的场景，内置指挥官系即此写法）；动画命名空间默认 `<包名>_<模型段>`，**同一模型在多个条目里配了不同动画文件**时带上动画文件段（`<包名>_<模型段>_<动画段>`，如酒狐的马/骡子共用 foxcar）；`animation`/`controller` 声明按**路径解析模式**注册（与玩家侧同规则：资源包文件精确命中 → 文件内全部注册，BP 副本回落）。**播放按 Java 通道语义**：只有谓词状态键与并行键直接播——弹射物 `water`/`fire`/`ground`/`air` 按水>火>地>空互斥（Java 主控制器只在 `water`/`ground`/`fly`/`fire` 任一存在时才建：`fly` 本身从不播、只有 `air` 的包 `air` 也不播）；载具 `water`/`ground`/`fly` + `forward`/`idle`（速度阈值 1.0 m/s ≈ Java 0.05 格/tick）+ `has_ride`/`not_ride` 按 `query.has_rider` 互斥（对齐 Java：替换会留在空载具上）；`parallel0-7`（载具另有 `pre_parallel0-7`）常开；**其余动画只注册、不直播**（Java 没有通道播它们，只能被控制器引用）；控制器常开，**按名接管同名通道**（对齐 Java `HybridAnimationController`：`projectile.parallel_N` 接管 `parallelN`、`projectile.main`/`vehicle.main`/`vehicle.move`/`vehicle.ride` 接管对应谓词状态，状态里引用的 `parallelN` 同样不再直挂——基岩逐通道相加，两路驱动会翻倍），**没被接管的谓词状态与并行键照常直挂**，animate 按 Java 通道序排；移植工具把控制器落在 `animation_controllers/<包名>/replace_entities/<命名空间段>.animation_controllers.json`（与玩家侧作者控制器隔开，声明路径同步改名）。弹射物生成时替换；载具按 Java 语义在**第一乘客**上车时写入，下车不撤、随实体存档（空载具播 `not_ride`） | `replace_entities` 逐实体 ID 覆盖（字段级浅合并，**通常无需配置**——推导+直通+路径解析已覆盖常见场景）；箭矢亦可用旧 `arrow` 字段 |
| molang 变量初始化 | **对齐 Java 的"变量零声明"**：Java molang 未定义变量读取缺省为 0，模型包无需声明；基岩引擎必须显式初始化，主包自动完成——① `config_forms` 的 `value` 变量（Java 未定义变量读 0：取 0 夹进 range 的 `[min,max]`，位置类滑条居中、`[1,1.6]` 的大小滑条得 1；checkbox/radio 为 0）；② **该模型命名空间下动画/控制器文件里扫描到的全部 `v.*` 引用**（资源包那份，排除主包基线已初始化者），随模型注册并入全局初始化表；装载时若同名变量已被其他包（旧版 py 副包/先装的 JSON 包）初始化，自动补 0 让位，不会踩掉对方的显式初值。**每渲染实例的初始化**：世界实体经接口 `SetPlayerVariable` 求值全表；纸娃娃等独立实例靠每包一份的变量初始化控制器 `controller.animation.<包名>.ysm_variable_init`（移植/修复工具生成，`on_entry` 逐条 `v.x = v.x ?? 默认值;`，主包在资源索引发现即以保留键恒开注册，见下文"纸娃娃是玩家的独立渲染实例"） | `initialize`：`["v.自定义变量", "variable.x = 1.5;"]` 显式声明优先（裸变量名展开为 `= 0.0`，表达式原样） |
| 轮盘（含高级项） | `properties.extra_animation` **与 Java 一格一条目**（格序 = 声明序，每页 8 格）：普通项播放动画（值为空时显示条目序号）；`#分类id` 键→进入子轮盘（`extra_animation_classify` 按引用翻译，最多套 5 层，同 Java）；`#return` 键→占一格，返回上一级、根级关闭界面；`"键": "#按钮id"` 值→一格表单页（Java 同一格外圈播动画、内圈开表单，基岩轮盘分不出内外圈：点格子打开右侧表单页，页顶"播放动画"按钮在该键确有动画时出现；按钮没有表单就直接是播放格）。`config_forms` 的 range/checkbox/radio 与 Java 同 schema，**radio 的 `labels` 解析期转成有序二元组列表**（dict 经 ModAttr 同步会丢序，而选项序号就是变量值）。表单取值对齐 Java：滑条按 `step` 取整并夹进 `[min,max]`、显示最多两位小数；开关写 1/0；单选点选执行该项语句（缺结尾 `;` 自动补）后整页按变量现值刷新。**表单设置按模型存档**（ModAttr `extraVariable` = `{"models": {模型ID: {"v.x": 值}}}`，同步给其他玩家；切回模型恢复上次的设置，旧版全局表首次写入时并入当前模型）。轮盘用到的非标准动画键自动注册进动画表。**停止条件由系统维护**：播放指令自动附加"移动/跳跃/潜行时停止"（Java 版无此概念，创作者零感知）；`query.mod.ysm_wheel_anim` 在轮盘动画开播时置 1，移动/动作时清 0，只播一遍的动画到时长清 0（对齐 Java `ctrl.playing_extra_animation`） | `extra_stop_expression` 换条件 / 置空关闭；`skins` 覆盖 |
| 标准动画注册 | 23 个标准键 × 动画命名空间 + 全套共享基线（战斗/物理/CarryOn/TACZ），键名差异自动映射：`use_righthand`→`use_mainhand`、`use_lefthand`→`use_offhand`；轮盘 extra 键**按 `properties.extra_animation` 声明动态注册**（不再盲注册 extra0-7） | `animations_extra` 覆盖/追加 |
| 全量动画注册（**路径解析模式**） | `files.player.animation` 两种形态。**列表混排（推荐）**：`["路径", {"短名": "动画ID", ...}, ...]`——字符串按路径解析导入**整个文件的所有动画**（对齐 Java 按文件加载，短键 = `animation.xxx.abc.def` 去掉前两段，注册顺序 = 书写顺序），dict 逐条注册单个动画（跨命名空间共享动画/键名映射场景；值留空/`null` 按约定展开为 `animation.<包名>.<键>`），同键后写覆盖。**Java 原生 dict**：`{"main": 路径, "arm": 路径, ...}` 语义槽位（`arm`/`fp_arm` 走第一人称手臂通道）。路径匹配规则：① 写资源包真实相对路径 → 精确命中；② Java 原包路径（如 `animations/main.animation.json`）→ 按文件名匹配 + **命名空间校验**（候选文件必须含 `animation.<包名>.*` 的 ID，防止捡走别家的同名文件），多候选经命名空间裁决唯一才采信；③ 未命中的声明明确告警（不静默）。资源只需放资源包一份，未命中时回落行为包副本（历史兼容）。未声明该字段的包只注册标准键（行为不变）。**轮盘动画键不覆盖已显式声明的键** | `animations_extra`（兼容形态，等价于列表里的 dict 条目） |
| 自定义控制器注册 | `files.player.animation_controllers` 列表混排：`["路径", {"注册键": "控制器ID", ...}, ...]`——路径按解析模式导入文件内全部控制器（注册键取 ID 末段），dict 逐条注册（**显式键名**，共享控制器/键名兼容场景）。播放条件统一为**纸娃娃+第一人称双门**（`!v.is_paperdoll && !v.is_first_person`，替代 Java"加载即常开"的裸 `1`；保留键 `ysm_variable_init` 的每实例变量初始化控制器恒开，不经此门）：控制器转移普遍含物品/骑乘 query，纸娃娃是无装备上下文的 Custom 实体会逐帧刷错；基岩 FP 手臂与主模型同实体同骨骼名，主域控制器在第一人称会驱动手臂几何乱摆（Java 的 FP/GUI 是独立渲染域，无此二患——**与 Java 版的行为差异点**）。条件用 `netease.animate_extra` 同键覆盖 | `animation_controllers_extra`（兼容形态） |
| **渲染通道** | JSON 包模型贴图写入原版 `default` 键，几何写入独占的 `ysm` 键（第三人称主体 `ysm_main`）与 `arm` 键（第一人称 `first_person_ysm_arm`），两者都读 `Texture.default`。**`default` 几何键只作附着物/护甲的绑定锚点**：第三人称装模型几何，第一人称换成原版体型 `geometry.default_steve`（装载时按当前视角初始化，切视角时切换，见 `playerRender.ApplyItemAnchorGeometry`），原版第一人称动画原样驱动它——弓/弩/盾/三叉戟等附着物与普通手持物都落在原版位置（对齐 CSM 的 `csm_default`/`default` 分工）。卸下模型时引擎 **`ResetEntityExtraSkin`** 一次性清除全部 ActorRender 附加资源与操作历史，自动恢复原版/网易资源中心皮肤（**含官方 4D 皮肤**）——`default` 键可放心顶替，无需键隔离与手工回填；引用 `Texture.default` 的原版系控制器（护甲纹饰等）也能正确取到模型贴图，自定义 `arm` 模型无需关心专有键名 | `render_controllers_extra` |
| **第一人称手臂** | 所有 JSON 包模型第一人称统一渲染 `arm` 几何键：`files.player.model.arm` 声明时指向独立手臂几何 `geometry.<包名>_arm`，未声明时回落主几何（等效旧剔除法）。⚠️ 独立手臂几何由移植工具**重建成"原版手臂骨架包装骨骼 + 移位后的 Java 子树"**（`body[0,24,0]` → `rightArm[-5,22,0]` → Java 右臂子树整体移位 `(-4,-4.8,0)`；左臂子树挂唯一名包装 `ysm_fp_leftarm` 并由主包动画 `scale 0` 隐藏）：基岩没有引擎级第一人称手臂变换，摆位全靠原版 `base_pose`（写 `body`）+ `empty_hand`（写 `rightarm`），而 Java 是把 arm 几何刚性放进原版手臂的姿态栈（换算 = 上述 `∓4/-4.8` 移位）。**早先的"嫁接主几何父链"已撤**——父链在第一人称是静态的，补不出视角俯仰（旧产物 25/25 个包都没有 `body` 骨骼，抬头 30° 手臂就掉出画面）。声明了 `model.arm` 的包渲染控制器 `part_visibility` **全放行**（Java 渲染整个 `RightArm` 子树，按名字前缀过滤会掉件）；未声明的包回落主几何、仍走白名单版 `first_person_ysm_arm_main`。动画声明键 `fp_arm`（并行 parallel0-7）与 `arm`（手持条件动画）走 `animation.<包名>_arm.*` 命名空间，注册短键加 `fp_` 前缀并自动叠加 FP 门控。**空手门**：`first_person_ysm_arm` 控制器与全部 FP 域自动条目（`fp_parallel*`/甲槽/`ysm_fp_swing`）都带 `!query.is_item_equipped(0)`——手持物品时模型手臂不渲染，且 `rightarm`/`leftarm` 等骨骼名与附着物锚点共用（引擎匹配不分大小写），再播只会把物品带偏；手写 `animate_extra` 里作用于这些骨骼的第一人称动画请自带同一个门，动 `LeftArm` 会挪副手物品，尽量只动 `Right*` | `animate_extra` |
| 合集文件夹分组 | 合集形态 `ysm_models/<合集>/<子包>/ysm.json` + 合集目录下 `ysm-pack.json`（Java 原格式：`name`/`description`/`lang.zh_cn.name`）→ **自动生成模型选择界面文件夹**（显示名取 `lang.zh_cn.name` > `name` > 目录名）。成员默认**只在文件夹内显示**（等同文件夹界面"是否隐藏YSM模型"开启，对齐 Java 合集包：模型不在主列表重复平铺）；**封面**：`ysm-pack.json` 顶级扩展键 `folder_texture` 写资源包纹理路径（不带扩展名，如 `"textures/ui/ysm_packs/wine_fox"`），图片按 Java 合集封面口径画成 **52x90**（整张铺满卡片，底部约 20 px 是名字区，名字由界面叠上去），不写则用默认文件夹图（Java 原版 `default_pack_icon`）；Java 合集目录里的 `ysm-pack.png` 由移植工具自动拷进资源包 `textures/ui/ysm_packs/<合集>.png` 并写好该键。封面每次按清单刷新（游戏内不能改封面，老存档跟着换）。`description` 暂不使用；合集只认一层（`ysm_models/<合集>/<子包>`）。合集文件夹**玩家不能删除**（"删除文件夹"按钮只对玩家自建的文件夹显示）；并入存档文件夹列表是幂等增量：用户对该文件夹的改名/隐藏/成员编辑全保留，合集新增子包自动并入、卸载的剔除；根目录误放 `ysm-pack.json` 不参与分组 | —（文件夹后续可在游戏内自由编辑） |
| **条件动画** | Java 把条件编码在动画名里，本主包**自动识别并合成基岩原生 molang 播放条件**（零手写、零运行时开销）。支持前缀：`hold_mainhand`/`hold_offhand`（持有）、`swing`/`swing_offhand`（挥手）、`use_mainhand`/`use_offhand`（使用中）、`head`/`chest`/`legs`/`feet`（盔甲槽）、`vehicle`（骑乘类型）、`carryon`（搬运：`carryon:block`/`entity`/`player` 按主包 `ysm_carryon` 取值表 2/1/3 合成，`princess` 走 `ysm_riding==5`；Java 模式下 princess 由骑乘链带互斥驱动；教程 5.6 的下划线改名形态仅 Java 模式合成，旧通道由基线 `carryon_ctl` 驱动）。三种形态：物品 ID → `is_item_name_any`；tag → `equipped_item_any_tag`；分类 → 内置分类表（sword/axe/pickaxe/shovel/hoe/eat 走原版 tag，shield/bow/crossbow/fishing_rod/fishing/spear/spyglass/toot_horn/brush/drink/throwable_potion 走物品名，charged_crossbow 走 `item_is_charged`）；`empty` → 空手。**优先级与 Java 一致**（id > tag > 分类），自动合成互斥条件。模组专属分类（slashblade/gohei/lance 等）基岩无对应，汇总告警一次。**一次性动画（挥击 `swing*`、受击 `attacked`、死亡 `death`）由生成式状态机驱动**：基岩直挂 animate 条目对不循环的定时动画不重放（条件再次成立只是再应用，动画时钟不回零——实机第三人称挥手只播第一次），移植/修复工具按包生成 `animation_controllers/<包名>/ysm_oneshot.json`（`ysm_swing`/`ysm_fp_swing`/`ysm_attacked`/`ysm_death`：idle → 每成员独占状态、播完 `all_animations_finished` 才走 → cooldown 等触发消失 → idle，进入状态即重置时钟；转移按 Java 优先级 id > tag > 分类 > 兜底排列），主包在资源索引发现即替换对应直挂条目（位置、双门不变），旧产物无此文件时维持直挂。副手挥击族（`swing_offhand*`）不进状态机——基岩无副手挥击信号。⚠️ **动画名必须按下方规则转义** | `animate_extra` 同键覆盖 |
| 死亡/爬梯状态 | 模型自己提供了 `death` / `ladder_up` / `ladder_stillness` / `ladder_down` 动画时自动补播放条件（判据与 Java `AnimationRegister` 一致：死亡用 `death_ticks`，爬梯用主包下发的 `ysm_is_on_ladder` + `ysm_climbing_vector` 正负零）。未提供这些动画的模型零影响。`riptide`（激流冲刺）基岩无对应 query，暂缺 | `animate_extra` 同键覆盖 |

副包动画文件里没有的标准动画只产生一条无害引擎日志（`can't find animation`），与官方
转换教程 FAQ 说明一致。

## 声明形态约定（对齐 Java，减少转换成本）

- **JSONC 照收**：`ysm.json` 允许 `//` 行注释、`/* */` 块注释与尾逗号（与 Java 侧
  Gson 宽容模式一致，野外模型包普遍带注释）。剥离是字符串感知的，值里的 `//`
  （如 `link.home` 的 URL）与 `/* */` 不受影响，且注释按等长空白替换以保留行号，
  语法报错的行列仍与原文件对齐。

- **资源声明写在 `files` 下**（Java 原生位置 + 基岩扩展字段同居）：动画/控制器用
  `files.player.animation` / `animation_controllers` 的**列表混排**形态——字符串 =
  路径导入整个文件，`{"短名": "动画ID"}` = 逐条注册（含跨命名空间共享动画）；
  贴图用 `files.player.texture` 列表混排——字符串 = Java 包内路径（皮肤名=文件名，
  按约定拼装），`{"皮肤名": "真实路径"}` = 皮肤名显式+路径直通（皮肤名与文件名
  不一致/共享贴图场景）；几何用 `files.player.model.main`/`arm`（`geometry.*`
  直通）；抛射物/载具用 `files.projectiles`/`vehicles`（共享资源写 ID/真实路径
  直通）。基岩扩展字段也挂 `files.player` 下：`animate`（`{"键": "molang 条件"}`
  给注册项挂/改播放条件）、`render_controllers`（`{"控制器ID": "条件"}` 追加）、
  `sound_effect` / `particle_effect`（音效/粒子表，形态 `[["效果键", "定义名"], …]`，主包据此
  `AddPlayerSoundEffect`/`AddPlayerParticleEffect`；移植工具自动生成——音频拷到
  `ysm_rp/sounds/ysm/<包>/`、定义写进 `ysm_rp/sounds/sound_definitions.json`，动画关键帧的
  `effect` 改写为效果键 `ysm_snd_<名>`；`validate_rp_animations.py` 会核对三件是否齐全）。
- **键值对一律用 `{}`**：`animate_extra` / `render_controllers_extra` /
  `preview_animation_extra`（值为 `["动画ID", "显示名"]`）/ `replace_entities` 内层
  等全部写 `{"键": 值}`，书写顺序即注册顺序（解析保序）。**只有天然有序的列表才用
  `[]`**：`initialize`（表达式序列）、`*_remove` 族（键集合）、`skin_sort`、声明
  列表本身。旧 `[[键, 值], ...]` 形态继续兼容。
- **网易扩展配置直接写顶级**（与 `spec`/`metadata`/`properties`/`files` 同级）：
  `priority` / `model_id` / `animation_namespace` / `strum` / `skins` /
  `preview_parallel` / `gui_scale` / `animations_remove` / `initialize`
  等全部顶级书写，**无需 `netease` 段**（`netease` 段为兼容形态，顶级同名键优先）。
  这些字段都是 Java 没有的概念（多皮肤、注册优先级、拨弦、轮盘扩展等），与 Java
  字段同级共存、互不侵扰。
- **移植产物的 ysm.json 只有 `spec` / `metadata` / `properties` / `files` +
  `initialize`**（molang `??` 默认值收敛而来，没有 `??` 的包连它都不需要）。
  Java 模式、并行通道接管这两件事**全自动判定**，创作者只管增删自己的动画与控制器：
  - **Java 模式**看 `files.player.animation` 的声明形态：`{}`（Java 语义槽位
    `main`/`arm`/`extra`/…）= Java 原生格式，走状态链 + `java_default` 官方基线；
    `[]`（路径 + kv 逐条）= 旧版/基岩扩展写法，走旧版共享基线。两代包的动画底表完全
    不同，但不需要任何开关声明。
  - **通道接管**看控制器：声明了 `player_parallel_N` / `player_pre_parallel_N`
    控制器（整条通道交给它），或任何自有控制器的状态里引用了 `parallelN`/
    `pre_parallelN`（常见写法是把 8 条并行动画全列进 `player_parallel_0` 的单一状态），
    该动画就不再自动直挂 —— 与 Java 的 `ParallelControllerDiscovery` 同语义。

## 顶级扩展字段（无 Java 对应，与 files/properties 同级书写）

| 字段 | 说明 |
|---|---|
| `priority` | 注册优先级（int，缺省 0，越大越靠前）。主包内置模型为 1000 恒排最前；副包同优先级按包名字典序稳定排序，保证模型列表顺序与存档索引不随组件加载顺序漂移 |
| `player_scale` / `gui_scale` | 玩家/GUI 缩放，默认 0.8 / 1.0（显式声明时优先于 `height_scale` 换算链） |
| `material` | 材质：`"bloom"` 或 `[["default","bloom"],["hair","nocull"]]`（nocull 自动切换、light→tohru 与内置一致） |
| `gui_render_controller` | **通常无需配置**：缺省统一为 `controller.render.ysm_pack_gui`，所有模型共用。`AddActorRenderControllerArray` 改的是**控制器定义本身**（全局），所以共享控制器的数组只由运行层追加一次固定槽位名 `Texture/Geometry.ysm_skin_slot_1..31`，每个预览实体把**自己的**第 i 个可选皮肤注册到槽位 i（没有的槽位回落默认皮肤）；JSON 里的数组必须只留 `default`。声明自定义控制器时沿用旧行为（按皮肤名往该控制器追加数组，多个模型别共用同一个自定义控制器） |
| `replace_entities` | `{实体ID: {geometry, texture, animations, animate, ...}}` 弹射物/载具替换表，与 `files.projectiles`/`vehicles` 推导结果**逐 ID 字段级浅合并**。内层 `animations`/`animate` 用 `{}` 键值对。**通常无需配置**：几何/贴图推导+共享资源直通+动画路径解析已覆盖常见场景（内置模型都已零补丁），只在推导确实表达不了时用（如替换实体的自定义状态条件覆盖）。载具另自动带根骨骼缩放动画（Java 硬编码 0.7）与本包音效登记；**载具的写入与存档语义对齐 Java**：第一乘客上车写入该玩家模型、下车不撤、随载具实体存档，直到下一个第一乘客覆盖（船、运输船与矿车是引擎硬编码渲染，直接换上后引擎不再转模型，运行层在根骨骼上补朝向、矿车照 Java 按车底轨道形状定，创作者无感） |
| `arrow` | ~~箭矢替换直通~~ **已由 `files.projectiles` + `replace_entities` 取代**（Java 原生位置声明抛射物，netease 只补差量）。旧字段仍兼容；`files.projectiles`（或 Java 废弃字段 `files.arrow`）声明 `minecraft:arrow` 时 `arrow` 键自动派生，无需手写。注意区分：这里说的是顶级网易扩展 `arrow`，Java 的 `files.arrow` 已按弹射物正常解析 |
| `icon` | 选择界面图标 |
| `gui_background` / `gui_foreground` | 选择界面卡片的背景图（纸娃娃之下）/前景图（纸娃娃之上、名字之下），铺满卡片内区，与 Java 卡片绘制顺序一致；资源包贴图路径，缺省由 Java `properties` 同名键推导 |
| `molang_bind_bones_list` | 需要回传骨骼位姿 molang 的骨骼名列表 |
| `animate_extra` / `render_controllers_extra` | 兼容形态——标准写法是 `files.player.animate` / `render_controllers`（后写覆盖 files 声明）。动画与控制器的**注册**写在 `files.player.animation`/`animation_controllers` 列表里（`animations_extra`/`animation_controllers_extra`/`textures` 同为兼容形态） |
| `preview_parallel` | 选择界面预览用的**并行叠加动画**。字符串形态：单动画 ID，仅大预览窗生效（条件 `variable.ysm_preview == 1.0`，内置酒狐系的纸娃娃站姿即此）；列表形态：多动画 ID，列表缩略图+大预览窗都生效（`ysm_show \|\| ysm_preview`）。**javaMode 包自动推导**为模型自有的 `parallel0-7`/`pre_parallel0-7` 全表（Java 的并行动画恒 LOOP 含 GUI——隐藏/摆位装饰部件靠它，不叠上去预览就是零件摊开的杂乱状态），显式声明仍优先。**GUI 展示动画**（Java `properties.preview_animation` → `gui_animation`）指向资源包里不存在的动画时（野外包常见：声明了 `stage` 却没这条动画），索引确认缺失即回落注册表 `idle` 并告警（Java 对此静默不播）；GUI 预览注册链路与玩家侧同样经资源索引剔除坏引用，不再逐帧刷 `can't find`。自动推导的全表同时包含并行族的**伴生动画**（`pre_parallel0__own1` 之类，见"动画流畅度"⑬）；其余动画的伴生由主包自动生成的配置字段 `preview_companions`（`{原动画 ID: [伴生 ID…]}`，无需手写）在注册 GUI 展示动画/预览动作表时同条件带上 |
| `channel_ownership` | **移植工具维护，不要手写**：`{伴生键: 权重}` —— 逐通道覆盖拆出的伴生动画 `<键>__own<N>` 里由主包直挂的那部分的让位权重（读 `variable.ysm_ownset_<n>`）。① 带 override 的直挂条件动画（hold/passenger/carry_on）：值是核心权重，主包挂 原条件 `&&` 核心；② 裸 `pre_parallelN`/`parallelN` 直挂早层：值是带 1e-4 下限的完整权重，主包挂 `门?权重:0`；③ 给 **GUI 展示动画**让位的 pre_parallel 伴生（Java cap 通道晚于 pre_parallel —— 官方酒狐的 pre_parallel 把舞台骨骼缩成 0，展示动画再放出来）：权重读卡片纸娃娃的 `variable.ysm_show`，预览实体按同一份权重注册（自动生成的配置字段 `preview_parallel_weights`）。缺项时只用原条件（等价未拆分）。见"动画流畅度"⑬ |
| `java_state` | **移植工具维护，一般不用手写**：本包要主包运行层维护哪些 Java 专有量。`needs`（`weather`/`open_air`/`dimension`/`light`/`air`/`health`/`hit_target`/`fishing`/`ladder_facing`/`frozen`/`texture`）—— 产物里读了对应落点（`query.mod.ysm_weather`、`variable.ysm_env_dimension` 等）就要声明，没声明的量读到的是缺省值；`roaming` —— Java `v.roaming.*` 扁平化后的变量名清单（`roaming_a` …），运行层给它们做存档与多人同步（上限 64 个；解析器会自动并上表单变量与文件扫描到的 `roaming_*`，漏写也能同步）；`probes` —— `{探针变量名: 声明}`，带常量参数的 Java 函数（`effect_level`/`enchant_level`/`block_name`/`block_name_any`）由运行层每 2 tick 求值写进 `variable.ysm_pb_*`。手写基岩包想用这些量也可以照此声明。落点全表见 `docs/ysm-java-molang-mapping.md` 第七节 |
| `extra_stop_expression` | 轮盘播放动画的停止条件 molang（缺省为系统默认"移动/跳跃/潜行时停止"，置空 `""` 关闭）；条目键自带播放参数的旧指令形态不受影响 |
| `java_state_driver` | **Java 模式的覆盖开关，正常无需书写** —— 模式按 `files.player.animation` 的声明形态自动判定（dict = Java 语义槽位 → Java 模式；list = 旧版/基岩扩展 → 旧版基线）。只在自动判定不合意时显式写 `true`/`false` 强制（两个方向都支持）。Java 模式的行为:①状态动画按 Java `AnimationRegister` 优先级链合成互斥 molang 条件直驱（死亡>骑乘链(vehicle$ 条件/猪/鞍乘/船/被抱/sit 兜底)>睡觉>泳姿>爬行>爬梯>飞行>鞘翅>踩水>受击>跳跃>潜行>疾跑/走/待机）;②**动画基线换血**——底表从旧版共享资源（fight 战斗状态机/TACZ/CarryOn/actor 姿态族）换成 `java_default` 官方基线包,缺失的键回落官方 default 动画（Java `AnimationStore` fallback 语义）,基线的手持条件动画对模型生效,`swing_hand`/`use_mainhand` 为条件无命中时的兜底;③旧版控制器/`injectRes` 注入/CarryOn 共享预览一概不带——**模型动画失败就表现为失败,不再被旧版动画顶替**;④`parallel0-7`/`pre_parallel0-7` 恒播（仅 `!v.is_first_person` 门——纸娃娃保留播放,隐藏装饰件靠它;主域并行动画在 FP 会驱动同名骨骼的手臂几何,Java FP 是独立域只播 fp_arm 空壳）,纸娃娃另经独立键 `idle_gui` 播 idle;⑤**标准键不再盲注册**（缺键回落基线,与 Java `AnimationStore` 一致——`sneak_arm` 等网易专属键 Java 包必缺,盲注册只会用坏 ID 顶掉基线兜底）;⑥轮盘键在资源索引确认缺失时**静默跳过**（Java 对无动画的轮盘键即无操作——表单宿主键/纯文本签名键属此类）,基线已提供的键（`extra0-7` 等）不盲注册以保基线兜底。**注**: `java_default` 官方基线包**不入版本库**（官方 default 模型版权归 YSM 作者,`.gitignore` 已排除）——本地执行 `python devtools/port_java_pack.py .ref/ysm-java-src/src/main/resources/assets/ysm/builtin/default --name java_default` 即从子模块自带的官方包生成;基线缺席时装载告警一次,缺键就是缺键（等价于 Java 删掉内置 default 的降级形态） |
| `animations_remove` 等 `*_remove` 族 | `[键, ...]` 从合成结果中剔除指定注册项（精确对齐用）。其中 **`animate_remove` 只对旧版声明形态（`files.player.animation` 写 list）有意义** —— 它是相对旧版共享基线 `animate` 表的差量；Java 格式包没有基线表、通道接管也自动推导，写它无效（一律忽略） |
| `skins` | `{皮肤名: {字段...}}` 皮肤级覆盖/补充（如发光皮肤的 `material`、完整自定义皮肤声明） |
| `preview_animation` | `{"键": ["动画ID", "显示名"]}` 整体覆盖选择界面预览动画表。**通常无需配置**：预览切换列表是网易 UI 独有特性（Java 卡片只播 `properties.preview_animation` 单动画），标准表（17 标准动作 + 4 CarryOn）由系统按命名空间自动推导 |
| `preview_animation_extra` / `preview_animation_remove` | 预览动画表增量：`{"键": ["动画ID", "显示名"]}` 按键覆盖/追加、`[键, ...]` 按键剔除。**通常无需配置**：标准预览条目的动画 ID 会**优先取注册表同键实际值**（模型声明覆盖过的键——网易键名文件、`_man` 变体等——预览自动播真实注册的动画），只在需要额外预览条目/改显示名时用 |

> **内置模型也使用本格式**：自带模型的声明位于主包 `ysm_bp/ysm_models/`
> ——与副包走**同一发现机制、同一解析器、同一归一化链路**，各自示范一种场景：
> `commander_male`/`commander_female`（顶级 `priority: 1000`，零 netease 段）=
> `files.player.texture` 皮肤推导 + 共享箭矢直通；`wine_fox/`（2026-09-17 起取代旧版
> 酒狐/JK 酒狐）= Java 官方内置"酒狐与小伙伴"合集 22 个变种的**移植产物**，示范合集
> 文件夹、Java 高级轮盘三件套（`#分类` → `$子轮盘`、`#按钮` 配置表单）、`files.projectiles`/
> `vehicles`/`files.arrow`；`ref_*` = 三个社区 Java 包的移植产物（复杂作者状态机）。

## 预览实体定义模板

`resource_pack/entity/<包名>.entity.json`，`identifier` 必须与模型 ID 一致：

```json
{
  "format_version": "1.10.0",
  "minecraft:client_entity": {
    "description": {
      "identifier": "ysm_pack:<包名>",
      "materials": { "default": "saury", "tohru": "tohru" },
      "textures": { "default": "textures/entity/<包名>/<默认皮肤文件名>" },
      "geometry": { "default": "geometry.<包名>" },
      "render_controllers": [ "controller.render.ysm_pack_gui" ],
      "scripts": {
        "initialize": [
          "variable.ysm_skin = 0.0;", "variable.ysm_gui = 0.0;",
          "variable.ysm_show = 0.0;", "variable.ysm_preview = 0.0;",
          "variable.ysm_light = 0.0;"
        ]
      }
    }
  }
}
```

## 动画文件的引擎红线（整份作废，静默无声）

基岩动画解析器是**整份文件**粒度：文件里**任意一处**不合法，该文件的 `animations`
段全部作废（`node parse failed`），文件里所有动画一条都注册不上。Python 侧
`json.loads` 照样通过，`AddPlayerAnimation` 只返回 `False` 不抛错，游戏内表现是
**模型完全僵直**而没有任何 Python 报错——排查时极易误判为控制器或条件的问题。

判断某文件是否整份作废，在游戏内探测即可（返回 `False` 即该文件没加载）：

```python
comp = clientApi.GetEngineCompFactory().CreateActorRender(playerId)
print(comp.AddPlayerAnimation("probe", "animation.<包名>.walk"))
```

已实测的四条红线（`devtools/port_java_pack.py` 移植时全部自动规范化）：

| # | 非法形态 | 基岩要求 | 实测代价 |
|---|---|---|---|
| 1 | **赋值语句无分号** `"v.bv=math.cos(...)*5"` | 含赋值必须是完整语句，补 `;` | Java `main.animation.json` 的 9 处让 **46 条主体动画全丢** |
| 2 | **动画 ID 含 `$` `:` `#` 或英文大写** | 只接受 `[a-z0-9_.]`：条件动画名按 `.id.` / `.tag.` / `.cls.` 转义（前缀自带的冒号一并折叠，如 TACZ 的 `tac:hold:fire$tacz:minigun`）；大写统一转小写（Java 允许 `PefectDef`/`walkBack` 这类短名；骨骼名不受限——引擎按大小写不敏感匹配骨骼） | 一处非法 → 整份作废 |
| 2b | **标识符含非 ASCII(中文等)** | 动画 ID、动画控制器 ID、**控制器状态名**、状态转移目标、控制器内的动画引用一律只收 ASCII。实机日志：状态名含中文 → `child '散热开始' not valid here`，**该控制器整个失效**；动画 ID 含中文则命中第 2 条的整份作废（且是静默的）。移植工具自动转拼音（`pypinyin`，未装时退化为 `u<码点>`），并同步改写控制器引用与 `ysm.json` 里的轮盘键/预览动画 | 一处非法 → 该控制器失效 / 整份动画作废 |
| 3 | **Java 专有 molang** `ysm.*` / `ctrl.*` | 替换清单见 `ysm-java-molang-mapping.md` | unknown token → 整份作废 |
| 4 | **geckolib 专有字段** `geckolib_format_version` | 剔除 | `child not valid here` → 连锁作废 |
| 5 | **引擎不存在的 query** | token 存在性按证据分级裁决，**不臆断**：实机探针（最终裁决）> 引擎二进制字符串表（强，双向）> 原版 RP 使用（仅正面）> 微软文档（参考）。⚠️ 两个方向都不能想当然："文档收录 ≠ 网易引擎有"（`frozen_alpha` 缺失），"原版没使用 ≠ 引擎没有"（`yaw_speed`/`math.exp`/`equipped_item_any_tag` 原版零使用但引擎实有）。二进制表来自 `Minecraft.UnitTest.dll`（370MB **未加壳**，对照组 8/8 验证；主 exe 加壳不可用）：3.9 引擎有文档 query 298/306，仅缺 `kinetic_weapon_*` 等 8 个新版专有名。移植工具引擎门：二进制确认缺失才置零，证据缺失的保留并告警待实机探针 | unknown token → 整份作废 |

> 标量通道值（geckolib 的 `"scale": 2`）引擎能容忍，移植工具仍统一展开为三分量
> 向量 `[2, 2, 2]` 保持规范。
>
> `??`（空值合并）基岩**原生支持**，但本项目的 molang 变量会被主包自动初始化，
> `??` 的 fallback 因而永不触发 —— 移植工具改写为变量本身 + 把默认值写进
> `netease.initialize`，保住 Java 的"未设置时取默认值"语义。
>
> **批量替换层**（`port_java_pack.py`）按三级处理并输出逐项汇总：① 函数调用剥离
> （`ysm.second_order`/`first_order` 改写为 molang 状态积分——见下；`ysm.particle`/
> 骨骼函数置零）② 名字映射（census 驱动，覆盖 Java builtin 普查出的 ysm.\* 31 种 /
> ctrl.\* 25 种 / query.\* 23 种）③ 引擎存在性门（残余 token 逐个对照实测集，未知即
> 置零告警）。汇总里 `[!]` 开头的行必须人工过目。
>
> **动画流畅度四件套**（移植工具自动，`fix_ported_controllers.py` 可就地补除物理外的三件）：
> ⓪ **pre / main / parallel 三个域的动画一律不带 `override_previous_animation`**（裸
> `pre_parallelN`、`player_pre_*` 控制器所播的 jump_up/jump_fall、主链成员、parallel 族；
> 只有 hold/swing/use/armor 条件动画与轮盘 extra 保留它 —— 它们语义上确实要独占骨骼）。
> 这是交叉淡化能生效的前提：淡化期间入态带该标志会把出态的姿态整个抹掉，看起来仍是硬切
> （原版 73 个带 `blend_transition` 的状态里播 override 动画的为 0，实机多轮核实）。该标志在
> animate 条目之间的确切语义**没有可靠实机结论**（早先的"定案"被一次整份动画文件遭引擎拒载的
> 事故污染，已作废），设计不依赖它。**Java 的"main 通道逐通道覆盖 pre 通道"由逐通道覆盖（⑬
> `ApplyChannelOwnership`）按主链状态让位**：pre 层与主链同写的（骨骼, 通道）拆进伴生动画，主链
> 状态写占用变量，伴生只在写它的主链状态活跃时让出。早先的 `ApplyJavaMainOverride` 假设"常驻 pre
> 与 idle 总在同播"直接删 pre 层通道，走路/奔跑/卡片预览（不播 idle）时被删的隐藏缩放就没了 ——
> 大酒狐 pre_parallel0/1 藏爱心/ZZZ 的缩放被删，卡片上一直顶着它们（2026-09-18），已撤；旧产物
> 补不回来，需从 Java 源重新移植。pre 控制器对**主链成员**的冗余引用（`fly` 常被 pre 控制器的飞行态
> 引用，两边同播会叠成两倍）由 `DropPreControllerMainChainRefs` 摘掉；**删空的动画连 `bones` 键一起删**
> —— `"bones": {}` 会让引擎拒载整份动画文件（2026-09-03 实机：模型停在绑定姿态、换装件全亮）。
> pre 层内部同理（`pre_parallel_0..7 → vehicle → pre_main`）：晚层只在**当前状态**写该（骨骼, 通道）
> 时覆盖早层，同一状态内的多条动画 Java 是加权相加（与基岩相同），都交给逐通道覆盖按状态让位；
> 只有晚层**恒在播**（未被接管的裸 `pre_parallelN`、单状态无转移控制器）时才静态删早层通道。早先的
> `DedupPreLayer` 把"跨控制器即同播"的两条静态去重（同状态内也按后者覆盖）：K 螺诺亚 `pre_parallel3`
> 的待机摆尾被 `pre_main` 脚本控制器的走/跑/跳动画整条删光，站着不动时尾巴僵直（2026-09-19），已撤；
> 旧产物同样补不回来，需重新移植。
> 常量变体折叠（`ReconcileConditionalVariants`）只折进**恒在播**的 pre 写入者，且该通道全包只有它与
> 变体在写 —— 否则折叠等于把变体降到 pre 层，被门控/夹在中间的晚层连变体一起压住；其余交给逐通道覆盖。
> 改完产物跑 `devtools/validate_rp_animations.py` 做引擎红线体检。
> ① `animation_controllers/<包>/ysm_state.json` —— Java 主链（idle/walk/run/jump/
> sneak/骑乘/泳姿/受击/死亡…）的生成式状态机，转移判据即 `java_state_driver` 合成的
> 互斥条件，各态 `blend_transition` 0.1s = Java main 通道起始过渡（可用
> `netease.state_blend` 按包覆盖）；直挂 animate 条目是零过渡硬切换，主包在资源索引发现
> 此控制器即用它替换包自有主链条目。`death`/`attacked` 由 `hold_on_last_frame` 停在末帧，
> 判据翻转后直接淡进下一状态（即 Java 尾过渡）。跳跃带**粘滞**：整个腾空期留在 `jump`，
> 不会在最高点被 walk/idle 抢走再触发一次；挥击族仍保留 override（Java 跨通道覆盖），
> 因此其状态不写任何 blend（写了只会让手臂先塌到绑定姿态再弹回）；② 主链成员的 `loop` 按 Java 运行语义改写（成员强制
> 循环，death/attacked 与未写循环的主手挥击族改 `hold_on_last_frame` 保住可供淡出的末
> 姿态）；③ `ysm.second_order('键', 输入, f, z, r)` → `v.ysm_so_<键拼音>_y` 状态变量
> + 逐帧积分语句（半隐式稳定变体，`q.life_time` 差分取帧间隔并封顶 0.1s，同帧多次求值
> 恒等），状态变量经变量扫描自动初始化——这是头发/尾巴/胸部随动"Q 弹"手感的来源，
> 已落盘产物无法就地恢复，须从 Java 包重新移植。
> ④ **腾空闩锁** `variable.ysm_airborne`：主包共享的 `animation.ysm.java_input_state`（animate 表首位）
> 逐帧更新（进入走死区挡走路翻转，留在腾空只看 `!is_on_ground&&!is_in_water`），作者
> 控制器里的 `ctrl.jump/idle/walk/run` 展开全部改用它，跳到最高点不再出跳；
> ⑤ **空中转状态旁路**：作者状态机里不播动画的中转状态（凋灵娘 `cache`）进出各要一次
> 淡化，中间姿态淡到绑定姿态（落地一瞬间直立），工具给 `X→中转` 前置 `X→Y` 复合转移；
> ⑥ **每 tick 脚本动画**（`loop` + **显式** 0 长度 + `timeline`）改 0.05s 循环，timeline 每 tick
> 触发（凋灵娘眼球跟随靠它）；**不写长度**的动画按 Java 是无限长（timeline 只跑一次），工具显式
> 写成 1e6 秒（凋灵娘 `voice_set_N` 轮盘键靠它只设一次变量）；⑦ Java `q.ground_speed` 的替换式带
> 静止死区（<0.05 钳 0），作者状态机的 `==0` 判据在冰面/潜行微动时仍成立；⑧ timeline 里的
> `ysm.particle()` 转成 `particle_effects` 关键帧 + 根骨骼 locator + `files.player.particle_effect` 登记
> （只在移植期做，需要 Java 源包）；⑨ **条件变体折叠**：pre 通道的静态显隐与 parallel
> 通道的条件变体共写同一 (骨骼, position/scale) 时合并为单一所有者，变体条件折进 pre
> （`scale: ["(v.Emotions==3)?1:0", …]`）—— 火焰、表情、嘴型这类"隐藏基线 + 条件显示"
> 在基岩下双写会互相抵消而完全不显示；⑩ **空中转状态旁路**：指向不播动画的中转状态的
> 转移排到列表最后并前置直达转移（起步/起跳/落地不再闪一下直立）；⑫ **手持物品定位骨骼**：
> 基岩把手持物品挂在固定骨骼名 `rightItem`/`leftItem` 上，Java 用 `RightHandLocator`/
> `LeftHandLocator` —— 工具自动在定位骨骼下补这两根空骨骼（主几何 pivot 就取父骨骼 pivot，
> 与 CSM 及作者自制的基岩版一致，第三人称摆位由主包按物品类别叠修正动画
> `animation.ysm.java_item_fix_*`/`java_bow_fix`/`java_crossbow_fix`；arm 几何沿用原版的 z+1；
> 都带 `lead_hold`/`lead_hold2` 拴绳点。早先主几何也是 z+1，修复工具会把形状完全等于生成物的
> 旧骨骼挪到新挂点），**主几何与 arm 几何两份都补**
> （主几何缺了就是第三人称弓/弩/盾不显示、普通物品掉回实体原点；第一人称的手持物
> 绑在原版体型锚点上，不依赖 arm 几何的这两根骨骼，补上只是两份几何同构）；
> 自己写了这两根的包原样保留；⑪ **背面不剔除**：
> Java 模式包未显式声明 `material` 时回退 `bloom_nocull`/`bloom_plus_nocull`（Java 侧是
> `entityCutoutNoCull`，作者按两面可见建模；基岩默认剔除会让火焰/飘带只有正面显示）；
> ⑬ **逐通道覆盖**（`ApplyChannelOwnership`，移植期最后一步）：Java 每个（骨骼, 通道）由最后
> 处理的通道决定——作者控制器接管的通道（含 `player.parallel_N`）连旋转也是覆盖，只有内置
> 并行通道（裸 `parallelN`）旋转相加；基岩逐通道相加、缩放相乘。早层动画里会被晚层**条件状态**
> 覆盖的通道搬进伴生动画 `<键>__own<N>`（同文件紧跟原动画），在原动画出现的每个状态里紧跟播放，
> 权重 `!((variable.ysm_ownset_<n>??0))`；晚层控制器（含 `ysm_state`）每个状态 `on_entry` 写
> `variable.ysm_own_<控制器> = <状态序号>` 并重算用到它的状态集合。晚层恒活跃的直接删早层通道。
> 效果：持剑姿态不再与主链/前置步态叠成两倍，前置层藏起的攻击特效在攻击状态里正常显示。
> **`__own<数字>` 键名后缀与 `variable.ysm_own_*`/`ysm_ownset_*`/`ysm_t0_*` 为工具保留**，这些运行期变量不参与
> 文件扫描补 0（模型应用时清零会让早层通道重新叠加）。GUI 预览与纸娃娃没有晚层控制器，主包把
> 伴生跟着原动画同条件一起播（配置字段 `preview_companions`，见"顶级扩展字段"的 `preview_parallel` 行）。
> 2026-09-17 补全：基岩**权重 0 的动画暂停计时**、还让所在状态的 `all_animations_finished` 永不成立，
> 不带 override 的伴生让出时改保留 1e-4 权重（`核心*0.9999+0.0001`）；**带 override 的条件动画也拆**
> （持盾 hold 与持剑状态机同写左臂时 Java 里状态机胜出），这类伴生精确 0 让位（override 权重大于 0 就整通道
> 清空前序条目），直挂条件动画的核心权重写进顶级 `channel_ownership`、挥击/使用的写进一次性状态机；
> 作者状态里有带条件的条目时，`q.all/any_animation(s)_finished` 改写成按进入时刻 `variable.ysm_t0_<控制器>`
> 计时（Java 只统计条件为真的动画）；animate 表把带伴生的晚层宿主倒排在最前，占用变量当帧生效
> （顺排时凋灵娘持剑跑跳衔接闪一帧）；
> **裸 `pre_parallelN`/`parallelN` 直挂早层同样拆**（小小酒狐表情缩放 × 奔跑/潜行的脸部缩放），伴生由主包紧跟裸条目
> 直挂；**晚层把缩放恒写成 0 不算冲突**（0 × 早层 = 0 就是 Java 的结果，让出反而在晚层状态淡入的 0.1s 里漏出被藏的
> 骨骼 —— 小小酒狐起跑闪出整架飞机）；**GUI 展示动画（cap 通道）晚于 pre_parallel、早于 parallel**：pre_parallel 与它同写的
> 通道拆伴生、卡片上（`variable.ysm_show`）让出，被裸 parallel 写的位移/缩放从展示动画里删掉；
> ⑬′ **关键帧形态**：所有只写 `post` 的帧补同值 `pre`（基岩线性入段缺 `pre` 取通道默认值——
> 凋灵娘火焰莫名缩放的根因），收尾帧 pre/post 都写；首帧晚于 0 的通道在 0.0 补保持帧；
> catmullrom 按 Java"区间任一端"语义换算成基岩"出边"标记，再把预计算窗口 [i-1, i+2] 内有表达式的
> 降为 linear。
>
> **分组标题条目的判据**：Java 包常用 `————头颅动画————` 这类纯符号条目分组，移植工具会丢弃
> 它们。判据是"名字里没有字母数字 **且** 动画体没有内容"——只看名字会误杀纯中文名的真动画
> （曾丢掉凋灵娘的骷髅头开合、warden 的整套拳击）。
>
> **纸娃娃门**：主包为合成的条件/状态动画统一前置 `!v.is_paperdoll` —— 网易 UI
> 纸娃娃是无装备上下文的实体，物品/骑乘类 query 在其上求值会持续刷
> `called without a specified entity` 错误，前置门利用 && 短路挡掉。
>
> **纸娃娃是玩家的独立渲染实例**：它有自己的 molang 变量作用域，主包对玩家实体
> 做的 `SetPlayerVariable` 到不了它（实机报错行的实体名形如
> `minecraft:player.0.<uuid>.CustomSlim<hash>-<运行时id>`，切换模型还会重建实例；
> 原版背包纸娃娃、模型设置界面的网易触屏控件 / PC 端 `live_player_renderer` 都是
> 这样的独立实例）。未定义变量按 0 求值的实机后果：warden `pre_parallel1` 的 Root
> 缩放 `v.player_size` 归零 → 整模消失；wither `parallel0` 的眼睛 `v.Emotions==0` →
> 没眼睛；sahmet `parallel3` 换装件按索引 0 全亮并逐通道刷 `unhandled request for
> unknown variable`。解法是**每模型包一份变量初始化控制器**
> `animation_controllers/<包名>/ysm_variable_init.json`（移植工具随移植生成，
> `fix_ported_controllers.py` 幂等重建）：唯一状态 `init` 的 `on_entry` 逐条
> `v.x = v.x ?? 默认值;` 后单向转到空状态 `done`——每个渲染实例创建时各跑一次，
> 世界实体上已由 `SetPlayerVariable`/轮盘写入的值经 `??` 原样保留（不会被默认值
> 顶掉），纸娃娃实例则取到包默认值。默认值来源与预览实体 `scripts.initialize`
> 同一口径（`netease.initialize` 显式值 > `config_forms` 初值 > 文件扫描补 0）。
> 主包解析器在资源索引里发现 `controller.animation.<包名>.ysm_variable_init` 即以
> 保留键 `ysm_variable_init`、条件恒 `1` 自动注册（不经双门，所有渲染域生效），
> 不需要在 ysm.json 里声明；**改动 `netease.initialize`/`config_forms` 后重跑修复
> 工具**刷新该文件即可。不改玩家实体定义 `entity/player.entity.json`——那是全局
> 唯一覆盖点，与其他同样覆盖玩家实体的组件互斥。渲染**预览实体**
> （`ysm_pack:<包名>`，选择界面的缩略图与大预览窗）的纸娃娃不经此路——预览实体
> 是本包自己的实体定义，包变量直接烘在它的 `scripts/initialize` 里（同一次工具
> 运行、同一口径生成；实机 2026-09 验证：运行期给预览实体挂初始化控制器**不生效**，
> 缩略图逐通道刷 `unknown variable`，所以这里就用原生 initialize）。手写面只有
> ysm.json 一处，两份产物都是它的派生。
>
> **纸娃娃的并行族**：主域并行动画带纸娃娃门，装饰件的隐藏正靠它 —— 主包因此以
> 独立键 `paperdoll.<并行键>` 复用同一动画 ID 单独补一份，条件仅 `v.is_paperdoll`，
> 按 `pre_parallel → idle_gui → parallel` 的通道序插入。只收**资源索引确认不含
> query** 的并行动画；含 query 的（悬浮武器/鞘位切换族）在纸娃娃上不播，是可接受
> 的静态降级。独立键与主域键错开，故不受控制器接管通道时的主域键摘除影响。
> 实机（2026-09）两类纸娃娃上 `variable.is_paperdoll` 均为 0，主域条目照常运行，
> 这一族目前只是引擎日后置位时的兜底。

## 排错

- **改了资源包却没生效**：热重载（R 键 / `reload_game`）**不重新扫描资源包**，
  引擎的动画/几何索引是进程启动时建立的——新增文件不会被发现，改动的文件仍用
  旧内容。资源包改动必须**完整重启游戏**验证。
- **纸娃娃刷 `unhandled request for unknown variable` / 纸娃娃整模消失、没眼睛、
  糊满换装件**：纸娃娃是独立渲染实例，变量作用域与玩家实体分开。该模型包缺
  `animation_controllers/<包名>/ysm_variable_init.json`（旧版移植产物），跑一次
  `python devtools/fix_ported_controllers.py <包名>` 生成后重启游戏即可；渲染预览
  实体的则靠实体定义的 `scripts/initialize`。
- **包自带的装饰件没隐藏（模型糊满配件）**：多半是该包的动画控制器文件被引擎
  整份拒载 —— 网易引擎对**状态里写了空 `animations: []` 数组**的控制器文件直接
  不加载，文件内所有控制器的 `AddPlayerAnimationController` 全返回 `false`，
  且不打任何日志。移植工具剪枝后会把空列表删键，已移植的包补跑
  `python devtools/fix_ported_controllers.py <包名>` 即可。
- 日志搜 `[YSM-PackLoader]`：`扫描完成: 行为包 N 个, 发现声明 M 份, 注册模型 K 个`，
  以及逐包解析错误/警告。
- 引擎刷 `Error: can't find animation <键>`：模型引用了不存在的动画/控制器资源。
  主包维护一份磁盘资源索引，**JSON 模型包自己命名空间**（`animation.<包名>.*` /
  `controller.animation.<包名>.*`）下确认缺失的引用会被自动跳过注册并汇总告警
  （`模型 X 引用了 N 个不存在的资源`）——按告警列出的键补齐资源即可；引擎/共享
  命名空间的引用不做判定（存在性对引擎侧资源不可知）。
- 模型没被发现：确认组件已在本存档启用，且声明位于**行为包**根下
  `ysm_models/<包名>/ysm.json`（放资源包读不到）。
- `模型ID xxx 重复`：两个包推导/声明了同一模型 ID，后加载者被跳过——给包名加作者前缀。
- GUI 里看不到模型：预览实体定义的 `identifier` 与模型 ID 不一致、或资源 ID 拼写不符合命名规范。
- 端到端参考：主包内置 `ysm_bp/ysm_models/` 即完整实例（`commander_*` = `files` 推导
  形态 + 共享箭矢直通、`wine_fox/<变种>` = Java 合集移植产物：合集文件夹/高级轮盘/弹射物与载具）。

## 暂不支持

- **PBR 贴图**（`normal`/`specular`）：玩家与替换实体均只取 `uv`（替换实体侧会告警）。
- **条件动画里的模组专属分类**（`:slashblade`、`:gohei`）：基岩无对应判定，跳过并告警。
- **`riptide`（激流冲刺）状态**：基岩无对应 query（`is_riptide` / `is_auto_spin_attack`
  实测均不存在）。
- **渲染标志**：`render_layers_first` / `all_cutout` / `gui_no_lighting` /
  `thumbnail` / `free`（`gui_foreground` / `gui_background` / `disable_preview_rotation` 已支持，见上表）。
- **等待上游 / 属转换器职责**：音效（`sounds` 与轮盘按钮 `sound`——Java 官方包中该字段
  从未使用，其自身声音打包亦未实现）、自定义函数（`functions/*.molang`）、
  多语言（`lang/*.json`：主包运行期不读；移植工具会把 `zh_cn.json` 的显示文本烘进 ysm.json）、
  `merge_multiline_expr`（多行 molang 数组需在转换时合并为单串）。
- **替换实体（弹射物）的音效**：弹射物动画里的 `sound_effects` 关键帧不登记（载具已登记，见上）。
- **Java 专有 molang**：约 100 个 `query.*` 与函数在基岩不存在，替换清单见
  `ysm-java-molang-mapping.md`。

## 实现位置（维护者）

`ysmModelScripts/packLoader/`：`resourceIO`（文件读取唯一封装点：经
`common.minecraftMod.instance().addonPaths` 取已挂载行为包磁盘路径后枚举读取。
**`os` / `open` 是网易审核违规模块，模块内经 `__builtins__["__import__"]` 绕过取得，
文件系统操作必须全部收敛在此文件，不要在别处直接 import**）→ `clientScanner`
（幂等 `EnsureScanned`，注册表构造期即执行，
`LoadClientAddonScriptsAfter` 与 `UiInitFinished` 为后备触发）→ `modelInstaller`
（解析 + 按 priority 经 `RegisterModelSorted` 原地插入，与旧版 py 副包通道稳定共存）→
`packParser`（合成）。纯客户端单侧完成，无服务端参与、无下发通道。

2026-08 游戏内实测排除的其他途径（勿再尝试）：隐藏接口 `resource.load_file` /
`load_all_file` 在客户端 mod 上下文对任意路径恒返回空（含原版 `entity/player.entity.json`
与引擎自用的 `extra_info.json`）；官方 `GetModConfigJson` 只能读 `/modconfigs` 下的
已知路径且无目录枚举；`server_resource` 仅服务端可用（客户端全 `None`），走它必须
引入下发通道。引擎自身的图鉴插件（`illustratedBook`）用的正是 `addonPaths` + `os` 这条路。

离线测试（改动 packLoader 后全跑一遍）：`devtools/test_pack_parser.py`（含 Java 2.6.5
真实主包文件用例）、`devtools/test_legacy_subsidiary.py`（旧版 py 副包注册链路）、
`devtools/test_resource_index.py`（坏引用过滤判定域）、`devtools/diff_new_vs_old.py`
（JSON 全链路等价性，`engine_stub` 用真实 `ysm_bp` 目录模拟 `addonPaths`）。
