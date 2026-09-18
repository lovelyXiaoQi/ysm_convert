# Java YSM → 网易基岩 molang 映射清单

移植 Java 版 YSM 模型包时,动画/控制器表达式中 **Java 专有 query/函数** 的替换指引。
Java 侧 `ysm.*`/`ctrl.*` 注册于 `YSMBinding`/`CtrlBinding`,`query.*` 由 **YSM 内嵌的
geckolib 分支自己实现**(QueryBinding)——命名沿袭基岩惯例但**不是引擎提供**,个别
同名不同义(见"同名陷阱")。它们在网易基岩引擎里不存在或语义有别,原样保留会导致
表达式求值失败(unknown token 会让**整份动画文件静默作废**)。

> 本表的机器可执行版本是 `devtools/port_java_pack.py` 的批量替换层
> (`_JAVA_NAME_MAP`/`_CTRL_NAME_MAP`/`_FUNCTION_STRATEGIES` 等)——移植工具自动
> 完成全部替换,本文档供人工核对与手工移植参考。语义依据 dev/1.20 源码
> (.ref/ysm-java-src),关键行号在工具源码注释里。

标记:✅ 直接对应;≈ 近似(语义有差异,见说明);🔶 主包已同步的 mod 侧值;❌ 无对应。

## 零之前之前、**表达式关键帧及其相邻帧的形态红线**(2026-09-05;2026-09-16 引擎判据定案)

> **2026-09-16 定案(以本段为准,下文为 09-05 的推导过程)**:
> ① **窗口是 [i-1, i+2],不是 ±1 邻域。** 网易引擎加载时为 catmullrom 关键帧 i 预计算三次样条,
> 控制点取关键帧 i-1、i、i+1、i+2,任一帧含表达式就在 Debug_Log 报
> `Precomputed cubic interpolation requires keyframes have constant data`。对部署产物逐通道检验,
> 这个窗口与日志里 9 条报错动画逐条、逐通道数量完全吻合(对称 [i-2, i+2] 多报,旧的 ±1 一条都
> 抓不到)。由此也可知基岩 `lerp_mode` 只管关键帧的**出边**;Java 是"区间任一端 catmullrom 即样条",
> 移植期由 `ApplyJavaCatmullSegments` 换算。
> ② **只写 `post` 的关键帧不是无害形态**:线性入段的终点取 `pre`,缺 `pre` 时引擎取**通道默认值**
> (scale 1、rotation/position 0)。实机骨骼缩放探针:凋灵娘火焰精灵帧与 death 的 Root 在
> `{"post": [0,0,0], "lerp_mode": "linear"}` 收尾帧前一段从 0 线性涨到 1(首帧值是 0,排除回绕)。
> 下文"仅 post 非致命"的结论只对 Blockbench 式的 catmullrom 平滑帧、或值恰好等于默认值的帧成立
> (wine_fox 那 81 处多是 `[0,0,0]` 位移)。移植工具现在给所有只写 post 的帧补同值 `pre`;
> 体检对"线性入段 + 只写 post + 非默认值"报错。

关键帧值写成 molang 表达式时,`"lerp_mode": "catmullrom"` 在基岩下不可靠 ——
catmull-rom 要靠**相邻关键帧的值**算切线,而表达式值逐帧变化,样条本身没有良定义
(跨长空档时还会把通道值拉到失控范围,见 `port_java_pack.SealAnimationTails` 记录的
凋灵娘眨眼案例)。

**关键:受限的作用域是「表达式帧 ∪ 它的前后邻居」,不只是表达式帧自己。**
插值是**帧间**行为,一段区间只要有一头是表达式,这段就受限。首轮只修表达式帧自己
(122 + 184 处)之后,拉弓仍然"先侧面停半秒再跳到正面",真凶正是那个**纯数值**的邻居帧:
warden `use_mainhand.cls.bow` 的 `UpBody` t=0.0 是 `[10.07, -64.58, -11.12]` + catmullrom,
紧邻 t=0.5 的表达式帧 —— 0→0.5 整段退化成"停在起点值,到 0.5 再跳过去"。

**判据来自 CSM 的既成事实**(它转换同一批 Java YSM 包且工作正常,全库 30 文件 / 3742 通道):

| 关键帧形态 | 挨着表达式(自身或邻居) | 远离表达式 |
|---|---|---|
| `lerp_mode: catmullrom` | **0 处** | 68 处 |
| 只写 `post` 不写 `pre` | **0 处** | 8346 处 |

右列量很大且工作正常 —— 所以这不是"CSM 不用 catmullrom / 不用仅 post",而是它
**精确避开表达式的邻域**。我们修复前:三个移植包共 48 处邻域违例(warden 24 / sahmet 34 /
wither 34,含表达式帧自身),`compat/ysm_tacz.animation.json` 另有 37 处 catmullrom 违例。

**两条规则的分量不一样,别混为一谈:**

① **`catmullrom` → `linear`(致命)。** 这条是实机可复现的破坏。
② **仅 `post` 的补一份同值 `pre`(对齐 CSM,非致命)。** 见下方旁证。

