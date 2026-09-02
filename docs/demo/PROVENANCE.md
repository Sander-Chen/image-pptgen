# Public Demo Provenance

本目录只保存经筛选、可用于公开首页的演示图片。它不是完整验收证据目录，也不包含用户数据、会话内容、凭据或机器路径。

## 中国历史 Image 3.0 演示

| 字段 | 值 |
| --- | --- |
| 主题 | 纵览中国历史 |
| 来源 | Image PPTGen 3.0 评测样本 |
| 生成路线 | `public_image_3_0` / `codex_native_image` |
| 候选版本 | `0.1.1` |
| 源提交 | `47bbb1e4708032983796b01d26ce86a317632733` |
| 输入材料 | 公开样本 `eval-materials/chinese-history.md` |
| 原始结果 | 5/5 PNG 成功，Preview 与 ZIP 核验通过 |
| 安全结论 | 临时凭据挂载已移除；原始证据的 secret scan 通过；公开目录只复制生成图片 |

### 公开图片清单

| 公开文件 | 原始页 | 原始 PNG SHA-256 | 公开 WebP SHA-256 |
| --- | --- | --- | --- |
| `image-3-history/cover.webp` | `slide-01.png` | `ffd41efd986d8d8adf1e295314cd87a18c50205fb0765f5d252d58ee2668030c` | `7803ec77df9d7fc94a44144de82cda1fe40c3181ed9f8ef92784b8cb82af4256` |
| `image-3-history/middle-history.webp` | `slide-03.png` | `6152f024dadfe2b3d0eebfe402253ce795eca78f3f0b0e1be2fac0778cf2f626` | `28fd9163ccbcdbda3359dda617a070d99ffce6d2af44a45a9b9243f0755960a9` |
| `image-3-history/modern-china.webp` | `slide-05.png` | `0de314d1704ccc72ad4019bdcdc32932600ca7e378825bb8ecad3a3dfde4ea57` | `183d36004697973ce08f437b41e6468c85c8933d6f490626a7e0610b15eb8005` |

公开图片由原始 1672×941 PNG 等比例缩放为 1200×676 WebP，仅用于减小 GitHub 页面体积；未增加、删除或重写画面内容。

## 未采用的候选

- `ppt-gen-platform` 中的 HTML 路线截图：不是 Image 3.0 生成结果，未混入本页 Demo。
- 仅在文件名中出现 `gpt-image-2` 的 UI mockup：无法证明是演示文稿生成结果，未采用。
- Desktop/VM 验收截图：含操作环境和会话界面，不作为产品成品图。
