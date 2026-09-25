# Java 自定义函数(functions/*.molang)转换设计

YSM Java 版 2.5.0 起支持用 molang 写函数文件。网易基岩没有用户函数,本工程的做法是:**移植期把函数编译成基岩
molang**(引擎里与动画同帧执行),molang 做不到的操作(动态音量音高的音效、停止音效、联网同步、键鼠输入)交给
**主包 Python 宿主**用网易 API 实现。模型包仍是纯数据,不带 Python。

> **实验性功能,缺省不转换**(2026-09-25 起):转换器勾选「转换自定义函数(实验性)」、命令行 `ysmconv convert --java-functions`、
> 移植工具 `port_java_pack.py --java-functions`(内核 `PortPack(javaFunctions=True)`,任务单 `options.javaFunctions`)才转换。
> 关着时与支持它之前一样:`fn.*`、`ysm.play_sound` / `stop_sound` / `stop_all_sounds` / `sync` / `keyboard` / `mouse` 由
> `PortMolangText` 置 0,`@player_init` / `@player_update` / `@sync` 不转换,脚本控制器只做决策树展开(`script_controller.py`,
> 展开不了的跳过),ysm.json 不写 `java_functions`。主包运行层(执行体挂载、音效宿主、同步转发)照常支持已转换的包。
> 开启前先看已知出入:
> - 音效没有动画 / 控制器上下文:Java 在动画回绕、播完、切状态、`ctrl.reset` 时停掉该上下文里的音效,`stop_sound` /
>   `stop_all_sounds` 只作用于本上下文;这里只按"实体 + 实例 id"管理,timeline / `on_entry` / 脚本里起的循环音效会一直响到
>   换模型。匿名(id 0)实例与按时长估算"已播完"的实例不留句柄,停不掉。
> - 函数体里的 `??` 被降成 `||`(返回 0/1):`args[i] ?? 默认值` 这类可选参数的缺省值算错(`fn.f(7)` 里 `args[0] ?? 3` 得 1)。
> - `player_update` 的 `args[0]` 按 20Hz 逻辑 tick 算;Java 是限帧器放行的世界更新,几乎每帧为真。
> - main / use / swing 通道(及替换实体、第一人称手臂)的脚本控制器、`ysm.defer` 未支持;Java 方块标签未映射(第六节)。
> - 纸娃娃不跑执行体;实体离开镜头时执行体不求值(Java 离屏仍有 10 帧/秒)。

代码入口:

| 位置 | 作用 |
|---|---|
| `devtools/java_molang.py` | Java molang 解析器(优先级照 `MolangParserImpl`)+ AST + 基岩文本打印器 |
| `devtools/java_functions.py` | 函数目录加载、调用处内联、宿主调用改写、脚本控制器(`ScriptChannel`)、逐帧执行体 / 初始化语句 / ysm.json 声明 |
| `devtools/port_java_pack.py` | `PortPack` 建包级编译器;`RewriteAnimations` / `RewriteControllers` 在物理改写之前调它;`PlanScriptControllers` 给脚本控制器分流 |
| `devtools/script_controller.py` | 纯决策树的脚本控制器展开成有序规则的状态机(不经执行体) |
| `devtools/fix_ported_controllers.py` | 重建初始化控制器 / 预览实体时带上 `java_functions.init` |
| `ysm_bp/ysmModelScripts/packLoader/packParser.py` | Java 模式 animate 表挂执行体;`java_functions` 直通进模型配置与替换实体数据 |
| `ysm_bp/ysmModelCoreScripts/config/functionHost.py` | 宿主规则(纯逻辑,离线可测):音效、键鼠映射、同步的发送 / 投递 / 校验 |
| `ysm_bp/ysmModelCoreScripts/client/javaFunctionHost.py` | 宿主客户端胶水(网易音频接口、按键事件、实体登记、逐帧轮询、同步收发) |
| `ysm_bp/ysmModelCoreScripts/server/javaFunctionSync.py` | `ysm.sync` 的服务端转发(校验、限流、广播) |
| `devtools/test_java_functions.py` / `test_function_host.py` | 差分测试(Java 参考解释器 vs 基岩解释器)/ 宿主规则 |
| `devtools/test_script_channels.py` | 脚本控制器逐帧差分(Java 动画播放器参考实现 vs 编译出的执行体) |

