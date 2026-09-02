主brief:

# Phase 2：Requirement & Eval Draft

## 项目：HTML-PPT-Gen 改造为多 Generation Route 的 PPT 生成实验工作台

---

## 1. Interpreted Intent

你要改造的不是一个普通 PPT 生成器，也不是一个最终给外部用户用的 PPT 编辑器。

你真正要的是一个给自己使用的 **PPT 生成实验工作台**，服务对象是你这个 prompt engineer / AI product manager。它的核心价值是：

- 减少手动复制请求、拼接上下文、保存中间产物的脏活；
- 支持同一批 Deck / Slides 在不同 generation route 下运行；
- 支持 HTML route 和 Image / 图像生成 route 并存；
- 支持 1.0、3.0、3.2、5.0 这些 Image flow version；
- 支持真实模型调用，而不是只做 mock；
- 支持多版 prompt、模型配置、Auto 模式、批次管理、历史记录、失败追踪、产物审阅；
- 让你能快速比较 A/B/C prompt 或 route 结果，并继续迭代。

当前目标是基于现有 HTML-PPT-Gen 的副本改造一个新项目，而不是污染原日常使用项目。

---

## 2. Delivery Target / Layer Separation

### L0 — Underlying Human Problem

你现在需要反复实验 PPT 生成流程，尤其是 Image / 文生图路线。
如果继续手动构造 A 请求、复制响应、拼接到 B 请求、再并发跑 C/D/E 请求，会浪费大量时间，并且难以复盘失败原因和 prompt 版本差异。

### L1 — Target Deliverable / Artifact

目标交付物是：

> 一个由现有 HTML-PPT-Gen 复制改造而来的新 Web 工作台项目，支持 HTML route 和 Image / 图像生成 route，并能完整运行 Image 1.0、3.0、3.2、5.0 四种 flow version。

它必须是可运行的软件 artifact，不是方案文档、prompt 文档或一次性手动结果。

### L2 — Downstream Executor Mission

下游 Codex / coding agent 的任务是：

> 在隔离的新项目 / 新 GitHub project / 新数据库环境中，改造现有 HTML-PPT-Gen，使其成为支持多 generation routes 的 PPT 生成实验工作台，并用真实模型 API 跑出可审阅、可追踪、可复盘的 HTML 与 Image 路线结果。

### L3 — End-use Operating Scenario

你作为用户在 Web UI 中：

1. 选择已有 Deck，例如“中国历史”或“Figma”。
2. 确认 Deck 已拆分为 Slides，并能区分 cover / content。
3. 选择 Requirement、Color、Style、自定义指令、参考图等输入。
4. 选择 generation route：
   - HTML；
   - Image / 图像生成路线。
5. 如果选择 Image，则进一步选择 flow version：
   - 1.0；
   - 3.0；
   - 3.2；
   - 5.0。
6. 选择对应 prompt 版本和模型配置。
7. 使用 Manual 或 Auto 模式发起 batch。
8. 系统自动执行所选流程，调用真实 Gemini / Nano Image / Image / GPT 相关模型配置。
9. 在 History / Run Detail 中查看：
   - 最终 PPT 页面图片或 HTML 截图；
   - XML / visual blueprint；
   - prompt 渲染结果；
   - request / response；
   - seed page 依赖；
   - conversation id 或上下文复用证据；
   - 每页状态；
   - 失败原因；
   - 模型和 prompt 版本；
   - A/B/C 结果对比线索。

### 不允许替代目标交付物的东西

以下都不能算完成：

- 只写一份技术方案；
- 只加几个数据库字段；
- 只做一个 UI mockup；
- 只手动调用一次 Image 生成图片；
- 只把 prompt 文件放进去；
- 只跑 mock provider；
- 只实现 HTML，不实现 Image；
- 只实现 Image 5.0，而 1.0 / 3.0 / 3.2 留空；
- 只展示最终图片，但没有中间 XML、prompt、request、response、失败追踪；
- 只在原来的 `ppt.db` 上试验，污染日常项目；
- 没有 fresh run evidence，却声称已经跑通。

---

## 3. Real Outcome

任务成功后，应该出现这样的实际结果：

> 你可以在一个新的、隔离的 PPT 生成实验工作台中，用同一批 Deck / Slides / Requirement / Color / Style 输入，分别跑 HTML route 和 Image route 的 1.0、3.0、3.2、5.0 flow，并在 History / Run Detail 中审阅最终产物和关键中间产物，从而高效比较 prompt、模型、route 和 flow version 的效果。

核心成果不是“视觉效果一次做到最好”，而是：

- 流程自动化；
- route / flow 可切换；
- prompt / model 可配置；
- run 可追踪；
- 中间产物可审阅；
- 失败可定位；
- 真实模型调用可验证；
- Auto 模式可用；
- HTML 原有能力不被破坏。

---

## 4. Target Artifact Contract

### 4.1 Artifact Name / Role

暂定为：

> 多 Generation Route PPT 生成实验工作台

项目名、route 命名前缀、UI 文案可以由下游决定。
但业务上必须清楚区分：

- HTML route；
- Image / image-based PPT route；
- Image 1.0；
- Image 3.0；
- Image 3.2；
- Image 5.0。

