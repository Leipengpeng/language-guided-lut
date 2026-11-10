# Language-Guided LUT

Language-Guided LUT turns a written color direction or a voice transcript into a reusable 3D LUT. An image editor creates a color reference from a small JPEG. The local fitter estimates a global RGB transform from that reference and applies the exported LUT to the original-resolution photo. It also exports XMP presets and profiles for Lightroom and Photoshop Camera Raw.

用一句话描述想要的调色。系统先从小图生成颜色参考，再导出 3D LUT，最后在本地套回原尺寸照片。也支持双图追色，以及导出 Lightroom 和 Photoshop Camera Raw 的 XMP 预设与配置文件。

[Color grading Skill](skills/color-match-lut/SKILL.md) · [豆包与 Lightroom 实机用法](skills/color-match-lut/references/doubao-workflow.md) · [Backends and setup](skills/color-match-lut/references/benchmark.md) · [Algorithms](skills/color-match-lut/references/algorithms.md)

## First use in the agent

Attach the photo you want to grade and describe the look you want. A style name or one specific change is enough. For a film, director, photographer or broad look, the agent researches and selects a reference. You can add a strength or something to preserve, or supply your own reference if you have one.

> 把这张照片调成清透日系。阴影稍微偏青，力度轻一点，保留自然肤色和原来的曝光。

> Give this photo a clean, airy look. Make the shadows slightly cyan and keep the original exposure and natural skin tones.

When you first invoke the skill without a complete request, the agent shows an example like this and asks for the missing photo or color direction.

Before generating a color reference, the agent checks the local Python environment and the host's file, search and image-editing tools. If a required capability or dependency is missing, it stops and tells you what to configure. You can then ask it to set up the environment and resume.

## Use in Doubao Work

1. From a Git checkout, build a ZIP containing only the color grading Skill. You can also select the unpacked skill folder in Doubao.

   ~~~sh
   git archive --format=zip --output=language-guided-lut.zip HEAD:skills color-match-lut
   ~~~

2. Open **插件 · 技能 · 工作台** → **技能** → **添加** → **上传技能**. Select the ZIP, then choose **在对话中使用** on the installed Skill. Use a signed-in Doubao Work session that has access to the photo, a local Python environment and image editing.
3. Attach the original photo and describe the style in one sentence. For example, 「把这张照片调成《末代皇帝》的感觉。自己找剧照参考，给我原尺寸成片、LUT 和 XMP。」 For a film look, the agent searches and selects references before grading. You can request Bing Images and ask it to report the search provider it actually used.
4. Check the delivered PNG against the original dimensions. The .cube contains the LUT. To apply the editable XMP in Lightroom Classic, open the original, import the XMP from the Develop Presets panel, apply the preset, and use **Before/After** to compare.

Doubao uses Microsoft Bing Images by default. If a requested Google search fails, it switches to Bing. If Bing is also unavailable, it tries the host's built-in search, accessible Chinese search providers, or official galleries and reports the actual source used.

The [Doubao and Lightroom walkthrough](skills/color-match-lut/references/doubao-workflow.md) covers first-use guidance, search fallback and output verification. The [environment guide](skills/color-match-lut/references/environment_setup.md) explains missing dependencies, domestic mirrors and offline setup.

## Recommended input

**Starting from a camera RAW?** Develop it in your RAW editor, then export a **full-resolution 16-bit SDR PNG in sRGB** and attach that PNG to the Skill. The Skill also accepts 8-bit PNG and JPEG. The final LUT render keeps the input dimensions after EXIF orientation and writes a 16-bit PNG when the input is 16-bit. Text-guided grading uses a separate small JPEG for the image editor.

**如果原片是相机 RAW，建议先在 RAW 编辑器中完成基础显影，再导出原尺寸、16 位、SDR、sRGB 的 PNG 交给 Skill。** 也兼容 8 位 PNG 和 JPEG。16 位 PNG 的本地 LUT 套用会输出原尺寸 16 位 PNG。

## Setup

Run these examples from the repository root with Python 3.11 or later. The core uses NumPy, Pillow, SciPy and PyPNG. Use sRGB for 16-bit PNG input, or install Little CMS 2 for other embedded ICC profiles.

Check an existing environment before installing packages. Exit code 0 means the selected local checks passed. Exit code 2 lists missing packages or Skill files. The host's image-editing and search tools are checked separately by the agent.

~~~sh
python skills/color-match-lut/scripts/check_environment.py --guide --xmp
~~~

If dependencies are missing, install them in the Python environment selected for grading, then run the check again.

~~~sh
python -m pip install -r requirements.txt
~~~

