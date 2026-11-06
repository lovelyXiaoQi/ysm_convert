//! 界面设置: %APPDATA%\YsmConvert\settings.json。键名沿用旧版(C# WPF)的写法, 升级后设置照常读得回来。

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase", default)]
pub struct AppSettings {
    pub output_root: String,
    pub is_component_mode: bool,
    pub component_name: String,
    pub with_mods: bool,
    /// 转换 Java 自定义函数(实验性, 缺省关)。
    pub java_functions: bool,
    pub validate_after: bool,
    pub use_collection: bool,
    pub collection_dir: String,
    /// 文件夹显示名(网易版只显示一个名字)。
    pub collection_name: String,
    /// 文件夹封面图(PNG)路径, 空 = 用 Java 合集自带的 ysm-pack.png 或默认封面。
    pub collection_cover: String,
    /// 产物 JSON 压成一行。
    pub compact_json: bool,
    pub last_input_dir: String,
    pub show_details: bool,
}

impl Default for AppSettings {
    fn default() -> Self {
        AppSettings {
            output_root: String::new(),
            is_component_mode: true,
            component_name: "my_ysm_models".into(),
            with_mods: false,
            java_functions: false,
            validate_after: true,
            use_collection: false,
            collection_dir: String::new(),
            collection_name: String::new(),
            collection_cover: String::new(),
            compact_json: true,
            last_input_dir: String::new(),
            show_details: false,
        }
    }
}

fn file_path() -> Option<PathBuf> {
    std::env::var_os("APPDATA").map(|d| PathBuf::from(d).join("YsmConvert").join("settings.json"))
}

impl AppSettings {
    /// 坏设置文件按缺省处理。
    pub fn load() -> AppSettings {
        file_path()
            .and_then(|p| std::fs::read_to_string(p).ok())
            .and_then(|t| serde_json::from_str(t.trim_start_matches('\u{feff}')).ok())
            .unwrap_or_default()
    }

    /// 保存失败不影响使用。
    pub fn save(&self) {
        let Some(path) = file_path() else { return };
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(text) = serde_json::to_string_pretty(self) {
            let _ = std::fs::write(path, text);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_settings_written_by_the_old_wpf_app() {
        let old = r#"{
  "OutputRoot": "D:\\out",
  "IsComponentMode": false,
  "ComponentName": "fox",
  "WithMods": true,
  "JavaFunctions": false,
  "ValidateAfter": true,
  "UseCollection": true,
  "CollectionDir": "wine_fox",
  "CollectionName": "酒狐",
  "CollectionCover": "",
  "CompactJson": false,
  "LastInputDir": "D:\\java",
  "ShowDetails": true
}"#;
        let s: AppSettings = serde_json::from_str(old).unwrap();
        assert_eq!(s.output_root, "D:\\out");
        assert!(!s.is_component_mode);
        assert_eq!(s.collection_name, "酒狐");
        assert!(!s.compact_json);
        assert!(s.show_details);
        // 缺的键取缺省
        let partial: AppSettings = serde_json::from_str(r#"{"OutputRoot": "x"}"#).unwrap();
        assert!(partial.is_component_mode && partial.validate_after && partial.compact_json);
        assert_eq!(partial.component_name, "my_ysm_models");
        assert!(serde_json::to_string(&s).unwrap().contains("\"IsComponentMode\":false"));
    }
}
