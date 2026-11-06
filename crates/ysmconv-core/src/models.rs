//! 任务单与发现结果的数据形态(字段名与 port_cli.py 的约定一一对应)。

use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::material;

/// 产物落点: 组件根目录下的行为包与资源包。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutputLayout {
    pub root: String,
    pub bp_dir: String,
    pub rp_dir: String,
}

impl OutputLayout {
    pub fn new(root: impl Into<String>, bp_dir: impl Into<String>, rp_dir: impl Into<String>) -> Self {
        OutputLayout {
            root: root.into(),
            bp_dir: bp_dir.into(),
            rp_dir: rp_dir.into(),
        }
    }

    pub fn bp_models(&self) -> String {
        join(&self.bp_dir, "ysm_models")
    }

    /// 资源包里已经有 java_default 基线(YSM 主组件工程形态)。
    pub fn has_baseline(&self) -> bool {
        Path::new(&self.rp_dir).join("animations").join("java_default").is_dir()
    }
}

/// 一个待移植的 Java 包。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PackSpec {
    pub java_dir: String,
    pub name: String,
    /// 合集目录名(ysm_models/<合集>/<包名>); None = 平铺。
    pub collection: Option<String>,
    /// 模型材质(写进产物 ysm.json 顶级 material): 预设名 / 材质名, 或 "键=材质, 键=材质" 逐键指定; None = 自动
    /// (YSM 主组件按 Java 包判定)。写法见 [`crate::material`]。
    pub material: Option<String>,
    /// 背面剔除(写进产物 ysm.json 顶级 cull): true 剔除 / false 不剔除; None = 照 Java properties.all_cutout。
    pub cull: Option<bool>,
}

/// 合集(模型选择界面里的文件夹)的设置 → ysm-pack.json。网易版文件夹只显示一个名字, 不分中英文;
/// 没填的字段沿用 Java 源旁那份清单(内核 port_cli._CollectionManifest 以它为底覆盖)。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CollectionSpec {
    pub dir: String,
    /// 文件夹显示名。
    pub name: Option<String>,
    pub description: Option<String>,
    /// 文件夹封面图(PNG, 不超过 1MB)。转换时拷进资源包 textures/ui/ysm_packs/<合集>.png 并写 folder_texture;
    /// 游戏里按 Java 卡片 52x90 的比例等比铺满, 图最好也是这个比例。不填就用 Java 合集自带的 ysm-pack.png(有的话)。
    pub cover_image: Option<String>,
}

impl CollectionSpec {
    /// 文件夹 PNG 封面允许的最大字节数(与 Java 版同一上限, 内核同样拒收更大的)。
    pub const MAX_COVER_BYTES: u64 = 1024 * 1024;

    pub fn is_empty(&self) -> bool {
        is_blank(&self.name) && is_blank(&self.description) && is_blank(&self.cover_image)
    }

    /// 封面图的问题(不存在 / 不是 PNG / 超过 1MB), 没问题或没填时为 None。
    pub fn cover_problem(&self) -> Option<String> {
        let cover = self.cover_image.as_deref().filter(|c| !c.trim().is_empty())?;
        let path = Path::new(cover);
        if !path.is_file() {
            return Some(format!("封面图不存在: {cover}"));
        }
        if !cover.to_ascii_lowercase().ends_with(".png") {
            return Some(format!("封面图必须是 PNG: {cover}"));
        }
        if std::fs::metadata(path).map(|m| m.len()).unwrap_or(0) > Self::MAX_COVER_BYTES {
            return Some(format!("封面图超过 1MB(Java 版同样拒收): {cover}"));
        }
        None
    }

