namespace YsmConvert.Core;

/// <summary>一条需要开发者过目的项目 + 它意味着什么、该去哪看。</summary>
public sealed record Explanation(string Category, string Advice, string? Doc);

/// <summary>
/// 把内核汇总里的告警文本翻译成"这是什么、要不要管、去哪查"。
/// 规则按内核(port_java_pack / java_runtime_bindings / validate_rp_animations)的固定措辞匹配, 措辞变了这里跟着改。
/// molang 类别由内核宿主 port_cli.SplitMolangLabel 给出: map 等价替换 / const 中性常量 / zero 置零 / func 函数改写 /
/// warn 退化 / lower 动画名转小写 / norm 规范化 / skip 跳过 / tick 每 tick 脚本。
/// </summary>
public static class WarningCatalog
{
    public const string MolangMappingDoc = "ysm-java-molang-mapping.md";
    public const string PortTutorialDoc = "ysm-java-port-tutorial.md";
    public const string PackGuideDoc = "ysm-json-pack-guide.md";
    public const string AnimationMechanismDoc = "ysm-java-animation-mechanism.md";

    /// <summary>molang 留痕类别(与 port_cli 的 molang.kind 一致)。</summary>
    public static readonly IReadOnlyList<string> MolangKinds =
        ["map", "const", "zero", "func", "warn", "lower", "norm", "skip", "tick"];

    public static bool IsMolangKind(string? kind) => kind is not null && MolangKinds.Contains(kind);

    public static Explanation ExplainMolang(string kind, string label)
    {
        switch (kind)
        {
            case "const":
                return ExplainConstant(label);
            case "zero":
                return ExplainZero(label);
            case "func":
                if (Has(label, "未知"))
                    return new("未知函数置零", "Java 侧的函数(ysm.*/ctrl.*)内核不认识, 已置 0; 对照 Java 源码手工改写成基岩 molang。", MolangMappingDoc);
                if (Has(label, "物理"))
                    return new("物理函数改写", "second_order/first_order 物理函数已改写成 molang 状态积分(v.ysm_so_<键>_y), 一般无需处理; 想调整手感改 ysm.json 里的参数后重新转换。", AnimationMechanismDoc);
                if (label.EndsWith("(置常量)", StringComparison.Ordinal))
                    return ExplainConstant(label);
                return new("函数替换", "Java 函数按映射表取参数(物理函数的键不是字面量等改写不了的情况, 丢掉平滑保留运动), 通常可用; 只在效果异常时回头核对。", MolangMappingDoc);
            case "warn":
                return ExplainWarn(label);
            case "lower":
                return new("动画名转小写", "基岩资源 ID 只认小写, 动画名与全部引用(含 ysm.json 的轮盘/预览动画)已同步转小写, 一般无需处理; 只有在 Java 里靠大小写区分两个同名动画时才需要先在 Java 包里改名。", PortTutorialDoc);
            case "map":
                if (IsConstantReplacement(label))
                    return ExplainConstant(label);
                if (IsRuntimeMapping(label))
                    return new("主组件运行层提供", "这个 Java 量由 YSM 主组件运行时逐帧/逐 tick 提供真值(视角/运动量/天气/血量/露天/roaming 存档同步/药水附魔探针/骨骼旋转回读等), 无需处理; 产物要与同期或更新的 YSM 主组件一起使用。", MolangMappingDoc);
                return new("molang 映射", "按映射表完成的等价替换, 无需处理。", MolangMappingDoc);
            case "norm":
                return new("按 Java 语义规范化", "动画/控制器字段按 Java 的运行语义换算成基岩写法(循环长度、catmullrom 分段、过渡归属等), 无需处理。", AnimationMechanismDoc);
            case "skip":
                return new("Java 同样不挂载", "这个控制器在 Java 里也不会动作(非通道名或初始状态不存在), 转换时一并跳过, 与 Java 表现一致。", AnimationMechanismDoc);
            case "tick":
                return new("每 tick 脚本动画", "Java 显式 0 长度的循环动画是\"每 tick 执行一次\"的脚本, 已换成 0.05 秒循环, 无需处理。", AnimationMechanismDoc);
            default:
                return new("molang", "内核留痕项, 按说明判断。", MolangMappingDoc);
        }
    }

