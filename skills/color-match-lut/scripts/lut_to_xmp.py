"""Fit editable Camera Raw develop presets to a full-range SDR sRGB 3D LUT."""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.optimize import least_squares

from color_match_lut import apply_cube, read_cube, read_rgb, srgb_profile, to_oklab
from xmp_preset import BANDS, read_preset, write_preset


HUE_CENTERS = np.array([0, 30, 60, 120, 180, 240, 280, 320, 360]) / 360
CHANNELS = ("red", "green", "blue")
KNOTS = np.rint(np.linspace(0, 255, 9)).astype(int)
GRID = KNOTS / 255
LUMA = np.array([0.2126, 0.7152, 0.0722])


def rgb_to_hsl(rgb):
    red, green, blue = rgb.T
    hi, lo = rgb.max(axis=1), rgb.min(axis=1)
    chroma = hi - lo
    light = (hi + lo) / 2
    hue_terms = np.choose(rgb.argmax(axis=1), [green - blue, blue - red, red - green])
    hue = np.divide(hue_terms, chroma, out=np.zeros_like(chroma), where=chroma > 1e-10)
    hue = ((hue + 2 * rgb.argmax(axis=1)) / 6) % 1
    denom = 1 - np.abs(2 * light - 1)
    sat = np.divide(chroma, denom, out=np.zeros_like(chroma), where=denom > 1e-10)
    return hue, sat, light


def hsl_to_rgb(hue, sat, light):
    k = (hue[:, None] * 12 + np.array([0, 8, 4])) % 12
    amplitude = sat * np.minimum(light, 1 - light)
    return light[:, None] - amplitude[:, None] * np.maximum(-1, np.minimum(np.minimum(k - 3, 9 - k), 1))


def hue_mix(hue, saturation, band_values):
    hue = np.where(hue >= 1, 0, hue)
    band = np.searchsorted(HUE_CENTERS, hue, side="right") - 1
    fraction = (hue - HUE_CENTERS[band]) / (HUE_CENTERS[band + 1] - HUE_CENTERS[band])
    # Neutral colors should not inherit a large shift from an unstable hue angle.
    attenuation = np.clip(saturation / .20, 0, 1) * (saturation > 1e-8)
    left = band_values[band]
    right = band_values[(band + 1) % len(BANDS)]
    return (left * (1 - fraction[:, None]) + right * fraction[:, None]) * attenuation[:, None]


def apply_curve(values, points):
    knots = np.asarray(points) / 255
    return np.interp(values, knots[:, 0], knots[:, 1])


def render(rgb, curves, mixer, gray_mixer, monochrome):
    """Local preview model for documented Adobe controls, with approximate response."""
    if monochrome:
        light = rgb @ LUMA
        if gray_mixer is not None:
            hue, sat, _ = rgb_to_hsl(rgb)
            mix = hue_mix(hue, sat, np.asarray(gray_mixer).reshape(-1, 1) / 100)[:, 0]
            light = np.clip(light + .22 * mix * 4 * light * (1 - light), 0, 1)
        gray = apply_curve(light, curves["master"])
        return np.repeat(gray[:, None], 3, axis=1)

    result = rgb
    if "master" in curves:
        result = apply_curve(result, curves["master"])
    if any(channel in curves for channel in CHANNELS):
        result = np.column_stack([
            apply_curve(result[:, i], curves[channel]) if channel in curves else result[:, i]
            for i, channel in enumerate(CHANNELS)
        ])
    if mixer is None:
        return result
    hue, sat, light = rgb_to_hsl(result)
    shift = hue_mix(hue, sat, np.asarray(mixer) / 100)
    hue = (hue + .10 * shift[:, 0]) % 1
    sat = np.clip(sat * (1 + shift[:, 1]), 0, 1)
    light = np.clip(light + .22 * shift[:, 2] * 4 * light * (1 - light), 0, 1)
    return hsl_to_rgb(hue, sat, light)


