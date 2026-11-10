"""Build a same-photo grading guide and role-specific Image instructions."""

import numpy as np
from PIL import Image

from color_match_lut import from_oklab, grid, srgb_profile, to_oklab


FEEDBACK_EMPHASIS = (
    "\nCarry over the guide's visible color separation and strength across matching input colors. "
    "Keep its palette relationship consistent from the shadows through the midtones to the highlights. "
    "Use the original photograph's spatial detail and lighting geometry.\n"
)


def source_context(rgb):
    sample = rgb[::4, ::4]
    light, a, b = np.moveaxis(to_oklab(sample), -1, 0)
    low, median, high = np.percentile(light, [10, 50, 90])
    chroma = float(np.median(np.hypot(a, b)))
    details = []
    if median < .42:
        details.append("Keep the existing low-light exposure and readable separation in its dark tones.")
    if high > .87:
        details.append("Keep the brightest source detail and roll the grade off smoothly into the highlights.")
    if high - low < .32:
        details.append("Keep the narrow existing tonal range legible and avoid a steep contrast jump.")
    if chroma < .035:
        details.append("Apply the palette through the photograph's existing neutral and low-chroma color ranges.")
    return {"luma_p10": float(low), "luma_median": float(median), "luma_p90": float(high),
            "median_chroma": chroma, "notes": details}


def grade(rgb, spec, strength, tone_limit=1.6):
    lab = to_oklab(rgb)
    light = lab[..., 0].copy()
    # The tone portion rolls off at both ends, so stronger color does not
    # require a new exposure or imply harder clipping.
    tone_amount = min(tone_limit, strength)
    tone_delta = ((light - .55) * (spec["contrast"] - 1) + spec["lift"])
    lab[..., 0] = np.clip(light + tone_amount * tone_delta * 4 * light * (1 - light), 0, 1)
    a, b = lab[..., 1], lab[..., 2]
    gain = max(0., 1 + strength * (spec["chroma"] - 1))
    if spec["chroma"] > 0:
        # A smooth color-range guard also covers skin-like hues outside faces.
        hue = np.arctan2(b, a)
        chroma = np.hypot(a, b)
        warm = np.exp(-((hue - .75) / .53) ** 2)
        mid = np.exp(-((light - .61) / .23) ** 2)
        chromatic = np.clip((chroma - .02) / .04, 0, 1)
        protection = .68 * warm * mid * chromatic
    else:
        protection = np.zeros_like(light)
    lab[..., 1:] *= (1 + (gain - 1) * (1 - protection))[..., None]
    dark = (1 - light)[..., None] ** 2
    bright = light[..., None] ** 2
    tint = dark * np.asarray(spec["shadow_ab"]) + bright * np.asarray(spec["highlight_ab"])
    lab[..., 1:] += strength * tint * (1 - protection)[..., None]
    return np.clip(from_oklab(lab), 0, 1)


def choose_guide(rgb, spec, target, max_strength=5., tone_limit=1.6):
    # Probe a bounded 1-D family on the source only. The score guides amplitude
    # and is not used as a claim about the eventual Image result.
    from skimage.color import deltaE_ciede2000, rgb2lab

    sample = rgb[::5, ::5]
    lab = rgb2lab(sample)
    strengths = np.linspace(.75, max_strength, 18)
    results = [float(np.mean(deltaE_ciede2000(lab, rgb2lab(grade(sample, spec, strength, tone_limit)))))
               for strength in strengths]
    index = int(np.argmin(np.abs(np.asarray(results) - target)))
    amount = float(strengths[index])
    table = grade(grid(33), spec, amount, tone_limit)
    return table, {"requested_delta_e00": target, "guide_probe_delta_e00": results[index], "strength": amount}


def write_jpeg(path, rgb):
    image = Image.fromarray(np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    image.save(path, quality=86, optimize=True, icc_profile=srgb_profile())


def build_prompt(style, context, guide=False):
    head = (
        "Image 1 is the only photograph to edit and defines every pixel position. "
        "Return one full-frame photograph at Image 1's aspect ratio."
    )
    guide_note = (
        "Image 2 is a color guide made from the same photograph with a single global color mapping. "
        "Match its overall palette, tonal placement and clearly visible strength, while using Image 1 for every "
        "contour, texture, person, object and detail. Apply that color relationship smoothly across the full frame."
    )
    style_note = (f'Color and tone direction inspired by {style["film"]}:\n'
                  f'{style["style_en"].strip()}')
    controls = (
        "Create a clearly visible grade through a smooth global tone curve and broad existing color ranges. "
        "Use the same RGB-to-RGB response everywhere in the image. "
        "Keep the exposure pattern, light boundaries, fine detail, identities, objects, lettering, crop and camera viewpoint. "
        "Keep skin credible unless the specified look is monochrome. Retain shadow and highlight detail."
    )
    sections = [head, style_note]
    if guide:
        sections.append(guide_note)
    sections.append(controls)
    if context["notes"]:
        sections.append(" ".join(context["notes"]))
    return "\n\n".join(sections) + "\n"


def calibrated_guide(rgb, recipe, guide_target, config):
    return choose_guide(
        rgb, recipe, guide_target * config["target_gain"],
        max_strength=config["max_strength"], tone_limit=config["tone_limit"],
    )
