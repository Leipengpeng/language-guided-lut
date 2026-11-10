"""Run Photo Room's SDR sRGB point curves and mixers on a flat RGB buffer.

Uses the MIT-licensed Python operators from Hiemenz/photo-room at
f78507c1011648337af6ec97c66e90bb4e9331f1. The only vendored source change
is a package-relative import. This adapter translates the preset fit's
curve and slider names and follows Photo Room's stage order for these
controls. Photo Room uses its own Lightroom-style response.
"""

import numpy as np

from photo_room_vendor import colormix, tonecurve


def render(rgb, curves, mixer, gray_mixer, monochrome):
    """Apply point curves, then the selected Photo Room mixer."""
    pixels = np.asarray(rgb, dtype=np.float32).reshape(1, -1, 3)
    points = {"rgb" if name == "master" else name: list(curve) for name, curve in curves.items()}
    pixels = tonecurve.apply_tone_curve(pixels, {"points": points})
    if monochrome:
        if gray_mixer is not None:
            settings = {f"gray_{band}": value for band, value in zip(colormix.BANDS, gray_mixer)}
            pixels = colormix.apply_gray_mixer(pixels, settings)
        # Same luminance conversion as Photo Room's editor final stage.
        red, green, blue = pixels[0].T
        gray = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        return np.repeat(gray[:, None], 3, axis=1)
    if mixer is not None:
        settings = {
            f"{axis}_{band}": value
            for band, sliders in zip(colormix.BANDS, mixer)
            for axis, value in zip(("hue", "sat", "lum"), sliders)
        }
        pixels = colormix.apply_hsl(pixels, settings)
    return pixels.reshape(-1, 3)
