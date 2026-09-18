# YSM Convert — Java 版 YSM 模型包 → 网易基岩版组件

把 **未加密** 的 Java 版 YSM(Yes Steve Model)模型包目录(含 `ysm.json`)一键转换成网易基岩版 YSM 组件。
Windows 专用。三种用法共用一个转换内核:

| 入口 | 文件 | 用途 |
|---|---|---|
| 图形界面 | `YsmConvert.exe` | 拖入目录、批量转换、合集归组、日志与"开发者提醒"面板、体检、修复 |
| 命令行 | `ysmconv.exe` | 脚本化 / CI: `discover` `convert` `validate` `fix` `baseline` |
| MCP 服务器 | `ysmconv.exe mcp` | 让 AI(Claude Code 等)一键转换、看告警、改产物、复检的闭环 |

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
.\build\sync-core.ps1 -YsmRepo "D:\...\AddOn\ysm"   # 同步内核快照(改了 YSM 仓库的 devtools 后重跑)
.\build\make-python.ps1 -Force                     # 生成 core/python(便携 2.7, 约 17 MB, 不入库)
dotnet build                                       # 开发构建(exe 在 src/*/bin/Debug, 会自动向上找到 core/)
dotnet test                                        # 单元测试
.\build\publish.ps1                                # 发布到 dist/YsmConvert(自包含, 无需装 .NET)
```

程序图标是根目录 `icon.ico`(多尺寸, 两个 exe 与窗口标题栏共用), **构建必需, 别删**; 它由 1000x1000 的 PNG 经 Pillow 生成,
换图时把新图存为 icon.png 重跑下面这行(生成后 icon.png 可删, icon.ico 要留):

```powershell
py -3 -c "from PIL import Image; Image.open('icon.png').convert('RGBA').save('icon.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(24,24),(16,16)])"
```

## 命令行

```powershell
ysmconv discover "D:\java_models"                                   # 列出发现的 Java 包(单包/合集/上级目录, 深 2 层)
ysmconv convert "D:\java_models\wine_fox" --out "D:\out" --component my_models --collection wine_fox --collection-name-zh 酒狐合集
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
- **合集**: 若干包归入 `ysm_models/<合集目录>/` 并写 `ysm-pack.json`(显示名), 游戏里自动出文件夹。

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
