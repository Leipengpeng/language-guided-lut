"""Detect nearly neutral references in decoded 8-bit sRGB images."""

import numpy as np
from PIL import Image


def classify_reference(image: Image.Image) -> dict:
    """Classify the visible image after orientation, ICC, and alpha handling."""
    rgb = np.asarray(image if image.mode == "RGB" else image.convert("RGB"))
    spread = rgb.max(axis=-1) - rgb.min(axis=-1)
    mean_spread = float(spread.mean())
    mild_fraction = float(np.count_nonzero(spread > 4) / spread.size)
    strong_fraction = float(np.count_nonzero(spread > 12) / spread.size)
    # Allow slight channel noise and isolated outliers. A small, distinctly
    # colored area or a low-level tint throughout the image takes the color path.
    is_monochrome = mean_spread <= 2.0 and mild_fraction <= 0.01 and strong_fraction <= 0.0001
    return {
        "is_monochrome": is_monochrome,
        "mean_channel_spread_8bit": mean_spread,
        "fraction_spread_over_4": mild_fraction,
        "fraction_spread_over_12": strong_fraction,
    }
