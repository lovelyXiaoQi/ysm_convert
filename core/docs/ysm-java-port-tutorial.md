# YSM 网易版 · Java 模型包移植教程(JE → 基岩)

> **读者**:已经会制作 Java 版 YSM 模型包(参照 [Java 官方 Wiki](../.ref/ysm-java-wiki/docs/notes/wiki/) 流程,
> 有一份能在 JE 跑起来的模型包)、想把它带到网易基岩版的创作者。
> **结论先行**:`ysm.json` 配置层**几乎原样拷贝**,真正的移植工作在**资源层**——
> 动画 ID 加前缀、molang 替换、贴图/几何按基岩全局 ID 规范摆放。
>
> 姊妹文档(建议按需查阅,本文不重复其内容):
> - [ysm-json-pack-guide.md](ysm-json-pack-guide.md) —— 网易版格式**速查手册**(字段推导规则、
>   全部顶级扩展字段、排错表)
> - [ysm-java-molang-mapping.md](ysm-java-molang-mapping.md) —— Java 专有 molang **逐条替换清单**
> - [ysm-java-animation-mechanism.md](ysm-java-animation-mechanism.md) —— 两边动画机制**深度对照**
>   (通道/状态机/数值,面向想写自定义控制器的高级作者)

---

## 0. 移植总览:要做什么,不用做什么

| 部分 | 结论 | 工作量 |
|---|---|---|
| `ysm.json` | **原样拷贝**(spec 2 原生兼容),网易扩展全部可选 | ≈0 |
| `metadata`(名称/作者/授权) | 零改动(作者头像路径需改成资源包引用) | 低 |
| `properties`(轮盘/表单/预览/缩放) | 零改动(三态轮盘、config_forms、缩放换算全自动) | ≈0 |
| 几何体(main/arm) | 机械转换:改 identifier、按规范摆放 | 低 |
| 贴图 | 机械转换:按约定目录摆放 | 低 |
| **动画文件** | **移植大头**:ID 加前缀转小写、条件动画名转义、molang 替换 | 中-高 |
| 动画控制器 | 多数机械转换,少数 Java 扩展需降级 | 中 |
| 弹射物/载具替换 | 基本零改动(实体 ID 自动映射、状态条件按 Java 通道自动合成、`files.arrow` 照常转换) | ≈0 |
| 预览实体定义 | **新增**一个模板文件(基岩独有,Java 无此概念) | 低 |
| 模组联动动画(跑酷/马术/女仆/拔刀剑/奏乐/铁魔法) | **不移植**(基岩无对应模组) | — |
| 自定义函数 / 多语言 | 函数暂不支持;多语言由移植工具把 `lang/zh_cn.json` 烘进显示文本(见 §2.3) | — |

> 本仓库 `devtools/port_java_pack.py` 是上述机械转换的自动化工具(动画 ID/转义/molang
> 批量替换/手臂父链嫁接全自动),本文按**手工移植**讲解——手工过一遍才知道工具在做什么、
> 汇总里的 `[!]` 告警该怎么处理。

---

## 1. 包结构对照

Java 版一个文件夹装下一切;网易版是**行为包(声明)+ 资源包(资源)**的组件形态,
资源全部通过**全局 ID** 引用:

```
Java 版模型包                          网易版组件
custom/纸板狐模型包/                    你的组件/
├── ysm.json                          ├── behavior_pack/
├── models/                           │   └── ysm_models/
│   ├── main.json                     │       └── <包名>/
│   └── arm.json                      │           └── ysm.json          ← 唯一的声明文件
├── animations/                       └── resource_pack/
│   ├── main.animation.json               ├── models/entity/<包名>/*.json      geometry.<包名>
│   ├── arm.animation.json                ├── animations/<包名>/*.json         animation.<包名>.*
│   └── extra.animation.json              ├── animation_controllers/<包名>/    controller.animation.<包名>.*
├── textures/*.png                        ├── textures/entity/<包名>/*.png
└── controller/*.json                     └── entity/<包名>.entity.json        ← 预览实体(新增,见第 9 步)
```

要点:

- **`<包名>`** = `ysm_models/` 下的文件夹名,是模型 ID(`ysm_pack:<包名>`)与所有资源 ID
  推导的词根。**小写英文+数字+下划线,并带作者前缀起长名防冲突**(如 `zuozhe_wine_fox`)
  ——基岩资源 ID 是全局命名,撞名即互踩,这与 Java 版转换教程的建议一致。
- 资源**只放资源包一份**。主包启动时扫描资源包磁盘,自动索引该命名空间下全部动画/
  控制器 ID 与 molang 变量——不存在"忘了同步另一份"的问题。
- 一个组件可以放多个 `<包名>/`;Java **合集包**形态(`ysm_models/<合集>/<子包>/ysm.json`
  + 合集目录 `ysm-pack.json`)同样支持,自动生成选择界面文件夹分组。
- 无需任何 Python 代码、无需清单注册——放对位置即被发现。

---

## 2. `ysm.json` 支持度总表

以 Java Wiki《项目结构》2.5.2+ 版字段为基准逐项说明。
标记:✅ 完整支持;≈ 支持但语义有差异;❌ 不支持(忽略无害,除非注明)。

### 2.1 `metadata` —— 全部照抄

| 字段 | 支持 | 说明 |
|---|---|---|
| `name` / `tips` | ✅ | 进选择界面与详情页,`\n` 换行同样有效 |
| `authors[]`(name/role/comment/avatar) | ✅ | **`avatar` 路径要改**:Java 写包内路径(`avatar/头像.png`),网易写资源包贴图引用路径(不带扩展名,如 `textures/entity/<包名>/authors/gsl`) |
| `license` / `link` / `contact` | ≈ | 解析保留,当前 UI 未展示(照抄无害,未来版本可启用) |

### 2.2 `properties` —— 玩法字段全支持,渲染标志忽略

| 字段 | 支持 | 说明 |
|---|---|---|
| `height_scale` / `width_scale` | ✅ | 自动换算:网易实体缩放 = Java 值 × 0.8⁄0.7(两边默认基准 0.7↔0.8)。缺省 0.7 时还会回读几何文件 description 里的 `ysm_height_scale`(与 Java 同语义)。想直接指定网易值,写顶级 `player_scale` |
| `extra_animation` | ✅ | **三态全支持**:普通条目、`"#分类id"` 键(子轮盘)、`"#按钮id"` 值(配置按钮)、`#return`(占一格)。一条目一格、保序、数量不限;按钮格点开是表单页,页顶"播放动画"= Java 外圈 |
| `extra_animation_classify` | ✅ | 子轮盘,可嵌套(最多 5 层,与 Java 一致) |
| `extra_animation_buttons` | ✅ | `config_forms` 的 range/checkbox/radio 与 Java **同 schema**(滑条按 step 取整、单选执行选项语句);`value` 变量自动初始化(Java 未定义变量读 0:取 0 夹进 range 的 `[min,max]`,位置类滑条居中、`[1,1.6]` 的大小滑条得 1;checkbox/radio 取 0);设置按模型存档,切回模型恢复 |
| `preview_animation` | ✅ | 选择界面展示动画(短名 → 自动拼 `animation.<包名>.<值>`)。与 Java 一样**在卡片上循环播放**:移植工具给只在界面里播的展示动画补 `loop: true`(Java 无视动画文件的 loop) |
| `default_texture` | ✅ | 默认皮肤名(不写取贴图列表首项) |
| `icon` | ✅ | 选择界面图标,取文件名 → `textures/entity/<包名>/<文件名>` |
| `free` | ❌ | 授权体系是 Java 服务,忽略 |
| `gui_background` / `gui_foreground` | ✅ | 选择界面卡片的背景图(纸娃娃之下)/前景图(纸娃娃之上、名字之下),铺满卡片,与 Java 卡片同序;移植工具拷到 `textures/entity/<包名>/gui_background.png` / `gui_foreground.png`(文件名统一,中文名也行) |
| `render_layers_first` / `all_cutout` / `gui_no_lighting` | ❌ | 渲染标志,基岩渲染管线不同,忽略。描边/发光类需求改用网易侧 `material` 字段(见 §2.4) |
| `disable_preview_rotation` | ✅ | 选择界面卡片的取景: 缺省与 Java 一样把模型转过 20° 并略俯视(展示动画就是按这个取景摆的),置 `true` 才正对镜头。旧版写法的包与 py 模型一律正对,不受影响。皮肤卡片与 Java 一样恒带转角、播 idle |
| `merge_multiline_expr` | ❌ | 属转换环节职责:多行 molang 数组请在移植时手工合并成单串 |

