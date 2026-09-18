<#
.SYNOPSIS
  从 YSM 网易版仓库同步转换内核(Python 源 + java_default 基线 + 文档)到本仓库 core/kernel、core/docs。

.DESCRIPTION
  转换规则的唯一真源是 YSM 仓库的 devtools/ 与 ysm_bp/ysmModelScripts/packLoader/(游戏内解析器共用同一份代码)。
  本脚本只做"快照拷贝", 不改任何内容; 同步后 core/kernel/KERNEL_SOURCE.json 记录来源提交。
  内核目录布局必须保持 <kernel>/devtools、<kernel>/ysm_bp/ysmModelScripts、<kernel>/ysm_rp/animations/java_default:
  port_java_pack.py 按自身位置推导 ROOT 并把 ROOT/ysm_bp 加进 sys.path, ROOT/ysm_rp 是缺省资源包(装着基线)。

.PARAMETER YsmRepo
  YSM 仓库根目录(含 devtools/、ysm_bp/、ysm_rp/、docs/)。
#>
param(
    [Parameter(Mandatory = $true)][string]$YsmRepo
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$kernel = Join-Path $repoRoot "core\kernel"
$docs = Join-Path $repoRoot "core\docs"

if (-not (Test-Path (Join-Path $YsmRepo "devtools\port_java_pack.py"))) {
    throw "不是 YSM 仓库(缺 devtools/port_java_pack.py): $YsmRepo"
}

$devtoolsFiles = @(
    "port_cli.py", "port_java_pack.py", "molang_syntax.py", "script_controller.py",
    "fix_ported_controllers.py", "validate_rp_animations.py",
    "data_bedrock_queries.json", "data_engine_binary_tokens.json", "data_netease_vanilla_tokens.json"
)
# packParser 的 import 闭包(py -2.7 下 sys.modules 实测): 只需这 7 个文件
$modFiles = @(
    "ysmModelScripts\__init__.py",
    "ysmModelScripts\data\__init__.py",
    "ysmModelScripts\data\baseAnimations.py",
    "ysmModelScripts\data\molangBase.py",
    "ysmModelScripts\data\skinBuilder.py",
    "ysmModelScripts\packLoader\__init__.py",
    "ysmModelScripts\packLoader\packParser.py"
)

function Copy-Into([string]$src, [string]$dst) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst -Force
}

# 先清空再拷, 保证删掉的文件不会残留
if (Test-Path $kernel) { Remove-Item -Recurse -Force $kernel }
if (Test-Path $docs) { Remove-Item -Recurse -Force $docs }
New-Item -ItemType Directory -Force $kernel, $docs | Out-Null

foreach ($f in $devtoolsFiles) { Copy-Into (Join-Path $YsmRepo "devtools\$f") (Join-Path $kernel "devtools\$f") }
foreach ($f in $modFiles) { Copy-Into (Join-Path $YsmRepo "ysm_bp\$f") (Join-Path $kernel "ysm_bp\$f") }

$baselineSrc = Join-Path $YsmRepo "ysm_rp\animations\java_default"
if (-not (Test-Path $baselineSrc)) {
    throw "YSM 仓库缺 java_default 基线(ysm_rp/animations/java_default): 先在 YSM 仓库跑 port_java_pack.py <builtin/default> --baseline"
}
Get-ChildItem $baselineSrc -Filter *.json | ForEach-Object {
    Copy-Into $_.FullName (Join-Path $kernel "ysm_rp\animations\java_default\$($_.Name)")
}
Get-ChildItem (Join-Path $YsmRepo "docs") -Filter *.md | ForEach-Object {
    Copy-Into $_.FullName (Join-Path $docs $_.Name)
}

$commit = ""
try { $commit = (git -C $YsmRepo rev-parse HEAD 2>$null) } catch {}
$dirty = ""
try { $dirty = (git -C $YsmRepo status --porcelain -- devtools ysm_bp/ysmModelScripts/packLoader 2>$null) } catch {}
$meta = [ordered]@{
    source    = (Resolve-Path $YsmRepo).Path
    commit    = "$commit"
    dirty     = [bool]$dirty
    syncedAt  = (Get-Date).ToString("s")
    devtools  = $devtoolsFiles
    modules   = $modFiles
}
$meta | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 (Join-Path $kernel "KERNEL_SOURCE.json")

$count = (Get-ChildItem -Recurse -File $kernel).Count
Write-Host "内核已同步: $count 个文件 -> $kernel (来源提交 $commit$(if ($dirty) { ', 工作区有未提交改动' }))"
