%%{init: {
  'theme': 'base',
  'themeVariables': {
    'primaryColor': '#e8f4f3',
    'primaryTextColor': '#2d4a47',
    'primaryBorderColor': '#5a8a85',
    'lineColor': '#7a9e9b',
    'background': '#fafbfc',
    'fontSize': '12px',
    'fontFamily': '-apple-system, BlinkMacSystemFont, sans-serif'
  }
}}%%

flowchart TB
    %% ===== 样式定义 =====
    classDef startEnd fill:#ffffff,stroke:#3d8b84,stroke-width:2.5px,color:#2d5a56
    classDef input fill:#f0f7f6,stroke:#5a9a94,stroke-width:1.5px,color:#2d5a56
    classDef color fill:#fff5f5,stroke:#c9847a,stroke-width:2px,color:#8b4a42
    classDef gpt fill:#fef9f3,stroke:#d4a574,stroke-width:1.5px,color:#8b6914
    classDef blueprint fill:#f5f9f8,stroke:#6ba39d,stroke-width:1.5px,color:#3d6b66
    classDef image fill:#f2f8f0,stroke:#7ab06a,stroke-width:1.5px,color:#3d6633
    classDef output fill:#fdf6f4,stroke:#c9847a,stroke-width:2px,color:#8b4a42
    classDef decision fill:#f9f5ff,stroke:#9a85b0,stroke-width:2px,color:#5a4570
    classDef styleClass fill:#eef5f9,stroke:#6a9ab8,stroke-width:2px,color:#3a6a88
    classDef fork fill:#faf8f5,stroke:#b8a070,stroke-width:2px,color:#6b5a30
    classDef parallel fill:#fffbeb,stroke:#d97706,stroke-width:1.5px,color:#92400e
    classDef note fill:#fefefe,stroke:#a0a0a0,stroke-width:1px,stroke-dasharray:3 3,color:#606060
    classDef section fill:#f8fafc,stroke:#94a3b8,stroke-width:1px,color:#475569

    %% ════════════════════════════════════════
    %% 入口
    %% ════════════════════════════════════════
    Start(("Ultra PPT"))
    ModeSelect{{"选择生成模式"}}

    Start --> ModeSelect

    %% ════════════════════════════════════════
    %% 模式一：逐张生成单页 PPT
    %% ════════════════════════════════════════
    subgraph Mode1 ["模式一 - 逐张生成单页 PPT"]
        direction TB

        M1_HasRef{{"是否有参考图？"}}

        M1_Ref["用户提供参考图"]
        M1_ColorExtract["提取颜色代码<br/>生成 Color Result"]

        M1_Inputs["整合输入信息<br/>Color Result + 用户需求 + 页面文字"]
        M1_GPT["GPT 5.1 High 设计总监<br/>生成视觉施工蓝图"]
        M1_BP["第 1 页施工蓝图"]
        M1_Image["Image 引擎渲染"]
        M1_Page1["生成第 1 页 PPT 图片"]

        M1_Reverse["对生成图片进行颜色逆向分析"]
        M1_BaseColor["获得 Color Result 基底<br/>供后续页面复用"]

        M1_NextPage["用户指定参考页<br/>继续生成下一页"]
    end

    %% ════════════════════════════════════════
    %% 模式二：生成完整 PPT
    %% ════════════════════════════════════════
    subgraph Mode2 ["模式二 - 生成完整 PPT"]
        direction TB

        M2_HasRef{{"是否有参考图？"}}

        %% ──────────────────────────────────
        %% 场景 A：用户提供了参考图
        %% ──────────────────────────────────
        subgraph SceneA ["场景 A - 用户提供参考图 + 文字要求"]
            direction TB

            SA_Input["用户输入<br/>参考图 + 全部页面文字 + 自定义指令"]
            SA_Step1["Step 1 - 颜色提取"]
            SA_Color["分析参考图 - 生成 Color Result"]

            SA_Fork1(("并发开始"))

            subgraph SA_Parallel1 ["Step 2 - 并发生成施工蓝图"]
                direction LR
                SA_GPT1["GPT 5.1<br/>第 1 页蓝图"]
                SA_GPT2["GPT 5.1<br/>第 2 页蓝图"]
                SA_GPT3["GPT 5.1<br/>第 3 页蓝图"]
                SA_GPTN["GPT 5.1<br/>第 N 页蓝图"]
            end

            SA_Join1(("蓝图就绪"))

            SA_Fork2(("并发开始"))

            subgraph SA_Parallel2 ["Step 3 - 并发渲染 PPT 页面"]
                direction LR
                SA_B1["Image<br/>渲染第 1 页"]
                SA_B2["Image<br/>渲染第 2 页"]
                SA_B3["Image<br/>渲染第 3 页"]
                SA_BN["Image<br/>渲染第 N 页"]
            end

            SA_Join2(("渲染完成"))
            SA_Output["输出全部 PPT 页面"]
        end

        %% ──────────────────────────────────
        %% 场景 B：无中生有
        %% ──────────────────────────────────
        subgraph SceneB ["场景 B - 无中生有 - 无参考图"]
            direction TB

            SB_Input["用户输入"]

            subgraph SB_InputDetail ["用户提供三类信息"]
                direction LR
                SB_Style["Style 风格选择<br/>极简/巴洛克/赛博朋克..."]
                SB_Custom["自定义指令<br/>元素/材质/氛围要求"]
                SB_Text["每页文字内容"]
            end

            SB_Step1["Step 1 - 生成封面"]
            SB_Cover["根据 Style + 指令<br/>生成封面供用户确认"]
            SB_Confirm["用户确认封面"]

            SB_Lock["Style 锁定<br/>后续页面必须沿用此风格"]

            SB_Step2["Step 2 - 封面颜色逆向"]
            SB_Reverse["分析封面图片<br/>提取 Color Result"]

            SB_Step3["Step 3 - 整合全部信息"]

            subgraph SB_Merge ["整合四项输入传递给设计总监"]
                direction LR
                SB_M1["Color Result<br/>颜色代码"]
                SB_M2["Style<br/>锁定的风格"]
                SB_M3["自定义指令<br/>元素/材质"]
                SB_M4["每页文字<br/>内容"]
            end

            SB_Fork1(("并发开始"))

            subgraph SB_Parallel1 ["并发生成各页施工蓝图"]
                direction LR
                SB_GPT2["GPT 5.1<br/>第 2 页蓝图"]
                SB_GPT3["GPT 5.1<br/>第 3 页蓝图"]
                SB_GPT4["GPT 5.1<br/>第 4 页蓝图"]
                SB_GPTN["GPT 5.1<br/>第 N 页蓝图"]
            end

            SB_Join1(("蓝图就绪"))

            SB_Step4["Step 4 - 并发渲染"]
            SB_Fork2(("并发开始"))

            subgraph SB_Parallel2 ["并发渲染全部页面"]
                direction LR
                SB_B1["Image<br/>封面已就绪"]
                SB_B2["Image<br/>渲染第 2 页"]
                SB_B3["Image<br/>渲染第 3 页"]
                SB_BN["Image<br/>渲染第 N 页"]
            end

            SB_Join2(("渲染完成"))
            SB_Output["输出封面 + 全部内容页"]
        end
    end

    %% ════════════════════════════════════════
    %% 最终输出
    %% ════════════════════════════════════════
    FinalPPT(("完整 PPT<br/>图片集"))

    %% ════════════════════════════════════════
    %% 核心原则
    %% ════════════════════════════════════════
    subgraph CorePrinciple ["Ultra 版本核心原则"]
        CP1["只参考颜色代码 不再参考图片"]
        CP2["统一智能设计师 不再区分初创/传承"]
        CP3["Style 必须从封面锁定 全程不可更改"]
    end

    %% ════════════════════════════════════════
    %% 连接关系
    %% ════════════════════════════════════════

    %% 模式选择
    ModeSelect -->|"单页迭代"| M1_HasRef
    ModeSelect -->|"完整生成"| M2_HasRef

    %% ──── 模式一连接 ────
    M1_HasRef -->|"是"| M1_Ref
    M1_HasRef -->|"否，直接整合"| M1_Inputs
    M1_Ref --> M1_ColorExtract
    M1_ColorExtract --> M1_Inputs
    M1_Inputs --> M1_GPT
    M1_GPT --> M1_BP
    M1_BP --> M1_Image
    M1_Image --> M1_Page1
    M1_Page1 --> M1_Reverse
    M1_Reverse --> M1_BaseColor
    M1_BaseColor --> M1_NextPage
    M1_NextPage --> FinalPPT

    %% ──── 模式二分支 ────
    M2_HasRef -->|"是，有参考图"| SA_Input
    M2_HasRef -->|"否，无中生有"| SB_Input

    %% ──── 场景 A 连接 ────
    SA_Input --> SA_Step1
    SA_Step1 --> SA_Color
    SA_Color --> SA_Fork1
    SA_Fork1 --> SA_GPT1
    SA_Fork1 --> SA_GPT2
    SA_Fork1 --> SA_GPT3
    SA_Fork1 --> SA_GPTN
    SA_GPT1 --> SA_Join1
    SA_GPT2 --> SA_Join1
    SA_GPT3 --> SA_Join1
    SA_GPTN --> SA_Join1
    SA_Join1 --> SA_Fork2
    SA_Fork2 --> SA_B1
    SA_Fork2 --> SA_B2
    SA_Fork2 --> SA_B3
    SA_Fork2 --> SA_BN
    SA_B1 --> SA_Join2
    SA_B2 --> SA_Join2
    SA_B3 --> SA_Join2
    SA_BN --> SA_Join2
    SA_Join2 --> SA_Output
    SA_Output --> FinalPPT

    %% ──── 场景 B 连接 ────
    SB_Input --> SB_Style
    SB_Input --> SB_Custom
    SB_Input --> SB_Text
    SB_Style --> SB_Step1
    SB_Custom --> SB_Step1
    SB_Step1 --> SB_Cover
    SB_Cover --> SB_Confirm
    SB_Confirm --> SB_Lock

    SB_Lock --> SB_Step2
    SB_Step2 --> SB_Reverse
    SB_Reverse --> SB_Step3

    %% Style 锁定传递（关键连接）
    SB_Lock -.->|"Style 传递"| SB_M2
    SB_Reverse -.->|"Color 传递"| SB_M1
    SB_Custom -.->|"指令传递"| SB_M3
    SB_Text -.->|"文字传递"| SB_M4

    SB_Step3 --> SB_M1
    SB_M1 --> SB_Fork1
    SB_M2 --> SB_Fork1
    SB_M3 --> SB_Fork1
    SB_M4 --> SB_Fork1
    SB_Fork1 --> SB_GPT2
    SB_Fork1 --> SB_GPT3
    SB_Fork1 --> SB_GPT4
    SB_Fork1 --> SB_GPTN
    SB_GPT2 --> SB_Join1
    SB_GPT3 --> SB_Join1
    SB_GPT4 --> SB_Join1
    SB_GPTN --> SB_Join1
    SB_Join1 --> SB_Step4
    SB_Step4 --> SB_Fork2
    SB_Fork2 --> SB_B1
    SB_Fork2 --> SB_B2
    SB_Fork2 --> SB_B3
    SB_Fork2 --> SB_BN
    SB_B1 --> SB_Join2
    SB_B2 --> SB_Join2
    SB_B3 --> SB_Join2
    SB_BN --> SB_Join2
    SB_Join2 --> SB_Output
    SB_Output --> FinalPPT

    %% 核心原则连接
    FinalPPT -.-> CP1

    %% ════════════════════════════════════════
    %% 应用样式
    %% ════════════════════════════════════════
    class Start,FinalPPT startEnd
    class ModeSelect,M1_HasRef,M2_HasRef decision
    class M1_Ref,SA_Input,SB_Input input
    class M1_ColorExtract,SA_Color,SB_Reverse,M1_BaseColor color
    class M1_GPT,SA_GPT1,SA_GPT2,SA_GPT3,SA_GPTN,SB_GPT2,SB_GPT3,SB_GPT4,SB_GPTN gpt
    class M1_BP blueprint
    class M1_Image,SA_B1,SA_B2,SA_B3,SA_BN,SB_B1,SB_B2,SB_B3,SB_BN image
    class M1_Page1,SA_Output,SB_Output,M1_NextPage output
    class SB_Style,SB_Lock,SB_Confirm,SB_M2 styleClass
    class SA_Fork1,SA_Join1,SA_Fork2,SA_Join2,SB_Fork1,SB_Join1,SB_Fork2,SB_Join2 fork
    class M1_Inputs,M1_Reverse,SB_Cover,SB_Custom,SB_Text,SB_Step1,SB_Step2,SB_Step3,SB_Step4,SA_Step1,SB_M1,SB_M3,SB_M4 input
    class CP1,CP2,CP3 note