## 一、Java 语义(源码 + Wiki《自定义函数》)

- **文件**:`<files.function_path,缺省 functions>/<名>.molang`,递归收集,名字大小写不敏感。`名@事件.molang` 既是可调用
  函数 `fn.名`,又订阅事件;`@事件.molang` 只订阅(`ModelRenderTargetAssembler.buildUserFunctionMap / buildEventHandlers`)。
- **调用** `fn.名(实参…)`,无参可省括号(`fn.b;`)。每次调用压一个栈帧(`StackMemory`):`t.*` 各帧独立、从 null 开始,
  `v.*` 共享;`args[i]` 取实参(下标 `(int)` 截断、负数按 0、越界 null);`for_each(t.x, args, {…})` 遍历实参;调用链上限
  32 层(超出返回 null);函数不存在 / 文件解析失败返回 null / 0。
- **返回**:`return` 穿透 `{…}` 与循环,直接结束本次调用(Wiki"闭包返回");没执行到 `return` 时返回**最后一条语句的值**
  (赋值语句的值是右值)。
- **值**:float / Boolean / null / 字符串 / 结构体 / 列表;`null` 与 `false` 为假,数字非 0 且非 NaN 为真,其余对象为真;
  算术里 null 按 0;除以 0 得 0;`loop` 次数 `Math.round` 后上限 1024。
- **事件**:`player_init`(模型装载后第一次动画更新前)、`player_update`(每次动画更新前,`args[0]` = 限帧器是否放行这次世界更新:
  不超过显示器刷新率、远处 30/60 帧、离屏 10 帧,只有 GUI / 可变上下文重渲染时为假 —— 不是 20Hz 逻辑 tick)、
  `sync`(`ysm.sync(数值…)` 经服务器广播到全体客户端,最多 16 个参数)、`defer`(`ysm.defer(名, 参数…)` 排队,本轮动画
  求值结束后倒序交给全部 `@defer` 处理函数;名字参数没被使用)。同时触发的顺序 player_init > player_update > sync。
- **脚本动画控制器** `@player_ctrl_<通道>.molang`:每帧执行,`ctrl.set_animation(名[, 循环类型])` 选动画(同名同循环类型
  再设是空操作,要重播先 `ctrl.indicate_reload()`),`ctrl.set_beginning_transition_length(秒)`,`ctrl.reset()`;返回值按
  整数取:2 continue / 3 stop / 4 pause,其余(含 5 bypass、没 return 时最后一条语句的值)交回通道内置逻辑。
- **音效** `ysm.play_sound(实例id, 音效名[, 模式[, 音量[, 音高]]])`:实例 id 为 0 不跟踪;否则强制位(模式 &1)停旧播新,
  不带强制位时旧实例还在响就不播;模式 &2 走全局管理器(否则挂在当前动画 / 控制器上下文),&4 循环;音量音高夹在
  [0.001, 1000]。`ysm.stop_sound(id[, 全局])`、`ysm.stop_all_sounds([全局])`。
- **发出类调用只在 allowEmitting 时生效**:音效族、`ysm.sync`、`ysm.defer`、粒子在 `allowEmitting` 为假时直接返回
  (false / null,参数都不求值)。为真的只有:事件体(init / update / sync / defer)、脚本控制器、控制器 `on_entry` / `on_exit`、
  timeline 指令帧;骨骼通道、转移条件、动画权重求值时为假(`InstructionKeyFrameExecutor` / `transition` 用完即复位)。
- **键鼠** `ysm.keyboard(GLFW 键码…)`(任一按下即真)/ `ysm.mouse(GLFW 鼠标键)`:读**本机**键盘,不在游戏画面(开着界面)
  时恒假;在远程玩家的动画里读到的也是本机键盘 —— 脚本拿它决定要不要 `ysm.sync`,而 sync 在远程玩家身上什么都不做。
