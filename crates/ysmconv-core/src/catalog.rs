//! 把内核汇总里的告警文本翻译成"这是什么、要不要管、去哪查"。
//! 规则按内核(port_java_pack / java_runtime_bindings / validate_rp_animations)的固定措辞匹配, 措辞变了这里跟着改。
//! molang 类别由内核宿主 port_cli.SplitMolangLabel 给出: map 等价替换 / const 中性常量 / zero 置零 / func 函数改写 /
//! warn 退化 / lower 动画名转小写 / norm 规范化 / skip 跳过 / tick 每 tick 脚本。

pub const MOLANG_MAPPING_DOC: &str = "ysm-java-molang-mapping.md";
pub const PORT_TUTORIAL_DOC: &str = "ysm-java-port-tutorial.md";
pub const PACK_GUIDE_DOC: &str = "ysm-json-pack-guide.md";
pub const ANIMATION_MECHANISM_DOC: &str = "ysm-java-animation-mechanism.md";
pub const FUNCTIONS_DOC: &str = "ysm-java-functions.md";

/// molang 留痕类别(与 port_cli 的 molang.kind 一致)。
pub const MOLANG_KINDS: &[&str] = &["map", "const", "zero", "func", "warn", "lower", "norm", "skip", "tick"];

/// 一条需要开发者过目的项目意味着什么、该去哪看。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Explanation {
    pub category: &'static str,
    pub advice: &'static str,
    pub doc: Option<&'static str>,
}

const fn ex(category: &'static str, advice: &'static str, doc: &'static str) -> Explanation {
    Explanation {
        category,
        advice,
        doc: Some(doc),
    }
}

fn has(text: &str, part: &str) -> bool {
    text.contains(part)
}

/// 不分 ASCII 大小写的包含判断(needle 必须是 ASCII 小写)。
fn has_ignore_case(text: &str, lowercase_ascii_part: &str) -> bool {
    text.to_ascii_lowercase().contains(lowercase_ascii_part)
}

pub fn is_molang_kind(kind: Option<&str>) -> bool {
    kind.is_some_and(|k| MOLANG_KINDS.contains(&k))
}

pub fn explain_molang(kind: &str, label: &str) -> Explanation {
    match kind {
        "const" => explain_constant(label),
        "zero" => explain_zero(label),
        "func" => {
            if has(label, "未知") {
                return ex(
                    "未知函数置零",
                    "Java 侧的函数(ysm.*/ctrl.*)内核不认识, 已置 0; 对照 Java 源码手工改写成基岩 molang。",
                    MOLANG_MAPPING_DOC,
                );
            }
            if has(label, "物理") {
                return ex(
                    "物理函数改写",
                    "second_order/first_order 物理函数已改写成 molang 状态积分(v.ysm_so_<键>_y), 一般无需处理; 想调整手感改 ysm.json 里的参数后重新转换。",
                    ANIMATION_MECHANISM_DOC,
                );
            }
            if label.ends_with("(置常量)") {
                return explain_constant(label);
            }
            ex(
                "函数替换",
                "Java 函数按映射表取参数(物理函数的键不是字面量等改写不了的情况, 丢掉平滑保留运动), 通常可用; 只在效果异常时回头核对。",
                MOLANG_MAPPING_DOC,
            )
        }
        "warn" => explain_warn(label),
        "lower" => ex(
            "动画名转小写",
            "基岩资源 ID 只认小写, 动画名与全部引用(含 ysm.json 的轮盘/预览动画)已同步转小写, 一般无需处理; 只有在 Java 里靠大小写区分两个同名动画时才需要先在 Java 包里改名。",
            PORT_TUTORIAL_DOC,
        ),
        "map" => {
            if is_constant_replacement(label) {
                return explain_constant(label);
            }
            if is_maid_runtime_mapping(label) {
                return ex(
                    "车万女仆运行层提供",
                    "女仆身份(ysm.is_maid / is_player / entity_type)、女仆坐下(tlm.is_sitting)与祈求(tlm.is_begging, 主人拿着蛋糕时)由网易版车万女仆写在套着这个模型的女仆身上; 玩家身上恒为缺省值(不是女仆、没坐下、没在祈求), 与 Java 玩家侧一致, 无需处理。",
                    MOLANG_MAPPING_DOC,
                );
            }
            if is_runtime_mapping(label) {
                return ex(
                    "主组件运行层提供",
                    "这个 Java 量由 YSM 主组件运行时逐帧/逐 tick 提供真值(视角/运动量/天气/血量/露天/roaming 存档同步/药水附魔探针/骨骼旋转回读等), 无需处理; 产物要与同期或更新的 YSM 主组件一起使用。",
                    MOLANG_MAPPING_DOC,
                );
            }
            ex("molang 映射", "按映射表完成的等价替换, 无需处理。", MOLANG_MAPPING_DOC)
        }
        "norm" => ex(
            "按 Java 语义规范化",
            "动画/控制器字段按 Java 的运行语义换算成基岩写法(循环长度、catmullrom 分段、过渡归属等), 无需处理。",
            ANIMATION_MECHANISM_DOC,
        ),
        "skip" => {
            if has(label, "不在脚本控制器里") {
                return ex(
                    "脚本控制器函数用在别处",
                    "ctrl.set_animation / set_beginning_transition_length / reset / indicate_reload 只在 @player_ctrl_ 脚本里有效, 别处调用 Java 同样什么都不做, 已删除, 无需处理。",
                    FUNCTIONS_DOC,
                );
            }
            if has(label, "debug_output") {
                return ex(
                    "调试输出已删除",
                    "query.debug_output 是 Java 的调试打印, 基岩没有对应, 已删除, 不影响动画。",
                    FUNCTIONS_DOC,
                );
            }
            if has(label, "Java 不发出") {
                return ex(
                    "值位置的发出类调用",
                    "音效 / ysm.sync 写在骨骼通道、转移条件或动画权重里时 Java 同样不发出(allowEmitting 为假), 已删除, 表现与 Java 一致; 要触发请写进 timeline 指令帧、控制器 on_entry 或事件函数。",
                    FUNCTIONS_DOC,
                );
            }
            if label.starts_with("ysm.defer") {
                return ex(
                    "defer 未支持",
                    "ysm.defer 延迟事件尚未支持, 调用已删除, 对应的 @defer 处理函数在基岩不会执行。",
                    FUNCTIONS_DOC,
                );
            }
            if has(label, "事件尚未支持") {
                return ex(
                    "函数事件未编译",
                    "@defer 等事件处理函数尚未支持, 基岩侧不会执行这段函数; 依赖它的效果不会出现。",
                    FUNCTIONS_DOC,
                );
            }
            ex(
                "Java 同样不挂载",
                "这个控制器在 Java 里也不会动作(非通道名或初始状态不存在), 转换时一并跳过, 与 Java 表现一致。",
                ANIMATION_MECHANISM_DOC,
            )
        }
        "tick" => ex(
            "每 tick 脚本动画",
            "Java 显式 0 长度的循环动画是\"每 tick 执行一次\"的脚本, 已换成 0.05 秒循环, 无需处理。",
            ANIMATION_MECHANISM_DOC,
        ),
        _ => ex("molang", "内核留痕项, 按说明判断。", MOLANG_MAPPING_DOC),
    }
}

