<#
.SYNOPSIS
  生成便携 Python 2.7 运行时 core/python/(python.exe + python27.dll + Lib\ + DLLs\ + pypinyin), 供转换内核使用。

.DESCRIPTION
  内核必须跑在 Python 2.7 —— 它 import 的 packParser 在游戏内也是 2.7 解释器执行, 产物与游戏解析口径才一致。
  2.7 没有官方嵌入版, 这里从本机安装(缺省 C:\Python27)裁剪出一份独立目录:
    python.exe / python27.dll   解释器(DLL 在全用户安装下位于 System32)
    Lib\                        标准库(裁掉测试/GUI/开发工具), 必须是真实目录 —— 解释器按 Lib\os.py 地标把自身目录
                                当 prefix, 从而跳过注册表里其它 Python 安装的核心路径(否则本机装了 2.7 时会从那边加载)
    DLLs\                       内核用到的扩展模块 .pyd
    Lib\site-packages\          pypinyin(中文标识符转拼音) + enum34(它在 py2 的依赖)
  运行时以 python.exe -E -s -B 启动: 忽略 PYTHON* 环境变量与用户 site, 不写 .pyc。

.PARAMETER PythonHome
  本机 Python 2.7 安装目录。缺省 C:\Python27。
.PARAMETER Force
  已存在 core/python 时先删除重建。
#>
param(
    [string]$PythonHome = "C:\Python27",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $repoRoot "core\python"

if (-not (Test-Path (Join-Path $PythonHome "python.exe"))) {
    throw "找不到 Python 2.7: $PythonHome\python.exe (装一份 python-2.7.18.amd64.msi 再来, 或用 -PythonHome 指定)"
}
$version = & (Join-Path $PythonHome "python.exe") -c "import sys; sys.stdout.write('%d.%d' % sys.version_info[:2])"
if ($version -ne "2.7") { throw "需要 Python 2.7, 找到的是 $version" }

if (Test-Path $target) {
    if (-not $Force) { throw "已存在 $target (加 -Force 重建)" }
    Remove-Item -Recurse -Force $target
}
New-Item -ItemType Directory -Force $target, (Join-Path $target "DLLs"), (Join-Path $target "Lib\site-packages") | Out-Null

Copy-Item (Join-Path $PythonHome "python.exe") $target
$dll = @("$PythonHome\python27.dll", "$env:SystemRoot\System32\python27.dll") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $dll) { throw "找不到 python27.dll(安装目录与 System32 都没有)" }
Copy-Item $dll $target

# 内核实际用到的扩展模块(其余 .pyd 不带): unicodedata 供 re/拼音, 其余是标准库按需 import 的可选依赖
foreach ($pyd in @("unicodedata.pyd", "bz2.pyd", "_hashlib.pyd", "select.pyd", "_socket.pyd", "pyexpat.pyd", "_ctypes.pyd")) {
    $p = Join-Path $PythonHome "DLLs\$pyd"
    if (Test-Path $p) { Copy-Item $p (Join-Path $target "DLLs") }
}

# 标准库(只拷 .py; 剔除测试/GUI/开发工具等内核用不到的大块)
$skip = @("test", "site-packages", "lib-tk", "idlelib", "lib2to3", "ensurepip", "distutils", "bsddb", "sqlite3",
          "multiprocessing", "pydoc_data", "curses", "wsgiref", "hotshot", "compiler", "msilib", "lib-old",
          "unittest\test", "ctypes\test", "email\test", "json\tests")