    pub fn to_manifest(&self) -> Value {
        let mut obj = Map::new();
        if let Some(name) = non_blank(&self.name) {
            obj.insert("name".into(), json!(name.trim()));
        }
        if let Some(description) = non_blank(&self.description) {
            obj.insert("description".into(), json!(description.trim()));
        }
        if let Some(cover) = non_blank(&self.cover_image) {
            obj.insert("coverImage".into(), json!(full_path(cover)));
        }
        Value::Object(obj)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConvertOptions {
    /// 携带第三方模组联动动画(slashblade/parcool/...), 缺省不带; 枪械 tacz 动画缺省就带。
    pub with_mods: bool,
    /// 转换 Java 自定义函数(functions/*.molang: fn.* 内联、音效 / ysm.sync / 键鼠改写、事件体与脚本控制器编进逐帧执行体)。
    /// **实验性, 缺省关**: 关着时 fn.* 与音效调用置零、@player_init / @player_update / @sync 不转换。
    pub java_functions: bool,
    /// 转换后跑资源包红线体检。
    pub validate: bool,
    /// 产物 JSON 压成一行(缺省开)。磁盘约省 70%, 内核读写也快 2.5~3 倍(Python 2.7 只在不缩进时用 C 编码器);
    /// 关掉则按 2 空格缩进, 便于人读和 git diff。
    pub compact_json: bool,
    /// 同时转换的包数; 0 = 自动(见 [`crate::service::auto_parallelism`])。
    pub max_parallel: usize,
    /// 按包拆出的子任务不写合集清单, 由收尾任务 finalize 统一写。
    pub write_collections: bool,
}

impl Default for ConvertOptions {
    fn default() -> Self {
        ConvertOptions {
            with_mods: false,
            java_functions: false,
            validate: true,
            compact_json: true,
            max_parallel: 0,
            write_collections: true,
        }
    }
}

/// 交给 `port_cli.py --job` 的任务单。
#[derive(Debug, Clone)]
pub struct JobSpec {
    pub action: String,
    pub layout: OutputLayout,
    pub ref_rp: Option<String>,
    pub packs: Vec<PackSpec>,
    pub collections: Vec<CollectionSpec>,
    pub options: ConvertOptions,
}

impl JobSpec {
    pub fn new(action: &str, layout: OutputLayout) -> Self {
        JobSpec {
            action: action.to_string(),
            layout,
            ref_rp: None,
            packs: Vec::new(),
            collections: Vec::new(),
            options: ConvertOptions::default(),
        }
    }

    pub fn to_value(&self) -> Value {
        let mut layout = Map::new();
        layout.insert("root".into(), json!(self.layout.root));
        layout.insert("rp".into(), json!(self.layout.rp_dir));
        layout.insert("bpModels".into(), json!(self.layout.bp_models()));
        if let Some(ref_rp) = self.ref_rp.as_deref().filter(|r| !r.is_empty()) {
            layout.insert("refRp".into(), json!(ref_rp));
        }

        let packs: Vec<Value> = self
            .packs
            .iter()
            .map(|p| {
                let mut pack = Map::new();
                pack.insert("javaDir".into(), json!(p.java_dir));
                pack.insert("name".into(), json!(p.name));
                pack.insert("collection".into(), json!(p.collection));
                // 材质 / 背面剔除只在不是"自动"时写(内核 1.3 起认; 缺省时内核不改 ysm.json)
                if let Some(material) = material::normalize(p.material.as_deref()) {
                    pack.insert("material".into(), json!(material));
                }
                if let Some(cull) = p.cull {
                    pack.insert("cull".into(), json!(cull));
                }
                Value::Object(pack)
            })
            .collect();

        let mut collections = Map::new();
        for c in &self.collections {
            collections.insert(c.dir.clone(), c.to_manifest());
        }

        json!({
            "action": self.action,
            "layout": Value::Object(layout),
            "packs": packs,
            "collections": Value::Object(collections),
            "options": {
                "withMods": self.options.with_mods,
                "javaFunctions": self.options.java_functions,
                "validate": self.options.validate,
                "compactJson": self.options.compact_json,
                "writeCollections": self.options.write_collections,
            },
        })
    }

    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(&self.to_value()).unwrap_or_default()
    }
}

/// Java 包的材质画像(内核发现阶段 port_java_pack.DetectMaterialProfile 给出)。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct MaterialProfile {
    /// Java properties.all_cutout: 无论贴图情况全部剔除背面, 给负尺寸方块做描边外壳用。
    pub all_cutout: bool,
    /// 带 PBR 贴图(normal / specular)的皮肤数。
    pub pbr_skins: i64,
    pub pbr_vehicles: i64,
    /// 玩家几何(main / arm)里尺寸带负分量的方块数。官方模型也常见, Java 缺省不剔除它们, 只有 all_cutout 打开才成描边外壳。
    pub negative_cubes: i64,
}

