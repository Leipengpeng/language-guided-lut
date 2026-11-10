---
name: color-match-lut
description: "输入一张原片和一句调色描述。遇到电影、导演、摄影师或模糊风格时，主动搜索并按多数配色、原片光照和题材选参考，再用 Image 生成颜色参考，导出原尺寸成片、3D LUT 和可编辑 XMP。也支持直接参考照追色和套用已有 LUT。"
---

# Language-Guided LUT

文字调色只需要一张原片和一句话。风格、片名或导演名都够用。Agent 负责查找、筛选和选择参考，随后组织 Image 编辑、本地追色和导出。用户给了参考照并要求直接匹配时，使用 `scripts/color_match_lut.py` 做本地追色。只需转译指令时，使用下面的离线工具或通用 Prompt。

文字模式使用 Image 神经图像模型生成参考。ColorFM-L 用本地预训练模型拟合 LUT，MKL/Oklab 用本地颜色统计拟合。两条分支都由脚本在本地将 LUT 套用到高清原片。

## 首次使用

在当前对话首次启用 Skill 时，根据已有输入决定如何开场。

- 用户刚打开 Skill 或询问用法时，请用户发一张要调色的照片，并用一句话说想要的风格或具体色调变化。已有 16 位 SDR PNG 时优先使用原尺寸文件，也接受 8 位 PNG 和 JPEG。力度和想保留的内容可以一起说。参考照是可选项。用户愿意附上时，再标明两张图的角色。
- 提供一句能直接照着说的例子：「把这张照片调成《末代皇帝》的感觉。」
- 用户已经在布置任务时，只询问完成当前任务所缺的输入。原片和任意非空调色描述已经齐全，或两张照片的角色已经明确时，直接开始。风格模糊、只有片名或导演名时也自行检索，不要求用户补参考图或代选。
- 首次接待可简短说明：「发一张原片，再说一句想要的风格，例如『把这张照片调成《爱乐之城》的感觉，肤色自然一点，给我原尺寸成片和 XMP』。RAW 建议先导出原尺寸 16 位 sRGB PNG，也接受 JPEG。开始前我会检查本地环境和可用工具；缺少组件时会列出配置方法。」已有完整输入时直接检查并执行。

## 开始前检查

在生成颜色参考前，用即将执行调色的同一个 Python 运行 `python "<skill>/scripts/check_environment.py" --guide --xmp`。只做双图统计追色时可省略 `--guide`，不需要 XMP 时可省略 `--xmp`。检查器仅检查本地依赖和随包资源；Agent 另行确认原片可读、宿主能执行 Python、图片搜索和图像编辑工具实际可用。用户指定 ColorFM 等可选方法时，同时按后端说明核对其代码、权重和依赖，准备齐全后才开始生成参考。

