//! 极简参数解析: 位置参数 + `--key value` / `--key=value` / `--flag`。

use ysmconv_core::{Error, Result};

#[derive(Debug, Default)]
pub struct ParsedArgs {
    pub positional: Vec<String>,
    options: Vec<(String, String)>,
}

impl ParsedArgs {
    /// `flags` 是不带值的开关(出现即为真); 其余 `--key` 都要一个值。键名不分大小写。
    pub fn parse(args: &[String], flags: &[&str]) -> Result<ParsedArgs> {
        let mut parsed = ParsedArgs::default();
        let mut i = 0;
        while i < args.len() {
            let a = &args[i];
            if let Some(body) = a.strip_prefix("--").filter(|b| !b.is_empty()) {
                if let Some(eq) = body.find('=').filter(|&eq| eq > 0) {
                    parsed.add(&body[..eq], &body[eq + 1..]);
                } else if flags.iter().any(|f| f.eq_ignore_ascii_case(body)) {
                    parsed.add(body, "true");
                } else if i + 1 >= args.len() {
                    return Err(Error::Argument(format!("--{body} 需要一个值")));
                } else {
                    i += 1;
                    parsed.add(body, &args[i]);
                }
            } else {
                parsed.positional.push(a.clone());
            }
            i += 1;
        }
        Ok(parsed)
    }

    fn add(&mut self, key: &str, value: &str) {
        self.options.push((key.to_ascii_lowercase(), value.to_string()));
    }

    pub fn has(&self, key: &str) -> bool {
        self.options.iter().any(|(k, _)| k == key)
    }

    /// 同一个键写多次时取最后一次。
    pub fn get(&self, key: &str) -> Option<&str> {
        self.options
            .iter()
            .rev()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    }

    pub fn get_all(&self, key: &str) -> Vec<&str> {
        self.options
            .iter()
            .filter(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
            .collect()
    }

    pub fn require(&self, key: &str) -> Result<&str> {
        self.get(key).ok_or_else(|| Error::Argument(format!("缺少 --{key}")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn parses_flags_values_and_positionals() {
        let a = ParsedArgs::parse(
            &args(&[
                "D:\\in",
                "--out",
                "D:\\out",
                "--json",
                "--rename=a=b",
                "--Rename",
                "c=d",
                "more",
            ]),
            &["json"],
        )
        .unwrap();
        assert_eq!(a.positional, vec!["D:\\in", "more"]);
        assert_eq!(a.get("out"), Some("D:\\out"));
        assert!(a.has("json"));
        assert_eq!(a.get_all("rename"), vec!["a=b", "c=d"]);
        assert_eq!(a.get("rename"), Some("c=d"));
        assert!(a.require("component").is_err());
    }

    #[test]
    fn missing_value_is_an_error() {
        assert!(ParsedArgs::parse(&args(&["--out"]), &[]).is_err());
        // 单独的 "--" 当位置参数
        assert_eq!(ParsedArgs::parse(&args(&["--"]), &[]).unwrap().positional, vec!["--"]);
    }
}