**旁证 / 对照组:内置 `wine_fox` 与 `wine_fox_jk`**(原版长期实机可用,不是移植产物)——
它们有 81 处"表达式 + 仅 post"的关键帧、成片存在且表现正常,但**挨着表达式的 catmullrom
是 0 处**。这把两条规则彻底分开了:仅 `post` 本身不致命(补 `pre` 只是与 CSM 形态对齐、
语义等价的无害改写),真正会把整段插值打废的是 **catmullrom 落在表达式邻域**。

实机症状(warden/wither 拉弓)分两轮:
- 第一轮:`UpBody`/`AllBody`/`Arm`/`Head` 的偏航是 `-75 + 按 head_yaw 回正` 的表达式且带
  catmullrom → 身体侧身姿态在播,但回正项不生效,弓甩到视线左侧六十多度。
- 第二轮:回正对了,但起手 0.5 秒仍停在侧面再跳到正面 —— 邻居帧(纯数值 catmullrom)没修。

修法:`port_java_pack.NormalizeExpressionKeyframes`(按邻域判定,纯数值帧只要不挨着表达式
就一概不动);已落盘的移植包用 `python devtools/fix_ported_controllers.py` 就地补(幂等)。
围栏:`devtools/validate_rp_animations.py` 已把"catmullrom 落在表达式邻域"列为**错误**
(此前是警告,且只看表达式帧自身)。人工维护的 `compat/ysm_tacz.animation.json` 走定点
文本改写(只重写命中的 37 行 `lerp_mode`),不能用 `DumpJson` —— 那会把 8957 行整体重排。
### 顺带:`use` 通道必须走状态机,不能用直挂 animate 条目

基岩直挂条目对**不循环的定时动画不重放** —— 条件再次成立只是继续应用,时钟不回零。
实机:第一次拉弓正常,射出后再拉,动画直接从末帧起步(弓一上来就是放大 2 倍的终态)。
这与仓库早已为挥击/受击/死亡解过的是同一个问题(`BuildOneShotControllers`),
现已把 `use_mainhand`/`use_offhand` 两族一并纳入(`_ONESHOT_USE_CHANNELS`)。
CSM 同样把 use 动画塞进控制器状态(`controller.animation.csm.player.custom_use` 的
`on_use` 态)而非直挂。**与挥击的差别**:use 成员状态在触发期间一直停留 ——
use 动画多为 `hold_on_last_frame`,按 `all_animations_finished` 出态会在拉满弓/
举着盾时提前掉回 idle,姿态当场归位。

## 零之前、**三套求值上下文不等价**(2026-09-05 实机定标)

同一串 molang 在这三处的可用范围**不同**,别拿一处能跑当另一处的证据:

| 上下文 | 例 | 已知限制 |
|---|---|---|
| `QueryVariable.EvalMolangExpression` / `GetMolangValue` | 调试探针 | 最宽松。带参数的 query 只能走 `EvalMolangExpression` 赋值再读回,`GetMolangValue` 直接传参返回 `None` |
| **animate 播放条件** / 动画控制器 transition | `hold_mainhand.cls.sword` 的条件串 | 有实体上下文,`is_item_name_any` / `equipped_item_any_tag` 都可用 |
| **动画文件的骨骼通道**(`bones.<骨骼>.<通道>`) | `"scale": "…"` | ⚠️ **`query.is_item_name_any` 不可用** —— 引擎报 `called without a specified entity`,**整条通道作废**(纸娃娃/预览实体尤其没有装备上下文)。`equipped_item_any_tag` 实测可用 |

引擎对这类错误**会在日志里报全文**(`[ERROR][Engine] <包> \| <文件> \| animations \| <动画> \| bones \| <骨骼> \| <通道> \| <表达式> \| Error: …`),
排查动画不动时先翻日志,比看现象反推快得多。

## 零、同名陷阱(最重要,不改必错)