关于命名：

- 可以使用 `Image` 作为当前路线名称；
- 也可以使用更泛化的 `ImagePPT` / `Image Route` / `Image Generation Route`；
- 但不能把 HTML 和 Image 的 prompt role、model role、run 产物混在一起，导致后续难以管理。

### 4.2 Existing HTML Route Requirements

现有 HTML route 必须保留，并作为回归验证对象。

HTML route 仍然应支持：

- Deck；
- Slides；
- Requirement；
- Color；
- Designer Agent；
- HTML Agent；
- Prompt System；
- Config；
- Generate；
- History；
- Run Detail；
- HTML 产物；
- Playwright 截图；
- Raw Response；
- Clean HTML；
- Design Principle / Design System 中间产物。

本次还要补齐一个历史遗漏：

> HTML route 也需要支持 cover / content page 区分，并能生成封面页或至少不再把封面页语义丢失。

具体实现可以由下游决定，例如：

- 增加 slide type；
- 在 split / edit UI 中标记 cover / content；
- route 内部识别 seed / cover / content；
- 或使用等价机制。

但验收时必须能证明：

- 系统知道哪些 slide 是 cover，哪些是 content；
- HTML route 没有因为 Image 改造而退化；
- HTML route 的 Run Detail 仍然能正常审阅结果。

### 4.3 Image Route Common Contract

Image route 是图像生成路线。它不是把内容绘制成 HTML，而是：

1. 根据 Deck / Slides / Requirement / Color / Style / Reference Image 等输入；
2. 通过设计总监 / design contractor 生成页面级设计指导，例如 XML visual blueprint；
3. 再把设计指导和页面内容传给图像生成模型；
4. 最终生成每页 PPT 图片。

Image route 必须支持：

- 真实模型调用；
- 多 flow version；
- per-slide 产物记录；
- seed page 依赖记录；
- conversation id 或上下文复用记录；
- XML / visual blueprint 保存和审阅；
- prompt 渲染结果保存；
- request / response 保存；
- final image 保存；
- run status / error tracking；
- Auto 模式；
- Manual 模式；
- History / Run Detail 审阅。

### 4.4 Image 1.0 Flow Contract

Image 1.0 是 **基于对话继续生成** 的流程。

根据你给的截图和说明，它的核心语义是：

1. 第一轮对话生成第一页内容页或种子页图片。
2. 后续页面继续复用第一轮对话的 conversation id / session context。
3. 例如：
   - 第一轮生成“发挥农业高校科教优势……”对应页面；
   - 第二轮在同一对话里追加“组建农科信息直通车体系……”；
   - 模型基于前面对话、第一页图像和上下文生成后续页面。
4. 后续页面可以并发，但这种并发必须保持 1.0 的真实上下文语义：
   - 要么是真实复用 conversation id；
   - 要么 provider 明确支持等价的 conversation branch / continuation；
   - 不能只是无状态地给每页单独发 prompt，然后声称是 1.0。

验收时，1.0 必须提供证据说明：

- seed page 是如何生成的；
- conversation id / thread id / session id 是什么；
- 后续页面如何基于该 conversation 继续；
- 每个后续页面的 request 如何携带第一页内容、第一页图像或必要上下文；
- 如果 provider 不支持真实 conversation 复用，必须暂停并报告，不允许偷偷降级成 3.x 或 5.0 语义。

### 4.5 Image 3.0 Flow Contract

Image 3.0 是 **种子页 + 丢弃原始对话 + 后续新对话并发生成** 的流程。

核心语义：

1. 3.0 不使用封面作为风格参考。
2. 3.0 始终以第一页内容页作为风格锚点。
3. Seed Page 阶段：
   - 使用 Seed Page Design Contractor prompt；
   - 例如用 Gemini 1.5 Flash 处理第一页内容文本；
   - 生成 XML visual blueprint；
   - 对 XML 做固定规则微调，例如删除 XML 结尾 checkpoint 节点；
   - 将处理后的 XML 嵌入 Nano Image 请求；
   - 将第一页内容作为 user prompt；
   - 调用真实图像生成模型生成第一页内容页图片。
4. 后续页面阶段：
   - 第一页生成后，原始对话被丢弃；
   - 第 2 页到最后一页在新的对话 / 新的请求中并发生成；
   - 每一页的 Design Contractor 使用：
     - 第一页图片；
     - 第一页 visual blueprint；
     - 当前页文本；
     - Non-Seed Design Contractor prompt；
   - 生成该页 XML visual blueprint；
   - 删除 checkpoint 节点或执行固定 XML 微调规则；
   - 再调用图像生成模型生成该页最终图片。

验收时，3.0 必须证明：

- 没有使用封面作为风格参考；
- 第一内容页确实作为风格锚点；
- 后续页面没有继续复用 seed conversation；
- 后续页面确实引用了 seed page 图片或等价风格锚点；
- seed / non-seed prompt 是不同角色；
- 每页 XML 和最终图片均可审阅。

### 4.6 Image 3.2 Flow Contract

Image 3.2 是 3.0 的扩展，核心变化是加入封面参考。

核心语义：