$libSrc = Join-Path $PythonHome "Lib"
$libDst = Join-Path $target "Lib"
$count = 0
Get-ChildItem -Recurse -File -Filter *.py $libSrc | ForEach-Object {
    $rel = $_.FullName.Substring($libSrc.Length + 1)
    $skipIt = $false
    foreach ($s in $skip) { if ($rel -eq $s -or $rel.StartsWith("$s\")) { $skipIt = $true; break } }
    if ($skipIt) { return }
    $dst = Join-Path $libDst $rel
    New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null
    Copy-Item $_.FullName $dst
    $count++
}

# pypinyin(中文标识符转拼音; 缺席时内核退化成码点形式, 可用但难读) + enum34(pypinyin 在 py2 下的依赖)
foreach ($pkg in @("pypinyin", "enum")) {
    $src = Join-Path $PythonHome "Lib\site-packages\$pkg"
    if (Test-Path $src) {
        Copy-Item -Recurse $src (Join-Path $target "Lib\site-packages\$pkg")
        Get-ChildItem -Recurse (Join-Path $target "Lib\site-packages\$pkg") -Include *.pyc, *.pyi | Remove-Item -Force
    } else {
        Write-Warning "本机 Python 2.7 未安装 $pkg (pip install $pkg), 转换器将退化为码点命名"
    }
}

# VC++ 2008 运行库随包带一份"应用程序私有程序集": python.exe 与 python27.dll 的嵌入清单声明依赖
# Microsoft.VC90.CRT, 而 Windows 默认不带 —— 目标机器没装 VC++ 2008 x64 时 python.exe 根本起不来
# (CreateProcess 报 14001, 或进程以 0xC0150002 退出)。私有程序集放在 python.exe 同目录的
# Microsoft.VC90.CRT\ 下; 系统 WinSxS 里有同名程序集时加载器优先用系统的, 没有才回落到这份,
# 所以装了运行库的机器上验证不出差别(本机实测两边都从 WinSxS 加载)。清单里的版本必须与 exe 请求的
# 完全一致(私有程序集不走 publisher policy 重定向), 故从 exe 的嵌入清单里读, 不写死。
$crtDeployed = "未部署"
$exeText = [System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes((Join-Path $target "python.exe")))
if ($exeText -match 'name="Microsoft\.VC90\.CRT"\s+version="([0-9.]+)"\s+processorArchitecture="([A-Za-z0-9]+)"\s+publicKeyToken="([0-9a-fA-F]+)"') {
    $crtVersion = $Matches[1]; $crtArch = $Matches[2]; $crtToken = $Matches[3]
    $crtCandidates = @()
    $crtCandidates += Get-ChildItem "$env:SystemRoot\WinSxS" -Directory -Filter "${crtArch}_microsoft.vc90.crt_*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName "msvcr90.dll" }
    $crtCandidates += @("$env:SystemRoot\System32\msvcr90.dll", (Join-Path $PythonHome "msvcr90.dll"))
    $crtSource = $crtCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($crtSource) {
        $crtDir = Join-Path $target "Microsoft.VC90.CRT"
        New-Item -ItemType Directory -Force $crtDir | Out-Null
        Copy-Item $crtSource (Join-Path $crtDir "msvcr90.dll") -Force
        $crtManifest = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <noInheritable/>
  <assemblyIdentity type="win32" name="Microsoft.VC90.CRT" version="$crtVersion" processorArchitecture="$crtArch" publicKeyToken="$crtToken"/>
  <file name="msvcr90.dll"/>
</assembly>
"@
        [System.IO.File]::WriteAllText((Join-Path $crtDir "Microsoft.VC90.CRT.manifest"), $crtManifest,
                                       (New-Object System.Text.UTF8Encoding($false)))
        $crtDeployed = "$crtVersion/$crtArch <- $crtSource"
    } else {
        Write-Warning "本机找不到 msvcr90.dll, 发布包在未装 VC++ 2008 x64 的机器上无法启动内核; 装一份 vcredist_x64.exe(2008 SP1) 后重跑本脚本"
    }
} else {
    Write-Warning "未能从 python.exe 读出 Microsoft.VC90.CRT 依赖标识, 跳过私有运行库部署"
}

# 自检: 用生成的解释器导入内核依赖, 并确认所有模块都来自本目录(不受本机其它 Python 安装影响)
$probe = @'
import sys, os
import json, re, shutil, tempfile, collections, io, unicodedata
import pypinyin, enum
home = os.path.dirname(os.path.abspath(sys.executable)).lower()
outside = [n for n, m in sys.modules.items() if m is not None and n != "__main__" and getattr(m, "__file__", None)
           and not os.path.abspath(m.__file__).lower().startswith(home)]
sys.stdout.write("ok %s outside=%s prefix=%s" % (sys.version.split()[0], outside, sys.prefix))
'@
$tmp = Join-Path $env:TEMP "ysmconv_probe.py"
Set-Content -Path $tmp -Value $probe -Encoding ascii
$check = & (Join-Path $target "python.exe") -E -s -B $tmp
Remove-Item $tmp -Force
$size = [math]::Round((Get-ChildItem -Recurse -File $target | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "便携 Python 已生成: $target ($size MB, 标准库 $count 个文件)"
Write-Host "私有 VC++ 2008 运行库: $crtDeployed"
Write-Host "自检: $check"
if ($check -notmatch "outside=\[\]") { throw "自检失败: 有模块从本目录之外加载" }
