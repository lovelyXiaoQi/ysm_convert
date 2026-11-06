//! 模型材质与背面剔除的转换选项(逐包): 内核写进产物 ysm.json 的顶级 material / cull(port_java_pack.ApplyMaterialOptions)。
//! 缺省"自动"不写键, YSM 主组件按 Java 包判定 —— properties.all_cutout 打开 → 剔除背面(负尺寸描边), 皮肤带 PBR 贴图 → pbr,
//! 其余 cutout; 发光组(ysmGlow* 骨骼)缺省 emissive。背面剔除打开时这些预设一律换成剔除背面版(主组件 materialResolver.CULL_TWINS)。

use crate::error::{Error, Result};
use crate::models::{MaterialProfile, PackSpec};

pub const AUTO: &str = "自动";
pub const CULL_ON: &str = "剔除";
pub const CULL_OFF: &str = "不剔除";

/// 材质下拉框的一项: 值写进 ysm.json, 说明给人看。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MaterialChoice {
    pub value: &'static str,
    pub label: &'static str,
}

/// YSM 主组件的材质预设(materialResolver.MATERIAL_PRESETS); 剔除背面版通常不用直接选, 用"背面剔除"列。
pub const CHOICES: &[MaterialChoice] = &[
    MaterialChoice {
        value: AUTO,
        label: "读 Java 包: all_cutout → 剔除背面, 带 PBR 贴图 → pbr, 其余 cutout",
    },
    MaterialChoice {
        value: "cutout",
        label: "裁切: 按贴图透明裁掉(alpha < 0.1), 不剔除背面 —— Java 缺省",
    },
    MaterialChoice {
        value: "translucent",
        label: "半透明: 按贴图 alpha 混合",
    },
    MaterialChoice {
        value: "emissive",
        label: "全亮发光: 不受光照, 夜里也亮, 带泛光",
    },
    MaterialChoice {
        value: "pbr",
        label: "PBR: 裁切 + LabPBR 法线 / 高光贴图",
    },
    MaterialChoice {
        value: "bloom",
        label: "旧版泛光: 贴图 alpha < 0.3 的像素当发光(旧版基岩 YSM 约定)",
    },
    MaterialChoice {
        value: "cutout_cull",
        label: "裁切 + 剔除背面(负尺寸描边)",
    },
    MaterialChoice {
        value: "translucent_cull",
        label: "半透明 + 剔除背面",
    },
    MaterialChoice {
        value: "emissive_cull",
        label: "全亮发光 + 剔除背面",
    },
    MaterialChoice {
        value: "pbr_cull",
        label: "PBR + 剔除背面",
    },
];

pub const CULL_CHOICES: &[&str] = &[AUTO, CULL_ON, CULL_OFF];

/// YSM 主组件认的材质预设名(devtools/test_port_cli.py 的 TestConverterMaterialPresets 按这张表与主包核对)。
pub const PRESET_NAMES: &[&str] = &[
    "cutout",
    "cutout_cull",
    "translucent",
    "translucent_cull",
    "emissive",
    "emissive_cull",
    "pbr",
    "pbr_cull",
    "bloom",
    "bloom_nocull",
];

fn is_auto_word(text: &str) -> bool {
    text.is_empty() || text.eq_ignore_ascii_case("auto") || text == AUTO
}

/// `^[A-Za-z0-9_.]+$`
fn is_material_name(text: &str) -> bool {
    !text.is_empty() && text.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '.')
}

/// `[,，;；\r\n]+` 分隔的各段(空段由调用方跳过)。
fn split_pairs(text: &str) -> impl Iterator<Item = &str> {
    text.split([',', '，', ';', '；', '\r', '\n'])
}

/// 界面 / 命令行写的材质 → 交给内核的文本; "自动"与空白返回 None。
pub fn normalize(text: Option<&str>) -> Option<String> {
    let trimmed = text.unwrap_or("").trim();
    if is_auto_word(trimmed) {
        None
    } else {
        Some(trimmed.to_string())
    }
}