1. 系统接收：
   - 封面参考图；
   - 第一页内容文本。
2. 使用 3.2 对应 Seed Page Design Contractor prompt。
3. 生成第一页内容页作为种子页。
4. 拿到种子页之后，后续页面并发生成。
5. 后续流程与 3.0 类似，但 prompt 版本和输入上下文不同。
6. 3.2 必须保留 seed / non-seed 角色区分。

验收时，3.2 必须证明：

- 封面参考图被纳入 seed page 阶段；
- 第一内容页仍然是内容页种子，而不是直接把封面当内容页；
- 后续页面基于 seed page 风格生成；
- 3.2 使用的 prompt 与 3.0 可区分；
- 3.2 的中间 XML、请求、响应、最终图片可审阅。

### 4.7 Image 5.0 Flow Contract

Image 5.0 是新版统一流程，核心变化是：

1. 不再区分 seed / non-seed design contractor；
2. 使用统一 Design Contractor prompt；
3. Style / 自定义指令是 optional；
4. 风格参考图是 optional；
5. 如果用户提供参考图，系统需要先提取该图的设置；
6. 参考图设置提取不应建成一个新的 Image Style Extractor 角色；
7. 这类图片设置提取能力应视为跨路线共享能力，或复用 HTML 路线已有逻辑；
8. 对第 1 页到第 N 页，每页并发生成 XML visual blueprint；
9. 每页 XML 和内容再传给图像生成模型生成最终页面。

验收时，5.0 必须证明：

- 使用统一 prompt，而不是 seed / non-seed prompt；
- Style 缺省时可以运行；
- 参考图缺省时可以运行；
- Style 存在时会进入对应 prompt slot；
- 参考图存在时会先提取设置，然后进入后续设计流程；
- 每页可以独立并发生成 visual blueprint 和最终图片；
- 每页产物可审阅。

---

## 5. Prompt / Model Configuration Contract

### 5.1 Prompt System

下游必须让这些 prompt 进入 Prompt Setting / Prompt System，而不是硬编码在 pipeline 里：

- HTML Designer Agent prompt；
- HTML Agent prompt；
- Image 1.0 image generation prompt；
- Image 3.0 Seed Design Contractor prompt；
- Image 3.0 Non-Seed Design Contractor prompt；
- Image 3.2 Seed Design Contractor prompt；
- Image 3.2 Non-Seed Design Contractor prompt；
- Image 5.0 Unified Design Contractor prompt；
- Image Generator prompt；
- XML checkpoint 删除规则或对应固定处理规则；
- 图片设置提取 prompt，如果项目已有跨路线逻辑，则复用，不新增 Style Extractor。

注意：

- 当前说明中不存在 Style Extractor 角色；
- 不要凭空新增 Image Style Extractor；
- 已有 Image Generator 角色应以实际代码 / 数据库为准，不要因为旧项目介绍没有提到就删掉；
- HTML 和 Image 的角色必须分开管理。

### 5.2 Config / Model Profile

Config 需要支持新的模型角色或等价能力，例如：

- HTML Designer model；
- HTML Agent model；
- Image Seed Design Contractor model；
- Image Non-Seed Design Contractor model；
- Image Unified Design Contractor model；
- Image Generator model；
- shared image setting extraction model，如果现有系统已有；
- Auto Split model；
- Prompt Assistant model。

具体字段名、表结构、UI 布局由下游决定，但必须能恢复这些语义：

- 哪个 flow 用哪个 prompt；
- 哪个 stage 用哪个 model；
- 哪个 run 使用了哪个 prompt version；
- 哪个 run 使用了哪个 model profile；
- 哪个 image 是哪个 provider / model 生成的；
- 哪个 XML 是哪个 design contractor 生成的。

### 5.3 真实模型调用要求

本次不是纯 mock 项目。

必须支持真实 API 调用，至少包括：

- Gemini / Google 模型，用于设计总监或 XML 生成，例如 Gemini 1.5 Flash / Flash Lite 等；
- Nano Image / Image / Image 2 相关图像生成模型；
- GPT / GPT Image 相关模型配置能力，如果真实 API key 和 endpoint 已提供。

测试环境可以优先用成本更低的 Gemini / Nano / Image key 跑通流程。
如果某个 GPT provider 没有 key 或 endpoint，不能声称 GPT provider 已完成真实验证，只能标记为未验证或待真实环境验证。

---

## 6. Interaction Surface / Operating Model

### Primary Interaction Surface

主交互面是现有 React Web UI。

API 和 CLI 可以作为辅助，但不是本次主要产品入口。

### Operating Model

操作模式是：

- 你手动配置 Deck / Requirement / Color / Style / Prompt / Config；
- 你选择 route 和 flow version；
- 系统自动执行 batch；
- 系统并发处理页面；
- 你在 History / Run Detail 审阅结果；
- 系统保留足够中间证据，支持你继续调 prompt。

### UI 期望

#### Generate 页面

需要能选择：

- Deck；
- Requirement；
- Color；
- Style / 自定义指令，如果适用；
- Reference Image，如果适用；
- generation route；
- Image flow version；
- Prompt version；
- Config / Model combination；
- Manual / Auto 模式；
- candidate 数量或 Requirement × Color 组合。

