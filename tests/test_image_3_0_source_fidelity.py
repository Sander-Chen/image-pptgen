import pytest

import pipeline


SIMULATED_HISTORY_SLIDE = """
当时主要的经济数据足以说明其强盛（此处为模拟数据，用以佐证盛世概念）：

| 指标 | 唐朝盛世时期（模拟估算） | 说明 |
| :--- | :--- | :--- |
| 世界GDP占比 | 约 25% - 30% | 这一比例远超当时其他文明古国 |
| 长安人口 | 超过 100 万 | 真正的世界第一大都会 |
""".strip()

R55_ORDINARY_PROSE = """
说完对绍琳点点头，示意她继续
当同伴革命时，她们必须表现得更革命，至少要同样革命
""".strip()

QUALIFIED_QUANTITATIVE_CLAIMS = """
此处为模拟数据，用于说明趋势
| 指标 | 模拟估算 | 说明 |
| :--- | :--- | :--- |
| 世界GDP占比 | 约 25% - 30% | 模拟区间 |
| 长安人口 | 超过 100 万 | 模拟估算 |
| 改革项目 | 至少 3 项 | 最低数量 |
| 成本偏差 | 不超过 10% | 上限 |
""".strip()

R57_FACTUAL_ON_SLIDE_TEXT = (
    "十五岁女孩在枪火中倒下并被悬挂为靶；几千人参加的批斗会持续近两个小时；"
    "首都四十天内有一千七百多名批斗对象被活活打死。"
)
R57_SOURCE_QUALIFIER_LINE = (
    "估算：2000 人参加的批斗会已进行近两个小时；"
    "首都四十天内一千七百多名批斗对象被活活打死。"
)

CIRCLE_ESTIMATE_SLIDE = (
    "根据招股说明书，2024年Circle总营收16.76亿美元中有99%以上"
    "（约16.61亿美元）属于储备利息收入。"
)
CIRCLE_ESTIMATE_OMITTED_BLUEPRINT = (
    "<SlideBlueprint><Instantiation_Copy>"
    "<On_Slide_Text>2024年Circle总营收16.76亿美元中有99%以上"
    "（16.61亿美元）属于储备利息收入。</On_Slide_Text>"
    "</Instantiation_Copy></SlideBlueprint>"
)

R57_FAILED_SLIDE_CONTENT = f"""
# 武斗洪流与批斗大会

{R57_FACTUAL_ON_SLIDE_TEXT}
{R57_SOURCE_QUALIFIER_LINE}
""".strip()

