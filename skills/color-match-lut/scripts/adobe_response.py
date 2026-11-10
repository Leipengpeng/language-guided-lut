"""RAWmakase-calibrated XMP response for already rendered SDR sRGB images.

The point curve, Color Mixer and B&W Mixer are based on RAWmakase v0.4.12
(704ab207b618bb9f2626d50741ecba4a7761d444), Copyright (c) 2026
RAWmakase contributors, under the MIT license in assets/rawmakase/LICENSE.
This subset assumes neutral Basic controls, default Curve Refine Saturation
and no camera/creative profile. It is not an Adobe renderer.
"""

from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline


ASSETS = Path(__file__).resolve().parents[1] / "assets" / "rawmakase"
HUES, SATURATIONS, VALUES = 36, 6, 6
CELLS = HUES * SATURATIONS * VALUES
CURVE_LUT_X = np.linspace(0, 1, 4097)
RGB_TO_PROPHOTO = np.array([
    [0.529345, 0.330072, 0.140583],
    [0.098374, 0.873462, 0.028164],
    [0.016883, 0.117673, 0.865444],
])
PROPHOTO_TO_RGB = np.array([
    [2.034075, -0.727334, -0.306742],
    [-0.228813, 1.231731, -0.002918],
    [-0.008570, -0.153286, 1.161856],
])
LUMINANCE = np.array([0.2126, 0.7152, 0.0722])
RGB_TO_LMS = np.array([
    [0.41222146, 0.53633255, 0.051445995],
    [0.21190350, 0.68069950, 0.107396960],
    [0.08830246, 0.28171885, 0.629978700],
])
LMS_TO_RGB = np.array([
    [4.0767417, -3.3077116, 0.23096994],
    [-1.268438, 2.6097574, -0.34131940],
    [-0.0041960863, -0.7034186, 1.70761470],
])
LMS_ROOT_TO_LIGHTNESS = np.array([0.21045426, 0.7936178, -0.004072047])


@lru_cache(maxsize=2)
def _load_table(filename):
    return np.fromfile(ASSETS / filename, dtype="<i2")


def _srgb_decode(encoded):
    return np.where(encoded <= .04045, encoded / 12.92, ((encoded + .055) / 1.055) ** 2.4)


def _srgb_encode(linear):
    return np.where(linear <= .0031308, 12.92 * linear, 1.055 * np.maximum(linear, 0) ** (1 / 2.4) - .055)


def _curve(values, points):
    x, y = (np.asarray(points, dtype=float) / 255).T
    if x[0] == 0 and x[-1] == 1 and np.array_equal(x, y):
        return values
    spline = CubicSpline(x, y, bc_type="natural")
    samples = np.clip(spline(np.clip(CURVE_LUT_X, x[0], x[-1])), 0, 1)
    return np.interp(values, CURVE_LUT_X, samples)


def _point_curves(rgb, curves):
    encoded = _srgb_encode(np.clip(rgb @ RGB_TO_PROPHOTO.T, 0, 1))
    if "master" in curves:
        low = encoded.min(axis=1)
        high = encoded.max(axis=1)
        ends = _curve(np.column_stack((low, high)), curves["master"])
        span = (high - low)[:, None]
        fraction = np.divide(encoded - low[:, None], span,
                             out=np.zeros_like(encoded), where=span > 1e-8)
        encoded = ends[:, :1] + (ends[:, 1:] - ends[:, :1]) * fraction
    for index, channel in enumerate(("red", "green", "blue")):
        if channel in curves:
            encoded[:, index] = _curve(encoded[:, index], curves[channel])
    return _srgb_decode(encoded)


def _hsv(rgb):
    high, low = rgb.max(axis=1), rgb.min(axis=1)
    chroma = high - low
    largest = rgb.argmax(axis=1)
    red, green, blue = rgb.T
    numerator = np.choose(largest, [green - blue, blue - red, red - green])
    hue = np.divide(numerator, chroma, out=np.zeros_like(chroma), where=chroma >= 1e-9)
    hue = ((hue + largest * 2) / 6) % 1
    sat = np.divide(chroma, high, out=np.zeros_like(chroma), where=high > 1e-6)
    return hue, sat, high


def _from_hsv(hue, saturation, value):
    offsets = np.array([5, 3, 1])
    k = (hue[:, None] * 6 + offsets) % 6
    return value[:, None] * (1 - saturation[:, None] * np.maximum(0, np.minimum(np.minimum(k, 4 - k), 1)))