#### History 页面

需要能展示：

- route；
- flow version；
- Deck；
- Config；
- Prompt versions；
- Run 状态；
- 成功 / 失败数；
- failure rate；
- 创建时间；
- 是否 Auto；
- 是否真实模型 run；
- 是否存在未验证项。

#### Run Detail 页面

最终 PPT 产物应该占主体视觉空间。

辅助信息可以折叠，但必须可查，包括：

- XML visual blueprint；
- XML before / after checkpoint removal；
- rendered system prompt；
- rendered user prompt；
- raw model response；
- request metadata；
- provider / model；
- prompt version；
- seed page dependency；
- conversation id / thread id；
- reference image；
- extracted settings；
- error message；
- timing / retry / status。

---

## 7. Scope / Non-goals

### In Scope

本次范围包括：

- 复制现有项目并在新项目中改造；
- 不污染原日常使用项目；
- 保留 HTML route；
- 给 HTML route 补齐 cover / content 区分；
- 新增多 generation route 能力；
- 接入 Image 1.0、3.0、3.2、5.0；
- 支持真实模型 API；
- 支持 Prompt System 中的新角色；
- 支持 Config 中的新模型角色；
- 支持 Manual 模式；
- 支持 Auto 模式；
- 支持 Deck / Slides / Requirement / Color / Style / Reference Image 作为共享输入；
- 支持 Run / Batch / History / Run Detail；
- 保存中间 XML、prompt、request、response、final image；
- 对“中国历史”和“Figma”两个真实 Deck 做验收；
- 生成外部执行 ledger 和验证证据。

### Out of Scope

本次不以这些为目标：

- 做传统 PPT 编辑器；
- 人工精修 PPT；
- 保证生成图片视觉质量达到最终商业交付标准；
- 让两个长期项目共享同一个 SQLite；
- 把原项目的 `ppt.db` 当实验数据库；
- 把 mock run 当真实完成；
- 一次性设计完所有未来图像模型路线；
- 强制确定未来 GPT Image 2 的最终流程；
- 把内部实现、数据库字段、前端组件结构过度锁死。

---

## 8. Constraints

### Hard Constraints

1. 必须基于现有 HTML-PPT-Gen 的副本改造。
2. 原项目继续作为日常使用项目，不允许污染。
3. 新项目使用隔离数据库。
4. 不允许两个长期独立项目直接读写同一个 SQLite。
5. 不允许在根目录生产 `ppt.db` 上跑自动测试或实验生成。
6. HTML route 必须保留。
7. Image 1.0、3.0、3.2、5.0 必须全部接入并可运行。
8. 必须接入真实模型 API，mock 只能作为辅助验证。
9. Prompt 必须进入 Prompt System / Prompt Setting，不应硬编码。
10. Model roles 必须进入 Config 或等价配置系统。
11. 不存在 Image Style Extractor 角色，不得凭空新增。
12. Cover / content page 必须能区分。
13. Auto 模式必须保留并适配多 route。
14. API key、凭证、用户内容不能泄露到公开日志或文档中。
15. 样例输出必须由当前 artifact 生成，不能手工伪造。

### Soft Preferences

1. 最终图片 / PPT 页面在 UI 中占主体。
2. XML、prompt、request、response 等辅助信息可以折叠。
3. route 命名可以灵活，但 HTML 和 Image 必须清楚分开。
4. 不要过度泛化到未来所有图像模型，以免当前管理复杂度太高。
5. 但也不要把 Image 写死到无法未来接 GPT Image 2。
6. 下游可决定 UI 展开方式、字段名、数据库具体 schema。
7. 业务语义必须清晰可恢复。

---

## 9. Preferences as Signals

### Desired Signals

好的实现应该让你看到：

- 你不再需要手动复制 A 请求结果到 B 请求；
- 你可以在一个页面发起不同 route / flow 的 run；
- 你可以快速切换 prompt version；
- 你可以看到每个 flow 的中间 XML；
- 你可以知道每张图来自哪个 prompt、model、request；
- 你可以比较“中国历史”和“Figma”在不同 flow 下的结果；
- 你可以追踪失败到底发生在：
  - design contractor；
  - XML cleanup；
  - image generation；
  - conversation continuation；
  - file saving；
  - UI loading；
  - provider timeout；
  - prompt variable rendering。
- Auto 模式仍然能用于批量候选生成。

### Anti-signals

坏实现会表现为：

- 只能看最终图，看不到 XML 和 prompt；
- Image 1.0、3.0、3.2、5.0 的差异被混成一个流程；
- seed page 依赖不可见；
- conversation id 不保存；
- 失败只显示“failed”，没有阶段原因；
- Prompt 仍然散落在代码里；
- Config 无法表达不同 stage 的 model；
- Auto 模式只支持 HTML；
- 生成结果像是手动放进去的；
- 真实 API 没跑，却声称已完成；
- 原项目数据库被污染。

### Tradeoff Posture

- 可以牺牲一些 UI 精致度，换取流程可运行和证据完整；
- 可以先接受视觉质量一般，换取真实模型链路跑通；
- 不可以牺牲 route / flow 语义；
- 不可以牺牲证据可追踪；
- 不可以牺牲数据库隔离；
- 不可以用 mock 替代真实模型验收。

