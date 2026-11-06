//! molang 留痕在提醒面板 / 日志 / 文本报告里怎么摆: 分级(notice = 要看, info = 知道一下即可)与
//! "动画名转小写"按包合并(官方包一个就能有几十条, 逐条列会把置零/退化项淹没)。
//! 类别与要不要提醒由内核宿主 port_cli.SplitMolangLabel 给出(kind / attention), 这里只决定呈现。

use std::collections::HashMap;

use crate::event::KernelEvent;
use crate::report::MolangItem;

const LOWER_NAMES_SHOWN: usize = 4;

/// 一行要展示的 molang 提醒(小写改名已按包合并)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MolangRow {
    pub kind: String,
    pub text: String,
    pub count: i64,
    pub severity: &'static str,
}

/// 中性常量与动画名转小写只需知道一下(info); 置零/退化/未知函数要看(notice)。
pub fn severity(kind: Option<&str>) -> &'static str {
    match kind {
        Some("const") | Some("lower") => "info",
        _ => "notice",
    }
}

pub fn marker(kind: Option<&str>) -> &'static str {
    if severity(kind) == "info" { "[i]" } else { "[!]" }
}

/// 一个包里要提醒的 molang 留痕 → 展示行: 小写改名合并成一行; notice 在前, info 在后(同级保持内核顺序)。
pub fn rows(items: &[MolangItem]) -> Vec<MolangRow> {
    let attention: Vec<&MolangItem> = items.iter().filter(|m| m.attention).collect();
    let mut rows: Vec<MolangRow> = attention
        .iter()
        .filter(|m| m.kind != "lower")
        .map(|m| MolangRow {
            kind: m.kind.clone(),
            text: m.label.clone(),
            count: m.count,
            severity: severity(Some(&m.kind)),
        })
        .collect();
    let lower: Vec<&str> = attention
        .iter()
        .filter(|m| m.kind == "lower")
        .map(|m| m.label.as_str())
        .collect();
    if !lower.is_empty() {
        rows.push(MolangRow {
            kind: "lower".into(),
            text: lower_summary(&lower),
            count: 1,
            severity: "info",
        });
    }
    rows.sort_by_key(|r| if r.severity == "info" { 1 } else { 0 });
    rows
}

/// "Emotions_1 -> emotions_1" 若干条 → "N 个动画名改成小写: a→a, b→b …"。
pub fn lower_summary(labels: &[&str]) -> String {
    let head: Vec<String> = labels
        .iter()
        .take(LOWER_NAMES_SHOWN)
        .map(|l| l.replace(" -> ", "→"))
        .collect();
    format!(
        "{} 个动画名改成小写: {}{}",
        labels.len(),
        head.join(", "),
        if labels.len() > LOWER_NAMES_SHOWN { " …" } else { "" }
    )
}

/// 流式展示(CLI / 界面日志)用的按包缓冲: 小写改名先攒着, 包完成时 [`StreamBuffer::flush`] 出一行汇总;
/// 其余要提醒的事件立即返回展示行。调用方负责串行化。
#[derive(Debug, Default)]
pub struct StreamBuffer {
    lower: HashMap<String, Vec<String>>,
}

impl StreamBuffer {
    pub fn new() -> Self {
        Self::default()
    }

    /// 一个 molang 事件 → 立即展示的一行; 不提醒或被攒起来时返回 None。
    pub fn line(&mut self, e: &KernelEvent) -> Option<String> {
        if e.attention != Some(true) {
            return None;
        }
        if e.kind.as_deref() == Some("lower") {
            let key = e.pack.clone().unwrap_or_default();
            self.lower
                .entry(key)
                .or_default()
                .push(e.label.clone().unwrap_or_default());
            return None;
        }
        Some(format!(
            "{} molang/{}: {} x{}",
            marker(e.kind.as_deref()),
            e.kind.as_deref().unwrap_or(""),
            e.label.as_deref().unwrap_or(""),
            e.count.map(|c| c.to_string()).unwrap_or_default()
        ))
    }

    /// 包完成: 攒着的小写改名 → 一行汇总(没有则 None)。
    pub fn flush(&mut self, pack: Option<&str>) -> Option<String> {
        let list = self.lower.remove(pack.unwrap_or(""))?;
        if list.is_empty() {
            return None;
        }
        let labels: Vec<&str> = list.iter().map(String::as_str).collect();
        Some(format!("[i] molang/lower: {}", lower_summary(&labels)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event;

    fn ev(pack: &str, kind: &str, label: &str, attention: bool) -> KernelEvent {
        KernelEvent {
            event: event::MOLANG.into(),
            pack: Some(pack.into()),
            kind: Some(kind.into()),
            label: Some(label.into()),
            count: Some(2),
            attention: Some(attention),
            ..Default::default()
        }
    }

    #[test]
    fn stream_buffer_holds_lowercase_renames_until_pack_done() {
        let mut buffer = StreamBuffer::new();
        assert_eq!(
            buffer.line(&ev("a", "zero", "fn.x(y)", true)).as_deref(),
            Some("[!] molang/zero: fn.x(y) x2")
        );
        assert_eq!(
            buffer.line(&ev("a", "const", "is_maid -> 0.0", true)).as_deref(),
            Some("[i] molang/const: is_maid -> 0.0 x2")
        );
        assert_eq!(buffer.line(&ev("a", "map", "head_yaw -> x", false)), None);
        assert_eq!(buffer.line(&ev("a", "lower", "A -> a", true)), None);
        assert_eq!(buffer.line(&ev("b", "lower", "B -> b", true)), None);
        assert_eq!(
            buffer.flush(Some("a")).as_deref(),
            Some("[i] molang/lower: 1 个动画名改成小写: A→a")
        );
        assert_eq!(buffer.flush(Some("a")), None);
        assert_eq!(
            buffer.flush(Some("b")).as_deref(),
            Some("[i] molang/lower: 1 个动画名改成小写: B→b")
        );
    }

    #[test]
    fn lower_summary_truncates() {
        let labels = ["A -> a", "B -> b", "C -> c", "D -> d", "E -> e"];
        assert_eq!(lower_summary(&labels), "5 个动画名改成小写: A→a, B→b, C→c, D→d …");
    }
}