/// "名字 -> 字面量": Java 专有量按中性常量处理(与 port_cli._CONST_REPLACEMENT 同口径)。
pub fn is_constant_replacement(label: &str) -> bool {
    let Some(arrow) = label.find(" -> ") else { return false };
    let replacement = label[arrow + 4..].trim();
    if replacement.chars().count() >= 2 && replacement.starts_with('\'') && replacement.ends_with('\'') {
        // 两端各一个引号、中间没有引号: 'x' 是字面量, 'x' + 'y' 不是
        return !replacement[1..replacement.len() - 1].contains('\'');
    }
    is_number_literal(replacement)
}

/// `^-?[0-9]+(?:\.[0-9]+)?$`
fn is_number_literal(text: &str) -> bool {
    let body = text.strip_prefix('-').unwrap_or(text);
    let (int_part, frac_part) = match body.split_once('.') {
        Some((i, f)) => (i, Some(f)),
        None => (body, None),
    };
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    digits(int_part) && frac_part.is_none_or(digits)
}

fn is_maid_runtime_mapping(label: &str) -> bool {
    has(label, "query.mod.ysm_is_maid") || has(label, "query.mod.ysm_tlm_")
}

fn is_runtime_mapping(label: &str) -> bool {
    [
        "主包运行层",
        "骨骼旋转回读",
        "query.mod.ysm_",
        "variable.ysm_env_",
        "variable.ysm_pb_",
        "variable.ysm_elytra_rot_",
        "v.roaming",
        // 自定义函数: 音效由主包宿主播放, 事件体 / 脚本控制器编进的逐帧执行体与初始化控制器由主包挂载
        "主包音效宿主",
        "主包同步宿主",
        "逐帧执行体",
        "变量初始化控制器",
    ]
    .iter()
    .any(|part| has(label, part))
}

