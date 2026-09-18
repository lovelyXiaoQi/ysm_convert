using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Data;
using YsmConvert.App.Services;
using YsmConvert.Core;

namespace YsmConvert.App.ViewModels;

public sealed class MainViewModel : ObservableObject
{
    private readonly SynchronizationContext _ui;
    private readonly AppSettings _settings;
    private ConversionService? _service;
    private string? _kernelError;
    private CancellationTokenSource? _cts;
    private ConversionReport? _lastReport;
    private OutputLayout? _lastLayout;

    private bool _isRunning;
    private string _statusText = "就绪";
    private double _progress;
    private bool _isIndeterminate;
    private string _elapsedText = "";
    private System.Windows.Threading.DispatcherTimer? _timer;
    private PackItem? _selectedPack;
    private int _selectedTab;

    public MainViewModel()
    {
        _ui = SynchronizationContext.Current ?? new SynchronizationContext();
        _settings = SettingsStore.Load();
        LogView = CollectionViewSource.GetDefaultView(Logs);
        LogView.Filter = o => ShowDetails || o is LogEntry { Level: not "detail" };

        AddPacksCommand = new RelayCommand(async () => await PickAndAddAsync(), () => !IsRunning);
        RemoveSelectedCommand = new RelayCommand(RemoveSelected, _ => !IsRunning);
        ClearCommand = new RelayCommand(() => Packs.Clear(), () => !IsRunning);
        BrowseOutputCommand = new RelayCommand(BrowseOutput, () => !IsRunning);
        RunCommand = new RelayCommand(async () => await RunAsync(), () => !IsRunning && _service is not null);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsRunning);
        ValidateCommand = new RelayCommand(async () => await ValidateAsync(), () => !IsRunning && _service is not null);
        FixCommand = new RelayCommand(async () => await FixAsync(), () => !IsRunning && _service is not null);
        OpenOutputCommand = new RelayCommand(OpenOutput);
        ExportLogCommand = new RelayCommand(ExportLog, () => _lastReport is not null);
        OpenWikiCommand = new RelayCommand(() => OpenUrl(YsmLinks.WikiUrl));
        OpenRepoCommand = new RelayCommand(() => OpenUrl(YsmLinks.RepoUrl));
        BrowseCoverCommand = new RelayCommand(BrowseCover, () => !IsRunning);
        ClearCoverCommand = new RelayCommand(() => CollectionCover = "", () => !IsRunning);
        ApplyCollectionCommand = new RelayCommand(ApplyCollection, () => !IsRunning);
        CopyAttentionCommand = new RelayCommand(CopyAttention, () => Attention.Count > 0);