def _interpolate(grid, hue, sat, value):
    """Trilinear interpolation between cell centers, with circular hue."""
    coords = (
        hue * HUES - .5,
        np.clip(np.sqrt(np.clip(sat, 0, 1)) * SATURATIONS - .5, 0, SATURATIONS - 1),
        np.clip(np.clip(value, 0, 1) ** .45 * VALUES - .5, 0, VALUES - 1),
    )
    lower = [np.floor(c).astype(np.intp) for c in coords]
    fraction = [c - low for c, low in zip(coords, lower)]
    result = np.zeros((len(hue), grid.shape[0]))
    for hue_step in (0, 1):
        h = (lower[0] + hue_step) % HUES
        hue_weight = fraction[0] if hue_step else 1 - fraction[0]
        for sat_step in (0, 1):
            s = np.minimum(lower[1] + sat_step, SATURATIONS - 1)
            sat_weight = fraction[1] if sat_step else 1 - fraction[1]
            for value_step in (0, 1):
                v = np.minimum(lower[2] + value_step, VALUES - 1)
                value_weight = fraction[2] if value_step else 1 - fraction[2]
                weight = hue_weight * sat_weight * value_weight
                result += grid[:, (h * SATURATIONS + s) * VALUES + v].T * weight[:, None]
    return result


@lru_cache(maxsize=1)
def _color_tables():
    return _load_table("color_mixer_chart.bin").reshape(52, 3, CELLS) / 8000


def _color_grid(mixer):
    sliders = np.clip(np.asarray(mixer, dtype=float), -100, 100) / 100
    active, = np.nonzero(sliders.ravel())
    if not len(active):
        return None
    strengths = np.abs(sliders.ravel()[active])
    positive = sliders.ravel()[active] > 0
    luminance = active % 3 == 2
    strengths[luminance] **= np.where(positive[luminance], .79, 1.6)
    tables = _color_tables()[active * 2 + positive]
    shifts = tables * strengths[:, None, None]
    # Negative extremes blend the saturation multiplier, not its logarithm.
    negative = ~positive
    shifts[negative, 1] = np.log2(np.maximum(
        1 + strengths[negative, None] * np.expm1(tables[negative, 1] * np.log(2)), .001))
    return shifts.sum(axis=0)


@lru_cache(maxsize=1)
def _gray_tables():
    return _load_table("black_white_chart.bin").reshape(33, CELLS) / 4000


def _gray_grid(mixer):
    sliders = np.zeros(8) if mixer is None else np.clip(np.asarray(mixer, dtype=float), -100, 100) / 100
    magnitude = np.abs(sliders)
    near = np.where(sliders < 0, 1, 2) + 1 + 4 * np.arange(8)
    far = np.where(sliders < 0, 0, 3) + 1 + 4 * np.arange(8)
    near_weight = np.where(magnitude <= .5, 2 * magnitude, 2 * (1 - magnitude))
    far_weight = np.maximum(2 * magnitude - 1, 0)
    tables = _gray_tables()
    return (tables[0] + (tables[near] * near_weight[:, None]).sum(axis=0)
            + (tables[far] * far_weight[:, None]).sum(axis=0))[None, :]


def _clip_lightness(rgb):
    # RAWmakase clamps Oklab lightness while preserving chroma before output gamut clipping.
    out_of_range = ((rgb < 0) | (rgb > 1)).any(axis=1)
    if np.any(out_of_range):
        roots = np.cbrt(rgb[out_of_range] @ RGB_TO_LMS.T)
        lightness = roots @ LMS_ROOT_TO_LIGHTNESS
        clipped = (lightness < 0) | (lightness > 1)
        if np.any(clipped):
            shift = np.clip(lightness[clipped], 0, 1) - lightness[clipped]
            rgb[out_of_range.nonzero()[0][clipped]] = ((roots[clipped] + shift[:, None]) ** 3) @ LMS_TO_RGB.T
    return rgb


def render(rgb, curves, mixer, gray_mixer, monochrome):
    """Apply XMP settings to an N×3 array of encoded sRGB pixels."""
    prophoto = _point_curves(_srgb_decode(np.asarray(rgb, dtype=float)), curves)
    grid = None if mixer is None or monochrome else _color_grid(mixer)
    if grid is not None:
        hue, sat, value = _hsv(prophoto)
        dh, ds, dv = _interpolate(grid, hue, sat, value).T
        mixed = _from_hsv((hue + dh) % 1, np.clip(sat * np.exp2(ds), 0, 1), value * np.exp2(dv))
        prophoto = np.where((value > 1e-6)[:, None], mixed, prophoto)

    linear = _clip_lightness(prophoto @ PROPHOTO_TO_RGB.T)
    if monochrome:
        positive = np.maximum(linear, 0)
        prophoto = positive @ RGB_TO_PROPHOTO.T
        hue, sat, value = _hsv(prophoto)
        exponent = _interpolate(_gray_grid(gray_mixer), hue, sat, value)[:, 0]
        gray = (positive @ LUMINANCE) * np.where(value > 1e-6, np.exp2(exponent), 1)
        linear = np.repeat(gray[:, None], 3, axis=1)
    return _srgb_encode(np.clip(linear, 0, 1))
