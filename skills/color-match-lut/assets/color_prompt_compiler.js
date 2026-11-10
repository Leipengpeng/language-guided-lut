(function () {
  'use strict';

  const scopes = {
    overall: ['整体', 'the overall palette'],
    shadows: ['阴影', 'existing shadows'],
    midtones: ['中间调', 'existing midtones'],
    highlights: ['高光', 'existing highlights'],
  };

  const scopeWords = /全图|整体|整张(?:图)?|全局|阴影|暗部|暗处|黑位|中间调|中间亮度|高光|亮部|亮处/gu;
  const scopeOf = (word) => {
    if (/阴影|暗部|暗处|黑位/u.test(word)) return 'shadows';
    if (/中间/u.test(word)) return 'midtones';
    if (/高光|亮部|亮处/u.test(word)) return 'highlights';
    return 'overall';
  };

  const values = {
    warm: ['偏暖', 'warmer color temperature'],
    cool: ['偏冷', 'cooler color temperature'],
    cyan: ['偏冷青', 'cyan'],
    blue: ['偏蓝', 'blue'],
    cyanBlue: ['偏青蓝', 'cyan-blue'],
    green: ['偏绿', 'green'],
    teal: ['偏青绿', 'blue-green'],
    olive: ['偏橄榄绿', 'olive green'],
    yellowGreen: ['偏黄绿', 'yellow-green'],
    yellow: ['偏黄', 'yellow'],
    gold: ['偏金黄', 'golden yellow'],
    orange: ['偏橙', 'orange'],
    ivory: ['偏奶油白', 'ivory'],
    purple: ['偏紫', 'purple'],
    pink: ['偏粉', 'pink'],
    magenta: ['偏洋红', 'magenta'],
    neutral: ['保持中性', 'neutral'],
    up: ['提亮', 'Lift'],
    down: ['压暗', 'Lower'],
    firm: ['增加反差', 'Increase tonal contrast while retaining shadow and highlight detail'],
    soft: ['降低反差', 'Soften tonal contrast while keeping tonal separation'],
    less: ['降低饱和度', 'Reduce saturation while keeping distinct hue families'],
    more: ['增加饱和度', 'Increase saturation while keeping natural color separation'],
  };

  const paletteRules = [
    ['cyanBlue', /(?:偏|调成)?冷?(?:青蓝|蓝青)/gu],
    ['cyan', /偏青|冷青|青一点|^青$/gu],
    ['blue', /偏蓝|更蓝|蓝一点|调蓝|^蓝$/gu],
    ['teal', /(?:偏|调成)?青绿/gu],
    ['green', /偏绿|绿一点|^绿$/gu],
    ['olive', /(?:偏|调成)?橄榄绿/gu],
    ['yellowGreen', /(?:偏|调成)?黄绿/gu],
    ['gold', /(?:偏|调成)?金黄色?/gu],
    ['yellow', /偏黄|黄(?:一些|一点|点儿|点)|加黄|变黄|太黄|^黄$/gu],
    ['orange', /偏橙|橙一点|暖橙|偏琥珀/gu],
    ['ivory', /(?:偏|调成)?奶油白/gu],
    ['magenta', /(?:偏|调成)?(?:紫红|洋红|品红)/gu],
    ['purple', /偏紫|紫一点/gu],
    ['pink', /偏粉|粉一点/gu],
    ['neutral', /中性(?:色)?|去色偏|去偏色/gu],
    ['warm', /偏暖|更暖|暖(?:一些|一?点儿?|起来)|变暖|太暖|加暖|提高色温|升高色温|^暖$/gu],
    ['cool', /偏冷|更冷|冷(?:一些|一?点儿?|起来)|变冷|太冷|降低色温|降色温|^冷$/gu],
  ];
  const toneRules = [
    ['up', /(?:提亮|亮(?:一些|一?点儿?|起来)|变亮|抬(?:高|亮|一点|一下|起来)?|太亮|加(?:一点)?曝光|增加(?:整体)?曝光|提高亮度|^亮$)/gu],
    ['down', /(?:压暗|压低|变暗|太黑|暗(?:一些|一?点儿?|下去)|太暗|降(?:一点)?曝光|减少(?:整体)?曝光|降低亮度|^暗$)/gu],
  ];
  const contrastRules = [
    ['firm', /(?:高对比|对比(?:度|拉|加|强|高|硬)|反差)(?:度)?(?:太强|太高|拉高|拉满|大一?点|强一?点|高一?点|增强|加强|太大)|(?:提高|增加|加强|加大|拉高)(?:反差|对比度?)|反差(?:更?硬|更强)|更硬朗/gu],
    ['soft', /(?:低对比|低反差)|(?:对比度?|反差)(?:降低|降一点|弱一点|小一点|柔(?:一些|一点|点)|软一点|低一点)|(?:降低|减小|减弱|柔化)(?:反差|对比度?)|(?:柔和一点|柔一点|软一些|柔和一些|软一点)/gu],
    ['firm', /高对比|高反差/gu],
  ];
  const saturationRules = [
    ['less', /低饱和|饱和(?:度)?(?:再|更)?(?:低|降|减|小)(?:一些|一点|点)?|(?:降低|减少|减点|降点|压低)(?:整体)?饱和度?|淡(?:一些|一?点儿?)|去掉一些颜色|颜色(?:收|淡|克制)/gu],
    ['more', /高饱和|饱和(?:度)?(?:再|更)?(?:高|加|增|拉)(?:一些|一点|点|高|强|满)?|(?:提高|增加|增强|拉高|加(?:一?点)?)(?:整体)?饱和度?|饱和(?:度)?(?:别|不要)太高|颜色(?:艳|浓|鲜艳)|鲜艳(?:一些|一点|点)|艳(?:一些|一点|点|过头)|太艳|更鲜艳/gu],
  ];

  const negated = (prefix) => /(?:不要|不想(?:要)?|不能|不用|不必|别(?:再)?|避免|防止|拒绝|不(?:需要|打算|可以|允许|要|想|宜|太|用)?)[^，。；、！？,;.!?]{0,11}$/u.test(prefix);
  const intensity = (text) => {
    if (/别太重|别过头|不要太重|克制一点|一点儿?|一些|[冷暖暗亮淡软艳黄蓝]点儿?|稍微|稍稍|微微|微调|轻微|轻(?:一?点|一些|度)|小幅/u.test(text)) return 'slight';
    if (/明显|强烈|大幅|重一些|重度|拉满|越.+越好|对比拉大/u.test(text)) return 'strong';
    return null;
  };
  const blockedHue = (blocked, value) => blocked === value ||
    (blocked === 'warm' && ['yellow', 'gold', 'orange', 'ivory'].includes(value)) ||
    (blocked === 'cool' && ['blue', 'cyan', 'cyanBlue', 'teal'].includes(value)) ||
    (blocked === 'yellow' && ['gold', 'ivory'].includes(value)) ||
    (blocked === 'cyan' && ['cyanBlue', 'teal'].includes(value));

  const editorGuard = 'Photographic color-grading guardrails: Adjust only color and tonal curves. Simulate one global 3D LUT across the entire image. Identical input RGB values must produce identical output RGB values anywhere in the frame, regardless of position or subject. Apply hue-based and tonal adjustments uniformly wherever their input colors occur, including gentle treatment of skin-like colors. Do not use masks, local adjustments, separate foreground and background grades, left-right color splits, mirrored color effects, or position-based gradients. Do not infer or simulate lighting. If a style asks for contrasting hues, derive them from existing colors or tonal ranges and keep them independent of image position. Preserve the existing light sources, their count, type, position, direction and falloff, along with the spatial pattern of highlights, shadows and reflections. Do not add neon signs, glowing objects, colored light sources, new rim lights or localized red or blue light, or relight the scene. Do not add, remove or redraw objects, people or background elements. Preserve framing, camera viewpoint, objects, people, identity, facial features, skin texture, hair, clothing and background details. Keep exposure credible with detail in highlights and shadows. Avoid newly clipped highlights, crushed shadows, extreme saturation, and unnatural skin hues in color renders. Use global color relationships that can be represented by a 3D LUT. Return one full-frame photograph at the input aspect ratio.';
  const editScope = 'Edit the supplied photograph in place. Preserve the positions and shapes of existing scene elements, text and fine texture. Do not add, remove, move or replace scene elements. Do not redraw, crop, retouch, blur or add grain.';
  const briefPriority = 'Use the original wording below as color and tone guidance within the guardrails. Explicit user color, tone and preservation requests take priority over general style references.';
  const styleLead = /^(?:(?:我)?(?:想要|想|希望|要|调成)\s*)?(?:像|类似|参考|参照|仿照|模仿|按照|同款|风格\s*[:：])/u;
  const styleEnding = /(?:风格|电影感|电影|映画|质感|写真|摄影|影像|色调|调子|冷调|暖调|风|style|look|aesthetic)[」』”’"']?$/iu;
  const colorLead = /^(?:(?:我想(?:要)?|希望|稍微|略微|微微|再|把|给|请|想要|要)\s*)*(?:偏|更|调(?:成|得)|变|太|加|升高|提高|降低|降色温|去色偏|去偏色)/u;
  const limitedRange = /(?:红|橙|黄|绿|青|蓝|紫|粉|白|黑)(?:色(?:系|相|通道)?|通道)|肤色|皮肤|人脸|脸色|头发|眼睛|天空|背景|衣服|衣物|草地/u;
  const filler = /^(?:我想|帮我|麻烦了|谢谢|可以吗|嗯+|呃+)$/u;
  const intensityEnglish = {
    slight: 'a small but visible',
    medium: 'a clearly visible, photographic',
    strong: 'a pronounced, photographic',
  };

  function renderAction({ axis, value, strength }) {
    if (Object.hasOwn(scopes, axis)) {
      if (value === 'neutral') return 'Keep ' + scopes[axis][1] + ' neutral and retain existing color separation.';
      return 'Give ' + scopes[axis][1] + ' ' + intensityEnglish[strength] + ' shift toward ' + values[value][1] + '.';
    }
    if (axis === 'exposure' || axis.startsWith('tone_')) {
      const target = axis === 'exposure' ? 'the overall exposure' : scopes[axis.slice(5)][1];
      return values[value][1] + ' ' + target + ' by ' + intensityEnglish[strength] + ' amount.';
    }
    const degree = { slight: ' slightly', medium: ' noticeably', strong: ' substantially' };
    return values[value][1].replace(/^(Increase|Reduce|Soften)([^,]*?) while/u,
      (_, verb, object) => verb + object + degree[strength] + ' while') + '.';
  }

  function unsupportedEdit(clause) {
    const outOfScope = /(?:换|替换|删除|去掉|移除|添加|增加|加)(?:个|一[个点层]|一些|上)?(?:背景|天空|路人|行人|太阳|霓虹灯|灯光|颗粒|噪点|光斑|暗角|文字|水印)|抠图|换脸|瘦脸|磨皮|虚化|加光|重打光|重新打光|改变光线方向|变成(?:白天|夜晚|漫画|插画)|改成(?:白天|夜晚|日落)|(?:只|仅)(?:对|让|把|给|调|改|调整|修改|变|给到|影响){0,3}(?:天空|背景|衣服|衣物|衣服颜色|皮肤|肤色|脸|人脸|人物|眼睛|头发|草地|树|地面)|(?:天空|背景|衣服|脸|人脸|人物|眼睛|头发|草地)(?:单独|局部)|(?:天空|背景|衣服|脸|人脸|人物|眼睛|头发|草地)[^，。；、！？]{0,8}(?:提亮|压暗|偏青|调蓝|改(?:成|颜色)|(?:冷|暖|亮|暗|蓝|红|黄)一点|更(?:冷|暖|亮|暗|蓝|红|黄)|(?:色彩|颜色)(?:保持|别动|不要改|不变))|单独(?:处理|调整|调)(?:天空|背景|脸|衣服|肤色)/gu;
    return [...clause.matchAll(outOfScope)].some((match) => !negated(clause.slice(0, match.index)));
  }

  function compile(input, { strategy = 'guard-only', guidance = '' } = {}) {
    const wording = String(input ?? '');
    const text = wording.trim();
    const supplemental = String(guidance ?? '').trim();
    const actions = new Map();
    const limits = new Map();
    const assumptions = [];
    const conflict = new Set();
    const namedStyles = [];
    const unparsed = [];
    const stylePhrases = () => namedStyles.length ? namedStyles : unparsed;
    let skin = false;
    const unavailable = [];
    const result = (status, summary, question = null, prompt = null) => ({
      status, summary, question, prompt, assumptions, strategy,
      intent: {
        style: stylePhrases().join(' / ') || null,
        actions: [...actions.values()], constraints: [...limits.values()], protectSkin: true,
      },
    });
    if (!text) return result('clarify', '等待输入调色方向。', '希望这张照片的色彩或明暗怎么变？');
    if (text.length > 1200) return result('clarify', '输入超过本地工具的长度范围。', '能把调色目标缩成几句话吗？');
    if (strategy !== 'guard-only' && strategy !== 'guided') {
      return result('clarify', '无法识别所选策略。', '请选择保留原话或附加调色建议。');
    }

    const clauses = text.split(/[，。；、！？,;!?\r\n]+|(?:但是|而且|同时|然后|但|并且)(?=把|将|整体|全图|阴影|暗部|中间调|高光|曝光|饱和度|反差|对比度|稍微|提亮|压暗|不要|别)/u)
      .map((part) => part.trim()).filter(Boolean);
    if (!clauses.some((clause) => !filler.test(clause))) {
      return result('clarify', '还没有明确的调色方向。', '想先改哪一项？例如「整体暖一点」「阴影偏冷青」或「降低饱和度」。');
    }

    const setAction = (axis, value, strength) => {
      const prior = actions.get(axis);
      if (prior && prior.value !== value) conflict.add(axis);
      if (!prior || prior.value !== value || strength !== 'medium') {
        actions.set(axis, { axis, value, strength, source: 'explicit' });
      }
    };

    const setLimit = (axis, value, label, english, maximum = null) => {
      const key = axis + '/' + value;
      if (!limits.has(key) || maximum === null) limits.set(key, { axis, value, label, english, maximum });
    };

    for (const original of clauses) {
      let clause = original.normalize('NFKC');
      if (unsupportedEdit(clause)) {
        unavailable.push(clause);
        continue;
      }
      // Remove explicit preservation clauses before matching any directions within them.
      const before = clause;
      clause = clause
        .replace(/(?:脸(?:色)?|肤色|皮肤|人脸)(?:尽量)?(?:不要|别|不(?:要)?能?)(?:太|过|变|偏|发|被调){0,3}(?:黄|橙|绿|红|白|蓝|灰|暗)|(?:保护|保留|保持|保住|还原|维持)(?:原有|原来的|自然的|自然)?(?:肤色|脸色)|(?:别|不要)(?:把)?(?:脸(?:色)?|肤色|皮肤|人脸)[^，。；、！？]{0,4}(?:调|弄|变)?(?:太|偏|发)?(?:黄|橙|绿|红|白|蓝|灰|暗)/gu, () => {
          skin = true;
          return '';
        })
        .replace(/(?:构图|人物|场景|内容|细节|原始光线)(?:别改|不要变|不变)|(?:别|不要|不许|不能)(?:动|改|换|磨皮|虚化|去掉|删除|重画|裁剪|添加|加颗粒)(?:原图|背景|构图|脸|肤色|人物|路人|天空|细节|内容|文字)?|(?:保留|保持)(?:原图|原有|原来的)?(?:构图|场景|细节|人物|质感|光线方向)/gu, '');
      let recognized = clause !== before;
      const scope = [...clause.matchAll(scopeWords)];
      if (styleLead.test(clause.trim()) || (scope.length === 0 && styleEnding.test(clause.trim()))) {
        namedStyles.push(original);
        continue;
      }

      if (/(?:整体|全局|整张图?|全图)?(?:曝光|亮度)(?:维持原样|保持原样|不要变|别变|不变)|(?:保留|维持|保持|保住|锁住)(?:原来的|原有|原图的|整体|全局)?(?:曝光|亮度)|(?:别|不要)(?:改|动)(?:整体)?(?:曝光|亮度)/u.test(clause)) {
        setLimit('exposure', 'hold', '维持整体曝光', 'Keep the overall exposure close to the source.');
        recognized = true;
      }
      if (/(?:对比度?|反差)(?:保持原样|别变|不变|不要变)|(?:保留|保持|维持)(?:原有|原来的)?(?:对比度?|反差)/u.test(clause)) {
        setLimit('contrast', 'hold', '保留原反差', 'Keep the source tonal contrast.');
        recognized = true;
      }
      if (/(?:饱和度?|鲜艳度)(?:保持原样|别变|不变|不要变)|(?:保留|保持|维持)(?:原有|原来的)?(?:饱和度?|鲜艳度)/u.test(clause)) {
        setLimit('saturation', 'hold', '保留原饱和度', 'Keep the source saturation.');
        recognized = true;
      }

      if (/(?:别|不要|避免)(?:过曝|曝光过度|发白|高光(?:溢出|剪切))|高光(?:别|不要)(?:过曝|发白|溢出|剪切)/u.test(clause)) {
        setLimit('tone_highlights', 'clip', '保留高光层次', 'Protect bright detail and avoid washing out existing highlights.');
        recognized = true;
      }
      const parts = scope.length ? [
        ['overall', clause.slice(0, scope[0].index), false],
        ...scope.map((match, index) => [scopeOf(match[0]), clause.slice(match.index + match[0].length, scope[index + 1]?.index), true]),
      ] : [['overall', clause, false]];
      for (const [zone, body, scoped] of parts) {
        const part = body.trim();
        // Keep hue-family and subject-specific requests in the verbatim brief.
        // The global action schema cannot express their range faithfully.
        if (limitedRange.test(part)) {
          recognized = true;
          continue;
        }
        const toneAxis = zone === 'overall' ? 'exposure' : 'tone_' + zone;
        const rules = [[zone, paletteRules], [toneAxis, toneRules], ['contrast', contrastRules], ['saturation', saturationRules]];
        const strength = intensity(part) ?? 'medium';
        for (const [axis, entries] of rules) {
          const matched = [];
          for (const [value, regex] of entries) {
            for (const match of part.matchAll(regex)) {
              const prefix = part.slice(0, match.index);
              if (axis === zone && !scoped && match[0] !== part && !colorLead.test(part) && !negated(prefix)) continue;
              if (matched.some(([start, end]) => match.index < end && start < match.index + match[0].length)) continue;
              matched.push([match.index, match.index + match[0].length]);
              recognized = true;
              const capped = /(?:别|不要|不能|避免)\s*太\s*$/u.test(prefix) ||
                (/^太/u.test(match[0]) && negated(prefix)) || /(?:别|不要)太/u.test(match[0]);
              if (negated(prefix) || capped) {
                const target = scopes[axis]?.[1] ?? scopes[axis.slice(5)]?.[1];
                const extent = capped ? ' too much' : '';
                let english;
                if (Object.hasOwn(scopes, axis)) {
                  english = 'Avoid a ' + (capped ? 'strong ' : '') + values[value][1] + ' cast in ' + target + '.';
                } else if (axis === 'exposure') {
                  english = 'Do not ' + (value === 'down' ? 'darken' : 'brighten') + ' the image overall' + extent + '.';
                } else if (axis.startsWith('tone_')) {
                  english = 'Do not ' + (value === 'down' ? 'darken ' : 'lift ') + target + extent + '.';
                } else if (axis === 'contrast') {
                  english = 'Avoid ' + (value === 'firm' ? 'increasing' : 'decreasing') + ' tonal contrast' + extent + '.';
                } else {
                  english = 'Avoid ' + (value === 'more' ? 'increasing' : 'decreasing') + ' saturation' + extent + '.';
                }
                const zoneLabel = axis === 'exposure' ? '整体' : scopes[axis]?.[0] ?? scopes[axis.slice(5)]?.[0] ?? '';
                setLimit(axis, value, '避免' + zoneLabel + (capped ? '过度' : '') + values[value][0], english, capped ? 'slight' : null);
              } else {
                setAction(axis, value, strength);
              }
            }
          }
        }
      }
      if (!recognized && !filler.test(clause.trim())) unparsed.push(original);
    }

    if (unavailable.length) return result('unsupported', '这段指令含有选区、对象或纹理编辑，单个全局 LUT 无法按画面位置执行。', '可以改成全局色彩，或按阴影、中间调、高光来描述吗？');
    if (/^(?:\d{2,4}\s*年代|\d{4}(?:\s*年)?|\d{4}\s*[年.\-/]\s*\d{1,2}(?:\s*[月.\-/]\s*\d{1,2})?\s*[月日号]?|\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?)$/u.test(text.normalize('NFKC'))) {
      return result('clarify', '识别到日期或年代，缺少可确定的调色方向。', '这个时间是拍摄日期，还是想复刻那个年代的色彩？');
    }

    for (const { axis, value, maximum } of limits.values()) {
      for (const [key, action] of actions) {
        if (axis !== key) continue;
        if (value !== 'hold' && !blockedHue(value, action.value)) continue;
        if (!maximum || action.strength === 'strong') conflict.add(key);
        else action.strength = maximum;
      }
    }

    if (conflict.size) return result('clarify', '同一项调色收到冲突的要求。', '冲突项是' + [...conflict].map((axis) => ({
      exposure: '整体曝光', saturation: '饱和度', contrast: '反差',
    })[axis] ?? scopes[axis]?.[0] ?? scopes[axis.slice(5)]?.[0]).join('、') + '。想优先保留哪个方向？');
    if (strategy === 'guided' && !supplemental) {
      return result('clarify', '已选择附加调色建议。', '请填写想附上的建议，或切换到保留原话。');
    }
    const chosen = [...actions.values()];

    const directions = chosen.map((action) => {
      const zone = scopes[action.axis]?.[0] ?? scopes[action.axis.slice(5)]?.[0] ?? (action.axis === 'exposure' ? '整体' : '');
      const strength = { slight: '轻度', medium: '中等', strong: '明显' };
      const degree = action.value === 'neutral' ? '' : strength[action.strength] + '，';
      return zone + values[action.value][0] + '（' + degree + '用户明示）';
    });
    const sections = [editorGuard, editScope, briefPriority];
    if (strategy === 'guided') {
      const constraints = [...limits.values()].map((limit) => limit.english).filter(Boolean);
      if (skin) constraints.push('Keep existing skin hues close to the source and preserve natural complexion and texture.');
      const explicit = [...chosen.map(renderAction), ...constraints];
      if (explicit.length) sections.push('Explicit user color and tone directions: ' + explicit.join(' '));
      sections.push('Supplemental color direction (opt-in):\n' + supplemental + '\nApply this guidance only where it is compatible with the original user wording and the guardrails.');
    }
    sections.push('Original user wording (verbatim):\n' + wording);
    const prompt = sections.join('\n\n');
    const qualifiers = [...limits.values()].map((limit) => limit.label);
    if (skin) qualifiers.push('保留自然肤色');
    const styles = stylePhrases();
    const summary = [
      ...(styles.length ? ['保留风格原话 ' + styles.map((style) => '「' + style + '」').join('、') + '。'] : []),
      ...(directions.length ? [directions.join('、') + '。'] : []),
      ...(qualifiers.length ? ['保护项包括' + qualifiers.join('、') + '。'] : []),
    ].join('') || '按原话提交调色要求。';
    return result('ready', summary, null, prompt);
  }

  const api = { compile };
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    if (require.main === module) {
      const args = process.argv.slice(2);
      const { parseArgs } = require('node:util');
      const { values, positionals } = /^--(?:(?:strategy|guidance)(?:=|$)|$)/u.test(args[0] ?? '')
        ? parseArgs({ args, allowPositionals: true, options: { strategy: { type: 'string' }, guidance: { type: 'string' } } })
        : { values: {}, positionals: args };
      process.stdout.write(JSON.stringify(compile(positionals.join(' '), values), null, 2) + '\n');
    }
  } else {
    globalThis.ColorPromptCompiler = api;
  }
})();
