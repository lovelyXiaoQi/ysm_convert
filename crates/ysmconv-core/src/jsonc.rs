//! 宽松 JSON: 容忍 UTF-8 BOM、`//` 与 `/* */` 注释、尾逗号(基岩 manifest.json 里常见), 其余按标准 JSON 解析。

use serde_json::Value;

pub fn parse(text: &str) -> Result<Value, String> {
    let cleaned = strip_trailing_commas(&strip_comments(text.strip_prefix('\u{feff}').unwrap_or(text)));
    serde_json::from_str(&cleaned).map_err(|e| e.to_string())
}

fn strip_comments(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    let mut in_string = false;
    while i < chars.len() {
        let c = chars[i];
        if in_string {
            out.push(c);
            if c == '\\' && i + 1 < chars.len() {
                out.push(chars[i + 1]);
                i += 2;
                continue;
            }
            if c == '"' {
                in_string = false;
            }
            i += 1;
            continue;
        }
        let next = chars.get(i + 1).copied();
        match (c, next) {
            ('"', _) => {
                in_string = true;
                out.push(c);
                i += 1;
            }
            ('/', Some('/')) => {
                while i < chars.len() && chars[i] != '\n' {
                    i += 1;
                }
            }
            ('/', Some('*')) => {
                i += 2;
                while i < chars.len() && !(chars[i] == '*' && chars.get(i + 1) == Some(&'/')) {
                    i += 1;
                }
                i = (i + 2).min(chars.len());
                out.push(' ');
            }
            _ => {
                out.push(c);
                i += 1;
            }
        }
    }
    out
}

fn strip_trailing_commas(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut in_string = false;
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if in_string {
            out.push(c);
            if c == '\\' && i + 1 < chars.len() {
                out.push(chars[i + 1]);
                i += 2;
                continue;
            }
            if c == '"' {
                in_string = false;
            }
            i += 1;
            continue;
        }
        if c == '"' {
            in_string = true;
        } else if c == ',' {
            let mut j = i + 1;
            while j < chars.len() && chars[j].is_whitespace() {
                j += 1;
            }
            if matches!(chars.get(j), Some('}') | Some(']')) {
                i += 1;
                continue;
            }
        }
        out.push(c);
        i += 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tolerates_bom_comments_and_trailing_commas() {
        let text = "\u{feff}{\n  // 注释\n  \"a\": \"x // 不是注释\", /* 块 */\n  \"b\": [1, 2,],\n}";
        let v = parse(text).unwrap();
        assert_eq!(v["a"], "x // 不是注释");
        assert_eq!(v["b"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn keeps_escaped_quotes() {
        let v = parse(r#"{"a": "q\"/*x*/,]"}"#).unwrap();
        assert_eq!(v["a"], "q\"/*x*/,]");
    }

    #[test]
    fn rejects_garbage() {
        assert!(parse("{not json").is_err());
    }
}
