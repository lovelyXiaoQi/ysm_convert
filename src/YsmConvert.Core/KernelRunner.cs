using System.ComponentModel;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace YsmConvert.Core;

/// <summary>起 Python 子进程跑 port_cli.py, 把 stdout 的 JSON 行解析成事件; stderr 原样转给调用方。</summary>
public sealed class KernelRunner
{
    /// <summary>
    /// ERROR_SXS_CANT_GEN_ACTCTX: 解析 exe 的并行程序集清单失败。
    /// python.exe 与 python27.dll 的嵌入清单依赖 Microsoft.VC90.CRT, 目标机器既没装 VC++ 2008 x64、
    /// 随包的私有程序集又缺失时, CreateProcess 就以这个错误失败。
    /// </summary>
    private const int ErrorSxsCantGenActCtx = 14001;

    private const int ErrorFileNotFound = 2;

    /// <summary>缺 VC++ 2008 运行库时给用户看的说明。</summary>
    public const string MissingVcRuntimeMessage =
        "转换内核用的 Python 2.7 需要 Visual C++ 2008 运行库(x64), 这台机器上没有。\n\n" +
        "请安装 vcredist_x64.exe(Microsoft Visual C++ 2008 SP1 可再发行组件包)后重试:\n" +
        "https://www.microsoft.com/download/details.aspx?id=26368\n\n" +
        "若发布包里带了 core\\python\\Microsoft.VC90.CRT\\, 请确认该目录连同 msvcr90.dll 与清单一起解压完整。";

    private readonly KernelPaths _paths;

    public KernelRunner(KernelPaths paths) => _paths = paths;

    public KernelPaths Paths => _paths;

    public async Task<KernelInfo?> GetInfoAsync(CancellationToken ct = default)
    {
        KernelInfo? info = null;
        var stderr = new StringBuilder();
        var code = await RunAsync(new[] { "--version" }, line =>
        {
            if (line.StartsWith('{'))
                info = JsonSerializer.Deserialize<KernelInfo>(line, KernelJson.Options);
        }, err => stderr.AppendLine(err), ct).ConfigureAwait(false);
        if (info is null)
            throw new KernelException($"转换内核没有响应(退出码 {code})", stderr.Length > 0 ? stderr.ToString() : _paths.PortCli);
        return info;
    }

    public async Task<IReadOnlyList<DiscoveredPack>> DiscoverAsync(IEnumerable<string> dirs, CancellationToken ct = default)
    {
        var request = Path.Combine(TempDir(), $"discover-{Guid.NewGuid():N}.json");
        await File.WriteAllTextAsync(request,
            JsonSerializer.Serialize(new { dirs = dirs.Select(Path.GetFullPath).ToArray() }, KernelJson.Options),
            new UTF8Encoding(false), ct).ConfigureAwait(false);
        var packs = new List<DiscoveredPack>();
        var stderr = new StringBuilder();
        try
        {
            var code = await RunAsync(new[] { "--discover", request }, line =>
            {
                if (!line.StartsWith('{')) return;
                using var doc = JsonDocument.Parse(line);
                if (doc.RootElement.TryGetProperty("event", out var ev) && ev.GetString() == KernelEvent.Pack)
                {
                    var pack = JsonSerializer.Deserialize<DiscoveredPack>(line, KernelJson.Options);
                    if (pack is not null) packs.Add(pack);
                }
            }, err => stderr.AppendLine(err), ct).ConfigureAwait(false);
            if (code != 0)
                throw new KernelException($"内核发现 Java 包失败(退出码 {code})", stderr.ToString());
        }
        finally
        {
            TryDelete(request);
        }
        return packs;
    }

    /// <summary>跑一份任务单; 每个事件回调一次。返回内核退出码(0 = 全部成功)。</summary>
    public async Task<int> RunJobAsync(JobSpec job, Action<KernelEvent> onEvent, Action<string>? onStderr, CancellationToken ct = default)
    {
        var jobFile = Path.Combine(TempDir(), $"job-{Guid.NewGuid():N}.json");
        await File.WriteAllTextAsync(jobFile, job.ToJson(), new UTF8Encoding(false), ct).ConfigureAwait(false);
        try
        {
            return await RunAsync(new[] { "--job", jobFile }, line =>
            {
                var ev = KernelEvent.TryParse(line);
                if (ev is not null) onEvent(ev);
                else onStderr?.Invoke(line);
            }, onStderr, ct).ConfigureAwait(false);
        }
        finally
        {
            TryDelete(jobFile);
        }
    }

