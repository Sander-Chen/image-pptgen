from splitter import split_by_markdown


def test_h2_parent_moves_with_following_h3_slide():
    source = """# 青蛙的一生
## 引言
### 引言
引言正文
---
## 生命的序曲：卵期
章节导语
### 卵期结构
卵期正文
---
## 蝌蚪期
### 蝌蚪形态
蝌蚪正文
"""

    slides = split_by_markdown(source)

    assert slides is not None
    assert [slide["title"] for slide in slides] == [
        "青蛙的一生",
        "生命的序曲：卵期",
        "蝌蚪期",
    ]
    assert "## 生命的序曲：卵期" not in slides[0]["content"]
    assert slides[1]["content"].startswith("章节导语\n### 卵期结构")
    assert "## 蝌蚪期" not in slides[1]["content"]
    assert slides[2]["content"].startswith("### 蝌蚪形态")
    assert [slide["content"] for slide in slides] == [
        "## 引言\n引言正文\n---",
        "章节导语\n### 卵期结构\n卵期正文\n---",
        "### 蝌蚪形态\n蝌蚪正文",
    ]


def test_first_h1_is_slide_title_and_h2_precedes_distinct_h3_in_body():
    slides = split_by_markdown("# 标题\n## 引言\n### 导读\n正文\n### 背景\n背景正文")

    assert slides is not None
    assert slides[0] == {
        "title": "标题",
        "content": "## 引言\n### 导读\n正文",
        "split_mode": "h3",
    }


def test_first_h3_under_h2_uses_parent_title_and_later_sibling_stays_h3_title():
    slides = split_by_markdown("## 阶段\n### 一\n甲\n### 二\n乙")

    assert slides == [
        {"title": "阶段", "content": "### 一\n甲", "split_mode": "h3"},
        {"title": "二", "content": "乙", "split_mode": "h3"},
    ]


def test_agriculture_shape_keeps_parent_before_child_on_each_new_chapter():
    slides = split_by_markdown(
        "### 前页\n前文\n"
        "## 一、发挥我校科教优势\n章节导语\n"
        "### 1、构建农业科技成果转化平台\n平台正文\n"
        "### 2、实施科技绿舟计划\n计划正文\n"
        "## 二、发挥人才优势\n"
        "### 1、组建支农博士团\n博士团正文"
    )

    assert slides == [
        {"title": "前页", "content": "前文", "split_mode": "h3"},
        {
            "title": "一、发挥我校科教优势",
            "content": "章节导语\n### 1、构建农业科技成果转化平台\n平台正文",
            "split_mode": "h3",
        },
        {
            "title": "2、实施科技绿舟计划",
            "content": "计划正文",
            "split_mode": "h3",
        },
        {
            "title": "二、发挥人才优势",
            "content": "### 1、组建支农博士团\n博士团正文",
            "split_mode": "h3",
        },
    ]


def test_heading_fold_matrix_only_folds_immediate_whitespace_equivalent_h2_h3():
    cases = [
        (
            "h1_equals_h2",
            "# 相同\n## 相同\n### 子标题\n正文\n### 尾页\n尾文",
            "相同",
            "## 相同\n### 子标题\n正文",
        ),
        (
            "h1_equals_h3",
            "# 相同\n## 父标题\n### 相同\n正文\n### 尾页\n尾文",
            "相同",
            "## 父标题\n### 相同\n正文",
        ),
        (
            "h2_equals_h3_with_unicode_whitespace",
            "# 文档\n## 同 名\n###  同\u3000名  \n正文\n### 尾页\n尾文",
            "文档",
            "## 同 名\n正文",
        ),
        (
            "all_equal",
            "# 相同\n## 相同\n### 相同\n正文\n### 尾页\n尾文",
            "相同",
            "## 相同\n正文",
        ),
        (
            "all_distinct",
            "# 文档\n## 父标题\n### 子标题\n正文\n### 尾页\n尾文",
            "文档",
            "## 父标题\n### 子标题\n正文",
        ),
    ]

    for case_name, source, expected_title, expected_content in cases:
        slides = split_by_markdown(source)
        assert slides is not None, case_name
        assert slides[0]["title"] == expected_title, case_name
        assert slides[0]["content"] == expected_content, case_name


def test_h2_only_split_is_unchanged():
    slides = split_by_markdown("# 标题\n## 第一章\n甲\n## 第二章\n乙")

    assert slides == [
        {"title": "第一章", "content": "# 标题\n\n甲", "split_mode": "h2"},
        {"title": "第二章", "content": "乙", "split_mode": "h2"},
    ]


def test_trailing_h2_without_following_h3_is_not_lost():
    slides = split_by_markdown(
        "## 第一章\n### 第一页\n甲\n### 第二页\n乙\n## 尚未细分的下一章\n章节导语"
    )

    assert slides is not None
    assert slides[-1]["content"].endswith("## 尚未细分的下一章\n章节导语")


def test_plain_preamble_without_higher_heading_keeps_existing_behavior():
    slides = split_by_markdown("前言正文\n### 第一页\n甲\n### 第二页\n乙")

    assert slides == [
        {"title": "第一页", "content": "前言正文\n\n甲", "split_mode": "h3"},
        {"title": "第二页", "content": "乙", "split_mode": "h3"},
    ]


