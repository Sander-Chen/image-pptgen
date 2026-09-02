%%{init: {
  'theme': 'base',
  'themeVariables': {
    'primaryColor': '#e8f4f3',
    'primaryTextColor': '#2d4a47',
    'primaryBorderColor': '#5a8a85',
    'lineColor': '#7a9e9b',
    'secondaryColor': '#f7f3ee',
    'tertiaryColor': '#fff8f0',
    'background': '#fafbfc',
    'fontSize': '13px',
    'fontFamily': '-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
  }
}}%%

flowchart TB
    %% ===== 优雅配色方案 =====
    classDef startNode fill:#ffffff,stroke:#3d8b84,stroke-width:2.5px,color:#2d5a56
    classDef inputNode fill:#f0f7f6,stroke:#5a9a94,stroke-width:1.5px,color:#2d5a56
    classDef geminiNode fill:#fef9f3,stroke:#d4a574,stroke-width:1.5px,color:#8b6914
    classDef blueprintNode fill:#f5f9f8,stroke:#6ba39d,stroke-width:1.5px,color:#3d6b66
    classDef imageNode fill:#f2f8f0,stroke:#7ab06a,stroke-width:1.5px,color:#3d6633
    classDef outputNode fill:#fdf6f4,stroke:#c9847a,stroke-width:2px,color:#8b4a42
    classDef anchorNode fill:#eef5f9,stroke:#6a9ab8,stroke-width:2px,color:#3a6a88
    classDef parallelNode fill:#faf8f5,stroke:#b8a070,stroke-width:1.5px,color:#6b5a30
    classDef ruleNode fill:#fefefe,stroke:#a0a0a0,stroke-width:1px,stroke-dasharray:3 3,color:#606060
    classDef endNode fill:#f8f5f2,stroke:#8b7355,stroke-width:2.5px,color:#5a4a35

    %% ===== 主流程 =====
    Start(("开始"))

    InputAll["用户提供全部页面文本"]

    subgraph Phase1 [" "]
        direction TB
        P1Title["阶段一 · 首页生成"]
        Text1["取第 1 页文本"]
        Designer1["初创设计师<br/>Gemini 3 Pro"]
        Blueprint1["视觉蓝图"]
        Image1["Image 引擎"]
        Page1["第 1 页内容图"]
    end

    AnchorSet["第 1 页成为风格锚点"]

    subgraph Phase2 [" "]
        direction TB
        P2Title["阶段二 · 并发生成"]

        subgraph TaskA [" "]
            TA["任务 A · 封面生成"]
            CoverLogic["根据内容页倒推"]
            CoverStatus["状态: 待实现"]
        end

        subgraph TaskB [" "]
            TB["任务 B · 后续页面"]
            TextN["取第 2~N 页文本"]
            Designer2["风格传承设计师<br/>Gemini 3 Pro"]
            BlueprintN["延续蓝图"]
            ImageN["Image 引擎"]
            PageN["第 2~N 页图"]
        end
    end

    Merge["汇总全部页面"]
    FinalOutput(("完整 PPT"))

    subgraph Rules [" "]
        RTitle["关键规则"]
        R1["✗ 禁止用封面作为参考"]
        R2["✓ 始终用内容页作为锚点"]
    end

    %% ===== 连接 =====
    Start --> InputAll
    InputAll --> Text1
    P1Title ~~~ Text1
    Text1 --> Designer1
    Designer1 --> Blueprint1
    Blueprint1 --> Image1
    Image1 --> Page1
    Page1 --> AnchorSet

    AnchorSet --> P2Title
    P2Title ~~~ TA
    P2Title ~~~ TB

    AnchorSet -.->|"锚点"| Designer2
    TA ~~~ CoverLogic
    CoverLogic ~~~ CoverStatus

    TB ~~~ TextN
    TextN --> Designer2
    Designer2 --> BlueprintN
    BlueprintN --> ImageN
    ImageN --> PageN

    CoverStatus --> Merge
    PageN --> Merge
    Merge --> FinalOutput

    Rules ~~~ RTitle
    RTitle ~~~ R1
    R1 ~~~ R2

    %% ===== 应用样式 =====
    class Start,FinalOutput startNode
    class InputAll,Text1,TextN inputNode
    class Designer1,Designer2 geminiNode
    class Blueprint1,BlueprintN blueprintNode
    class Image1,ImageN imageNode
    class Page1,PageN outputNode
    class AnchorSet anchorNode
    class P1Title,P2Title,TA,TB,CoverLogic,CoverStatus parallelNode
    class Merge endNode
    class RTitle,R1,R2 ruleNode