---

## 10. Autonomy / Decision Delegation

### 下游可以自主决定

下游 coding agent 可以自主决定：

- 数据库具体 schema；
- 是否新增表，还是扩展现有表；
- route 命名前缀；
- UI 折叠方式；
- 文件保存路径；
- artifact 命名方式；
- provider adapter 内部结构；
- 并发调度实现；
- XML 展示组件；
- Run Detail 页面布局；
- 是否用 `slide_type` 字段或等价机制表达 cover / content；
- 如何在 Prompt System 中组织新 prompt roles；
- 如何在 Config 中组织新 model roles。

### 下游不能自主改变

下游不能改变：

- 目标是多 route 实验工作台；
- 必须保留 HTML route；
- 必须接入 Image 1.0 / 3.0 / 3.2 / 5.0；
- 必须接入真实模型；
- 必须保留 Auto 模式；
- 必须区分 cover / content；
- 必须保存 XML / prompt / request / response / final image；
- 必须使用隔离数据库；
- 不得新增 Style Extractor 角色；
- 不得把 mock 当真实完成；
- 不得把手动结果当系统产物；
- 不得污染原日常项目。

### 必须暂停确认的情况

下游遇到以下情况必须暂停，不得假装完成：

1. Nano / Image provider 不支持 1.0 所需的 conversation id / session continuation。
2. 用户未提供 1.0 / 3.0 / 3.2 / 5.0 的 prompt 文件。
3. prompt 变量语法不清，无法安全渲染。
4. API key 缺失或无法调用真实模型。
5. 真实模型调用成本明显超出预期。
6. 迁移或测试可能污染原项目数据库。
7. “中国历史”或“Figma”Deck 在新数据库中不存在。
8. 需要把密钥写入日志或前端才能继续。
9. Provider 返回结构和预期严重不一致，无法可靠保存 image / response / conversation id。
10. XML checkpoint 删除规则缺失或与 prompt 文件冲突。

---

## 11. Weak Areas / Assumptions

当前仍有一些弱点，但可以作为假设进入执行规格：

1. 你稍后会给 coding agent 一个单独文件夹，包含 1.0、3.0、3.2、5.0 的 prompt 文件。
2. 每个 prompt 文件中变量会用 `{变量名}` 或项目最终约定的占位方式清楚标出。
3. XML checkpoint 删除规则已有固定文本或规则，会随 prompt 资产一起提供。
4. “中国历史”和“Figma”Deck 会存在于新项目可用数据库中，或可从旧项目安全复制。
5. Nano / Image API 支持真实图像生成。
6. Image 1.0 所需 conversation id / session continuation 需要由下游在真实 API 层确认。
7. GPT 相关模型如果没有可用 key，则只能配置，不得声称真实验证通过。
8. HTML 项目 introduction 可能不是最新状态；下游必须以实际代码和数据库为准，尤其是已有 Image Generator 角色。
9. 视觉质量不是本次自动验收的主要指标，最终审美判断由你人工 review。

提醒你给下游准备 prompt 资产时，建议文件夹至少这样组织或等价组织：

```text
prompts/
  image_1_0/
  image_3_0/
    seed_design_contractor.md
    non_seed_design_contractor.md
  image_3_2/
    seed_design_contractor.md
    non_seed_design_contractor.md
  image_5_0/
    unified_design_contractor.md
  image_generator/
  xml_cleanup_rules.md
```

实际命名可以不同，但必须让下游能一眼知道每个 prompt 属于哪个 flow、哪个 stage。
变量请明确用 `{变量名}` 或你最终决定的占位符标出来，避免模型误改原 prompt。

---

## 12. Required Evidence

下游完成时必须提交 evidence pack。至少包括：

### 12.1 Artifact / Build Evidence

- 新项目位置；
- 新数据库位置；
- 迁移说明；
- 新增或修改的主要功能说明；
- 未触碰原项目生产 `ppt.db` 的证据；
- 新 route / flow 在 UI 中可选择的截图或录屏；
- Prompt System 中新角色可管理的截图；
- Config 中新模型角色可管理的截图。

### 12.2 Fresh Run Evidence

必须提供 fresh run，而不是旧截图或手工图片。

至少包括：

- HTML route 回归 run；
- HTML cover / content 支持证据；
- Image 1.0 真实 run；
- Image 3.0 真实 run；
- Image 3.2 真实 run；
- Image 5.0 真实 run；
- Manual 模式证据；
- Auto 模式证据；
- “中国历史”Deck run 证据；
- “Figma”Deck run 证据。

理想完整验收矩阵是：

```text
2 个 Deck：中国历史、Figma
×
4 个 Image flow：1.0、3.0、3.2、5.0
=
8 个 Image representative real runs
```

如果因为成本、API 限制、Deck 数据问题无法完成完整矩阵，必须明确降级为 partial validation，不能声称 full done。

### 12.3 Per-run Evidence

每个 representative run 至少需要：