### 2.3 `files` —— 声明位置全兼容,三个路径字段除外

| 字段 | 支持 | 说明 |
|---|---|---|
| `player.model.main` / `arm` | ✅ | 路径写法照抄(实际按 `<包名>` 推导几何 ID);写 `geometry.*` 资源 ID 时**直通**(多包共享/改名几何)。`arm` 有**父链红线**,见第 3 步 |
| `player.animation` | ✅ | **Java 原生 dict 槽位形态原样可用**(`main`/`extra` 进主域,`arm`/`fp_arm` 进第一人称域);另有更灵活的**列表混排**扩展形态(推荐,见第 5 步)。模组联动槽位(`tac`/`carryon`/`parcool`/`swem`/`slashblade`/`tlm`/`immersive_melodies`/`irons_spell_books`)的处置见第 5.6 节 |
| `player.animation_controllers` | ✅ | 照抄;文件内全部控制器导入并**常开**(对齐 Java"控制器加载即接管") |
| `player.texture` | ✅ | 字符串与 `{uv}` 形态照抄(皮肤名=文件名去扩展);**PBR(`normal`/`specular`)忽略**;另有 `{"皮肤名": "真实路径"}` 扩展形态(皮肤名与文件名不一致/共享贴图) |
| `projectiles` / `vehicles` / `arrow` | ✅ | 对象形态(2.5.0)与 `match` 数组形态(2.5.2+)都支持,废弃字段 `files.arrow` 按 `minecraft:arrow` 弹射物转换;实体 ID 自动映射 JE↔BE 差异(`minecraft:trident`→`thrown_trident`、`fishing_bobber`→`fishing_hook`);**`#实体tag` 匹配不支持**,请展开写具体实体 ID;`controller` 字段支持;播放按 Java 通道语义:只有谓词状态键(弹射物 `water`/`fire`/`ground`/`air`,载具 `water`/`ground`/`fly`/`forward`/`idle`/`has_ride`/`not_ride`)与 `parallel0-7`(载具另有 `pre_parallel0-7`)直接播,其余动画只注册;模型文件名里的大写/点号自动规整(`GMA_T.50` → `gma_t_50`);载具按 Java 硬编码缩放 0.7(自动外包根骨骼)、第一乘客写入且下车不撤随实体存档,船/运输船/矿车因引擎硬编码渲染改走客户端替身实体(矿车朝向按车底轨道推) |
| `sound_path` | ✅ | 音效自动移植(2026-09-16):`sounds/`(或 `files.sound_path`/`files.player.sound_path` 指定的目录)里的 ogg 随动画音频关键帧一起搬到 `ysm_rp/sounds/ysm/<包>/`,定义与注册由工具生成;原版音效 ID(含 `:`)两边命名不同,需人工对照 |
| `function_path` | ❌ | 自定义函数(`functions/*.molang`)暂不支持:过程式脚本,无表达式等价物(见映射文档§三) |
| `language_path` | 🔶 | 移植工具读 `lang/zh_cn.json`(或 `files.language_path`)把中文显示文本**烘进 ysm.json**:模型名/简介/作者、轮盘条目名(值为 `#按钮` 时顶替按钮名)、配置表单标题/说明/单选项名。`.desc` 悬浮说明与皮肤显示名(`files.player.texture.<名>`)不烘;主包运行期不读语言文件,手写包请直接写中文 |

### 2.4 网易独有扩展(Java 没有的能力)

全部写在**顶级**(与 `files`/`properties` 同级),完整清单与细则见
[格式手册·顶级扩展字段](ysm-json-pack-guide.md)。移植时最常用的几个:

| 字段 | 用途 |
|---|---|
| `animation_namespace` | 动画 ID 前缀不想用默认 `animation.<包名>` 时声明(比如沿用你已有的命名) |
| `skins` | **多皮肤差异化**:逐皮肤覆盖几何/材质/缩放(Java 的皮肤只能换贴图,网易可以整套换) |
| `material` | 发光(`bloom`)/描边/无剔除(`nocull`)等材质,可逐渲染键指定 |
| `files.player.animate` | `{"注册键": "molang 条件"}` 给动画/控制器挂播放条件——**基岩动画系统的核心机制**,Java 硬编码状态机在基岩就是靠这些条件+控制器复刻的(想深改看[机制对照](ysm-java-animation-mechanism.md)) |
| `extra_stop_expression` | 轮盘动画停止条件(缺省"移动/跳跃/潜行即停",对齐 Java 的移动打断;置 `""` 相当于 Java 的"锁定"常开) |
| `initialize` | molang 变量显式初始化(通常不用写——动画文件里的变量会被自动扫描初始化) |
| `animations_remove` | 从注册结果剔除指定键(如文件导入了 `idle` 但你想让它走自定义控制器) |
| `preview_parallel` | 选择界面预览时常开的并行动画(纸娃娃站姿等) |
| `strum` | 弹奏(吉他)玩法开关 |

---

## 3. 逐步移植教程

### 第 1 步:建组件骨架

用 MC Studio 新建空组件(行为包+资源包),按 §1 的目录树建好文件夹。
`ysm.json` 放**行为包** `ysm_models/<包名>/` 下(放资源包读不到);其余资源全进资源包。

### 第 2 步:拷贝 `ysm.json`

原样复制(UTF-8 无 BOM,与 Java 一致)。不支持的 `properties` 字段**留着无害**(解析忽略),
不必删。按需补顶级扩展(第 2.4 节)——最小移植一个都不用写。

**JSONC 照收**:`// 行注释`、`/* 块注释 */` 与尾逗号都能直接解析(与 Java 的 Gson
宽容模式一致,官方 Wiki 的 ysm.json 示例本身即标注为 jsonc),不必为了搬到基岩去
手工清注释。

### 第 3 步:几何体

Java YSM 的模型本来就是基岩几何格式(BlockBench 基岩导出),转换是机械的:

1. `models/main.json` → 资源包 `models/entity/<包名>/`,文件内 identifier 改为
   **`geometry.<包名>`**;
2. 若文件带 `geckolib_format_version` 字段,**删掉**(引擎报 `child not valid here`,
   连锁作废,见红线表);
3. `models/arm.json`(第一人称手臂)→ identifier 改 `geometry.<包名>_arm`,并**重建成
   "原版手臂骨架包装骨骼 + 移位后的 Java 子树"**(移植工具 `BuildFirstPersonArmGeometry`
   自动完成,转换日志会打印"补原版手臂骨架包装骨骼 / 子树整体移位")。

   **为什么要重建**:基岩没有"引擎第一人称手臂变换",手臂摆到相机前全靠原版 `root`
   控制器 `first_person` 状态里的那几条动画 —— `base_pose` 写 **`body`** 的
   `[俯仰, 偏航, 0]`、`empty_hand` 写 **`rightarm`** 的 `pos[13.5,-10,12]/rot[95,-45,115]`,
   外加 `swap_item`/`walk`/挥击。Java 那边则是独立渲染域:arm 几何按
   `translate(∓0.25, 1.8, 0) + scale(-1,-1,1)` 刚性放进原版手臂的姿态栈,换算下来正好是
   **arm 模型坐标整体移位 `(∓4, -4.8, 0)`(右臂 -4/左臂 +4)后挂在原版手臂骨骼上**。
   于是产物长这样:

   ```text
   body            pivot [0,24,0]    ← 原版 base_pose(视角俯仰+偏航)
   ├ rightArm      pivot [-5,22,0]   ← 原版 empty_hand / swap_item / walk / 挥击
   │  └ RightArm_ysmfp …             ← Java 右臂子树, 全体移位 (-4,-4.8,0), bind 旋转保留
   └ ysm_fp_leftarm pivot [5,22,0]   ← 左臂子树; 主包动画 scale 0 隐藏(基岩只渲染主手侧)
   ```

   > 旧做法(嫁接主几何父链)已撤销:父链在第一人称是静态的,**补不出俯仰** ——
   > 2026-09-18 逐包核对发现 25/25 个旧产物的 arm 几何都没有 `body` 骨骼,原版
   > `base_pose` 整条落空,抬头 30° 手臂就掉出画面、45° 跑到相机背后。

   **骨骼命名**:只要按 Java 官方约定把手臂放在 `RightArm`/`LeftArm` **两个根组**下
   (Wiki 要求从主模型复制这两组到新项目根目录)即可,组内子骨骼叫什么都行 ——
   第一人称渲染的是整个 `RightArm` 子树(与 Java 一致),不再按名字前缀过滤。
   自创根组名(如 `arm_r`)则退化成"整份挂到主手包装骨骼下、不做移位",位置要自己调
   (转换日志会告警)。与原版第一人称动画撞名的骨骼(`body`/`head`/`rightArm`/`leftArm`)
   会自动改名 `<原名>_ysmfp`,`fp_arm` 动画里的引用同步改写。