def fit(rgb, target_lab, weights, names, mixer_width, monochrome, renderer=render):
    curve_count = len(names)
    parameter_count = curve_count * len(GRID)
    initial = np.r_[np.tile(GRID, curve_count), np.zeros(len(BANDS) * mixer_width)]
    lower = np.r_[np.zeros(parameter_count), np.full(len(initial) - parameter_count, -1.)]
    upper = np.ones(len(initial))
    weights = np.sqrt(weights / weights.sum())[:, None]

    def parts(values):
        ordinates = values[:parameter_count].reshape(curve_count, len(GRID))
        curves = {name: np.column_stack((KNOTS, row * 255)) for name, row in zip(names, ordinates)}
        sliders = values[parameter_count:].reshape(len(BANDS), mixer_width) * 100
        return ordinates, curves, sliders

    def residual(values):
        ordinates, curves, sliders = parts(values)
        color = sliders if mixer_width == 3 else None
        gray = sliders[:, 0] if mixer_width == 1 else None
        predicted = renderer(rgb, curves, color, gray, monochrome)
        steps = np.diff(ordinates, axis=1)
        return np.concatenate((
            ((to_oklab(predicted) - target_lab) * weights).ravel(),
            (.10 * np.minimum(steps, 0)).ravel(),
            (.025 * np.diff(steps, axis=1)).ravel(),
            .003 * (values - initial),
        ))

    def jacobian(values):
        # Some renderers evaluate in float32 or via lookup tables, so use a
        # fixed perturbation large enough to change the rendered color.
        base = residual(values)
        derivatives = np.empty((base.size, values.size))
        for index, value in enumerate(values):
            step = 1 / 512 if value <= upper[index] - 1 / 512 else -1 / 512
            sample = values.copy()
            sample[index] += step
            derivatives[:, index] = (residual(sample) - base) / step
        return derivatives

    derivative = "2-point" if renderer is render else jacobian
    fit_result = least_squares(residual, initial, jac=derivative,
                               bounds=(lower, upper), max_nfev=45, ftol=1e-6)
    _, curves, sliders = parts(fit_result.x)
    for points in curves.values():
        points[:, 1] = np.maximum.accumulate(np.rint(points[:, 1]))
    curves = {key: points.astype(int).tolist() for key, points in curves.items()}
    mixer = None
    gray_mixer = None
    if mixer_width == 3:
        mixer = dict(zip(BANDS, np.rint(sliders).astype(int).tolist()))
    if mixer_width == 1:
        gray_mixer = dict(zip(BANDS, np.rint(sliders[:, 0]).astype(int).tolist()))
    elif monochrome:
        # An explicit neutral mix lets this standalone preset reset previous B&W adjustments.
        gray_mixer = dict.fromkeys(BANDS, 0)
    return curves, mixer, gray_mixer


def measure(result, target):
    errors = 100 * np.linalg.norm(to_oklab(result) - to_oklab(target), axis=1)
    return [round(float(value), 3) for value in np.percentile(errors, [50, 95])]


def save_preview(path, rgb, size, bit_depth=8):
    pixels = np.clip(rgb, 0, 1).reshape(size[1], size[0], 3)
    if bit_depth == 16:
        from png16 import save_png16

        save_png16(path, pixels)
        return
    pixels = np.rint(pixels * 255).astype(np.uint8)
    Image.fromarray(pixels).save(path, icc_profile=srgb_profile(), compress_level=3)


