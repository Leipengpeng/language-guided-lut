# Language-Guided LUT

Language-Guided LUT turns a written color direction or a voice transcript into a reusable 3D LUT. An image editor creates a color reference from a small JPEG. The local fitter estimates a global RGB transform from that reference and applies the exported LUT to the original-resolution photo.

用一句话描述想要的调色。系统先从小图生成颜色参考，再导出 3D LUT，最后在本地套回原尺寸照片。也支持用两张照片直接追色。

v2.8.0 · [Codex Skill](skills/color-match-lut/SKILL.md) · [Backends and setup](skills/color-match-lut/references/benchmark.md) · [Algorithms](skills/color-match-lut/references/algorithms.md)

## First use in the agent

Attach the photo you want to grade and describe the look you want. A style name or one specific change is enough. If it matters, add how strong the grade should be and what to preserve. You can also attach a reference photo and say which colors or tones you want to borrow.

> 把这张照片调成清透日系。阴影稍微偏青，力度轻一点，保留自然肤色和原来的曝光。

> Give this photo a clean, airy look. Make the shadows slightly cyan and keep the original exposure and natural skin tones.

When you first invoke the skill without a complete request, the agent shows an example like this and asks for the missing photo or color direction.

## Setup

Run these examples from the repository root with Python 3.11 or later. The core uses NumPy, Pillow and SciPy.

~~~sh
python -m pip install -r requirements.txt
~~~

For Codex, install the [skills/color-match-lut](skills/color-match-lut) folder as a skill. For standalone use, run the Python entry points below.

## Start with words

1. Prepare a small JPEG and an image-editing prompt. The default long edge is 1080 pixels.

   ~~~sh
   python skills/color-match-lut/scripts/text_style_lut.py prepare --content input.jpg --style-text "Warm highlights and cool cyan shadows, with natural skin" --output out/text
   ~~~

   Open the [offline prompt tool](skills/color-match-lut/assets/color_prompt_tool.html) locally or use the [rewriting guide](skills/color-match-lut/references/color_prompt_rewriter.md). Pass a UTF-8 prompt file with <code>--image-prompt-file</code> to use your own reviewed wording.

2. Give <code>out/text/upload.jpg</code> and <code>out/text/prompt.txt</code> to an image-editing host. Save its returned color reference as <code>styled.png</code>. In Codex, the skill can use the built-in Image editor. Review the reference for composition, lighting and local recoloring before fitting.

3. Fit and export the LUT. With <code>--apply</code>, the script reloads the exported cube and renders the original photo locally.

   ~~~sh
   python skills/color-match-lut/scripts/text_style_lut.py finish --job out/text/job.json --styled styled.png --apply
   ~~~

   The default finish uses MKL/Oklab. With <code>--method auto --backend-root local-backends</code>, reference pixels select the route. Color uses the optional ColorFM-L backend. Near-neutral black and white uses MKL/Oklab with equal RGB output channels. [ColorFM-L setup](skills/color-match-lut/references/benchmark.md#colorfm-l).

## Start with a reference photo

~~~sh
python skills/color-match-lut/scripts/color_match_lut.py fit --content input.jpg --style reference.jpg --output out/reference --apply
python skills/color-match-lut/scripts/color_match_lut.py apply --lut out/reference/bures/look.cube --content another.jpg --output out/apply
~~~

The [skill](skills/color-match-lut/SKILL.md) and [benchmark guide](skills/color-match-lut/references/benchmark.md) cover shared LUT fitting, one-to-many and many-to-one review galleries, six comparison backends, and optional local CLIP pairing.

## Output and verification

- The default LUT is a 65³ <code>.cube</code> for full-range encoded sRGB. The renderer uses trilinear interpolation in RGB space.
- A global LUT gives the same output to the same input RGB anywhere in the frame. The renderer keeps the original pixel grid after EXIF orientation and writes a separate sRGB PNG. Color changes, clipping and quantization can make the RGB transform irreversible.
- Run reports record image hashes, the selected route, and LUT-to-PNG checks. The image-editing host handles the small reference. Fitting, LUT export and full-resolution rendering run locally.

Run the local tests with the core dependencies.

~~~sh
python -B -m unittest discover -s skills/color-match-lut/scripts -p "test_*.py"
~~~

## Attribution

Optional learned backends use separate upstream source trees and checkpoints. Obtain them from the sources and under the terms listed in the [benchmark guide](skills/color-match-lut/references/benchmark.md). The [upstream Apache-2.0 text](research/ColorFM-APACHE-2.0.txt) accompanies the ColorFM compatibility patch.

- [Linear Monge–Kantorovitch color mapping](https://doi.org/10.1049/cp:20070055)
- [Oklab](https://bottosson.github.io/posts/oklab/)
- [ColorFM](https://github.com/cszn/ColorFM)

