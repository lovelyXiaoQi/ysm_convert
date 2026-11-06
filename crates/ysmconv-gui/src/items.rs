//! 任务列表与日志里的条目。

use ysmconv_core::time::LocalTime;
use ysmconv_core::{DiscoveredPack, PackSpec, material, names};

/// 任务列表里的一个 Java 包。
pub struct PackItem {
    pub source: DiscoveredPack,
    pub java_dir: String,
    pub folder: String,
    pub display_name: String,
    pub summary: String,
    pub enabled: bool,
    pub name: String,
    pub collection: String,
    /// 材质: "自动"、预设名、自定义材质名或 "键=材质, ..."(见 ysmconv_core::material)。
    pub material: String,
    /// 背面剔除: 自动 / 剔除 / 不剔除。
    pub cull_mode: String,
    pub status: String,
    pub attention_count: i64,
    pub ok: Option<bool>,
    pub selected: bool,
}

impl PackItem {
    pub fn new(pack: DiscoveredPack) -> Self {
        let folder = pack.folder_name();
        let summary = match &pack.error {
            Some(error) => error.clone(),
            None => {
                let mut s = format!(
                    "动画文件 {} · 手臂几何 {} · 弹射物 {} · 载具 {}",
                    pack.animation_files.unwrap_or(0),
                    if pack.has_arm == Some(true) { "有" } else { "无" },
                    pack.projectiles.unwrap_or(0),
                    pack.vehicles.unwrap_or(0)
                );
                if let Some(profile) = &pack.material_profile {
                    s.push_str(&format!(" · 材质 {}", material::describe_auto(Some(profile))));
                    if profile.negative_cubes > 0 {
                        s.push_str(&format!(" · 负尺寸方块 {}", profile.negative_cubes));
                    }
                }
                s
            }
        };
        let readable = pack.error.is_none();
        PackItem {
            java_dir: pack.java_dir.clone(),
            display_name: pack.display_name(),
            name: pack.suggested_name.clone().unwrap_or_else(|| names::sanitize(&folder)),
            collection: pack.collection_folder.clone().unwrap_or_default(),
            folder,
            summary,
            enabled: readable,
            material: material::AUTO.to_string(),
            cull_mode: material::AUTO.to_string(),
            status: if readable { "待转换" } else { "无法读取" }.to_string(),
            attention_count: 0,
            ok: None,
            selected: false,
            source: pack,
        }
    }

    pub fn collection_or_none(&self) -> Option<String> {
        let c = self.collection.trim();
        (!c.is_empty()).then(|| c.to_string())
    }

    pub fn material_or_none(&self) -> Option<String> {
        material::normalize(Some(&self.material))
    }

    pub fn cull_value(&self) -> Option<bool> {
        material::cull_from_text(&self.cull_mode)
    }

    pub fn to_spec(&self) -> PackSpec {
        PackSpec {
            java_dir: self.java_dir.clone(),
            name: self.name.trim().to_string(),
            collection: self.collection_or_none(),
            material: self.material_or_none(),
            cull: self.cull_value(),
        }
    }

    /// 材质 / 背面剔除两列的提示: 自动会选什么、写法有没有问题、负尺寸方块怎么显示。
    pub fn material_tip(&self) -> String {
        let mut lines = Vec::new();
        let profile = self.source.material_profile.as_ref();
        if let Some(problem) = material::problem(Some(&self.material)) {
            lines.push(format!("写法有问题: {problem}"));
        } else if material::normalize(Some(&self.material)).is_none() {
            lines.push(format!(
                "自动: {} —— Java properties.all_cutout 打开则剔除背面(负尺寸描边), 皮肤带 PBR 贴图用 pbr, 其余 cutout; 发光组(ysmGlow* 骨骼)用 emissive",
                material::describe_auto(profile)
            ));
        } else if material::is_custom(Some(&self.material)) {
            lines.push("自定义材质: 资源包的 materials/*.material 里要有它的定义(原版材质名也行), 不参与描边 / 泛光后处理, 背面剔除对它不起作用".into());
        }
        let cull = self.cull_value();
        lines.push(format!(
            "背面剔除: {}",
            match cull {
                None => format!(
                    "自动(Java all_cutout = {})",
                    if profile.is_some_and(|p| p.all_cutout) {
                        "true, 剔除"
                    } else {
                        "false, 不剔除"
                    }
                ),
                Some(true) => "剔除".into(),
                Some(false) => "不剔除".into(),
            }
        ));
        if let Some(hint) = material::negative_cube_hint(profile, cull) {
            lines.push(hint);
        }
        lines.join("\n")
    }
}

/// 日志面板的一行。level: error / warn / notice / info / detail(明细, 勾选"显示全部内核明细行"才显示)。
pub struct LogEntry {
    pub time: String,
    pub level: String,
    pub text: String,
}

impl LogEntry {
    pub fn new(level: &str, text: impl Into<String>) -> Self {
        LogEntry {
            time: LocalTime::now().format_clock(),
            level: level.to_string(),
            text: text.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ysmconv_core::MaterialProfile;

    #[test]
    fn builds_summary_and_tip() {
        let pack = DiscoveredPack {
            java_dir: r"C:\j\fox".into(),
            folder: Some("fox".into()),
            suggested_name: Some("wine_fox".into()),
            animation_files: Some(3),
            has_arm: Some(true),
            material_profile: Some(MaterialProfile {
                all_cutout: true,
                pbr_skins: 0,
                pbr_vehicles: 0,
                negative_cubes: 4,
            }),
            ..Default::default()
        };
        let mut item = PackItem::new(pack);
        assert_eq!(item.name, "wine_fox");
        assert!(item.enabled);
        assert_eq!(
            item.summary,
            "动画文件 3 · 手臂几何 有 · 弹射物 0 · 载具 0 · 材质 负尺寸描边·剔除背面 · 负尺寸方块 4"
        );
        assert!(item.material_tip().contains("true, 剔除"));
        item.material = "bad name".into();
        assert!(item.material_tip().starts_with("写法有问题"));
        item.material = " my_toon ".into();
        item.cull_mode = material::CULL_OFF.into();
        let spec = item.to_spec();
        assert_eq!(spec.material.as_deref(), Some("my_toon"));
        assert_eq!(spec.cull, Some(false));
        let broken = PackItem::new(DiscoveredPack {
            java_dir: "x".into(),
            error: Some("坏了".into()),
            ..Default::default()
        });
        assert!(!broken.enabled);
        assert_eq!(broken.status, "无法读取");
        assert_eq!(broken.summary, "坏了");
    }
}
