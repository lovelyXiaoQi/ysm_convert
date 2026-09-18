# Java 版 YSM 动画/动画控制器机制调查表(对照网易基岩版)

> 用途:Java ↔ 基岩迁移的机制级对照,以及后续 Java 版更新的跟踪基准。
> 数据来源:Java 源码子模块 `.ref/ysm-java-src`(2.6.x 全量源码)、官方 Wiki 子模块
> `.ref/ysm-java-wiki`、本仓库基岩实现;核心结论(主状态机、通道表、基线 animate、
> 条件前缀表)已逐行实读核对(2026-08)。
> 路径缩写:`java:` = `.ref/ysm-java-src/src/main/java/com/elfmcys/ysm/`;
> `wiki:` = `.ref/ysm-java-wiki/docs/notes/wiki/`;基岩侧为仓库相对路径。
> 姊妹文档:molang 查询逐条替换 → `ysm-java-molang-mapping.md`;包格式声明 → `ysm-json-pack-guide.md`。

---

## 0. 结论速览

**基岩版(网易)**:一切动画由两样东西驱动 ——

1. `animate` 列表:`(短名, molang 条件)`,条件为真即播,`"1"` 恒播;键指向动画或
   动画控制器,**共用一个扁平键空间**;
2. `animation_controllers` JSON 状态机:`states` / `transitions` / `blend_transition`,
   状态内 `animations` 支持 `{短名: 权重molang}`。

**Java 版**:**通道(Controller)体系**。玩家固定挂 24 路具名通道(`player.main`、
`player.hold_mainhand`…),按注册顺序逐帧处理,同骨骼**后者覆盖前者**。每路内部三层仲裁
(HybridAnimationController):

```
ysm.json 声明的同名基岩式动画控制器 (整通道接管)
  > molang 事件脚本 player_ctrl_<通道名> (返回 continue/pause/stop/bypass)
    > 硬编码 Predicate (Java if 链, 内置默认行为)
```

> java:geckolib3/core/controller/HybridAnimationController.java、CodedAnimationController.java:116-134

**核心判断:Java 版的目标形态本来就是 animation_controllers**(2.3.0 引入基岩控制器格式,
2.6.3 扩展到动态数量通道 + 子控制器)。硬编码 if 链只是"内置默认控制器",官方 wiki 明言:
"当你添加特定名称的动画控制器时,也就意味着先前硬编码的控制器被替换掉了"
(wiki:模型包制作/动画控制器.md)。所以移植不是"翻译代码",而是**把 Java 硬编码链视为一组
隐形基线控制器,与我方 `data/baseAnimations.py` + `ysm_rp/animation_controllers/` 基线
逐槽位对照**(见 §2)。

### 概念等价表

| Java 概念 | 基岩(网易)等价物 | 备注 |
|---|---|---|
| 一路通道(AnimationController) | animate 列表中的一条(挂控制器或动画) | 通道恒挂 ≈ animate 条件 `"1"`,播不播由内部决定 |
| 通道注册顺序(覆盖序) | animate 列表顺序(应用序)+ 非 parallel 动画的 `override_previous_animation` | Java 后者覆盖前者;基岩默认逐通道**相加**,靠 override 标志(重置自身骨骼)复刻覆盖,Java 模式 animate 表按 Java 通道顺序排列(packParser._OrderJavaAnimates) |
| 硬编码 Predicate | 我方基线控制器状态机 | §2 逐槽位对照 |
| ysm.json `files.player.animation_controllers` | 同名字段(本项目已支持列表混写) | Java 语义是"同名接管通道";基岩语义是"注册 + animate 挂载" |
| 通道 transition(0.1s / 0s) | 控制器状态 `blend_transition` | Java 按通道一刀切;基岩按状态各配(§9)。Java 模式转换包的主链走生成式状态机 `ysm_state`(各态 0.1s),直挂 animate 条目没有过渡 |
| `ysm.second_order/first_order` 物理函数(每键一份弹簧状态,逐帧积分) | 移植工具改写为 molang 状态变量积分(`v.ysm_so_<键>_y/yd/x/t`,port_java_pack.PhysicsRewriter) | 半隐式稳定变体,60fps 下与 Java 算法逐帧一致(devtools/test_port_java_pack.py 用产物表达式实跑对比);多骨骼引用同键共享一份状态(dt=0 恒等) |
| `PlayState.STOP` | 空状态 / 权重 0 | |
| `PlayState.PAUSE`(时间轴照走,不输出骨骼) | **无对应物** | 互斥需求改写成 molang 条件门(§2.4) |
| `ysm-builtin` 状态(还权硬编码) | ≈ 保留基线控制器对应状态原样 | |
| `ysm-entry-<x>` 子控制器(嵌套 ≤5 层) | **无对应物** | 迁移时需展平进父控制器 |
| molang 事件脚本 / 脚本控制器(`ctrl.set_animation` 等) | 空白通道(pre_*/post_*)的决策树脚本由移植工具展开成控制器(`devtools/script_controller.py`: 每个 return 一条规则, 首个命中者播放) | main/use/swing/parallel_N 与带计数器的脚本仍需手写 |
| 缺动画键回落内置 default 模型(AnimationStore fallback) | `java_default` 资源包动画基线(`port_java_pack.py <builtin/default> --baseline`), 解析器并进 Java 模式包的动画表; 移植期挥击/使用/主链状态机成员并入基线键, 基线并行族不并(Java 并行通道只认模型自有键) | 缺基线 = 拉弓/举盾/三叉戟等 57 条手持动画全无 |
| 条件动画命名(`$` / `#` / `:`) | 解析期合成 molang 条件(packParser) | §3 |
| 动画缺失回退默认模型动画库(wiki:动画概述) | 无隐式回退;`resourceIndex.FilterRenderEntries` 剔坏引用 | 本项目**刻意不回退**旧资源(设计红线) |

---

## 1. Java 玩家 24 路通道全表 ↔ 基岩挂载点

Java 注册顺序 = 处理顺序 = 覆盖顺序(java:client/controller/collections/PlayerControllerCollection.java:30-72,已实读核对)。
`transition` 单位是**秒**——参数名叫 ticks 但构造里 ×20(java:geckolib3/core/controller/transition/LinearBlendTransition.java:6-8),`0.1f` = 2 tick。

| # | Java 通道 | 过渡 | 内置判定 | 基岩现状挂载点 | 备注 |
|---|---|---|---|---|---|
| 1 | `player.pre_parallel_0..7` | 0 | 恒播 `pre_parallel<n>`(覆盖语义) | `pre_default` / `pre_default_tail` 控制器(ysm_rp/animation_controllers/compat/ysm_model.animation_controllers.json:365/390);Java 模式 animate 补 `"1"`(packParser.py:1646-1648) | 覆盖语义两边一致 |
| 2 | `player.parcool` | — | ParCool 模组 | 无 | 跑酷模组基岩无对应 |
| 3 | `player.vehicle` | 0.1 | 9 级 if 链(§2.3) | `riding_ctl` + `vehicle$` 合成条件 | |
| 4 | `player.pre_main[_*]` | 0 | 空(仅声明驱动) | `files.player.animation_controllers` + `animate` | Java 2.6.3 起前缀通道数量不限 |
| 5 | **`player.main`** | **0.1** | **19 槽位状态机(§2)** | move/idle/jump/sneak/attacked/sleep/fall/swim 一组基线控制器 | Java 一个通道的职责,基岩拆成 8+ 个控制器 |
| 6 | `player.post_main[_*]` | 0 | 空 | 同 #4 | |
| 7 | `player.pre_hold[_*]` | 0 | 空 | 同 #4 | |
| 8 | `player.hold_offhand` | 0.1 | `hold_offhand$/#/:` | 合成 animate 条件(§3) | |
| 9 | `player.hold_mainhand` | 0.1 | `hold_mainhand$/#/:` | 合成 animate 条件;基线另有 `controller_hold` 播共享持物姿态 | |
| 10 | `player.post_hold[_*]` | 0 | 空 | 同 #4 | |
| 11 | `player.fire` | 0 | 枪械开火(仅 TACZ/SWarfare 安装时创建) | tacz upbody `hold_fire`/`aim_fire` 状态(`v.tac.is_fire`,on_entry 自清) | |
| 12 | `player.pre_swing[_*]` | 0 | 空 | 同 #4 | |
| 13 | `player.swing` | **0** | `swing$/#/:` + 兜底 | `controller_swing`(fight 状态机,`v.attack_time` 驱动);Java 模式:生成式状态机 `ysm_swing`/`ysm_fp_swing`(idle + 每成员一对 `<键>`/`<键>__re`;主包共享动画维护挥动序号 `v.ysm_swing_serial`,序号变了就进入/切到孪生态 = 时钟回零;播完回 idle) | Java SwingPredicate **每次起挥都 indicateReload**:挥动中再挥会打断重播(引擎前半程再挥不重启挥动)。"播完才走"只针对没有新挥动的情形。⚠️ 直挂 animate 条目对不循环动画**不重放**(实机只播第一次),一次性通道必须走状态机 |
| 14 | `player.post_swing[_*]` | 0 | 空 | 同 #4 | |
| 15 | `player.pre_use[_*]` | 0 | 空 | 同 #4 | |
| 16 | `player.use` | 0.1 | `use_*$/#/:` + 兜底 | `controller_use`(17 状态)+ 合成条件(分手使用门,§2.4);Java 模式:生成式状态机 `ysm_use_mainhand`/`ysm_use_offhand`(触发期间停留) | Java 使用物品期间攻击无效;基岩出手会让格挡掉线,门控读共享动画的锁存(§2.4) |
| 17 | `player.post_use[_*]` | 0 | 空 | 同 #4 | |
| 18 | `player.passenger` | 0.1 | `passenger$/#`(实体骑在玩家头上) | **未实现**(可用 `query.has_rider` 近似) | ⚠️ 缺口 |
| 19 | `player.carry_on` | — | CarryOn 模组 | `carryon_ctl`(`query.mod.ysm_carryon` 1/2/3)+ 公主抱(`ysm_riding==5`) | ✅ |
| 20 | `player.cap` | 0 | 轮盘 extra 动画(§5) | `/playanimation` + 停止表达式 | Java 此通道无 Hybrid(纯硬编码) |
| 21 | `player.gui_hover` | 0 | 鼠标悬停(仅预览实体创建,guiOnly) | 无对应(§6) | |
| 22 | `player.gui_focus` | 0 | 选中(仅预览实体) | 无对应(§6) | |
| 23 | `player.parallel_0..7`(multi 模式可任意名) | 0 | 恒播 `parallel<n>`,**旋转加法混合**(仅内置通道;作者控制器接管后位移/旋转/缩放全覆盖,§4) | `default_ctl` 播 parallel1-4(**覆盖语义**);Java 模式 animate 补 `"1"` | ✅ 基岩默认即逐通道相加,parallel 通道动画不加 override 标志即得加法(§4) |
| 24 | `player.armor_<slot>`(head/chest/legs/feet/mainhand/offhand) | 0 | `<slot>$/#` + `<slot>:default` 兜底 | 合成条件(仅 head/chest/legs/feet 四槽) | mainhand/offhand 甲槽与 `:default` 兜底为缺口(wiki 也只文档化四槽) |

---

## 2. 主动画状态机对照(核心)

### 2.1 Java 侧机制

- 槽位注册:java:client/animation/AnimationRegister.java:22-52(**已逐行核对**)
- 执行:java:client/animation/predicate/PlayerMainPredicate.java:63-84 —— 5 档优先级
  (0=HIGHEST → 4=LOWEST,java:client/animation/Priority.java:4-8),档内按注册顺序线性扫,
  **首个命中即播,不再往下**
