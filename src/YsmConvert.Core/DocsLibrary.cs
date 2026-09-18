namespace YsmConvert.Core;

public sealed record DocInfo(string Name, string Title, long Size);

public sealed record DocHit(string Name, int Line, string Text);

/// <summary>随内核同步过来的 YSM 移植文档(core/docs/*.md): 列表、读取、按关键字检索。供 GUI 帮助面板与 MCP 的 ysm_docs 工具用。</summary>
public sealed class DocsLibrary
{
    private readonly string _dir;

    public DocsLibrary(string dir) => _dir = dir;

    public string Dir => _dir;

    public IReadOnlyList<DocInfo> List()
    {
        if (!Directory.Exists(_dir)) return Array.Empty<DocInfo>();
        var docs = new List<DocInfo>();
        foreach (var file in Directory.EnumerateFiles(_dir, "*.md").OrderBy(f => f, StringComparer.OrdinalIgnoreCase))
        {
            var title = File.ReadLines(file).FirstOrDefault(l => l.StartsWith("# ", StringComparison.Ordinal))?[2..].Trim()
                        ?? Path.GetFileNameWithoutExtension(file);
            docs.Add(new DocInfo(Path.GetFileName(file), title, new FileInfo(file).Length));
        }
        return docs;
    }

    public string? Read(string name)
    {
        var path = Resolve(name);
        return path is null ? null : File.ReadAllText(path);
    }

    /// <summary>读取某个标题(## / ###)下的章节; 找不到标题时返回 null。</summary>
    public string? ReadSection(string name, string headingContains)
    {
        var path = Resolve(name);
        if (path is null) return null;
        var lines = File.ReadAllLines(path);
        var start = Array.FindIndex(lines, l => l.StartsWith("#", StringComparison.Ordinal) && l.Contains(headingContains, StringComparison.OrdinalIgnoreCase));
        if (start < 0) return null;
        var level = lines[start].TakeWhile(c => c == '#').Count();
        var end = start + 1;
        while (end < lines.Length)
        {
            var l = lines[end];
            if (l.StartsWith("#", StringComparison.Ordinal) && l.TakeWhile(c => c == '#').Count() <= level) break;
            end++;
        }
        return string.Join('\n', lines[start..end]);
    }

    public IReadOnlyList<DocHit> Search(string query, int maxHits = 60)
    {
        var hits = new List<DocHit>();
        if (string.IsNullOrWhiteSpace(query) || !Directory.Exists(_dir)) return hits;
        foreach (var file in Directory.EnumerateFiles(_dir, "*.md").OrderBy(f => f, StringComparer.OrdinalIgnoreCase))
        {
            var lineNo = 0;
            foreach (var line in File.ReadLines(file))
            {
                lineNo++;
                if (line.Contains(query, StringComparison.OrdinalIgnoreCase))
                {
                    hits.Add(new DocHit(Path.GetFileName(file), lineNo, line.Trim()));
                    if (hits.Count >= maxHits) return hits;
                }
            }
        }
        return hits;
    }

    private string? Resolve(string name)
    {
        if (string.IsNullOrWhiteSpace(name)) return null;
        var fileName = Path.GetFileName(name);
        if (!fileName.EndsWith(".md", StringComparison.OrdinalIgnoreCase)) fileName += ".md";
        var path = Path.Combine(_dir, fileName);
        return File.Exists(path) ? path : null;
    }
}
