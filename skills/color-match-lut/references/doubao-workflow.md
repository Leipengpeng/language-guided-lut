# 豆包工作与 Lightroom 实机用法

## 安装并启动

1. 在 Git 仓库根目录打包单个调色 Skill。ZIP 的顶层是 color-match-lut 文件夹。

   ~~~sh
   git archive --format=zip --output=language-guided-lut.zip HEAD:skills color-match-lut
   ~~~

2. 在已登录的 Windows 豆包桌面端进入「插件 · 技能 · 工作台」，选择「技能 → 添加 → 上传技能」。选择 ZIP，也可以选包含 SKILL.md 的单个技能文件夹。确认它已启用，点「在对话中使用」进入工作对话。
3. 附一张原片和一句风格描述。电影、导演、摄影师或模糊风格由豆包查找、筛选参考。需要指定搜索入口或输出时可以放在同一句。

> 把这张照片调成《爱乐之城》的感觉。请自行用 Bing 图片搜索找剧照，给我选中的出处、原尺寸成片、LUT 和 XMP。

相机 RAW 先显影成原尺寸、16 位、SDR、sRGB PNG。也支持 8 位 PNG 和 JPEG。豆包需要能读取原片、使用图片编辑工具并运行本地脚本。同名技能已存在时，可以更新已安装的 Skill。

## 第一次使用与环境检查

先向用户说明用法。「发一张原片，再说一句想要的风格；还可以说力度、肤色保护和输出格式。我会先检查工具和环境，缺组件时告诉你怎么配置。」已有完整任务时，直接检查。

确认豆包能读取照片、执行本地脚本并编辑上传图片。多图引导还需支持两张图片作为同一次编辑输入。优先复用已有的 Python 3.11+，将 `<skill>` 替换为本次安装目录，用实际调色的解释器执行。

~~~sh
python "<skill>/scripts/check_environment.py" --guide --xmp
~~~

结果为 `blocked` 时停止调色，向用户列出缺失的环境、包名或随包文件，以及配置命令。`png` 由 PyPNG 提供，`PIL` 由 Pillow 提供。使用国内镜像安装和完全离线的处理见[环境配置说明](environment_setup.md)。需要用户配置时，把具体缺项说清楚。用户要求配置后再安装，检查通过后继续。ColorFM 等可选后端缺资源时列出缺项；用户指定的方法不可用时说明原因，选择其他方法后记录实际算法。

## 搜索入口与交付

用户指定 Google 等入口时先尝试该入口；Google 不可用时切换微软必应。未指定时，豆包优先使用微软必应图片搜索，可尝试 `https://cn.bing.com/images/search`。Bing 与微软必应是同一个服务；全球入口不可达时，国内入口可以作为一次区域回退。

必应也不可用时，尝试豆包内置图片搜索、可用的国内网页搜索或作品官方图集。记录实际搜索入口、可核验出处和失败原因。每个失败入口有限尝试；两轮内仍找不到有效样张时，明确说明未完成网络研究，并按用户原意及保护约束选择可用的单图编辑路线。图像编辑能力缺失时按上节停止并报告。

可用样张保留去重候选、配色统计、光照与题材匹配。配色并列时按[选图规则](style_reference_search.md#筛图与按多数选择)处理和标注。对照实际生成参考和原片，核查画幅与内容。用本地脚本把导出的 LUT 套回原片，再验证交付 PNG 的实际尺寸与 EXIF 转正后的原片一致。分别标注 Image 调色参考、原尺寸 PNG、.cube、Normal XMP 和参考来源。

## 用 Lightroom Classic 核对 XMP

1. 导入同一张原图。进入「修改照片」，从「预设」面板的加号选择「导入预设」，选本次生成的 Normal .xmp。
2. 找到预设并点击应用，检查名称和修改历史。按 Y 进入「修改前／修改后」，用工具栏选择左右并排。
3. Lightroom 应用的是可编辑预设。软件的渲染和原尺寸 LUT PNG 可分别检查。

[Adobe 预设导入说明](https://helpx.adobe.com/lightroom-classic/desktop/process-and-develop-photos/apply-presets.html) · [Adobe 修改前后对照说明](https://helpx.adobe.com/lightroom-classic/desktop/process-and-develop-photos/develop-module-tools.html)