- batch id；
- run id；
- route；
- flow version；
- deck id / deck title；
- requirement id / title；
- color id / title；
- style input，如果有；
- reference image，如果有；
- prompt versions；
- model profiles；
- provider；
- started / completed timestamp；
- final status；
- output directory；
- per-slide status；
- failure reason，如果失败。

### 12.4 Per-slide Evidence

每页至少需要保存或可审阅：

- slide id；
- slide position；
- slide type：cover / content / other；
- original slide content；
- XML visual blueprint，如果该 flow 有；
- XML cleanup 前后内容，如果适用；
- rendered system prompt；
- rendered user prompt；
- raw model response；
- request metadata；
- image path；
- final generated image；
- seed dependency，如果适用；
- conversation id / thread id，如果适用；
- error message，如果失败。

### 12.5 Secrets Redaction

证据中不得包含：

- API key；
  -完整 Authorization header；
  -敏感 endpoint token；
  -不应公开的用户数据。

---

## 13. External Execution Ledger Requirement

因为这是长时间、多阶段、多 flow 的 coding agent 任务，必须维护外部执行 ledger。

可以复用现有项目习惯：

- `agent-tasks.csv`
- `artifacts/verification.json`

也可以使用等价文件，但至少要能恢复这些信息：

- task id；
- phase / group；
- task description；
- route / flow；
- status；
- priority；
- blocker；
- next action；
- evidence reference；
- timestamp / freshness marker；
- validation level achieved；
- whether real model evidence exists。

执行 ledger 的目的不是做项目管理形式主义，而是防止长跑任务中：

- 忘记某个 flow；
- 把 mock 当 real；
- 把 partial 当 done；
- 失败后无法定位；
- 后续 evaluator 无法知道哪些证据是新鲜的。

---

## 14. Validation Ladder

### Level 0 — Static / Shape Validation

目的：确认功能入口、schema、prompt roles、config roles、route 选择等静态结构存在。

证据：

- UI 截图；
- schema / migration 说明；
- Prompt System 截图；
- Config 截图；
- route selector 截图；
- no-root-db-use 说明。

能证明：

- 结构上支持多 route；
- 相关配置入口存在。

不能证明：

- pipeline 能跑；
- 真实模型能调用；
- Image flow 语义正确。

---

### Level 1 — Controlled / Fixture Validation

目的：用 fixture 或 dry-run 验证 orchestration、状态流转、artifact 保存。

证据：

- mock provider run；
- dry-run request construction；
- per-slide artifact 保存；
- History / Run Detail 可展示；
- failure handling fixture。

能证明：

- 编排逻辑基本可运行；
- UI 和数据库链路通；
- artifact 可追踪。

不能证明：

- 真实 API 可用；
- 真实 image generation 可用；
- conversation id 语义真实成立。

---

### Level 2 — Provider Connectivity Validation

目的：确认真实模型最小调用可用。

证据：

- Gemini / Google 模型成功生成 XML；
- Nano / Image 成功生成至少一张图片；
- request / response 有记录；
- image artifact 保存成功；
- secret 已脱敏。

能证明：

- 真实 API key、endpoint、provider adapter 最小可用。

不能证明：

- 四条完整 flow 都跑通；
- Auto 模式可用；
- 多页并发可用；
- History / Run Detail 完整可审阅。

---

### Level 3 — Representative Flow E2E Validation

目的：确认每条 Image flow 都能完成至少一个真实端到端 run。

证据：

- 1.0 完整 run；
- 3.0 完整 run；
- 3.2 完整 run；
- 5.0 完整 run；
- 每条 run 有 per-slide image、XML、prompt、request、response；
- 1.0 有 conversation id 证据；
- 3.x 有 seed image 依赖证据；
- 5.0 有 unified prompt 证据。

能证明：

- 每条 flow 可运行；
- route / flow 语义大体成立。

不能证明：

- 对两个真实 Deck 都稳定；
- Auto 模式完整可用；
- 长跑可靠性；
- 视觉质量稳定。

---

### Level 4 — Full Representative Acceptance Validation

目的：达到本次“全部接入、长时间运行”的完成标准。

建议完整矩阵：

- Deck：
  - 中国历史；
  - Figma。
- Routes：
  - HTML；
  - Image。
- Image flows：
  - 1.0；
  - 3.0；
  - 3.2；
  - 5.0。
- Modes：
  - Manual；
  - Auto。

完成要求：

- 两个 Deck 至少都被真实跑过；
- 四个 Image flow 都有真实 E2E 证据；
- Auto 模式在多 route 语义下可用；
- HTML route 回归通过；
- History / Run Detail 可审阅；
- 所有失败有明确阶段和错误；
- 没有污染原项目数据库。

只有达到 Level 4，才允许声称 full done。
如果只达到 Level 2 或 Level 3，必须标记为 partial / incomplete。

---

## 15. Evaluation Strategy

### Evaluation Subject

评估对象是：

> 改造后的多 route PPT 生成实验工作台 artifact。

不是：

- 单张生成图片；
- 单次手动请求；
- prompt 文件；
- 技术方案；
- UI mockup；
- 数据库 schema。

### Hard Gates

以下任一失败，即整体不能标记为完成：