        KernelReady = InitKernelAsync();
    }

    /// <summary>内核探测任务; 窗口加载后 await 它再决定要不要弹错误框。</summary>
    public Task KernelReady { get; }

    /// <summary>内核不可用时的完整说明, 可用时为 null。</summary>
    public string? KernelError => _kernelError;

    // ---------------------------------------------------------------- collections
    public ObservableCollection<PackItem> Packs { get; } = new();
    public ObservableCollection<LogEntry> Logs { get; } = new();
    public ICollectionView LogView { get; }
    public ObservableCollection<AttentionEntry> Attention { get; } = new();
    public ObservableCollection<ValidationEntry> ValidationItems { get; } = new();
    /// <summary>线上文档与教程(人看的); core/docs 的本地副本只给 AI 通过 MCP 查。</summary>
    public string WikiUrl => YsmLinks.WikiUrl;
    /// <summary>转换器本身的开源仓库。</summary>
    public string RepoUrl => YsmLinks.RepoUrl;

    // ---------------------------------------------------------------- settings-backed properties
    public string OutputRoot { get => _settings.OutputRoot; set { if (_settings.OutputRoot != value) { _settings.OutputRoot = value; OnPropertyChanged(); } } }
    public bool IsComponentMode { get => _settings.IsComponentMode; set { if (_settings.IsComponentMode != value) { _settings.IsComponentMode = value; OnPropertyChanged(); OnPropertyChanged(nameof(IsProjectMode)); } } }
    public bool IsProjectMode { get => !IsComponentMode; set => IsComponentMode = !value; }
    public string ComponentName { get => _settings.ComponentName; set { if (_settings.ComponentName != value) { _settings.ComponentName = value; OnPropertyChanged(); } } }
    public bool WithMods { get => _settings.WithMods; set { if (_settings.WithMods != value) { _settings.WithMods = value; OnPropertyChanged(); } } }
    public bool ValidateAfter { get => _settings.ValidateAfter; set { if (_settings.ValidateAfter != value) { _settings.ValidateAfter = value; OnPropertyChanged(); } } }
    public bool UseCollection { get => _settings.UseCollection; set { if (_settings.UseCollection != value) { _settings.UseCollection = value; OnPropertyChanged(); } } }
    public string CollectionDir { get => _settings.CollectionDir; set { if (_settings.CollectionDir != value) { _settings.CollectionDir = value; OnPropertyChanged(); } } }
    public string CollectionName { get => _settings.CollectionName; set { if (_settings.CollectionName != value) { _settings.CollectionName = value; OnPropertyChanged(); } } }
    public string CollectionCover { get => _settings.CollectionCover; set { if (_settings.CollectionCover != value) { _settings.CollectionCover = value; OnPropertyChanged(); } } }
    public bool CompactJson { get => _settings.CompactJson; set { if (_settings.CompactJson != value) { _settings.CompactJson = value; OnPropertyChanged(); } } }
    public bool ShowDetails
    {
        get => _settings.ShowDetails;
        set
        {
            if (_settings.ShowDetails == value) return;
            _settings.ShowDetails = value;
            OnPropertyChanged();
            LogView.Refresh();
        }
    }

    // ---------------------------------------------------------------- state
    public bool IsRunning
    {
        get => _isRunning;
        private set
        {
            if (!SetProperty(ref _isRunning, value)) return;
            foreach (var c in new[] { AddPacksCommand, RemoveSelectedCommand, ClearCommand, BrowseOutputCommand, RunCommand, CancelCommand, ValidateCommand, FixCommand, ApplyCollectionCommand, BrowseCoverCommand, ClearCoverCommand })
                c.RaiseCanExecuteChanged();
        }
    }

    public string StatusText { get => _statusText; private set => SetProperty(ref _statusText, value); }
    public double Progress { get => _progress; private set => SetProperty(ref _progress, value); }
    public bool IsIndeterminate { get => _isIndeterminate; private set => SetProperty(ref _isIndeterminate, value); }
    /// <summary>转换计时: 运行中每 100ms 刷新, 结束后停在总耗时。</summary>
    public string ElapsedText { get => _elapsedText; private set => SetProperty(ref _elapsedText, value); }
    public PackItem? SelectedPack { get => _selectedPack; set => SetProperty(ref _selectedPack, value); }
    public int SelectedTab { get => _selectedTab; set => SetProperty(ref _selectedTab, value); }
    public int AttentionCount => Attention.Count;

    // ---------------------------------------------------------------- commands
    public RelayCommand AddPacksCommand { get; }
    public RelayCommand RemoveSelectedCommand { get; }
    public RelayCommand ClearCommand { get; }
    public RelayCommand BrowseOutputCommand { get; }
    public RelayCommand RunCommand { get; }
    public RelayCommand CancelCommand { get; }
    public RelayCommand ValidateCommand { get; }
    public RelayCommand FixCommand { get; }
    public RelayCommand OpenOutputCommand { get; }
    public RelayCommand ExportLogCommand { get; }
    public RelayCommand OpenWikiCommand { get; }
    public RelayCommand OpenRepoCommand { get; }
    public RelayCommand ApplyCollectionCommand { get; }
    public RelayCommand CopyAttentionCommand { get; }
    public RelayCommand BrowseCoverCommand { get; }
    public RelayCommand ClearCoverCommand { get; }

    // ---------------------------------------------------------------- kernel
    private async Task InitKernelAsync()
    {
        if (!KernelLocator.TryLocate(out var paths, out var error))
        {
            _kernelError = error;
            StatusText = "内核不可用: " + error?.Split('\n')[0];
            return;
        }
        _service = new ConversionService(paths!);
        try
        {
            await _service.GetInfoAsync();
        }
        catch (Exception ex) when (ex is KernelException or IOException)
        {
            _kernelError = ex.Message;
            StatusText = "内核启动失败: " + ex.Message.Split('\n')[0];
            if (ex is KernelException { Details: not null } detailed)
                Logs.Add(new LogEntry("", "detail", detailed.Details));
            _service = null;
        }
        RunCommand.RaiseCanExecuteChanged();
        ValidateCommand.RaiseCanExecuteChanged();
        FixCommand.RaiseCanExecuteChanged();
    }

    // ---------------------------------------------------------------- packs
    private async Task PickAndAddAsync()
    {
        var dialog = new Microsoft.Win32.OpenFolderDialog
        {
            Title = "选择 Java 模型包目录 / 合集目录 / 装着若干包的上级目录(可多选)",
            Multiselect = true,
        };
        if (!string.IsNullOrEmpty(_settings.LastInputDir) && Directory.Exists(_settings.LastInputDir))
            dialog.InitialDirectory = _settings.LastInputDir;
        if (dialog.ShowDialog() != true) return;
        var folders = dialog.FolderNames;
        if (folders.Length > 0)
            _settings.LastInputDir = Path.GetDirectoryName(folders[0]) ?? folders[0];
        await AddPathsAsync(folders);
    }

    public async Task AddPathsAsync(IEnumerable<string> paths)
    {
        if (_service is null)
        {
            StatusText = _kernelError ?? "内核不可用";
            return;
        }
        var dirs = paths.Select(p => File.Exists(p) ? Path.GetDirectoryName(p)! : p).Distinct().ToList();
        if (dirs.Count == 0) return;
        IsIndeterminate = true;
        StatusText = "正在扫描 Java 模型包…";
        try
        {
            var found = await _service.DiscoverAsync(dirs);
            var added = 0;
            foreach (var pack in found)
            {
                if (Packs.Any(p => string.Equals(p.JavaDir, pack.JavaDir, StringComparison.OrdinalIgnoreCase)))
                    continue;
                var item = new PackItem(pack);
                if (UseCollection && !string.IsNullOrWhiteSpace(CollectionDir))
                    item.Collection = CollectionDir.Trim();
                Packs.Add(item);
                added++;
            }
            StatusText = found.Count == 0
                ? "所选目录里没有 Java 模型包(需要含 ysm.json 的目录)"
                : $"新增 {added} 个包(共 {Packs.Count} 个)";
        }
        catch (Exception ex) when (ex is KernelException or IOException)
        {
            StatusText = "扫描失败: " + ex.Message;
        }
        finally
        {
            IsIndeterminate = false;
        }
    }

    private void RemoveSelected(object? parameter)
    {
        var selected = (parameter as System.Collections.IList)?.Cast<PackItem>().ToList() ?? new List<PackItem>();
        if (selected.Count == 0 && SelectedPack is not null) selected.Add(SelectedPack);
        foreach (var item in selected) Packs.Remove(item);
    }

    private void ApplyCollection()
    {
        var dir = UseCollection ? CollectionDir.Trim() : "";
        foreach (var p in Packs) p.Collection = dir;
        StatusText = UseCollection ? $"全部包归入合集 {dir}" : "全部包改为平铺(不归入合集)";
    }

    // ---------------------------------------------------------------- output
    private void BrowseOutput()
    {
        var dialog = new Microsoft.Win32.OpenFolderDialog { Title = IsComponentMode ? "选择放置新组件的目录" : "选择已有的组件工程根目录(含行为包与资源包)" };
        if (!string.IsNullOrEmpty(OutputRoot) && Directory.Exists(OutputRoot))
            dialog.InitialDirectory = OutputRoot;
        if (dialog.ShowDialog() == true)
            OutputRoot = dialog.FolderName;
    }

    private void BrowseCover()
    {
        var dialog = new Microsoft.Win32.OpenFileDialog
        {
            Title = "选择文件夹封面图(PNG, 不超过 1MB, 最好是 Java 卡片 52x90 的比例)",
            Filter = "PNG 图片 (*.png)|*.png",
        };
        if (!string.IsNullOrEmpty(CollectionCover) && File.Exists(CollectionCover))
            dialog.InitialDirectory = Path.GetDirectoryName(CollectionCover);
        if (dialog.ShowDialog() != true) return;
        if (new CollectionSpec { Dir = "cover", CoverImage = dialog.FileName }.CoverProblem() is { } problem)
        {
            MessageBox.Show(problem, "文件夹封面", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        CollectionCover = dialog.FileName;
    }

    private OutputLayout? ResolveLayout(bool allowCreate)
    {
        if (string.IsNullOrWhiteSpace(OutputRoot))
        {
            StatusText = "请先选择输出目录";
            return null;
        }
        try
        {
            if (IsComponentMode)
            {
                if (!allowCreate && !Directory.Exists(Path.Combine(OutputRoot, ComponentName.Trim() + "_bp")))
                {
                    StatusText = $"组件 {ComponentName} 还不存在, 先转换一次";
                    return null;
                }
                return OutputTarget.EnsureComponent(OutputRoot, ComponentName.Trim());
            }
            return OutputTarget.Detect(OutputRoot);
        }
        catch (Exception ex) when (ex is ArgumentException or InvalidOperationException or IOException)
        {
            StatusText = ex.Message;
            MessageBox.Show(ex.Message, "输出目录", MessageBoxButton.OK, MessageBoxImage.Warning);
            return null;
        }
    }

    // ---------------------------------------------------------------- run
    private async Task RunAsync()
    {
        if (_service is null) return;
        var enabled = Packs.Where(p => p.Enabled).ToList();
        if (enabled.Count == 0)
        {
            StatusText = "没有勾选任何 Java 包";
            return;
        }
        var layout = ResolveLayout(allowCreate: true);
        if (layout is null) return;
        var packs = enabled.Select(p => new PackSpec { JavaDir = p.JavaDir, Name = p.Name.Trim(), Collection = p.CollectionOrNull }).ToList();
        var problems = PackNameRules.Validate(packs);
        if (problems.Count > 0)
        {
            MessageBox.Show(string.Join("\n", problems), "包名有问题", MessageBoxButton.OK, MessageBoxImage.Warning);
            StatusText = "请先修正包名";
            return;
        }
        var collections = new List<CollectionSpec>();
        var dir = CollectionDir.Trim();
        var collectionSpec = new CollectionSpec { Dir = dir, Name = CollectionName.Trim(), CoverImage = CollectionCover.Trim() };
        if (UseCollection && dir.Length > 0 && packs.Any(p => p.Collection == dir) && !collectionSpec.IsEmpty)
        {
            if (collectionSpec.CoverProblem() is { } coverProblem)
            {
                MessageBox.Show(coverProblem, "文件夹封面有问题", MessageBoxButton.OK, MessageBoxImage.Warning);
                StatusText = coverProblem;
                return;
            }
            collections.Add(collectionSpec);
        }
        var request = new ConvertRequest
        {
            Layout = layout,
            Packs = packs,
            Collections = collections,
            Options = new ConvertOptions { WithMods = WithMods, Validate = ValidateAfter, CompactJson = CompactJson },
        };
        var byName = enabled.ToDictionary(p => p.Name.Trim());
        foreach (var p in enabled)
        {
            p.Status = "排队中";
            p.AttentionCount = 0;
            p.Ok = null;
        }
        await ExecuteAsync("转换", byName, packs.Count, ct => _service.ConvertAsync(request, e => OnKernelEvent(e, byName), OnStderr, ct));
        SettingsStore.Save(_settings);
    }

    private async Task ValidateAsync()
    {
        if (_service is null) return;
        var layout = ResolveLayout(allowCreate: false);
        if (layout is null) return;
        var names = Packs.Where(p => p.Enabled).Select(p => p.Name.Trim()).ToList();
        var byName = Packs.Where(p => p.Enabled).ToDictionary(p => p.Name.Trim());
        await ExecuteAsync("体检", byName, 0, ct => _service.ValidateAsync(layout, names, e => OnKernelEvent(e, byName), OnStderr, ct));
    }

    private async Task FixAsync()
    {
        if (_service is null) return;
        var layout = ResolveLayout(allowCreate: false);
        if (layout is null) return;
        var names = Packs.Where(p => p.Enabled).Select(p => p.Name.Trim()).ToList();
        var byName = Packs.Where(p => p.Enabled).ToDictionary(p => p.Name.Trim());
        await ExecuteAsync("修复", byName, names.Count,
            ct => _service.FixAsync(layout, names, ValidateAfter, e => OnKernelEvent(e, byName), OnStderr, ct, CompactJson));
    }

    private async Task ExecuteAsync(string verb, Dictionary<string, PackItem> byName, int total,
        Func<CancellationToken, Task<ConversionReport>> action)
    {
        Logs.Clear();
        Attention.Clear();
        ValidationItems.Clear();
        _pendingLogs.Clear();
        _molangStream = new MolangPresentation.StreamBuffer();
        _runningPacks.Clear();
        _finishedPacks = 0;
        OnPropertyChanged(nameof(AttentionCount));
        Progress = 0;
        IsIndeterminate = total == 0;
        IsRunning = true;
        _cts = new CancellationTokenSource();
        StatusText = $"{verb}中…";
        var watch = Stopwatch.StartNew();
        ElapsedText = FormatElapsed(watch.Elapsed);
        _timer ??= new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromMilliseconds(100) };
        void Tick(object? s, EventArgs a) => ElapsedText = FormatElapsed(watch.Elapsed);
        _timer.Tick += Tick;
        _timer.Start();
        try
        {
            var report = await action(_cts.Token);
            _lastReport = report;
            _lastLayout = report.Layout;
            foreach (var item in report.BuildAttention())
                Attention.Add(new AttentionEntry(item));
            foreach (var v in report.Validation)
                ValidationItems.Add(new ValidationEntry(v));
            OnPropertyChanged(nameof(AttentionCount));
            foreach (var pack in report.Packs)
            {
                if (!byName.TryGetValue(pack.Name, out var item)) continue;
                item.AttentionCount = Attention.Count(a => a.Pack == pack.Name);
                item.Ok = pack.Ok;
                item.Status = pack.Ok switch
                {
                    true => item.AttentionCount > 0 ? $"完成, {item.AttentionCount} 项待过目" : "完成",
                    false => "失败",
                    null => item.Status,
                };
            }
            if (report.FatalError is not null)
            {
                StatusText = report.FatalError;
                Logs.Add(new LogEntry("", "error", report.FatalError));
                MessageBox.Show(report.FatalError, "内核未能运行", MessageBoxButton.OK, MessageBoxImage.Error);
            }
            else
            {
                StatusText = report.Ok
                    ? $"{verb}完成: 成功 {report.PacksOk}, 体检错误 {report.ValidationErrors ?? 0}, 待过目 {Attention.Count} 项 (资源包改动需重启游戏)"
                    : $"{verb}结束但有问题: 失败 {report.PacksFailed}, 体检错误 {report.ValidationErrors ?? 0}, 待过目 {Attention.Count} 项";
                if (Attention.Count > 0) SelectedTab = 1;
            }
            Progress = 100;
        }
        catch (OperationCanceledException)
        {
            StatusText = $"{verb}已取消";
            foreach (var item in byName.Values.Where(i => i.Status is "排队中" or "转换中")) item.Status = "已取消";
        }
        catch (ArgumentException ex)
        {
            StatusText = ex.Message;
            MessageBox.Show(ex.Message, "无法开始", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        finally
        {
            _timer.Stop();
            _timer.Tick -= Tick;
            watch.Stop();
            ElapsedText = FormatElapsed(watch.Elapsed);
            IsIndeterminate = false;
            IsRunning = false;
            _cts = null;
            ExportLogCommand.RaiseCanExecuteChanged();
            CopyAttentionCommand.RaiseCanExecuteChanged();
        }
    }

    private static string FormatElapsed(TimeSpan elapsed) =>
        elapsed.TotalHours >= 1
            ? $"耗时 {(int)elapsed.TotalHours}:{elapsed.Minutes:00}:{elapsed.Seconds:00}"
            : $"耗时 {elapsed.Minutes}:{elapsed.Seconds:00}.{elapsed.Milliseconds / 100}";

    // 多个包并行转换时各包的事件会交错到达: 日志按包缓冲, 该包完成时整块写入日志面板
    // (内核本来就是一个包转完才吐出它的全部日志行, 缓冲不损失实时性)
    private readonly Dictionary<string, List<LogEntry>> _pendingLogs = new(StringComparer.Ordinal);
    private MolangPresentation.StreamBuffer _molangStream = new();
    private readonly List<string> _runningPacks = new();
    private int _finishedPacks;

    private List<LogEntry> PendingFor(string? pack)
    {
        var key = pack ?? "";
        if (!_pendingLogs.TryGetValue(key, out var list))
            _pendingLogs[key] = list = new List<LogEntry>();
        return list;
    }

    private void UpdateRunningStatus(int total)
    {
        var running = _runningPacks.Count == 0
            ? ""
            : $", 正在转换: {string.Join("、", _runningPacks.Take(3))}{(_runningPacks.Count > 3 ? $" 等 {_runningPacks.Count} 个" : "")}";
        StatusText = $"已完成 {_finishedPacks}/{total}{running}";
    }

    private void OnKernelEvent(KernelEvent e, Dictionary<string, PackItem> byName)
    {
        _ui.Post(_ =>
        {
            switch (e.Event)
            {
                case KernelEvent.PackStart:
                    if (e.PackName is not null && byName.TryGetValue(e.PackName, out var starting))
                        starting.Status = "转换中";
                    if (e.PackName is not null) _runningPacks.Add(e.PackName);
                    PendingFor(e.PackName).Add(new LogEntry(e.PackName ?? "", "info",
                        $"== {e.PackName}{(e.JavaDir is null ? "" : "  <- " + e.JavaDir)}"));
                    UpdateRunningStatus(e.Total ?? byName.Count);
                    break;
                case KernelEvent.Log:
                    PendingFor(e.PackName).Add(new LogEntry(e.PackName ?? "", e.Level ?? "info", e.Text ?? ""));
                    break;
                case KernelEvent.Molang:
                    if (_molangStream.Line(e) is { } molangLine)
                        PendingFor(e.PackName).Add(new LogEntry(e.PackName ?? "",
                            MolangPresentation.Severity(e.Kind) == "info" ? "info" : "notice", molangLine));
                    break;
                case KernelEvent.PackDone:
                    if (e.PackName is not null && byName.TryGetValue(e.PackName, out var done))
                        done.Status = e.Ok == true ? "完成" : "失败";
                    var block = PendingFor(e.PackName);
                    if (_molangStream.Flush(e.PackName) is { } lowerLine)
                        block.Add(new LogEntry(e.PackName ?? "", "info", lowerLine));
                    block.Add(new LogEntry(e.PackName ?? "", e.Ok == true ? "info" : "error",
                        e.Ok == true
                            ? $"-> 完成 {e.Seconds:0.0}s  错误 {e.Errors} / 警告 {e.Warnings} / 提醒 {e.Notices}"
                            : $"-> 失败: {e.Error}"));
                    foreach (var entry in block) Logs.Add(entry);
                    _pendingLogs.Remove(e.PackName ?? "");
                    if (e.PackName is not null) _runningPacks.Remove(e.PackName);
                    _finishedPacks++;
                    if (e.Total is > 0)
                        Progress = 100.0 * _finishedPacks / e.Total.Value;
                    UpdateRunningStatus(e.Total ?? byName.Count);
                    break;
                case KernelEvent.Collection:
                    Logs.Add(new LogEntry("", "info", $"合集清单 {e.Dir}: {(e.Written == true ? "已写入" : "沿用 Java 源")} {e.Path}"));
                    if (e.ExtraString("cover") is { Length: > 0 } cover)
                        Logs.Add(new LogEntry("", "info", $"   文件夹封面 → {cover}.png"));
                    if (e.ExtraString("warning") is { Length: > 0 } coverWarning)
                        Logs.Add(new LogEntry("", "warn", $"   [WARN] {coverWarning}"));
                    break;
                case KernelEvent.ValidateItem:
                    Logs.Add(new LogEntry("", e.Level == "error" ? "error" : "warn", $"体检: {e.Text}"));
                    break;
                case KernelEvent.ValidateDone:
                    Logs.Add(new LogEntry("", "info", $"体检: 动画文件 {e.Animations} / 控制器文件 {e.Controllers}; 错误 {e.Errors} / 警告 {e.Warnings}"));
                    break;
            }
        }, null);
    }

    private void OnStderr(string line) =>
        _ui.Post(_ => Logs.Add(new LogEntry("", "detail", "(stderr) " + line)), null);

    // ---------------------------------------------------------------- misc commands
    private void OpenOutput()
    {
        var target = _lastLayout?.Root ?? (Directory.Exists(OutputRoot) ? OutputRoot : null);
        OpenPath(target);
    }

    private static void OpenPath(string? path)
    {
        if (string.IsNullOrEmpty(path) || !(Directory.Exists(path) || File.Exists(path))) return;
        Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
    }

    /// <summary>用系统默认浏览器打开网址。</summary>
    public void OpenUrl(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
        }
        catch (System.ComponentModel.Win32Exception ex)
        {
            StatusText = $"打不开浏览器: {ex.Message}(网址 {url})";
        }
    }

    private void ExportLog()
    {
        if (_lastReport is null) return;
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            Title = "导出转换报告",
            Filter = "文本报告 (*.txt)|*.txt|JSON 报告 (*.json)|*.json",
            FileName = $"ysm-convert-{DateTime.Now:yyyyMMdd-HHmmss}.txt",
        };
        if (dialog.ShowDialog() != true) return;
        var text = dialog.FileName.EndsWith(".json", StringComparison.OrdinalIgnoreCase)
            ? _lastReport.ToJsonString()
            : _lastReport.ToText(details: true);
        File.WriteAllText(dialog.FileName, text, new UTF8Encoding(false));
        StatusText = "报告已导出: " + dialog.FileName;
    }

    private void CopyAttention()
    {
        var sb = new StringBuilder();
        foreach (var a in Attention)
            sb.AppendLine($"[{a.Severity}] ({a.Pack}) {a.Category}: {a.Text} {a.CountText}\n    → {a.Advice}{(a.Doc is null ? "" : $" [{a.Doc}]")}");
        Clipboard.SetText(sb.ToString());
        StatusText = $"已复制 {Attention.Count} 项到剪贴板";
    }

    public void SaveSettings() => SettingsStore.Save(_settings);
}