境外 PyPI 不可达时，优先使用国内镜像。下面的源设置只作用于本条安装命令。完全离线时使用[离线包安装方法](skills/color-match-lut/references/environment_setup.md#国内镜像与离线安装)。

~~~sh
python -m pip install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple --timeout 20 --retries 1
~~~

For Codex, install the [skills/color-match-lut](skills/color-match-lut) folder as a skill. For standalone grading, use the Python entry points below.

## Start with words

Use a single-image prompt for concrete changes such as “lift the shadows” or “keep the skin tones natural and make the highlights warmer.” For a vague look or named inspiration, the agent starts with reference research.

1. Prepare a small JPEG and an image-editing prompt. The default long edge is 1080 pixels.

   ~~~sh
   python skills/color-match-lut/scripts/text_style_lut.py prepare --content input.png --style-text "Warm highlights and cool cyan shadows, with natural skin" --output out/text
   ~~~

   Open the [offline prompt tool](skills/color-match-lut/assets/color_prompt_tool.html) locally or use the [rewriting guide](skills/color-match-lut/references/color_prompt_rewriter.md). Pass a UTF-8 prompt file with <code>--image-prompt-file</code> to use your own reviewed wording.

2. Give <code>out/text/upload.jpg</code> and <code>out/text/prompt.txt</code> to an image-editing host. Save its returned color reference as <code>styled.png</code>. In Codex, the skill can use the built-in Image editor. Review the reference for composition, lighting and local recoloring before fitting.

3. Fit and export the LUT. With <code>--apply</code>, the script reloads the exported cube and renders the original photo locally.

   ~~~sh
   python skills/color-match-lut/scripts/text_style_lut.py finish --job out/text/job.json --styled styled.png --apply
   ~~~

   The default finish uses MKL/Oklab. With <code>--method auto --backend-root local-backends</code>, reference pixels select the route. Color uses the optional ColorFM-L backend. Near-neutral black and white uses MKL/Oklab with equal RGB output channels. [ColorFM-L setup](skills/color-match-lut/references/benchmark.md#colorfm-l).

## Automatic reference-guided grading

Attach the original photo and name a film, a director, or a look, such as “Give this photo an old Hong Kong cinema look.” The agent searches publicly available images, verifies sources, and filters out posters, portraits of creators, and repeated frames. It identifies recurring palettes across the eligible examples, then selects a frame in the same palette group with lighting and subject matter closest to the original. A night street photo gets priority for night street references. A sunny portrait gets priority for sunny portraits.

The selected frame supplies color evidence. The image editor receives a small JPEG of the original and a globally graded copy of that same photo as the second image. It follows the source photo’s composition, subjects and lighting. The skill fits the returned reference locally, renders the original at full resolution, and exports a 3D LUT and an editable Normal XMP.

The [search and selection guide](skills/color-match-lut/references/style_reference_search.md) defines the ranking and evidence record. The [reference-guided workflow](skills/color-match-lut/references/image_reference_providers.md) covers the local guide, ordered image inputs, and fallback when a host lacks image search or multi-image editing.

**给原片和一句风格描述即可。** 例如「调成《末代皇帝》的感觉」或「有点老港片的感觉」。Agent 自行搜索并筛选样张，先找出现最多的配色，再在同组里优先选和原片同光照、同题材的画面。网页样张提供配色依据，Image 使用原片小图和同图引导。结果包含原尺寸 PNG、LUT、可编辑 XMP 和样张来源。

## Start with a reference photo

~~~sh
python skills/color-match-lut/scripts/color_match_lut.py fit --content input.png --style reference.jpg --output out/reference --apply
python skills/color-match-lut/scripts/color_match_lut.py apply --lut out/reference/bures/look.cube --content another.jpg --output out/apply
~~~

The [skill](skills/color-match-lut/SKILL.md) and [benchmark guide](skills/color-match-lut/references/benchmark.md) cover shared LUT fitting, one-to-many and many-to-one review galleries, six comparison backends, and optional local CLIP pairing.

## Export Lightroom and Camera Raw XMP

Generate editable presets from an existing LUT and the original photo it was fitted for. The fitter uses RAWmakase's measured curve and mixer responses in this example. Color presets fit point curves and eight-band HSL in three combinations. Black-and-white presets fit a master curve, with or without a B&W mix.

~~~sh
python skills/color-match-lut/scripts/lut_to_xmp.py --content input.png --lut out/reference/bures/look.cube --output out/xmp --name "My Look" --engine rawmakase
~~~

The output includes editable <code>.xmp</code> files, local previews, and a fit report. Import the XMP files into Lightroom Classic's Presets panel. In Photoshop Camera Raw, put the XMP files in a ZIP and use **Import Profiles & Presets**. Local previews approximate the Adobe controls. Actual Adobe rendering can differ.

To use the 3D transform as a **Creative Profile** with a single amount control, export a separate XMP profile. The default grid is up to 32³.

~~~sh
python skills/color-match-lut/scripts/lut_to_xmp_profile.py --lut out/reference/bures/look.cube --output out/my_look_profile.xmp --name "My Look"
~~~

## Output and verification

- The default LUT is a 65³ <code>.cube</code> for full-range encoded sRGB. The renderer uses trilinear interpolation in RGB space.
- A global LUT gives the same output to the same input RGB anywhere in the frame. The renderer keeps the original pixel grid after EXIF orientation and writes a separate sRGB PNG. Color changes, clipping and quantization can make the RGB transform irreversible.
- Run reports record image hashes, the selected route, and LUT-to-PNG checks. The image-editing host handles the small reference. Fitting, LUT export and full-resolution rendering run locally.

## Attribution

Optional learned backends use separate upstream source trees and checkpoints. Obtain them from the sources and under the terms listed in the [benchmark guide](skills/color-match-lut/references/benchmark.md). The [upstream Apache-2.0 text](skills/color-match-lut/references/colorfm/ColorFM-APACHE-2.0.txt) accompanies the ColorFM compatibility patch.

- [Linear Monge–Kantorovitch color mapping](https://doi.org/10.1049/cp:20070055)
- [Oklab](https://bottosson.github.io/posts/oklab/)
- [ColorFM](https://github.com/cszn/ColorFM)
- [RAWmakase](https://github.com/pch/rawmakase) and [Photo Room](https://github.com/Hiemenz/photo-room). The XMP response implementations retain their upstream licenses and source attribution.