1. 未使用隔离项目 / 隔离数据库。
2. 原项目 `ppt.db` 被自动测试或实验生成污染。
3. HTML route 无法继续运行。
4. Image 1.0 / 3.0 / 3.2 / 5.0 任一 flow 不存在或不可运行。
5. Auto 模式被破坏或只支持旧 HTML。
6. Prompt 没有进入 Prompt System。
7. Model roles 没有进入 Config 或等价配置。
8. 没有真实模型调用证据。
9. 只有 mock run，却声称真实完成。
10. 只有最终图片，没有 XML / prompt / request / response 追踪。
11. 1.0 没有 conversation id / session continuation 证据。
12. 3.0 / 3.2 没有 seed page 依赖证据。
13. 5.0 仍然误用 seed / non-seed prompt 区分。
14. API key 泄露到日志、前端或证据包中。
15. 手工生成图片被当作系统生成结果。

### Deterministic Checks

应检查：

- route selector 是否存在；
- flow version selector 是否存在；
- slide type 是否可恢复；
- Prompt System 中是否有对应 roles；
- Config 中是否有对应 model roles；
- Batch / Run 是否记录 route 和 flow；
- Run Detail 是否能看到 per-slide artifacts；
- 每个 run 是否有 status；
- 每个 failed stage 是否有 error message；
- XML cleanup 前后是否保存；
- final image path 是否存在；
- output artifact 是否能打开；
- History 是否能展示 route / flow / prompt / model；
- Auto mode 是否能创建多 route run。

### Evidence Checks

应要求：

- fresh run timestamps；
- run ids；
- output directories；
- screenshots / logs；
- request / response samples；
- redacted provider metadata；
- conversation id；
- seed image dependency；
- model profile ids；
- prompt version ids；
- verification report；
- execution ledger。

### Rubric Checks

这些由 evaluator 或你人工判断：

- Run Detail 是否真的方便 prompt engineering 复盘；
- XML / prompt / response 是否足够容易查看；
- route / flow 差异是否清楚；
- Auto mode 是否符合你的实验习惯；
- final images 是否足够作为审阅主体；
- UI 是否没有把关键产物埋得太深；
- 失败定位是否足够具体；
- 命名是否可长期维护。

### Human Review Checks

这些不能自动标记通过：

- 生成图片是否“好看”；
- 设计风格是否满足你的审美；
- prompt 质量是否达标；
- Image 5.0 是否优于 3.x；
- 是否值得后续接 GPT Image 2；
- 最终 route 命名是否满意。

---

## 16. False-Pass Risks

### 风险 1：只做 route 字段，不做真实 pipeline

防护：

- 要求每条 flow 都有真实 E2E run evidence。

### 风险 2：用手动图片冒充系统生成

防护：

- 要求 run id、request、response、artifact path、timestamp 互相对应。

### 风险 3：把 1.0、3.0、3.2、5.0 混成一个流程

防护：

- 每个 flow 有单独语义验收：
  - 1.0 conversation continuation；
  - 3.0 seed content page，无封面参考；
  - 3.2 cover reference + seed；
  - 5.0 unified prompt + optional style/reference。

### 风险 4：只实现 5.0，历史 flow 留 placeholder

防护：

- hard gate 要求 1.0 / 3.0 / 3.2 / 5.0 全部可运行。

### 风险 5：只跑 mock，不跑真实模型

防护：

- mock 只能通过 Level 1；
- full done 需要 Level 4。

### 风险 6：中间产物不可见，导致无法 prompt engineering

防护：

- Run Detail 必须能查看 XML、prompt、request、response、seed dependency。

### 风险 7：污染原项目数据库

防护：

- root `ppt.db` 不得用于实验；
- required evidence 中必须说明新项目和新数据库位置。

### 风险 8：Auto 模式被遗忘

防护：

- Auto mode 是 hard gate 和 acceptance criteria。

### 风险 9：长跑任务中失忆或跳步

防护：

- 必须维护外部 execution ledger。

---

## 17. Definition of Ready

下游开始完整执行前，最好满足：

1. 新项目副本已创建。
2. 新 GitHub project / repo 已准备。
3. 原项目日常使用数据库不会被触碰。
4. 新项目隔离数据库已准备。
5. “中国历史”和“Figma”Deck 可在新环境使用。
6. 1.0、3.0、3.2、5.0 prompt 文件已放入明确文件夹。
7. prompt 变量已用 `{变量名}` 或最终约定语法标出。
8. XML checkpoint 删除规则已提供。
9. API key 已在安全位置配置。
10. Nano / Image provider endpoint 信息可用。
11. Gemini / Google model profile 可用。
12. 真实模型调用成本边界已被你接受。
13. 下游知道不能在原 `ppt.db` 上验证。
14. 下游知道 Auto 模式必须保留。
15. 下游知道 completion 必须有 fresh run evidence。

如果部分 prompt 或 key 尚未准备，下游可以先做 scaffolding 和 Level 0 / Level 1 验证，但不能声称 full done。

---

## 18. Definition of Done

只有同时满足以下条件，才能算完成：

