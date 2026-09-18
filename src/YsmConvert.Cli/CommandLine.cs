namespace YsmConvert.Cli;

/// <summary>极简参数解析: 位置参数 + --key value / --key=value / --flag。</summary>
internal sealed class ParsedArgs
{
    public List<string> Positional { get; } = new();
    private readonly Dictionary<string, List<string>> _options = new(StringComparer.OrdinalIgnoreCase);

    public static ParsedArgs Parse(IEnumerable<string> args, params string[] flags)
    {
        var flagSet = new HashSet<string>(flags, StringComparer.OrdinalIgnoreCase);
        var parsed = new ParsedArgs();
        var list = args.ToList();
        for (var i = 0; i < list.Count; i++)
        {
            var a = list[i];
            if (a.StartsWith("--", StringComparison.Ordinal) && a.Length > 2)
            {
                var body = a[2..];
                var eq = body.IndexOf('=');
                if (eq > 0)
                {
                    parsed.Add(body[..eq], body[(eq + 1)..]);
                    continue;
                }
                if (flagSet.Contains(body))
                {
                    parsed.Add(body, "true");
                    continue;
                }
                if (i + 1 >= list.Count)
                    throw new ArgumentException($"--{body} 需要一个值");
                parsed.Add(body, list[++i]);
            }
            else
            {
                parsed.Positional.Add(a);
            }
        }
        return parsed;
    }

    private void Add(string key, string value)
    {
        if (!_options.TryGetValue(key, out var values))
            _options[key] = values = new List<string>();
        values.Add(value);
    }

    public bool Has(string key) => _options.ContainsKey(key);

    public string? Get(string key) => _options.TryGetValue(key, out var v) ? v[^1] : null;

    public IReadOnlyList<string> GetAll(string key) => _options.TryGetValue(key, out var v) ? v : Array.Empty<string>();

    public string Require(string key)
    {
        return Get(key) ?? throw new ArgumentException($"缺少 --{key}");
    }
}