    /// <summary>"名字 -> 字面量": Java 专有量按中性常量处理(与 port_cli._CONST_REPLACEMENT 同口径)。</summary>
    public static bool IsConstantReplacement(string label)
    {
        var arrow = label.IndexOf(" -> ", StringComparison.Ordinal);
        if (arrow < 0) return false;
        var replacement = label[(arrow + 4)..].Trim();
        if (replacement.Length >= 2 && replacement[0] == '\'' && replacement[^1] == '\'')
            return replacement.IndexOf('\'', 1) == replacement.Length - 1;
        return System.Text.RegularExpressions.Regex.IsMatch(replacement, @"^-?[0-9]+(?:\.[0-9]+)?$");
    }

    private static bool IsRuntimeMapping(string label) =>
        Has(label, "主包运行层") || Has(label, "骨骼旋转回读") || Has(label, "query.mod.ysm_")
        || Has(label, "variable.ysm_env_") || Has(label, "variable.ysm_pb_") || Has(label, "variable.ysm_elytra_rot_")
        || Has(label, "v.roaming");

    private static Explanation ExplainConstant(string label)
    {
        if (Has(label, "ctrl.tac_"))
            return new("TACZ 联动未接入", "ctrl.tac_* 按\"没拿枪\"处理(类型 ''、其余 0): 网易侧 TACZ 联动的数据在结构体变量 variable.tac.* 里, 实机确认可读之前不映射。拿枪相关的姿态在基岩不会出现。", MolangMappingDoc);
        if (Has(label, "riptide"))
            return new("molang 中性常量", "基岩没有激流(三叉戟冲刺)查询, ctrl.riptide / ysm.is_riptide 按 0 处理, 激流姿态在基岩不会出现。", MolangMappingDoc);
        if (Has(label, "ctrl."))
            return new("模组联动按未安装处理", "这是 Java 端某个模组(跑酷/拔刀剑/背包/乐器等)的联动变量, 按\"该模组没装\"的缺省值处理(字符串 ''、数值 0); 基岩没有这个模组, 对应动画不会触发。", MolangMappingDoc);
        if (Has(label, "particle"))
            return new("molang 中性常量", "通道表达式里的 ysm.particle 基岩没有对应, 按 0 处理; 只有 timeline 里的 particle 调用会转成基岩 particle_effects 关键帧。", MolangMappingDoc);
        if (Has(label, "sound"))
            return new("molang 中性常量", "play_sound/stop_sound 族在基岩表达式里没有对应, 按 0 处理; 动画音效请用 sound_effects 关键帧(转换时自动拷 ogg 并登记)。", MolangMappingDoc);
        if (Has(label, "ysm.bone_"))
            return new("molang 中性常量", "bone_pos/bone_scale/bone_pivot_abs/bone_color 等骨骼函数基岩没有对应, 按 0 处理; 只有 ysm.bone_rot('骨骼').x/y/z(骨骼名是字面量)能转成骨骼旋转回读。", MolangMappingDoc);
        if (Has(label, "effect_level") || Has(label, "enchantment_level") || Has(label, "relative_block_name"))
            return new("molang 中性常量", "药水/附魔/相对方块查询的参数不是常量, 登记不成主组件运行层探针, 按旧策略置常量(等级 0 / 方块名 ''); 改成字面量参数后重新转换。", MolangMappingDoc);
        return new("molang 中性常量", "Java 专有量在基岩没有对应, 已按语义中性常量处理(如 is_maid = 0、attack_speed 取原版玩家默认值 4); 动画里依赖它变化的效果在基岩不会出现, 一般无需处理。完整清单见映射文档第五节。", MolangMappingDoc);
    }

