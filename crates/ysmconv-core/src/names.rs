//! 包名 = ysm_models/ 下的目录名 = 模型 ID(ysm_pack:<包名>)与全部资源 ID(geometry./animation./controller.animation.)的词根。
//! 基岩资源 ID 只认 [a-z0-9_.-], 含其他字符整份文件拒载; 这里进一步收紧到 [a-z0-9_](点与连字符留给 ID 分段)。

use std::path::Path;

use crate::models::PackSpec;
use crate::output;

pub const MAX_LENGTH: usize = 64;

fn is_name_char(c: char) -> bool {
    c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_'
}

/// `^[a-z0-9][a-z0-9_]*$`
fn matches_pattern(name: &str) -> bool {
    let mut chars = name.chars();
    match chars.next() {
        Some(first) if first.is_ascii_lowercase() || first.is_ascii_digit() => chars.all(is_name_char),
        _ => false,
    }
}

pub fn is_valid(name: &str) -> bool {
    !name.is_empty() && name.chars().count() <= MAX_LENGTH && matches_pattern(name)
}

/// ASCII 化 + 小写 + 非法段换下划线(拼音化由内核 --discover 的 suggestedName 负责, 这里只兜底)。
pub fn sanitize(text: &str) -> String {
    let mut ascii = String::with_capacity(text.len());
    for ch in text.chars().flat_map(char::to_lowercase) {
        ascii.push(if ch.is_ascii() { ch } else { '_' });
    }
    // [^a-z0-9_]+ → "_"
    let mut slug = String::with_capacity(ascii.len());
    let mut in_run = false;
    for ch in ascii.chars() {
        if is_name_char(ch) {
            slug.push(ch);
            in_run = false;
        } else if !in_run {
            slug.push('_');
            in_run = true;
        }
    }
    let mut slug = slug.trim_matches('_').to_string();
    while slug.contains("__") {
        slug = slug.replace("__", "_");
    }
    if slug.is_empty() { "pack".to_string() } else { slug }
}

pub fn problem(name: &str) -> Option<String> {
    if name.trim().is_empty() {
        return Some("包名为空".into());
    }
    if name.chars().count() > MAX_LENGTH {
        return Some(format!("包名超过 {MAX_LENGTH} 字符"));
    }
    if !matches_pattern(name) {
        return Some("包名只能用小写英文、数字、下划线, 且不能以下划线开头".into());
    }
    None
}

/// 整批校验: 单个包名合法性、重名、Java 目录存在、合集目录名合法。返回问题列表(空 = 通过)。
pub fn validate(packs: &[PackSpec]) -> Vec<String> {
    let mut problems = Vec::new();
    let mut seen: Vec<(&str, &str)> = Vec::new();
    for p in packs {
        if let Some(problem) = problem(&p.name) {
            problems.push(format!("{}: {problem}(当前 \"{}\")", p.java_dir, p.name));
        }
        if let Some((_, other)) = seen.iter().find(|(name, _)| *name == p.name) {
            problems.push(format!("包名重复: {}({other} 与 {})", p.name, p.java_dir));
        } else {
            seen.push((&p.name, &p.java_dir));
        }
        if !Path::new(&p.java_dir).join("ysm.json").is_file() {
            problems.push(format!("不是 Java 模型包目录(缺 ysm.json): {}", p.java_dir));
        }
        if let Some(collection) = &p.collection
            && !output::is_valid_component_name(collection)
        {
            problems.push(format!("合集目录名只能用英文、数字、下划线与连字符: {collection}"));
        }
    }
    problems
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_names() {
        for (name, valid) in [
            ("wine_fox_01", true),
            ("01_maid", true),
            ("Wine", false),
            ("_x", false),
            ("a-b", false),
            ("", false),
            ("中文", false),
        ] {
            assert_eq!(is_valid(name), valid, "{name}");
        }
        assert!(!is_valid(&"a".repeat(65)));
        assert!(is_valid(&"a".repeat(64)));
    }

    #[test]
    fn sanitize_produces_valid_names() {
        assert_eq!(sanitize("Wine Fox (01)"), "wine_fox_01");
        assert_eq!(sanitize("酒狐"), "pack");
        assert!(is_valid(&sanitize("--Weird__Name--")));
        assert_eq!(sanitize("a__b"), "a_b");
    }

    #[test]
    fn validate_reports_duplicates_and_missing_dirs() {
        let tmp = std::env::temp_dir();
        let pack = |dir: &str, name: &str, collection: Option<&str>| PackSpec {
            java_dir: tmp.join(dir).to_string_lossy().into_owned(),
            name: name.into(),
            collection: collection.map(str::to_string),
            material: None,
            cull: None,
        };
        let packs = vec![
            pack("nope1", "a", None),
            pack("nope2", "a", None),
            pack("nope3", "Bad Name", Some("合集")),
        ];
        let problems = validate(&packs);
        assert!(problems.iter().any(|p| p.contains("重复")));
        assert!(problems.iter().any(|p| p.contains("缺 ysm.json")));
        assert!(problems.iter().any(|p| p.contains("小写英文")));
        assert!(problems.iter().any(|p| p.contains("合集目录名")));
    }
}