/// `port_cli.py --discover` 发现的一个 Java 包。
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct DiscoveredPack {
    pub java_dir: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub folder: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub suggested_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub collection_dir: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub collection_folder: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub spec: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub has_arm: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub animation_files: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub projectiles: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vehicles: Option<i64>,
    /// 材质画像(all_cutout / PBR 皮肤数 / 负尺寸方块数); 旧内核或几何读不了时为 None。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub material_profile: Option<MaterialProfile>,
}

impl DiscoveredPack {
    /// 宽松解析一行 `pack` 事件: 字段类型对不上的按缺失处理, 不让一个怪字段吃掉整包。
    pub fn from_value(value: &Value) -> Option<DiscoveredPack> {
        if let Ok(pack) = serde_json::from_value::<DiscoveredPack>(value.clone()) {
            return Some(pack);
        }
        let map = value.as_object()?;
        let s = |k: &str| map.get(k).and_then(Value::as_str).map(str::to_string);
        let n = |k: &str| {
            map.get(k)
                .and_then(|v| v.as_i64().or_else(|| v.as_f64().map(|f| f as i64)))
        };
        Some(DiscoveredPack {
            java_dir: s("javaDir").unwrap_or_default(),
            folder: s("folder"),
            suggested_name: s("suggestedName"),
            collection_dir: s("collectionDir"),
            collection_folder: s("collectionFolder"),
            error: s("error"),
            spec: map.get("spec").filter(|v| !v.is_null()).cloned(),
            metadata: map.get("metadata").filter(|v| !v.is_null()).cloned(),
            has_arm: map.get("hasArm").and_then(Value::as_bool),
            animation_files: n("animationFiles"),
            projectiles: n("projectiles"),
            vehicles: n("vehicles"),
            material_profile: map
                .get("materialProfile")
                .and_then(|v| serde_json::from_value::<MaterialProfile>(v.clone()).ok()),
        })
    }

    pub fn folder_name(&self) -> String {
        self.folder.clone().unwrap_or_else(|| file_name(&self.java_dir))
    }

    pub fn display_name(&self) -> String {
        self.metadata_string("name").unwrap_or_else(|| self.folder_name())
    }

    pub fn author(&self) -> Option<String> {
        self.metadata_string("author")
    }

    pub fn metadata_string(&self, key: &str) -> Option<String> {
        let value = self.metadata.as_ref()?.as_object()?.get(key)?;
        match value {
            Value::String(s) => Some(s.clone()),
            Value::Array(items) => Some(items.iter().map(element_text).collect::<Vec<_>>().join(", ")),
            Value::Null => None,
            other => Some(element_text(other)),
        }
    }
}

fn element_text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        other => other.to_string(),
    }
}

/// `port_cli.py --version` 的回答。
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct KernelInfo {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kernel: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub python: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub port_tool: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pinyin: Option<bool>,
}

impl KernelInfo {
    pub fn from_value(value: &Value) -> Option<KernelInfo> {
        let map = value.as_object()?;
        let text = |k: &str| match map.get(k)? {
            Value::String(s) => Some(s.clone()),
            Value::Null => None,
            other => Some(other.to_string()),
        };
        Some(KernelInfo {
            kernel: text("kernel"),
            python: text("python"),
            port_tool: text("portTool"),
            pinyin: map.get("pinyin").and_then(Value::as_bool),
        })
    }