- `ctrl.*` 布尔变量族与这些条件官方保证完全一致(wiki:更新日志/2.4.1"修正部分 ctrl 变量,
  保证其与主动画播放条件完全相同")—— 即 Java 把状态机条件以 molang 形式公开了,
  写控制器时可直接引用
- 阈值 `MIN_SPEED = 0.05`(作用于 limb_swing_amount,肢体摆动插值量);
  垂直速度 = `20 * (pos.y - yo)`(AnimationRegister.java:62-64)

**前置短路**(优先级循环之前,PlayerMainPredicate.java:40-61):

| 条件 | 行为 | 基岩对应 |
|---|---|---|
| GUI 预览实体 | STOP | 预览走独立 AddActor* 通道 |
| ParCool 正在播 | STOP | — |
| **有存活载具** | **整个 main 通道 STOP,让位 vehicle 通道** | `controller_third_person.is_riding` 状态:停 move/in_air/hold/swim,保留视线/use/swing —— 同构 |
| 机械动力 skyhook 悬链 | 播 `parcool:ride_zipline` | — |

### 2.2 19 槽位对照表

| 槽位 | Java 进入条件(源码直译) | 级 | 循环 | 基岩现状挂载点(控制器.状态) | 差异与迁移注记 |
|---|---|---|---|---|---|
| `death` | `isDeadOrDying()` | 0 | **单次** | `third_person.dead`(`!q.is_alive`)播共享 die_anim/die_rot;模型自带 death 时解析期合成 `query.death_ticks>0` 直驱(packParser.py:128) | 基线不播模型 death 键,合成条件补齐 |
| `riptide` | `isAutoSpinAttack()` | 0 | 循环 | **无** | ❌ `query.is_auto_spin_attack` 网易实测 expression not valid,已知缺口 |
| `sleep` | `pose == SLEEPING` | 0 | 循环 | `sleep_ctl.get_in_bed`(`q.is_sleeping`)播 `sleep`;`third_person.sleeping` 冻结其余 | ✅ |
| `swim` | `isSwimming()` | 0 | 循环 | `controller_swim.swim`,权重 `v.swim_amount`(引擎泳姿插值 0~1) | Java 布尔切换,基岩权重渐变(更平滑) |
| `climb` | `pose == SWIMMING && abs(limb) > 0.05` | 0 | 循环 | `move_ctl.craw`(`is_crawling && speed > 0.1`)播 `climb` | **语义 = 原版趴地爬行(crawling),不是梯子!** wiki《玩家主动画》"活板门下"的措辞易误导,梯子是 ladder_* 三兄弟 |
| `climbing` | `pose == SWIMMING` | 0 | 循环 | `move_ctl.crawling`(`is_crawling && speed < 0.1`)播 `climbing` | 移动版注册在前先命中、静止版兜底 —— 两边同构(`XXX`=移动,`XXXing`=静止,sneak 同) |
| `ladder_up` | `onClimbable() && v > 0` | 0 | 循环 | 合成 `query.mod.ysm_is_on_ladder>0.5 && ysm_climbing_vector>0`(仅模型声明该动画时,packParser.py:129) | 变量由主包每 tick 下发;climbing_vector 带贴梯重力残值死区滤波(client/molangSystem.py:370-371) |
| `ladder_stillness` / `ladder_down` | 同上,`v == 0` / `v < 0` | 0 | 循环 | 同上 `==0` / `<0` | 2.2.1 新增的三槽位 |
| `fly` | 本地 `abilities.flying`;远端读同步 stateTracker | 1 | 循环 | `jump_ctl.up` 权重 `query.mod.ysm_is_flying`(服务端 actionType 34/35 广播,server/molangSystem.py:30-33) | 远端同步模式一致(Java stateTracker ↔ 网易 ModAttr 广播) |
| `elytra_fly` | `pose == FALL_FLYING && isFallFlying()` | 1 | 循环 | **基线不消费模型 elytra_fly 键**;滑翔姿态用共享 glide_pose(`target_anim.horizontal`);Java 模式合成 `query.is_gliding`(packParser.py:1026) | ⚠️ 模型自定义鞘翅动画在基线第三人称不生效 —— 需补 animate 条件或控制器状态。⚠️⚠️ **实体层旋转两边不同**(2026-09-18 实机定量):基岩在 `is_gliding` 时给整个模型加 `90 + pitch` 度俯仰(`ActorRenderData::getDamageOrGlidingXYRotation`,**绕脚底的纯旋转、不带平移**,骨骼矩阵里看不到);Java 的 `GeoReplacedEntityRenderer` 继承 `LivingEntityRenderer` 而非 `PlayerRenderer`,实体层一点不转 —— 姿态全写在模型的 `elytra_fly` 动画里(12_little `Root` 转 90 躺平)。两者叠加 = 水平飞行头朝下。修法:主几何外包 `ysm_glide_root` + 主包 `animation.ysm.java_glide_fix` 反向转(§9) |
| `swim_stand` | `isInWater() && !onGround()` | 2 | 循环 | `move_ctl.in_water` + `controller_swim.default`(权重 `speed>0.2 \|\| vertical!=0`) | ✅ |
| `attacked` | `hurtTime > 0` | 2 | **单次** | `attacked_ctl.flash`(`query.mod.ysm_hurt==1`,服务端受击事件 0.05s 脉冲);另有 `player.flash` 控制器用 on_entry 血量差分,两套并存 | Java 读实体字段(约 10 tick),基岩靠事件脉冲,时序略异;Java 模式合成 `query.hurt_time>0` 并由生成式 `ysm_attacked` 状态机驱动重放(直挂条目不重放) |
| `jump` | `!onGround() && !isInWater()` | 2 | 循环 | `jump_ctl.up`:`!is_on_ground && !is_in_water && vertical_speed>0`,退出 `<0` | ⚠️ **基岩 `is_on_ground` 走路时每秒翻转数次(实测 40 帧翻 10 次),裸抄 Java 条件会鬼畜**;Java 模式进入判据加 `(vs>0 \|\| vs<-4)` 门。⚠️⚠️ **但该死区会让跳跃触发两次**:`vertical_speed` 实测是格/秒、重力 32 格/秒²,最高点附近穿过 (0,-4) 约 3 tick,期间 jump 判据为假 → 低优先级 walk/idle 抢走 → 下落时 jump 再进一次(用户 2026-09-03 实机报告"跳一下触发一次、到最高处下降又触发一次")。修法=状态机**粘滞**(`_JAVA_STATE_HOLDS`):进入仍要上升沿/真下落,但整个腾空期 `!on_ground&&!in_water` 压制**更低优先级**成员与 empty 兜底,更高优先级(死亡/受击/入水/骑乘)照旧可打断 —— 进出条件不对称是状态机相对直挂 animate 的关键优势。注意 Java 的 jump 实为"腾空"(含下落) |
| `sneak` | `onGround() && pose==CROUCHING && abs(limb)>0.05` | 2 | 循环 | `sneak_ctl.sneak`(`is_sneaking && modified_move_speed>0.1`),并叠网易独有 `sneak_arm` | 阈值 0.05(limb)与 0.1(move_speed)量纲不同,不可直比 |
| `sneaking` | `onGround() && pose == CROUCHING` | 2 | 循环 | `sneak_ctl.sneaking`(`<=0.1`),叠 `sneaking_arm` | `*_arm` 是网易基线自有键(Java 无此概念,§7) |
| `run` | `onGround() && isSprinting()` | 3 | 循环 | `move_ctl.run`(`modified_move_speed > 0.87`) | **判据不同**:Java 按疾跑状态位,基岩按速度阈值 → 速度药水/潜行疾跑等边界行为不同 |
| `walk` | `onGround() && limb > 0.05` | 3 | 循环 | `move_ctl.default`,权重 `speed*1.5`(CarryOn 持物时切 carryon_walk/carryon_run) | |
| `idle` | 恒真兜底 | 4 | 循环 | `idle_ctl` 四态循环(still_0/1 ↔ idle_0/1,播 idle_timer/idle_0/idle_1)+ `bob` | ⚠️ **模型 `idle` 键基线不消费**(仅预览/纸娃娃 GUI/Java 模式);Java 直接播模型 idle,基线观感靠 idle_0/idle_1 随机小动作。Java 包不带 idle_0/idle_1/idle_timer → 这正是 Java 模式(按声明形态自动判定)要给主链换血的理由 |

### 2.3 载具通道 9 级 if 链 ↔ riding_ctl

Java(java:client/animation/predicate/VehiclePredicate.java:36-97,**顺序敏感**,全循环、过渡 0.1s):

```
SWEM 马术 → chair$(女仆坐垫) → vehicle$/#(条件动画) → Pig→ride_pig
→ Saddleable→ride → Boat→boat → CarryOn 被背→carryon:princess → 女仆兼容 → sit(兜底)
```

基岩:`riding_ctl` 按 `query.mod.ysm_riding` 数值分发(client/molangSystem.py:73-81)——
马/驴/骡=1(ride)、猪=2(ride_pig)、船=3(boat)、矿车=4(sit)、玩家=5(公主抱);
`vehicle$<实体ID>` 合成 `query.is_riding_any_entity_of_type('<换算ID>')`
(packParser.py:946-956,ID 经 JAVA_TO_BEDROCK_ENTITY_IDS 换算)。

| 差异 | 说明 |
|---|---|
| `chair$` | ⚠️ 未实现(车万女仆坐垫) |
| `vehicle#`(tag) | ⚠️ 未实现 —— 基岩实体无 forge tag 体系,需按包声明枚举展开 |
| Saddleable 泛化 | 基岩枚举 horse/donkey/mule=1;骆驼/炽足兽等 Java 走 ride,基岩 riding=0 无姿态 |
| `sit` 兜底 | ⚠️ Java 骑**任意**未知实体都有 sit;基岩仅矿车=4 → sit,未知实体停 default(只有 third_person.is_riding 冻结移动) |

### 2.4 手部兜底槽位与互斥

| 槽位 | Java | 基岩 |
|---|---|---|
| `use_mainhand` / `use_offhand`(裸名兜底) | UsePredicate 无条件动画命中时播(java:client/animation/predicate/UsePredicate.java:40/49) | Java 模式 `_CONDITION_FALLBACK_KEYS` 合成(门 `_USE_MAINHAND_GATE`/`_USE_OFFHAND_GATE` 且非任何已声明条件命中);基线模式:`use_righthand` 由 fight punch 状态播,`use_lefthand` **仅预览用** |
| `swing_hand` / `swing_offhand`(兜底) | SwingPredicate 兜底(SwingPredicate.java:66-67) | `swing_hand` Java 模式合成(门 `_SWING_ACTIVE_TEST`:静音感知的挥动进度 >0);`swing_offhand` ❌ 基岩无副手挥击信号 |

**互斥保护**:Java 持物动画在"该手正在挥动/使用"时返回 **PAUSE**(时间轴冻结、姿态仍输出,
Mainhand/OffhandPredicate.checkSwingAndUse)。基岩没有"冻结但仍输出"的语义 → 不模拟:
swing 族带挥动进度门、use 族带分手门(见下),hold 族照常播,靠
**通道顺序**(hold 在 swing/use 之前 → 后者按骨骼覆盖前者)自然让位。
残留差异:hold 动画写、而 swing/use 动画不写的骨骼,Java 会冻结在最后一帧,基岩仍继续走时间轴。

**分手门(2026-09-04)**:基岩 `query.is_using_item` **不分手**,而 Java UsePredicate 按
`getUsedItemHand()` 二选一只播一条。基岩副手槽只收盾/图腾/地图之类,**唯一会被"使用"的
副手物品是盾**,故照搬原版 `shield.entity.json` 的 pre_animation 判据分手:
`(query.blocking && 副手是盾)` = 副手在用,其余归主手。`use_offhand` 裸名兜底因此也能合成了。

**blocking 必须配持盾(2026-09-16 实机)**:不持盾时**按住潜行约 0.1 秒后 `query.blocking` 也变 1**
(手持下界合金剑实测)。早先主手门是 `(main_hand_item_use_duration>0 || query.blocking) && !副手在用`,
潜行就会让 use 状态机进出一轮 —— 持剑潜行"触发一次挥手攻击动画"。现主手门为
`(main_hand_item_use_duration>0 || (query.blocking && 主手是盾)) && !(query.blocking && 副手是盾)`,
副手门为 `query.blocking && 副手是盾`(`packParser._USE_MAINHAND_GATE/_USE_OFFHAND_GATE`);
修复工具 `_MigrateLegacyBlockingGates` 迁移已落盘的旧门控。

**使用中出手(2026-09-17 实机逐 tick)**:Java `Minecraft.handleKeybinds` 在 `isUsingItem` 时吞掉攻击
点击 —— 不挥手、不打断使用。基岩照常出手,且举盾出手时 `query.blocking` 掉 0 持续 2~4 tick,
**可能比 `variable.attack_time` 起跳早 2 tick**,期间 `query.is_sneaking` 保持 1。坚守者娘的格挡状态据此
退出、进持剑攻击态再回来(悬浮拳"重置再恢复")。现在上面的"格挡 / 使用计时 / 挥动进度"都读主包共享动画
`animation.ysm.java_input_state`(Java 模式恒挂 animate 表首位)逐帧维护的锁存:

| 变量 | 含义 |
|---|---|
| `variable.ysm_block_hold` | 持盾格挡。掉线时只要**仍潜行且持盾**,并且刚出过手(0.1s 内)或掉线不足 0.2s,就沿用上一帧;松开潜行立即失效(紧接着的出手是真挥动),盾被打掉 0.2s 后失效 |
| `variable.ysm_use_hold` | 主手正在使用后保持 0.1s,刚出过手时沿用上一帧。"正在使用"读 `variable.ysm_item_in_use`,由共享控制器 `controller.animation.ysm.java_use_state`(排在输入状态动画之前)在**转移**里判 `query.main_hand_item_use_duration`、进入状态时写入 —— **骨骼通道里查物品使用不可靠**(2026-09-17 实机:`item_remaining_use_duration('main_hand',1.0)` 在骨骼通道里持剑不使用时也大于 0,锁存恒为使用中,挥动全被静音、使用状态机卡在弓的成员态) |
| `variable.ysm_swing_muted` | 挥动起始时正处于使用中 → 这次挥动整次静音,`ysm.attack_time`/`swinging`/`ctrl.swing` 读作 0 |
| `variable.ysm_swing_serial` | 未静音的挥动每开始一次 +1,驱动挥击状态机重播(§1 #13) |

门控读取都带 `??` 回落到原始 query,共享动画缺席时退化为 v2 行为;这些变量不参与文件扫描补 0。
回归用例 `devtools/test_input_state_latch.py` 回放了实机轨迹 —— 旧的"掉线后宽限 0.1s"口径在轨迹上
第一下出手就漏(t=21 掉线、t=23 才起挥)。

### 2.5 wiki 28 槽位 ↔ 驱动通道归属(澄清)

wiki《玩家主动画》把 `main.animation.json` 里 28 个名字都叫"主动画",但**文件归属 ≠ 驱动通道**:

| 驱动通道 | 槽位 |
|---|---|
| player.main(19) | death riptide sleep swim climb climbing ladder_up/stillness/down fly elytra_fly swim_stand attacked jump sneak sneaking run walk idle |
| player.vehicle(4 + 条件) | boat ride ride_pig sit(+ vehicle$/chair$) |
| player.use / player.swing(4 + 条件) | use_mainhand use_offhand swing_hand swing_offhand(+ `:分类` 等) |
| 调试占位 | empty |

基岩 `STANDARD_MODEL_ANIM_KEYS`(23 键,packParser.py:138-143)与上表差集:
- 基岩**多**:`sneak_arm` `sneaking_arm` `sit` `parallel1` `parallel2`(网易基线概念);
- 基岩**少**:`death` `riptide` `ladder×3` `swing_hand` `swing_offhand` `use_offhand`
  (死亡/爬梯走按需合成条件,其余为缺口或兜底);
- 键名映射:`use_righthand→use_mainhand`、`use_lefthand→use_offhand`(NETEASE_TO_JAVA_ANIM_KEYS,packParser.py:151-154)。

---

## 3. 条件动画体系对照(`$` / `#` / `:`)

**Java 机制**:启动时**扫描模型全部动画名**,符合前缀的登记进对应 Condition 测试集
(java:client/animation/condition/ConditionManager.java:18-30,调用点 ModelRenderTargetAssembler.java:60-66),
逐帧匹配物品/实体。
**基岩机制**:解析期把同样的名字**合成 molang 条件**写进 animate,引擎逐帧求值,
零运行时脚本开销(packParser.py:861-970)。作者书写体验两边一致。

### 3.1 匹配符号(两边一致)

`$` 注册表 ID 精确 → `#` tag → `:` 内置分类;判定优先级恒为 **$ > # > :**
(Java:doIdTest→doTagTest→doExtraTest,ConditionalHold.java:72-85;
基岩用否定链复刻:`#` 条目附 `!(本通道所有$)`,`:` 条目附 `!(所有$) && !(所有#)`)。

**`$` 之上还有一层"代码级"判定**(hold 通道限定):Mainhand/OffhandPredicate 在整条
条件链之前先判 `charged_crossbow`(弩已上弦)与 `hold_mainhand:fishing`(鱼钩已抛出),
命中即 return —— 连 `$物品ID` 都压得住。基岩同样把这两项排在最前,并给本通道其余条目
附 `!(代码级判据)`。

**`:` 分类之间也要互斥**:Java 侧"一个物品只归一类"(InnerClassify 逐项 return)天然互斥,
基岩判据是**近似**、可能同时命中(上了弦的弩同时满足 `item_is_charged` 与 `name==crossbow`),
故低优先级分类显式否定**判定域真的重叠**的高优先级项(不重叠的不加,避免条件无谓膨胀)。
顺序表 `_CLASSIFY_PRIORITY`:代码级 → InnerClassify 链 → UseAnim 兜底;其中 `drink` 排在
`eat` 之前是刻意的(`eat` 走宽泛的 `is_food` tag,蜜/奶两边都可能算进去,Java 按 UseAnim 归 DRINK)。

### 3.2 前缀对照表

| Java 前缀 | 通道 | 基岩实现(packParser.py:74-86) | 状态 |
|---|---|---|---|
| `hold_mainhand` / `hold_offhand` | hold | `slot.weapon.mainhand/offhand`,无门控 | ✅ |
| `swing`(主手,**无 _mainhand 后缀**) | swing | 门 `_SWING_ACTIVE_TEST`(静音感知的挥动进度 >0,§2.4) | ✅ |
| `swing_offhand` | swing | **整族不合成**(解析期告警一次) | ❌ 基岩无副手挥击信号;挂在主手挥击上会在主手攻击时乱播,宁缺勿错 |
| `use_mainhand` / `use_offhand` | use | 分手使用门(读输入状态锁存,§2.4) | ✅ |
| `head` / `chest` / `legs`(**复数**)/ `feet` | armor | `slot.armor.*` | ✅ |
| `mainhand` / `offhand`(甲槽) | armor | — | ⚠️ 未实现(wiki 亦未文档化) |
| `vehicle` | vehicle | `query.is_riding_any_entity_of_type` | ✅($)/ ⚠️(#) |
| `passenger` | passenger | — | ⚠️ 未实现 |
| `chair`(女仆坐垫) | vehicle | — | ⚠️ 未实现 |
| `tac:<姿态>$<枪ID>` | 枪械 | TACZ 走 `v.tac.*` 变量 + tacz 控制器体系(另一套等价实现) | △ 机制不同但覆盖 |

### 3.3 `:` 分类清单对照

**Java** = InnerClassify 13 项(**有判定先后**:slashblade → sword → gohei → axe → pickaxe
→ shovel → hoe → shield → crossbow → bow → fishing_rod → spear → throwable_potion,
java:client/animation/condition/InnerClassify.java:22-66;每项 `instanceof` 或物品 tag 双通道命中)
+ UseAnim 枚举 9 项(eat drink block bow spear crossbow spyglass toot_horn brush,`none` 排除)
+ 特例(见下)。

**基岩** = `_CLASSIFY_TESTS` 19 项(packParser.py:90-110,已实读):
sword/axe/pickaxe/shovel/hoe/eat 走 tag(`minecraft:is_*`/`is_food`),
shield/block/bow/crossbow/fishing_rod/spear/spyglass/toot_horn/brush/throwable_potion/drink/fishing
走物品名枚举,charged_crossbow 走充能判定;另 `:empty` → `!query.is_item_equipped(手序)`(packParser.py:918)。

| Java 特例 | Java 依据 | 基岩状态 |
|---|---|---|
| `hold_*:empty` | 手上无物(ConditionalHold.java:21-22) | ✅ `:empty` |
| `hold_*:charged_crossbow` | 弩已上弦,**代码级判定, 排在条件链之前**(Mainhand/OffhandPredicate) | ✅ `是弩 && item_is_charged`(照抄 Java 的两个条件),并压住本通道其余条目 |
| `hold_mainhand:fishing`(**无副手版**) | `player.fishing != null` 鱼钩已抛出,同为代码级判定 | △ 按鱼竿物品名近似(判不到"已抛出") —— 需要一个"正在钓鱼"的运行期信号才能做准 |
| `<slot>:default` 甲槽兜底 | 任意护甲,优先级最低 | ⚠️ 未实现 |
| `slashblade` / `gohei` 分类 | 拔刀剑/女仆模组物品 | — 基岩无对应模组 |
| `lance` 分类(**2.6.5 新增**) | 长矛类 | ⚠️ 未实现 —— **Java 更新跟踪点** |
| 模组物品入分类 | instanceof + tag 双通道 | 仅 tag/名单;超纲物品解析期告警(packParser.py:966-969) |

### 3.4 基岩侧独有约束

- **动画 ID 不接受 `$ : #` 与大写**,且一个非法 ID 会废掉整份动画文件的注册 →
  条件名转义 `.id.` / `.tag.` / `.cls.` + 统一小写(EscapeConditionKey,packParser.py:798-818);
- **纸娃娃门**:合成条件一律前置 `!variable.is_paperdoll`(网易纸娃娃是无装备上下文的
  Custom 实体,物品类 query 在其上持续报错,靠 && 短路规避,packParser.py:115-120);
- Java"切换物品时持有动画从头重播"(wiki)= indicateReload 机制;基岩同名动画条件翻转
  即重播,天然等价;
- **animate 表的求值顺序**(`packParser._OrderJavaAnimates`,2026-09-17 定稿):带 override 的条件
  动画按 Java 通道注册序排(`_CONDITION_CHANNEL_ORDER`: hold_offhand → hold_mainhand → swing → use →
  carry_on,之后裸 parallel、armor),它们只能清空**排在前面**的条目;同一通道内只会有一条命中
  (条件互斥),通道之间错了就是"该压的没压住"(例:`use_mainhand:bow` 拉弓把手部定位骨骼放大到
  2 倍,必须压过 `hold_mainhand:bow` 的 1 倍)。**不带 override 的 pre/main/parallel 层反过来按
  Java 通道序倒排、放在表最前**:`player_parallel_*`(名字倒序)→ 骑乘条件 → `ysm_state` →
  `player_pre_*`(族序、名字倒序)→ 裸 `pre_parallelN`。基岩逐条目交错求值(轮到控制器才切状态、
  跑 on_entry,紧接着求它的动画权重),早层伴生读的占用变量由晚层 on_entry 写,晚层必须先求值;
  顺排时切状态那一帧早层读到旧值 —— 同一通道没人写或两边都写,就是凋灵娘持剑跑跳"闪一帧"
  (§9 逐通道覆盖行)。骑乘条件夹在 `ysm_state` 前是为了同键合并:条目表按键合并时位置取首次、
  值取末次,vehicle$ 键在主链里的版本带完整组互斥,必须后写。

---

## 4. 并行动画对照

| 维度 | Java | 基岩现状 |
|---|---|---|
| 通道数 | `pre_parallel0-7`(低,覆盖)+ `parallel0-7`(高,**旋转加法**)= 16 路;multi 模式还可声明任意名 `player.parallel_<x>`(数量无上限) | 基线 `default_ctl` 播 parallel1-4、`pre_default` 播 parallel0-4 + pre_parallel0-4(tail 变体到 parallel5);Java 模式:凡模型声明的 parallelN/pre_parallelN 一律 animate `"1"`(packParser.py:1646-1648) |
| 播放 | 恒播恒循环(ParallelPredicate 强制 LOOP,java:client/animation/predicate/ParallelPredicate.java:15-18) | animate `"1"` 等价 |
| **混合语义** | 每个 (骨骼, 通道) 由**最后处理的通道**决定(AnimationProcessor.tickAnimation:`apply` 直接 set)。**只有内置并行通道**(裸 `parallelN` 由 CodedAnimationController 播)旋转做加法(`@Deprecated` 历史特性);**作者控制器接管的通道 `blendRotation` 恒 false**(IAnimationController.blendRotation 注原文"如果使用动画控制器，那么将永远返回 false"),含 `player.parallel_N` —— 位移/旋转/缩放全是覆盖。同一状态内多条动画:位移/旋转加权求和,缩放按 `1+(s-1)w` 相乘 | **全部动画逐通道相加、缩放相乘**(2026-09-16 实机骨骼缩放探针:pre_parallel0 的 0 × death 的放大 = 0)。移植工具:内置并行通道不加 override(旋转相加一致);位移/缩放被它覆盖的早层通道直接删;**作者控制器状态**覆盖早层的 (骨骼, 通道) 由 `ApplyChannelOwnership` 用伴生动画 + 占用变量复刻(§9) ✅ |
| 停止 | 仅 molang 事件脚本(`player_ctrl_parallel_0` 返回 `ctrl.state_stop`) | animate 条件写任意表达式即可(反而更灵活) |
| 命名坑 | 动画名 `parallel0`(无下划线),控制器名 `player.parallel_0`(有下划线) | 同名约定沿用 |

**迁移注记**:基岩默认相加——Java 包里 idle 与 pre_parallel 同骨骼(尾摆)会相加成两倍
(实机:sahmet 尾巴过卷贴腿);持剑/持镰类控制器动画是**整套姿态**(sahmet 的 sword_walk 腿部数值与
walk 逐帧相同),与主链相加就是全身旋转翻倍;前置层用 `scale 0` 藏起的特效骨骼,晚层攻击动画
写 `scale 1` 放出来,基岩 0×1 永远不可见(坚守者娘持剑攻击特效缺失)。移植工具的分工:
条件动画/轮盘动画带 `override_previous_animation`;pre/main/parallel 三个域不带(标志与交叉淡化
互斥,且按骨骼整体清空),只有晚层**恒在播**时才在移植期静态删早层通道(逐通道覆盖的第 4 条; 恒播 pre 与常量
变体的精确折叠),其余(含主链压 pre 层、pre 层内部的门控/跨控制器覆盖)由 `ApplyChannelOwnership` 按状态动态让出(§9)。
Java 同一状态内的多条动画是加权相加(`BedrockAnimationController` 的 `fma`),与基岩相同,不算冲突。早先的两条静态删除
都已撤: 按"常驻 pre 与 idle 总在同播"删的 `ApplyJavaMainOverride`(走路/卡片预览不播 idle 时隐藏缩放漏出来, 大酒狐爱心/ZZZ),
与按"跨控制器即同播"去重的 `DedupPreLayer`(K 螺诺亚 pre_parallel3 的待机摆尾被 pre_main 走/跑动画删光, 站着尾巴僵直)。
主包排 animate 表时把带伴生的晚层宿主倒排在前(占用变量当帧生效),override 层按 Java 通道序排在后(§3.4)。wiki 推荐的护甲隐藏套路(并行动画把护甲组缩放置 0,护甲条件
动画改回 1)在基岩同样是缩放相乘,需要单一所有者或覆盖标志。

---

## 5. 轮盘动画(extra)对照

| 维度 | Java | 基岩 |
|---|---|---|
| 配置 | `properties.extra_animation`(LinkedHashMap 保序;数量/名称不限;`#分类` 子菜单 / `值#按钮` config_forms / `#return`) | 同,`packParser._BuildRoulette` 一条目一格(`#return` 占格,子轮盘点击时限 5 层);按钮格 = 表单页 + 页顶"播放动画"(Java 内外圈两个动作,基岩选择轮盘只有一个点击);radio labels 解析期转有序列表。界面逻辑 `client/ui/rouletteMenu.py`(纯函数,离线测试 `TestRouletteMenu`) |
| 表单 | ForgeSlider 按 step 取整、checkbox 写 1/0、radio 执行选项语句后 `init()` 整页重读;roaming 变量按模型哈希持久化并同步,非 roaming 赋值提交给周围玩家 | 同口径取值;全部表单变量按模型分桶存 ModAttr `extraVariable`(`config/rouletteVariables.py`,存档 + 同步,切回模型恢复),滑条拖动防抖 0.4s 后整桶提交 |
| 触发 | 8 个可绑按键 + 轮盘 UI → 网络同步 → `player.cap` 通道播(CapPredicate) | 轮盘 UI → Call 服务端 → `SetCommand("/playanimation @s <键> default 0 \"<停止条件>\"", 玩家)`(server/modelSystem.py:148-154;**第 4 参才是停止表达式**,第 2 参是回落态、第 3 参是淡出秒) |
| 循环 | 听动画 JSON 的 loop(不传 override) | 同(播放注册键,循环类型由动画文件决定) |
| **移动打断** | 客户端每 tick:输入冲量/跳跃/潜行键即停(PlayerMoveEvent.java:36-58);**移动中按键直接拒绝触发** | 停止表达式默认 `query.vertical_speed>0.3 \|\| query.ground_speed>0.3 \|\| q.is_sneaking`(packParser.py:61,语义对齐跳/跑/潜行);另 ysmWheelAnim 标志被任何 action/输入变化清零(server/molangSystem `OnYsmPlayerActionMolang` / `PlayerInputVectorChanged`) |
| 锁定 | 2.3.0 轮盘"锁定"按钮 + Alt L 快捷键,运行时切换 | `extra_stop_expression: ""` 关停止条件 ≈ 永锁,但粒度是**整包配置** ⚠️ 无运行时开关 |
| 播完自停 | PLAY_ONCE 播完 → 本地玩家回报服务端(CustomPlayerEntity.java:120-130) | /playanimation 单次动画播完自然结束,回落 `default` 态 |
| 重复触发重播 | shouldReset → indicateReload(CapPredicate.java:23-30) | 重发 /playanimation 即重播 |
| 可见性查询 | `ctrl.playing_extra_animation`(cap 通道非 IDLE) | `query.mod.ysm_wheel_anim`(ModAttr):轮盘动画指令执行成功时置 1(server/modelSystem `_MarkWheelAnimation`),移动/动作清 0,只播一遍的动画按资源索引里的 `animation_length` 到点清 0(循环与 hold_on_last_frame 与 Java 一样一直算"播放中");旧界面"关轮盘就置 1"已废弃 |
| 优先级 | cap 通道在 main/hold/swing/use 之后、parallel 之前(1.2.0 起低于 Parallel) | /playanimation 引擎层叠加,与控制器动画的覆盖关系由引擎决定(实测:高于控制器动画) |

---

## 6. GUI / 预览动画对照

| Java | 触发 | 基岩 |
|---|---|---|
| `properties.preview_animation`(任意名,2.2.1 前硬编码 idle) | 选择界面待机 | 预览注册表查找:同键取真实注册值,无则标准推导(packParser.py:1811-1822) |
| `hover` / `hover_fadeout` / `focus`(2.2.2;focus 限 1.20+) | 鼠标悬停/移出/选中 | ❌ 无对应 —— 网易 UI 不驱动模型 hover;预览动画用 `variable.ysm_preview / ysm_gui / ysm_show` 条件(client/render/previewRender.py:25/35/50) |
| `disable_preview_rotation`(2.3.0) | 取消预览旋转: 卡片缺省 yBodyRot 200(比正对多转 20°)、姿态栈绕 X -10°(略俯视), 置 true 才正对(`RenderUtil.renderModelInGui`) | 卡片纸娃娃 `init_rot_y` 200 / `init_rot_x` 10, 置 true 时 180 / 0(`client/ui/modelCard.ApplyCardRotation`; 展示动画是按各自取景摆的, 一律正对会歪) |
| `player.gui_hover/gui_focus` 通道仅在预览实体创建(guiOnly,PlayerControllerCollection.java:65-66/88-90) | | 预览实体走 AddActor* 系 API,与玩家渲染隔离 —— 同思路 |

---

## 7. 第一人称手臂对照

| 维度 | Java | 基岩 |
|---|---|---|
| 模型/动画库 | 独立实体 CustomFirstPersonArmEntity;`files.player.model.arm` + `files.player.animation.fp_arm`(2.5.0) | `AddPlayerGeometry("arm")` 恒注册(playerRender.SetYsmModelRes)+ `first_person_ysm_arm` 渲染控制器(条件含 `!query.is_item_equipped`,手持物品时整个关掉) |
| **摆位机制(2026-09-18 定案)** | **独立渲染域 + 纯平移**:在原版 `ItemInHandRenderer.renderPlayerArm` 的姿态栈上 `translate(∓0.25, 1.8, 0)` + `scale(-1,-1,1)`,再按 `RENDER_MODE_RIGHT/LEFT_ARM` 只画对应子树(`CustomFirstPersonArmRenderer.java:38-45`)。结合 `GeoBuilder.java:75-79` 的 bedrock→内部映射换算 = **arm 模型坐标 (x,y,z) ≡ 原版手臂骨架坐标系的 (x∓4, y−4.8, z)**,刚性挂在原版手臂骨骼上(原版那套挥击/换物/走路摆动整体套在它身上)。旁证:Java 自带 CC0 `default/models/arm.json` 右臂 cube 按此式移位后手部底端 12.03 ↔ 原版 rightArm cube 底 12 | 基岩**没有**引擎级第一人称手臂变换,摆位全靠原版 `root` 控制器 `first_person` 状态那几条动画:`base_pose` 写 **body** 的 `[俯仰, 偏航, 0]`、`empty_hand` 写 **rightarm** 的 `pos[13.5,-10,12]/rot[95,-45,115]`,外加 `swap_item`/`walk`/挥击控制器。故移植工具把 arm 几何**重建成"原版手臂骨架包装骨骼 + 移位后的 Java 子树"**(`port_java_pack.BuildFirstPersonArmGeometry`):`body[0,24,0]` → `rightArm[-5,22,0]` → Java 右臂子树(pivot/cube origin/cube pivot/locator 全体 `(-4,-4.8,0)`,自身 bind 旋转保留);左臂子树挂唯一名包装 `ysm_fp_leftarm[5,22,0]`,由主包 `animation.ysm.fp.java_arm` 的 `scale 0` 隐藏(基岩第一人称只渲染主手侧;**不能叫 leftArm** —— 会命中附着物锚点几何同名骨骼、把副手物品一起缩没)。⚠️ **撤销**早先的"嫁接主几何父链"(`GraftArmParentChain`):父链在第一人称是静态的,既补不出俯仰,还带进 Java 没有的静态旋转(08 斯塔·柏 `RightArm_size` 18°)与 `UpBody` 双偏航 |
| **视角跟随**(2026-09-18 逐包核对的历史 bug) | 手臂画在相机前的姿态栈里,天然随视角 | 靠原版 `base_pose` 旋转 `body` 实现。**旧产物 25/25 个包的 arm 几何都没有 `body` 骨骼** → base_pose 整条落空,只有主包 `animation.ysm.fp.body_follow` 补的 `UpBody` 偏航,**俯仰完全没有**:按原版数值算手臂位于相机下方 0.92 方块/前方 0.70 方块,抬头 30° 就掉出画面、抬头 45° 跑到相机背后 = 用户报的"第一人称手臂看不见"。重建后 `body` 包装骨骼接住 base_pose,俯仰偏航与原版/Java 一致;Java 模式包同时停掉那三条旧版差量(`arm_offset` 的 `[-4,-4,-5]`、`walk_sway`、`breathing`,见 `playerRender._FP_JAVA_ARM_ANIMATIONS`) |
| 通道 | 仅 3 类(FPArmControllerCollection.java:26-32):`fp.arm.misc`(**EmptyPredicate,硬编码什么都不播**)、`fp.arm.parallel_0-7`(恒播加法)、`fp.arm.armor_<slot>` | 同样只有这三路:`_BuildFpArmAnimates` 只合成 **parallel 恒开 + 甲槽条件**(`v.is_first_person&&!q.is_spectator&&!q.is_item_equipped(0)` 门——空手门与渲染控制器同源:手持物品时模型手臂不渲染,再播只会把与之共用骨骼名的附着物锚点带偏,见 §7.1);`arm` 文件的 hold/swing/use 条件动画只进主域(第三人称),FP 域仅注册动画本体供手写引用 —— **2026-09-04 修正**:早先也在 FP 域合成了这些条件,而第一人称手臂只在空手时可见,手持物品时它们只是在挪一条看不见的手臂,并连带把 arm 几何的 `rightItem` 挪走/缩放掉(第一人称弓变大、盾不显示的真凶,见 §7.1)。基线另有整组 first_person_* 共享动画/控制器 |
| **硬编码状态机** | **没有** —— 第一人称一切动作逻辑靠模型作者自己写控制器或 molang 脚本 | 基线反而更全(root.first_person 状态 + fp jump/fall/land 控制器) |
| 骨骼约束 | 仅 `LeftArm`/`RightArm` **子树**生效(子树外的组"既不会渲染,也无法应用动画" —— wiki《第一人称动画》);`Background` 组不支持动画;**手持物品时不显示 fp 动画**(机制:Forge `RenderArmEvent` 只在原版**空手臂**渲染路径上触发,手持物品走 `renderArmWithItem`,模型手臂根本不参与 —— ReplacePlayerHandRenderEvent) | `first_person_ysm_arm` 的 `part_visibility` 已改成**全放行** `{"*": true}`(2026-09-18):旧的 `Right*`/`right*` 前缀白名单按骨骼名过滤,而 Java 渲染整个 `RightArm` 子树 —— 官方酒狐 22 个包里 **8 个**因此在第一人称掉件(01 大正女仆 / 19 酒尾狐的大臂在 `main2`、06 汉服的手在 `shou3`、18 花嫁的 `HandBand`/`legBand*`、15 K螺诺亚的 `ysmGlow*`、10/11/22 的零件)。重建后 arm 几何里只有手臂子树,左臂靠包装骨骼 `scale 0` 隐藏,子树外的骨骼一并挂到隐藏包装下(与 Java 的"不渲染不驱动"同语义)。未声明 `model.arm` 的包("arm" 键回落主几何 = 整个身体)仍走旧白名单版控制器 `first_person_ysm_arm_main`。⚠️ 残留差异:Java 手持物品时不画手也不画原版手臂(`renderArmWithItem` 只在空手那一支画),基岩同样只剩物品 —— 两边一致 |
| `*_arm` 命名 | **不存在此槽位约定**(全库仅 fp_arm 文件键/swinging_arm 变量) | `sneak_arm`/`sneaking_arm` 是**网易基线独有键**(sneak_ctl 内叠加,作用于**第三人称**) —— 勿当成 Java 概念 |
| roaming 共享 | 主体 → fp 手臂复制 roaming 结构(CustomFirstPersonArmEntity.java:104-108) | 同一玩家实体,变量天然共享 |

### 7.1 第一人称的**手持物品**(基岩专有问题)

Java 第一人称的手持物由原版 `ItemInHandRenderer` 画在屏幕空间,与玩家模型无关 —— **没有对应物**。
基岩不是:第一人称手持物走的是与第三人称同一套 attachable 绑定,只是换了几何。因此:

| 环节 | 基岩机制 | 移植要求 |
|---|---|---|
| 绑定 | 弓/弩/盾/三叉戟/望远镜/重锤是 **attachable**:或者几何骨骼直接叫 `rightitem`(原版 `bow.geo.json`),或者写 `binding: q.item_slot_to_bone_name(c.item_slot)`(`shield.geo.json`);普通物品由引擎画在同名骨骼上 | 主几何必须有 `rightItem`/`leftItem`(第三人称缺了就是"弓弩盾不显示、普通物品掉回实体原点");第一人称的手持物绑在原版体型锚点上,不再依赖 arm 几何的这两根骨骼(移植工具 `_HELD_ITEM_GEOMETRIES` 两份几何都补,只为同构) |
| 摆位 | 原版 attachable 动画自带 `c.is_first_person ? … : …` 分支,数值按**原版几何**标定(`rightItem` pivot `[-6,15,1]`,父 `rightArm` pivot `[-5,22,0]`);网易原版 `animation.player.first_person.empty_hand` 自己就用 `q.get_default_bone_pivot` 把 `rightitem` 归一到"手臂 pivot 下方 7 格" | **锚点换骨架**(对齐 CSM,2026-09-04):第一人称下把 `default` 几何键换成原版体型 `geometry.default_steve`(`playerRender.ApplyItemAnchorGeometry`——装载时按当前视角初始化,`PerspChangeClientEvent` 时切换),本体渲染走独占 `ysm` 键不受影响;资源包**不覆盖**任何 `animation.player.first_person.*` 原版 ID,原版第一人称动画原样驱动锚点骨架 → 任何附着物/普通手持物都落在原版位置。例外是 `first_person_empty_hand` 键:`q.get_default_bone_pivot` 实机对部分模型读到 `ysm` 键上的模型主几何(2026-09-18,07 JK 酒狐两手物品骨骼偏 [0,6,1]),新版模型把该键改挂 `animation.ysm.fp.empty_hand`(手臂摆位同原版、物品归一按 default_steve 写成常量 [0,0,-1])。模型手臂的额外摆位(`animation.ysm.fp.*`:空手臂差量 `[-4,-4,-5]` + 设置里的 `empty_hand_x/y/z`、`UpBody` 偏航跟随、走路摆臂、呼吸起伏)全部带主手空手门 `!q.is_item_equipped(0)`——锚点骨架与模型手臂共用 `rightarm` 等骨骼名(引擎匹配不分大小写),手持物品时锚点只看到原版数值。旧版 py 副包不换锚点(其本体仍由原版 `third_person` 控制器渲染 `default` 键),只共享上述空手门动画 |
| 可见性 | `part_visibility` 只管骨骼自身的 cube,**不影响 attachable / 手持物渲染**(原版第一人称 `{"*": false}` 照样出物品) | 我方 `first_person_ysm_arm` 的空手门(`Right*` 仅空手可见)不会挡住物品 |

参考实现:CSM(另一款基岩模型附加包)走的就是这条路——本体渲染在独占的 `csm_default` 键,
`default` 键进第一人称时换成 `geometry.csm.default_steve`(`csm_client_system.on_persp_change_client_event`,
离开第一人称换回模型几何),第一人称手臂控制器带 `!q.is_item_equipped`,模型手臂的额外摆位
(`animation.csm.player.first_person.hand.base`)用 `* !v.ysm.has_mainhand` 门;它的
`right_item_fix`/`bow_fix`/`crossbow_fix` 只作用于**第三人称**(`controller.animation.csm.fix`
挂在 `!v.is_first_person` 域)。本包同构,细节见 `playerRender.py` 锚点注;第三人称修正见 §7.2。

### 7.2 第三人称的**手持物品摆位**(对齐 CSM,2026-09-17)

| 环节 | Java | 基岩(本包) |
|---|---|---|
| 挂点 | `CustomPlayerItemInHandLayer`:手部定位骨骼(`RightHandLocator`/`LeftHandLocator`)变换之后平移 `(0,-1/16,-0.1)`、绕 X 转 -90°,再按物品模型的 `THIRD_PERSON_*_HAND` 显示变换画物品 | 引擎把物品画在 `rightItem`/`leftItem` 骨骼上,套基岩自己的持物变换(attachable 另有 `c.is_first_person` 分支)。移植工具把**主几何**物品骨骼的 pivot 放在定位骨骼上(`port_java_pack._HELD_ITEM_FORWARDS`),与 CSM、作者自制的基岩版凋灵娘(`.ref/bedrock_wither` 的 `ys_wither.json`)一致 —— 定位骨骼的缩放/旋转直接作用在物品原点上,不会被 pivot 偏移放大成位移 |
| 按物品类别修正 | 物品模型的显示变换因物品而异 | 两套持物变换没法解析等价,取 CSM 实调的常量(`rp/animations/csm.fix.animation.json`):工具类手持物(三叉戟/弓/弩/望远镜/盾/刷子/钓竿/剪刀/打火石/山羊角/重锤,以及剑/斧/镐/锹/锄 tag)位移 `[0,2,1]`,其余非挂载物 `[0,1,2]`,挂载物(attachable)不叠;主手弓另叠位移 `[0,2.5,-1]`、旋转 `[0,0,4.5]`,主手弩另叠位移 `[-0.5,0.5,1.5]`、旋转 `[0,3,-3.25]`。动画在 `ysm_rp/animations/java_mode/item_fix.animation.json`,主包 `packParser._JavaItemFixAnimates` 在 Java 模式注册(第三人称双门,排在头部跟踪之前) |
| 物品判定 | — | 有没有物品、工具类名单(`is_item_equipped`/`is_item_name_any`/`equipped_item_any_tag`)放在 animate 条件里 —— 骨骼通道里 `is_item_name_any` 不可用;**挂载物判定照 CSM 留在骨骼通道**(`query.equipped_item_is_attachable('main_hand') ? 0 : 2`):2026-09-17 实机 `EvalMolangExpression` 对主手弓(原版 attachable,`data/definitions/attachables/bow.json`)求值恒 0,这个查询依赖渲染上下文,挪去别处没有证据可用,照抄 CSM 的求值位置它调出的常量才成立 |
| 旧产物 | — | 2026-09-17 之前主几何物品骨骼前推 z+1:凋灵娘拉弓把定位骨骼放大到 2 倍时,物品被一并往前甩 2 格。修复工具 `MigrateHeldItemBones` 只迁移**形状完全等于生成物**的骨骼(字段、父骨骼、拴绳点、pivot 都对得上),手写的不动;arm 几何(第一人称,§7.1)仍是 z+1 |

注意 Java 的 `arm.animation.json` 是**第三人称**的手部条件动画文件(`ModelRenderTargetLoader`:只有 `fp_arm`
进第一人称动画集,`arm` 与 `main` 同进主动画集;wiki《手部条件动画》"优先级高于主动画")。凋灵娘/坚守者娘/
萨赫梅特的 `hold_mainhand:bow`、`use_mainhand:bow`、`use_mainhand:crossbow` 都只写在 arm 文件里,主包按主域短键
注册(`_LoadArmAnimations`),第三人称拉弓/装填看到的就是它们。

---

## 8. 动画文件字段支持矩阵

Java 解析:java:format/parser/pojo/animation/Animation.java;运行:内嵌魔改 geckolib3。
**"pojo 解析了" ≠ "运行时生效"**,矩阵如下(移植时死字段两边都别依赖):

| 字段 | Java 运行时 | 基岩(网易) | 迁移注记 |
|---|---|---|---|
| `loop` 缺省 | **PLAY_ONCE**(LoopType.fromJson)：播到长度即"播完"(判据当帧成立)，随后 3 tick 尾过渡从末姿态淡到 0，再转空闲、**不再写任何通道**(AnimationPlayer.process/setupEndingTransition；2026-09-17 源码核实，早先记成"保持末帧直到控制器切走"是错的) | 单次，播完**立即不再作用于骨骼** | ❌ **不一致**。① 挂在控制器状态里、靠 `q.all_animations_finished` 出态时，动画停作用的那一刻转移还没生效 → 骨骼掉回底层姿态**一帧**（实机 2026-09-05 warden 持剑攻击 punch_left / punch_right 收回瞬间闪一下；Java 由尾过渡接住）。修法 `port_java_pack.HoldControllerOneShots`：**控制器状态引用的**一次性动画统一改 `hold_on_last_frame`（不影响出态）。② 但状态**停留**时 Java 0.15s 内收回姿态、补了 hold 的基岩会一直定格（实机 2026-09-17 萨赫梅特连段中切换物品：连段_N 出口全要求持剑，Java 攻击姿态自动收回，基岩卡住）。修法 `FadeControllerOneShots`（排在补 hold 之前）：作者状态里这类条目乘淡出权重 `math.clamp(1-((query.life_time-(variable.ysm_t0_<控制器>??0))-L)/0.15,0.0001,1)`，状态 on_entry 首条写计时；逐通道覆盖把 `(t<L+0.15)` 并进该条目的写入判据（逐帧项），淡出结束后早层伴生收回通道，与 Java 转空闲一致；带 override 的下限取精确 0 并由 RewriteFinishedQueries 改计时判据。显式 `hold_on_last_frame`（凋灵娘 攻击A/B/C）是作者要的保持，Java 同样定格，不淡出。直挂 animate 条目的动画不适用（靠播放条件去留）。⚠️ 未复刻：一次性通道使用成员触发期间一直保持（兜底 `use_mainhand`/`use_offhand` 在三个参考包里都是 PLAY_ONCE） |
| `loop: true/false/"HOLD_ON_LAST_FRAME"` | ✅ 三态 | ✅ 三态 | 一致 |
| loop 由谁定 | 主动画槽位:代码强制(death/attacked 单次其余循环);手部动画 formatVer≥19 起听 JSON;基岩控制器通道/轮盘:听 JSON | 一律听 JSON;移植工具按 Java 强制语义改写主链成员(port_java_pack.ApplyJavaLoopSemantics:成员强制 `true`,death/attacked 与未写循环的主手挥击族改 `hold_on_last_frame` 供淡出) | ⚠️ Java 包常把 sleep/sit/boat 写成不循环(BlockBench 默认),不改写基岩会播一遍就停 |
| `blend_weight` | ✅ molang 逐帧求值(AnimationPlayer.java:318) | ✅(controller `animations` 权重表达式) | 一致 |
| `anim_time_update` / `start_delay` / `loop_delay` | ❌ 解析后丢弃(AnimationBuilder.toProto 不搬) | 引擎按原版语义支持 | ⚠️ **反向差异**:Java 包写了≈没写;搬到基岩会突然生效。移植工具一律删掉(`DropJavaIgnoredAnimationFields`) |
| `loop` 取值形态 | 布尔;字符串大小写不敏感认 `true/loop`、`false/play_once`、`hold_on_last_frame`;其他值(数字/未知串)一律 PLAY_ONCE(LoopType.fromJson) | 只认布尔与小写 `hold_on_last_frame` | 移植工具归一(`NormalizeLoopField`) |
| `animation_length` 缺省 | = 最后一个带时间戳的**骨骼**关键帧(timeline/音效帧不算);一个都没有 → **Float.MAX_VALUE**:永不结束、LOOP 永不回绕、timeline **只跑一次**、`all_animations_finished` 永假(pojo Animation.calculateLength) | 按关键帧推算;推成 0 长度时立即结束/起播触发一次 timeline | ❌ **不一致**。移植工具显式写出:有关键帧取末帧时间,没有则写 1e6 秒的"无限长"(`ApplyJavaImpliedLength`,封尾/tick 化都跳过它)。实例:凋灵娘轮盘键 `voice_set_1..23` 的 timeline 靠它只设一次 `v.voice_short=N`,早先误当"每 tick 脚本"改成 0.05s 循环,变量每 tick 被顶回去,语音状态 `on_exit` 归零后又重进 → 站着不动反复播语音 |
| 关键帧 `[x]` 单元素数组 / 只写 `pre` / `{vector, easing}` 形态 | 标量广播 / `post=pre`(contiguous) / geckolib easing 形态,easing 只认 linear/catmullrom(pojo BoneKeyFrameList) | 无证据 / 无证据 / 不认识的键作废整份文件 | 移植工具展开为三分量 / 补同值 `post` / 转成 `pre`+`post`+`lerp_mode`(`_NormalizeKeyframeDict`);`lerp_mode` 大小写同样归一 |
| 关键帧**只写 `post`** | `post` 同时当 `pre`(JsonKeyFrameUtils"没错，post 赋给 pre") | ❌ **线性入段取通道默认值**(scale 1、rotation/position 0)。2026-09-16 实机骨骼缩放探针:凋灵娘火焰精灵帧 `0.25: {pre 1, post 0}` → 收尾帧 `0.5: {"post": [0,0,0], "lerp_mode": "linear"}` 这一段从 0 线性涨到 1(首帧值是 0,排除"回绕到首帧"),death 的 Root 同样涨回 1 —— 用户看到的"莫名其妙的 scale 动画"。Blockbench 导出的平滑帧 `{"post", "lerp_mode": "catmullrom"}` 入段是样条,不在此列 | 移植工具所有只写 post 的帧补同值 `pre`,收尾帧 pre/post 都写(`_NormalizeKeyframeDict` / `SealAnimationTails`);体检 `validate_rp_animations.py` 对"线性入段 + 只写 post + 非默认值"报错(compat 资源降为警告) |
| catmullrom 的**分段归属** | 段 [i, i+1] **任一端** catmullrom 即走样条,控制点 i-1..i+2 端点钳位(BoneKeyFrameProcessor,"和 BlockBench 保持一致") | 关键帧 i 的 `lerp_mode` 只管**出边** [i, i+1];加载时为 catmullrom 帧预计算三次样条,控制点 i-1..i+2 **必须全是常量**,否则 Debug_Log 报 `Precomputed cubic interpolation requires keyframes have constant data`、整段退化(2026-09-16:该窗口与日志里 9 条报错动画逐通道吻合,旧的 ±1 邻域一条都抓不到) | 移植期换算 `b[i] = cm[i] or cm[i+1]`,末帧一律 linear(`ApplyJavaCatmullSegments`,非幂等,只在移植期跑);随后窗口内有表达式的 catmullrom 降为 linear(`NormalizeExpressionKeyframes`,幂等) |
| 首个关键帧**之前** | 恒输出首帧 `pre`(EasingType.buildTransitionKeyFrame) | 无证据(Blockbench 预览按循环回绕) | 首帧晚于 0 的通道在 0.0 补一帧同值线性帧(`SealAnimationHeads`,幂等) |
| `override_previous_animation` | ❌ 恒 true | 引擎支持 | 同上 |
| 关键帧 `pre`/`post`、单标量广播、molang 关键帧 | ✅ | ✅ | 一致 |
| `lerp_mode` | 仅 linear/catmullrom,其余降级 linear(EasingType.java:16-28) | 同两种 | 一致;GeckoLib 花哨 easing 在 Java YSM 也是降级,勿依赖 |
| `timeline` 指令帧 | ✅(molang 脚本逐条执行) | ✅(引擎 timeline) | 两边都有;脚本里的 Java 自定义函数需按 molang 映射表改写 |
| `sound_effects`(动画级) | 源码快照里编译管线丢弃(pojo 读了、AnimationBuilder.toProto 不搬,运行时 AnimationProtoMapper 传空);但 wiki《添加音频》明言 2.3.0+ 可在 Blockbench 音频关键帧里写 `sounds/` 下的文件名或原版 ID —— **以 wiki 为准视作有效** | ✅(AddPlayerSoundEffect 注册后可引用) | 移植工具三件套(`ConvertSoundKeyframes` / `WriteSoundResources`):ogg 拷到 `ysm_rp/sounds/ysm/<包>/`、`sounds/sound_definitions.json` 登记 `ysm.<包>.<安全名>`、`files.player.sound_effect` 注册 `[效果键, 定义名]`,关键帧 `effect` 改写为效果键 `ysm_snd_<安全名>`;音频目录认 `files.sound_path` / `files.player.sound_path`(缺省 `sounds`);原版 ID(含 `:`)两边命名不同,定义名直通并告警。三个参考包共 26 条轮盘动画带音频(凋灵娘 24 个 ogg) |
| `particle_effects`(动画级) | ❌ 完全不解析(走 molang `particle()`) | ✅(基线控制器就在用 splash/hurt/sleeping) | 反向差异:基岩可用 |
| 控制器 `blend_via_shortest_path` | ❌ 解析不执行 —— 但 Java 的跨态过渡**本来就是四元数 nlerp**(BlendBoneAnimationQueue.pollRotationPoint → MathUtil.lerpRotationValues),天然最短路径 | ✅ 执行(move_ctl crawling/craw、tacz climbing 在用);缺省逐分量插值 | 移植工具给带正 blend 的状态统一补 `true`(`ApplyShortestPathBlend`,开关 `_JAVA_SHORTEST_PATH_BLEND`),更像 Java |
| 控制器 `blend_transition` 点集曲线 | ✅ YSM 扩展(SegmentedBlendTransition) | ❌ 网易仅数字 | 移植工具取最大时间键(曲线总长)降为数字(`_NumericBlend`,Blockbench 导入同一处理) |
| 控制器状态 `sound_effects` / `particle_effects` | pojo 有、AnimationProtoMapper 传空 | ✅ 执行 | 反向差异,移植工具删掉 |
| 状态里 `{动画: 表达式}` | **布尔 apply 条件**(ConditionHolder.evalAsBoolean:非零即播、全权重) | **混合权重**(乘进旋转/位移,缩放按 1+(s-1)w) | 布尔式两边同值;非布尔式移植工具包成 `((expr))!=0`,非零常量去掉条件、零常量保留(`_JavaApplyCondition`) |
| `animations` / `transitions` 里的多键字典 | Adapter 只取**首键**(`break`) | 全部生效 | 移植工具只留首键 |
| 控制器名不是通道名 / `initial_state` 不存在 | 不挂载(发现规则按名字模式匹配) / `updateState` 直接返回,永不动作 | 主包挂成常开条目 / 未验 | 移植工具跳过并留痕(`IsJavaChannelControllerName`);`initial_state` 缺省补 `default`(pojo 缺省值) |
| `ysm-builtin` / `ysm-entry-*` 状态 | ✅(2.6.3) | ❌ | ysm-builtin ≈ 保留基线对应控制器;子控制器需展平 |
| 空状态连续跳转(一帧穿多状态,防环) | ✅(2.6.3) | 引擎未验证 | 迁移控制器时避免依赖 |

---

## 9. 过渡 / 时间轴 / 阈值数值对照

| 项 | Java | 基岩现状 |
|---|---|---|
| 起始过渡 | 按通道一刀切:main/hold/use/vehicle/passenger = **0.1s(2 tick)**;swing/fire/cap/gui/parallel/pre_*/post_*/armor = **0**。机制:进入 BEGINNING_TRANSITION 态,动画时间锁 0,从**当前骨骼姿态快照**线性插到新动画首帧(AnimationPlayer.runBeginningTransition / BeginningTransitionPoint) | 旧基线按状态各配:move 0.2 / sneak 0.24 / jump 0.24 / swim 0.2-0.3 / idle 0.3 / sleep 0.12 / fight 0.05-0.15 / crossbow 0.15-0.4 / land 0.08-0.16 / tacz 0.3(draw 0.5);**riding/carryon/attacked/root/third_person 无 blend(硬切)**。**Java 模式转换包**:主链改由生成式状态机 `controller.animation.<包>.ysm_state` 驱动(packParser.BuildStateChainController,移植工具落盘 `animation_controllers/<包>/ysm_state.json`),全部状态 `blend_transition` 0.1 = Java main/vehicle 通道;引擎做的是新旧状态交叉淡化(非 Java 的姿态快照→首帧插值),观感等价。直挂 animate 条目是零过渡硬切,只保留给基线借来的成员与无状态机文件的旧产物 |
| 结尾过渡 | **全局硬编码 3 tick,final 不可配**(AnimationData.java:17);PLAY_ONCE 播完进 ENDING_TRANSITION,末姿态权重 1→0 淡到底层 | 控制器离态 blend_transition 兼任。Java 模式:death/attacked 播完转 `<键>_done` 空状态(0.15s),挥击族在 ysm_swing 成员状态写 0.15s 离态淡出;这些动画由移植工具改为 `hold_on_last_frame`(基岩 `loop:false` 播完即撤,没有末姿态可供淡出) |
| blend_transition 归属 | **被进入**的状态:`transition()` 把新状态的 blend 设成播放器的起始过渡,即"该状态自己的淡入时长"(BedrockAnimationController.transition → setBeginningTransition(newState.blendTransition())) | 写在**被离开**的状态上(Mojang 文档原注 "when transitioning away from this state";原版 camel 只可能被离开的初始态显式写 0.0、Blockbench 预览 animation_controllers.js:1995 用 `last_state` 同证)。**两边归属相反**:各态同值时等价(生成器给主链每态同值);作者控制器各态不同值(warden `parallel_3` 缓冲态无值/其余 0.2,wither `pre_parallel_1` walk 族 0.15/其余 0.1/fly 族无值)时移植工具按目标重映射:S 的各出边目标(去重)的 Java blend 里**出现次数最多**的值,**平局取较小值**(`RemapBlendTransitions`,**非幂等,只在移植期跑一次**,修复工具不碰)。早先取最大值,会把作者刻意写的硬切改成淡化(凋灵娘 flyA → fly_tranlate_A 目标 blend 0,只因另一条出边 jump_down 是 0.1 就被补成 0.1 秒淡化);基岩单值归属下两者不可兼得,平局取小是误差更小的一侧 |
| **经过空状态的切换** | 从当前姿态快照插值到新动画,空状态只多花 1 tick,姿态连续 | 交叉淡化 = 出态权重 1→0 + 入态 0→1;入**不播动画**的中转状态(凋灵娘 `cache`)时没有入态动画,姿态先淡到绑定姿态再淡进下一状态 —— 落地/起步/起跳一瞬间直立(2026-09-03 实测)。移植/修复工具 `BypassEmptyHubStates`:①给每条 `X→中转` 前置 `X→Y`(条件相与)旁路转移,目标限有动画的状态,矛盾(`!A` 对 `A`)/自环/含 `all_animations_finished`/目标也是空状态的不插;②把指向中转状态的转移**一律排到列表最后**(兜底语义)—— 基岩按顺序取第一个成立的转移,作者常把兜底写在中间(凋灵娘 `idle` 的 `→cache` 排在 `→jump_up` 之前,于是起跳先命中 cache) |
| **pre 静态显隐 × parallel 条件变体** | pre 通道设 `scale 0` 隐藏全部变体,当前变体的动画(parallel 通道)再设回 1;Java parallel 对 position/scale 是**覆盖**(仅 rotation 相加)→ 变体可见 | 两条动画共写同一 (骨骼, 通道) 时**缩放相乘**(2026-09-16 实机探针定案),0 × 1 = 0 **不可见** —— 凋灵娘的火焰(几何骨骼 `Fires_*`/`ysmGlowFire_*`)、表情、嘴型全都出不来。移植/修复工具 `ReconcileConditionalVariants` 折叠成**单一所有者**:把变体的条件折进 pre 那一份(`scale: [0,0,0]` + `v.Emotions==3 → [1,1,1]` ⇒ `scale: ["(v.Emotions==3)?1:0", …]`),变体动画里删掉该通道(它自己独占的通道如火焰闪烁子骨骼照旧保留)。单一所有者与引擎的合成规则无关 —— 那一点至今没有可靠实机结论,不能依赖。只折叠双方都是常量向量且变体全包只被引用一次的对;rotation 不动;变体值是关键帧的(凋灵娘攻击族对 pre 摆位偏移的覆盖)折叠放弃,交给下一行的逐通道覆盖处理 |
| **逐通道覆盖(伴生动画 + 占用变量)** | 晚层通道在播时,它写的 (骨骼, 通道) 覆盖早层;不在播时早层照常生效。坚守者娘:`pre_parallel0` 把攻击特效骨骼(`Light_1_1`/`ysmGlowHalo_*`/`ysmGlowWave_*`)`scale` 置 0,`player_parallel_1/2` 的攻击状态再放出来 | 相加/相乘 → 特效永远不可见;持剑姿态与主链、前置步态叠成两倍。`port_java_pack.ApplyChannelOwnership`(移植期最后一步,修复工具先撤销再重做):①早层动画 E 里会被晚层覆盖的通道搬进伴生动画 `<E>__own<N>`,在 E 出现的每个状态里紧跟 E 播放;②晚层控制器(含 `ysm_state` 主链)每个状态 `on_entry` 写 `variable.ysm_own_<控制器> = <状态序号>`(从 1 起),并重算用到它的"晚层状态集合" `variable.ysm_ownset_<n>`;③伴生权重 `!((variable.ysm_ownset_<n>??0))`,晚层条目自带的 Java 布尔 apply 条件逐帧会变,内联进权重;④晚层恒活跃(裸 parallelN 的位移/缩放、单状态无转移控制器的无条件条目)时直接删早层通道。权重 0 时位移/旋转贡献 0、缩放贡献 1 = 让出该通道(Java computeWeightedScale 与 Blockbench displayScale 同式)。不拆:带 override 标志的、条件动画键/兜底键/轮盘键/第一人称控制器引用的键(它们还在别处直接播)。GUI 预览与纸娃娃没有晚层控制器,运行层把伴生跟着原动画同条件一起播(`preview_companions` / `idle_gui__own<N>`)。**2026-09-17 补全**(实机测得三条引擎事实,见 `override_previous_animation` 跨条目行与 `all/any_animations_finished` 行):⑤ **权重 0 的动画暂停计时**、还让所在状态的 `all_animations_finished` 永不成立 —— 不带 override 的伴生权重改成 `核心*0.9999+0.0001`(1e-4 实测照常计时,残留旋转 360° 仅 0.036°),时间轴与原动画同步、不卡作者的"播完"转移;⑥ **带 override 的早层也拆**:直挂条件动画(hold_offhand/hold_mainhand/passenger/carry_on)、一次性通道成员(挥击/使用)、作者 post_main 类控制器里带标志的动画,与更晚的非 override 写入者冲突的骨骼**整根**搬进保留标志的伴生(2026-09-18 起按骨骼分组, 见 override 清空粒度行),权重**精确 0** 让位(override 权重大于 0 就清空前序条目);直挂伴生的核心权重写进 ysm.json 顶级 `channel_ownership`(主包 `_AttachOwnershipCompanions` 紧跟原条目挂 `原条件&&核心`),挥击/使用伴生写进一次性状态机,挥击出口改 `any_animation_finished`;⑦ animate 表把带伴生的宿主倒排在最前(§3.4)。离线全栈逐帧模拟(scratchpad `full_sim.py`:按解析器产出的真实 animate 顺序逐条切状态/跑 on_entry/求权重,override 按"权重大于 0 清空前序通道",对比 Java 最后通道胜出)三个参考包各 1500 帧、约 130 万次通道比对零不一致;同一数据换回旧顺序,凋灵娘持剑"跑→起跳"那一帧 51 个通道、"落地→跑"那一帧 199 个通道错一帧 = 用户报告的闪现。**近似**:占用变量在进入状态那一帧切换,晚层淡入淡出期间早层不跟着渐变;精确 0 的 override 伴生让位期间暂停,直挂 hold 让位结束后从暂停处接着播;只在裸 `pre_parallelN` 直挂里播的早层没有状态可挂伴生(三个参考包 0 处,移植报告计数) |
| **输入向量的时延** | `ysm.xxa/zza` 等在客户端本地读取,零延迟 | `query.mod.ysm_input_*` 原先走 客户端→服务端 `ModAttr`→广播回客户端 的往返(数 tick),而作者控制器用它选行走方向状态 → 往返期间没有一个方向状态成立,状态机落到空中转状态 = 起步一瞬间直立。修法:本机玩家在客户端 tick 里**就地** `Set` 一份(`client/molangSystem.py`),远程玩家仍靠 `ModAttr` 同步 |
| **腾空判据** | `ctrl.jump = !onGround && !inWater`,onGround 稳定 | `is_on_ground` 走路时每秒翻转数次;主链状态机用"进入带死区、留在腾空不带"的粘滞;作者控制器改用主包共享 `animation.ysm.java_input_state`(animate 表首位;2026-09-17 前挂在表末的 `java_head_look` 上,作者控制器比主链晚一帧切跳跃状态)逐帧维护的闩锁 `variable.ysm_airborne`(进入走死区,留在腾空只看 `!is_on_ground&&!is_in_water`),`ctrl.jump/idle/walk/run` 展开全部走它 —— 带死区的展开会在最高点判"在地",作者状态机在最高点与落地各抽一次(凋灵娘实测) |
| **每 tick 脚本动画** | `loop:true` + **显式** 0 长度 + timeline:geckolib 每帧重播,语句每帧执行(凋灵娘逐 tick 写眼球变量)。**不写长度的不算**:那是无限长,timeline 只跑一次(§8 `animation_length` 缺省行) | 0 长度动画只在起播触发一次 timeline → 工具改 0.05s 循环(1 tick 一圈),与 Java 同拍;无长度的写成 1e6 秒无限长 |
| timeline 条目超出(或正好在)`animation_length` | LOOP 回绕 / PLAY_ONCE 结束时 `executeRemaining` **补跑**本圈没执行的条目,LOOP 紧接着跑下一圈的 0.0(builtin wine_fox 0.01s 物理循环:0.0 求增量/积分,0.0101 锁存 —— 每帧两段都跑;0.5s 循环在结尾设弹簧系数);HOLD 锁末帧永不执行 | **循环动画不触发 >= 长度的条目**(2026-09-18 实机:15 号克洛诺亚锁存变量恒 0,头发增量退化成绝对角、跟随链卷成一团;系数不设的包头发不动)→ 工具把循环的挪到 **0.0 条目最前**(与 Java 同序,第一圈开头多跑一次),单次的挪到结尾时间戳(同刻合并),hold 的删除(`ClampTimelineToLength`,修复工具补跑) |
| timeline 里的字符串字面量语句 `'注释';` | 空操作(作者当注释用,builtin 10_zhiban) | 引擎容忍度无证据 → 工具删掉(`StripStringStatements`) |
| **`override_previous_animation` 跨 animate 条目** | Java 的通道覆盖是逐通道后写覆盖, 由引擎按通道顺序保证 | **2026-09-17 实机骨骼矩阵探针(`GetQueryableBoneOrientation`)**: 原版玩家右臂先挂非 override 的僵尸举臂(-90°), 后挂带标志的搬运举臂(-37.5°) —— 后者权重 1 → 37.7°, 权重 0.5 → 19.2°(清空后按权重叠加; 插值应为 63.8°), 权重 **1e-4 → 3.9°(同样整通道清空, 连原版摆臂也清掉)**, 权重 0 → 90°(不清空)。即: **权重大于 0 就清空排在它前面的条目的贡献**(粒度见下一行, 2026-09-18 定案为骨骼整体), 权重 0 什么都不做(原版 swim/sleeping 带标志、平时权重 0 不影响摆臂同证)。逐通道覆盖的 override 伴生因此只能精确 0 让位。**2026-09-16 实机定案: 跨条目生效** —— 强制播放带标志的 `pose_3`(`Sickle` scale 1), 被 `pre_parallel0` 隐藏的 Sickle 缩放恢复为 1(骨骼缩放探针)。设计仍不依赖它: 标志按骨骼整体清空(连同该动画没写的通道)且与交叉淡化互斥(下一行), 逐通道覆盖改用上一行的伴生动画。以下为历史记录 —— 2026-09-03 曾两度"实机定案"(先"跨条目无效", 后"占住骨骼、挡住主链"), **均已作废**: 那两轮观察的对象里混进了整份动画文件被引擎拒载的事故 —— 被剥空的动画留下 `"bones": {}`, 引擎报 `Required child [a-zA-Z0-9_.-]+ not found / node parse failed: bones`, 该文件**全部**动画消失, 模型停在绑定姿态、装饰件全亮, 观感与"某层动画被挡住"完全一样(§11 红线; `devtools/validate_rp_animations.py` 把这类形态列为 ERROR)。目前唯一站得住的实机事实: 淡化期入态带该标志会抹掉出态(硬切+弹一下, 见下一行)。**设计上不再依赖它跨条目的语义**: pre / main / parallel 三个域一律不带; Java 的"main 逐通道覆盖 pre"由 `port_java_pack.ApplyJavaMainOverride` 在移植期改数据 —— 常驻 pre ↔ 地面兜底 idle、门控 pre ↔ 空中兜底 jump 删冲突通道, **主链成员不剥**(warden 的 fly 同为 pre 控制器引用, 误剥即残废), 含 molang 赋值的通道不动, 剥空的动画删掉 `bones` 键; pre 控制器对主链成员的冗余引用由 `DropPreControllerMainChainRefs` 摘掉(同播会叠成两倍); pre 层内部(常驻与门控, 跨控制器)由 `DedupPreLayer` 按 Java 通道序去重, 同状态内互斥的 dict 条目与同一控制器的不同状态不互剥。**(历史记录: `ApplyJavaMainOverride` 2026-09-18、`DedupPreLayer` 2026-09-19 均已撤, 两者都把"晚层只在某些状态写"当成恒同播静态删通道 —— 现由 `ApplyChannelOwnership` 按状态让位, 见 §4 迁移注记)** |
| **`override_previous_animation` 的清空粒度 = 骨骼整体; 恒等常量通道被丢弃** | Java 逐 (骨骼, 通道) 后写覆盖; 通道只要有关键帧就参与(`BoneAnimationQueue.setActive`, 没有"全 0 跳过"), 写 0 即清掉前层 | **2026-09-18 两条实机定案**(末影龙娘): ① 地面待机时往 animate 表末尾挂一条**只写翅膀位移/缩放**的 override 动画(`AddPlayerAnimation` + `AddPlayerScriptAnimate` + `RebuildPlayerRender`), 翅膀**旋转**立刻变成单位阵, 摘掉即恢复 —— 重置的是该条目驱动的骨骼的三个通道; ② 旋转/位移 `[0,0,0]`、缩放 `[1,1,1]` 的常量通道等于不存在(持上弦弩的 hold 动画给 RightHand 写常量 0, 手腕仍是主链 idle 的弯曲; 舞蹈 huangyanwuzhe 同样清不掉, 关键帧 0.202° 与非零常量清得掉)。移植工具: 带 override 的早层逐通道覆盖**按骨骼整体分组**(`_GroupOwnershipPairs`: 同一骨骼的通道不拆到两条同播的 override 条目 —— jump_fall 翅膀旋转在 own2、位移/缩放在 own10 时, own10 把旋转清掉, 下落时翅膀僵直展开; 排在它前面的晚层写该骨骼任意通道都算冲突; 排在后面的裸 parallelN 只看同通道); 收尾 `EpsilonizeOverrideIdentities` 把 override 动画的恒等常量换成 `[0.01,0,0]`/`[1.001,1,1]`, 轮盘键除外(/playanimation 在最上层, 补值会连并行层一起清)。**遗留近似**: override 骨骼上本动画没写的通道也被清成默认(Java 保留更早层的值) |
| **`override_previous_animation` 与交叉淡化互斥** ⚠️ | Java 的通道覆盖是**逐通道后写覆盖**(AnimationProcessor:非 parallel 通道 `applyRotation/apply` = set),与过渡无冲突:过渡在每个通道内部单独插值 | **基岩两者不能共存**。该标志=应用前把骨骼重置回绑定姿态,"覆盖此前的全部动画";淡化期间引擎同时应用出态与入态,入态带 override 就把出态贡献抹掉 → 0.1s 内从绑定姿态淡入新动画,观感=硬切+弹一下。**证据**:原版 73 个带 blend_transition 的状态里播 override 动画的**为 0**,玩家实体控制器通篇不写 blend_transition;2026-09-03 实机核实控制器已加载(`AddPlayerAnimationController` 探针 True、伪 ID False)、主链键已全部撤出 animate,排除"没生效"。**解法**=主链动画与它前面的 pre 通道动画(裸 `pre_parallelN` 与 `player_pre_*` 控制器所播的 jump_up/jump_fall 等)一律不带 override → 两态权重互补相加=真正的线性淡化;Java 的通道覆盖语义改在移植期改数据(见上一行)。曾经试过的"归零动画 + override 紧贴状态机之前"方案已废弃 |
| 骨骼归位 | 3 tick 线性回初始姿态(AnimationProcessor.java:134-198) | 引擎行为(未单测) |
| 移动判定 | `limb_swing_amount > 0.05` | `modified_move_speed`:走 >0(权重驱动)、跑 >0.87、潜行移动 >0.1、tacz 走 0.2;Java 模式统一 0.05 |
| 疾跑判定 | `isSprinting()` 状态位 | 速度阈值 0.87(**判据不同**) |
| 垂直速度 | `20*(y-yo)`(格/tick ×20 ≈ 米/秒) | `query.vertical_speed`(米/秒,语义同;创造飞行 ±7.5) |
| 动画更新 | 渲染帧驱动 + seekTime 单调时间轴;上限 60fps(2.2.2);远处实体降频 | 引擎渲染帧驱动 |
| `anim_time` | 秒;**LOOP 每轮回绕**(`animTicks %= len`,AnimationPlayer.java:209-218 源码实证);⚠️ wiki《molang参考表》对 anim_time/life_time 的描述疑似互换,**以源码为准** | `query.anim_time` 引擎语义 |
| `all/any_animations_finished` | 硬编码通道下**两者恒相等**(单动画);基岩格式控制器(BedrockAnimationController.updateState):状态里**每条动画无论 apply 条件真假都在走时钟**,all = "条件为真的都播完"、any = "有条件为真的播完",**没有条件为真的动画时两者都为真** | **权重 0 的动画暂停计时、且算没播完**(2026-09-17 实机:状态里挂一条权重 0 的 3 秒动画,原动画 0.46 秒播完后 5 秒不出态;权重改 1 后从头播满 3 秒才出态;直挂条目同样暂停、恢复后从暂停处接着播)→ 状态里只要有权重 0 的条目,`all_animations_finished` 永不成立。实例:凋灵娘 `player_pre_parallel_1.jump_up` 挂 `{jump_up_hover: 悬浮}`/`{jump_up_ground: 落地}` 两条互斥条件动画,跳跃中永远进不了 `jump_down`;萨赫梅特推进器展开/收纳态同理(`v.Boost` 1/2)。原版网易同类状态用 `any_animation_finished` 旁证。移植工具 `RewriteFinishedQueries`:有带条件的作者条目(或精确 0 的 override 伴生)的状态,判据改成按进入时刻 `variable.ysm_t0_<控制器>` 计时的显式式子(Java 口径,计时从状态进入起算,不计 Java 起始过渡的推迟);生成的挥击成员状态用 any(原动画权重恒 1) |
| 实测噪声(网易引擎) | — | `query.ground_speed` 帧间 0↔60 跳变不可用;`query.yaw_speed` 0~290 噪声不可用 → 主包 EMA 平滑 `query.mod.ysm_yaw_speed`(系数 0.35,client/molangSystem.py:338-353);`is_on_ground` 走路翻转 → jump 需垂直速度门;`modified_move_speed` 平滑可用。详见 molang 映射文档 |

---

## 10. Java 版本演进跟踪表(动画相关,供后续更新迁移)

| 版本 | 动画面变更 |
|---|---|
| 1.2.0 | CarryOn 4 槽位;箭矢动画;`:` 内置分类(hold/swing);**Extra 优先级降到 Parallel 之下**;挥动不中断直至播完 |
| 2.2.1 | **爬梯三槽位 ladder_up/stillness/down**;`vehicle$`/`passenger$` 骑乘条件动画;extra 不限数量/名称;`preview_animation` |
| 2.2.2 | SWEM(11)/ParCool(35)动画;**GUI 三动画 hover/hover_fadeout/focus**;动画更新限 60fps |
| 2.3.0 | **基岩版动画控制器落地**(states/transitions/blend/any-all_finished/ctrl.* 全家);轮盘分类+按钮+**锁定**;blend_weight;拔刀剑 |
| 2.3.1 | `ctrl.idle`;轮盘锁定快捷键;`#return` |
| 2.4.0 | 女仆 10 动画;**8 个空白 pre/post 通道**;缩放混合改连乘(对齐基岩) |
| 2.4.1 | **ctrl.* 与主动画播放条件强制一致**(对照表的锚点) |
| 2.5.0 | **fp_arm 第一人称手臂(仅 parallel0-7)**;**files.projectiles / files.vehicles**(弹射物 water/fire/ground/air、载具 idle/water/ground/fly/forward/has_ride/not_ride + 各自 parallel);arrow.* 控制器移除;手部动画循环类型交还 JSON;roaming 跨实体复制;脚本控制器(ctrl.set_animation 等) |
| 2.6.0 | 奏乐(11 乐器)/铁魔法(19 施法);Better Combat 复用 swing;`ysm.swinging/swing_time/swinging_arm/attack_time` |
| 2.6.3 | **动态通道**(pre/post 前缀任意数量,字母序);**ysm-builtin**;**ysm-entry-* 子控制器(≤5 层)**;空状态连跳 |
| 2.6.5 | **`:lance` 分类**(hold/use 主副手) ⚠️ 基岩未实现 |

**跟踪方法**:子模块更新后 diff 这几处 —— `AnimationRegister.java`(槽位/条件/优先级)、
`PlayerControllerCollection.java` / `FPArmControllerCollection.java`(通道)、
`InnerClassify.java` + `Conditional*.java`(分类/前缀)、`VehiclePredicate.java`(载具链)、
wiki 更新日志(新分类/新槽位)。

---

## 11. 迁移检查清单

**已对齐 ✅**
- 条件动画 hold/swing/use + 甲槽四件 `$`/`#`/`:` 合成(含 $># >: 优先级否定链、转义、纸娃娃门)
- `vehicle$`、CarryOn 三态 + 公主抱、TACZ 全套(变量体系等价实现)
- 轮盘全家桶(保序/子菜单/按钮/停止表达式≈移动打断)、preview 注册表
- 标准 19 槽位中 15 个有基线挂载,death/ladder×3 按需合成
- fp_arm 声明合成、blend_weight molang、HOLD_ON_LAST_FRAME、loop 缺省单次
- Java 模式(按 `files.player.animation` 声明形态自动判定):按 Java 优先级链合成互斥 molang(阈值 0.05、jump 防抖门);移植产物经 `ysm_state` 状态机播放,状态切换 0.1s 交叉淡化、一次性动画 0.15s 淡出(§9)
- `second_order/first_order` 物理函数 → molang 状态积分(头发/尾巴/胸部/鞘翅随动的"Q 弹"手感),主链成员 loop 按 Java 强制语义改写(§8)
- 替换实体(`files.projectiles`/`vehicles`)的通道语义与写入语义:载具按 Java 第一乘客写入、下车不撤、随实体存档
  (`server/modelSystem._SetVehicleModel`),`has_ride`/`not_ride` 按 `query.has_rider` 分,硬编码 0.7 缩放走
  `ysm_vehicle_root` 根骨骼;**船/运输船/矿车是引擎硬编码渲染**,单实体渲染接口直接换照样生效,但换上后引擎不再
  转模型,同一根骨骼补 Y 旋转(`client/render/vehicleOrient.py`:船 = 实体偏航 - 90,基岩船偏航比 Java 口径多 90°;
  矿车照 Java `getMinecartYaw` 按车底轨道形状算);载具动画 `sound_effects` 照常登记

**缺口 ⚠️(按需补)**
- `passenger$`、`chair$`、`vehicle#`(tag)、甲槽 mainhand/offhand、`<slot>:default` 兜底
- `:lance`(2.6.5)、`swing_offhand` 副手挥击信号、`sit` 未知载具兜底
- `elytra_fly` / `idle` / `use_lefthand` 三键基线不消费(仅预览/Java 模式)——
  模型自定义鞘翅/待机想在基线第三人称生效需补 animate 或控制器状态
- GUI hover/hover_fadeout/focus;轮盘运行时"锁定"开关
- 替换实体:乘客座位定位骨骼(`PassengerLocator*`,Java 3.0-dev 里本身还是 TODO)、
  (载具/弹射物的 `roaming` 变量 2026-09-18 起随主人复制:生成/上车时服务端抄一份快照,第一乘客骑乘中改了再刷)
  矿车朝向按轨道方块逐 tick 取(Java 在弯道上按插值位置前后 0.3 格连续变化,这里过弯是两次 45° 跳变)、
  矿车不复刻轨道坡度与受击摇晃(Java 同样没有)
- `riptide`(`query.is_auto_spin_attack` 网易不可用,硬缺口)

**机制性不可复刻 ❌(设计绕行)**
- 逐通道覆盖的**过渡期**:基岩伴生动画按占用变量硬切让出,Java 从当前姿态快照插值(§9)
- 带 override 的伴生精确 0 让位期间**暂停计时**(基岩权重 0 语义):直挂 hold 在晚层让出后从暂停处
  接着播,与原动画错相(Java 时钟不停)
- 作者状态"播完"判据改写按基岩计时口径(状态进入即开始),不计 Java 起始过渡(blend)推迟的时长
- PlayState.PAUSE → molang 条件门整体屏蔽
- molang 事件脚本 / 脚本控制器(ctrl.set_animation 等)→ 空白通道的决策树自动展开成控制器, 其余改写为普通控制器
- ysm-entry-* 子控制器 → 展平;ysm-builtin → 保留基线对应控制器

**基线侧自家债(与 Java 无关,改动须过 diff 等价性守护)**
- BASE_ANIMATE 死条目:`land_ctl`/`crossbow_ctl`/`ysm_model_scale` 键悬空、`root` 条件空串、
  `first_person_map_controller` 空 ID;`controller_fall`/`hudplayer` 指向不存在的控制器
  (均被 FilterRenderEntries 静默丢弃,data/baseAnimations.py:180-196)
- `move_ctl.in_air` 的 run/default 出口条件相同 → 落地永远先进 run 再回落(实现瑕疵)
- minigun 族动画 ID 拼接 bug(`animation.tac.hold.animation.tac.minigun`,线上如此,基线原样保留)