R57_FAILED_BLUEPRINT = """
<SlideBlueprint>
  <Deck_Consistency>
    <Style_Anchor_Extraction>继承黑色深空底色、暗红历史创伤与冷白星光。</Style_Anchor_Extraction>
    <Deck_Style_DNA>
      <Colour_Role_Syntax>黑灰为基础，暗红表示历史创伤，冷白用于文字。</Colour_Role_Syntax>
      <Shape_And_Line_Syntax>信号线、枪口火线或裂缝线采用单一斜向动力线。</Shape_And_Line_Syntax>
      <Material_And_Light_Physics>废墟表面粗糙，金属边缘出现微弱冷白反光。</Material_And_Light_Physics>
    </Deck_Style_DNA>
    <This_Slide_Style_Delta>改为校园操场与铁门下坠现场，以强化历史暴力。</This_Slide_Style_Delta>
    <Text_Safe_Zones_And_Contrast_Guards>人物、铁门尖刺和枪火不得遮挡任何文字。</Text_Safe_Zones_And_Contrast_Guards>
  </Deck_Consistency>
  <Information_Physics>
    <OneSentence_Thesis>当疯狂蔓延到校园，个体生命便被异化为旗帜、战利品和靶子。</OneSentence_Thesis>
    <Support_Units>十五岁女孩在枪火中倒下并被悬挂为靶；几千人参加的批斗会持续近两个小时；首都四十天内有一千七百多名批斗对象被活活打死。</Support_Units>
    <What_To_Remember_In_5s>疯狂把城市、群体和人的意识同时变成暴力机器。</What_To_Remember_In_5s>
  </Information_Physics>
  <Attention_And_Density_Budget>
    <Noise_Ceiling_Rules>枪火、旗帜、铁门尖刺和烟尘只服务于主线。</Noise_Ceiling_Rules>
  </Attention_And_Density_Budget>
  <Spatial_Engineering>
    <Spatial_Axes_Semantics>中央偏右为十五岁女孩、旗帜与铁门，承担唯一视觉中心。</Spatial_Axes_Semantics>
    <Visual_Mass_Map>女孩与旗帜获得最高轮廓对比，左侧文字区保持黑场。</Visual_Mass_Map>
    <Module_Blueprint>记忆句模块 → 人被变成旗帜、战利品和靶子 → 暗红突出“靶子”；事实标签模块 → “十五岁”“几千人参加”“近两个小时”“四十天内一千七百多名批斗对象”。</Module_Blueprint>
    <Reading_Path_Control>沿旗帜与铁门的斜线看到人物，最后落到底部限定事实。</Reading_Path_Control>
  </Spatial_Engineering>
  <Sensory_Language_Definition>
    <Material_And_Light_Physics>破碎混凝土、锈蚀铁门与烧焦布料构成前景。</Material_And_Light_Physics>
    <Form_Grammar>以尖刺、断裂、下坠和斜向拉扯构成形态语法。</Form_Grammar>
  </Sensory_Language_Definition>
  <Encoding_Plan>
    <Quantitative_Handling>保留“十五岁”“几千人参加”“近两个小时”“四十天”“一千七百多名”等原始表述及其限定词。</Quantitative_Handling>
    <Qualitative_Handling>将“旗帜、战利品、靶子、并行运算CPU”转化为视觉隐喻和短标签。</Qualitative_Handling>
  </Encoding_Plan>
  <Instantiation_Copy>
    <On_Slide_Text>
      <Main>疯狂如何吞没一个城市</Main>
      <Key_Line>人被变成旗帜、战利品和靶子</Key_Line>
      <Fact_One>十五岁｜从楼顶坠落</Fact_One>
      <Fact_Two>几千人参加｜估算：2000 人参加的批斗会已进行近两个小时</Fact_Two>
      <Fact_Three>首都｜四十天内一千七百多名批斗对象被活活打死</Fact_Three>
    </On_Slide_Text>
  </Instantiation_Copy>
</SlideBlueprint>
""".strip()


def test_source_qualifier_guard_preserves_simulation_and_estimate_context():
    guard = pipeline._source_qualifier_guard(SIMULATED_HISTORY_SLIDE)

    assert "此处为模拟数据，用以佐证盛世概念" in guard
    assert "唐朝盛世时期（模拟估算）" in guard
    assert "不得把限定数值改写成无条件的历史事实" in guard


def test_source_qualifier_guard_ignores_exact_r55_ordinary_prose():
    assert pipeline._source_qualifier_lines(R55_ORDINARY_PROSE) == ()
    assert pipeline._source_qualifier_guard(R55_ORDINARY_PROSE) == ""

    pipeline._validate_source_qualifiers_in_xml(
        R55_ORDINARY_PROSE,
        "<SlideBlueprint><Instantiation_Copy>普通文本</Instantiation_Copy></SlideBlueprint>",
    )


def test_source_qualifier_guard_preserves_qualified_quantitative_claims():
    lines = pipeline._source_qualifier_lines(QUALIFIED_QUANTITATIVE_CLAIMS)

    assert lines == (
        "此处为模拟数据，用于说明趋势",
        "| 指标 | 模拟估算 | 说明 |",
        "| 世界GDP占比 | 约 25% - 30% | 模拟区间 |",
        "| 长安人口 | 超过 100 万 | 模拟估算 |",
        "| 改革项目 | 至少 3 项 | 最低数量 |",
        "| 成本偏差 | 不超过 10% | 上限 |",
    )

    with pytest.raises(ValueError, match="native_image_source_qualifier_missing:simulation,estimate,range"):
        pipeline._validate_source_qualifiers_in_xml(
            QUALIFIED_QUANTITATIVE_CLAIMS,
            "<SlideBlueprint><Instantiation_Copy>25% - 30%；100 万；3 项；10%</Instantiation_Copy></SlideBlueprint>",
        )