    pub fn to_value(&self) -> Value {
        serde_json::to_value(self).unwrap_or(Value::Null)
    }
}

pub(crate) fn join(base: &str, child: &str) -> String {
    Path::new(base).join(child).to_string_lossy().into_owned()
}

pub(crate) fn file_name(path: &str) -> String {
    Path::new(path)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// 绝对路径(相对路径按当前目录展开, 不要求存在, 不解析链接), 失败时原样返回。
pub fn full_path(path: &str) -> String {
    std::path::absolute(path)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| path.to_string())
}

fn is_blank(text: &Option<String>) -> bool {
    text.as_deref().is_none_or(|t| t.trim().is_empty())
}

fn non_blank(text: &Option<String>) -> Option<&str> {
    text.as_deref().filter(|t| !t.trim().is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn layout() -> OutputLayout {
        OutputLayout::new(r"C:\out", r"C:\out\x_bp", r"C:\out\x_rp")
    }

    #[test]
    fn serializes_kernel_contract() {
        let mut job = JobSpec::new("convert", layout());
        job.ref_rp = Some(r"C:\ref".into());
        job.packs.push(PackSpec {
            java_dir: r"C:\java\p".into(),
            name: "p".into(),
            collection: Some("c".into()),
            ..Default::default()
        });
        job.collections.push(CollectionSpec {
            dir: "c".into(),
            name: Some("酒狐".into()),
            cover_image: Some(r"C:\art\cover.png".into()),
            ..Default::default()
        });
        job.options = ConvertOptions {
            with_mods: true,
            validate: false,
            ..Default::default()
        };
        let node: Value = serde_json::from_str(&job.to_json()).unwrap();
        assert_eq!(node["action"], "convert");
        assert_eq!(node["layout"]["bpModels"], r"C:\out\x_bp\ysm_models");
        assert_eq!(node["layout"]["refRp"], r"C:\ref");
        assert_eq!(node["packs"][0]["collection"], "c");
        // 网易版文件夹只显示一个名字: 写 name, 不再分中英文 lang
        assert_eq!(node["collections"]["c"]["name"], "酒狐");
        assert!(node["collections"]["c"].get("lang").is_none());
        assert_eq!(node["collections"]["c"]["coverImage"], r"C:\art\cover.png");
        assert_eq!(node["options"]["withMods"], true);
        assert_eq!(node["options"]["validate"], false);
        assert_eq!(node["options"]["compactJson"], true); // 缺省压一行
        assert_eq!(node["options"]["writeCollections"], true); // 只有按包拆出的子任务才关
        assert_eq!(node["options"]["javaFunctions"], false); // 自定义函数是实验性功能, 缺省不转
    }

    #[test]
    fn java_functions_option_reaches_kernel() {
        let mut job = JobSpec::new("convert", layout());
        job.packs.push(PackSpec {
            java_dir: r"C:\java\p".into(),
            name: "p".into(),
            ..Default::default()
        });
        job.options.java_functions = true;
        let node: Value = serde_json::from_str(&job.to_json()).unwrap();
        assert_eq!(node["options"]["javaFunctions"], true);
        assert!(!ConvertOptions::default().java_functions);
    }

    #[test]
    fn auto_material_is_not_written_to_job() {
        let mut job = JobSpec::new("convert", layout());
        job.packs = vec![
            PackSpec {
                java_dir: r"C:\java\a".into(),
                name: "a".into(),
                material: Some("自动".into()),
                ..Default::default()
            },
            PackSpec {
                java_dir: r"C:\java\b".into(),
                name: "b".into(),
                material: Some("default=my_toon".into()),
                cull: Some(true),
                ..Default::default()
            },
            PackSpec {
                java_dir: r"C:\java\c".into(),
                name: "c".into(),
                cull: Some(false),
                ..Default::default()
            },
        ];
        let packs = serde_json::from_str::<Value>(&job.to_json()).unwrap()["packs"].clone();
        assert!(packs[0].get("material").is_none()); // 自动: 不写, 内核不改 ysm.json
        assert!(packs[0].get("cull").is_none());
        assert_eq!(packs[1]["material"], "default=my_toon");
        assert_eq!(packs[1]["cull"], true);
        assert!(packs[2].get("material").is_none());
        assert_eq!(packs[2]["cull"], false);
        assert!(packs[2]["collection"].is_null()); // 平铺: 显式写 null
    }

    #[test]
    fn discovered_pack_carries_material_profile() {
        let line = r#"{"event": "pack", "javaDir": "C:/j/p", "folder": "p", "suggestedName": "p", "materialProfile": {"allCutout": true, "pbrSkins": 2, "pbrVehicles": 0, "negativeCubes": 13}}"#;
        let pack = DiscoveredPack::from_value(&serde_json::from_str(line).unwrap()).unwrap();
        let profile = pack.material_profile.as_ref().unwrap();
        assert!(profile.all_cutout);
        assert_eq!(profile.negative_cubes, 13);
        let old =
            DiscoveredPack::from_value(&serde_json::from_str(r#"{"event": "pack", "javaDir": "C:/j/q"}"#).unwrap())
                .unwrap();
        assert!(old.material_profile.is_none()); // 旧内核不给画像
        assert_eq!(old.display_name(), "q");
    }

    #[test]
    fn discovered_pack_tolerates_odd_fields() {
        let line = r#"{"javaDir": "C:/j/p", "folder": "p", "hasArm": "yes", "metadata": {"name": "酒狐", "author": ["A", "B"]}}"#;
        let pack = DiscoveredPack::from_value(&serde_json::from_str(line).unwrap()).unwrap();
        assert_eq!(pack.has_arm, None);
        assert_eq!(pack.display_name(), "酒狐");
        assert_eq!(pack.author().as_deref(), Some("A, B"));
    }

    #[test]
    fn collection_cover_and_manifest() {
        let dir = std::env::temp_dir().join(format!("ysmconv-cover-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let write = |name: &str, bytes: usize| {
            let path = dir.join(name);
            std::fs::write(&path, vec![0u8; bytes]).unwrap();
            path.to_string_lossy().into_owned()
        };
        let spec = |cover: Option<String>| CollectionSpec {
            dir: "fox".into(),
            cover_image: cover,
            ..Default::default()
        };
        assert_eq!(spec(None).cover_problem(), None);
        assert_eq!(spec(Some(write("ok.png", 1000))).cover_problem(), None);
        assert!(
            spec(Some(dir.join("missing.png").to_string_lossy().into_owned()))
                .cover_problem()
                .unwrap()
                .contains("不存在")
        );
        assert!(
            spec(Some(write("art.jpg", 1000)))
                .cover_problem()
                .unwrap()
                .contains("PNG")
        );
        assert!(
            spec(Some(write("huge.png", CollectionSpec::MAX_COVER_BYTES as usize + 1)))
                .cover_problem()
                .unwrap()
                .contains("1MB")
        );

        assert!(
            CollectionSpec {
                dir: "fox".into(),
                name: Some("  ".into()),
                ..Default::default()
            }
            .is_empty()
        );
        assert!(
            !CollectionSpec {
                dir: "fox".into(),
                name: Some("狐狸".into()),
                ..Default::default()
            }
            .is_empty()
        );
        assert!(
            !CollectionSpec {
                dir: "fox".into(),
                cover_image: Some("a.png".into()),
                ..Default::default()
            }
            .is_empty()
        );
        let manifest = CollectionSpec {
            dir: "fox".into(),
            name: Some(" 狐狸 ".into()),
            cover_image: Some(write("c.png", 10)),
            ..Default::default()
        }
        .to_manifest();
        assert_eq!(manifest["name"], "狐狸");
        assert!(Path::new(manifest["coverImage"].as_str().unwrap()).is_absolute());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
