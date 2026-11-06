//! port_cli.py 事件流里的一行(JSON Lines)。

use serde_json::{Map, Value};

pub const START: &str = "start";
pub const PACK_START: &str = "pack_start";
pub const LOG: &str = "log";
pub const MOLANG: &str = "molang";
pub const PACK_DONE: &str = "pack_done";
pub const COLLECTION: &str = "collection";
pub const VALIDATE_ITEM: &str = "validate_item";
pub const VALIDATE_DONE: &str = "validate_done";
pub const DONE: &str = "done";
/// `--discover` 发现的一个 Java 包。
pub const PACK: &str = "pack";

/// 事件里有名字的字段; 其余字段原样留在 [`KernelEvent::extra`]。
const KNOWN_KEYS: &[&str] = &[
    "event",
    "pack",
    "level",
    "text",
    "kind",
    "label",
    "error",
    "javaDir",
    "collection",
    "dir",
    "path",
    "action",
    "count",
    "index",
    "total",
    "errors",
    "warnings",
    "notices",
    "packs",
    "packsOk",
    "packsFailed",
    "validationErrors",
    "animations",
    "controllers",
    "attention",
    "ok",
    "written",
    "seconds",
];

/// 字段按事件种类选填(port_cli 的约定), 未知字段进 `extra`。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct KernelEvent {
    pub event: String,
    /// 包名(JSON 键 `pack`)。
    pub pack: Option<String>,
    pub level: Option<String>,
    pub text: Option<String>,
    pub kind: Option<String>,
    pub label: Option<String>,
    pub error: Option<String>,
    pub java_dir: Option<String>,
    /// 合集目录名(JSON 键 `collection`)。
    pub collection: Option<String>,
    pub dir: Option<String>,
    pub path: Option<String>,
    pub action: Option<String>,

    pub count: Option<i64>,
    pub index: Option<i64>,
    pub total: Option<i64>,
    pub errors: Option<i64>,
    pub warnings: Option<i64>,
    pub notices: Option<i64>,
    pub packs: Option<i64>,
    pub packs_ok: Option<i64>,
    pub packs_failed: Option<i64>,
    pub validation_errors: Option<i64>,
    pub animations: Option<i64>,
    pub controllers: Option<i64>,

    pub attention: Option<bool>,
    pub ok: Option<bool>,
    pub written: Option<bool>,
    pub seconds: Option<f64>,

    pub extra: Map<String, Value>,
}

impl KernelEvent {
    pub fn new(event: &str) -> Self {
        KernelEvent {
            event: event.to_string(),
            ..Default::default()
        }
    }

    /// 一行内核输出 → 事件; 不是 JSON 对象的行(内核打到 stdout 的杂讯)返回 None。
    pub fn try_parse(line: &str) -> Option<KernelEvent> {
        if line.trim().is_empty() || !line.starts_with('{') {
            return None;
        }
        match serde_json::from_str::<Value>(line) {
            Ok(Value::Object(map)) => Some(Self::from_map(map)),
            _ => None,
        }
    }

    pub fn from_map(map: Map<String, Value>) -> Self {
        let s = |k: &str| string_field(&map, k);
        let n = |k: &str| int_field(&map, k);
        let b = |k: &str| map.get(k).and_then(Value::as_bool);
        let mut event = KernelEvent {
            event: s("event").unwrap_or_default(),
            pack: s("pack"),
            level: s("level"),
            text: s("text"),
            kind: s("kind"),
            label: s("label"),
            error: s("error"),
            java_dir: s("javaDir"),
            collection: s("collection"),
            dir: s("dir"),
            path: s("path"),
            action: s("action"),
            count: n("count"),
            index: n("index"),
            total: n("total"),
            errors: n("errors"),
            warnings: n("warnings"),
            notices: n("notices"),
            packs: n("packs"),
            packs_ok: n("packsOk"),
            packs_failed: n("packsFailed"),
            validation_errors: n("validationErrors"),
            animations: n("animations"),
            controllers: n("controllers"),
            attention: b("attention"),
            ok: b("ok"),
            written: b("written"),
            seconds: map.get("seconds").and_then(Value::as_f64),
            extra: Map::new(),
        };
        for (key, value) in map {
            if !KNOWN_KEYS.contains(&key.as_str()) {
                event.extra.insert(key, value);
            }
        }
        event
    }