    private async Task<int> RunAsync(IEnumerable<string> args, Action<string> onStdout, Action<string>? onStderr, CancellationToken ct)
    {
        if (!File.Exists(_paths.PortCli))
            throw new KernelException($"内核入口不存在: {_paths.PortCli}", null);
        var psi = new ProcessStartInfo(_paths.PythonExe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = false,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            WorkingDirectory = _paths.KernelRoot,
        };
        foreach (var a in _paths.PythonArgsPrefix) psi.ArgumentList.Add(a);
        psi.ArgumentList.Add("-E");   // 忽略 PYTHON* 环境变量(宿主机可能装着别的 Python)
        psi.ArgumentList.Add("-s");   // 不加载用户 site-packages
        psi.ArgumentList.Add("-B");   // 不写 .pyc(内核目录可能只读)
        psi.ArgumentList.Add(_paths.PortCli);
        foreach (var a in args) psi.ArgumentList.Add(a);

        using var process = new Process { StartInfo = psi };
        try
        {
            if (!process.Start())
                throw new KernelException("无法启动 Python 子进程", _paths.PythonExe);
        }
        catch (Win32Exception ex)
        {
            throw new KernelException(DescribeLaunchFailure(ex.NativeErrorCode), $"{_paths.PythonExe}\n{ex.Message}");
        }
        catch (Exception ex) when (ex is not KernelException)
        {
            throw new KernelException($"无法启动 Python 子进程: {_paths.PythonExe}", ex.Message);
        }

        using var registration = ct.Register(() =>
        {
            try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch { /* 已退出 */ }
        });

        var stderrTail = new StringBuilder();
        var stderrTask = Task.Run(async () =>
        {
            string? line;
            while ((line = await process.StandardError.ReadLineAsync().ConfigureAwait(false)) is not null)
            {
                if (stderrTail.Length < 4000) stderrTail.AppendLine(line);
                onStderr?.Invoke(line);
            }
        }, CancellationToken.None);

        string? outLine;
        while ((outLine = await process.StandardOutput.ReadLineAsync().ConfigureAwait(false)) is not null)
            onStdout(outLine);

        await stderrTask.ConfigureAwait(false);
        await process.WaitForExitAsync(CancellationToken.None).ConfigureAwait(false);
        ct.ThrowIfCancellationRequested();
        var exitCode = process.ExitCode;
        // 进程起来了但立刻挂掉: 0xC015xxxx 一族是并行程序集(SxS)错误, 最常见的就是缺 VC++ 2008 运行库,
        // 此时 python27.dll 加载失败, stdout 一个字节都没有。业务退出码(0/1/2)不在这个区间。
        if (IsSideBySideFailure(exitCode))
            throw new KernelException(MissingVcRuntimeMessage,
                $"{_paths.PythonExe} 以 0x{(uint)exitCode:X8} 退出\n{stderrTail}");
        return exitCode;
    }

    /// <summary>
    /// 退出码是不是 NTSTATUS 0xC015xxxx(STATUS_SXS_*, 并行程序集装载失败)。
    /// 内核自己的业务退出码只有 0/1/2, 与这一段不重叠。
    /// </summary>
    public static bool IsSideBySideFailure(int exitCode) => ((uint)exitCode >> 16) == 0xC015;

    private string DescribeLaunchFailure(int nativeError) => nativeError switch
    {
        ErrorSxsCantGenActCtx => MissingVcRuntimeMessage,
        ErrorFileNotFound => $"找不到 Python 解释器: {_paths.PythonExe}",
        _ => $"无法启动 Python 子进程(Win32 错误 {nativeError}): {_paths.PythonExe}",
    };

    private static string TempDir()
    {
        var dir = Path.Combine(Path.GetTempPath(), "YsmConvert");
        Directory.CreateDirectory(dir);
        return dir;
    }

    private static void TryDelete(string path)
    {
        try { File.Delete(path); } catch { /* 留着也无妨 */ }
    }
}

public sealed class KernelException : Exception
{
    public string? Details { get; }

    public KernelException(string message, string? details) : base(message) => Details = details;
}