1. 新项目可以独立运行。
2. 使用隔离数据库。
3. HTML route 保持可用。
4. HTML route 支持或不丢失 cover / content 区分。
5. Image route 可在 UI 中选择。
6. Image 1.0 可真实运行。
7. Image 3.0 可真实运行。
8. Image 3.2 可真实运行。
9. Image 5.0 可真实运行。
10. Prompt System 可管理所有相关 prompt。
11. Config 可管理所有相关 model roles。
12. Manual 模式可用。
13. Auto 模式可用。
14. “中国历史”和“Figma”Deck 至少被纳入代表性真实验证。
15. Run Detail 能审阅最终图片和关键中间产物。
16. 每个 representative run 有 fresh evidence。
17. 1.0 有 conversation id / session continuation 证据。
18. 3.0 / 3.2 有 seed image dependency 证据。
19. 5.0 有 unified prompt 证据。
20. XML cleanup 规则有执行证据。
21. 失败可定位到具体 stage。
22. API key 没有泄露。
23. 外部 execution ledger 完整。
24. 验证报告明确说明达到的最高 validation level。
25. 没有把 mock、计划、手工输出当完成。

---

## 19. Definition of Fail

以下任一情况即使界面看起来完整，也应判定为失败或未完成：

- 原项目数据库被污染；
- HTML route 不能跑；
- Image 任一 flow 只是 placeholder；
- 没有真实模型调用；
- 没有 run evidence；
- 1.0 没有 conversation continuation 证据；
- 3.x 没有 seed dependency 证据；
- 5.0 仍误用 seed / non-seed 角色；
- Auto 模式不可用；
- Prompt 硬编码在代码里；
- Config 无法配置新 model roles；
- 只展示最终图，不保存中间产物；
- 失败没有 stage 信息；
- API key 泄露；
- evaluator 无法确认图片由 artifact 生成；
- 完成报告只讲“实现了”，没有 fresh run 证据。

---



补充文档内容：
3. 真正要做的架构：不是四条 Image 流水线，而是一套 Route Strategy

你对 Image 四条路线的理解是对的。不要把 1.0、3.0、3.2、5.0 当成四个独立大工程，而要抽象成一套 ImageRouteStrategy。

我建议的核心抽象是：

Generation Engine:
- html
- image

Image Strategy:
- continuation_1_0
- anchor_3_0
- cover_ref_3_2
- unified_5_0

Common Contract:
- input deck / slide
- route strategy
- model profile
- prompt template
- rendered system prompt
- rendered user prompt
- request payload
- response payload
- generated image / XML / intermediate artifact
- seed dependency
- conversation/session id
- stage-level error trace

这套抽象有一个关键好处：你不是为 1.0 / 3.0 / 3.2 / 5.0 分别写四套系统，而是在同一个实验平台里切换策略。

结合你现有架构，我建议新增或扩展这些概念：

runs
- engine: html | image
- strategy: html_default | image_1_0 | image_3_0 | image_3_2 | image_5_0
- route_metadata_json

run_slides
- 保留现有 status / error_message / position
- 对 Image 增加 image_path 或统一放入 artifact table

new table: run_artifacts
- id
- run_id
- run_slide_id nullable
- stage
- artifact_type
- text_content nullable
- json_content nullable
- file_path nullable
- created_at

new table: llm_call_logs / model_call_logs
- id
- run_id
- run_slide_id nullable
- role
- model_profile_id
- system_prompt_rendered
- user_prompt_rendered
- request_payload_json
- response_payload_json
- conversation_id nullable
- dependency_json
- error_trace
- created_at

这会直接服务你的核心目标：实验复盘。不要只看最终图。你要能知道某一页为什么生成成这样、它用了哪个 Seed、哪个 Prompt、哪个模型、哪个 Request、哪个 Response、哪个 Conversation ID。

这也和现有系统的方向一致：当前 Run/Run Slide 已经保存 raw response、clean HTML、HTML path、screenshot path、error_message 等字段，说明你的系统天然适合扩展成实验复盘平台。

4. Image 实现顺序：先 5.0，再 3.0/3.2，最后 1.0

你直觉上觉得 1.0 effort 最小，这没错。但从架构收益看，我建议顺序是：

第一优先：5.0 统一流

5.0 是最适合定义平台抽象的一条路线，因为它废除 Seed / Non-seed 角色差异，把 Style 和参考图作为 optional 参数。也就是说，它最接近未来的“通用生成策略”。

先做 5.0，可以迫使系统形成干净抽象：

Deck + Slides + Optional Style + Optional Reference Image + Prompt + Model Config
→ Parallel Page Jobs
→ Image Outputs + Evidence Chain
第二优先：3.0 与 3.2

3.0 和 3.2 应该共用大部分实现。区别只是 3.2 多了“封面参考图 + 首内容页”的联合 Seed 输入。

抽象上：

3.0 = Seed content page → Seed XML/Image → discard conversation → parallel independent generation
3.2 = Cover reference + first content page → Seed XML/Image → discard conversation → parallel independent generation

它们的共同点是“并发独立生成”，不是延续对话。

第三优先：1.0

1.0 的特殊点是 conversation/session continuation。它不是最重要，但它有独特的 session 追踪价值。

所以 1.0 可以最后补，但一定要把 conversation_id / session_id / dependency chain 存下来。否则 1.0 以后最难复盘。