# Image PPTGen

<p align="center">
  <strong>把一份材料，变成一套视觉统一、可以直接预览和下载的图片型演示文稿。</strong><br>
  <sub>Image-first presentations, orchestrated by Codex.</sub>
</p>

<p align="center">
  <img src="docs/demo/image-3-history/cover.webp" alt="Image PPTGen 中国历史主题演示封面" width="100%">
</p>

## 30 秒开始

在 Codex 中只需要发送这一句话：

> 请安装这个 Skill，地址：https://image-pptgen.pages.dev/install.sh

安装完成后，把材料交给 Codex，并说“帮我生成一个 PPT”。Image PPTGen 会先给出拆页方案；你可以调整，确认后才会开始生成。

> [!IMPORTANT]
> 安装指令不需要指定目录、Python、环境变量或后续步骤。安装器会识别系统并完成本地配置。

## 它怎么工作

```text
一句话安装
    ↓
提交文字或 Markdown 材料
    ↓
查看并调整拆页方案
    ↓
明确确认
    ↓
逐页生成图片
    ↓
打开静态 Preview · 全屏查看 · 下载 ZIP
```

拆页和生成是两个独立阶段：确认拆页之前不会提前生成，修改页数也不需要重新提交材料。

## Demo：纵览中国历史

下面三张图来自同一套 Image 3.0 演示文稿。原材料经过拆页确认后生成，共 5 页；这里展示封面、中段与收束页。

| 历史中段 | 现代中国 |
| --- | --- |
| ![中国历史演示：盛世与转折](docs/demo/image-3-history/middle-history.webp) | ![中国历史演示：从站起来到强起来](docs/demo/image-3-history/modern-china.webp) |

Demo 使用 `public_image_3_0` / `codex_native_image` 路线生成。图片经过等比例压缩用于 GitHub 展示，没有重新生成或改写画面。完整来源与哈希见 [Demo provenance](docs/demo/PROVENANCE.md)。

## 适合什么场景

- 把文章、研究材料和汇报稿快速变成视觉化演示；
- 需要先审拆页、再生成，避免一开始就浪费生成额度；
- 希望页面风格统一，又不想手工维护模板；
- 需要一个不依赖后台服务长期运行的静态 Preview 和 ZIP。

## 当前支持范围

| 平台 | 架构 | 状态 |
| --- | --- | --- |
| macOS | Apple Silicon / ARM64 | 已完成真实安装与端到端验收 |
| Linux | x86_64 | 已完成真实安装与端到端验收 |
| Windows | x86_64 | 安装包仍处于验证阶段，不作为当前支持承诺 |

当前稳定发布锁定为 `0.0.0-r62-0bf9599a`。短安装地址只映射到这份已验收版本，不会静默切换到未经验证的新包。

## 你会得到什么

- 可逐页查看、放大和全屏展示的静态 Preview；
- 一组按顺序编号的高清 PNG；
- 可以一次下载全部页面的 ZIP；
- 可追踪的拆页、确认和生成状态。

## 常见问题

<details>
<summary><strong>安装到了哪里？</strong></summary>

安装器会根据系统选择用户级目录，不要求管理员权限。命令、Skill、运行环境和状态数据彼此隔离；升级时替换版本目录，不覆盖用户生成的内容。

</details>

<details>
<summary><strong>为什么一定要先确认拆页？</strong></summary>

每张图片都需要单独生成。先确认结构，可以在消耗生成资源之前调整页数、标题和材料边界，也能避免“拆错之后整套重做”。

</details>

<details>
<summary><strong>Preview 为什么是静态的？</strong></summary>

图片、页面数据和 ZIP 会在生成完成时准备好。Preview 不依赖一个必须长期存活的本地服务，因此在 macOS 和 Linux 上更稳定，也更容易迁移和归档。

</details>

<details>
<summary><strong>可以直接运行 Shell 安装吗？</strong></summary>

主要入口是 Codex Skill。高级用户也可以检查安装脚本后再执行，但普通用户不需要复制任何 Shell 管道命令。

</details>

## 技术与架构

想了解 Cloudflare 分发、跨平台安装、本地 CLI、Skill、状态目录和静态 Preview 的职责，可查看 [Image PPTGen 架构说明](https://image-pptgen-architecture.pages.dev/)。

<details>
<summary><strong>公开源码从哪里开始读？</strong></summary>

| 路径 | 作用 |
| --- | --- |
| `skills/generate-image-presentation/` | Codex Skill：约束安装、拆页确认和生成流程 |
| `packages/pptgen_toolkit/` | 跨平台 CLI、客户端与静态 Preview 打包 |
| `backend/` | 拆页、生成、状态、审计和产物服务 |
| `frontend/` | Preview 与本地审阅界面的前端源码 |
| `packaging/image/` | Linux、macOS 及实验性 Windows 安装适配层 |
| `deploy/installer-site/` | 当前短安装入口的 Cloudflare Pages 静态配置 |

这是一份从已验收 R62 整理出的干净公开源码快照，不包含私有 Git 历史、内部工作流、验收档案、数据库或用户数据。完整文件级来源见 [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md) 和 [PUBLIC_SOURCE_MANIFEST.tsv](PUBLIC_SOURCE_MANIFEST.tsv)。

</details>

参与开发前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请按 [SECURITY.md](SECURITY.md) 提交，不要在公开 Issue 中披露凭据或私人材料。

---

<p align="center"><sub>目前公开交付重点是可靠的 Codex Skill 使用路径；浏览器 Web 应用不是普通用户入口。Licensed under Apache-2.0.</sub></p>
