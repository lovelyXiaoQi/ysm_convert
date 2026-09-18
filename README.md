# YSM Convert — Java 版 YSM 模型包 → 网易基岩版组件

把 **未加密** 的 Java 版 YSM(Yes Steve Model)模型包目录(含 `ysm.json`)一键转换成网易基岩版 YSM 组件。
Windows 专用。三种用法共用一个转换内核:

| 入口 | 文件 | 用途 |
|---|---|---|
| 图形界面 | `YsmConvert.exe` | 拖入目录、批量转换、合集文件夹(显示名 + 自定义封面)、日志与"开发者提醒"面板、体检、修复 |
| 命令行 | `ysmconv.exe` | 脚本化 / CI: `discover` `convert` `validate` `fix` `baseline` |
| MCP 服务器 | `ysmconv.exe mcp` | 让 AI(Claude Code 等)一键转换、看告警、改产物、复检的闭环 |

文档与教程在线上 Wiki: <https://ysm.cfpa.team/wiki/intro/>(界面里点"文档"直接打开)。`core/docs/` 留着同一批文档的
本地副本, 给 AI 经 MCP 的 `ysm_docs` 工具查阅。转换器开源地址: <https://github.com/lovelyXiaoQi/ysm_convert>(界面里点"开源地址")。

## 转换速度与产物体积

- **每个包一个内核进程, 并行转换**。并发数自动取逻辑核数的一半(最多 8), 再按可用内存封顶(每进程预算 1.5 GB;
  实测最大的末影龙娘峰值 1.2 GB、凋灵娘 0.7 GB)。拆进程还有一个硬理由: 同一个 Python 进程连续转多个大包时内存只涨不回,
  30 个包串行会在第 28 个时 MemoryError。
- **产物 JSON 缺省压成一行**(界面开关 / `--pretty` 关掉)。磁盘约省 70%(30 个包的资源包 258 MB → 85 MB),
  内核读写也快 2.5~3 倍; zip 打包后只小约 25%, 因为 zip 本来就把空白压掉了大半。
- 内核算法优化: molang 名字映射先一次扫描收集出现过的名字、loop 映射按目录签名缓存、比对用紧凑序列化。

| 30 个包(官方酒狐 22 + misc 4 + 坚守者 / 凋灵 / 萨赫梅特 / 末影龙娘) | 耗时 |
|---|---|
| 优化前, 串行 | 约 514 秒(单进程批量还会因内存累积失败 2 个) |
| 优化后, 缩进, 串行 | 322 秒 |
| 优化后, 压一行, 8 并发 | 约 120 秒(下限是最大的末影龙娘单包约 106 秒) |

优化前后的产物经 30 个包、520 个文件逐字节 / 逐值比对: 缩进模式逐字节一致, 压一行模式 JSON 解析后逐值一致。