    private static Explanation ExplainZero(string label)
    {
        if (Has(label, "Java 同样解析失败") || Has(label, "基岩解析不了"))
            return new("作者原文解析失败", "这个值在 Java 里同样解析失败(Java 只作废这一个值, 按 0 算), 已按同样口径处置: 分量/条件落 0, timeline/on_entry 条目删除, 游戏里表现与 Java 一致。多半是作者笔误, 想修就改 Java 源再重新转换。", MolangMappingDoc);
        if (Has(label, "ctrl.ride"))
            return new("molang 置零", "Java 的骑乘判定(passenger/#tag 形态)基岩没有对应查询, 该表达式按 0 求值; 若动画依赖它, 改用 query.is_riding 一类的基岩查询手写。", MolangMappingDoc);
        if (Has(label, "结构体成员"))
            return new("molang 置零", "返回结构体(Vec3)的函数再取成员 .x/.y/.z 基岩没有对应, 连同调用一起置 0; 其中 ysm.bone_rot('骨骼').x/y/z(骨骼名是字面量)已转成骨骼旋转回读, 剩下的是 bone_pos/bone_scale 或参数不是字面量的调用, 需要时重写动画。", MolangMappingDoc);
        if (label.StartsWith("fn.", StringComparison.Ordinal))
            return new("自定义函数置零", "functions/*.molang 自定义函数(过程式脚本)基岩表达式里没有对应, 已置 0; 若动画依赖函数的返回值, 需要把函数逻辑手写成 molang 语句。", MolangMappingDoc);
        if (label.StartsWith("tlm.", StringComparison.Ordinal))
            return new("车万女仆联动置零", "tlm.* 是车万女仆联动变量, 玩家模型上本来就取不到值, 置 0 与 Java 玩家侧一致, 一般无需处理。", MolangMappingDoc);
        if (Has(label, "引擎二进制无此名"))
            return new("基岩没有的查询", "网易基岩引擎没有这个查询(引用它会让整份文件被拒载), 已置 0; 对照映射文档改写成基岩有的查询。", MolangMappingDoc);
        if (Has(label, "残缺前缀"))
            return new("作者原文残句", "Java 源里有 ysm./ctrl./fn. 后面没接名字的残句(Java 同样解析不了), 直接删除; 多半是作者笔误。", MolangMappingDoc);
        if (Has(label, "槽位/分类无法转换"))
            return new("物品条件转换失败", "ctrl.hold/swing/use/armor 的槽位或物品分类参数转不成基岩判定, 该条件按 0 处理; 参数改成 Java 标准写法($物品ID / #标签 / :分类)后重新转换。", MolangMappingDoc);
        if (Has(label, "ysm.particle(ID 非字面量"))
            return new("粒子未转换", "粒子 ID 不是字面量, 转不成基岩 particle_effects 关键帧, 这次调用被删除; 改成字面量 ID 后重新转换。", MolangMappingDoc);
        if (Has(label, "无映射"))
            return new("molang 置零", "Java 专有查询/变量在映射表里没有对应, 已替换成 0(可能是新版 Java YSM 新增的名字或作者笔误); 检查该动画是否因此失去驱动, 必要时按映射文档改写。", MolangMappingDoc);
        return new("molang 置零", "Java 专有查询/函数在基岩没有对应, 已替换成常量 0; 检查该动画是否因此失去驱动, 必要时按映射文档改写。", MolangMappingDoc);
    }