def test_source_qualifier_oracle_reproduces_mac_blueprint_omission():
    unqualified_blueprint = """
    <SlideBlueprint><Instantiation_Copy>
      <On_Slide_Text>世界GDP占比 25% - 30%；长安人口超过100万</On_Slide_Text>
    </Instantiation_Copy></SlideBlueprint>
    """

    with pytest.raises(ValueError, match="native_image_source_qualifier_missing:simulation,estimate"):
        pipeline._validate_source_qualifiers_in_xml(
            SIMULATED_HISTORY_SLIDE,
            unqualified_blueprint,
        )


def test_source_qualifier_oracle_accepts_explicit_simulated_estimate():
    qualified_blueprint = """
    <SlideBlueprint><Instantiation_Copy>
      <On_Slide_Text>模拟估算：世界GDP占比 25% - 30%；长安人口超过100万</On_Slide_Text>
    </Instantiation_Copy></SlideBlueprint>
    """

    pipeline._validate_source_qualifiers_in_xml(
        SIMULATED_HISTORY_SLIDE,
        qualified_blueprint,
    )
    renderer_prompt = pipeline._native_renderer_prompt_with_source_qualifier(
        qualified_blueprint,
        SIMULATED_HISTORY_SLIDE,
    )
    assert renderer_prompt == (
        f"{qualified_blueprint}\n\n{pipeline._source_qualifier_guard(SIMULATED_HISTORY_SLIDE)}"
    )
    assert "# Source Qualification Guard" in renderer_prompt
    assert "唐朝盛世时期（模拟估算）" in renderer_prompt


def test_renderer_prompt_carries_omitted_estimate_via_source_qualifier_guard():
    with pytest.raises(ValueError, match="native_image_source_qualifier_missing:estimate"):
        pipeline._validate_source_qualifiers_in_xml(
            CIRCLE_ESTIMATE_SLIDE,
            CIRCLE_ESTIMATE_OMITTED_BLUEPRINT,
        )

    renderer_prompt = pipeline._native_renderer_prompt_with_source_qualifier(
        CIRCLE_ESTIMATE_OMITTED_BLUEPRINT,
        CIRCLE_ESTIMATE_SLIDE,
    )
    assert renderer_prompt == (
        f"{CIRCLE_ESTIMATE_OMITTED_BLUEPRINT}\n\n"
        f"{pipeline._source_qualifier_guard(CIRCLE_ESTIMATE_SLIDE)}"
    )
    assert CIRCLE_ESTIMATE_OMITTED_BLUEPRINT in renderer_prompt
    assert "# Source Qualification Guard" in renderer_prompt
    assert "约16.61亿美元" in renderer_prompt


def test_renderer_prompt_recovers_mac_omission_by_appending_source_qualifier_guard():
    unqualified_blueprint = """
    <SlideBlueprint><Instantiation_Copy>
      <On_Slide_Text>世界GDP占比 25% - 30%；长安人口超过100万</On_Slide_Text>
    </Instantiation_Copy></SlideBlueprint>
    """

    with pytest.raises(ValueError, match="native_image_source_qualifier_missing:simulation,estimate"):
        pipeline._validate_source_qualifiers_in_xml(
            SIMULATED_HISTORY_SLIDE,
            unqualified_blueprint,
        )

    renderer_prompt = pipeline._native_renderer_prompt_with_source_qualifier(
        unqualified_blueprint,
        SIMULATED_HISTORY_SLIDE,
    )
    assert renderer_prompt == (
        f"{unqualified_blueprint}\n\n{pipeline._source_qualifier_guard(SIMULATED_HISTORY_SLIDE)}"
    )


