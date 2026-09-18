# 开发与发版

使用说明见 [README.md](README.md); 这里是从源码构建、发版与项目结构。

## 架构

```text
YsmConvert.exe / ysmconv.exe   C# (.NET 10, WPF)      —— 壳: 任务编排、进程管理、报告聚合、界面
        │  子进程 + JSON Lines 事件流
core/python/python.exe          便携 Python 2.7        —— 与游戏内解释器同版本
core/kernel/devtools/port_cli.py                        —— 宿主入口(任务单 → 事件流)
core/kernel/devtools/port_java_pack.py 等               —— 转换规则(YSM 仓库 devtools 的快照)
core/kernel/ysm_bp/ysmModelScripts/packLoader           —— 与游戏内解析器共用的同一份代码
core/kernel/ysm_rp/animations/java_default              —— Java 默认模型基线动画(缺键回落用)
core/docs/*.md                                          —— 移植文档(Wiki 同一批文档的本地副本, 供 MCP 的 ysm_docs 查阅)
```

转换规则的 **唯一真源** 是 YSM 网易版仓库的 `devtools/`(它直接 import 游戏运行时的 `packParser`, 产物必须与游戏解析口径一致,
所以内核跑在 Python 2.7 而不是重写成 C#)。本仓库只保存快照, 用 `build/sync-core.ps1` 更新。

- 每个包一个内核进程, 并行转换。并发数取逻辑核数的一半(最多 8), 再按可用内存封顶(每进程预算 1.5 GB);
  不复用进程, 因为同一个 Python 进程连续转多个大包时内存只涨不回。
- 产物路径一律走 Windows 长路径(`\\?\` 前缀), 完整路径超过 260 字符也能写。
- MCP 服务器: stdio、JSON-RPC 2.0、每行一条消息, 不依赖 SDK(手写 `initialize / ping / tools/list / tools/call`)。

## 构建

前置: .NET 10 SDK; 本机 Python 2.7(`C:\Python27`, 装了 `pypinyin`)只在生成便携运行时时需要。

```powershell
.\build\sync-core.ps1 -YsmRepo "D:\...\AddOn\ysm"   # 同步内核快照(改了 YSM 仓库的 devtools 后重跑; 按实际导入闭包拷文件, 拷完自检导入)
.\build\make-python.ps1 -Force                     # 生成 core/python(便携 2.7 + 私有 VC++ 2008 运行库, 约 18 MB, 不入库)
dotnet build                                       # 开发构建(exe 在 src/*/bin/Debug, 会自动向上找到 core/)
dotnet test                                        # 单元测试
.\build\publish.ps1                                # 发布到 dist/YsmConvert(自包含, 无需装 .NET)
```

发布包就是 `dist/YsmConvert/` 整个目录。两个 exe 共享一份 .NET 运行时, 所以不用单文件打包。

## 程序图标

程序图标是根目录 `icon.ico`(多尺寸)。它是 **构建输入, 删了就编译不过**(CS7064), 通过两个 csproj 的 `ApplicationIcon`
编进 exe 的资源; 窗口标题栏图标由 WPF 自动取主模块的, 不另外嵌资源, 所以发布目录里 **不会** 出现 icon.ico。
它由 1000x1000 的 PNG 经 Pillow 生成, 换图时把新图存为 icon.png 重跑下面这行(生成后 icon.png 可删, icon.ico 要留):

```powershell
py -3 -c "from PIL import Image; Image.open('icon.png').convert('RGBA').save('icon.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(24,24),(16,16)])"
```

## 发版(GitHub Release)

仓库页 **Actions → Release → Run workflow**, 选版本递增位(patch / minor / major)后运行。流水线
`.github/workflows/release.yml` 在 Windows runner 上从已提交的代码重新构建: 算版本号 → 装官方 Python 2.7.18 并跑
`make-python.ps1` 生成 `core/python` → `dotnet test` → `publish.ps1 -Version` → 把 `dist/YsmConvert` 打成
`YsmConvert-vX.Y.Z-win-x64.zip` → 建 tag 与 Release 并上传, 更新内容取上一个 tag 以来的提交标题。

- **版本号以 tag 为准**: 在最新的 `vX.Y.Z` 上按所选位递增, 还没有 tag 时用 `Directory.Build.props` 的 `<Version>`;
  props 的版本比递增结果高时用 props 的(想直接发 1.0.0 就把 props 改成 1.0.0)。版本号经 `-p:Version` 写进两个 exe
  与 MCP 的 serverInfo, 不回写仓库。当前提交已经发过版时流水线直接报错, 不会重复发。
- **zip 直接传**: Release 附件单个文件上限 2 GiB、总量不限。
- **发的是提交里的内核快照**: runner 上没有 YSM 仓库, 改了 devtools 要先本机跑 `sync-core.ps1` 并提交 `core/`, 再发版。
- `core/python` 与开发机同源: 官方安装包校验 SHA256, pypinyin / enum34 钉死版本(workflow 顶部 `env`);
  开发机的 pypinyin 换了版本时同步改那里。检出时关掉 autocrlf, 发布包里的文本文件与仓库逐字节一致。

## 目录

```text
build/        sync-core.ps1 / make-python.ps1 / publish.ps1
.github/      workflows/release.yml(一键发版)
core/kernel   内核快照(入库)     core/docs 文档快照(入库)     core/python 便携运行时(生成, 不入库)
src/YsmConvert.Core   任务模型、内核进程、事件解析、报告聚合、告警解释、组件脚手架
src/YsmConvert.App    WPF 界面
src/YsmConvert.Cli    命令行 + MCP 服务器
tests/                xunit
```