fn explain_constant(label: &str) -> Explanation {
    if has(label, "ctrl.tac_") {
        return ex(
            "TACZ 枪 ID 未接入",
            "ctrl.tac_gun_id / ctrl.tac_fire_mode 按空串处理: YSM 主组件暂未读取枪械联动协议里预留的枪 ID(gun_id), 射击模式协议里没有, 依赖它们的判定(含逐枪动画)在基岩不会成立。其余 ctrl.tac_*(持枪/枪种/开火/瞄准/换弹/近战/拔枪)由主组件运行层提供。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "riptide") {
        return ex(
            "molang 中性常量",
            "基岩没有激流(三叉戟冲刺)查询, ctrl.riptide / ysm.is_riptide 按 0 处理, 激流姿态在基岩不会出现。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "ctrl.") {
        return ex(
            "模组联动按未安装处理",
            "这是 Java 端某个模组(跑酷/拔刀剑/背包/乐器等)的联动变量, 按\"该模组没装\"的缺省值处理(字符串 ''、数值 0); 基岩没有这个模组, 对应动画不会触发。",
            MOLANG_MAPPING_DOC,
        );
    }
    if label.starts_with("tlm.") {
        return ex(
            "车万女仆联动按未安装处理",
            "tlm.* 里网易版车万女仆没有对应数据的量(好感度、任务、日程、背包、棋局等)按 Java 没装女仆模组时的缺省值处理(is_entity 为真、gomoku_rank 为 1、字符串 ''、其余 0); 女仆坐下 tlm.is_sitting、祈求 tlm.is_begging 另由女仆运行层提供。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "particle") {
        return ex(
            "molang 中性常量",
            "通道表达式里的 ysm.particle 基岩没有对应, 按 0 处理; 只有 timeline 里的 particle 调用会转成基岩 particle_effects 关键帧。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "sound") {
        return ex(
            "自定义函数音效置零",
            "ysm.play_sound / stop_sound / stop_all_sounds 属于自定义函数(实验性功能, 缺省不转换), 按 0 处理; 需要时勾选「转换自定义函数(实验性)」重新转换, 由主组件音效宿主播放。动画 sound_effects 关键帧的音效不受影响(总会转换)。",
            FUNCTIONS_DOC,
        );
    }
    if has(label, "ysm.sync") || has(label, "ysm.keyboard") || has(label, "ysm.mouse") {
        return ex(
            "自定义函数调用置零",
            "ysm.sync / keyboard / mouse 属于自定义函数(实验性功能, 缺省不转换), 按 0 处理; 需要时勾选「转换自定义函数(实验性)」重新转换。",
            FUNCTIONS_DOC,
        );
    }
    if has(label, "ysm.bone_") {
        return ex(
            "molang 中性常量",
            "bone_pos/bone_scale/bone_pivot_abs/bone_color 等骨骼函数基岩没有对应, 按 0 处理; 只有 ysm.bone_rot('骨骼').x/y/z(骨骼名是字面量)能转成骨骼旋转回读。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "effect_level") || has(label, "enchantment_level") || has(label, "relative_block_name") {
        return ex(
            "molang 中性常量",
            "药水/附魔/相对方块查询的参数不是常量, 登记不成主组件运行层探针, 按旧策略置常量(等级 0 / 方块名 ''); 改成字面量参数后重新转换。",
            MOLANG_MAPPING_DOC,
        );
    }
    ex(
        "molang 中性常量",
        "Java 专有量在基岩没有对应, 已按语义中性常量处理(如 biome_category 为空串、attack_speed 取原版玩家默认值 4); 动画里依赖它变化的效果在基岩不会出现, 一般无需处理。完整清单见映射文档第五节。",
        MOLANG_MAPPING_DOC,
    )
}

fn explain_zero(label: &str) -> Explanation {
    if has(label, "Java 同样解析失败") || has(label, "基岩解析不了") {
        return ex(
            "作者原文解析失败",
            "这个值在 Java 里同样解析失败(Java 只作废这一个值, 按 0 算), 已按同样口径处置: 分量/条件落 0, timeline/on_entry 条目删除, 游戏里表现与 Java 一致。多半是作者笔误, 想修就改 Java 源再重新转换。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "ctrl.ride") {
        return ex(
            "molang 置零",
            "Java 的骑乘判定(passenger/#tag 形态)基岩没有对应查询, 该表达式按 0 求值; 若动画依赖它, 改用 query.is_riding 一类的基岩查询手写。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "结构体成员") {
        return ex(
            "molang 置零",
            "返回结构体(Vec3)的函数再取成员 .x/.y/.z 基岩没有对应, 连同调用一起置 0; 其中 ysm.bone_rot('骨骼').x/y/z(骨骼名是字面量)已转成骨骼旋转回读, 剩下的是 bone_pos/bone_scale 或参数不是字面量的调用, 需要时重写动画。",
            MOLANG_MAPPING_DOC,
        );
    }
    if label.starts_with("fn.") {
        return ex(
            "自定义函数置零",
            "自定义函数(functions/*.molang)是实验性功能, 缺省不转换, 调用按 0 处理; 依赖它的效果需要时勾选「转换自定义函数(实验性)」重新转换(命令行 --java-functions)。已开启仍出现这一条, 说明这个调用没能内联(函数不存在 / 函数文件解析失败 —— Java 同样得 null, 或递归 / 调用链超过 32 层)。",
            FUNCTIONS_DOC,
        );
    }
    if label.starts_with("tlm.") {
        return ex(
            "车万女仆联动置零",
            "这个 tlm.* 不在车万女仆联动的已知绑定里(可能是新版 Java 女仆模组新增的名字或作者笔误), 已置 0; 玩家模型上本来就取不到女仆的值, 一般无需处理。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "引擎二进制无此名") {
        return ex(
            "基岩没有的查询",
            "网易基岩引擎没有这个查询(引用它会让整份文件被拒载), 已置 0; 对照映射文档改写成基岩有的查询。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "残缺前缀") {
        return ex(
            "作者原文残句",
            "Java 源里有 ysm./ctrl./fn. 后面没接名字的残句(Java 同样解析不了), 直接删除; 多半是作者笔误。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "槽位/分类无法转换") {
        return ex(
            "物品条件转换失败",
            "ctrl.hold/swing/use/armor 的槽位或物品分类参数转不成基岩判定, 该条件按 0 处理; 参数改成 Java 标准写法($物品ID / #标签 / :分类)后重新转换。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "ysm.particle(ID 非字面量") {
        return ex(
            "粒子未转换",
            "粒子 ID 不是字面量, 转不成基岩 particle_effects 关键帧, 这次调用被删除; 改成字面量 ID 后重新转换。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "无映射") {
        return ex(
            "molang 置零",
            "Java 专有查询/变量在映射表里没有对应, 已替换成 0(可能是新版 Java YSM 新增的名字或作者笔误); 检查该动画是否因此失去驱动, 必要时按映射文档改写。",
            MOLANG_MAPPING_DOC,
        );
    }
    ex(
        "molang 置零",
        "Java 专有查询/函数在基岩没有对应, 已替换成常量 0; 检查该动画是否因此失去驱动, 必要时按映射文档改写。",
        MOLANG_MAPPING_DOC,
    )
}

fn explain_warn(label: &str) -> Explanation {
    if has(label, "sound_effects") || has(label, "音效") {
        return ex(
            "原版音效命名差异",
            "Java 版原版音效 ID 与基岩不同名, 转换时把 Java 名直通进了 sound_definitions.json; 对照基岩原版音效表把该定义改成基岩的名字, 查无的名字在游戏里无声。",
            PACK_GUIDE_DOC,
        );
    }
    if has(label, "ysm.particle") && has(label, "位置为表达式") {
        return ex(
            "粒子位置近似",
            "粒子生成位置是表达式, 基岩粒子关键帧只能挂在 locator 上, 已落到实体原点; 偏差明显时在几何里加 locator 并手改该关键帧。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "abs_particle") {
        return ex(
            "粒子位置近似",
            "Java 在世界绝对坐标生成粒子, 基岩只能挂在随身 locator 上, 位置是近似。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "ysm.particle") && has(label, "无基岩对照") {
        return ex(
            "粒子 ID 无对照",
            "这个粒子 ID 没有基岩对照, 原名直通; 基岩查无该粒子时游戏里没有效果, 需要换成基岩原版粒子或自带粒子定义。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "texture_name") {
        return ex(
            "贴图名比较恒不成立",
            "Java 源在比较一个本包贴图表里没有的贴图名, 与 Java 一样恒不成立(== 恒假 / != 恒真); 多半是作者笔误或删掉的皮肤。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "登记成探针") {
        return ex(
            "运行层探针未登记",
            "药水/附魔/相对方块查询要常量参数才能登记成主组件运行层探针; 这里的参数是表达式, 按旧策略置常量(等级 0 / 方块名 ''), 改成字面量参数后重新转换。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "证据缺失") {
        return ex(
            "引擎支持待确认",
            "这个查询/函数在网易引擎里是否存在缺少实机证据, 已原样保留; 若进游戏发现这份动画整份不生效(模型停在绑定姿态), 先按映射文档改写它。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(label, "参数不足") {
        return ex(
            "math 参数个数不对",
            "math 函数参数个数与基岩要求不符, 原样保留; 基岩按参数个数校验, 不对会让整份文件被拒载 —— 对照 Java 源修正。",
            MOLANG_MAPPING_DOC,
        );
    }
    ex(
        "molang 退化",
        "参数残缺或键非字面量, 内核退化处理(保留原样/取输入); 检查 Java 源里的写法是否可简化成字面量。",
        MOLANG_MAPPING_DOC,
    )
}

pub fn explain_log(level: &str, text: &str) -> Explanation {
    let t = text.trim_start();
    if has(t, "脚本控制器") && has(t, "未转换") {
        return ex(
            "脚本控制器未转换",
            "Java 每帧脚本控制器属于 main/use/swing 等带内置状态机的通道(尚未支持), 或 set_animation 的动画名 / 循环类型不是常量, 内核放弃转换; 该通道姿态需手工写成基岩动画控制器。纯决策树的脚本总会展开成基岩控制器; 其余空白通道与并行通道的脚本要勾选「转换自定义函数(实验性)」才会编进逐帧执行体。",
            FUNCTIONS_DOC,
        );
    }
    if has(t, "自定义函数") && has(t, "实验性") {
        return ex(
            "自定义函数未转换(实验性)",
            "包里带 functions/*.molang 自定义函数, 这是实验性功能、缺省不转换: fn.* / 音效 / ysm.sync 调用按 0 处理, @player_init / @player_update / @sync 事件不执行。依赖它的效果(函数驱动的音效、计数器、脚本控制器等)需要时勾选「转换自定义函数(实验性)」重新转换(命令行 --java-functions)。",
            FUNCTIONS_DOC,
        );
    }
    if has(t, "自定义材质") {
        return ex(
            "自定义材质",
            "这个材质名不是 YSM 的材质预设, YSM 主组件原样注册: 某个资源包的 materials/*.material 里要有它的定义(原版材质名如 entity_alphatest_one_sided 也行), 否则模型画不出来; 它不参与 YSM 的描边 / 泛光后处理, “背面剔除”选项对它不起作用。只想让负尺寸描边显形, 材质留“自动”(或选预设)再把“背面剔除”设为剔除即可。",
            PACK_GUIDE_DOC,
        );
    }
    if has(t, "选项写得不对") {
        return ex(
            "材质选项无效",
            "材质 / 背面剔除选项写法不对, 这个包按“自动”转换了(YSM 主组件按 Java 包判定)。材质名只能用英文字母、数字、_ 和 .; 逐键指定写成 键=材质, 多个用逗号隔开(default = 模型本体, ysm_glow = 发光组); 背面剔除只认 自动 / 剔除 / 不剔除。改好后重新转换。",
            PACK_GUIDE_DOC,
        );
    }
    if has(t, "PBR") && has(t, "缺失") {
        return ex(
            "PBR 贴图缺失",
            "ysm.json 声明的 PBR 法线/高光贴图在 Java 包里找不到(Java 光影包同样拿不到), 这项声明已从产物 ysm.json 去掉, 按没有这张图处理; 补上文件后重新转换。",
            PACK_GUIDE_DOC,
        );
    }
    if has(t, "滑铲动画缺失") {
        return ex(
            "基线滑铲动画缺失",
            "生成 java_default 基线时找不到旧版枪械兼容的滑铲动画(YSM 工程的 ysm_rp/animations/compat/ysm_tacz), 滑铲这个基岩扩展状态没有默认动画; 只在 YSM 主组件工程里跑 baseline 时出现, 转换普通模型包不受影响。",
            ANIMATION_MECHANISM_DOC,
        );
    }
    if has(t, "源音频缺失") {
        return ex(
            "Java 包缺音频文件",
            "动画 sound_effects 关键帧引用的音频在 Java 包的音频目录里找不到(Java 同样不出声, 多见于从别的包整份拷来的动画), 这些关键帧已删掉、不登记, 表现与 Java 一致; 要出声就把 ogg 补进 Java 包的 sounds 目录后重新转换。",
            PACK_GUIDE_DOC,
        );
    }
    if has(t, "头像") && has(t, "格式") {
        return ex(
            "作者头像格式不支持",
            "Java 头像支持 PNG / JPEG / WEBP / AVIF, 基岩贴图只认 PNG / JPEG(转换器没有 WEBP / AVIF 解码器), 这位作者在模型信息页显示 YSM 默认头像; 用看图或画图工具另存为 PNG, 把 ysm.json 的 avatar 改指向它后重新转换。",
            PORT_TUTORIAL_DOC,
        );
    }
    if has(t, "缺失")
        && ["动画", "几何", "贴图", "控制器", "图片"]
            .iter()
            .any(|part| has(t, part))
    {
        return ex(
            "Java 包声明的文件不存在",
            "ysm.json 声明的文件在 Java 包里找不到(Java 同样加载失败), 已跳过; 补上文件后重新转换, 或从 ysm.json 删掉声明。",
            PORT_TUTORIAL_DOC,
        );
    }
    if has(t, "音频") || has_ignore_case(t, "ogg") {
        return ex(
            "音频三件套",
            "动画音效要 ogg + sound_definitions + files.player.sound_effect 三处齐全才会响, 缺一无声。",
            PACK_GUIDE_DOC,
        );
    }
    if t.starts_with("骨骼名改名") {
        return ex(
            "骨骼名改名",
            "基岩动画文件里的骨骼名只能用英文字母、数字和 _ . -, 带空格、中文等的骨骼名只要有一个, 整份动画文件就加载不了(模型停在绑定姿态)。这些骨骼已在几何和全部动画里同表改名(中文转拼音), 动画照常对得上骨骼; 想保留可读的名字, 可在 Java 包里先改成英文名再转换。",
            PORT_TUTORIAL_DOC,
        );
    }
    if has(t, "非 ASCII") || has(t, "拼音") {
        return ex(
            "标识符转拼音",
            "中文动画名/骨骼名已转成拼音(基岩 ID 只认 ASCII), 引用同步改写; 若转出的名字难读可在 Java 包里先改英文名。",
            PORT_TUTORIAL_DOC,
        );
    }
    if has(t, "?? 默认值") {
        return ex(
            "变量默认值",
            "基线动画读到的 ?? 默认值不在本包初始化表, 运行时按 0 读; 一般无害。",
            ANIMATION_MECHANISM_DOC,
        );
    }
    if has(t, "模组联动") {
        return ex(
            "模组联动动画已跳过",
            "slashblade/parcool 等第三方模组的条件动画基岩无对应物品, 缺省不转换; 确需携带勾选“携带模组联动动画”。枪械(tacz)动画不在此列, 缺省就转换, 由主组件对接基岩枪械模组。",
            PORT_TUTORIAL_DOC,
        );
    }
    if has(t, "合集封面") && has(t, "1MB") {
        return ex(
            "合集封面未带上",
            "合集目录的 ysm-pack.png 超过 1MB(Java 同样拒收), 文件夹改用默认封面; 把图压到 1MB 以内(Java 口径 52x90)后重新转换。",
            PACK_GUIDE_DOC,
        );
    }
    if t.starts_with("java_state(") {
        return ex(
            "主组件运行层",
            "本包用到 YSM 主组件运行层维护的量(天气/血量/露天/roaming 存档与多人同步/药水附魔探针等), 声明已写进 ysm.json 的 java_state; 要与同期或更新的 YSM 主组件一起使用。",
            MOLANG_MAPPING_DOC,
        );
    }
    match level {
        "error" => ex(
            "错误",
            "内核报错, 该包可能未完整落盘; 看完整日志定位。",
            PORT_TUTORIAL_DOC,
        ),
        "warn" => ex(
            "警告",
            "内核发现与 Java 行为有出入的地方, 建议进游戏核对该项。",
            PORT_TUTORIAL_DOC,
        ),
        _ => ex(
            "提醒",
            "内核留痕: 转换做了降级或近似, 建议进游戏核对。",
            PORT_TUTORIAL_DOC,
        ),
    }
}

pub fn explain_validation(level: &str, text: &str) -> Explanation {
    if has(text, "缺 entities 文件夹") {
        return ex(
            "行为包不会被挂载",
            "网易按 entities 文件夹识别行为包, 缺它时 MC Studio 测试与正式游戏只挂资源包, 模型不会出现在选择界面(MCDK 调试测不出来); 点「修复产物」或重新转换会补上 entities/.gitkeep。",
            PACK_GUIDE_DOC,
        );
    }
    if has(text, "同类项合并") {
        return ex(
            "引擎同类项合并",
            "网易引擎会把加法里只差比较常量的两项(如 (v.a==1?x:0) + (v.a==2?x:0))当成同一项合并: 第一项翻倍、其余归零, 动作幅度翻倍或整段消失; 用 ysm_fix 重跑修复规则(自动改写成等价的 (v.a-1)==0), 只差在字符串比较上的需手工改结构。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(text, "sound_definitions") || has(text, "sound_effect") {
        return ex(
            "音频未登记",
            "效果键的定义不在 sounds/sound_definitions.json 或没在 ysm.json 的 files.player.sound_effect 登记, 该音效无声; 检查 ogg 是否随 Java 包一起提供。",
            PACK_GUIDE_DOC,
        );
    }
    if has_ignore_case(text, "catmullrom") {
        return ex(
            "catmullrom 关键帧",
            "引擎要求 catmullrom 帧前后窗口全是常量, 否则整段退化; 内核通常已降级, 残留项需手改关键帧。",
            PORT_TUTORIAL_DOC,
        );
    }
    if has(text, "裸读") {
        return ex(
            "运行期变量裸读",
            "主组件运行期维护的变量(输入状态/运行层状态/状态计时)读取时必须带 ?? 回落, 它们不做初始化; 用 ysm_fix 重跑修复规则, 或手工补上 ?? 默认值。",
            ANIMATION_MECHANISM_DOC,
        );
    }
    if ["占用变量", "伴生动画", "动画播完", "状态计时"]
        .iter()
        .any(|part| has(text, part))
    {
        return ex(
            "逐通道覆盖不完整",
            "Java 通道覆盖语义的配套件(占用变量/伴生动画/播完判据)对不上, 动画会叠加或卡在某个状态; 用 ysm_fix 重跑修复规则, 仍报错就重新转换该包。",
            ANIMATION_MECHANISM_DOC,
        );
    }
    if has(text, "Java 专有 token") || has(text, "残缺的") || has(text, "隐式乘法") {
        return ex(
            "Java 写法残留",
            "产物里还留着基岩不认识的 Java 写法, 整份文件会被引擎拒载; 用 ysm_fix 重跑修复规则, 或重新转换该包。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has_ignore_case(text, "molang") || has(text, "表达式") {
        return ex(
            "molang 语法红线",
            "一个表达式解析失败会让整份动画/控制器文件被引擎拒载(游戏里模型停在绑定姿态); 必须修到 0 错误。",
            MOLANG_MAPPING_DOC,
        );
    }
    if has(text, "资源 ID") || has(text, "大写") || has(text, "非法字符") {
        return ex(
            "资源 ID 红线",
            "资源 ID 含大写/非法字符, 整份文件拒载; 改名并同步全部引用。",
            PORT_TUTORIAL_DOC,
        );
    }
    if level == "error" {
        ex(
            "体检错误",
            "引擎红线, 进游戏前必须修; 错误文本里带文件与位置。",
            PORT_TUTORIAL_DOC,
        )
    } else {
        ex(
            "体检警告",
            "不致命, 但对应功能可能缺失或与 Java 不同。",
            PORT_TUTORIAL_DOC,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explains_known_kinds() {
        assert_eq!(explain_molang("zero", "ctrl.x(...)").category, "molang 置零");
        assert_eq!(explain_molang("func", "fn.foo(未知函数置零)").category, "未知函数置零");
        assert_eq!(
            explain_log("notice", "[!] 脚本控制器 a@player_ctrl_main.molang 未转换: 含计数器").category,
            "脚本控制器未转换"
        );
        assert_eq!(
            explain_log("warn", "[WARN] 动画缺失: animations/x.json").category,
            "Java 包声明的文件不存在"
        );
        // 内核 port_java_pack 的原话: 自定义函数(实验性)没开 / PBR 贴图缺失
        assert_eq!(
            explain_log("notice", "[!] 自定义函数 3 个文件没有转换(实验性功能, 缺省关): fn.* / ysm.play_sound 等调用置零, @player_init / @player_update / @sync 不转换; 需要时开启转换自定义函数(转换器勾选, 命令行 --java-functions)").category,
            "自定义函数未转换(实验性)"
        );
        assert_eq!(
            explain_log("warn", "[WARN] 贴图 textures/skin.png 的 PBR specular 贴图缺失, 已从 ysm.json 去掉这项声明: textures/pbr/skin_s.png").category,
            "PBR 贴图缺失"
        );
        // 基线(baseline)里的滑铲默认动画缺失不是"Java 包声明的文件不存在"
        assert_eq!(
            explain_log(
                "warn",
                "[WARN] 旧版枪械兼容的滑铲动画缺失, 没有生成基岩扩展状态的默认动画"
            )
            .category,
            "基线滑铲动画缺失"
        );
        // 动画引用的音频 Java 包里没有(从别的包拷来的动画): 不是"音频三件套"缺一项
        assert_eq!(
            explain_log("notice", "[!] 源音频缺失 1 个(Java 同样不出声), 引用它们的 sound_effects 关键帧 2 条已删掉、不登记: otoko_wa_tsurai_yo").category,
            "Java 包缺音频文件"
        );
        // 作者头像: WEBP / AVIF 基岩解不了(提醒转 PNG); 缺文件归入"Java 包声明的文件不存在"
        assert_eq!(
            explain_log("notice", "[!] 作者 纸板 的头像 avatar/zb.avif 格式是 AVIF, 基岩贴图只认 PNG / JPEG, 显示默认头像; 另存为 PNG、把 ysm.json 的 avatar 指向它后重新转换").category,
            "作者头像格式不支持"
        );
        assert_eq!(
            explain_log(
                "warn",
                "[WARN] 作者 缺图 的头像图片缺失: avatar/missing.png(Java 同样加载失败), 已删除声明, 显示默认头像"
            )
            .category,
            "Java 包声明的文件不存在"
        );
        // 骨骼名不合动画文件的命名规则: 与"标识符转拼音"分开说
        assert_eq!(
            explain_log("info", "骨骼名改名 1 个(动画文件里的骨骼名只认 A-Za-z0-9_.-, 空格 / 中文等一个就作废整份动画文件; 几何与动画同表改): music box→music_box").category,
            "骨骼名改名"
        );
        assert_eq!(
            explain_validation("warn", "x: 效果键 y 的定义 z 不在 sounds/sound_definitions.json").category,
            "音频未登记"
        );
        assert!(explain_molang("zero", "anything").doc.is_some());
    }

    #[test]
    fn explains_molang_by_kernel_wording() {
        let cases = [
            ("const", "is_maid -> 0.0", "molang 中性常量"),
            ("const", "ctrl.tac_gun_id -> ''", "TACZ 枪 ID 未接入"),
            (
                "map",
                "ctrl.tac_hold_gun -> (query.mod.ysm_tac_hold_gun>0.5)",
                "主组件运行层提供",
            ),
            ("const", "ctrl.parcool_state -> ''", "模组联动按未安装处理"),
            ("const", "ysm.perlin_noise(置常量)", "molang 中性常量"),
            ("func", "ysm.stop_sound(置常量)", "自定义函数音效置零"),
            ("func", "ysm.sync(置常量)", "自定义函数调用置零"),
            ("func", "ysm.keyboard(置常量)", "自定义函数调用置零"),
            ("map", "entity_type -> 'player'", "molang 中性常量"),
            ("map", "is_maid -> query.mod.ysm_is_maid", "车万女仆运行层提供"),
            (
                "map",
                "entity_type -> (query.mod.ysm_is_maid>0.5?'maid':'player')",
                "车万女仆运行层提供",
            ),
            (
                "map",
                "tlm.is_sitting -> query.mod.ysm_tlm_is_sitting",
                "车万女仆运行层提供",
            ),
            (
                "map",
                "tlm.is_begging -> query.mod.ysm_tlm_is_begging",
                "车万女仆运行层提供",
            ),
            ("const", "tlm.is_entity -> 1.0", "车万女仆联动按未安装处理"),
            ("const", "tlm.task_id -> ''", "车万女仆联动按未安装处理"),
            ("map", "weather -> query.mod.ysm_weather", "主组件运行层提供"),
            (
                "map",
                "ysm.bone_rot('HairB1').x -> 骨骼旋转回读变量",
                "主组件运行层提供",
            ),
            (
                "map",
                "ysm.equipped_enchantment_level(...) -> variable.ysm_pb_enchant_level_fire_aspect_24445b73(主包运行层探针)",
                "主组件运行层提供",
            ),
            ("map", "has_mainhand -> query.is_item_equipped(0)", "molang 映射"),
            ("map", "true/false 关键字 -> 1.0/0.0", "molang 映射"),
            ("func", "ysm.second_order(物理 → molang 状态积分)", "物理函数改写"),
            (
                "norm",
                "catmullrom 分段语义 Java(任一端) -> 基岩(出边)换算",
                "按 Java 语义规范化",
            ),
            (
                "skip",
                "控制器 player.parallel_5(initial_state default 不存在, Java 侧永不动作)",
                "Java 同样不挂载",
            ),
            (
                "skip",
                "ctrl.set_animation(不在脚本控制器里, Java 同样什么都不做)",
                "脚本控制器函数用在别处",
            ),
            ("skip", "query.debug_output(调试输出, 基岩无对应)", "调试输出已删除"),
            (
                "skip",
                "@defer on_defer@defer(事件尚未支持, Java 侧照常触发)",
                "函数事件未编译",
            ),
            (
                "skip",
                "ysm.play_sound(骨骼通道 / 条件里 Java 不发出)",
                "值位置的发出类调用",
            ),
            ("skip", "ysm.defer(延迟事件尚未支持, 本次调用删除)", "defer 未支持"),
            (
                "map",
                "ysm.sync -> 主包同步宿主(请求计数器, 经服务端转发)",
                "主组件运行层提供",
            ),
            (
                "map",
                "ysm.keyboard -> 主包运行层按键状态 query.mod(本机玩家)",
                "主组件运行层提供",
            ),
            (
                "map",
                "ysm.play_sound(horn) -> 主包音效宿主(请求计数器)",
                "主组件运行层提供",
            ),
            (
                "map",
                "@player_ctrl_parallel_5 碰墙抬手@player_ctrl_parallel_5 -> 逐帧执行体 ysm_fx_frame(脚本 + 动画播放器状态机) + 控制器",
                "主组件运行层提供",
            ),
            (
                "map",
                "@player_init init@player_init -> 变量初始化控制器 on_entry",
                "主组件运行层提供",
            ),
            (
                "map",
                "fn.halo_battery_indicator -> 调用处内联(纯表达式)",
                "molang 映射",
            ),
            (
                "tick",
                "每 tick 脚本动画(loop+显式 0 长度+timeline) -> 0.05s 循环",
                "每 tick 脚本动画",
            ),
        ];
        for (kind, label, category) in cases {
            assert_eq!(explain_molang(kind, label).category, category, "{kind}:{label}");
        }
    }

    #[test]
    fn explains_zero_by_kernel_wording() {
        let cases = [
            (
                "基岩解析不了的表达式按 Java 口径置 0 / 删除(Java 同样解析失败): parallel0 timeline/0.0/1: 此处需要操作数, 遇到 ';' <- [;",
                "作者原文解析失败",
            ),
            ("fn.move(自定义函数脚本, 基岩无对应)", "自定义函数置零"),
            ("fn.missing(函数不存在, Java 同样返回 null)", "自定义函数置零"),
            ("tlm.no_such_value(无映射)", "车万女仆联动置零"),
            (
                "ysm.bone_pos(...).成员(结构体成员访问, 基岩无对应, 连同调用一起置零)",
                "molang 置零",
            ),
            ("query.foo(引擎二进制无此名)", "基岩没有的查询"),
            ("残缺前缀 ysm./ctrl./fn.(点后无标识符, 直接删除)", "作者原文残句"),
            ("ctrl.hold(x,y)(槽位/分类无法转换)", "物品条件转换失败"),
            ("ctrl.wal(无映射)", "molang 置零"),
        ];
        for (label, category) in cases {
            assert_eq!(explain_molang("zero", label).category, category, "{label}");
        }
    }

    #[test]
    fn explains_warn_by_kernel_wording() {
        let cases = [
            (
                "sound_effects 原版音效 minecraft:item.trident.throw 两边命名不同, 定义名直通 item.trident.throw(基岩查无即无声, 需人工对照)",
                "原版音效命名差异",
            ),
            ("ysm.particle 位置为表达式, 落到实体原点 locator", "粒子位置近似"),
            ("ysm.abs_particle 世界绝对偏移近似为随身 locator", "粒子位置近似"),
            (
                "ysm.particle(minecraft:foo) 无基岩对照, 原名直通(引擎查无即无效果)",
                "粒子 ID 无对照",
            ),
            (
                "ysm.texture_name 比较的贴图 'skin_x' 不在本包贴图表里(== 恒假)",
                "贴图名比较恒不成立",
            ),
            (
                "ysm.effect_level(参数不是常量, 无法登记成探针, 按旧策略置常量)",
                "运行层探针未登记",
            ),
            (
                "query.foo(文档亦无, 证据缺失已保留 — 若文件作废先实机探针此名)",
                "引擎支持待确认",
            ),
            ("math.foo(证据缺失, 已保留 — 需人工确认)", "引擎支持待确认"),
            ("math.clamp(参数不足, 保留原样)", "math 参数个数不对"),
            ("ysm.second_order(键非字面量或参数残缺, 退化为取输入)", "molang 退化"),
        ];
        for (label, category) in cases {
            assert_eq!(explain_molang("warn", label).category, category, "{label}");
        }
    }

    #[test]
    fn detects_constant_replacement() {
        let cases = [
            ("a -> 0.0", true),
            ("a -> -1", true),
            ("a -> ''", true),
            ("a -> 'player'", true),
            ("a -> 1.0/0.0", false),
            ("a -> 'x' + 'y'", false),
            ("a -> query.is_item_equipped(0)", false),
            ("no arrow 0.0", false),
            ("a -> 1.", false),
            ("a -> .5", false),
        ];
        for (label, expected) in cases {
            assert_eq!(is_constant_replacement(label), expected, "{label}");
        }
    }

    #[test]
    fn explains_validation_by_kernel_wording() {
        let cases = [
            (
                "a/b.json animations/x: 输入状态变量 variable.ysm_block_hold 裸读(不做初始化, 必须带 ?? 回落): x",
                "运行期变量裸读",
            ),
            (
                "pack: 占用变量 variable.ysm_own_x 被读取但没有任何控制器状态 on_entry 写入(伴生动画永不让出通道)",
                "逐通道覆盖不完整",
            ),
            (
                "pack: sound_effects 效果键 ysm_snd_x 未在 ysm.json 的 files.player.sound_effect 登记(无声)",
                "音频未登记",
            ),
            (
                "a/b.json animations/x: 未转换的 Java 专有 token: ysm.foo",
                "Java 写法残留",
            ),
            (
                "a/b.json animations/x: 基岩解析不了的 molang(整份文件拒载): x",
                "molang 语法红线",
            ),
            (
                "a/b.json [animation.x] bones/RightArm/rotation/1.0/post/0: 引擎会把加法里只差比较常量的项当同类项合并(第一项翻倍、其余归零), 应改写成 (x-常量)==0: (v.qh==1?(10) : 0) + (v.qh==2?(10) : 0) -> ((v.qh-1)==0?(10) : 0) + ((v.qh-2)==0?(10) : 0)",
                "引擎同类项合并",
            ),
            (
                "行为包 my_models_bp: 缺 entities 文件夹, 网易按它识别行为包 —— 没有它 MC Studio 测试与正式游戏都不挂载这个行为包, 模型不会出现在选择界面(MCDK 按 manifest 建联接, 测不出来); 修复(fix)或重新转换会补上 entities/.gitkeep(D:\\out\\my_models_bp)",
                "行为包不会被挂载",
            ),
        ];
        for (text, category) in cases {
            assert_eq!(explain_validation("error", text).category, category, "{text}");
        }
    }

    #[test]
    fn riptide_is_not_treated_as_mod_compat() {
        assert!(explain_molang("const", "ctrl.riptide -> 0.0").advice.contains("激流"));
    }

    #[test]
    fn java_state_line_is_explained() {
        assert_eq!(
            explain_log(
                "info",
                "java_state(顶层) → 运行层状态 weather/open_air, roaming 变量 13 个(存档 + 多人同步)"
            )
            .category,
            "主组件运行层"
        );
    }

    #[test]
    fn material_lines_are_explained() {
        let custom = explain_log(
            "notice",
            "[!] 自定义材质 my_toon: 不是 YSM 材质预设, 主组件原样注册 —— 资源包的 materials/*.material 里要有它的定义",
        );
        assert_eq!(custom.category, "自定义材质");
        assert_eq!(custom.doc, Some(PACK_GUIDE_DOC));
        let invalid = explain_log(
            "warn",
            "[WARN] 材质选项写得不对, 按自动处理: 材质名只能用英文字母、数字、_ 和 .: a b",
        );
        assert_eq!(invalid.category, "材质选项无效");
    }
}
