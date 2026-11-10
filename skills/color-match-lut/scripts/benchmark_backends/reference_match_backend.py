"""Reference Match adapter with a directly usable sRGB-in/sRGB-out 3D LUT.

Fit the upstream global engine on the actual source and reference photographs.
Its native LUT works in Rec.709 gamma 2.4.  Sample that frozen transform, with
the two transfer-function conversions, on an sRGB grid for portable export.
No fitting statistics are computed from the identity grid.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import numpy as np
import torch
from . import backend_root


_VENDOR_CORE = backend_root() / "vendor" / "reference-match" / "core"
if not (_VENDOR_CORE / "lutcore" / "global_match.py").is_file():
    raise ImportError(f"Reference Match checkout is missing: {_VENDOR_CORE}")
if str(_VENDOR_CORE) not in sys.path:
    sys.path.insert(0, str(_VENDOR_CORE))

from lutcore.colour_management import (  # noqa: E402
    rec709_gamma24_to_srgb,
    reference_srgb_to_rec709_gamma24,
)
from lutcore.global_match import fit_global_residual_lut  # noqa: E402
from lutcore.look_controls import LookControls, apply_look_controls  # noqa: E402
from lutcore.trilinear import apply_residual_tensor  # noqa: E402


def _validate_rgb(array: np.ndarray, name: str) -> np.ndarray:
    rgb = np.asarray(array, dtype=np.float32)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1:
        raise ValueError(f"{name} must be a nonempty H x W x 3 RGB array.")
    if not np.isfinite(rgb).all() or rgb.min() < 0.0 or rgb.max() > 1.0:
        raise ValueError(f"{name} must contain finite sRGB values in [0, 1].")
    return np.ascontiguousarray(rgb)


def _as_tensor(rgb: np.ndarray) -> torch.Tensor:
    return torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).contiguous()


def _as_rgb(tensor: torch.Tensor) -> np.ndarray:
    return tensor.squeeze(0).permute(1, 2, 0).contiguous().cpu().numpy()


def _transform_srgb(image: torch.Tensor, residual: torch.Tensor) -> torch.Tensor:
    match_domain = reference_srgb_to_rec709_gamma24(image)
    matched = apply_residual_tensor(match_domain, residual)
    return rec709_gamma24_to_srgb(matched).clamp(0.0, 1.0)


def fit_lut(
    content_rgb: np.ndarray,
    reference_rgb: np.ndarray,
    size: int = 33,
    *,
    match_strength: float = 100.0,
    protect_skin: bool = False,
    protect_saturation: bool = False,
    protect_contrast: bool = False,
) -> dict[str, Any]:
    """Return a final sRGB LUT in ``[R, G, B, output_channel]`` order.

    Inputs must already be ICC-normalized sRGB floats in [0, 1].  Different
    image dimensions/content are supported.  ``native_output`` is the actual
    source image evaluated with the native Rec.709 LUT and converted to sRGB;
    it is retained to measure the additional sRGB baking/interpolation error.

    Defaults use full transfer without upstream's optional protective controls.
    To reproduce its CLI defaults, pass ``match_strength=90`` and set all three
    protection flags to ``True``.  Core algorithm stability limits still apply.
    """
    content_rgb = _validate_rgb(content_rgb, "content_rgb")
    reference_rgb = _validate_rgb(reference_rgb, "reference_rgb")
    if isinstance(size, bool) or not isinstance(size, (int, np.integer)) or not 2 <= size <= 129:
        raise ValueError("size must be an integer between 2 and 129.")
    size = int(size)
    # Preserve the native transform at >=65 points even when the exported LUT
    # is smaller.  This isolates the error caused by baking the new input domain.
    native_size = max(65, size)
    controls = LookControls(
        match_strength=match_strength,
        protect_skin=protect_skin,
        protect_saturation=protect_saturation,
        protect_contrast=protect_contrast,
    ).validated()

    with torch.inference_mode():
        content = _as_tensor(content_rgb)
        reference = _as_tensor(reference_rgb)
        content_match = reference_srgb_to_rec709_gamma24(content)
        reference_match = reference_srgb_to_rec709_gamma24(reference)
        residual = fit_global_residual_lut(content_match, reference_match, dimension=native_size)
        residual = apply_look_controls(residual, controls)
        del content_match, reference_match, reference

        # Chunk image evaluation to bound temporary sampling buffers for large
        # source photos.  All chunks share the already fitted transform.
        native_output = np.empty_like(content_rgb)
        for row in range(0, content_rgb.shape[0], 256):
            native_output[row : row + 256] = _as_rgb(
                _transform_srgb(content[:, :, row : row + 256], residual)
            )

        axis = torch.linspace(0.0, 1.0, size, dtype=torch.float32)
        identity = torch.stack(torch.meshgrid(axis, axis, axis, indexing="ij"), dim=-1)
        # The identity grid is queried only after the fitting is complete.
        grid_image = identity.reshape(size * size, size, 3).permute(2, 0, 1).unsqueeze(0).contiguous()
        values = _as_rgb(_transform_srgb(grid_image, residual)).reshape(size, size, size, 3)
        values = np.ascontiguousarray(values, dtype=np.float32)

    return {
        "values": values,
        "native_output": np.ascontiguousarray(native_output),
        "metadata": {
            "backend": "reference-match-global",
            "upstream": "https://github.com/xrz1411/reference-match",
            "upstream_commit": "88ddf8fa7c6e6d424ec2b55e32c1310276a52500",
            "algorithm": "Oklab monotonic lightness and tonal-zone chroma histogram matching",
            "input_space": "sRGB encoded RGB, D65, full range [0,1]",
            "output_space": "sRGB encoded RGB, D65, full range [0,1]",
            "native_space": "Rec.709 primaries, gamma 2.4",
            "native_lut_size": native_size,
            "export_lut_size": size,
            "lut_layout": "R,G,B,RGB",
            "native_interpolation": "trilinear",
            "match_strength": float(match_strength),
            "protect_skin": bool(protect_skin),
            "protect_saturation": bool(protect_saturation),
            "protect_contrast": bool(protect_contrast),
            "shadows": 100.0,
            "midtones": 100.0,
            "highlights": 100.0,
            "device": "cpu",
            "output_clipped": True,
            "icc_normalization": "required before calling this backend",
            "native_output_meaning": "Frozen native LUT applied to source, then converted to sRGB; before exported sRGB LUT interpolation",
        },
    }