- **同步** `ysm.sync(数值…)`:本机玩家上发给服务端(远程玩家上什么都不做,等服务端转发),服务端校验 ≤16 个参数后广播给
  看得见发起者的玩家与发起者自己,各客户端把事件排进发起者实体的队列,在下一次动画更新的全部控制器之后一次跑完积压的
  `@sync` 处理函数(按订阅顺序,实参是浮点列表)。
- **与源码快照的出入**:`.ref/ysm-java-src` 的 `ModelRenderTargetLoader` 编译函数时传 `isUserFunc=false`(不滤注释、
  return 不穿透),与 Wiki 2.5.0 的"支持 C 风格注释""闭包返回"矛盾。真实模型包的函数普遍带注释,按快照语义它们会整份
  解析失败 —— 转换以 Wiki 为准。

## 二、基岩引擎实测(2026-09-23,客户端 `EvalMolangExpression`,与资源包文件同一解析器)

| 写法 | 结果 |
|---|---|
| `t.i = 0; loop(10, { t.i = t.i + 1; t.i >= 3 ? break; }); return t.i;` | 3(条件分支里的 break 可用) |
| `loop(5, { …; t.i == 2 ? continue; …; })` | continue 可用 |
| `loop(3, { loop(3, { …; t.x > 10 ? break; }); })` | break 只跳出内层 |
| `v.a = 0; 1 ? { v.a = 5; return 7; }; return 9;` | 7,且 `v.a` = 5(块里的 return 结束**整个表达式**) |
| `loop(5, { …; t.n == 3 ? { return 30 + t.n; }; }); return 99;` | 33(循环里的 return 同样结束整个表达式) |
| `loop(2000, { … })` | 跑满 2000 次 |
| `t.a = t.never_set; return 7;` | **0 —— 读未定义的变量让整段表达式中止**(不报错),`v.*` 同理 |
| `GetMolangValue("variable.x")` | 读实体变量,未定义返回 `None` |
| `PlayCustomMusic(…, entityId)` | 成功返回字符串 id(`'2793'`),失败返回字符串错误码(找不到定义 `'-4'`) |
| `StopCustomMusicById(id, 0)` | 正在播放的返回 True;已播完 / 错误码返回 False |
| 读没注册的 `query.mod.x` | 0,不中止;注册过没 `Set` 的读注册默认值,`Set` 之后读实体上的值 |
| 移植流水线产出的执行体(脚本控制器 + `@sync` + 键盘)按渲染 tick 逐步 Eval | 状态机时序与 Java 播放器逐步一致(载入 / PLAY_ONCE 到点进尾过渡 / 0.15 s 回空闲 / 循环首轮播完 / 停止),同步事件只跑一次、`for_each` 按实际个数 |
| 原版界面的纸娃娃(背包、暂停界面的皮肤预览;日志里的实例名 `minecraft:player.0.<uuid>.CustomSlim…`) | 跑玩家的**整张** animate 表,变量作用域独立(主包 Python 写的变量到不了),没有实体:执行体里的 `query.relative_block_has_any_tag` 报 `Scope requires an Actor`;`variable.is_paperdoll` 不置位;`query.mod.*` 不报错、读注册默认值 |
| 资源包动画里读未定义变量 | 报 `unhandled request for unknown variable`(Eval 里则静默中止);新渲染实例的第一帧最容易撞上 —— 变量初始化控制器必须排在 animate 表最前 |
| `query.relative_block_has_any_tag(0,1.5,-1,'minecraft:replaceable')`,该格是空气 | 0 —— 基岩方块标签与 Java 是两套词表,空气没有标签(Java 的 `#minecraft:replaceable` 含空气),见第六节缺口 |

推论:函数体的控制流可以原样落到基岩;但内联进宿主表达式时 `return` 会把宿主一起结束,要改写。临时变量必须先赋值再读
(Java 的 null 在基岩等于"中止"),实体变量靠包的初始化控制器(文件扫描补 0)兜底。

