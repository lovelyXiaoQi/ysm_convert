namespace YsmConvert.Core;

/// <summary>一条需要开发者过目的项目 + 它意味着什么、该去哪看。</summary>
public sealed record Explanation(string Category, string Advice, string? Doc);

/// <summary>
/// 把内核汇总里的告警文本翻译成"这是什么、要不要管、去哪查"。
/// 规则按内核(port_java_pack / validate_rp_animations)的固定措辞匹配, 措辞变了这里跟着改。
/// </summary>
public static class WarningCatalog
{
    public const string MolangMappingDoc = "ysm-java-molang-mapping.md";
    public const string PortTutorialDoc = "ysm-java-port-tutorial.md";
    public const string PackGuideDoc = "ysm-json-pack-guide.md";
    public const string AnimationMechanismDoc = "ysm-java-animation-mechanism.md";

    public static Explanation ExplainMolang(string kind, string label)
    {
        switch (kind)
        {
            case "zero":
                if (label.Contains("ctrl.ride", StringComparison.Ordinal))
                    return new("molang 置零", "Java 的骑乘判定(passenger/#tag 形态)基岩没有对应查询, 该表达式按 0 求值; 若动画依赖它, 改用 query.is_riding 一类的基岩查询手写。", MolangMappingDoc);
                if (label.Contains("结构体成员", StringComparison.Ordinal))
                    return new("molang 置零", "ysm.bone_rot('骨骼').x 这类结构体成员访问基岩没有, 连同调用一起置 0; 需要骨骼朝向时改读 query.mod.ysm_* 自定义变量或重写动画。", MolangMappingDoc);
                return new("molang 置零", "Java 专有查询/函数在基岩没有对应, 已替换成常量 0; 检查该动画是否因此失去驱动, 必要时按映射清单改写。", MolangMappingDoc);
            case "func":
                if (label.Contains("未知", StringComparison.Ordinal))
                    return new("未知函数置零", "Java 侧的自定义函数(fn.*/ctrl.*/ysm.*)内核不认识, 已置 0; 对照 Java 源码手工改写成基岩 molang。", MolangMappingDoc);
                if (label.Contains("物理", StringComparison.Ordinal))
                    return new("物理函数改写", "second_order/first_order 物理函数已改写成 molang 状态积分(v.ysm_so_<键>_y), 一般无需处理; 想调整手感改 ysm.json 里的参数后重新移植。", AnimationMechanismDoc);
                return new("函数替换", "Java 函数按映射表替换(取参数/置常量), 通常等价; 只在效果异常时回头核对。", MolangMappingDoc);
            case "warn":
                if (label.Contains("sound_effects", StringComparison.Ordinal) || label.Contains("音效", StringComparison.Ordinal))
                    return new("原版音效命名差异", "Java 版原版音效 ID 与基岩不同名, 内核把 Java 名直通进了 sound_definitions.json; 对照基岩原版音效表把该定义改成基岩的名字, 查无的名字在游戏里无声。", PackGuideDoc);
                return new("molang 退化", "参数残缺或键非字面量, 内核退化处理(保留原样/取输入); 检查 Java 源里的写法是否可简化成字面量。", MolangMappingDoc);
            case "lower":
                return new("动画名转小写", "基岩资源 ID 只认小写, 动画名与全部引用已同步转小写; 只有在 Java 里靠大小写区分两个同名动画时才需要改名。", PortTutorialDoc);
            case "map":
                return new("molang 映射", "按映射表完成的等价替换, 无需处理。", MolangMappingDoc);
            default:
                return new("molang", "内核留痕项, 按说明判断。", MolangMappingDoc);
        }
    }

