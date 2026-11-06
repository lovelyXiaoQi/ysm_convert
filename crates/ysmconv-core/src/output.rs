//! 产物落点的两种形态, 底层其实是同一件事 —— 一个装着行为包 + 资源包的目录:
//! - 工程形态: 已有的 YSM 组件工程(如主仓库 ysm_bp/ysm_rp), 按 manifest.json 的模块类型识别哪个是 BP 哪个是 RP;
//! - 组件形态: 新建 `<根>/<名>_bp` 与 `<根>/<名>_rp`(带 manifest.json), 运行时被 YSM 主包自动发现。

use std::fs;
use std::path::Path;

use serde_json::{Value, json};

use crate::error::{Error, Result};
use crate::models::{OutputLayout, full_path};
use crate::win;

pub fn detect(root: &str) -> Result<OutputLayout> {
    try_detect(root).map_err(Error::Invalid)
}

pub fn try_detect(root: &str) -> std::result::Result<OutputLayout, String> {
    let root = full_path(root);
    let root_path = Path::new(&root);
    if !root_path.is_dir() {
        return Err(format!("目录不存在: {root}"));
    }
    let dirs = sorted_subdirs(root_path);
    let mut bp: Option<String> = None;
    let mut rp: Option<String> = None;
    let mut seen = Vec::new();
    for dir in &dirs {
        let manifest = dir.join("manifest.json");
        if !manifest.is_file() {
            continue;
        }
        let types = read_module_types(&manifest);
        seen.push(format!("{}[{}]", name_of(dir), types.join(",")));
        let has = |t: &str| types.iter().any(|x| x.eq_ignore_ascii_case(t));
        if bp.is_none() && (has("data") || has("javascript")) {
            bp = Some(dir.to_string_lossy().into_owned());
        } else if rp.is_none() && has("resources") {
            rp = Some(dir.to_string_lossy().into_owned());
        }
    }
    // 没有 manifest 的裸目录按名字兜底(ysm_bp/ysm_rp 之类)
    if bp.is_none() {
        bp = dirs
            .iter()
            .find(|d| looks_like_bp(&name_of(d)))
            .map(|d| d.to_string_lossy().into_owned());
    }
    if rp.is_none() {
        rp = dirs
            .iter()
            .find(|d| looks_like_rp(&name_of(d)))
            .map(|d| d.to_string_lossy().into_owned());
    }
    match (bp, rp) {
        (Some(bp), Some(rp)) => Ok(OutputLayout::new(root, bp, rp)),
        _ => Err(format!(
            "在 {root} 下没找到成对的行为包与资源包(按 manifest.json 的模块类型 data / resources 识别)。{} 要新建组件请用组件形态(指定组件名)。",
            if seen.is_empty() {
                " 该目录下没有任何带 manifest.json 的子目录。".to_string()
            } else {
                format!(" 找到: {}", seen.join("; "))
            }
        )),
    }
}

/// 新建(或复用)组件: `<root>/<name>_bp`、`<root>/<name>_rp`, 缺 manifest.json 就补。
pub fn ensure_component(root: &str, component_name: &str) -> Result<OutputLayout> {
    if !is_valid_component_name(component_name) {
        return Err(Error::Argument(format!(
            "组件名只能用英文、数字、下划线与连字符: {component_name}"
        )));
    }
    let root = full_path(root);
    let bp = Path::new(&root).join(format!("{component_name}_bp"));
    let rp = Path::new(&root).join(format!("{component_name}_rp"));
    fs::create_dir_all(bp.join("ysm_models"))?;
    fs::create_dir_all(&rp)?;
    ensure_behavior_pack_marker(&bp.to_string_lossy())?;
    let bp_manifest = bp.join("manifest.json");
    let rp_manifest = rp.join("manifest.json");
    if !bp_manifest.is_file() {
        fs::write(
            &bp_manifest,
            build_manifest_json("data", &win::uuid_v4(), &win::uuid_v4()),
        )?;
    }
    if !rp_manifest.is_file() {
        fs::write(
            &rp_manifest,
            build_manifest_json("resources", &win::uuid_v4(), &win::uuid_v4()),
        )?;
    }
    Ok(OutputLayout::new(root, bp.to_string_lossy(), rp.to_string_lossy()))
}