| Java 写法 | Java 实际语义 | 基岩同名 query 语义 | 正确替换 |
|------|------|------|------|
| `query.head_x_rotation` | **头部偏航**(=ysm.head_yaw,度,±85) | 头部**俯仰** | 🔶 `query.mod.ysm_head_yaw` |
| `query.head_y_rotation` | **头部俯仰**(=ysm.head_pitch) | 头部**偏航** | 🔶 `query.mod.ysm_head_pitch` |
| `query.is_item_name_any('mainhand',...)` | 槽位用短名 | 槽位要全称 | 参数改写 `'slot.weapon.mainhand'`(armor 同理;`equipped_item_any/all_tags`、`max/remaining_durability` 同族) |
| `query.life_time` | 模型累计动画秒(换模型清零) | 实体存活秒 | 保留同名(sin 摆动等价,仅相位差;绝对时长判断不等价) |
| `query.yaw_speed` | 度/秒 | **实测噪声不可用**(走路中 0~290 间歇归零) | 🔶 `query.mod.ysm_yaw_speed`(主包差分+EMA 平滑,度/秒) |
| `query.ground_speed` | 格/秒(行走≈1.7 疾跑≈3.2) | **实测噪声不可用**(帧间 0↔60 乱跳) | ≈ `((query.modified_move_speed>0.05)?query.modified_move_speed*1.9:0)`(平滑,量纲近似 Java;低于走路阈值钳 0 —— 作者状态机拿 `==0` 判静止,基岩在冰面滑行/潜行微动时是小非零值,不钳会卡在空中转状态) |
| `query.is_using_item` | 正在使用物品 | 同名 query 存在且能取值(实测返回 0/1),但**网易 3.8.0 的原版资源包零引用**,可靠性无旁证 | ✅ 改用 `(query.main_hand_item_use_duration>0\|\|(query.blocking&&主手是盾)\|\|(query.blocking&&副手是盾))` —— 原版 `player.entity.json` pre_animation 与 attachable 动画自己在用的量(`packParser._ITEM_IN_USE_TEST`)。**注**:`is_using_item` 并未被证伪,换掉只是取更保守的原版口径。⚠️ **`query.blocking` 必须配持盾判定**:2026-09-16 实机,不持盾时按住潜行约 0.1 秒后 blocking 也变 1,早先裸用它让持剑潜行触发一次挥手(use 状态机进出一轮)。⚠️ **举盾出手时 blocking 掉线 2~4 tick**(2026-09-17 实机,可能早于挥动进度起跳 2 tick):门控里的格挡读主包共享动画 `animation.ysm.java_input_state` 维护的锁存 `(variable.ysm_block_hold??query.blocking)`,只在仍潜行且持盾时桥接掉线;使用计时取 `((variable.ysm_use_hold??0)>0\|\|query.main_hand_item_use_duration>0)`(原始 query 与锁存取或)。⚠️ **物品使用类 query 不要写进动画骨骼通道**:2026-09-17 实机 `item_remaining_use_duration('main_hand',1.0)` 在骨骼通道里持剑不使用时也大于 0(接口求值为 0);锁存的"正在使用"改由共享控制器 `controller.animation.ysm.java_use_state` 在转移里判定后写 `variable.ysm_item_in_use` |

### 0.0 无 else 三元 `cond ? value`

Java geckolib 允许省略 else 分支(缺省 0),Java 包里到处是这种写法
(`ysm.head_yaw>0 ? -ysm.head_yaw`)。**作为算术子表达式时基岩动画通道不可靠**:
实测(2026-09-05 warden 拉弓)`-75+(条件?补偿)` 的整个通道退化,身体只剩 -75° 侧身、
没有按 head_yaw 回正,弓因此甩到视线左侧六十多度。同一串喂给
`QueryVariable.EvalMolangExpression` 却能正确算出 -25 —— **两套求值器行为不同**,
不能拿 API 求值结果证明动画通道也认。

形态差异是关键线索:内置 wine_fox 的 43 处裸三元全是**独立整串**形态
(`q.ground_speed<=2?-5`,整个通道值就是一个三元)且工作正常;出问题的是
**被算术运算包着**的形态。移植工具统一补成 `cond ? value : 0`
(`port_java_pack.NormalizeBareTernary`,语义完全等价,认与不认都安全);
`??` 空值合并与块形态 `cond ? { … }` 不动(后者补 `: 0` 反而非法)。
已落盘的产物用 `python devtools/fix_ported_controllers.py [包名]` 补跑。

### 0.1 物品 tag:Java 与基岩是两套命名,且基岩覆盖极稀疏

Java 的 `#minecraft:swords` 这类原版 tag **在基岩不存在**(基岩是单数 `minecraft:is_sword`),
`#tag` 条件原样搬过去就是**静默恒假** —— 动画一条不播、引擎不报任何错。
实测(2026-09-05 warden):悬浮臂状态机 `player.parallel_1/2` 的转移全吃这几个 tag,
于是永远停在"缓冲"态,持镐/斧/锹/锄的拳击姿态与持盾防御姿态全部消失。

| Java tag | 基岩内置 tag |
|------|------|
| `minecraft:swords` | `minecraft:is_sword` |
| `minecraft:axes` | `minecraft:is_axe` |
| `minecraft:pickaxes` | `minecraft:is_pickaxe` |
| `minecraft:shovels` | `minecraft:is_shovel` |
| `minecraft:hoes` | `minecraft:is_hoe` |
| `minecraft:tools` | `minecraft:is_tool` |
| 其余 `minecraft:*`(`planks`/`logs`/`boats`/`wool`…) | **无对应**,解析时告警 |
| 模组/自定义命名空间(`tacz:guns` 等) | 原样透传(基岩侧可由 addon 自行声明同名 tag) |

**更要紧的是:基岩的 tag 覆盖面远比 Java 稀疏,且认不认无法离线证实** ——
原版工具是引擎硬编码物品,整个游戏数据里搜不到一处 `is_pickaxe`/`is_sword` 声明
(只有 `apple.json` 的 `is_food` 与 `wolf.json` 的 `is_armor` 是数据驱动的)。
故成员闭合的工具类判定一律写成 **`tag || 具名列表`** 双保险
(`packParser._TAG_VANILLA_ITEMS`,六档材质 ID):tag 认就走 tag(兼容 addon 物品),
不认也有原版 ID 兜底。合成的唯一出口是 `packParser._ItemTagTest`,条件动画 /
挥击控制器 / 移植工具的 `ctrl.*` 展开三条路共用。

## 一、主包已同步(query.mod.ysm_* / 基线变量)