环境检查返回 `blocked` 或非零退出码时，停止本次调色，明确告诉用户使用的解释器、缺失模块或文件，以及适合当前系统的配置命令。没有 Python、脚本执行权限或图像编辑能力时，同样列明缺少的能力。按[环境配置与失败处理](references/environment_setup.md)提供安装建议，用户要求配置后再安装和复查。境外软件源不可达时优先使用国内镜像；完全离线时使用适配的离线包。搜索失败与本地依赖缺失分别处理，搜索的可用替代入口见[选图流程](references/style_reference_search.md#理解描述并搜图)。

## 推荐流程

默认推荐**单张内容图 + 单张参考图，逐对独立求 MKL/Oklab，逐对导出 LUT**。用户明确要求共享滤镜或融合对照时，再启用下面的多图模式。

批量输入也沿用这个原则：一张内容配多张参考时，每个组合各算一个 LUT；多张内容配一张参考时，每张内容各算一个 LUT；两侧都多图时，按已经明确的配对分别执行。配对关系无法从用户输入或上下文确定时，先确认配对意图，不按文件顺序盲配。

普通批量逐对调用单图 `fit`，不能把整个文件夹直接交给多图 `fit` 来代替，因为后者会聚合成共享滤镜。`compare_one_many.py` 会额外计算融合结果，只用于用户明确要求独立与共享对照。

## 文字风格入口

输入为内容照片和用户原句或语音转写。仅要求改写指令时只执行第 1 步。已有实际风格参考图时直接走双图追色。

**默认先研究模糊风格。** 风格名、情绪描述、片名、导演、摄影师、胶片等指代配色时，按[自动搜索与选图](references/style_reference_search.md)完成检索。先从有效样张确定反复出现的配色，再在同组内按原片光照和题材选一张参考。没有浏览器控制时使用宿主现成的搜索工具。开始制作引导图前保存 selection.json。即使风格命中内置模板，也先检索核对模板是否贴合原片和所选参考。

在豆包工作中安装、调用或交付时，先读[豆包与 Lightroom 实机用法](references/doubao-workflow.md)，核对实际可用的图片搜索、图像编辑和 Python 环境。用原片加一句话启动，按用户指定的搜索入口尝试，报告实际使用的入口。

找到可信方向且宿主支持多图编辑时，按[双图引导](references/image_reference_providers.md)准备和提交，取得返回图后从下方第 4 步继续。已有明确颜色、明暗、曝光等直接调整且未要求模仿风格时，按下面的单图流程。搜索不可达、证据不足或宿主不支持多图时，可走已有单图编辑能力支持的路线，保留英文原意和固定保护约束，并说明实际原因。完全离线且无可用本地图像编辑模型，或图像编辑服务不可用时，停止文字生图流程并说明缺失条件；已有本地参考图或 LUT 时可完成对应的本地操作。

1. 将用户原话和明确保护项分别记录，将原意简洁译成英文，保留专名、指定的颜色和力度，再加入固定保护项。已有可信配色线索时，可按原片已有色相和明暗范围单列定性建议。同一范围的颜色在整张图中按同一规则处理。可用 [离线转译页](assets/color_prompt_tool.html)、下方命令或[通用转译 Prompt](references/color_prompt_system_prompt.txt)核对原话和保护项。

   ```text
   node "<skill>/assets/color_prompt_compiler.js" "整体暖一点，阴影偏冷青"
   ```

2. 以原话和审核后的指令运行 `prepare`。脚本做 EXIF 转正、ICC→sRGB、缩小和 JPEG 压缩。文字模式默认先用最长边 1080。最长边 256 用于节省流量或作低分辨率对照，4K 档按最长边 3840，仅在明确要求比较时用。各档保留宽高比和较小原图的尺寸，默认 JPEG 质量 85。原话保存在 `job.json`，实际发送的完整指令存为 `prompt.txt`。文字转译流程使用 `--image-prompt-file`。做过研究时另传 `--style-research`，包括研究后回退到单图的任务。

   ```text
   python "<skill>/scripts/text_style_lut.py" prepare --content "原图.jpg" --style-text "整体暖一点，阴影偏冷青" --image-prompt-file "调色指令.txt" --output "文字调色任务" --edge 1080
   ```

3. 读取 `job.json` 和 `prompt.txt`，使用宿主实际可用的图片编辑工具，例如 Codex 的 `image_gen` 或豆包的 Seedream。先检查 `upload.jpg`，只把准备好的压缩 JPEG 交给 Image，保持不透明背景。每次都明确要求一套全图共用的颜色和色调映射。同一输入 RGB 在任意位置得到同一输出，映射只取决于已有颜色和明暗。禁止按人物、背景、物件或画面位置选区，以及左右分色、镜像配色、局部渐变或补光。肤色保护通过同一映射保留自然肤色与皮肤纹理，黑白时保留肤色对应的明暗层次。保留人物身份、构图、裁切、物件、原有光源的数量、位置与方向，以及高光阴影的形状和分布。未要求改变整体曝光时保持贴近原片，明示调整也要保留高光和暗部细节。对比色从原片已有的色相或明暗范围建立，作用于全图对应的颜色。禁止新增霓虹、彩色光源、光晕或重打光。把 `prompt.txt` 作为实际执行的调色指令，记录提示词和工具调用。

4. 原样保存每次 Image 返回文件。执行 `finish` 前，将返回图与上传小图对照，检查取景、物件、身份、光照与肤色，并比较原片中相近颜色在不同位置的变化。若发现按物件选区或局部染色、左右分色或镜像配色、新增光源或受光变化，以及重绘、构图变化或新增物件，将该图标为不通过并修订指令。只用通过检查的参考图拟合。本地链接或审阅页应明确区分 Image 参考图与 LUT 成片。用户要求先审阅参考图时，在确认后再拟合。Image 返回失败时保留准备文件，报告停在哪一步。拟合源使用实际上传 JPEG 的解码像素，目标为通过检查的 Image 参考图。最终照片由保存后重读的 LUT 套到原片。

   ```text
   python "<skill>/scripts/text_style_lut.py" finish --job "文字调色任务/job.json" --styled "AI调色参考.png" --apply
   ```

需要让彩色和黑白走不同算法时，给 `finish` 加 `--method auto`。脚本检查实际返回图的 sRGB 像素，彩色参考走 ColorFM-L，黑白参考走 MKL/Oklab，并将该分支导出的 LUT 输出锁为中性灰。实际方法和判定结果记入 `report.json`。

   ```text
   python "<skill>/scripts/text_style_lut.py" finish --job "文字调色任务/job.json" --styled "AI调色参考.png" --method auto --backend-root "<本地后端目录>" --apply
   ```

省略 `--method` 或显式指定 `--method mkl_oklab` 时，沿用 MKL/Oklab 流程。指定 `--method colorfm` 可直接选择 ColorFM-L。彩色分支使用[本地模型依赖](references/benchmark.md#本地运行)，可用 `--device auto|cpu|cuda` 指定设备，依赖缺失时报告具体错误。黑白分支仅需基础 Python 依赖。`--strength`、`--tone-strength` 和 `--color-strength` 适用于 MKL/Oklab。

用户要求比较提示词或上传尺寸时，每组只改变一个变量。记录实际上传和返回尺寸，保留各组 Image 参考图和 LUT 成片。

只要原图未改变，`finish` 可重用已有 AI 参考。完整文字调色用 `--apply` 保留转正后的原尺寸，再用同一原片和本次 LUT [导出可编辑 XMP](#lightroom--photoshop-camera-raw-xmp)。交付原尺寸 PNG、`.cube`、最佳 Normal XMP、生成参考和选图记录。用户明确只要 LUT 时可跳过成图和 XMP。

记录上传尺寸、实际字节、原图哈希、返回图尺寸和 LUT 指标。压缩可减少上传体积。返回体积、调用费用和推理时间取决于 Image 服务。MKL/Oklab 只匹配全局 Gaussian 颜色统计，不能保证精确复现 AI 的局部效果或全部非线性调色。

Image 参考图仍可能出现重绘。用户反馈参考图重绘或调色太重时，先检查实际返回图，再根据反馈调整提示词并保留参考图供核对。

## 多算法对比和语义配对

用户要求比较不同追色算法时，使用 [六种后端说明](references/benchmark.md) 的单对对比入口。可选 ColorFM-L、ModFlows B6、D-LUT、Reference Match、MKL / Oklab 和 Reinhard / Lab。每种方法独立输出 `.cube` 和重载该 LUT 后的结果。模型后端使用本地后端目录中的上游代码及权重。没有可用依赖时，指出缺失项和实际完成的方法。

```text
python "<skill>/scripts/compare_benchmark.py" --backend-root "<本地后端目录>" --content "内容.jpg" --reference "风格.jpg" --output "六种方法对照" --methods all
```

Lab 直方图匹配可显式选用 `--methods histogram_lab`。下文的 `pooled`、`mean-cov`、`bures`、`robust-bures`、`pairwise`、`anchor` 是 MKL/Oklab 的多图选项，用于共享滤镜。`fit --methods all` 选择的是后者。

仅在用户需要从两组图片自动配对时，按 [本地 CLIP 配对说明](references/benchmark.md#可选的本地-clip-配对) 筛选主体和场景相近的候选。已有明确配对时直接追色。

## 输入与输出

- 确认哪些是**内容图**（要改色），哪些是**风格图**（希望模仿）；未能从上下文确定时，先询问这两个角色。支持每侧一张或多张，文件或文件夹均可。
- `fit` 为一个输入集合生成**一个共享 LUT**。两侧无须张数相同，也无须一一对齐。一对多/多对一的可视对照另用 `compare_one_many.py`，同时产生逐张独立 LUT 和整组共享 LUT，并明确区分两者。
- 默认导出 65³、编码 sRGB 输入/输出、全范围 `[0,1]` 的 `.cube`，保存在 `<输出目录>/<方法>/look.cube`。默认只导出 LUT；用户要求套用时，加 `--apply`，或用 `--apply-to` 指定另外的图片。
- PNG 结果必须来自**保存后重新读取的 LUT**。原图不覆盖；默认保留 EXIF 转正后的原始分辨率。只有用户同意预览尺寸或任务已指定上限时才设置 `--max-pixels`。
- 首选原尺寸 16 位 SDR PNG，其次 8 位 PNG、JPEG。也支持 8 位 WebP、TIFF、BMP。16 位 PNG 的本地统计追色、XMP 拟合和套用 LUT 保留高精度，套用成图以 16 位 PNG 交付。文字模式提交 Image 的 JPEG 仍是单独的参考预览。RAW、Log/HDR、HEIC 先显式导出为 SDR PNG。
- 追色工作空间为 sRGB。没有色彩标签的 SDR PNG 按 sRGB 处理。读取非 sRGB 的 16 位 ICC 文件需要可用的 LittleCMS；也可先从相机或修图软件导出带 sRGB 标记的 16 位 PNG。

## 运行

将 `<skill>` 替换为此 `SKILL.md` 所在目录。先检查现有 Python 是否可导入 `numpy`、`PIL`、`scipy`、`png`，优先复用已安装环境；需要时在独立虚拟环境安装 `scripts/requirements.txt`。核心 MKL/Oklab 和已有 LUT 套用仅需此 Skill 与上述 Python 依赖。可选研究后端需要额外资源，见六种后端说明。

推荐的单图生成并套用：

```text
python "<skill>/scripts/color_match_lut.py" fit --content "内容.jpg" --style "风格.jpg" --output "结果目录" --apply
```

按用户要求选择以下工作流。

多图共享 LUT（不产生图片输出）：

```text
python "<skill>/scripts/color_match_lut.py" fit --content "内容1.jpg" "内容2.jpg" --style "风格1.jpg" "风格2.jpg" "风格3.jpg" --methods bures --output "共享LUT目录"
```

一对多或多对一的可滚动对照（至少一侧仅一张图）：

```text
python "<skill>/scripts/compare_one_many.py" --content "内容.jpg" --style "风格文件夹" --output "一对多对照"
python "<skill>/scripts/compare_one_many.py" --content "内容文件夹" --style "风格.jpg" --output "多对一对照"
```

该比较入口会实际生成并套用 LUT，无须加 `--apply`。一对多逐张展示参考图与其独立追色结果，下方另外展示多参考融合的五种共享结果；多对一逐张展示内容、独立调色、共享滤镜结果，共享方法可切换。独立对按单图 MKL/Oklab 求解；图片集合权重仅影响共享拟合。

用户明确要求两侧多图共同拟合共享 LUT 时，使用 `color_match_lut.py fit --methods all --apply`。普通 `fit` 提供矩阵审阅。可滚动审阅入口适用于独立 LUT 与共享 LUT 对照。

用户指定偏重某些照片时，可以加 `--content-weights 1 2`、`--style-weights 3 1 1`。权重与展开后的文件顺序对应；文件夹按路径排序，默认不递归，需递归时加 `--recursive`。默认每张照片等权，像素更多不会得到更高权重；重复路径应改为权重表达。

单独套用已生成的 LUT，无须提供风格图：

```text
python "<skill>/scripts/color_match_lut.py" apply --lut "共享LUT目录/bures/look.cube" --content "新照片.jpg" --output "套用结果"
```

## 方法选择

单图 `fit` 默认选择 `bures`，对单对图使用 MKL/Oklab。多图输入也默认使用 `bures`。用户明确要求融合时，按下列方法选择，或用 `--methods all` 比较。

| 参数 | 含义 |
|---|---|
| `pooled` | 合并每张图的颜色分布，包含图间均值差异带来的方差 |
| `mean-cov` | 平均每张图的均值与协方差，不额外计入图间曝光和色偏的差异 |
| `bures` | 在 Gaussian Wasserstein 几何下融合每张图的颜色分布 |
| `robust-bures` | 根据颜色分布距离对离群图片软降权，再计算重心；它可能压低用户刻意选入的独特风格 |
| `pairwise` | 每个内容图与每个风格图分别求映射，再按两侧权重的乘积平均映射 |
| `anchor` | 只用两侧第一张非零权重图片，用于单对图拟合 |

`--strength` 控制整体强度，`--tone-strength` 控制 Oklab L，`--color-strength` 控制 a/b，范围都为 0–1，默认 1。没有明确需求时保留默认，避免混淆方法与参数变化。

多图统计与算法边界见 [references/algorithms.md](references/algorithms.md)。

## Lightroom / Photoshop Camera Raw XMP

完整文字调色和明确要求 Adobe 预设时，使用同一张内容图和本次一对一 sRGB .cube 拟合可编辑预设。用户已有 LUT 时可直接拟合。以下命令在本地运行。

~~~text
python scripts/lut_to_xmp.py --content original.png --lut look.cube --output xmp_review --name "My Look" --engine rawmakase
~~~

可用 --engine rawmakase 在拟合和预览中使用 RAWmakase v0.4.12 实测响应的本地移植，包含 ProPhoto RGB 点曲线及八色彩色、黑白混色表。--engine photo-room 可用于开源 Photo Room 响应的对照，默认 baseline 使用简化响应模型。三种引擎独立求解参数。RAWmakase 和 Photo Room 的图像属于各自的本地渲染，Adobe 实机效果需单独核对。

彩色导出 RGB 点曲线、主曲线加八色混色、RGB 点曲线加八色混色三个 Normal XMP。黑白 LUT 导出黑白主曲线和主曲线加黑白混色两种。点曲线与混色可在 Adobe 中继续调整。比较 report.json 中原片留出像素的 p95 色差，优先交付 p95 最小的 Normal XMP，并列时比较中位数和 RGB 探针 p95。其余候选留在输出目录供对照。使用同一份原片的 SDR sRGB 版本并保留基础曝光、白平衡和配置文件。XMP 只写点曲线、混色及彩色/黑白处理模式。预览为本地近似模型渲染，报告里的色差衡量本地模型对目标 LUT 的拟合。

用户想把 LUT 本身带进 Adobe 的「配置文件」面板时，可选 Creative Profile。它保留三维色彩映射，支持配置文件强度；Adobe 面板会把它作为一个整体使用。默认最大网格为 32³，更大的 LUT 会先重采样。确认目标 Adobe 版本支持更大网格时，可通过 --max-grid 64 保留更多采样。

~~~text
python scripts/lut_to_xmp_profile.py --lut look.cube --output my_look_profile.xmp --name "My Look"
~~~

Lightroom Classic 可从预设面板导入 Normal XMP，Creative Profile 在配置文件浏览器中查看。Photoshop 的 Camera Raw 从「预设」面板菜单导入配置文件和预设 ZIP。可在本地将所需的 XMP 压成一个 ZIP。Adobe 软件内的效果还需用实际导入后的渲染核对。

## 检查与交付

脚本保存 `run.json` 和每种方法的 `model.json`，记录源文件哈希、输入顺序、实际权重、统计量、矩阵、正则化、拟合残差、色域裁切和插值误差。PNG 检查保存后的 2048 个像素是否与重载 LUT 一致。数值通过不能替代美学评价。

可滚动比较入口生成本地 `review.html`：黑底、等尺寸、内容红框、参考蓝框、大字按钮；固定单张输入，逐行列出其余输入与对应结果，桌面右侧上下滚动，窄屏整页滚动。全部输入可直接看到，不强制压进一屏，也没有评分表或滑动叠图。点击图片查看完整结果，点击 LUT 保存其对应滤镜。仅在用户本地浏览，不用截图工具读取。

交付 LUT、可选 PNG、审阅页和运行记录的绝对路径链接。文字模式另附 Image 实际返回的参考文件。做过风格研究时，附选中的公开参考图链接和选图报告，简述多数配色、光照与题材为何匹配。