/// 网易按有没有 `entities` 文件夹识别行为包: 没有就不挂载(MC Studio 测试世界与正式游戏都只启用它的资源包),
/// ysm_models 里的 ysm.json 永远扫不到。MCDK 按 manifest 建目录联接, 不受这条限制, 在那边测不出来。
/// 缺就补一个带 .gitkeep 的空文件夹(git 不跟踪空目录), 已有的不动。
pub fn ensure_behavior_pack_marker(bp_dir: &str) -> Result<()> {
    let entities = Path::new(bp_dir).join("entities");
    if entities.is_dir() {
        return Ok(());
    }
    fs::create_dir_all(&entities)?;
    fs::write(entities.join(".gitkeep"), b"")?;
    Ok(())
}

pub fn is_valid_component_name(name: &str) -> bool {
    !name.trim().is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// 与 YSM 主包 manifest 同形(网易形态: header 无 name/description, format_version 1, 最低引擎 1.18.0)。
pub fn build_manifest_json(module_type: &str, header_uuid: &str, module_uuid: &str) -> String {
    let manifest = json!({
        "modules": [
            {
                "type": module_type,
                "uuid": module_uuid,
                "version": [1, 0, 0],
            }
        ],
        "header": {
            "uuid": header_uuid,
            "version": [1, 0, 0],
            "min_engine_version": [1, 18, 0],
        },
        "format_version": 1,
    });
    serde_json::to_string_pretty(&manifest).unwrap_or_default() + "\n"
}

fn read_module_types(manifest: &Path) -> Vec<String> {
    // 坏 manifest 当作没有模块
    let Ok(text) = fs::read_to_string(manifest) else {
        return Vec::new();
    };
    let Ok(doc) = crate::jsonc::parse(&text) else {
        return Vec::new();
    };
    let mut types: Vec<String> = Vec::new();
    if let Some(modules) = doc.get("modules").and_then(Value::as_array) {
        for m in modules {
            if let Some(t) = m.get("type").and_then(Value::as_str)
                && !types.iter().any(|x| x.eq_ignore_ascii_case(t))
            {
                types.push(t.to_string());
            }
        }
    }
    types
}

fn sorted_subdirs(root: &Path) -> Vec<std::path::PathBuf> {
    let mut dirs: Vec<std::path::PathBuf> = fs::read_dir(root)
        .map(|it| {
            it.filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.is_dir())
                .collect()
        })
        .unwrap_or_default();
    dirs.sort_by_key(|d| name_of(d).to_lowercase());
    dirs
}

