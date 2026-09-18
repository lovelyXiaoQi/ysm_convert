<#
.SYNOPSIS
  发布可分发目录 dist/YsmConvert/: GUI(YsmConvert.exe) + CLI/MCP(ysmconv.exe) + core/(内核快照 + 便携 Python + 文档)。

.DESCRIPTION
  两个 exe 以自包含方式发布到同一目录, 共享一份 .NET 运行时(不装 .NET 也能跑); 不用单文件打包, 否则两个 exe 各带一份运行时。
  core/python 必须先由 build/make-python.ps1 生成, core/kernel 与 core/docs 由 build/sync-core.ps1 同步。
  旧 dist 先整目录改名再删除: 目录里有文件被占用(GUI / CLI 正在运行)时改名会失败, 于是在删掉任何东西之前就中止 ——
  2026-09-18 曾在 GUI 运行中直接 Remove-Item, 删到被锁的 DLL 才报错, dist 只剩半截。

.PARAMETER SelfContained
  缺省 $true。设为 $false 则发布框架依赖版(体积小, 需要目标机装 .NET 10 桌面运行时)。
.PARAMETER KernelOnly
  只把 core/(kernel / docs / python)重新拷进已有的 dist, 不重新发布 exe —— 改了内核、GUI 又开着的时候用。
#>
param(
    [bool]$SelfContained = $true,
    [string]$Runtime = "win-x64",
    [switch]$KernelOnly
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $repoRoot "dist\YsmConvert"

foreach ($required in @("core\kernel\devtools\port_cli.py", "core\python\python.exe")) {
    if (-not (Test-Path (Join-Path $repoRoot $required))) {
        throw "缺 $required —— 先跑 build\sync-core.ps1 与 build\make-python.ps1"
    }
}

function Copy-Core([string]$target) {
    $coreDst = Join-Path $target "core"
    foreach ($sub in @("kernel", "python", "docs")) {
        $dst = Join-Path $coreDst $sub
        if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
        Copy-Item -Recurse (Join-Path $repoRoot "core\$sub") $dst
    }
    Get-ChildItem -Recurse $coreDst -Include *.pyc, __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    # 内核来源记录里带着开发机的绝对路径, 发布包里只留仓库目录名与提交号
    $srcInfo = Join-Path $coreDst "kernel\KERNEL_SOURCE.json"
    if (Test-Path $srcInfo) {
        $meta = Get-Content $srcInfo -Raw | ConvertFrom-Json
        $meta.source = Split-Path -Leaf $meta.source
        $meta | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 $srcInfo
    }
    if (-not (Test-Path (Join-Path $coreDst "python\Microsoft.VC90.CRT\msvcr90.dll"))) {
        Write-Warning "发布包里没有私有 VC++ 2008 运行库, 未装 vcredist 2008 x64 的机器上内核起不来; 重跑 build\make-python.ps1 -Force"
    }
}

if ($KernelOnly) {
    if (-not (Test-Path (Join-Path $dist "ysmconv.exe"))) { throw "dist 里还没有发布产物, 去掉 -KernelOnly 完整发布" }
    Copy-Core $dist
    Write-Host "已更新 $dist\core (exe 未动)"
    exit 0
}

if (Test-Path $dist) {
    $old = "$dist.old"
    if (Test-Path $old) { Remove-Item -Recurse -Force $old }
    try {
        Rename-Item -LiteralPath $dist -NewName (Split-Path -Leaf $old) -ErrorAction Stop
    } catch {
        throw "dist 正被占用(YsmConvert.exe / ysmconv.exe 还开着?), 关掉后重试; 只换内核可用 -KernelOnly"
    }
    Remove-Item -Recurse -Force $old
}
New-Item -ItemType Directory -Force $dist | Out-Null

$sc = if ($SelfContained) { "true" } else { "false" }
$common = @("-c", "Release", "-r", $Runtime, "--self-contained", $sc, "-o", $dist, "-p:PublishSingleFile=false", "-p:DebugType=none")
dotnet publish (Join-Path $repoRoot "src\YsmConvert.App") @common
if ($LASTEXITCODE -ne 0) { throw "发布 GUI 失败" }
dotnet publish (Join-Path $repoRoot "src\YsmConvert.Cli") @common
if ($LASTEXITCODE -ne 0) { throw "发布 CLI 失败" }

Copy-Core $dist
Copy-Item (Join-Path $repoRoot "README.md") $dist

$size = [math]::Round((Get-ChildItem -Recurse -File $dist | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "已发布到 $dist ($size MB): YsmConvert.exe(GUI) / ysmconv.exe(CLI + MCP) / core"