| Java | 主包提供 | 说明 |
|------|----------|------|
| `head_yaw` / `query.head_x_rotation` | 🔶 `query.mod.ysm_head_yaw` | 头相对身体偏航,度,已 clamp ±85(对齐 Java) |
| `head_pitch` / `query.head_y_rotation` | 🔶 `query.mod.ysm_head_pitch` | 俯仰,度,正=抬头 |
| `armor_value` | 🔶 `query.mod.ysm_armor_value` | 护甲值(0–30) |
| `food_level` | 🔶 `query.mod.ysm_food_level` | 饥饿值(0–20) |
| `input_vertical` / `input_horizontal` | 🔶 `query.mod.ysm_input_vertical` / `_horizontal` | 移动输入方向 [-1,1](Java 按位移方向算,主包按输入,近似) |
| `xxa` / `zza` | 🔶 同上两个输入变量 | 左右/前后输入分量;`yya` 置 0 |
| `is_close_eyes` | 🔶 `query.mod.ysm_is_close_eyes` | 4.5s 周期 0.25s 眨眼(对齐 Java;无 UUID 相位错开) |
| `on_ladder` | 🔶 `query.mod.ysm_is_on_ladder` | onClimbable |
| (爬梯三态) | 🔶 `query.mod.ysm_climbing_vector` | 攀爬垂直速度,配合 is_on_ladder |
| `ctrl.fly` | 🔶 `query.mod.ysm_is_flying` | 创造飞行(ModAttr 同步,远程玩家可用) |
| `ctrl.playing_extra_animation` | 🔶 `query.mod.ysm_wheel_anim` | 轮盘动画播放中:开播置 1,移动/动作或只播一遍的动画播完清 0 |
| `ctrl.carryon_is_princess` | 🔶 `query.mod.ysm_riding==5` | 被玩家抱起=骑乘玩家(riding 值表:马1 猪2 船3 矿车4 玩家5) |
| `rendering_in_paperdoll` / `rendering_in_inventory` | 🔶 `variable.is_paperdoll` | GUI/纸娃娃渲染中 |
| `person_view` | ≈ `(variable.is_paperdoll?2.0:(query.is_first_person?0.0:1.0))` | Java: 0一人称/1背面/2正面或GUI |

## 二、基岩原生对应

| Java | 替换 | 说明 |
|------|------|------|
| `is_sleep` | ✅ `query.is_sleeping` | |
| `is_sneak` | ≈ `query.is_sneaking` | Java 附带 onGround 判定 |
| `is_passenger` | ✅ `query.is_riding` | |
| `eye_in_water` | ≈ `query.is_in_water` | Java 为"眼部入水" |
| `hurt_time` / `query.hurt_time` | ✅ `query.hurt_time` | 均为 tick(10→0) |
| `time_delta` | ✅ `query.delta_time` | 秒;**作除数,不可置零** |
| `attack_time` | ✅ `(variable.attack_time*(1-(variable.ysm_swing_muted??0)))` | **同为原版挥手进度**(getAttackAnim 回卷插值,一次挥动升到 ≈1 再归零),不是攻击冷却。Java 使用物品期间攻击无效、根本没有这次挥动,基岩照常出手 → 使用中开始的挥动由主包输入状态静音,读作 0 |
| `swinging` | ≈ `((variable.attack_time*(1-(variable.ysm_swing_muted??0)))>0.0)` | 同上静音 |
| `swing_time` | ≈ `((variable.attack_time*(1-(variable.ysm_swing_muted??0)))*6)` | Java 一次挥击约 6 tick |
| `ground_speed2` | ≈ `((query.modified_move_speed>0.05)?query.modified_move_speed*1.9:0)` | 网易 `query.ground_speed` 实测帧间乱跳不可用,统一换平滑近似 + 静止死区(见"同名陷阱") |
| `delta_movement_length` | ≈ `math.sqrt(gs²+vs²)` 展开 | |
| `has_mainhand` / `has_offhand` | ✅ `query.is_item_equipped(0/1)` | |
| `has_helmet` 等 4 槽 | ≈ `query.is_item_name_any('slot.armor.head', <护甲物品枚举>)` | 原版全量护甲+南瓜/头颅;官方"换装设计"玩法靠它 |
| `has_elytra` | ✅ `query.is_item_name_any('slot.armor.chest','minecraft:elytra')` | |
| `mainhand/offhand_charged_crossbow` | ✅ `query.item_is_charged(0/1)` | |
| `ctrl.elytra_fly` | ✅ `query.is_gliding` | |
| `ctrl.jump` | ≈ `((variable.ysm_airborne??0)>0.5)` | Java 含下落。`variable.ysm_airborne` 是主包共享的 `animation.ysm.java_input_state`(animate 表首位,作者控制器与主链状态机同帧看到新值)逐帧更新的**帧间闩锁**:进入要求 `!is_on_ground&&!is_in_water&&(vertical_speed>0\|\|<-4)`(挡住走路时 `is_on_ground` 逐帧翻转),留在腾空只看 `!is_on_ground&&!is_in_water`;裸的 `!is_on_ground` 或带最高点死区的判据都会让作者状态机在最高点/落地各抽一次(凋灵娘实测) |
| `ctrl.sneak`(潜行**移动**) | ≈ `(query.is_sneaking&&query.modified_move_speed>0.05)` | 注意 sneak=移动、sneaking=兜底(Java L45-46,别记反) |
| `ctrl.climb` / `ctrl.climbing` | ✅ `query.is_crawling` ± 移动量 | **是爬行(匍匐)不是爬梯**(Pose==SWIMMING 落地) |
| `ctrl.swim` | ✅ `(query.swim_amount>0)` | |
| `ctrl.swim_stand` | ≈ `(query.is_in_water&&!query.is_swimming&&!query.is_on_ground)` | 踩水 |
| `ctrl.attacked` | ✅ `(query.hurt_time>0)` | |
| `ctrl.death` | ✅ `(query.death_ticks>0)` | |
| `ctrl.run` | ✅ `(!闩锁&&query.is_sprinting)` | Java 判 isSprinting,非速度阈值;"在地"一律取 `!((variable.ysm_airborne??0)>0.5)` |
| `ctrl.walk` / `ctrl.idle` | ≈ `!闩锁` + 移动量互斥表达式(0.05 阈值) | 见工具映射表 |
| `ctrl.hold/swing/use/armor(slot, pattern)` | ✅ 函数转换 | `$id`→is_item_name_any;`#tag`→equipped_item_any_tag;`:分类`→_CLASSIFY_TESTS;`:empty`→!is_item_equipped;swing 叠静音感知的挥动进度门、use 叠分手使用门(`packParser._SWING_ACTIVE_TEST` / `_USE_MAINHAND_GATE` / `_USE_OFFHAND_GATE`,带 `??` 回落原样落盘) |
| `frozen_ticks` | ❌(`frozen_alpha` 网易 3.9 二进制无) | 置零 |