4. 没有 arm 模型也没关系:不声明 `model.arm` 时第一人称自动回落主几何(等效 Java 旧版
   剔除法,父链天然完整),先跑通再精修——主包内置的 4 个模型走的都是这条路。

缩放:`properties.height_scale` 会自动换算;几何 description 里的
`ysm_height_scale`/`ysm_width_scale` 声明也照 Java 语义读取。

### 第 4 步:贴图

1. 全部丢进 `textures/entity/<包名>/`,**保持 Java 原文件名**——`files.player.texture`
   里的包内路径(`textures/skin.png`)会自动按"文件名+约定目录"拼装,声明零改动;
2. 想放别的目录:顶级 `texture_path_prefix` 换前缀,或用
   `{"皮肤名": "完整路径"}` 直通形态逐张写死;
3. PBR 附属贴图(`normal`/`specular`)不会被使用,可以不搬;
4. 作者头像、`properties.icon` 的图也放进资源包,引用路径**不带扩展名**。

### 第 5 步:动画文件(移植大头)

#### 5.1 动画 ID 加前缀

Java 动画文件里的键是裸短名(`idle`、`walk`、`hold_mainhand$minecraft:bow`);基岩动画
是全局命名,裸短名直接报错。规则:

> **`<原短名>` → `animation.<包名>.<原短名>`,只加前缀不改短名,并整体转小写。**

- 键名差异(`use_righthand`↔`use_mainhand` 等)**不用你改**,主包自动映射;
- Java 允许 `walkBack`/`PefectDef` 这种大小写短名,基岩 ID 只接受 `[a-z0-9_.]`
  ——**统一转小写**,控制器/轮盘里的引用同步改(骨骼名不受此限);
- **中文名必须转拼音**:Java 允许 `攻击A`、`持剑走路_前` 这类中文动画名与控制器
  状态名,基岩标识符只收 ASCII。实机报错 `child '散热开始' not valid here`(该控制器
  整个失效);动画 ID 含中文则触发整份文件静默作废。移植工具自动转拼音
  (`攻击A` → `gongjia`、`散热开始` → `sanrekaishi`),并**同步改写**控制器里的
  动画引用、状态转移目标,以及 `ysm.json` 里的轮盘键与 `preview_animation`
  ——手工移植时这四处必须一起改,漏一处就是"动画不播但零报错";
- 前缀想用别的(不跟包名走),顶级声明 `animation_namespace` 即可。

#### 5.2 条件动画名转义

`$` `:` `#` 在基岩动画 ID 里是非法字符,按下表转义(**主包按转义名识别并自动合成
molang 播放条件**,语义与 Java 一致、优先级同为 id > tag > 分类):

| Java 写法 | 网易写法 | 说明 |
|---|---|---|
| `hold_mainhand$minecraft:diamond_sword` | `hold_mainhand.id.minecraft.diamond_sword` | `$` → `.id.`,ID 里的 `:` 也变 `.` |
| `legs#forge:armor/diamond` | `legs.tag.minecraft.xxx` | `#` → `.tag.`;**注意 forge tag 在基岩不存在**,换成基岩物品 tag 或改用 `.id.` 枚举 |
| `use_mainhand:eat` | `use_mainhand.cls.eat` | `:分类` → `.cls.` |
| `hold_mainhand:empty` | `hold_mainhand.cls.empty` | 空手判定 ✅ |
| `vehicle$minecraft:pig` | `vehicle.id.minecraft.pig` | 骑乘条件 ✅(实体 ID 自动 JE↔BE 映射) |

支持的前缀:`hold_mainhand/offhand`、`swing`(主手)、`use_mainhand/offhand`、
`head/chest/legs/feet`(甲槽)、`vehicle`。`swing_offhand` 族**整族不播**(基岩无副手挥击信号)。
同名物品在两个通道都有动画时,覆盖顺序按 Java 通道注册序:`hold_offhand` → `hold_mainhand` → `swing` → `use`(后者压前者)。**不支持**(见 §4.2):`passenger`、`chair`、
甲槽 `mainhand/offhand`、`<slot>:default` 兜底,及模组分类 `slashblade`/`gohei`/`lance`。

#### 5.3 molang 替换

Java YSM 的 `ysm.*`、`ctrl.*` 与自实现的部分 `query.*` 在网易引擎不存在,**一个未知
token 会让整份动画文件静默作废**。逐文件处理:

1. 搜 `ysm.` 与 `ctrl.` ——逐条按[映射清单](ysm-java-molang-mapping.md)替换
   (高频条目见附录 §5.2);
2. **同名陷阱**必查:`query.head_x_rotation`/`head_y_rotation` 两边**轴向相反**、
   `query.ground_speed`/`query.yaw_speed` 网易噪声不可用(替换见附录);
3. 赋值语句**补分号**:`"v.x=math.cos(q.anim_time)*5"` → 结尾加 `;`(Java 宽容,
   基岩必须是完整语句——官方 main 动画就有 9 处,漏了直接废 46 条动画);
4. `??`(空值合并)基岩原生支持,但主包会把变量自动初始化为 0,`??` 永不触发——
   改写为变量本身,默认值写进顶级 `initialize`(保住"未设置取默认"语义);
5. `v.roaming.<名>` 扁平化为 `v.roaming_<名>`(丢持久化,重进游戏重置);
6. Java timeline 里的 `ysm.particle()` 调用**自动转成**基岩动画原生 `particle_effects`
   关键帧(粒子 ID 换成原版基岩粒子、位置打成根骨骼 locator、效果键登记进
   `files.player.particle_effect`,见 molang 映射表);`ysm.play_sound()` 仍置零删除;动画
   **`sound_effects` 关键帧自动移植**(2026-09-16):ogg 从 `sounds/` 拷到 `ysm_rp/sounds/ysm/<包>/`、
   `sound_definitions.json` 登记、`files.player.sound_effect` 注册,关键帧 `effect` 改写为
   `ysm_snd_<名>`(Java 源码快照的编译管线其实丢弃了这个字段,但官方 wiki《添加音频》承诺
   2.3.0+ 可用,工具按 wiki 语义搬运);
7. Java 的"每 tick 脚本"惯用法(`loop:true` + **显式** `animation_length: 0` + `timeline`,如凋灵娘逐
   tick 把眼球变量写成头部朝向)自动改成 0.05s 循环——基岩对 0 长度动画只在起播时触发一次
   timeline,不改眼球就不跟视角。**不写长度**的动画是另一回事:Java 把"无长度且无关键帧"算成
   无限长(timeline 只跑一次、永不 finished),工具显式写成 1e6 秒(凋灵娘 `voice_set_N` 轮盘键
   靠它只设一次 `v.voice_short=N`);有关键帧的写成末关键帧时间。
8. **Java 容错、基岩不容错的写法**(2026-09-17 官方酒狐合集 7 个包因此整份文件拒载):Java 标识符不分
   大小写(`YSM.head_yaw` 要先按小写映射)、`ysm.bone_rot('骨骼').x` 这类结构体成员访问要连 `.x` 一起替换、
   作者笔误(把 `0` 打成字母 `O`、多一个右括号)Java 只让这一个值变 0 —— 基岩任何一个表达式解析失败,
   **整份文件**的动画全部作废。工具最后一步按 Java 口径把解析不了的值落 0 / 删条目并在报告里逐条
   `[!] zero:基岩解析不了…` 留痕,手工移植时对照 [molang 映射表 §六](ysm-java-molang-mapping.md) 的实测表自查;
9. **纸娃娃(选择界面预览)没有实体**:骨骼通道里的 `query.position` 会逐帧刷
   `called without an entity specified`。工具给玩家侧动画的 `position`/`position_delta`/`is_item_name_any`
   调用包一层 `((variable.ysm_show??0)+(variable.ysm_preview??0)>0?0:原调用)` —— 这两个变量只有界面
   纸娃娃设为 1,世界里取值不变。

#### 5.4 摆放与声明

文件放 `resource_pack/animations/<包名>/`,`files.player.animation` 推荐**列表混排**:

```jsonc
"animation": [
    // 字符串 = 按路径导入整个文件(该文件全部动画注册进模型, 短键=ID 去前两段)
    "animations/<包名>/main.animation.json",
    "animations/<包名>/extra.animation.json",
    // dict = 逐条注册/键名重定向(跨包共享动画、键名映射等场景)
    { "shield_left": "animation.fight.shield_left" }
]
```

