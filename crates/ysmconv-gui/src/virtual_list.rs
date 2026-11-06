//! 行高不等(文字折行)的长列表: 只画可见的行。行高按宽度缓存, 宽度或内容版本变了才重算, 新增的行只量新的。
//! 日志一次转换能有上万行, 逐帧把每一行都排版一遍会卡。

use eframe::egui::{self, Rect, Ui, vec2};

#[derive(Default)]
pub struct VirtualList {
    version: u64,
    width: f32,
    heights: Vec<f32>,
    /// offsets[i] = 第 i 行顶边; 长度 = 行数 + 1。
    offsets: Vec<f32>,
}

impl VirtualList {
    /// `version` 变了(清空 / 过滤条件变了)整表重量; 只追加时版本不变, 只量新行。
    #[allow(clippy::too_many_arguments)]
    pub fn show(
        &mut self,
        ui: &mut Ui,
        id_salt: &str,
        count: usize,
        version: u64,
        stick_to_bottom: bool,
        mut measure: impl FnMut(&Ui, usize, f32) -> f32,
        mut paint: impl FnMut(&mut Ui, usize, Rect),
    ) {
        egui::ScrollArea::vertical()
            .id_salt(id_salt)
            .auto_shrink([false, false])
            .stick_to_bottom(stick_to_bottom)
            .show_viewport(ui, |ui, viewport| {
                let width = ui.available_width().max(1.0);
                if (width - self.width).abs() > 0.5 || version != self.version || count < self.heights.len() {
                    self.width = width;
                    self.version = version;
                    self.heights.clear();
                    self.offsets.clear();
                }
                if self.offsets.is_empty() {
                    self.offsets.push(0.0);
                }
                while self.heights.len() < count {
                    let height = measure(ui, self.heights.len(), width).max(1.0);
                    let top = *self.offsets.last().unwrap_or(&0.0);
                    self.heights.push(height);
                    self.offsets.push(top + height);
                }
                let total = *self.offsets.last().unwrap_or(&0.0);
                let origin = ui.max_rect().min;
                ui.set_min_height(total);
                ui.set_min_width(width);
                // 第一条底边越过视口顶边的行
                let first = self
                    .offsets
                    .partition_point(|&top| top <= viewport.min.y)
                    .saturating_sub(1);
                let mut i = first;
                while i < count && self.offsets[i] < viewport.max.y {
                    let rect = Rect::from_min_size(origin + vec2(0.0, self.offsets[i]), vec2(width, self.heights[i]));
                    paint(ui, i, rect);
                    i += 1;
                }
            });
    }
}