## 三、物理 / 粒子 / 声音 / 骨骼函数

| Java | 替换策略 |
|------|----------|
| `second_order('k',input,f,z,r)` | ✅ 改写为 molang 状态积分(port_java_pack.PhysicsRewriter):调用处换成 `v.ysm_so_<键拼音>_y`,积分语句提到该表达式之前(纯表达式包成 `语句; return 表达式;`)。数学(t3ssel8r):k1=z/πf, k2=1/(2πf)², k3=rz/2πf,f∈[0,5]Hz z∈[0,1];帧间隔取 `q.life_time` 差分并封顶 0.1s(同帧多次求值 dt=0 恒等 → 多骨骼共用同键与 Java 一致);用 k2s=max(k2, dt²/2+dt·k1/2, dt·k1) 的稳定变体免掉 Java 的子步循环,60fps 逐帧与 Java 算法一致(test_port_java_pack.py)。键非字面量时退化为取第 2 参并告警 |
| `first_order('k',input,resp)` | ✅ 同上一阶滤波 `y += clamp(dt/resp,0,1)·(x-y)`,状态 `v.ysm_fo_<键>_y` |
| `perlin_noise()` | ❌ 置零;`math.sin` 组合近似 |
| `particle('id',x,y,z,dx,dy,dz,speed,count,life)` / `abs_particle()` | ✅ **timeline 里的调用**转成基岩动画原生 `particle_effects` 关键帧(port_java_pack.ConvertTimelineParticles):粒子 ID 按对照表换成原版基岩粒子(`flame`→`basic_flame_particle`、`soul_fire_flame`→`blue_flame_particle`、`smoke`→`basic_smoke_particle`…,无对照的原名直通并告警),位置 (x,y,z) 打成主几何**根骨骼的 locator**(模型空间 ×16,z 取反:Java 本地 +z 为前、基岩 -z 为前),效果键 `ysm_pt_<粒子>` 登记进 `netease.particle_effect` 由主包 `AddPlayerParticleEffect` 注册。"每 tick 脚本"动画(0 长度循环)里的调用于是每 tick 触发一次(Java 是每渲染帧,密度略低);dx/dy/dz/speed/count/life 由基岩粒子定义自带,无法逐参照搬;`abs_particle` 的世界绝对偏移近似为随身 locator;位置是表达式时落到原点 locator。**通道表达式里的调用**(无时间戳)仍置零。关键帧带 `bind_to_actor:false`:Java `ParticleSpawner` 把粒子生成在**世界坐标**(实体坐标 + 按身体朝向旋转的偏移)后交给粒子引擎,不随实体移动;基岩缺省 `true` 会让发射器跟着玩家跑(2026-09-17;该键在网易引擎的可用性由 `ysm_rp/animations/shared/particle_probe.animation.json` 探针文件确认,确认前已落盘的包不补,修复工具 `_BindTimelineParticles` 就绪) |
| `play_sound()` 族 | 置零(30 个 Java 包普查零调用)。动画 **`sound_effects` 关键帧**由移植工具自动三件套(`ConvertSoundKeyframes`/`WriteSoundResources`):ogg 从 `sounds/`(或 `files.sound_path`)拷到 `ysm_rp/sounds/ysm/<包>/`、`sound_definitions.json` 登记 `ysm.<包>.<安全名>`、`files.player.sound_effect` 注册 `[效果键, 定义名]`,关键帧 `effect` 改写为 `ysm_snd_<安全名>`;原版 ID(含 `:`)两边命名不同,定义名直通并告警 |
| `ctrl.ride('vehicle'\|'passenger', '$ID'\|'#tag')` | `vehicle`+`$ID` → `query.is_riding_any_entity_of_type('<换算ID>')`;`passenger`/`#tag` 置零告警(基岩无"谁骑在我身上"与实体 tag 判定) |
| `bone_rot/pos/scale('骨骼')` | 置零,**连同结构体成员访问 `.x/.y/.z` 一起**(Java 返回 Vec3 结构体;只置换调用会残留成 `0.0.x`,基岩整份文件拒载——2026-09-17 官方酒狐 03/06/10/15 头发摆动链)。可选等价:主包 `molang_bind_bones_list` 机制(粒子绑定采样 → `v.ysm_<骨骼>_rotX/rotY/rotZ/posX/posY/posZ`,每帧每骨骼有开销);`bone_pivot_abs` Java 侧本身是恒 null 残桩,无需对齐 |
| `bone_color/transparency/glow()` | 置零;整模型级可走渲染控制器 overlay/发光贴图 |
| `keyboard()` / `mouse()` | ❌ 置零(仅本机键盘,Java 侧远程玩家也看不到) |
| `sync()` / `defer()` / `dump_*()` / `mod_version()` | 剥除置零 |
| `fn.<名>(...)`(functions/*.molang 自定义函数) | ❌ 置零+告警:过程式脚本(args/return/循环)无表达式等价;`@player_init`/`@player_update`/`@sync`/`@defer` 挂接函数不移植 |
| `v.roaming.<名>`(持久化+网络同步域,上限 64) | 扁平化 `v.roaming_<名>`(普通会话级变量,自动初始化;`??默认值` 收进 netease.initialize)。丢持久化/同步——重进游戏重置 |

## 四、math 别名、缺失函数与同名不同义(2026-09-17 按 geckolib3 MathBinding 逐函数核对源码)

引擎二进制字符串表对 math **只作正面证据**(`devtools/data_engine_binary_tokens.json` 的 `_note`)。**2026-09-17 实机存在性探针**(`mcdkSelfTest.mcdk_test_molang_semantics_result` 的 existence 表):`ln/die_roll/die_roll_integer/random_integer/lerprotate/hermite_blend/min_angle/inverse_lerp/copy_sign/trunc` **存在**(当初按字符串表未命中判缺失是错的,移植工具 `_ENGINE_MATHS_PROBED` 已登记,不再告警);`math.e`、缓动函数(`ease_in_quad` 等,引擎枚举里有但语法未暴露)、`math.random` 三参数**不存在**(`expression is not valid`,整份文件拒载)。

| Java 写法 | 处理 |
|------|------|
| `math.random_integer(a,b)` / `math.randomi` | → `math.floor(math.random(a,b))`(Java `min+nextInt(max-min)` 上界不含、各整数等概率,floor 同分布;round 会让两端概率减半且含上界) |
| `math.lerprotate(a,b,t)` / `lerprotatee` | → 最短角插值展开 `a+(mod(mod(b-a,360)+540,360)-180)*t` |
| `math.min_angle(x)` | → `(mod(mod(x,360)+540,360)-180)` |
| `math.hermite(_blend)(t)` | → `((3-2*ceil(t))*ceil(t)*ceil(t))`:**按 Java 实现复刻**(`HermitBlend.java` 先 `ceil` 再 `floor(3c²-2c³)`,对整数 c 是 0/1/-4… 的阶跃而非平滑;早先展开成标准 hermite 与 Java 画面不符,2026-09-17 改) |
| **`math.acos/asin/atan(x)` / `math.atan2(y,x)`** | **同名不同义**:Java 直接返回 `Math.acos`/`Mth.atan2` 的**弧度**(只有 sin/cos 的入参按角度换算),基岩全套三角函数按角度 → 结果 `*0.017453292519943295`(π/180)换回 Java 口径。builtin 30 包 0 处使用,第三方包常见于 `atan2` 算朝向 |
| `math.e` | 基岩只有 `math.pi` → 常量 `2.718281828459045` |
| `math.roll` / `math.rolli` | Java 别名 → `math.die_roll` / `math.die_roll_integer`(引擎存在性另查,缺失则整份文件拒载——告警) |
| `math.random(a,b,c)` | Java 容忍并忽略第 3 参;基岩按参数个数拒载整份文件 → 截成 `math.random(a,b)` |
| `math.round(x)` | Java `Math.round` 半数**向上**(-2.5→-2),基岩半数远离零(实机 -2.5→-3) → `math.floor((x)+0.5)` |
| `math.die_roll*` / `math.ln` | 告警保留,需人工改写 |

## 五、无对应(置中性常量)

`weather`(0) `dimension_name`('minecraft:overworld') `biome_category`('') `fps`(**60**,防除零)
`air_supply`(**300**,防恒溺水) `block_light`/`sky_light`(**15**,防恒暗) `is_player`(1) `is_maid`(0)
`entity_type`('player') `texture_name`('',TODO:按皮肤表改写成 `query.mod.ysm_skin_index==N`)
`is_fishing`(0) `is_riptide`/`ctrl.riptide`(0,基岩无激流查询) `is_open_air`(0) `has_any_curios`(0)
`elytra_rot_x/y/z`(0) `arrow_count`/`stinger_count`(0) `yya`(0) `shoot_item_id`('')
`hit_target_id/type`('') `in_shield_block_cooldown`(0) `ladder_facing`(0) `frozen_ticks`(0)
`swinging_arm`(0=主手) `first_person_mod_hide`(0) 鹦鹉四件(0)
玩家属性取原版默认值:`attack_damage`(1) `attack_speed`(4) `attack_knockback`(0) `movement_speed`(0.1)
`knockback_resistance`(0) `luck`(0) `block_reach`(4.5) `entity_reach`(3) `swim_speed`(1)
`entity_gravity`(0.08) `step_height_addition`(0) `nametag_distance`(64)。
以上覆盖 YSMBinding 的**全部**值绑定(2026-09-16 按源码补齐),新名字才会落到"残余置零"告警。
模组联动 ctrl 变量按 `client/compat/*Compat.java` 里模组缺席时的默认绑定:字符串型
`parcool_state`/`slashblade_animation`/`swem_state`/`bcombat_attack_animation`/`iss_animation`/
`tac_gun_type`/`tac_gun_id` 给 `''`(`ctrl.parcool_state==''` 这类"没在跑酷"判据才保持为真),
`tac_hold_gun`/`tac_is_*`/`im_*`/`swem_is_ride`/`has_sophisticated_backpack` 给 0;
`--with-mods` 关闭时联动动画整键跳过。

## 六、词法与前缀(Java 解析器 com.elfmcys.ysm.molang 与基岩的差异,2026-09-16)

| Java 写法 | 处理 |
|------|------|
| `tlm.*`(车万女仆联动值) / `args.*`(自定义函数参数) | Java CustomMolangParser 注册 `ysm/ctrl/fn/tlm/args` 五个前缀,全部拦截:`tlm.x`/`args.x` 置零,`tlm.f()` 同 `fn.*` 置零告警(builtin 13_matured 48 处 `tlm.is_sitting`,漏一处整份文件作废) |
| `true` / `false` 关键字 | Java 词法分析器当关键字(=1/0);基岩是否认无证据(原版资源零使用)→ `1.0`/`0.0`(只改 JSON 字符串内、molang 单引号串外) |
| `(a)(b)` / `2(b)` | Java 解析器 LPAREN 分支 = **隐式乘法**;基岩当非法调用 → 补显式 `*` |
| `'注释';` 字符串字面量语句 | Java 空操作;timeline 里一律删除 |
| 优先级 | `=`(1) < `??`(1200) < `?:`/无 else `?`(1400) < `\|\|`(1600) < `&&`(1800) < `==`/`!=`(2000) < 比较(2200) < `+-`(2400) < `*/`(2600) < 一元 `!`/`-`(2800) < `->`(3000);同级左结合,三元右结合(MolangParserImpl)—— 与 C 一致,与 molangjs(Blockbench 预览,`&&` 松于 `\|\|`)**不同**,后者不是引擎证据 |
| 三元结合性 / 比较与逻辑优先级 / 负数除法 | Java 固定为上一行的 C 口径;基岩按资源包 `min_engine_version` 走 `MolangVersion` 分层(引擎头文件 `.ref/bedrock_rp_internals/01_molang/src/mc/molang/MolangVersion.h`:v5 `ConditionalOperatorAssociativity`、v6 `ComparisonAndLogicalOperatorPrecedence`、v7 `DivideByNegativeValue`)。**2026-09-17 实机定案**(`mcdkSelfTest.mcdk_test_molang_semantics_start/_result`,同一批式子在 EvalMolangExpression / animate 条件 / 资源包 timeline 三处各算一遍):**资源包文件(动画/控制器)走旧语义** —— `1 ? 0 : 1 ? 2 : 3` 得 3(三元左结合)、`1 \|\| 0 && 0` 得 0(&& 不比 \|\| 紧);`1 == 1 && 2 == 2`、`4 / -2` 与 Java 同值;animate 条件与 EvalMolangExpression 是新语义。移植工具 `molang_syntax.ExplicitPrecedence` 在可能分叉处补括号(三元嵌套、&& 与 \|\| 混写、等值与比较混写、?? 混写、三元条件/分支里的逻辑运算、除以负数),体检脚本对非 compat 残留报错。探针表另有 `tern_and_cond` / `tern_else_add` / `and_or_left` / `eq_rel_chain` / `neg_div_mul` 等细分项,下次重启后可给旧语义完整定性 |
| `1.0f` / `1e-5` 这类字面量 | Java 词法只认 `数字(.数字)`(`MolangLexerImpl`:无指数、无后缀),整个值解析失败按 0;基岩放行。Blockbench 不会导出这两种写法,移植工具不特殊处理 |
| 除法 / `math.mod` 的 0 与 NaN | Java 除零得 0、`a % 0` 得 NaN 再按 0 读;基岩实机 `1 / 0` 三处都得 0、`math.mod(-7, 3)` 得 -1(同 Java) |
| 除零 / 未定义变量 / 标识符大小写 | Java 除零得 0、未定义读 null(算术当 0)、标识符全部小写化;基岩变量大小写不敏感 —— 移植工具变量初始化按小写去重 |
| `YSM.head_yaw` / `Query.Position` / `Math.Sin` 等大写写法 | Java 词法整体小写,等价小写写法;基岩命名空间/查询名/变量名也不分大小写(引擎实测),但**映射表只认小写** —— 漏过映射的 `YSM.` 在基岩是"未知命名空间",整份文件拒载(官方酒狐 03/20 的 extra 动画)。移植第一步 `FoldJavaMolangCase`:Java 专有前缀与 query/math 连成员名小写,变量类只小写前缀 |
| 解析失败的表达式(作者笔误 `O`、多余右括号、`;` 残句) | Java `MolangParser.parseExpression` 捕获异常返回 0,**只作废这一个值**(关键帧分量 / timeline 条目 / 转移条件逐值解析);基岩**整份文件拒载**(文件里所有动画 ID 查无,官方酒狐 21 圣女光环、22 精灵举盾)。移植最后一步 `molang_syntax.GuardAnimationMolang`/`GuardControllerMolang` 按 Java 口径处置:分量/条件落 0,timeline/on_entry/控制器动画条目删除;判定口径是引擎解析器实测(见下表) |

**基岩 molang 解析器实测**(2026-09-17 客户端 `EvalMolangExpression` 逐条喂,与动画文件同一个解析器;
全表见 `devtools/test_port_java_pack.py` 的 `_ENGINE_ACCEPTED_MOLANG`/`_ENGINE_REJECTED_MOLANG`):

| 放行 | 拒载 |
|------|------|
| `1.0f`、`1e-5`、`1.5e+2`、`.5`、`1.`、`true`、`this`、`[1]` | `0.0.x`、`(0.0).x`、`1..5`、`0x10`、`1_000` |
| `V.x`、`Query.Position(0)`、`RETURN 1;`、`math.PI`(大小写不敏感) | `YSM.x`(未知命名空间)、裸标识符 `O`、`math . abs(1)`(路径里有空格) |
| `math.abs (1)`(名字与括号间可空格)、`q.life_time.x` | `q.life_time()`(空参数表)、`(1, 2)`、`f(1,, 3)`、`f(1,)` |
| `!!1`、`-!1`、`1--1`、`1 - -1`、`- 1` | `+1`(无一元加)、`--1`、`1 * --1`(一元负号不能连写) |
| `v.x = 1;`、`v.x = 1;;`、`1; 2;`、`return v.x = 1;` | `v.x = 1`、`return 1`(单表达式不能赋值/return)、`1; 2`、`v.x = 1; return v.x`(复杂表达式每句须以 `;` 结尾) |
| `(v.x = 1);`、`v.x = (v.y = 1);`、`v.x = 1 ? 2 : 3;`、`return 1 ? (v.x = 3) : v.x;`(得 3、赋值生效、假分支不执行) | `v.x = v.y = 1;`(连续赋值)、`1 ? v.x = 5 : 0;`(三元分支赋值)、`(v.x) = 1;`、`q.x = 1;`、`(v.x = 1)` / `1 ? (v.x = 5) : v.x`(**含 `=` 就是复杂表达式**,不带 `;` 拒载,2026-09-18 官方酒狐 17 号飞机动画整份作废) |
| `{v.x = 2;};`、`loop(2, {v.x = 1;});`、`1 ? {v.x=1;} : {v.x=2;};` | `{};`/`loop(0, {});`(空块)、`{return 1;}`、`loop(2, {v.x = 1;})`(块外缺 `;`)、`;1`(以空语句开头)、`return 1; v.x = 2;`(return 后还有语句) |
| `(1)(2)` 之外的全部括号写法 | `(1)(2)`、`2(3)`、`math.sin(1)(2)`(隐式乘法)、`math.sin(1))*(2)`、`((1)` |

**`??` 的左操作数必须以变量为根**(2026-09-18 实测,引擎原文 `found left-hand-side of ?? expression that isn't a
direct-variable reference`):按 Java 优先级、剥掉括号后,左侧是变量(`t.x ?? 3`、`(v.x) ?? 1`)、变量取负(`-v.x ?? 1`)、
含变量的四则运算(`1 + v.zz ?? 2`、`2 * v.a ?? 3`)放行;三元(`1 ? a : b ?? 5`、`(1 ? a : 2) ?? 9`)、常量(`3 ?? 1`、
`1 + 2 ?? 3`)、查询(`q.life_time ?? 1`)、函数调用(`math.abs(v.a) ?? 1`)、比较(`v.a > 1 ?? 4`)、逻辑运算(`v.a && 1 ?? 7`)拒载。
`EvalMolangExpression` 在带分号的语句里放行 `return (1 ? a : b) ?? 5;`,但资源包文件(旧版语义)连语句形式也拒载
(语义探针文件因这一条整份作废),`molang_syntax` 按资源包口径一律判非法。移植产物里的 `??` 只有工具生成的
"变量 ?? 常量"形态(作者原文的 `??` 移植期已降级)。

**已实机定标(2026-08)**:网易 `query.ground_speed` 走路中帧间 0↔60 乱跳(平均跳变 16.8)、
`query.yaw_speed` 0~290 间歇归零(平均跳变 21.7),两者均不可用——统一替换为
`(query.modified_move_speed*1.9)`(量纲近似 Java 行走 1.7)与 `query.mod.ysm_yaw_speed`
(主包差分+EMA 0.35 平滑,度/秒)。**仍待实机**:`query.mod.ysm_head_yaw` 的符号方向
(Java 取负+geckolib 轴向镜像,理论上与主包现符号一致,眼追踪若反向则主包统一翻转)。
