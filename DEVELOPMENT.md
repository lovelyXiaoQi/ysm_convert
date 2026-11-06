# 开发与发版

使用说明见 [README.md](README.md); 这里是从源码构建、发版与项目结构。

## 架构

```text
YsmConvert.exe / ysmconv.exe   Rust(egui 图形界面 / 命令行 + MCP) —— 壳: 任务编排、进程管理、报告聚合、界面
        │  子进程 + JSON Lines 事件流
core/python/python.exe          便携 Python 2.7                     —— 与游戏内解释器同版本
<内核根>/devtools/port_cli.py                                       —— 宿主入口(任务单 → 事件流)
<内核根>/devtools/port_java_pack.py 等                              —— 转换规则
<内核根>/ysm_bp/ysmModelScripts/packLoader                          —— 与游戏内解析器共用的同一份代码
<内核根>/ysm_rp/animations/java_default                             —— Java 默认模型基线动画(缺键回落用)
<文档目录>/*.md                                                     —— 移植文档(供 MCP 的 ysm_docs 查阅)
```

转换规则的 **唯一真源** 是 YSM 工程的 `devtools/`(它直接 import 游戏运行时的 `packParser`, 产物必须与游戏解析口径一致,
所以内核跑在 Python 2.7 而不是重写成 Rust)。壳只做编排, 不含任何转换规则。**本仓库住在工程里**(`<工程>/ysm_convert`),
内核根按运行形态定(`crates/ysmconv-core/src/locator.rs`):

| 运行形态 | 内核根 / 文档 | 说明 |
| --- | --- | --- |
| 开发构建(`target/debug`、`target/release`) | 工程的 `devtools/`、`ysm_rp/`、`docs/` | **改了规则立刻生效, 不用同步快照** |
| 发布包(exe 旁边就有 `core/`) | 自带的 `core/kernel`、`core/docs` | 用户机上没有工程; 解压到工程里也按这条算, 免得把发布包"测成"实时内核 |
| 环境变量 `YSMCONV_CORE` | 指定的 core 目录 | 手工指定快照位置时用 |

`ysmconv info` 的"内核来源"一行会写明当前是哪种。快照只为发布包存在, 由 `build/sync-core.ps1` 生成
(`build/publish.ps1` 会自动先跑一遍), 入库是为了让 Actions 在没有工程的 runner 上也能发版。

- 每个包一个内核进程, 并行转换。并发数取逻辑核数的一半(最多 8), 再按可用内存封顶(每进程预算 1.5 GB);
  不复用进程, 因为同一个 Python 进程连续转多个大包时内存只涨不回。
