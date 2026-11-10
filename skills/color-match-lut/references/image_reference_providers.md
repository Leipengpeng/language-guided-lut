# 可选双图引导调色

适用于风格描述或指定来源，并且 [自动找图与选图](style_reference_search.md) 已找到适用于原片的配色方向。仅调整明确的色彩参数或无法双图编辑时，从 [Skill 的单图流程](../SKILL.md#文字风格入口) 开始。用户已给出参考照片并要求直接匹配时，可直接用本地 `fit`。

## 从风格来源写成指令

1. 从 [自动找图与选图](style_reference_search.md) 取得配色方向、选中的原始样张、来源和选图理由。用户原意、明确指定的配色、力度和保护项优先。样张证据不足时，沿用用户原意加保护约束的单图流程，并保留已检索的证据。
2. 将用户原意译成简洁英文，保留准确的来源专名。按输入色相、饱和度和明暗写两到三条可以执行的调色动作。写清变化的颜色、作用的明暗范围和力度，优先沿用用户指定的力度。下面是一段配色方向示例。

   ```text
   Requested look: The Aviator, with a restrained two-color film palette.
   Shift existing cool midtones toward soft blue-green cyan. Keep existing
   warm reds and oranges distinct and the brightest neutrals gently warm.
   Keep shadow and highlight detail, natural skin and the original lighting.
   ```

   将已审核的方向存成 UTF-8 文本，可用 `--image-prompt-file` 传入。`prepare` 会补充两张图的角色和全局保护项，包含保留构图、身份、细节和现有照明、同类颜色全图同规则、避免选区及重打光。已明确匹配内置风格时，可直接由内置模板生成完整提示词。网上样张提供配色证据，实际交给模型的第二张图来自用户原片。

## 准备引导图

- 主参考的配色与 [内置电影配色](../assets/film_color_corpus.json) 一致且用户未指定不同力度时，用 `--guide-style <id> --guide-baseline`。默认的引导强度保存在 [配置文件](../assets/reference_guide_baseline.json)，任务记录包含采用的参数和哈希。
- 新方向、自定义力度或内置模板与样张不一致时用 `--guide-spec guide.json`，按调色意图设置全局 Oklab 配方。可用 `--guide-strength` 指定手动力度，默认为 1。已有全局 sRGB 3D LUT 时可用 `--guide-cube look.cube`。

```sh
python skills/color-match-lut/scripts/text_style_lut.py prepare --content input.png --style-text "The Aviator, restrained two-color palette" --style-research out/references/selection.json --image-prompt-file out/references/image-prompt.txt --guide-style aviator_two_color --guide-baseline --output out/guided
```

使用检索出的新方向时，由 Agent 按已核对的颜色关系生成 guide.json，使用 --guide-spec guide.json 替换内置配置选项。以下自定义配方示例略加对比、降低整体色度，让暗部偏青蓝，高光微暖。数值用于生成本地引导，发给 Image 的调色方向用定性描述。

```json
{
  "contrast": 1.03,
  "lift": 0.0,
  "chroma": 0.92,
  "shadow_ab": [-0.006, -0.012],
  "highlight_ab": [0.002, 0.009]
}
```

## 提交给 Image

在同一次多图编辑请求中发送 `prompt.txt` 和 `job.json` 内按序排列的 `image_inputs`。

1. 图一 `upload.jpg` 是压缩原片，决定完整画面的构图、内容和原有光照。
2. 图二 `guide.jpg` 是同一张原片的本地全局调色版本，提供目标配色、明暗和力度。

默认只提交小图。工具返回一张调色参考后，检查画幅、结构、光照和色彩一致性，再运行 `finish --job out/guided/job.json --styled edited.jpg --apply`。脚本使用图一的像素与返回图拟合 LUT，重读 LUT 后可套回全尺寸原片。

- 内置 Image 用宿主支持的多图编辑入口，保留上述顺序。
- 火山方舟 Seedream 可按顺序把两张图放进 `image` 数组。[火山方舟图片生成教程](https://console.volcengine.com/ark/region%3Acn-beijing/docs/image-generation-tutorial)
- 腾讯云 TokenHub 的混元多图编辑接口将 Prompt 与两张 `image_url` 放进同一个用户消息，图片按原顺序排列。[腾讯云 Hy 生图调用指南](https://cloud.tencent.com/document/product/1823/135745)

`prepare` 和 `finish` 在本地运行。云端模型调用由具备多图编辑能力的宿主或 API 客户端完成。
