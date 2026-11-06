<#
.SYNOPSIS
  发布可分发目录 dist/YsmConvert/: GUI(YsmConvert.exe) + CLI/MCP(ysmconv.exe) + core/(内核快照 + 便携 Python + 文档)。

.DESCRIPTION
  两个 exe 由 cargo 以 release 配置构建(静态链接, 不依赖任何运行时), 拷到同一目录。
  core/python 必须先由 build/make-python.ps1 生成, core/kernel 与 core/docs 由 build/sync-core.ps1 同步。
  旧 dist 先整目录改名再删除: 目录里有文件被占用(GUI / CLI 正在运行)时改名会失败, 于是在删掉任何东西之前就中止 ——
  2026-09-18 曾在 GUI 运行中直接 Remove-Item, 删到被锁的文件才报错, dist 只剩半截。

.PARAMETER Version
  写进两个 exe 的版本号(文件属性里的版本、MCP 的 serverInfo、ysmconv --version)。缺省取 Cargo.toml 的
  [workspace.package] version; 发版流水线 .github/workflows/release.yml 按 tag 递增出版本号后从这里传入
  (经环境变量 YSMCONV_VERSION 交给构建)。
.PARAMETER KernelOnly
  只把 core/(kernel / docs / python)重新拷进已有的 dist, 不重新构建 exe —— 改了内核、GUI 又开着的时候用。
.PARAMETER SkipSync
  不先同步 core/kernel 快照。缺省行为: 上一级是 YSM 工程时先跑 build/sync-core.ps1, 保证发布包带的是当前 devtools
  (开发构建直接跑工程的 devtools, 只有发布包吃快照 —— 不自动同步就会发出旧内核)。Actions 发版的 runner 上没有工程,
  自动跳过, 发的是仓库里提交的那份快照。
#>
param(
    [string]$Version,
    [switch]$KernelOnly,
    [switch]$SkipSync
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $repoRoot "dist\YsmConvert"

if (-not $SkipSync) {
    $ysmRepo = Split-Path -Parent $repoRoot
    if (Test-Path (Join-Path $ysmRepo "devtools\port_cli.py")) {
        Write-Host "同步内核快照(发布包用): $ysmRepo"
        & (Join-Path $PSScriptRoot "sync-core.ps1") -YsmRepo $ysmRepo
        if ($LASTEXITCODE) { throw "同步内核快照失败" }
    } else {
        Write-Host "上一级不是 YSM 工程, 跳过同步, 发布仓库里现有的 core/kernel 快照"
    }
}

foreach ($required in @("core\kernel\devtools\port_cli.py", "core\python\python.exe", "core\python\python27.zip")) {
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
    Get-ChildItem -Recurse $coreDst -Include *.pyc, __pycache__ | Where-Object { $_.FullName -notlike "*python27.zip*" } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
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

# 先构建: 编译失败时旧 dist 原样保留。PATH 里没有 cargo 时(rustup 装的时候没改 PATH)用用户目录下的那份
$cargo = (Get-Command cargo -ErrorAction SilentlyContinue).Source
if (-not $cargo) {
    $cargo = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
    if (-not (Test-Path $cargo)) { throw "找不到 cargo: 先装 Rust(https://rustup.rs), 或把 %USERPROFILE%\.cargo\bin 加进 PATH" }
}
if ($Version) { $env:YSMCONV_VERSION = $Version } else { Remove-Item Env:YSMCONV_VERSION -ErrorAction SilentlyContinue }
Push-Location $repoRoot
# cargo 把进度写到 stderr; Windows PowerShell 5.1 在输出被重定向时会把 stderr 行当成错误记录, 遇上 Stop 就中断 ——
# 构建期间放宽, 并把它的 stderr 转成普通文本行; 成败只看退出码
$savedPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $cargo build --release --locked -p ysmconv -p ysmconvert-gui 2>&1 | ForEach-Object { "$_" }
    $buildExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $savedPreference
    Pop-Location
}
if ($buildExit) { throw "cargo build 失败(退出码 $buildExit)" }

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
foreach ($exe in @("YsmConvert.exe", "ysmconv.exe")) {
    Copy-Item (Join-Path $repoRoot "target\release\$exe") $dist
}
Copy-Core $dist
Copy-Item (Join-Path $repoRoot "README.md") $dist

$size = [math]::Round((Get-ChildItem -Recurse -File $dist | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "已发布到 $dist ($size MB): YsmConvert.exe(GUI) / ysmconv.exe(CLI + MCP) / core"