fn name_of(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn looks_like_bp(name: &str) -> bool {
    let n = name.to_lowercase();
    n.ends_with("_bp") || n == "bp" || n.contains("behavior")
}

fn looks_like_rp(name: &str) -> bool {
    let n = name.to_lowercase();
    n.ends_with("_rp") || n == "rp" || n.contains("resource")
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TempRoot(std::path::PathBuf);

    impl TempRoot {
        fn new(tag: &str) -> Self {
            let dir = std::env::temp_dir().join(format!("ysmconv-test-{tag}-{}", win::uuid_v4()));
            fs::create_dir_all(&dir).unwrap();
            TempRoot(dir)
        }
        fn path(&self) -> String {
            self.0.to_string_lossy().into_owned()
        }
    }

    impl Drop for TempRoot {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn read_json(path: &Path) -> Value {
        serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
    }

    #[test]
    fn ensure_component_creates_manifests_and_detects_them() {
        let root = TempRoot::new("comp");
        let layout = ensure_component(&root.path(), "my_models").unwrap();
        assert!(Path::new(&layout.bp_dir).join("manifest.json").is_file());
        assert!(Path::new(&layout.rp_dir).join("manifest.json").is_file());
        assert!(Path::new(&layout.bp_models()).is_dir());
        assert!(!layout.has_baseline());
        // 网易按 entities 文件夹识别行为包, 没有就不挂载; 放 .gitkeep 让它进得了 git
        assert!(Path::new(&layout.bp_dir).join("entities").join(".gitkeep").is_file());

        let bp = read_json(&Path::new(&layout.bp_dir).join("manifest.json"));
        assert_eq!(bp["modules"][0]["type"], "data");
        assert_eq!(bp["format_version"], 1);
        assert_eq!(bp["header"]["min_engine_version"], json!([1, 18, 0]));

        let detected = detect(&root.path()).unwrap();
        assert_eq!(detected.bp_dir, layout.bp_dir);
        assert_eq!(detected.rp_dir, layout.rp_dir);

        // 幂等: 再跑一次不会改 uuid
        let again = ensure_component(&root.path(), "my_models").unwrap();
        let bp2 = read_json(&Path::new(&again.bp_dir).join("manifest.json"));
        assert_eq!(bp["header"]["uuid"], bp2["header"]["uuid"]);
        assert_ne!(bp["header"]["uuid"], bp["modules"][0]["uuid"]);
    }

    #[test]
    fn behavior_pack_marker_leaves_existing_entities_alone() {
        let root = TempRoot::new("marker");
        let bp = root.0.join("ysm_bp");
        let entities = bp.join("entities");
        fs::create_dir_all(&entities).unwrap();
        fs::write(entities.join("__init__.py"), "").unwrap();
        ensure_behavior_pack_marker(&bp.to_string_lossy()).unwrap();
        let names: Vec<String> = fs::read_dir(&entities)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, vec!["__init__.py"]);

        // 缺的补上, 再跑一次不变
        let bare = root.0.join("bare_bp");
        fs::create_dir_all(&bare).unwrap();
        ensure_behavior_pack_marker(&bare.to_string_lossy()).unwrap();
        ensure_behavior_pack_marker(&bare.to_string_lossy()).unwrap();
        let names: Vec<String> = fs::read_dir(bare.join("entities"))
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, vec![".gitkeep"]);
    }

    #[test]
    fn detect_falls_back_to_folder_names() {
        let root = TempRoot::new("names");
        fs::create_dir_all(root.0.join("ysm_bp")).unwrap();
        fs::create_dir_all(root.0.join("ysm_rp")).unwrap();
        let layout = detect(&root.path()).unwrap();
        assert!(layout.bp_dir.ends_with("ysm_bp"));
        assert!(layout.rp_dir.ends_with("ysm_rp"));
    }

    #[test]
    fn detect_reads_manifests_with_comments() {
        let root = TempRoot::new("jsonc");
        let a = root.0.join("A");
        let b = root.0.join("B");
        fs::create_dir_all(&a).unwrap();
        fs::create_dir_all(&b).unwrap();
        fs::write(
            a.join("manifest.json"),
            "{ // rp\n \"modules\": [{\"type\": \"resources\",},],}",
        )
        .unwrap();
        fs::write(b.join("manifest.json"), "\u{feff}{\"modules\": [{\"type\": \"data\"}]}").unwrap();
        let layout = detect(&root.path()).unwrap();
        assert!(layout.bp_dir.ends_with('B'));
        assert!(layout.rp_dir.ends_with('A'));
    }

    #[test]
    fn detect_fails_on_empty_directory() {
        let root = TempRoot::new("empty");
        let error = try_detect(&root.path()).unwrap_err();
        assert!(error.contains("组件"));
        assert!(matches!(
            ensure_component(&root.path(), "bad name"),
            Err(Error::Argument(_))
        ));
    }
}
