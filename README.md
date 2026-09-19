# YSM Convert — Java 版 YSM 模型包 → 网易基岩版组件

把 **未加密** 的 Java 版 YSM(Yes Steve Model)模型包(含 `ysm.json` 的目录)转换成网易我的世界基岩版的 YSM 模型组件, 目前仅支持 Windows。

| 入口 | 文件 | 适合 |
| --- | --- | --- |
| 图形界面 | `YsmConvert.exe` | 拖入目录, 一键转换 |
| 命令行 | `ysmconv.exe` | 批量处理、写脚本 |
| MCP 服务器 | `ysmconv.exe mcp` | 让 Claude Code 等 AI 助手替你转换, 读日志、改模型 |

三种入口用的是同一个转换内核, 产物一样。模型移植教程、`ysm.json` 格式与 molang 替换清单见 [YSM Wiki](https://ysm.cfpa.team/wiki/intro/)。

## 下载与运行

1. 到 [Releases](https://github.com/lovelyXiaoQi/ysm_convert/releases) 下载最新的 `YsmConvert-vX.Y.Z-win-x64.zip`。
2. 解压到任意目录(路径可以含中文和空格), 双击 `YsmConvert.exe`。

.NET 运行时和内核用的 Python 都在包里, 不用另装。

- exe 没有代码签名, 首次运行弹出"Windows 已保护你的电脑"时, 点"更多信息"再点"仍要运行"。
- 提示转换内核无法启动时, 装一次 [VC++ 2008 SP1 运行库(x64)](https://www.microsoft.com/download/details.aspx?id=26368)。
- 解压出来的目录要保持完整: `core/` 是转换内核, `*.deps.json`、`*.runtimeconfig.json` 是程序启动必需的文件, 都不要删。

## 图形界面

窗口分三块: 左上 **① Java 模型包**, 右上 **② 输出与选项**, 下方是结果区, 操作按钮在结果区右上角。设置会自动记住。

### 1. 添加模型包

点 **添加目录…**(可多选), 或者直接把文件夹拖进窗口、拖到 `YsmConvert.exe` 图标上。单个模型包目录、带 `ysm-pack.json` 的合集目录、装着若干包的上级目录都可以, 程序会自动找出里面的包。

列表里一个包一行:

- **启用**: 取消勾选的包不参与转换。
- **包名(可改)**: 模型 ID 和全部资源 ID 的词根, 只能用小写英文、数字、下划线, 以字母或数字开头, 最长 64 个字符。默认是自动生成的建议名(中文转拼音); 建议加上作者前缀, 免得和别人的模型重名。
- **合集(可改)**: 合集就是游戏里模型选择界面的文件夹。合集名相同的包放进同一个文件夹, 留空则不进文件夹。
- **状态 / 待过目 / 概况**: 转换进度、需要人工过目的条数、包内容统计。

### 2. 选择输出位置

二选一:

- **新建 / 复用独立模型组件**(推荐): 在"目标根目录"下生成 `<组件名>_bp`(行为包)和 `<组件名>_rp`(资源包), 自带 `manifest.json`; 同名组件已存在时沿用它。行为包里的 `entities/` 文件夹(内放 `.gitkeep`)不能删: 网易按它识别行为包, 没有它 MC Studio 测试和正式游戏都只挂资源包, 模型不会出现在选择界面。组件名用英文、数字、下划线或连字符。
- **写入已有的资源包 / 行为包**: "目标根目录"选一个已经装着行为包和资源包的目录(比如你自己的组件工程), 产物写进这两个包。

### 3. 合集文件夹(可选)

勾选 **把列表里的包归入同一个合集文件夹** 后填写:

- **合集目录名**: 英文、数字、下划线或连字符。填好后点 **应用到列表里全部包的"合集"列**; 勾选之后再添加的包会自动填上。
- **文件夹名**: 游戏里文件夹卡片上显示的名字。不填则沿用 Java 合集自带的名字, 都没有就显示目录名。
- **文件夹封面**: 不超过 1MB 的 PNG, 最好是 Java 文件夹卡片 52x90 的比例。不选则用 Java 合集自带的 `ysm-pack.png`, 都没有就是默认封面。

### 4. 转换选项

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| 产物 JSON 压缩为一行 | 开 | 体积小、转换快; 要手工查看或修改产物、用 git 对比时关掉 |
| 转换后运行资源包红线体检 | 开 | 推荐保持开启 |
| 携带第三方模组联动动画 | 关 | tacz、slashblade 等模组的动画, 基岩版没有对应物品, 一般不勾 |

### 5. 开始转换, 看结果

点 **开始转换**。多个包会并行转换, 底部状态栏显示进度和耗时, 中途可以点 **取消**。结果在下方三个页签里:

- **日志**: 每个包的转换过程。红 = 错误, 橙 = 警告, 蓝 = 提醒(做了降级或近似处理, 建议进游戏核对), 灰 = 明细(勾选"显示全部内核明细行"才显示)。
- **开发者提醒**: 汇总需要人工过目的项, 每项带处理建议; 点"文档"列的按钮打开 Wiki 查对应文档, **复制全部** 可以整份复制出来。有提醒时, 转换完会自动切到这一页。
- **体检**: 资源包红线体检的结果。错误 = 整份动画 / 控制器文件会被引擎拒载(游戏里模型停在绑定姿态), 必须修到 0; 警告 = 某些功能可能缺失(如音效未登记)。

开发者提醒按级别区分:

| 级别 | 含义 | 典型项 |
| --- | --- | --- |
| error / warn | 要修 | 移植失败、Java 包声明的文件不存在、体检错误 |
| notice | 要看 | molang 置零(基岩版没有对应的查询或函数)、退化处理(如粒子位置近似)、未转换的脚本控制器 |
| info | 知道即可 | 基岩版没有的 Java 量按常量处理(如 `ctrl.tac_*` 按"没拿枪"、模组联动按"没装")、动画名转小写 |

按映射表做的等价替换不会进提醒, 完整清单见 Wiki 里的 molang 映射文档。

结果区右上角的其他按钮:

- **只体检**: 对已经生成的产物重新体检, 不重新转换。
- **修复产物**: 对已经生成的产物就地补跑修复规则, 重复执行也没关系。手上还有 Java 源包的话, 重新转换效果更完整。旧版转换器转出来的组件行为包缺 `entities/` 文件夹(体检会报"行为包不会被挂载")时也一并补上。
- **打开输出目录**、**导出报告…**(存成 txt 或 json)。

"只体检"和"修复产物"只处理列表里勾选的包(按包名找产物), 列表为空时处理输出位置里的全部包。**文档 ↗**、**开源地址 ↗** 两个页签点一下就在浏览器打开 Wiki 和本仓库。

### 6. 进游戏

- 独立组件要和 **YSM 主组件** 一起启用, 模型会被自动发现; 归入合集的包在模型选择界面里显示成文件夹。
- 资源包(包括文件夹封面)有改动时, 要重启游戏才会重新加载。
- 产物要搭配 **同期或更新版本的 YSM 主组件** 使用: 天气、血量、roaming 变量存档等 Java 专有量由主组件在运行时提供, 旧版主组件上这些量会停在默认值。
- Java 包里没有的动画(拉弓、举盾、游泳等)在游戏里会用 YSM 主组件自带的默认动画。

## 命令行(ysmconv)

`ysmconv.exe` 和图形界面在同一目录, 功能一样, 但不开窗口, 适合批量处理和写脚本。在解压目录里打开终端运行:

| 命令 | 作用 |
| --- | --- |
| `ysmconv info` | 查看内核、Python 与文档的位置; 内核起不来时用它排查 |
| `ysmconv discover <目录>...` | 列出目录里找到的 Java 包和建议包名 |
| `ysmconv convert <目录>... --out <根目录> [选项]` | 转换 |
| `ysmconv validate --out <根目录> [包名...]` | 只体检已有产物 |
| `ysmconv fix --out <根目录> [包名...]` | 就地修复已有产物 |
| `ysmconv baseline <Java default 模型目录> --out <主组件工程>` | 移植 Java 默认模型的基线动画, 只有 YSM 主组件工程需要跑一次 |
| `ysmconv mcp [--out <根目录>]` | 以 MCP 服务器方式运行, 见下一节 |

`--out` 是输出根目录: 加 `--component <组件名>` 时在它下面新建 / 复用独立组件, 不加时它必须已经装着行为包和资源包。`convert` 的常用选项:

| 选项 | 作用 |
| --- | --- |
| `--component <组件名>` | 新建 / 复用独立组件 `<组件名>_bp` 与 `<组件名>_rp` |
| `--collection <目录名>` | 全部包归入这个合集文件夹 |
| `--collection-name <名字>`、`--collection-cover <PNG>` | 文件夹显示名、封面, 要和 `--collection` 一起用 |
| `--prefix <前缀>` | 包名统一加前缀 |
| `--rename <文件夹>=<包名>` | 逐包指定包名, 可以写多次 |
| `--pretty` | 产物 JSON 按缩进输出(默认压成一行) |
| `--no-validate` | 转换后不体检 |
| `--with-mods` | 携带第三方模组联动动画 |
| `--jobs <N>` | 同时转换的包数(默认自动) |
| `--json`、`--report <文件>` | 输出 / 另存机器可读的 JSON 报告 |

全部选项见 `ysmconv --help`。示例:

```powershell
# 看看目录里有哪些包
ysmconv discover "D:\java_models"

# 全部转换成独立组件 D:\out\my_models_bp + D:\out\my_models_rp
ysmconv convert "D:\java_models" --out "D:\out" --component my_models

# 归入合集文件夹, 自定义显示名和封面
ysmconv convert "D:\java_models\wine_fox" --out "D:\out" --component my_models --collection wine_fox --collection-name 酒狐合集 --collection-cover "D:\art\fox.png"

# 写进已有工程(D:\MyAddon 下已有行为包和资源包)
ysmconv convert "D:\java_models\sahmet" --out "D:\MyAddon"

# 对已有产物只体检 / 修复其中一个包
ysmconv validate --out "D:\out" --component my_models
ysmconv fix --out "D:\out" --component my_models sahmet
```

退出码: 0 成功, 1 有转换失败或体检错误, 2 参数或环境错误。

## MCP 服务器

MCP(Model Context Protocol)是 AI 助手调用外部工具的标准协议。把 `ysmconv` 注册成 MCP 服务器后, 在 Claude Code 等 AI 客户端里直接说"把 D:\java_models 里的包转换到 D:\out", AI 会自己完成转换、读提醒、改产物、复检的整个流程。

在 Claude Code 里注册(路径换成你的解压目录; `--out` 是默认输出根目录, 可以不写):

```powershell
claude mcp add ysm-convert -- "D:\Tools\YsmConvert\ysmconv.exe" mcp --out "D:\out"
```

其他支持 MCP 的客户端, 在配置文件里加:

```json
{
  "mcpServers": {
    "ysm-convert": {
      "command": "D:\\Tools\\YsmConvert\\ysmconv.exe",
      "args": ["mcp", "--out", "D:\\out"]
    }
  }
}
```

提供的工具:

| 工具 | 作用 |
| --- | --- |
| `ysm_discover` | 在目录里找 Java 模型包 |
| `ysm_convert` | 转换并体检, 返回待过目清单和每个包的产物路径 |
| `ysm_validate`、`ysm_fix` | 对已有产物体检、就地修复 |
| `ysm_pack_files` | 列出某个包的全部产物文件, 供 AI 逐个打开修改 |
| `ysm_explain` | 解释一条提醒或体检结果: 什么意思、要不要处理、查哪份文档 |
| `ysm_docs` | 查阅随附的移植文档(格式速查、移植教程、molang 映射、动画机制) |
| `ysm_last_report` | 取上一次转换 / 体检 / 修复的完整报告 |
| `ysm_baseline` | 移植 Java 默认模型的基线动画(只有 YSM 主组件工程需要) |
| `ysm_info` | 查看转换器与内核信息 |

典型流程: `ysm_convert` 转换 → AI 按待过目清单修改产物 → `ysm_validate` 复检到 0 错误。

## 从源码构建

构建、发版与项目结构见 [DEVELOPMENT.md](DEVELOPMENT.md)。