def test_renderer_prompt_fails_closed_on_blank_blueprint():
    for blank_blueprint in ("", "   ", "\n\t"):
        with pytest.raises(ValueError, match="native_image_source_qualifier_missing:empty_blueprint"):
            pipeline._native_renderer_prompt_with_source_qualifier(
                blank_blueprint,
                SIMULATED_HISTORY_SLIDE,
            )
        with pytest.raises(ValueError, match="native_image_source_qualifier_missing:empty_blueprint"):
            pipeline._validate_source_qualifiers_in_xml(
                SIMULATED_HISTORY_SLIDE,
                blank_blueprint,
            )


def test_r58_non_graphic_historical_renderer_fixture_preserves_auditable_source():
    renderer_prompt = pipeline._native_renderer_prompt_with_source_qualifier(
        R57_FAILED_BLUEPRINT,
        R57_FAILED_SLIDE_CONTENT,
    )

    # Factual display text keeps its historical claims and quantities, but the
    # provider-facing wording is non-graphic. The raw blueprint remains audited
    # separately in the saved director response/XML artifacts.
    assert "武斗洪流与批斗大会" in R57_FAILED_SLIDE_CONTENT
    assert R57_FACTUAL_ON_SLIDE_TEXT in R57_FAILED_SLIDE_CONTENT
    for blocked_phrase in (
        "枪火",
        "枪口火线",
        "被悬挂为靶",
        "活活打死",
        "十五岁女孩",
        "靶子",
        "尖刺",
    ):
        assert blocked_phrase in R57_FAILED_BLUEPRINT

    assert "# Non-Graphic Historical Visualization Guard" in renderer_prompt
    assert "# Source Qualification Guard" in renderer_prompt
    assert "non-photorealistic historical-archive or symbolic composition" in renderer_prompt
    assert "十五岁个体成为致命暴力与非人化对待的受害者" in renderer_prompt
    assert "估算：2000 人参加的批斗会已进行近两个小时；首都四十天内一千七百多名批斗对象死亡。" in renderer_prompt
    assert "十五岁｜历史暴力的受害者" in renderer_prompt
    for fact in ("十五岁", "几千人参加", "2000 人", "近两个小时", "四十天内", "一千七百多名", "估算"):
        assert fact in renderer_prompt
    for blocked_phrase in (
        "枪火",
        "枪口火线",
        "被悬挂为靶",
        "活活打死",
        "十五岁女孩",
        "靶子",
        "尖刺",
    ):
        assert blocked_phrase.casefold() not in renderer_prompt.casefold()
    for visual_tag in (
        "Style_Anchor_Extraction",
        "Colour_Role_Syntax",
        "Shape_And_Line_Syntax",
        "This_Slide_Style_Delta",
        "Text_Safe_Zones_And_Contrast_Guards",
        "Noise_Ceiling_Rules",
        "Spatial_Axes_Semantics",
        "Visual_Mass_Map",
        "Module_Blueprint",
        "Reading_Path_Control",
        "Material_And_Light_Physics",
        "Form_Grammar",
    ):
        assert (
            f"<{visual_tag}>{pipeline._NON_GRAPHIC_HISTORICAL_VISUAL_REPLACEMENT}</{visual_tag}>"
            in renderer_prompt
        )


def test_r58_non_graphic_historical_renderer_leaves_ordinary_blueprint_unchanged():
    ordinary_blueprint = (
        "<SlideBlueprint><Visual_Concept>Books, maps, and students shooting basketballs "
        "beside a classroom timeline.</Visual_Concept></SlideBlueprint>"
    )

    assert pipeline._native_renderer_prompt_with_source_qualifier(
        ordinary_blueprint,
        R55_ORDINARY_PROSE,
    ) == ordinary_blueprint