- 内核进程放进 Windows 作业对象(`KILL_ON_JOB_CLOSE`): 取消时整树终止, 壳退出(含崩溃、关窗口)时系统同样整树终止, 不留孤儿进程。
- 产物路径一律走 Windows 长路径(`\\?\` 前缀, 内核 `port_cli._ApplyLayout`), 完整路径超过 260 字符也能写。
- MCP 服务器: stdio、JSON-RPC 2.0、每行一条消息, 不依赖 SDK(手写 `initialize / ping / tools/list / tools/call`)。

## 发布包里有什么

| 部分 | 大小 | 说明 |
| --- | --- | --- |
| `YsmConvert.exe` | 约 6 MB | 图形界面, 静态链接, 不依赖任何运行时; 用 OpenGL 2.0 画界面, 字体读系统的微软雅黑 |
| `ysmconv.exe` | 约 1 MB | 命令行 + MCP 服务器 |
| `core/python` | 约 7.6 MB | 便携 Python 2.7: 解释器 + 预编译标准库 `python27.zip` + 少量 `.pyd` + 私有 VC++ 2008 运行库 |
| `core/kernel` | 约 3.3 MB | 内核快照(`.py` 原样, 外加 java_default 基线动画) |
| `core/docs` | 约 0.4 MB | 移植文档 |

整包 zip 约 8.7 MB(解压后 18 MB、58 个文件)。旧版(C# WPF 自包含发布)zip 64 MB、解压后 156 MB / 730 个文件, 其中 .NET 运行时就占了 130 多 MB。

便携 Python 的标准库与 pypinyin 全部预编译成 `.pyc` 打进 `python27.zip`(`build/pyzip.py`): 内核进程启动从约 1 秒降到约 0.3 秒,
每个包一个进程, 包多时省得明显。`Lib\os.py` 只是地标(解释器按它把自身目录当 prefix, 不去读注册表里别的 Python 安装)。

## 构建

前置: Rust stable(`rustup`, MSVC 工具链; 嵌 exe 图标要 Windows SDK 的 `rc.exe`, 装了 VS Build Tools 就有);
本机 Python 2.7(`C:\Python27`, 装了 `pypinyin` 与 `enum34`)只在生成便携运行时时需要。

```powershell
.\build\make-python.ps1 -Force   # 生成 core/python(便携 2.7, 约 7.6 MB, 不入库)
cargo build                      # 开发构建(exe 在 target/debug, 跑的是工程的 devtools)
cargo test --workspace           # 单元测试
cargo fmt --all                  # 格式化(rustfmt.toml: 行宽 120)
cargo clippy --workspace --all-targets -- -D warnings
.\build\publish.ps1              # 发布到 dist/YsmConvert(会先跑 sync-core 把当前 devtools 快照进 core/)
.\build\sync-core.ps1            # 只同步快照(缺省取上一级工程; 发版前要把 core/ 的改动提交上去)
```

发布包就是 `dist/YsmConvert/` 整个目录。`publish.ps1 -KernelOnly` 只换 `core/`(GUI 开着、只改了内核时用)。

GUI 用 OpenGL 2.0 画界面; 显卡驱动太旧或在不带 GPU 的远程桌面 / 虚拟机里起不来时会弹框说明, 同目录的 `ysmconv.exe` 不受影响。

## 程序图标

程序图标是根目录 `icon.ico`(多尺寸, 全是 PNG 条目)。它是 **构建输入**: 两个 crate 的 `build.rs` 经 `winresource` 编进 exe 资源;
GUI 的窗口 / 任务栏图标在运行时从同一份 ico(`include_bytes!`)里解出最大的 PNG 条目。它由 1000x1000 的 PNG 经 Pillow 生成,
换图时把新图存为 icon.png 重跑下面这行(生成后 icon.png 可删, icon.ico 要留):

```powershell
py -3 -c "from PIL import Image; Image.open('icon.png').convert('RGBA').save('icon.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(24,24),(16,16)])"
```

## 发版(GitHub Release)

仓库页 **Actions → Release → Run workflow**, 选版本递增位(patch / minor / major)后运行。流水线
`.github/workflows/release.yml` 在 Windows runner 上从已提交的代码重新构建: 算版本号 → 装官方 Python 2.7.18 并跑
`make-python.ps1` 生成 `core/python` → `cargo test` → `publish.ps1 -Version` → 把 `dist/YsmConvert` 打成
`YsmConvert-vX.Y.Z-win-x64.zip` → 建 tag 与 Release 并上传, 更新内容取上一个 tag 以来的提交标题。

- **版本号以 tag 为准**: 在最新的 `vX.Y.Z` 上按所选位递增, 还没有 tag 时用 `Cargo.toml` `[workspace.package]` 的 `version`;
  那里的版本比递增结果高时用那里的(想直接发 1.0.0 就把它改成 1.0.0)。版本号经环境变量 `YSMCONV_VERSION` 写进两个 exe
  (文件属性、`ysmconv --version`、MCP 的 serverInfo), 不回写仓库。当前提交已经发过版时流水线直接报错, 不会重复发。
- **发的是提交里的内核快照**: runner 上没有 YSM 工程(`publish.ps1` 在那里自动跳过同步), 所以改了 devtools 之后要在本机
  跑一次 `publish.ps1`(或 `sync-core.ps1`)把 `core/` 刷新并**提交**, 再发版 —— 否则发出去的还是上一次的规则。
- `core/python` 与开发机同源: 官方安装包校验 SHA256, pypinyin / enum34 钉死版本(workflow 顶部 `env`);
  开发机的 pypinyin 换了版本时同步改那里。检出时关掉 autocrlf, 发布包里的文本文件与仓库逐字节一致。
- `Cargo.lock` 入库, 构建用 `--locked`, 依赖版本与开发机一致。

## 目录

```text
build/                sync-core.ps1 / make-python.ps1 + pyzip.py / publish.ps1
.github/              workflows/release.yml(一键发版)
core/kernel           内核快照(入库, 只为发布包)  core/docs 文档快照(入库)  core/python 便携运行时(生成, 不入库)
crates/ysmconv-core   任务模型、内核定位与进程管理、事件解析、报告聚合、告警解释、组件脚手架(单元测试随模块)
crates/ysmconv-cli    ysmconv.exe: 命令行 + MCP 服务器
crates/ysmconv-gui    YsmConvert.exe: egui 图形界面
```

告警说明表 `crates/ysmconv-core/src/catalog.rs` 按内核的固定措辞匹配; 内核新增或改了告警措辞时同步这里(YSM 工程的
`devtools/test_port_cli.py` 守护内核侧标签格式)。材质预设表 `crates/ysmconv-core/src/material.rs` 的 `PRESET_NAMES`
与主包 `materialResolver.MATERIAL_PRESETS` 一致, 由 `test_port_cli.py` 的 `TestConverterMaterialPresets` 核对。