## 三、编译策略(`java_functions.FunctionCompiler`)

1. **纯函数折成表达式**:函数体只有顶层临时变量赋值(每个只赋一次)+ 一条 return / if-else 链,链上每支只有 return,且
   不含赋值、宿主调用、不纯的函数调用、循环、物理函数 —— 调用处直接换成等价表达式,实参 / 临时变量代入后做常量折叠。
   宿主表达式的形态与短路语义不变(官方酒狐 15 号 `halo_battery_indicator(4)` → `ysm.food_level>10`)。实参 / 临时变量
   不是字面量或变量名、又被引用不止一次的,先求值进临时变量(`math.random` 代入多处会变成多次抽样)。
2. **其余按语句内联**:实参在调用方作用域依次求值进 `t.ysm_f<K>_a<i>`;函数的临时变量改名 `t.ysm_f<K>_<名>`,入口全部置 0;
   结果变量 `t.ysm_f<K>_r`。调用提到宿主语句之前 —— 在 `&&` / `||` / `??` 右侧、三元 / 条件分支里的调用,连同分支条件一起提
   (展开成条件块,Java 的短路语义不变)。函数体里不在末尾的 return:整个函数体包进 `loop(1, {…})`,return 改写成"写结果
   变量 + break";循环里的 return 另置完成标记 `t.ysm_f<K>_d`,每层循环之后 `完成标记 ? break;`。函数顶层的 break / continue
   丢弃(Java 在循环外什么都不做)。递归 / 调用链超过 32 层落 0 并留痕。
3. **args**:常量下标直接取实参;动态下标 `args[e]` 展开成按 `e < 1 / e < 2 …` 取值的三元链(同 Java `(int)` 截断与负数按 0);
   `for_each(t.x, args, {…})` 展开成计数 `loop(实参个数, {t.x = 选第 i 个; i += 1; 循环体})`,循环体里的 break / continue 原生可用。
4. **物理函数**:内联进来的函数体里的 `second_order` / `first_order` 在编译器里逐语句改写(积分语句紧贴所在语句,与 Java
   求值顺序一致),之后的 `PhysicsRewriter.RewriteTree` 就看不到它们了。`_SplitTopLevelStatements` 另改成识别花括号。
5. **宿主调用**:`ysm.play_sound` → 每个调用点一个请求槽位 `v.ysm_hs_<槽>`(计数器 +1;动态音量 / 音高写进 `_v` / `_p`);
   `stop_sound` → `v.ysm_hx_<槽>`;`stop_all_sounds` → `v.ysm_hxa_<槽>`。音效名必须是字面量(否则丢弃并留痕),登记进包的
   音频汇(拷 ogg + `sound_definitions.json`),并从源 ogg 读出时长。`ysm.sync(…)` → 调用点槽位实参 `v.ysm_sy_<槽>_<i>` +
   计数器 `v.ysm_sy_<槽>`。按 Java 的 allowEmitting 口径:值位置(骨骼通道 / 转移条件 / 权重,`RewriteValueText`)里这些调用
   直接丢弃(连同参数),只有语句位置、事件体与脚本里的才改写。`ysm.defer` 暂不支持(丢弃留痕)。`q.debug_output` 丢弃。
   `ysm.keyboard(键…)` / `ysm.mouse(键)` → `query.mod.ysm_kb_<GLFW 码>` / `query.mod.ysm_ms_<键>`(多个键取或),键码必须是
   常量且网易有对应键(`SUPPORTED_GLFW_KEYS`)。运行层写给执行体的值一律走主包注册的 `query.mod`:写成 `v.x ?? 0` 过不了
   `PortMolangText`(② 把 `v.x ?? 数字` 当作者默认值收走、其余 `??` 改成 `||` —— `||` 返回 0/1,与 `??` 不等价,函数体里
   `args[i] ?? 默认值` 因此算错,见文首已知出入),裸读未定义变量又会让整段中止。