    private static Explanation ExplainWarn(string label)
    {
        if (Has(label, "sound_effects") || Has(label, "音效"))
            return new("原版音效命名差异", "Java 版原版音效 ID 与基岩不同名, 转换时把 Java 名直通进了 sound_definitions.json; 对照基岩原版音效表把该定义改成基岩的名字, 查无的名字在游戏里无声。", PackGuideDoc);
        if (Has(label, "ysm.particle") && Has(label, "位置为表达式"))
            return new("粒子位置近似", "粒子生成位置是表达式, 基岩粒子关键帧只能挂在 locator 上, 已落到实体原点; 偏差明显时在几何里加 locator 并手改该关键帧。", MolangMappingDoc);
        if (Has(label, "abs_particle"))
            return new("粒子位置近似", "Java 在世界绝对坐标生成粒子, 基岩只能挂在随身 locator 上, 位置是近似。", MolangMappingDoc);
        if (Has(label, "ysm.particle") && Has(label, "无基岩对照"))
            return new("粒子 ID 无对照", "这个粒子 ID 没有基岩对照, 原名直通; 基岩查无该粒子时游戏里没有效果, 需要换成基岩原版粒子或自带粒子定义。", MolangMappingDoc);
        if (Has(label, "texture_name"))
            return new("贴图名比较恒不成立", "Java 源在比较一个本包贴图表里没有的贴图名, 与 Java 一样恒不成立(== 恒假 / != 恒真); 多半是作者笔误或删掉的皮肤。", MolangMappingDoc);
        if (Has(label, "登记成探针"))
            return new("运行层探针未登记", "药水/附魔/相对方块查询要常量参数才能登记成主组件运行层探针; 这里的参数是表达式, 按旧策略置常量(等级 0 / 方块名 ''), 改成字面量参数后重新转换。", MolangMappingDoc);
        if (Has(label, "证据缺失"))
            return new("引擎支持待确认", "这个查询/函数在网易引擎里是否存在缺少实机证据, 已原样保留; 若进游戏发现这份动画整份不生效(模型停在绑定姿态), 先按映射文档改写它。", MolangMappingDoc);
        if (Has(label, "参数不足"))
            return new("math 参数个数不对", "math 函数参数个数与基岩要求不符, 原样保留; 基岩按参数个数校验, 不对会让整份文件被拒载 —— 对照 Java 源修正。", MolangMappingDoc);
        return new("molang 退化", "参数残缺或键非字面量, 内核退化处理(保留原样/取输入); 检查 Java 源里的写法是否可简化成字面量。", MolangMappingDoc);
    }

    public static Explanation ExplainLog(string level, string text)
    {
        var t = text.TrimStart();
        if (t.Contains("脚本控制器", StringComparison.Ordinal) && t.Contains("未转换", StringComparison.Ordinal))
            return new("脚本控制器未转换", "Java 每帧脚本控制器含计数器/随机数/非常量赋值或属于 main/use/swing/parallel 通道, 内核放弃自动展开; 该通道姿态需手工写成基岩动画控制器。", AnimationMechanismDoc);
        if (t.Contains("缺失", StringComparison.Ordinal) && (t.Contains("动画", StringComparison.Ordinal) || t.Contains("几何", StringComparison.Ordinal) || t.Contains("贴图", StringComparison.Ordinal) || t.Contains("控制器", StringComparison.Ordinal) || t.Contains("图片", StringComparison.Ordinal)))
            return new("Java 包声明的文件不存在", "ysm.json 声明的文件在 Java 包里找不到(Java 同样加载失败), 已跳过; 补上文件后重新转换, 或从 ysm.json 删掉声明。", PortTutorialDoc);
        if (t.Contains("音频", StringComparison.Ordinal) || t.Contains("ogg", StringComparison.OrdinalIgnoreCase))
            return new("音频三件套", "动画音效要 ogg + sound_definitions + files.player.sound_effect 三处齐全才会响, 缺一无声。", PackGuideDoc);
        if (t.Contains("非 ASCII", StringComparison.Ordinal) || t.Contains("拼音", StringComparison.Ordinal))
            return new("标识符转拼音", "中文动画名/骨骼名已转成拼音(基岩 ID 只认 ASCII), 引用同步改写; 若转出的名字难读可在 Java 包里先改英文名。", PortTutorialDoc);
        if (t.Contains("?? 默认值", StringComparison.Ordinal))
            return new("变量默认值", "基线动画读到的 ?? 默认值不在本包初始化表, 运行时按 0 读; 一般无害。", AnimationMechanismDoc);
        if (t.Contains("模组联动", StringComparison.Ordinal))
            return new("模组联动动画已跳过", "tacz/slashblade 等第三方模组的条件动画基岩无对应物品, 缺省不转换; 确需携带勾选“携带模组联动动画”。", PortTutorialDoc);
        if (t.Contains("合集封面", StringComparison.Ordinal) && t.Contains("1MB", StringComparison.Ordinal))
            return new("合集封面未带上", "合集目录的 ysm-pack.png 超过 1MB(Java 同样拒收), 文件夹改用默认封面; 把图压到 1MB 以内(Java 口径 52x90)后重新转换。", PackGuideDoc);
        if (t.StartsWith("java_state(", StringComparison.Ordinal))
            return new("主组件运行层", "本包用到 YSM 主组件运行层维护的量(天气/血量/露天/roaming 存档与多人同步/药水附魔探针等), 声明已写进 ysm.json 的 java_state; 要与同期或更新的 YSM 主组件一起使用。", MolangMappingDoc);
        return level switch
        {
            "error" => new("错误", "内核报错, 该包可能未完整落盘; 看完整日志定位。", PortTutorialDoc),
            "warn" => new("警告", "内核发现与 Java 行为有出入的地方, 建议进游戏核对该项。", PortTutorialDoc),
            _ => new("提醒", "内核留痕: 转换做了降级或近似, 建议进游戏核对。", PortTutorialDoc),
        };
    }

