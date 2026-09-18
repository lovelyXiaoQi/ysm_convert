<#
.SYNOPSIS
  从 YSM 网易版仓库同步转换内核(Python 源 + java_default 基线 + 文档)到本仓库 core/kernel、core/docs。

.DESCRIPTION
  转换规则的唯一真源是 YSM 仓库的 devtools/ 与 ysm_bp/ysmModelScripts/packLoader/(游戏内解析器共用同一份代码)。
  本脚本只做"快照拷贝", 不改任何内容; 同步后 core/kernel/KERNEL_SOURCE.json 记录来源提交与文件清单。
  内核目录布局必须保持 <kernel>/devtools、<kernel>/ysm_bp/ysmModelScripts、<kernel>/ysm_rp/animations/java_default:
  port_java_pack.py 按自身位置推导 ROOT 并把 ROOT/ysm_bp 加进 sys.path, ROOT/ysm_rp 是缺省资源包(装着基线)。

  要拷哪些 .py 不再写死: 用本机 Python 2.7 实际导入一遍内核入口(port_cli 及其带进来的全部模块), 收集从 YSM 仓库里
  加载的模块文件 —— 2026-09-18 内核新增 devtools/java_runtime_bindings.py, 旧的固定清单漏了它, 转换器一启动就
  ImportError。拷完再用同步出来的副本真导入一次做自检, 漏文件当场报错。

.PARAMETER YsmRepo
  YSM 仓库根目录(含 devtools/、ysm_bp/、ysm_rp/、docs/)。
.PARAMETER PythonHome
  本机 Python 2.7(算依赖闭包用), 缺省 C:\Python27; 找不到时退到 py -2.7。
#>
param(
    [Parameter(Mandatory = $true)][string]$YsmRepo,
    [string]$PythonHome = "C:\Python27"
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$kernel = Join-Path $repoRoot "core\kernel"
$docs = Join-Path $repoRoot "core\docs"
$YsmRepo = (Resolve-Path $YsmRepo).Path

if (-not (Test-Path (Join-Path $YsmRepo "devtools\port_cli.py"))) {
    throw "不是 YSM 仓库(缺 devtools/port_cli.py): $YsmRepo"
}

$pyExe = Join-Path $PythonHome "python.exe"
$pyArgs = @()
if (-not (Test-Path $pyExe)) {
    $pyExe = Join-Path $env:SystemRoot "py.exe"
    $pyArgs = @("-2.7")
    if (-not (Test-Path $pyExe)) { throw "找不到 Python 2.7(算内核依赖闭包要用): 装在 $PythonHome 或让 py 启动器带 2.7" }
}

# ---- 1. 依赖闭包: 导入内核入口, 收集从 YSM 仓库里加载的模块文件 ----
$closureScript = @'
import os
import sys
repo = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(repo, "devtools"))
import port_cli  # noqa: F401  带进 port_java_pack / fix_ported_controllers / validate_rp_animations 及其依赖
import fix_ported_controllers  # noqa: F401
import validate_rp_animations  # noqa: F401
prefix = repo.lower() + os.sep
found = set()
for name, module in list(sys.modules.items()):
    path = getattr(module, "__file__", None) if module is not None else None
    if not path:
        continue
    path = os.path.abspath(path)
    if path.endswith((".pyc", ".pyo")):
        path = path[:-1]
    if path.lower().startswith(prefix) and os.path.isfile(path):
        found.add(os.path.relpath(path, repo))
sys.stdout.write("\n".join(sorted(found)) + "\n")
'@
$tmp = Join-Path $env:TEMP "ysmconv_closure.py"
Set-Content -Path $tmp -Value $closureScript -Encoding ascii
$closure = & $pyExe @pyArgs -B $tmp $YsmRepo
$closureExit = $LASTEXITCODE
Remove-Item $tmp -Force
if ($closureExit -ne 0) { throw "算内核依赖闭包失败(在 YSM 仓库里导入 devtools/port_cli.py 出错, 先在那边修好)" }
$pyFiles = @($closure | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() })

function Copy-Into([string]$src, [string]$dst) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst -Force
}

# ---- 2. 清空后拷贝(删掉的文件不残留) ----
if (Test-Path $kernel) { Remove-Item -Recurse -Force $kernel }
if (Test-Path $docs) { Remove-Item -Recurse -Force $docs }
New-Item -ItemType Directory -Force $kernel, $docs | Out-Null

foreach ($rel in $pyFiles) { Copy-Into (Join-Path $YsmRepo $rel) (Join-Path $kernel $rel) }
# 内核运行期按文件名读的数据表(不是模块, 闭包看不到)
$dataFiles = @(Get-ChildItem (Join-Path $YsmRepo "devtools") -Filter "data_*.json" | ForEach-Object { "devtools\$($_.Name)" })
foreach ($rel in $dataFiles) { Copy-Into (Join-Path $YsmRepo $rel) (Join-Path $kernel $rel) }

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

# ---- 3. 自检: 用同步出来的副本导入内核入口(优先用打包的便携解释器) ----
$checkPy = Join-Path $repoRoot "core\python\python.exe"
$checkArgs = @()
if (-not (Test-Path $checkPy)) { $checkPy = $pyExe; $checkArgs = $pyArgs }
$checkScript = @'
import os
import sys
kernel = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(kernel, "devtools"))
import port_cli  # noqa: F401
import fix_ported_controllers  # noqa: F401
import validate_rp_animations  # noqa: F401
sys.stdout.write("ok")
'@
$tmp = Join-Path $env:TEMP "ysmconv_kernelcheck.py"
Set-Content -Path $tmp -Value $checkScript -Encoding ascii
$check = & $checkPy @checkArgs -E -s -B $tmp $kernel 2>&1
$checkExit = $LASTEXITCODE
Remove-Item $tmp -Force
if ($checkExit -ne 0 -or "$check" -notmatch "ok") {
    throw "同步出来的内核导入失败(依赖闭包漏了文件?):`n$check"
}

$commit = ""
try { $commit = (git -C $YsmRepo rev-parse HEAD 2>$null) } catch {}
$dirty = ""
try { $dirty = (git -C $YsmRepo status --porcelain -- devtools ysm_bp/ysmModelScripts 2>$null) } catch {}
$meta = [ordered]@{
    source   = $YsmRepo
    commit   = "$commit"
    dirty    = [bool]$dirty
    syncedAt = (Get-Date).ToString("s")
    modules  = $pyFiles
    data     = $dataFiles
}
$meta | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 (Join-Path $kernel "KERNEL_SOURCE.json")

$count = (Get-ChildItem -Recurse -File $kernel).Count
Write-Host "内核已同步: $count 个文件(模块 $($pyFiles.Count) 个) -> $kernel (来源提交 $commit$(if ($dirty) { ', 工作区有未提交改动' })); 自检导入通过"