**长路径**: 产物目录一律走 Windows 长路径(`\\?\` 前缀), 完整路径超过 260 字符也能写(实测 311 字符含中文目录);
早先末影龙娘的 `...\replace_entities\ender_sword.animation_controllers.json` 在稍深的输出目录下就会"找不到文件"。

## 开发者提醒怎么读

转换完成后"开发者提醒"页汇总要人工过目的项, 每项带处理建议与文档名(CLI 与 MCP 的 `attention` 同一份):

| 级别 | 含义 | 典型项 |
|---|---|---|
| error / warn | 要修 | 移植失败、Java 包声明的文件不存在、体检错误(整份动画文件会被引擎拒载) |
| notice | 要看 | molang 置零(基岩没有对应的查询/函数)、退化处理(粒子位置近似等)、未转换的脚本控制器 |
| info | 知道即可 | 中性常量(基岩没有的 Java 量按常量处理, 如 `ctrl.tac_*` 按"没拿枪"、模组联动按"没装")、动画名转小写(每包合并成一行) |

按映射表完成的等价替换不进提醒。天气、血量、露天、roaming 变量的存档与多人同步、药水/附魔探针、骨骼旋转回读(`ysm.bone_rot`)
这批 Java 专有量现在由 **YSM 主组件运行层**给真值(不再置常量), 模型要什么写在产物 ysm.json 的顶级 `java_state` 里 ——
所以产物要搭配**同期或更新的 YSM 主组件**使用, 旧主组件上这些量停在缺省值、roaming 变量不存档。完整清单见
molang 映射文档第五节(仍置常量的)与第七节(运行层提供的)。

## 分发与运行要求

发布包就是 `dist/YsmConvert/` 整个目录, 压缩后发出去、对方解压到任意路径即可(实测含中文与空格的路径正常),
约 152 MB, zip 后 64 MB。.NET 运行时随包自带, 目标机器**不需要**装 .NET。

- **Visual C++ 2008 运行库(x64)**: 内核用的 Python 2.7 依赖 `msvcr90.dll`, Windows 默认不带。
  发布包已在 `core/python/Microsoft.VC90.CRT/` 带了一份应用程序私有程序集, 正常情况下免安装;
  万一仍起不来, 装一次 [vcredist_x64.exe (VC++ 2008 SP1)](https://www.microsoft.com/download/details.aspx?id=26368) 即可。
  两个 exe 都会在内核起不来时直接给出这句提示, 不会只报一个无头错误。
- **SmartScreen**: 两个 exe 没有代码签名, 别人首次运行会看到"Windows 已保护你的电脑", 点"更多信息"再点"仍要运行"。
- `*.deps.json` 与 `*.runtimeconfig.json` 看着像临时产物, 但是 .NET 启动必需文件, 不要删。

## 架构

```
YsmConvert.exe / ysmconv.exe   C# (.NET 10, WPF)      —— 壳: 任务编排、进程管理、报告聚合、界面
        │  子进程 + JSON Lines 事件流
core/python/python.exe          便携 Python 2.7        —— 与游戏内解释器同版本
core/kernel/devtools/port_cli.py                        —— 宿主入口(任务单 → 事件流)
core/kernel/devtools/port_java_pack.py 等               —— 转换规则(YSM 仓库 devtools 的快照)
core/kernel/ysm_bp/ysmModelScripts/packLoader           —— 与游戏内解析器共用的同一份代码
core/kernel/ysm_rp/animations/java_default              —— Java 默认模型基线动画(缺键回落用)
core/docs/*.md                                          —— 移植文档(界面/MCP 可查)
```

转换规则的**唯一真源**是 YSM 网易版仓库的 `devtools/`(它直接 import 游戏运行时的 `packParser`, 产物必须与游戏解析口径一致,
所以内核跑在 Python 2.7 而不是重写成 C#)。本仓库只保存快照, 用 `build/sync-core.ps1` 更新。

## 构建

前置: .NET 10 SDK; 本机 Python 2.7(`C:\Python27`, 装了 `pypinyin`)只在生成便携运行时时需要。

```powershell
.\build\sync-core.ps1 -YsmRepo "D:\...\AddOn\ysm"   # 同步内核快照(改了 YSM 仓库的 devtools 后重跑; 按实际导入闭包拷文件, 拷完自检导入)
.\build\make-python.ps1 -Force                     # 生成 core/python(便携 2.7, 约 17 MB, 不入库)
dotnet build                                       # 开发构建(exe 在 src/*/bin/Debug, 会自动向上找到 core/)
dotnet test                                        # 单元测试
.\build\publish.ps1                                # 发布到 dist/YsmConvert(自包含, 无需装 .NET)
```

程序图标是根目录 `icon.ico`(多尺寸)。它是**构建输入, 删了就编译不过**(CS7064), 通过两个 csproj 的 `ApplicationIcon`
编进 exe 的资源; 窗口标题栏图标由 WPF 自动取主模块的, 不另外嵌资源, 所以发布目录里**不会**出现 icon.ico。
它由 1000x1000 的 PNG 经 Pillow 生成, 换图时把新图存为 icon.png 重跑下面这行(生成后 icon.png 可删, icon.ico 要留):

```powershell
py -3 -c "from PIL import Image; Image.open('icon.png').convert('RGBA').save('icon.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(24,24),(16,16)])"
```

## 命令行

```powershell
ysmconv discover "D:\java_models"                                   # 列出发现的 Java 包(单包/合集/上级目录, 深 2 层)
ysmconv convert "D:\java_models\wine_fox" --out "D:\out" --component my_models --collection wine_fox --collection-name 酒狐合集 --collection-cover "D:\art\fox.png"
ysmconv convert "D:\java_models" --out "D:\out" --component my_models --jobs 4 --pretty   # 限 4 并发、产物按缩进落盘
ysmconv convert "D:\java_models\sahmet" --out "D:\...\AddOn\ysm"    # 直接写进 YSM 主仓库(工程形态)
ysmconv validate --out "D:\out"                                     # 只体检
ysmconv fix --out "D:\out" my_models_sahmet                         # 就地修复已落盘的包
ysmconv baseline "<java-src>\assets\ysm\builtin\default" --out "D:\...\AddOn\ysm"   # 移植 java_default 基线(主工程一次)
```

退出码: 0 成功, 1 有失败或体检错误, 2 参数/环境错误。加 `--json` 得机器可读报告, `--report x.json` 另存。

## MCP 服务器

```powershell
claude mcp add ysm-convert -- "D:\桌面\ysm_convert\dist\YsmConvert\ysmconv.exe" mcp --out "D:\out"
```

工具: `ysm_info` `ysm_discover` `ysm_convert` `ysm_validate` `ysm_fix` `ysm_baseline` `ysm_explain` `ysm_docs` `ysm_last_report` `ysm_pack_files`。
典型闭环: `ysm_convert` 返回"需要开发者过目"的清单与每个包的产物路径 → AI 读产物文件按建议修改 → `ysm_validate` 复检到 0 错误 →
必要时 `ysm_fix` 补跑修复规则; 看不懂的告警用 `ysm_explain`, 规则细节用 `ysm_docs` 检索随附文档。
协议: stdio、JSON-RPC 2.0、每行一条消息(不依赖 SDK, 手写 `initialize / ping / tools/list / tools/call`)。

## 输出形态

- **独立组件**(创作者): `<根>/<组件名>_bp` + `<组件名>_rp`, 自动生成 `manifest.json`; 与 YSM 主组件一起启用即被发现
  (主包扫描所有启用行为包下的 `ysm_models/`, 资源索引扫描整个 resource_packs 目录)。
- **已有工程**(开发者): 根目录下已含行为包 + 资源包(按 manifest 模块类型 `data` / `resources` 识别, 或 `*_bp`/`*_rp` 目录名兜底)。
- **合集**: 若干包归入 `ysm_models/<合集目录>/` 并写 `ysm-pack.json`, 游戏里自动出文件夹。网易版文件夹只显示一个名字,
  不分中英文; 不填就沿用 Java 合集自带的名字。**文件夹封面**: 选一张 PNG(不超过 1MB, 最好是 Java 卡片 52x90 的比例),
  转换时拷到资源包 `textures/ui/ysm_packs/<合集>.png` 并在清单写 `folder_texture`; 不选就用 Java 合集自带的 `ysm-pack.png`,
  都没有则是默认封面。贴图改动要重启游戏才会加载。

Java 包缺的动画(拉弓/举盾/游泳…)运行时回落 `java_default` 基线; 独立组件没有基线时内核用自带快照做移植期推导,
游戏里由 YSM 主组件的资源包提供。

## 目录

```
build/        sync-core.ps1 / make-python.ps1 / publish.ps1
core/kernel   内核快照(入库)     core/docs 文档快照(入库)     core/python 便携运行时(生成, 不入库)
src/YsmConvert.Core   任务模型、内核进程、事件解析、报告聚合、告警解释、组件脚手架
src/YsmConvert.App    WPF 界面
src/YsmConvert.Cli    命令行 + MCP 服务器
tests/                xunit
```