6. **事件**:`player_update` 事件体编进逐帧执行体动画 `animation.<包>.ysm_fx_frame`(文件 `ysm_functions.animation.json`,
   写在 `Head` 骨骼的旋转通道上,返回 0):`v.ysm_fx_t != q.life_time` 守卫保证每帧只跑一次,`args[0]` = 本帧是否跨逻辑 tick
   (`math.floor(q.life_time*20)` 变了;Java 几乎每帧为真,见文首已知出入)。`player_init` 事件体追加进每实例变量初始化控制器的 on_entry(变量默认值之后)与
   预览实体的 `scripts.initialize`。执行体与初始化语句都走包动画的同一条流水线(名字映射 / 物理 / 纸娃娃门控 / 语法守卫 /
   优先级显式化)。`@sync` 事件体编进执行体(排在 `player_update` 之后、脚本控制器之前):`query.mod.ysm_si_c != v.ysm_si_k`
   时记下计数并跑全部处理函数,实参取 `query.mod.ysm_si_<i>`,`for_each(args)` 按 `query.mod.ysm_si_n` 个数循环(Java 在下一次
   动画更新末尾执行,这里是主包写入事件后的那一帧)。

## 四、产物与运行层

ysm.json 顶级 `java_functions`(移植工具写,修复工具只读):

```jsonc
"java_functions": {
  "executor": "animation.<包>.ysm_fx_frame",
  "init": ["v.north=1;v.south=1;…"],                    // player_init 的基岩形态
  "sounds": [{"slot": 1, "sound": "ysm.<包>.motor", "id": "motor", "force": true, "global": true,
              "loop": true, "volume": 1.0, "pitch": null, "duration": 0.17}],   // null = 动态值
  "stops": [{"slot": 1, "id": "motor", "global": true}],
  "stop_all": [{"slot": 1, "global": false}],
  "syncs": [{"slot": 1, "count": 2}],                  // ysm.sync 调用点槽位与实参个数
  "sync_handlers": ["eventsubscriber@sync"],           // 编进执行体的 @sync 处理函数
  "keys": [258], "mouse": [1]                          // ysm.keyboard / ysm.mouse 用到的 GLFW 键码
}
```

- 脚本控制器的播放控制器登记在 `files.player.animation_controllers`(`controller/ysm_fx_<通道>.json`),主包按普通作者控制器
  挂载(纸娃娃 / 第一人称双门),不另外声明;播放器状态的复位语句并在 `init` 里。
- `packParser`:Java 模式把执行体插在 animate 表的共享状态动画(使用状态 / 输入锁存 / 主状态 / 鞘翅角)之后,条件
  `query.mod.ysm_is_model`(主包应用模型时写在世界实体上,纸娃娃读到默认值 0)—— 只在世界里的玩家实体上跑。纸娃娃
  没有实体,执行体里的方块 / 物品查询在它上面逐帧报错(见第二节);代价是纸娃娃上没有脚本控制器驱动的动画(Java 在 GUI
  里照跑函数)。变量初始化控制器排在 animate 表最前(新渲染实例第一帧就有值),带包动画的替换实体(载具 / 投射物)也挂它
  (`_WithVariableInit`,控制器里已含替换实体文件的变量)。`sounds` / `stops` / `stop_all` 直通进模型配置,并带进替换实体
  的数据 —— 它们的动画同样会调函数。
- `client/javaFunctionHost`:玩家由 `client/modelSystem` 的模型应用点登记(远程玩家同样登记:每个客户端都跑他们的动画),
  替换实体由 `vehicleRender` / `arrowRender` 登记。逐渲染帧用 `GetMolangValue` 读计数器,按 `config/functionHost` 的规则
  播放 / 停止:首次观察只记值;同 id 强制替换 / 在响不播(按 ogg 时长 ÷ 音高估算);音量再夹到网易上限 1;
  `StopCustomMusicById` 在播放后 7 帧内调用可能失败且之后永远停不掉(接口文档),不足 16 帧的停止推迟执行;
  实体离开视野 / 被移除 / 换模型即停掉它的全部实例。