/// 材质写法的问题(与内核 port_java_pack.NormalizeMaterialOption 同一规则), 没问题返回 None。
/// 写法: 预设名 / 材质名(英文字母、数字、_ .), 或 "键=材质, 键=材质" 按材质键分别指定(default = 模型本体, ysm_glow = 发光组)。
pub fn problem(text: Option<&str>) -> Option<String> {
    let value = normalize(text)?;
    if !value.contains('=') {
        return if is_material_name(&value) {
            None
        } else {
            Some(format!("材质名只能用英文字母、数字、_ 和 .: {value}"))
        };
    }
    let mut keys: Vec<String> = Vec::new();
    for part in split_pairs(&value) {
        if part.trim().is_empty() {
            continue;
        }
        let Some(eq) = part.find('=') else {
            return Some(format!("按材质键指定要写成 键=材质: {}", part.trim()));
        };
        let key = part[..eq].trim();
        let name = part[eq + 1..].trim();
        for piece in [key, name] {
            if !is_material_name(piece) {
                return Some(format!("材质键 / 材质名只能用英文字母、数字、_ 和 .: {piece}"));
            }
        }
        if keys.iter().any(|k| k == key) {
            return Some(format!("材质键重复: {key}"));
        }
        keys.push(key.to_string());
    }
    if keys.is_empty() {
        Some("材质为空".into())
    } else {
        None
    }
}

/// 写的材质里有没有不是 YSM 预设的名字(自定义材质: 要在资源包 materials 里有定义, 不参与描边 / 泛光)。
pub fn is_custom(text: Option<&str>) -> bool {
    let Some(value) = normalize(text) else { return false };
    if problem(Some(&value)).is_some() {
        return false;
    }
    if value.contains('=') {
        split_pairs(&value)
            .filter(|p| !p.trim().is_empty())
            .map(|p| p[p.find('=').map_or(0, |i| i + 1)..].trim())
            .any(|name| !PRESET_NAMES.contains(&name))
    } else {
        !PRESET_NAMES.contains(&value.as_str())
    }
}

/// "自动 / 剔除 / 不剔除"(也认 auto / on / off) → None / true / false; 认不出时报参数错误。
pub fn parse_cull(text: Option<&str>) -> Result<Option<bool>> {
    let value = text.unwrap_or("").trim();
    if is_auto_word(value) {
        return Ok(None);
    }
    match value.to_lowercase().as_str() {
        CULL_ON | "on" | "true" | "1" | "yes" | "cull" => Ok(Some(true)),
        CULL_OFF | "off" | "false" | "0" | "no" | "nocull" => Ok(Some(false)),
        _ => Err(Error::Argument(format!(
            "背面剔除只认 auto / on / off(自动 / 剔除 / 不剔除): {}",
            text.unwrap_or("")
        ))),
    }
}

pub fn cull_text(cull: Option<bool>) -> &'static str {
    match cull {
        Some(true) => CULL_ON,
        Some(false) => CULL_OFF,
        None => AUTO,
    }
}

/// 界面里"背面剔除"列的文字 → 值(认不出的按自动)。
pub fn cull_from_text(text: &str) -> Option<bool> {
    match text {
        CULL_ON => Some(true),
        CULL_OFF => Some(false),
        _ => None,
    }
}

/// "自动"会选什么(给界面的概况 / 提示): 普通 / PBR / 负尺寸描边。
pub fn describe_auto(profile: Option<&MaterialProfile>) -> String {
    let Some(profile) = profile else { return "自动".into() };
    let mut kind = if profile.all_cutout {
        "负尺寸描边·剔除背面"
    } else {
        "普通"
    }
    .to_string();
    if profile.pbr_skins > 0 {
        kind = format!("PBR · {kind}");
    }
    kind
}