写 Java 原包路径(`animations/main.animation.json`)也行——按文件名匹配并做
**命名空间校验**(候选文件必须真含 `animation.<包名>.*`,不会捡走别家的同名文件)。
Java 原生 dict 槽位形态(`{"main": 路径, "extra": 路径}`)同样原样可用。

#### 5.5 标准动画键

主包自动把 23 个标准状态键(`idle`/`walk`/`run`/`jump`/`swim`/`sneak`/…)按命名空间
注册,你的动画文件里**有的生效、没有的只产生一条无害引擎日志**(与 Java 官方转换教程
FAQ 的说明一致)。`death`/`ladder_up`/`ladder_stillness`/`ladder_down` 在你提供了
同名动画时自动补播放条件(判据与 Java `AnimationRegister` 逐条对齐)。

#### 5.6 模组联动槽位处置

| Java 槽位 | 处置 |
|---|---|
| `carryon`(Carry On) | **原样移植即可**:`carryon:block`/`entity`/`player`/`princess` 四条按条件动画转义为 `carryon.cls.*`,主包按搬运状态(`ysm_carryon` 1=实体 2=方块 3=玩家,princess=被玩家抱起)自动合成播放条件——网易版自带搬运玩法,语义同 Java。旧通道(旧版 list 声明形态的包)也可沿用改下划线(`carryon_block` 等)覆盖主包基线搬运动画的做法 |
| `tac`(枪械) | 网易走 TACZ 联动的 `tac_*` 基线键位体系(见格式手册),Java `tac:` 系条件名会被跳过并汇总告警;想做枪械动画请按网易键位重排,属高级内容 |
| `parcool` / `swem` / `slashblade` / `tlm` / `immersive_melodies` / `irons_spell_books` | 基岩无对应模组,**不移植**(文件不搬即可) |

### 第 6 步:动画控制器

Java 2.3.0 起支持的基岩版动画控制器格式,网易**原生就是它**,大部分照抄:

> **molang 脚本控制器**(`functions/<名>@player_ctrl_<通道>.molang`,每帧执行、`ctrl.set_animation` 选动画):
> 移植工具把"条件块套条件块、叶子上 set_animation + return"的决策树自动展开成控制器(`devtools/script_controller.py`,
> 官方酒狐 15 号的主链姿态就在 `@player_ctrl_pre_main` 里)。只转内置谓词为空的 `pre_*` / `post_*` 通道;
> `main` / `use` / `swing` / `parallel_N` 的脚本(放行要交回内置动画)和带计数器、随机数、逐帧积分的脚本会跳过并在报告里标
> `[!]`,这类逻辑请手写成普通控制器。

1. 控制器 ID 改 `controller.animation.<包名>.<名称>`,文件放
   `animation_controllers/<包名>/`(移植工具已自动:Java 的控制器名就是**通道名**
   如 `player.parallel_0`,点会被换成下划线成为注册键 `player_parallel_0`);
2. `files.player.animation_controllers` 写路径(文件内全部控制器导入并**常开**,
   与 Java"声明即接管"一致);要挂/改播放条件用 `files.player.animate` 同键覆盖;
3. 控制器内引用的动画短名 = 注册键(文件导入的短键或 dict 显式键);
4. **Java 扩展降级**:
   - `blend_transition` 写成 `{时间:位置}` 点集曲线 → 网易只支持**数字**,工具取曲线总长(最大时间键);
   - `blend_transition` 的**归属两边相反**:Java 是"被进入状态的淡入时长",基岩是"离开该状态时的
     淡化时长"。各态同值无感;各态不同值时工具按目标重映射(源态取出边目标 Java 值的最大值),
     **只在移植期做一次**(修复工具不碰,重跑会漂);
   - 状态里 `{动画: 表达式}` Java 是**布尔条件**(非零即播、全权重),基岩是**权重**:工具把非布尔式
     包成 `((expr))!=0`,非零常量去掉条件;多键条目 Java 只取首键,工具同样只留首键;
   - Java 的跨态过渡是四元数 nlerp(最短路径),工具给带 blend 的状态统一补 `blend_via_shortest_path: true`;
   - 控制器名不是 Java 通道名(如注释掉的 `#player.post_main2`)、或 `initial_state` 查无的控制器
     Java 不会动作,工具跳过并在汇总里留痕;
   - `ysm-builtin` 状态(还权硬编码)→ 删掉该状态,保留主包基线控制器的对应行为即可;
   - `ysm-entry-<x>` 子控制器 → **展平**进父控制器;
   - molang 脚本控制器(`ctrl.set_animation`/`set_beginning_transition_length`)→
     重写为普通状态机;
5. `q.all_animations_finished` / `q.any_animation_finished` 基岩原生可用,**但口径不同**:
   基岩里权重为 0 的条目暂停计时、算作没播完 —— 状态里挂着互斥条件动画(`{a: v.x==0}`/
   `{b: v.x==1}`)时 all 永不成立,状态卡死(Java 只统计条件为真的动画)。移植工具会把这类状态的
   判据改写成按进入时刻计时的显式式子(`RewriteFinishedQueries`,读 `variable.ysm_t0_<控制器>`);
   `blend_via_shortest_path` **基岩真的生效**(Java 解析但不执行——同一份控制器
   两边旋转过渡观感可能不同,留意)。
6. **通道接管语义**:Java 里声明 `player.parallel_2` 控制器 = 该通道改由控制器决定
   播什么、不再自动播 `parallel2` 动画。网易侧没有通道概念,控制器与动画都是常开
   条目,**两处同时播会叠加**——移植工具会自动为被接管的通道写
   声明的通道不再自动直挂同名动画 —— **主包自动识别**(控制器名 `player_parallel_N`,
   或任何自有控制器的状态引用了 `parallelN`), ysm.json 里不需要写任何删除清单。

### 第 7 步:轮盘与配置表单

`properties.extra_animation` 三件套零改动。三点差异:

- **按钮格**:Java 同一格外圈播动画、内圈开配置表单;基岩选择轮盘分不出内外圈,点按钮格打开右侧
  表单页,页顶的"播放动画"就是外圈动作(该键没有动画时不出现)。格子数、顺序、`#return` 占格与
  5 层嵌套上限都与 Java 一致;
- **停止条件**:Java 的"移动打断+锁定按钮"在网易是**停止表达式**——系统默认
  "移动/跳跃/潜行即停"(与 Java 默认行为对齐),顶级 `extra_stop_expression` 可换
  条件或置 `""` 永锁(无游戏内锁定快捷键,也没有根级 8 个热键);
- **显示名**:主包运行期不读 `lang/*.json`,`extra_animation` 的值就是最终显示文本;
  用移植工具转换时 `zh_cn.json` 的译名会自动烘进去(官方 03 宇航员酒狐的轮盘值全空、只靠
  语言表给名字)。

### 第 8 步:弹射物 / 载具

`files.projectiles` / `files.vehicles` 两种形态(对象 / `match` 数组)照抄。弹射物模型按 Java 约定建(前方朝 +X),
移植工具会外包一层 `ysm_projectile_root` 朝向根骨骼,箭矢/三叉戟在游戏里自动跟随射击方向与下坠并按 0.7 缩放
(对齐 Java `GeoProjectilesRenderer`),模型里不必自己写朝向动画:

- 实体 ID 自动映射 JE↔BE 差异;`match` 里的 `#实体tag` 展开成具体 ID;
- 模型/贴图按 `<包名>` 推导;多模型共用一套箭矢时,`model` 直接写 `geometry.*` ID、
  `texture` 写无扩展名路径即**直通**;
- 动画的状态键(`water`/`fire`/`ground`/`air`;载具 `water`/`ground`/`fly`/`forward`/`idle`/`has_ride`/`not_ride`)
  自动合成互斥播放条件,与 Java 谓词逐条对齐;`parallel0-7`(载具另有 `pre_parallel0-7`)常开;
  **其他名字的动画不会自己播**(Java 同样没有通道播它,需要控制器引用);声明了 `controller` 则控制器接管;
- 同一模型给不同实体配了不同动画文件(如马和骡子共用一个车模型)时,工具按"模型_动画"分开命名空间,
  动画产物改名为 `<模型段>_<动画段>.animation.json` 并同步改写声明路径;
- 载具模型按 Java 口径缩放 0.7:工具外包一层 `ysm_vehicle_root` 根骨骼,运行层在它上面播缩放,
  模型里不必自己缩放(Java 硬编码 0.7,不读 ysm.json 的缩放);