- `query.mod.ysm_kb_*` / `ysm_ms_*` / `ysm_si_*` 在 `LoadClientAddonScriptsAfter` 全部注册(默认 0,`functionHost.RegisteredQueries`),
  没 Set 过的实体(远程玩家、纸娃娃)读默认值。
- 键鼠:本机玩家模型声明的键码逐帧 `Set` 到本机玩家实体上(`OnKeyPressInGame` 的网易键码 = Windows 虚拟键码,换成 GLFW 码;
  左右修饰键网易不分;鼠标另听左右键按下 / 松开事件),顶层界面不是 `hud_screen` 时全为 0。
- 同步:逐帧轮询本机玩家的 `ysm.sync` 槽位(首次观察只记值),读出实参 `Call("YsmFunctionSync")`;`server/javaFunctionSync`
  校验(≤16 个有限数值,每人每秒 40 件)后 `Call("*", "YsmFunctionSyncEvent")` 广播;各客户端只给模型带 `@sync` 的实体排队,
  执行体处理完上一件(`v.ysm_si_k` 追上 `query.mod.ysm_si_c`)才写下一件(16 个实参、个数,计数 +1 最后写;计数以实体上的值
  为准,实体重建后两边都回到 0)。

## 五、脚本控制器(`@player_ctrl_<通道>`,`java_functions.ScriptChannel`)

**Java**(`CodedAnimationController.process` + `AnimationPlayer`,geckolib3):每帧先用"上一帧末的播完标记"设好
`all/any_animations_finished`,跑脚本;`ctrl.set_animation` 等直接改播放器;返回值取整 2 继续 / 3 停止 / 4 暂停,其余交回
通道内置谓词(空白通道 = 停止;`pre_parallel_N` / `parallel_N`、后缀 0~7 且包里有 `pre_parallelN` / `parallelN` = 循环播放它)。
播放器的几条要紧语义:

- `set_animation(名[, 循环类型])`:与上次设置(名 + 循环覆盖)相同则空操作;否则**立刻硬撤**当前动画(无尾过渡)、记下上次
  设置,包里有该动画才登记"待载入"。待载入的动画在下一次"继续 / 暂停"处理时才开始播(起始过渡 `set_beginning_transition_length`,
  通道缺省 0)。
- "停止"只让起始过渡 / 播放中的动画进 3 tick 尾过渡并清掉上次设置(下次同名设置会重播);空闲时什么都不做,尾过渡中照常处理
  (尾过渡结束后若有待载入的动画会直接开播)。
- PLAY_ONCE 播到时长即进尾过渡,LOOP 每轮播完置"已播完"并回绕,HOLD 停在末帧;"已播完"空闲时为真。
- 眨眼脚本(18 号)就是"设置 + 返回停止、下一帧返回继续"才触发一次性动画 —— 连续两轮选中同一个变种时第二轮不眨,Java 如此。

**编译**:脚本(连同它调用的函数)编进逐帧执行体,排在 `player_update` 之后、按 Java 通道处理序;脚本后面照抄 `process` 的
状态机。播放器状态存在实体变量 `v.ysm_sc<序号>_<字段>`(上次设置码 / 待载入 / 当前 / 状态 / 计时起点 / 播完取反 / 起始过渡 /
暂停 / 载入次数,字段表见 `ScriptChannel.FIELDS`,全部取 0 = Java 初值,由初始化控制器补 0),模型装载时复位(Java
`updateModel`)。末尾算出**播放码** `ps` = 起始过渡或播放中时 `当前动画序号×2 + 载入次数的奇偶`,否则 0。
`ctrl.*` 只在脚本里生效(函数里调用也算);脚本里的播完查询换成本帧开头记下的标记;`ctrl.state_*` 等常量按 CtrlBinding 取值。
动画名与循环类型必须是字面量(否则整份跳过留痕);时长按 Java 源文件口径(`animation_length` 优先,否则骨骼关键帧最大时间戳)。