/// 负尺寸方块的提醒文字(没有负尺寸方块时 None)。
pub fn negative_cube_hint(profile: Option<&MaterialProfile>, cull: Option<bool>) -> Option<String> {
    let profile = profile.filter(|p| p.negative_cubes > 0)?;
    let culled = cull.unwrap_or(profile.all_cutout);
    Some(if culled {
        format!(
            "几何有负尺寸方块 {} 个, 背面剔除打开: 它们从外面看只剩背面(描边外壳)",
            profile.negative_cubes
        )
    } else {
        format!(
            "几何有负尺寸方块 {} 个, 不剔除背面: 按普通方块显示(与 Java 缺省一致); 它们是描边外壳的话把“背面剔除”设为剔除",
            profile.negative_cubes
        )
    })
}

/// 整批校验材质写法, 返回问题列表(空 = 通过)。
pub fn validate(packs: &[PackSpec]) -> Vec<String> {
    packs
        .iter()
        .filter_map(|p| problem(p.material.as_deref()).map(|problem| format!("{}: {problem}", p.name)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_kernel_material_syntax() {
        for text in [
            None,
            Some(""),
            Some("自动"),
            Some("auto"),
            Some("cutout_cull"),
            Some("entity_alphatest_one_sided"),
            Some("my_toon.v2"),
            Some("default=cutout, ysm_glow=translucent"),
            Some("default = pbr；ysm_glow=emissive"),
        ] {
            assert_eq!(problem(text), None, "{text:?}");
        }
    }

    // 与内核 port_java_pack.NormalizeMaterialOption 同一规则(devtools/test_port_cli.py 的同名用例)
    #[test]
    fn rejects_what_kernel_rejects() {
        for text in [
            "bad name",
            "default=cut=out",
            "default=cutout,default=pbr",
            "中文材质",
            "default=",
        ] {
            assert!(problem(Some(text)).is_some(), "{text}");
        }
    }

    #[test]
    fn parses_cull_words() {
        for auto in [None, Some(""), Some("auto"), Some("自动"), Some(" AUTO ")] {
            assert_eq!(parse_cull(auto).unwrap(), None);
        }
        for on in ["on", "剔除", "true", "ON"] {
            assert_eq!(parse_cull(Some(on)).unwrap(), Some(true));
        }
        for off in ["off", "不剔除", "false"] {
            assert_eq!(parse_cull(Some(off)).unwrap(), Some(false));
        }
        assert!(matches!(parse_cull(Some("maybe")), Err(Error::Argument(_))));
        assert_eq!(cull_text(Some(true)), CULL_ON);
        assert_eq!(cull_text(None), AUTO);
    }

    #[test]
    fn flags_custom_materials() {
        assert!(!is_custom(Some("自动")));
        assert!(!is_custom(Some("cutout")));
        assert!(!is_custom(Some("default=pbr_cull, ysm_glow=emissive")));
        assert!(is_custom(Some("my_toon")));
        assert!(is_custom(Some("default=cutout, ysm_glow=my_glow")));
        assert!(!is_custom(Some("bad name"))); // 写错的先按写法报, 不算自定义
        // 下拉框里的预设(除"自动")都是 YSM 主组件认的预设名
        for choice in &CHOICES[1..] {
            assert!(!is_custom(Some(choice.value)), "{}", choice.value);
        }
        assert_eq!(CHOICES[0].value, AUTO);
    }

    #[test]
    fn describes_auto_and_negative_cubes() {
        let profile = MaterialProfile {
            all_cutout: true,
            pbr_skins: 2,
            pbr_vehicles: 0,
            negative_cubes: 13,
        };
        assert_eq!(describe_auto(Some(&profile)), "PBR · 负尺寸描边·剔除背面");
        assert_eq!(describe_auto(Some(&MaterialProfile::default())), "普通");
        assert!(negative_cube_hint(Some(&profile), None).unwrap().contains("描边外壳"));
        assert!(
            negative_cube_hint(Some(&profile), Some(false))
                .unwrap()
                .contains("不剔除背面")
        );
        assert_eq!(negative_cube_hint(Some(&MaterialProfile::default()), Some(true)), None);
    }

    #[test]
    fn validates_batch() {
        let packs = vec![PackSpec {
            java_dir: r"C:\j\01".into(),
            name: "p01".into(),
            collection: None,
            material: Some("bad name".into()),
            cull: None,
        }];
        assert_eq!(validate(&packs).len(), 1);
    }
}
