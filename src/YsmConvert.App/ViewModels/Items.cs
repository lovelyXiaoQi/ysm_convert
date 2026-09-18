using System.IO;
using YsmConvert.Core;

namespace YsmConvert.App.ViewModels;

/// <summary>任务列表里的一个 Java 包。</summary>
public sealed class PackItem : ObservableObject
{
    private bool _enabled = true;
    private string _name = "";
    private string _collection = "";
    private string _status = "待转换";
    private int _attentionCount;
    private bool? _ok;

    public PackItem(DiscoveredPack pack)
    {
        Source = pack;
        JavaDir = pack.JavaDir;
        Folder = pack.Folder ?? Path.GetFileName(pack.JavaDir);
        DisplayName = pack.DisplayName;
        Author = pack.Author ?? "";
        _name = pack.SuggestedName ?? PackNameRules.Sanitize(Folder);
        _collection = pack.CollectionFolder ?? "";
        Summary = pack.Error ?? $"动画文件 {pack.AnimationFiles ?? 0} · 手臂几何 {(pack.HasArm == true ? "有" : "无")} · 弹射物 {pack.Projectiles ?? 0} · 载具 {pack.Vehicles ?? 0}";
        if (pack.Error is not null)
        {
            _enabled = false;
            _status = "无法读取";
        }
    }

    public DiscoveredPack Source { get; }
    public string JavaDir { get; }
    public string Folder { get; }
    public string DisplayName { get; }
    public string Author { get; }
    public string Summary { get; }

    public bool Enabled { get => _enabled; set => SetProperty(ref _enabled, value); }
    public string Name { get => _name; set => SetProperty(ref _name, value); }
    public string Collection { get => _collection; set => SetProperty(ref _collection, value); }
    public string Status { get => _status; set => SetProperty(ref _status, value); }
    public int AttentionCount { get => _attentionCount; set => SetProperty(ref _attentionCount, value); }
    public bool? Ok { get => _ok; set => SetProperty(ref _ok, value); }

    public string? CollectionOrNull => string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim();
}

public sealed class LogEntry
{
    public LogEntry(string pack, string level, string text)
    {
        Time = DateTime.Now;
        Pack = pack;
        Level = level;
        Text = text;
    }

    public DateTime Time { get; }
    public string Pack { get; }
    public string Level { get; }
    public string Text { get; }
    public string TimeText => Time.ToString("HH:mm:ss");
    public bool IsAttention => Level is "error" or "warn" or "notice";
}

public sealed class AttentionEntry
{
    public AttentionEntry(AttentionItem item)
    {
        Pack = item.Pack;
        Severity = item.Severity;
        Category = item.Category;
        Text = item.Text;
        Count = item.Count;
        Advice = item.Advice;
        Doc = item.Doc;
    }

    public string Pack { get; }
    public string Severity { get; }
    public string Category { get; }
    public string Text { get; }
    public int Count { get; }
    public string Advice { get; }
    public string? Doc { get; }
    public string CountText => Count > 1 ? $"x{Count}" : "";
}

public sealed class ValidationEntry
{
    public ValidationEntry(ValidationItem item)
    {
        Level = item.Level;
        Text = item.Text;
    }

    public string Level { get; }
    public string Text { get; }
}