def export(content, lut, output, name, preview_edge, *, renderer=render, renderer_name="local approximation"):
    image, preparation = read_rgb(content, long_edge=preview_edge)
    size = tuple(preparation["working_size"])
    bit_depth = preparation["bit_depth"]
    rgb = image.reshape(-1, 3).astype(np.float64)
    table = read_cube(lut)
    monochrome = bool(np.max(np.ptp(table, axis=-1)) <= 1 / 255)
    output.mkdir(parents=True, exist_ok=True)
    target = apply_cube(rgb, table)
    save_preview(output / "original.png", rgb, size, bit_depth)
    save_preview(output / "lut.png", target, size, bit_depth)

    rng = np.random.default_rng(20261108)
    indices = rng.choice(len(rgb), size=min(12000, len(rgb)), replace=False)
    split = min(2000, max(1, len(indices) // 2))
    training = rgb[indices[:split]]
    held_out = indices[split:]
    # Balance the source distribution with cube probes and neutral colors.
    probes = (rng.permutation(384)[:, None] + rng.random((384, 3))) / 384
    for index in range(3):
        probes[:, index] = rng.permutation(probes[:, index])
    neutrals = np.repeat(np.linspace(0, 1, 48)[:, None], 3, axis=1)
    train_rgb = np.concatenate((training, probes, neutrals))
    weights = np.repeat([.80 / len(training), .16 / len(probes), .04 / len(neutrals)],
                        [len(training), len(probes), len(neutrals)])
    train_lab = to_oklab(apply_cube(train_rgb, table))
    test_probes = rng.random((4096, 3))
    test_probe_target = apply_cube(test_probes, table)

    models = (
        {"curves": (("master",), 0), "mixer": (("master",), 1)}
        if monochrome else
        {"curves": (CHANNELS, 0), "mixer": (("master",), 3), "hybrid": (("master", *CHANNELS), 3)}
    )
    methods = {}
    for method, (curve_names, width) in models.items():
        curves, mixer, gray_mixer = fit(train_rgb, train_lab, weights, curve_names, width, monochrome, renderer)
        path = output / (method + ".xmp")
        # Make curve/mixer families independent of previously edited values in those panels.
        complete_curves = {key: curves.get(key, [[0, 0], [255, 255]]) for key in ("master", *CHANNELS)}
        complete_mixer = None if monochrome else mixer or {band: [0, 0, 0] for band in BANDS}
        write_preset(path, f"{name} - {method}", curves=complete_curves,
                     mixer=complete_mixer, gray_mixer=gray_mixer)
        saved = read_preset(path)
        saved_mixer = [saved["mixer"][band] for band in BANDS] if saved["mixer"] else None
        saved_gray = [saved["gray_mixer"][band] for band in BANDS] if saved["gray_mixer"] else None

        def preview(values):
            return renderer(values, saved["curves"], saved_mixer, saved_gray, monochrome)

        result = preview(rgb)
        save_preview(output / (method + ".png"), result, size, bit_depth)
        median, p95 = measure(result[held_out], target[held_out]) if len(held_out) else (None, None)
        probe_median, probe_p95 = measure(preview(test_probes), test_probe_target)
        methods[method] = {"median": median, "p95": p95, "median_rgb_probes": probe_median,
                           "p95_rgb_probes": probe_p95}

    report = {
        "source_size": preparation["source_size"],
        "preview_size": list(size),
        "input_bit_depth": bit_depth,
        "monochrome": monochrome,
        "renderer": renderer_name,
        "metric": "100 × Euclidean Oklab distance to source LUT",
        "held_out_photo_pixels": len(held_out),
        "methods": methods,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", type=Path, required=True)
    parser.add_argument("--lut", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name")
    parser.add_argument("--preview-edge", type=int, default=1200)
    parser.add_argument("--engine", choices=("baseline", "rawmakase", "photo-room"), default="baseline")
    args = parser.parse_args()
    if args.preview_edge < 1:
        parser.error("--preview-edge must be positive")
    if args.content.resolve() in {(args.output / f"{name}.png").resolve()
                                  for name in ("original", "lut", "curves", "mixer", "hybrid") }:
        parser.error("Output would overwrite the content image")
    renderer = render
    renderer_name = "local approximation"
    if args.engine == "rawmakase":
        from adobe_response import render as renderer
        renderer_name = "RAWmakase measured response port"
    elif args.engine == "photo-room":
        from photo_room_response import render as renderer
        renderer_name = "Photo Room response adapter"
    report = export(args.content, args.lut, args.output, args.name or args.lut.stem, args.preview_edge,
                    renderer=renderer, renderer_name=renderer_name)
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