- 弹射物射出时替换;载具**第一乘客上车**时写入该玩家的模型,下车不撤、随实体存档一直留在载具上
  (播 `not_ride`),直到下一个第一乘客上车覆盖 —— 与 Java 一致;
- 载具动画里的 `sound_effects` 关键帧照常登记(ogg + `sound_definitions` + `files.player.sound_effect`,
  与玩家侧同一套),运行层给载具注册音效表;
- 船/运输船/矿车在基岩是**引擎硬编码渲染**(换在实体上的几何根本不画),运行层改用客户端替身实体顶上,
  作者侧无需额外配置。矿车的实体朝向在基岩恒为 0,替身按**车底轨道**定朝向、按行驶方向定车头正反 ——
  轨道弯道处的朝向按两端角平分线取,和 Java 一样不做上坡俯仰。

### 第 9 步:预览实体定义(基岩独有)

Java 的选择界面直接渲染模型;基岩的 GUI 预览需要一个**客户端实体定义**。
新建 `resource_pack/entity/<包名>.entity.json`:

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

`identifier` 必须等于模型 ID(默认 `ysm_pack:<包名>`);渲染控制器统一用
`controller.render.ysm_pack_gui`(所有模型共用,多皮肤由主包运行期动态注入,
**无需自写 GUI 控制器**)。

⚠️ **包变量必须在这里初始化**:GUI 预览纸娃娃是独立实体实例,主包对玩家做的
molang 变量初始化到不了它。预览会播放你的 `parallel*`/`pre_parallel*` 并行动画
(隐藏装饰件靠它)——其中引用的**全部自定义变量**(换装开关、定位组、表情等)
都要在 `scripts.initialize` 里补上(有默认外观语义的给默认值,如
`variable.breast_outfit = 1;`,其余补 `= 0.0;`)。缺一个,引擎就对该骨骼通道刷
`unhandled request for unknown variable`;缺得多,**整个预览实体直接不渲染**
(实机:选择界面缩略图空白)。这是本包自己的实体定义,用原生 initialize 即可(运行期
给它挂初始化控制器实机不生效)。移植工具自动扫描产物合成这份清单;已移植的包用
`python devtools/fix_ported_controllers.py <包名>` 回填。玩家**自身**的纸娃娃(原版
背包、模型设置界面)同样是独立实例,由移植工具生成的每包控制器
`animation_controllers/<包名>/ysm_variable_init.json` 负责:主包发现即自动恒开注册,
每个渲染实例创建时跑一次 `v.x = v.x ?? 默认值;`(世界实体已有的值不被顶掉),同样用
上面的修复工具刷新;**不要去改玩家实体定义** `entity/player.entity.json`。

### 第 10 步:进游戏验证

1. **资源包改动必须完整重启游戏**——热重载(R 键)不重扫资源包,新增/改动的动画、
   几何用的还是旧索引;
2. 日志搜 `[YSM-PackLoader]`:`扫描完成: … 注册模型 K 个` + 逐包解析警告;
   `模型 X 引用了 N 个不存在的资源` 按列出的键补资源;
3. **模型全身僵直、无任何报错** = 某份动画文件整份作废(基岩解析是整文件粒度,
   一处非法全文废),用探针定位:
   ```python
   comp = clientApi.GetEngineCompFactory().CreateActorRender(playerId)
   print(comp.AddPlayerAnimation("probe", "animation.<包名>.walk"))  # False = 文件没加载
   ```
   然后回到第 5.3 步的四条红线逐项排查;
4. 其余症状(模型没被发现/ID 重复/GUI 空白)见[格式手册·排错](ysm-json-pack-guide.md)。

---

## 4. 与 Java 版的行为差异(创作者视角)

### 4.1 动画系统

两边的"底盘"不同:Java 是 **24 路通道 + 硬编码状态机**(可被同名控制器接管),
网易基岩是 **animate 条件表 + 动画控制器**,主包已把 Java 状态机复刻成一组基线控制器。
对普通模型包**无感**;写自定义控制器前建议读[机制对照](ysm-java-animation-mechanism.md)。
创作者可感知的差异:

| 差异点 | Java | 网易基岩 | 影响 |
|---|---|---|---|
| 疾跑判定 | `isSprinting()` 状态位 | 移动速度阈值(0.87) | 速度药水/特殊移动下 run/walk 切换点略不同 |
| `jump` | "腾空"(含下落) | 垂直速度门控(引擎 `is_on_ground` 走路会抖,已做防抖) | 短坠落的姿态时序略不同 |
| **通道分层与混合** | 按通道注册顺序,每个 (骨骼, 通道) 由**最后写它的通道**决定(`pre_parallel→vehicle→pre_main→main→post_main→hold→swing→use→carry_on→cap→parallel→armor`,同族按控制器名排序)。只有**内置**并行通道(裸 `parallel0-7` 恒播)旋转做加法;作者用控制器接管的通道(含 `player.parallel_N`)位移/旋转/缩放**全是覆盖** | 引擎**逐通道相加、缩放相乘**(2026-09-16 实机探针);主包复刻:条件动画/轮盘动画带 `override_previous_animation`;pre/主链/parallel 不带(标志会吃掉状态淡化),冲突由移植工具在数据层解决 —— 总是同时在播的直接删早层通道,晚层是**控制器条件状态**的用伴生动画 `<键>__own<N>` + 占用变量按状态让出(`ApplyChannelOwnership`,带 override 的条件动画同样拆,伴生精确 0 让位);animate 表里带伴生的晚层宿主倒排在最前(占用变量当帧生效,顺排会在切状态那一帧闪一下),override 条件动画按 Java 通道顺序排在后 | 转换包无感。**手写移植时注意**:持剑/持镰这类控制器动画在 Java 里是整套姿态覆盖主链,基岩直接相加会全身旋转翻倍;前置层用 `scale 0` 藏起、晚层攻击动画再放出来的特效,基岩 0×1 永远不可见(坚守者娘持剑攻击特效缺失)。建议经移植工具出包,或让同一骨骼通道只由一条动画写 |
| **伴生动画 `<键>__own<N>`** | 无此概念 | 移植工具生成:原动画里会被晚层覆盖的通道搬进它,与原动画同状态播放,权重读 `variable.ysm_ownset_<n>`(晚层控制器状态进入时重算)。不带 override 的伴生让出时保留 1e-4 权重(基岩权重 0 会暂停计时);带 override 的(手持/挥击/使用条件动画)精确 0 让位,直挂条件动画的核心权重在 ysm.json 顶级 `channel_ownership`、挥击/使用的写在一次性状态机里;GUI 预览与纸娃娃里主包把它跟着原动画一起播 | 键名后缀 `__own<数字>` 视为工具保留,**别给自己的动画起这种名字**(修复工具会把它当伴生并回原动画);`variable.ysm_own_*` / `variable.ysm_ownset_*` / `variable.ysm_t0_*` 同为保留变量,`channel_ownership` 由工具维护、不要手写 |
| **关键帧只写 `post`** | `post` 同时当 `pre` | 线性入段取**通道默认值**(scale 1、rotation/position 0),段内会朝默认值插值再跳回 | 实机:凋灵娘火焰精灵帧莫名缩放。工具给所有只写 post 的帧补同值 `pre`;手写时 `{"post": …, "lerp_mode": "linear"}` 请同时写 `pre`(Blockbench 导出的平滑帧不受影响) |
| **catmullrom 的分段归属** | 段 [i, i+1] 任一端是 catmullrom 就走样条 | 关键帧 i 的 `lerp_mode` 只管出边;为它预计算样条的四帧 i-1..i+2 必须全是常量,否则日志报 `Precomputed cubic interpolation requires keyframes have constant data`、整段退化 | 工具移植期按 Java 语义换算出边标记,再把窗口内有表达式的 catmullrom 改 linear;`validate_rp_animations.py` 对残留报错 |
| 原版动画栈 | 整体替换原版模型渲染,原版 HumanoidModel 动画一条都不作用;头部跟踪由代码叠加 headPitch/netHeadYaw | 玩家实体自带原版 `root` 控制器(move.arms/legs、look_at_target、bob、attack…),Java 模式主包把 `root` 收敛为 `variable.is_first_person`(只在第三人称/纸娃娃域关停),再以主包自带的 `animation.ysm.java_head_look`(键 `ysm_java_head_look`)补回 Java 的代码级头部跟踪 | 无感;不关停就是原版腿臂摆动叠在 Java 动画上的鬼畜。⚠️ **第一人称必须放行**:原版 root 的 `first_person` 状态是基岩侧手臂摆位的唯一来源(Java 的 FP 是引擎独立渲染域),整体置 `0` 会让手臂停在建模原位、贯穿屏幕。⚠️ 头部跟踪**不能直接借原版 `animation.humanoid.look_at_target.default`**:它带 `relative_to: {rotation: entity}`,按实体世界朝向摆 `Head` 骨骼——原版 humanoid 的 head 是根骨骼直接子级故等价,YSM 模型的 `Head` 埋在十层父级下,实体空间会顶掉沿途父骨骼旋转,实机表现为"看正前方头却是歪的" |
| 动画缺失回退 | 自动回落官方 default 模型动画 | **缺失就是缺失**(刻意设计,不回退旧资源);要 Java 式兜底, 安装 `java_default` 基线包即可(Java 模式按声明形态自动判定, 无需开关) | 缺的动画表现为无动作而非"别人的动作" |
| **鞘翅滑翔的实体层旋转** | 渲染器继承 `LivingEntityRenderer`(不是 `PlayerRenderer`),实体层**一点不转**(死亡翻转与激流旋转也被显式清掉)——滑翔姿态全靠模型自己的 `elytra_fly` 动画摆(官方包普遍把根骨骼转 90° 让身体躺平) | 引擎在 `query.is_gliding` 时给**整个模型**额外加 `90 + pitch` 度俯仰(`ActorRenderData::getDamageOrGlidingXYRotation`;2026-09-18 实机定量:绕脚底的**纯旋转、不带平移**,骨骼矩阵里查不到) | ⚠️ 两者叠加 = 水平飞行时模型头朝下。移植/修复工具给主几何外包 `ysm_glide_root`(枢轴 0、无变换),主包 `animation.ysm.java_glide_fix` 在它上面反向转 `-(90+pitch)*clamp(4t²)`(Java 的渐入系数),由共享控制器 `controller.animation.ysm.java_glide_state` 进滑翔态时播。**手写包不用管**,但别给骨骼起 `ysm_glide_root` 这个名字 |
| `riptide`(激流) | ✅ | ❌ 引擎无对应查询 | 激流姿态缺失 |
| GUI `hover`/`hover_fadeout`/`focus` | ✅ | ❌ 网易 UI 不驱动模型 hover;预览走 `preview_animation` + 预览动作表 + `preview_parallel` | 选择界面交互动画不同 |
| 挥动/使用互斥 | `PAUSE`(暂停不清骨骼) | molang 条件门 | 极端时序下过渡细节略不同 |
| 动画帧率 | 60fps 上限 | 引擎渲染帧率 | 无感 |
| 结尾过渡 | 固定 3 tick | 控制器 `blend_transition` 兼任 | 自定义控制器时自己给过渡值 |
| **渲染域隔离** | GUI/纸娃娃/第一人称是独立渲染域,主域动画天然不进 | 同一实体共用 animate 表,主包给自动条目**统一加门**:条件/状态动画 `!v.is_paperdoll && !v.is_first_person`,自定义控制器同双门,并行动画同双门 | 手写 `files.player.animate`/`animate_extra` 条件时**自己带这两个门**——不带的话纸娃娃上物品/骑乘 query 会逐帧刷错,第一人称手臂会被主域动画驱动乱摆。纸娃娃的装饰件隐藏另由主包以独立键 `paperdoll.<并行键>` 补齐:只收**不含 query** 的 `parallel*`/`pre_parallel*`(纸娃娃是无装备上下文的实体),与主域键错开故不受控制器接管通道时的主域键摘除影响。玩家纸娃娃实例上的**包变量**由每包控制器 `ysm_variable_init`(移植工具生成,主包自动恒开注册,`on_entry` 逐条 `v.x = v.x ?? 默认值;`)补齐,不改玩家实体定义 |
| GUI 预览的装饰隐藏 | 并行通道在 GUI 照常运转 | 预览实体不跑 animate 表;主包把模型自有 `parallel*`/`pre_parallel*` 自动叠加进预览(缩略图+大预览窗,`preview_parallel` 机制) | 无感;显式声明 `preview_parallel` 可覆盖 |
| **复杂表达式的返回值** | 含 `;` 的表达式返回最后一条语句/赋值的值 | 按微软 Molang 规范,含 `;` 而无 `return` 的表达式**返回 0** | ⚠️ 通道值写成赋值(`v.player_size=v.player_size;`)在基岩等于 scale 0 → **整个模型不可见**;纯表达式带尾分号(`(...)+(...);`)姿态归零。移植工具自动规范化(去尾分号 / 补 `return`),手写时别在通道值里留分号 |
| **控制器状态里的空 `animations` 数组** | 合法(该状态不播动画) | ❌ **整份控制器文件被引擎拒载**——文件内所有控制器的 `AddPlayerAnimationController` 全返回 `false`,与 ID 不存在同表现,且不打任何日志 | 实机二分定位(2026-09):同为转换产物,`ref_sahmet`(0 处)正常,`ref_warden`(15 处)/`ref_wither`(13 处)整份失效,后果是包自带的 `pre_parallel` 装饰件隐藏动画永不播放 → 玩家身上糊满配件模型。移植工具剪枝后**空列表一律删键**(不写 `animations` 键是合法的) |
| **动画里的空 `bones` 节点** | 合法(该动画不动骨骼) | ❌ 引擎报 `Required child [a-zA-Z0-9_.-]+ not found / node parse failed: bones`,**该文件全部动画作废**(Debug_Log 只留这几行 ERROR) | 实机(2026-09-03):移植工具把 pre 通道动画的冲突通道剥空后留下 `"bones": {}`,`ref_warden`/`ref_sahmet` 的 main.animation.json 整份失效,主链/pre/parallel 一起消失 → 模型停在绑定姿态、换装件与表情面片全亮("显示配件模型")。工具现在剥空即删 `bones` 键(`SanitizeAnimationBody`);`devtools/validate_rp_animations.py` 把空 bones/空骨骼/空通道列为 ERROR |
| **一个表达式解析失败**(作者笔误 `O`、多余右括号、`YSM.` 大写前缀、`bone_rot('x').x` 残留成 `0.0.x`) | 只作废这一个值(`parseExpression` 捕获异常返回 0) | ❌ **整份文件拒载**——文件里全部动画 ID 查无,模型切过去没有并行动画、装饰件全亮、主链动作全空(纸娃娃同样) | 移植工具最后一步 `molang_syntax.Guard*` 按 Java 口径落 0 / 删条目(报告 `[!] zero:基岩解析不了…`);`validate_rp_animations.py` 以引擎实测口径拦残留;在游戏里用 `mcdkSelfTest.mcdk_test_probe_all_model_resources("包名")` 逐 ID 探测是否被拒载 |
| **timeline 里的声明语句 `v.x ?? v.x = 0;`** | 合法(未定义时赋默认值) | ❌ `??` 降级为 `\|\|` 后成了表达式内赋值,引擎报 `assignment to non-variable not allowed`,整条语句作废并逐次刷错 | 移植/修复工具改写为 `v.x = v.x ?? (0);`(`??` 引擎实测可用,变量初始化控制器同款) |
| **"每 tick 脚本"动画**(`loop:true` + **显式** `animation_length: 0` + `timeline`) | geckolib 每帧重播 0 长度循环动画,timeline 语句每帧执行 | ❌ 只在起播时触发一次(凋灵娘眼球变量不更新 → 眼睛不跟视角) | 工具改成 0.05s(1 tick)循环,每圈触发一次,与 Java 同拍 |
| **不写 `animation_length` 且没有带时间戳的关键帧** | 无限长(`Float.MAX_VALUE`):永不结束、timeline 只跑一次、`all_animations_finished` 永假 | 按关键帧推成 0 长度:立即结束、起播触发一次 | 工具显式写 1e6 秒(凋灵娘 `voice_set_N` 轮盘键:早先误当每 tick 脚本,变量每 tick 被顶回去,站着不动反复重进语音状态);有关键帧的写末关键帧时间 |
| **动画 `sound_effects` 关键帧** | 写 `sounds/` 下文件名或原版 ID(wiki《添加音频》) | 效果键必须经 `AddPlayerSoundEffect` 注册且 `sound_definitions.json` 有定义,缺一无声 | 工具三件套自动生成;`validate_rp_animations.py` 核对注册与定义 |
| **控制器状态里没写循环的动画**(PLAY_ONCE,如萨赫梅特的 `Sword_Attack_1`) | 播到长度即"播完",随后 3 tick 尾过渡把姿态淡到 0、不再写通道 —— 状态没切走也会收回(连段中切换物品,出口都要求持剑,攻击姿态照样收回) | ❌ `loop:false` 播完即撤(出态前闪一帧底层姿态);补成 `hold_on_last_frame` 又会在状态停留时一直定格 | 工具补 `hold_on_last_frame` 防闪帧,同时给作者状态里的这类条目乘 0.15s 淡出权重,淡出结束后早层通道收回,与 Java 一致。**想让姿态停在末帧就显式写 `"loop": "hold_on_last_frame"`**(凋灵娘的攻击 A/B/C 就是这么写的,Java 与基岩都会定格) |
| **状态机里不播动画的中转状态**(如凋灵娘的 `cache`:落地后 `jump_down → cache → idle`) | 切换时从当前姿态快照插值,经过空状态只多花 1 tick,姿态连续 | ❌ 交叉淡化是"出态权重 1→0 + 入态 0→1",入空状态时没有入态动画,姿态先淡到**绑定姿态**再淡进下一状态——落地一瞬间直立 | 工具给每条 `X→空状态` 前置 `X→Y` 旁路转移(条件相与,目标限有动画的状态,矛盾/自环/含 `all_animations_finished` 的不插),一步到位只做一次动画间淡化 |
| **手持物品的挂点** | 渲染在 `RightHandLocator` / `LeftHandLocator` 骨骼上(`PlayerLocator` 注册表 + `CustomPlayerItemInHandLayer`),定位组可有多个成员(`RightHandLocator2/3` → 渲染多份) | 只认**固定骨骼名 `rightItem` / `leftItem`**(原版 `geometry.humanoid.custom` 里 `rightItem` 是 `rightArm` 的空子骨骼,pivot 在掌心并把 z 前推 1;骨骼内的 `lead_hold` locator 只是拴绳点) | ⚠️ Java 包没有这两根骨骼,直接转换过来**手持物品不在手上**。移植/修复工具自动补:挂到 `<Left\|Right>HandLocator` 下(缺则回落 `<L\|R>Hand`→`<L\|R>Arm`),主几何 pivot = 父骨骼 pivot(与 CSM、作者自制的基岩版凋灵娘一致;早先是 z+1,拉弓动画把定位骨骼放大 2 倍时弓会被甩出去,修复工具会把形状完全等于生成物的旧骨骼挪过来),带 `lead_hold`/`lead_hold2`;第三人称的持物差由主包按物品类别叠修正动画(工具类 `[0,2,1]`、其余非挂载物 `[0,1,2]`、主手弓/弩另叠,数值取自 CSM)。定位组有多成员时取基名那个(基岩只能一根)。**自己写了 `rightItem`/`leftItem` 的包原样保留**,想微调位置就手写(修正动画照样叠加)。父子关系选定位骨骼是有讲究的:三个参考包的 `carryon.cls.*` 都把手部定位骨骼缩放到 0 来隐藏手持物,挂在它下面才能跟着隐藏(与 Java 同语义)。**`models/arm.json`(第一人称)工具同样补上**,但第一人称的手持物已不经它绑定:主包在第一人称把 `default` 几何键换成原版体型 `geometry.default_steve` 作附着物锚点(对齐 CSM),原版第一人称动画原样驱动它(网易原版 `empty_hand` 自带 `q.get_default_bone_pivot` 归一),弓/弩/盾/三叉戟与普通物品都落在原版位置;模型手臂的额外摆位与设置里的 `empty_hand_x/y/z` 只在主手空手时生效,不会挪动物品 |
| **纯中文名的动画** | 名字随便起,`头颅张开（左）`/`右勾拳` 都行 | 资源 ID 必须 ASCII,移植工具转拼音(全角括号等非汉字非 ASCII 转 `uXXXX`) | ⚠️ 移植工具早期把"名字里没有 ASCII 字母数字"当分组标题条目丢弃,**误杀纯中文名的真动画**(2026-09-04 实机:凋灵娘 16 条含两个骷髅头的张开/闭合/待机、持剑奔跑、头发飘动;warden 13 条整套拳击/肘击/防御。名字里恰好带 ASCII 的 `火焰动画A`/`语音13` 侥幸存活)。后果:控制器引用被当死引用剪掉 → 状态变空 → 部件停在绑定姿态、动作全无。现判据 = 名字无字母数字**且**动画体无内容(`bones`/`timeline`/粒子/音效都没有) |
| **模型渲染的背面剔除** | `RenderType.entityCutoutNoCull`,**不剔除背面**,作者据此建模(火焰/飘带/裙摆的单面片两侧都可见) | 基岩 `entity` 材质默认剔除背面 → 这些部件只有从正面看才显示,侧背面透明 | JSON 包(Java 模式)未显式声明 `material` 时自动回退 `bloom_nocull`/`bloom_plus_nocull`(`entity_nocull` 系),预览实体的 `default` 材质同步;显式声明 `material` 的包不受影响 |
| **"隐藏基线 + 条件显示"的装饰件**(火焰/表情/嘴型:pre 通道 `scale 0` 隐藏,变体动画设回 1) | parallel 通道对 position/scale 是覆盖 → 当前变体可见 | ❌ 两条动画共写同一通道时**缩放相乘**,0×1 **不可见**(凋灵娘骷髅头的火焰是几何骨骼 `Fires_*`/`ysmGlowFire_*`,不是粒子) | 常量变体:工具 `ReconcileConditionalVariants` 折叠为单一所有者(`scale: ["(v.Emotions==3)?1:0", …]`);关键帧变体(攻击特效逐帧缩放):`ApplyChannelOwnership` 让 pre 那份在攻击状态活跃时让出。**手写包请直接用一条 molang 表达式控制显隐**,不要两条动画共写同一 scale |
| **`ctrl.jump` 与"在地"判据** | `!onGround && !inWater`,onGround 稳定 | 走路时 `query.is_on_ground` 每秒翻转数次;带最高点死区的判据又会在最高点判"在地" | 主包共享的 `animation.ysm.java_input_state`(animate 表首位)逐帧维护闩锁 `variable.ysm_airborne`,`ctrl.jump/idle/walk/run` 全部改用它(见映射表) |
| 控制器引用缺失的动画 | 静默容忍(查无即跳过) | 引擎每次渲染重建逐条刷 `can't find animation`,且**转移目标状态未定义/条件为空**有连坐整个控制器失效的风险 | 移植工具自动剪掉死引用与非法转移(`fix_ported_controllers.py` 可对已移植包补跑) |