    public static Explanation ExplainValidation(string level, string text)
    {
        if (text.Contains("缺 entities 文件夹", StringComparison.Ordinal))
            return new("行为包不会被挂载", "网易按 entities 文件夹识别行为包, 缺它时 MC Studio 测试与正式游戏只挂资源包, 模型不会出现在选择界面(MCDK 调试测不出来); 点「修复产物」或重新转换会补上 entities/.gitkeep。", PackGuideDoc);
        if (text.Contains("sound_definitions", StringComparison.Ordinal) || text.Contains("sound_effect", StringComparison.Ordinal))
            return new("音频未登记", "效果键的定义不在 sounds/sound_definitions.json 或没在 ysm.json 的 files.player.sound_effect 登记, 该音效无声; 检查 ogg 是否随 Java 包一起提供。", PackGuideDoc);
        if (text.Contains("catmullrom", StringComparison.OrdinalIgnoreCase))
            return new("catmullrom 关键帧", "引擎要求 catmullrom 帧前后窗口全是常量, 否则整段退化; 内核通常已降级, 残留项需手改关键帧。", PortTutorialDoc);
        if (text.Contains("裸读", StringComparison.Ordinal))
            return new("运行期变量裸读", "主组件运行期维护的变量(输入状态/运行层状态/状态计时)读取时必须带 ?? 回落, 它们不做初始化; 用 ysm_fix 重跑修复规则, 或手工补上 ?? 默认值。", AnimationMechanismDoc);
        if (text.Contains("占用变量", StringComparison.Ordinal) || text.Contains("伴生动画", StringComparison.Ordinal)
            || text.Contains("动画播完", StringComparison.Ordinal) || text.Contains("状态计时", StringComparison.Ordinal))
            return new("逐通道覆盖不完整", "Java 通道覆盖语义的配套件(占用变量/伴生动画/播完判据)对不上, 动画会叠加或卡在某个状态; 用 ysm_fix 重跑修复规则, 仍报错就重新转换该包。", AnimationMechanismDoc);
        if (text.Contains("Java 专有 token", StringComparison.Ordinal) || text.Contains("残缺的", StringComparison.Ordinal) || text.Contains("隐式乘法", StringComparison.Ordinal))
            return new("Java 写法残留", "产物里还留着基岩不认识的 Java 写法, 整份文件会被引擎拒载; 用 ysm_fix 重跑修复规则, 或重新转换该包。", MolangMappingDoc);
        if (text.Contains("molang", StringComparison.OrdinalIgnoreCase) || text.Contains("表达式", StringComparison.Ordinal))
            return new("molang 语法红线", "一个表达式解析失败会让整份动画/控制器文件被引擎拒载(游戏里模型停在绑定姿态); 必须修到 0 错误。", MolangMappingDoc);
        if (text.Contains("资源 ID", StringComparison.Ordinal) || text.Contains("大写", StringComparison.Ordinal) || text.Contains("非法字符", StringComparison.Ordinal))
            return new("资源 ID 红线", "资源 ID 含大写/非法字符, 整份文件拒载; 改名并同步全部引用。", PortTutorialDoc);
        return level == "error"
            ? new("体检错误", "引擎红线, 进游戏前必须修; 错误文本里带文件与位置。", PortTutorialDoc)
            : new("体检警告", "不致命, 但对应功能可能缺失或与 Java 不同。", PortTutorialDoc);
    }

    private static bool Has(string text, string part) => text.Contains(part, StringComparison.Ordinal);
}