**生成的控制器** `player.<通道>`(文件 `ysm_fx_<通道>.json`,走控制器流水线):`idle` + 每个动画一对孪生态 `a<i>` / `a<i>_r`
(重新载入 = 换到另一个 = 基岩从头播),全部按播放码切换。基岩按**离开**的状态取 blend,直接写定:`idle` 取起始过渡(脚本里
最后一个常量实参),播放态取尾过渡 0.15 秒 —— 停止 / 播完都是从当前姿态淡回,与 Java 一致;两个动画直接切换时 Java 是"硬撤 +
起始过渡",这里是 0.15 秒交叉淡化(近似)。可能暂停时动画条目带 apply 条件 `pa==0`(Java 暂停时照常计时,基岩权重 0 时计时也停,
近似)。Java LOOP 而基岩文件不循环(循环覆盖 / 内置并行动画)时每轮换孪生态重播。后处理:PLAY_ONCE 淡出权重
(`FadeControllerOneShots`)与空中转旁路跳过它(执行体已按 Java 转尾过渡、空闲态是"什么都不播");逐通道覆盖照常规划,
并行族按 Java 硬编码控制器"运行中旋转相加"(`additiveRotation`)。

**分流**(`PlanScriptControllers`):同名基岩控制器已存在 → Java 用它,不转换;纯决策树、且没有决策树转换会丢掉的东西(音效 /
`reset` / `indicate_reload` / 函数调用)→ `script_controller.py` 展开成有序规则(不经执行体,15 号 `@player_ctrl_pre_main`);
其余空白 / 并行通道 → 执行体;main / use / swing 等 → 第 3 阶段,留痕跳过。

## 六、阶段与现状

整体仍是**实验性功能**(转换缺省关,见文首)。

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 解析器、`fn.*` 内联、宿主音效、`player_update` 执行体、`player_init` | 已完成,离线差分测试 + 引擎 Eval 验证;完整链路待实机 |
| 2 | 空白通道(pre_*/post_*)与 pre_parallel_N / parallel_N 的脚本控制器:执行体复刻播放器状态机 + 生成的控制器播放 | 已完成,逐帧差分测试(含变异检查)+ 引擎逐 tick Eval;生成的控制器待实机 |
| 3 | main / use / swing 通道的脚本:与主链状态机 / 一次性状态机的接管衔接 | 未开始 |
| 4 | `ysm.sync` + `@sync`、`ysm.keyboard` / `ysm.mouse`(发出类调用按 allowEmitting 口径);`ysm.defer` | 同步与键鼠已完成:同步的客户端 → 服务端 → 广播 → 排队投递实机走通,执行体里的处理函数与键盘读取引擎 Eval 验证;真实按键待实机;defer 未做 |

本地样本(官方酒狐 15 / 18 / 21 的 14 个函数文件):15 号 `@player_ctrl_pre_main` 走决策树;18 号眨眼(pre_parallel_3)、
碰墙抬手(parallel_5)、15 号 car_stuff(parallel_6,内置 parallel6;Tab 键 → `ysm.sync` → `@sync` 改 roaming 喇叭状态)走
执行体;18 号倒走(main)、BC(swing)、印度格挡(use)与 21 号 ArrowShoot(use)等第 3 阶段。

2026-09-23 实机(18 号移植进测试环境):执行体在世界实体上逐帧运行(`v.ysm_fx_t` 跟 `q.life_time`、眨眼倒计时逐 tick 递减);
骑上换成 GMA_T.50 的马:初始化控制器在载具上跑过(它带的 `@player_init` 语句写出的变量在马身上)、音效宿主循环播放引擎声并
按请求重启,下车后 `not_ride` 的停止请求把它停掉。已知缺口:**Java 方块标签**。18 号碰墙抬手用
`query.relative_block_has_any_tag(…, 'minecraft:replaceable')` 判断前方有没有墙,基岩标签词表不同、空气没有标签,四个方向
恒判成"有墙"(实机读到 `v.north` 等四个变量在空地上恒为 0),按脚本逻辑走路时会一直抬手。要对齐得把带常量参数的调用改成
运行层探针(同 `ysm.relative_block_name`),按 Java 标签的成员表判方块名 —— 尚未做。
