using System.IO;
using YsmConvert.Core;

namespace YsmConvert.App.ViewModels;

/// <summary>任务列表里的一个 Java 包。</summary>
public sealed class PackItem : ObservableObject
{
    private bool _enabled = true;
    private string _name = "";
    private string _collection = "";
    private string _material = MaterialOptions.Auto;
    private string _cullMode = MaterialOptions.Auto;
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
        Summary = pack.Error ?? $"动画文件 {pack.AnimationFiles ?? 0} · 手臂几何 {(pack.HasArm == true ? "有" : "无")} · 弹射物 {pack.Projectiles ?? 0} · 载具 {pack.Vehicles ?? 0}" +
            (pack.MaterialProfile is { } profile
                ? $" · 材质 {MaterialOptions.DescribeAuto(profile)}{(profile.NegativeCubes > 0 ? $" · 负尺寸方块 {profile.NegativeCubes}" : "")}"
                : "");
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

    /// <summary>材质: "自动"、预设名、自定义材质名或 "键=材质, ..."(见 <see cref="MaterialOptions"/>)。</summary>
    public string Material
    {
        get => _material;
        set
        {
            if (SetProperty(ref _material, string.IsNullOrWhiteSpace(value) ? MaterialOptions.Auto : value.Trim()))
                OnPropertyChanged(nameof(MaterialTip));
        }
    }

    /// <summary>背面剔除: 自动 / 剔除 / 不剔除。</summary>
    public string CullMode
    {
        get => _cullMode;
        set
        {
            if (SetProperty(ref _cullMode, value))
                OnPropertyChanged(nameof(MaterialTip));
        }
    }

    /// <summary>材质 / 背面剔除两列的提示: 自动会选什么、写法有没有问题、负尺寸方块怎么显示。</summary>
    public string MaterialTip
    {
        get
        {
            var lines = new List<string>();
            var profile = Source.MaterialProfile;
            if (MaterialOptions.Problem(Material) is { } problem)
                lines.Add("写法有问题: " + problem);
            else if (MaterialOptions.Normalize(Material) is null)
                lines.Add($"自动: {MaterialOptions.DescribeAuto(profile)} —— Java properties.all_cutout 打开则剔除背面(负尺寸描边), 皮肤带 PBR 贴图用 pbr, 其余 cutout; 发光组(ysmGlow* 骨骼)用 emissive");
            else if (MaterialOptions.IsCustom(Material))
                lines.Add("自定义材质: 资源包的 materials/*.material 里要有它的定义(原版材质名也行), 不参与描边 / 泛光后处理, 背面剔除对它不起作用");
            var cull = CullValue;
            lines.Add($"背面剔除: {(cull is null ? $"自动(Java all_cutout = {(profile?.AllCutout == true ? "true, 剔除" : "false, 不剔除")})" : cull == true ? "剔除" : "不剔除")}");
            if (MaterialOptions.NegativeCubeHint(profile, cull) is { } hint)
                lines.Add(hint);
            return string.Join("\n", lines);
        }
    }

    public string Status { get => _status; set => SetProperty(ref _status, value); }
    public int AttentionCount { get => _attentionCount; set => SetProperty(ref _attentionCount, value); }
    public bool? Ok { get => _ok; set => SetProperty(ref _ok, value); }

    public string? CollectionOrNull => string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim();
    public string? MaterialOrNull => MaterialOptions.Normalize(Material);
    public bool? CullValue => CullMode switch { MaterialOptions.CullOn => true, MaterialOptions.CullOff => false, _ => null };
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