### 4.2 条件动画差集

| 能力 | 状态 |
|---|---|
| `hold`/`swing`/`use` × `$id`/`#tag`/`:分类` | ✅(19 个内置分类,优先级同 Java) |
| `:empty` / `:charged_crossbow` | ✅ |
| `:fishing`(鱼钩已抛出) | ≈ 按持有鱼竿近似 |
| 甲槽 `head/chest/legs/feet` × `$`/`#` | ✅ |
| 甲槽 `mainhand`/`offhand`、`<slot>:default` 兜底 | ❌ |
| `vehicle$` | ✅ `vehicle#`(tag)❌ |
| `carryon:block/entity/player/princess` | ✅(主包搬运状态取值表;Java 模式 princess 由骑乘链互斥驱动) |
| `passenger$`(实体骑头上)、`chair$`(女仆坐垫) | ❌ |
| `swing_offhand` 兜底与 `swing_offhand$/#/:` 条件 | ❌ 整族不播(基岩无副手挥击信号)。早先按主手挥击门近似过,结果是主手攻击时副手动画乱播,已改为宁缺勿错并在解析期告警 |
| `use_offhand` 兜底 | ✅ 基岩副手唯一会被"使用"的物品是盾,按 `query.blocking && 副手是盾` 分手(照抄原版 shield 附着物的判据);主手条件则反过来排除这一情形。⚠️ 不持盾时按住潜行 `query.blocking` 也会变 1(2026-09-16 实机),所以主手的"使用中"只认 `main_hand_item_use_duration>0` 或 `blocking && 主手是盾` —— 早先裸用 blocking,持剑潜行会触发一次挥手。举盾时出手,基岩的 blocking 会掉线几 tick(Java 里使用物品期间攻击键无效),主包共享动画在"仍潜行且持盾"时把格挡锁住,并把这次挥动整次静音 —— 作者的格挡状态不会被打断 |
| 挥动中再挥 | ✅ Java 每次起挥都重播挥击动画(前半程再挥引擎不重启);生成的挥击状态机按主包挥动序号重进状态。连招控制器里"上一段播完且又挥了"的写法因此与 Java 同样生效 |
| `:slashblade` / `:gohei` / `:lance` | ❌ 模组分类,跳过并汇总告警 |
| forge 物品/实体 tag | ❌ 换基岩 tag 或 `$id` 枚举 |