    public static Explanation ExplainLog(string level, string text)
    {
        var t = text.TrimStart();
        if (t.Contains("脚本控制器", StringComparison.Ordinal) && t.Contains("未转换", StringComparison.Ordinal))
            return new("脚本控制器未转换", "Java 每帧脚本控制器含计数器/随机数/非常量赋值或属于 main/use/swing/parallel 通道, 内核放弃自动展开; 该通道姿态需手工写成基岩动画控制器。", AnimationMechanismDoc);
        if (t.Contains("缺失", StringComparison.Ordinal) && (t.Contains("动画", StringComparison.Ordinal) || t.Contains("几何", StringComparison.Ordinal) || t.Contains("贴图", StringComparison.Ordinal) || t.Contains("控制器", StringComparison.Ordinal) || t.Contains("图片", StringComparison.Ordinal)))
            return new("Java 包声明的文件不存在", "ysm.json 声明的文件在 Java 包里找不到(Java 同样加载失败), 已跳过; 补上文件后重新移植, 或从 ysm.json 删掉声明。", PortTutorialDoc);
        if (t.Contains("音频", StringComparison.Ordinal) || t.Contains("ogg", StringComparison.OrdinalIgnoreCase))
            return new("音频三件套", "动画音效要 ogg + sound_definitions + files.player.sound_effect 三处齐全才会响, 缺一无声。", PackGuideDoc);
        if (t.Contains("非 ASCII", StringComparison.Ordinal) || t.Contains("拼音", StringComparison.Ordinal))
            return new("标识符转拼音", "中文动画名/骨骼名已转成拼音(基岩 ID 只认 ASCII), 引用同步改写; 若转出的名字难读可在 Java 包里先改英文名。", PortTutorialDoc);
        if (t.Contains("?? 默认值", StringComparison.Ordinal))
            return new("变量默认值", "基线动画读到的 ?? 默认值不在本包初始化表, 运行时按 0 读; 一般无害。", AnimationMechanismDoc);
        if (t.Contains("模组联动", StringComparison.Ordinal))
            return new("模组联动动画已跳过", "tacz/slashblade 等第三方模组的条件动画基岩无对应物品, 缺省不移植; 确需携带勾选“携带模组联动动画”。", PortTutorialDoc);
        return level switch
        {
            "error" => new("错误", "内核报错, 该包可能未完整落盘; 看完整日志定位。", PortTutorialDoc),
            "warn" => new("警告", "内核发现与 Java 行为有出入的地方, 建议进游戏核对该项。", PortTutorialDoc),
            _ => new("提醒", "内核留痕: 转换做了降级或近似, 建议进游戏核对。", PortTutorialDoc),
        };
    }

    public static Explanation ExplainValidation(string level, string text)
    {
        if (text.Contains("sound_definitions", StringComparison.Ordinal))
            return new("音频未登记", "效果键的定义不在 sounds/sound_definitions.json, 该音效无声; 检查 ogg 是否随 Java 包一起提供。", PackGuideDoc);
        if (text.Contains("catmullrom", StringComparison.OrdinalIgnoreCase))
            return new("catmullrom 关键帧", "引擎要求 catmullrom 帧前后窗口全是常量, 否则整段退化; 内核通常已降级, 残留项需手改关键帧。", PortTutorialDoc);
        if (text.Contains("molang", StringComparison.OrdinalIgnoreCase) || text.Contains("表达式", StringComparison.Ordinal))
            return new("molang 语法红线", "一个表达式解析失败会让整份动画/控制器文件被引擎拒载(游戏里模型停在绑定姿态); 必须修到 0 错误。", MolangMappingDoc);
        if (text.Contains("资源 ID", StringComparison.Ordinal) || text.Contains("大写", StringComparison.Ordinal))
            return new("资源 ID 红线", "资源 ID 含大写/非法字符, 整份文件拒载; 改名并同步全部引用。", PortTutorialDoc);
        return level == "error"
            ? new("体检错误", "引擎红线, 进游戏前必须修; 错误文本里带文件与位置。", PortTutorialDoc)
            : new("体检警告", "不致命, 但对应功能可能缺失或与 Java 不同。", PortTutorialDoc);
    }
}