    /// 额外字段的文本形态: 字符串原样, 其他值用 JSON 文本(按内核原文的 ", " / ": " 分隔); 缺失或 null 时 None。
    pub fn extra_string(&self, key: &str) -> Option<String> {
        match self.extra.get(key)? {
            Value::Null => None,
            Value::String(s) => Some(s.clone()),
            other => Some(python_style_json(other)),
        }
    }

    pub fn is(&self, kind: &str) -> bool {
        self.event == kind
    }
}

/// 与 Python `json.dumps` 缺省分隔符相同的单行 JSON(`{"a": 1, "b": [1, 2]}`)。
pub fn python_style_json(value: &Value) -> String {
    fn write(value: &Value, out: &mut String) {
        match value {
            Value::Object(map) => {
                out.push('{');
                for (i, (key, item)) in map.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    out.push_str(&Value::String(key.clone()).to_string());
                    out.push_str(": ");
                    write(item, out);
                }
                out.push('}');
            }
            Value::Array(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    write(item, out);
                }
                out.push(']');
            }
            other => out.push_str(&other.to_string()),
        }
    }
    let mut out = String::new();
    write(value, &mut out);
    out
}

fn string_field(map: &Map<String, Value>, key: &str) -> Option<String> {
    map.get(key).and_then(Value::as_str).map(str::to_string)
}

fn int_field(map: &Map<String, Value>, key: &str) -> Option<i64> {
    let value = map.get(key)?;
    value.as_i64().or_else(|| value.as_f64().map(|f| f as i64))
}

impl std::fmt::Display for KernelEvent {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let pack = self.pack.as_deref().unwrap_or("");
        match self.event.as_str() {
            LOG => write!(
                f,
                "[{pack}] {}: {}",
                self.level.as_deref().unwrap_or(""),
                self.text.as_deref().unwrap_or("")
            ),
            MOLANG => write!(
                f,
                "[{pack}] molang/{}: {} x{}",
                self.kind.as_deref().unwrap_or(""),
                self.label.as_deref().unwrap_or(""),
                self.count.map(|c| c.to_string()).unwrap_or_default()
            ),
            PACK_DONE => write!(
                f,
                "[{pack}] done ok={} {}s",
                self.ok.map(|b| b.to_string()).unwrap_or_default(),
                self.seconds.map(|s| s.to_string()).unwrap_or_default()
            ),
            other => f.write_str(other),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_log_event() {
        let e =
            KernelEvent::try_parse(r#"{"event": "log", "pack": "demo", "level": "warn", "text": "[WARN] 几何缺失"}"#)
                .unwrap();
        assert_eq!(e.event, LOG);
        assert_eq!(e.pack.as_deref(), Some("demo"));
        assert_eq!(e.level.as_deref(), Some("warn"));
        assert_eq!(e.text.as_deref(), Some("[WARN] 几何缺失"));
    }

    #[test]
    fn parses_start_event_layout_into_extra() {
        let e = KernelEvent::try_parse(
            r#"{"event": "start", "action": "convert", "packs": 2, "layout": {"refRp": "X", "baselinePresent": true}, "kernel": {"version": "1.0", "python": "2.7.18"}}"#,
        )
        .unwrap();
        assert_eq!(e.packs, Some(2));
        assert!(e.extra.contains_key("layout"));
        assert!(!e.extra.contains_key("packs"));
    }

    #[test]
    fn rejects_non_json_lines() {
        assert!(KernelEvent::try_parse("plain text").is_none());
        assert!(KernelEvent::try_parse("{not json").is_none());
        assert!(KernelEvent::try_parse("").is_none());
        assert!(KernelEvent::try_parse(" {\"event\": \"log\"}").is_none());
    }

    #[test]
    fn extra_string_handles_null_and_values() {
        let e = KernelEvent::try_parse(r#"{"event": "collection", "cover": null, "warning": "w", "n": 3}"#).unwrap();
        assert_eq!(e.extra_string("cover"), None);
        assert_eq!(e.extra_string("warning").as_deref(), Some("w"));
        assert_eq!(e.extra_string("n").as_deref(), Some("3"));
        assert_eq!(e.extra_string("missing"), None);
        let k = KernelEvent::try_parse(
            r#"{"event": "start", "kernel": {"python": "2.7.18", "pinyin": true, "v": [1, {"a": null}]}}"#,
        )
        .unwrap();
        assert_eq!(
            k.extra_string("kernel").as_deref(),
            Some(r#"{"python": "2.7.18", "pinyin": true, "v": [1, {"a": null}]}"#)
        );
    }
}
