//! 随内核同步过来的 YSM 移植文档(core/docs/*.md): 列表、读取、按关键字检索。供 MCP 的 ysm_docs 工具用。

use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DocInfo {
    pub name: String,
    pub title: String,
    pub size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DocHit {
    pub name: String,
    pub line: usize,
    pub text: String,
}

pub struct DocsLibrary {
    dir: PathBuf,
}

impl DocsLibrary {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        DocsLibrary { dir: dir.into() }
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    fn files(&self) -> Vec<PathBuf> {
        let mut files: Vec<PathBuf> = fs::read_dir(&self.dir)
            .map(|it| {
                it.filter_map(|e| e.ok())
                    .map(|e| e.path())
                    .filter(|p| p.is_file() && p.extension().is_some_and(|x| x.eq_ignore_ascii_case("md")))
                    .collect()
            })
            .unwrap_or_default();
        files.sort_by_key(|p| file_name(p).to_lowercase());
        files
    }

    pub fn list(&self) -> Vec<DocInfo> {
        self.files()
            .into_iter()
            .map(|file| {
                let text = read_text(&file).unwrap_or_default();
                let title = text
                    .lines()
                    .find_map(|l| l.strip_prefix("# ").map(|t| t.trim().to_string()))
                    .unwrap_or_else(|| {
                        file.file_stem()
                            .map(|s| s.to_string_lossy().into_owned())
                            .unwrap_or_default()
                    });
                let size = fs::metadata(&file).map(|m| m.len()).unwrap_or(0);
                DocInfo {
                    name: file_name(&file),
                    title,
                    size,
                }
            })
            .collect()
    }

    pub fn read(&self, name: &str) -> Option<String> {
        read_text(&self.resolve(name)?)
    }

    /// 读取某个标题(## / ###)下的章节; 找不到标题时返回 None。
    pub fn read_section(&self, name: &str, heading_contains: &str) -> Option<String> {
        let text = self.read(name)?;
        let lines: Vec<&str> = text.lines().collect();
        let needle = heading_contains.to_lowercase();
        let start = lines
            .iter()
            .position(|l| l.starts_with('#') && l.to_lowercase().contains(&needle))?;
        let level = heading_level(lines[start]);
        let mut end = start + 1;
        while end < lines.len() {
            let l = lines[end];
            if l.starts_with('#') && heading_level(l) <= level {
                break;
            }
            end += 1;
        }
        Some(lines[start..end].join("\n"))
    }

    pub fn search(&self, query: &str, max_hits: usize) -> Vec<DocHit> {
        let mut hits = Vec::new();
        if query.trim().is_empty() {
            return hits;
        }
        let needle = query.to_lowercase();
        for file in self.files() {
            let Some(text) = read_text(&file) else { continue };
            for (index, line) in text.lines().enumerate() {
                if line.to_lowercase().contains(&needle) {
                    hits.push(DocHit {
                        name: file_name(&file),
                        line: index + 1,
                        text: line.trim().to_string(),
                    });
                    if hits.len() >= max_hits {
                        return hits;
                    }
                }
            }
        }
        hits
    }

    fn resolve(&self, name: &str) -> Option<PathBuf> {
        if name.trim().is_empty() {
            return None;
        }
        let mut file = Path::new(name).file_name()?.to_string_lossy().into_owned();
        if !file.to_ascii_lowercase().ends_with(".md") {
            file.push_str(".md");
        }
        let path = self.dir.join(file);
        path.is_file().then_some(path)
    }
}

fn heading_level(line: &str) -> usize {
    line.chars().take_while(|&c| c == '#').count()
}

fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn read_text(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    let text = String::from_utf8_lossy(&bytes).into_owned();
    Some(text.strip_prefix('\u{feff}').map(str::to_string).unwrap_or(text))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lists_reads_sections_and_searches() {
        let dir = std::env::temp_dir().join(format!("ysmconv-docs-{}", crate::win::uuid_v4()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("b.md"),
            "# 标题 B\n\n## 一节\n内容 Alpha\n### 小节\n细节\n## 二节\n别的\n",
        )
        .unwrap();
        fs::write(dir.join("a.md"), "没有标题\nalpha 行\n").unwrap();
        fs::write(dir.join("c.txt"), "不是文档").unwrap();
        let docs = DocsLibrary::new(&dir);
        let list = docs.list();
        assert_eq!(
            list.iter().map(|d| d.name.as_str()).collect::<Vec<_>>(),
            vec!["a.md", "b.md"]
        );
        assert_eq!(list[0].title, "a");
        assert_eq!(list[1].title, "标题 B");
        assert!(docs.read("b").unwrap().contains("内容"));
        assert!(docs.read("missing.md").is_none());
        assert_eq!(
            docs.read_section("b.md", "一节").unwrap(),
            "## 一节\n内容 Alpha\n### 小节\n细节"
        );
        assert!(docs.read_section("b.md", "无此节").is_none());
        let hits = docs.search("ALPHA", 60);
        assert_eq!(hits.len(), 2);
        assert_eq!((hits[0].name.as_str(), hits[0].line), ("a.md", 2));
        assert_eq!(docs.search("alpha", 1).len(), 1);
        let _ = fs::remove_dir_all(&dir);
    }
}