def test_substantive_h1_opening_is_its_own_slide_before_first_h2_h3_chain():
    source = (
        "# 青蛙的一生\n"
        "这是没有单独小标题的序言第一段。\n\n"
        "这是序言第二段，内部空行必须保留。\n"
        "## 生命的序曲：卵期\n"
        "卵期章节导语。\n"
        "### 1. 卵的结构\n"
        "卵的结构正文。\n"
        "### 2. 数量策略\n"
        "数量策略正文。"
    )

    assert split_by_markdown(source) == [
        {
            "title": "青蛙的一生",
            "content": "这是没有单独小标题的序言第一段。\n\n这是序言第二段，内部空行必须保留。",
            "split_mode": "h3",
        },
        {
            "title": "生命的序曲：卵期",
            "content": "卵期章节导语。\n### 1. 卵的结构\n卵的结构正文。",
            "split_mode": "h3",
        },
        {
            "title": "2. 数量策略",
            "content": "数量策略正文。",
            "split_mode": "h3",
        },
    ]


def test_blank_or_thematic_only_h1_opening_does_not_create_an_intro_slide():
    source = "# 文档\n\n---\n\n## 第一章\n### 第一节\n甲\n### 第二节\n乙"

    assert split_by_markdown(source) == [
        {
            "title": "文档",
            "content": "---\n\n## 第一章\n### 第一节\n甲",
            "split_mode": "h3",
        },
        {"title": "第二节", "content": "乙", "split_mode": "h3"},
    ]


def test_structured_h1_opening_counts_as_content_and_is_retained_exactly():
    source = (
        "# 文档\n"
        "- 列表项\n\n"
        "| 列 | 值 |\n"
        "| --- | --- |\n"
        "| A | 1 |\n\n"
        "```text\n"
        "原始代码\n"
        "```\n"
        "<!-- 原始注释 -->\n"
        "## 第一章\n"
        "章节导语\n"
        "### 第一节\n"
        "甲\n"
        "### 第二节\n"
        "乙"
    )

    slides = split_by_markdown(source)

    assert slides is not None
    assert slides[0] == {
        "title": "文档",
        "content": (
            "- 列表项\n\n"
            "| 列 | 值 |\n"
            "| --- | --- |\n"
            "| A | 1 |\n\n"
            "```text\n"
            "原始代码\n"
            "```\n"
            "<!-- 原始注释 -->"
        ),
        "split_mode": "h3",
    }
    assert slides[1] == {
        "title": "第一章",
        "content": "章节导语\n### 第一节\n甲",
        "split_mode": "h3",
    }


def test_h1_opening_without_h2_h3_chain_preserves_existing_fallbacks():
    assert split_by_markdown("# 文档\n开场正文\n## 唯一章节\n章节正文") is None
    assert split_by_markdown("# 文档\n开场正文\n### 第一页\n甲\n### 第二页\n乙") == [
        {
            "title": "文档",
            "content": "开场正文\n\n### 第一页\n甲",
            "split_mode": "h3",
        },
        {"title": "第二页", "content": "乙", "split_mode": "h3"},
    ]
    assert split_by_markdown(
        "# 文档\n开场正文\n## 第一章\n甲\n## 第二章\n乙"
    ) == [
        {
            "title": "第一章",
            "content": "# 文档\n开场正文\n\n甲",
            "split_mode": "h2",
        },
        {"title": "第二章", "content": "乙", "split_mode": "h2"},
    ]


def test_no_opening_agriculture_projection_remains_byte_for_byte_unchanged():
    source = (
        "# 发挥农业优势\n"
        "## 一、科教优势\n"
        "### 一、科教优势\n"
        "章节正文\n"
        "### 2、科技绿舟\n"
        "计划正文"
    )

    assert split_by_markdown(source) == [
        {
            "title": "发挥农业优势",
            "content": "## 一、科教优势\n章节正文",
            "split_mode": "h3",
        },
        {
            "title": "2、科技绿舟",
            "content": "计划正文",
            "split_mode": "h3",
        },
    ]


def test_heading_like_lines_inside_fenced_opening_are_preserved_as_content():
    source = (
        "# 文档\n"
        "```md\n"
        "## 代码里的标题，不是章节\n"
        "### 代码里的子标题，不是分页\n"
        "原样代码\n"
        "```\n"
        "## 第一章\n"
        "章节导语\n"
        "### 第一节\n"
        "甲\n"
        "### 第二节\n"
        "乙"
    )

    assert split_by_markdown(source) == [
        {
            "title": "文档",
            "content": (
                "```md\n"
                "## 代码里的标题，不是章节\n"
                "### 代码里的子标题，不是分页\n"
                "原样代码\n"
                "```"
            ),
            "split_mode": "h3",
        },
        {
            "title": "第一章",
            "content": "章节导语\n### 第一节\n甲",
            "split_mode": "h3",
        },
        {"title": "第二节", "content": "乙", "split_mode": "h3"},
    ]


def test_heading_like_lines_inside_multiline_html_comment_are_preserved_as_content():
    source = (
        "# 文档\n"
        "<!--\n"
        "## 注释里的标题，不是章节\n"
        "### 注释里的子标题，不是分页\n"
        "-->\n"
        "## 第一章\n"
        "章节导语\n"
        "### 第一节\n"
        "甲\n"
        "### 第二节\n"
        "乙"
    )

    assert split_by_markdown(source) == [
        {
            "title": "文档",
            "content": (
                "<!--\n"
                "## 注释里的标题，不是章节\n"
                "### 注释里的子标题，不是分页\n"
                "-->"
            ),
            "split_mode": "h3",
        },
        {
            "title": "第一章",
            "content": "章节导语\n### 第一节\n甲",
            "split_mode": "h3",
        },
        {"title": "第二节", "content": "乙", "split_mode": "h3"},
    ]