### 4.3 molang

- **未定义变量**:Java 读取缺省 0;基岩必须初始化——主包自动扫描你动画/控制器里的
  全部 `v.*` 补初始化,**体验对齐**,无需手写;
- **Java 专有名**约 100 个 `ysm.*`/`ctrl.*`/`query.*` 需替换(见映射清单);其中
  `head_x/y_rotation` 轴向陷阱、`ground_speed`/`yaw_speed` 噪声替换最容易踩;
- **物理**:`ysm.second_order`/`first_order` 由移植工具改写成 molang 状态积分
  (调用处变成 `v.ysm_so_<键拼音>_y`,积分语句提到表达式前面),头发/尾巴/胸部的
  "Q 弹"随动手感与 Java 一致;键必须是字符串字面量(表达式键退化为取输入并告警);
- **过渡**:Java 主链通道的 0.1s 起始过渡由生成的 `ysm_state` 状态机复刻(直挂
  animate 条目是零过渡硬切),主链成员的 `loop` 按 Java 强制语义改写(睡觉/坐下
  等在 JSON 里写成不循环也会循环;`death`/`attacked` 改 `hold_on_last_frame` 供淡出);
- `v.roaming.*` 持久化域降级为会话级变量。

### 4.4 网易独有增强(Java 做不到的)

- **多皮肤整套差异化**(`skins`:逐皮肤换几何/材质/缩放,Java 只能换贴图);
- **发光/描边/无剔除材质**(`material`,含 bloom 后处理);
- 动画原生 `particle_effects` 关键帧(Java 完全不解析;`sound_effects` Java 侧按 wiki 可用,工具自动搬运);
- 预览动作表(选择界面可切换多段展示动画,Java 只有单条 `preview_animation`);
- 轮盘停止条件可编程(`extra_stop_expression`);
- `strum` 弹奏玩法、模型使用权限管理(服主白名单/tag)、`priority` 列表排序;
- 骨骼位姿回传(`molang_bind_bones_list` → `v.ysm_<骨骼>_rot*/pos*`)。

---

## 5. 附录

### 5.1 高频 molang 替换速查(完整清单见[映射文档](ysm-java-molang-mapping.md))

| Java 写法 | 网易替换 | 备注 |
|---|---|---|
| `query.head_x_rotation` | `query.mod.ysm_head_yaw` | ⚠️ Java 此名是**偏航**(同名反轴陷阱) |
| `query.head_y_rotation` | `query.mod.ysm_head_pitch` | ⚠️ Java 此名是**俯仰** |
| `query.ground_speed` / `ysm.ground_speed2` | `((query.modified_move_speed>0.05)?query.modified_move_speed*1.9:0)` | 网易原生 `ground_speed` 帧间乱跳不可用(实测);低于走路阈值钳 0,作者状态机的 `==0` 静止判据在冰面/潜行微动时仍成立 |
| `query.yaw_speed` | `query.mod.ysm_yaw_speed` | 网易原生噪声不可用,主包提供平滑值(度/秒) |
| `ysm.time_delta` | `query.delta_time` | 作除数,不可置零 |
| `ysm.attack_time` | `(variable.attack_time*(1-(variable.ysm_swing_muted??0)))` | 同为原版挥手进度;使用物品期间开始的挥动读作 0(Java 没有这次挥动) |
| `ysm.swinging` | `((variable.attack_time*(1-(variable.ysm_swing_muted??0)))>0.0)` | 同上 |
| `ysm.is_close_eyes` | `query.mod.ysm_is_close_eyes` | 4.5s 周期眨眼 |
| `ysm.input_vertical` / `_horizontal` | `query.mod.ysm_input_vertical` / `_horizontal` | |
| `ysm.has_mainhand` / `has_offhand` | `query.is_item_equipped(0/1)` | |
| `ysm.on_ladder` | `query.mod.ysm_is_on_ladder` | |
| `ctrl.fly` | `query.mod.ysm_is_flying` | |
| `ctrl.playing_extra_animation` | `query.mod.ysm_wheel_anim` | 开播置 1,移动/动作或单次动画播完清 0 |
| `ysm.rendering_in_paperdoll` | `variable.is_paperdoll` | |
| `query.is_item_name_any('mainhand',…)` | 槽位改全称 `'slot.weapon.mainhand'` | 同族 query 都要改 |
| `math.random_integer(a,b)` | `math.floor(math.random(a,b))` | 引擎无此函数;Java 上界不含,floor 同分布 |
| `math.lerprotate(a,b,t)` | `a+(math.mod(math.mod(b-a,360)+540,360)-180)*t` | 最短角插值展开 |
| `math.acos/asin/atan(x)` / `math.atan2(y,x)` | 结果 `*0.017453292519943295` | Java 返回弧度、基岩返回角度(同名不同义) |
| `math.hermite_blend(t)` | `((3-2*math.ceil(t))*math.ceil(t)*math.ceil(t))` | 按 Java 的 ceil 阶跃实现复刻 |
| `math.e` / `math.roll` / `math.rolli` / `math.random(a,b,c)` | `2.718281828459045` / `math.die_roll` / `math.die_roll_integer` / 截成 2 参 | 基岩无 e;第 3 参基岩拒载整份文件 |

### 5.2 最小可用 `ysm.json`

```jsonc
{
    "spec": 2,
    "metadata": { "name": "我的模型" },
    "files": {
        "player": {
            "animation": [ "animations/my_model/main.animation.json" ],
            "texture": [ "textures/default.png" ]
        }
    }
}
```

几何/模型 ID/命名空间全走推导。**端到端参考**:主包内置 `ysm_bp/ysm_models/` 就是完整
实例——`commander_male/female` = `files` 推导 + 共享箭矢直通 + `_man` 键名重定向;
`wine_fox/<变种>` = 官方 Java"酒狐与小伙伴"合集 22 个变种的移植产物(合集文件夹、高级轮盘
三件套、弹射物/载具、多语言烘焙),命令见 §5.4;`ref_*` = 社区 Java 包的移植产物。

### 5.3 移植检查单

- [ ] 包名小写、带作者前缀;`ysm.json` 在**行为包** `ysm_models/<包名>/`
- [ ] 几何 identifier 已改(`geometry.<包名>` / `<包名>_arm`),`geckolib_format_version` 已删
- [ ] arm 几何已嫁接主几何手臂**父链空骨骼**(或暂不声明 arm)
- [ ] 动画 ID 已加前缀并**全小写**;条件动画名已按 `.id./.tag./.cls.` 转义
- [ ] 赋值语句已补 `;`;`ysm.*`/`ctrl.*` 已按映射清单替换;`??` 已改写
- [ ] 控制器 ID 已改;Java 扩展(点集 blend/ysm-builtin/子控制器/脚本控制器)已降级
- [ ] 预览实体定义已建,`identifier` = 模型 ID,渲染控制器 = `controller.render.ysm_pack_gui`
- [ ] **完整重启游戏**验证;`[YSM-PackLoader]` 日志无错;动画探针通过

### 5.4 批量移植合集(以官方酒狐合集为例)

**先移植 Java 默认模型基线**(只产出资源包动画,不会出现在模型列表里): Java 模型缺的动画会回落内置 default
模型 —— 拉弓/举盾/三叉戟蓄力/吃喝等 57 条手持条件动画、爬行/游泳等标准状态,官方酒狐各包都没写。基线不在场时这些
动作全部缺失,而且各包移植期生成的挥击/使用/主链状态机也认不到基线成员,之后补基线要**重新移植**这些包:

```bash
python devtools/port_java_pack.py .ref/ysm-java-src/src/main/resources/assets/ysm/builtin/default --baseline
```

合集目录(带 `ysm-pack.json`)里的每个子包各跑一次,`--collection` 相同即归入同一个选择界面文件夹,
包名建议带合集前缀防资源 ID 冲突:

```bash
for d in .ref/ysm-java-src/src/main/resources/assets/ysm/builtin/wine_fox/[0-9][0-9]_*; do
    n=$(basename "$d"); python devtools/port_java_pack.py "$d" --name "wine_fox_$n" --collection wine_fox
done
```

野外包常见的非法动画名(引号包裹、首尾空格、`#run#`、`idle to fight`、`ctrl:xxx` 这类以 Java molang
命名空间开头的名字)与中文文件名,工具会自动规整成资源 ID 合法形态并同步改写所有引用;
移植后跑 `python devtools/validate_rp_animations.py` 确认 0 错误。

