"""Read and write true 16-bit SDR PNG without Pillow's RGB8 decode path."""

import ctypes
from ctypes.util import find_library
from functools import lru_cache
import math
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageCms

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SRGB_CHROMATICITY = (0.3127, 0.3290, 0.6400, 0.3300, 0.3000, 0.6000, 0.1500, 0.0600)


def is_png16(path):
    """Inspect IHDR without decoding pixels or relying on the filename."""
    with open(path, "rb") as file:
        header = file.read(29)
    return (len(header) == 29 and header[:8] == PNG_SIGNATURE
            and header[8:16] == b"\0\0\0\rIHDR" and header[24] == 16)


def _pypng():
    try:
        import png
    except ImportError as exc:
        raise RuntimeError("16-bit PNG requires PyPNG. Install the skill requirements.") from exc
    return png


def _metadata(path):
    with Image.open(path) as image:
        if getattr(image, "is_animated", False):
            raise ValueError("Animated PNG is not supported by the 16-bit still-image reader")
        info = image.info.copy()
    # PNG eXIf can be placed after IDAT. Seek past compressed pixels to find it.
    cicp = None
    with open(path, "rb") as file:
        file.seek(8)
        while chunk := file.read(8):
            if len(chunk) != 8:
                raise ValueError("Truncated PNG chunk")
            length, kind = struct.unpack(">I4s", chunk)
            if kind == b"IEND":
                break
            if kind == b"cICP":
                if length != 4:
                    raise ValueError("Invalid PNG cICP chunk")
                cicp = file.read(4)
                file.seek(4, 1)
            elif kind == b"eXIf" and "exif" not in info:
                if length > 1_000_000:
                    raise ValueError("PNG EXIF metadata is too large")
                info["exif"] = file.read(length)
                file.seek(4, 1)
            else:
                file.seek(length + 4, 1)
    orientation = 1
    if "exif" in info:
        exif = Image.Exif()
        exif.load(info["exif"])
        orientation = exif.get(274, 1)
    return info, orientation, cicp


def _orient(pixels, orientation):
    if orientation in (5, 6, 7, 8):
        pixels = pixels.transpose(1, 0, 2)
    if orientation in (3, 4, 7, 8):
        pixels = pixels[::-1]
    if orientation in (2, 3, 6, 7):
        pixels = pixels[:, ::-1]
    return pixels


@lru_cache(maxsize=1)
def _little_cms():
    library = find_library("lcms2")
    if not library:
        raise RuntimeError("Install Little CMS 2 and make its shared library discoverable to convert 16-bit PNG ICC profiles.")
    lcms = ctypes.CDLL(library)
    pointer = ctypes.c_void_p
    unsigned = ctypes.c_uint32
    prototypes = {
        "cmsOpenProfileFromMem": (pointer, [pointer, unsigned]),
        "cmsCreate_sRGBProfile": (pointer, []),
        "cmsCreateTransform": (pointer, [pointer, unsigned, pointer, unsigned, unsigned, unsigned]),
        "cmsDoTransform": (None, [pointer, pointer, pointer, unsigned]),
        "cmsDeleteTransform": (None, [pointer]),
        "cmsCloseProfile": (ctypes.c_int, [pointer]),
    }
    for name, (result, arguments) in prototypes.items():
        fn = getattr(lcms, name)
        fn.restype = result
        fn.argtypes = arguments
    return lcms


@lru_cache(maxsize=1)
def _standard_icc():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _is_standard_icc(profile):
    # Creation time and computed profile ID can differ across identical ICC data.
    standard = _standard_icc()
    return profile[:24] == standard[:24] and profile[36:84] == standard[36:84] and profile[100:] == standard[100:]


def _icc_to_srgb(pixels, profile, greyscale):
    lcms = _little_cms()
    data = ctypes.create_string_buffer(profile)
    source = lcms.cmsOpenProfileFromMem(data, len(profile))
    if not source:
        raise ValueError("Invalid embedded PNG ICC profile")
    destination = lcms.cmsCreate_sRGBProfile()
    if not destination:
        lcms.cmsCloseProfile(source)
        raise RuntimeError("Could not create an sRGB ICC profile")
    transform = None
    try:
        # Little CMS format bitfields. Use 16-bit input and float32 output.
        input_format = ((3 if greyscale else 4) << 16) | ((1 if greyscale else 3) << 3) | 2
        output_format = (1 << 22) | (4 << 16) | (3 << 3) | 4
        transform = lcms.cmsCreateTransform(source, input_format, destination, output_format, 0, 0)
        if not transform:
            raise ValueError("Embedded ICC profile cannot convert these PNG channels to sRGB")
        source_pixels = np.ascontiguousarray(pixels, dtype=np.uint16)
        result = np.empty((*pixels.shape[:2], 3), dtype=np.float32)
        lcms.cmsDoTransform(transform, source_pixels.ctypes.data, result.ctypes.data, pixels.shape[0] * pixels.shape[1])
        if not np.isfinite(result).all():
            raise ValueError("Embedded ICC profile produced non-finite colors")
        return np.clip(result, 0, 1, out=result)
    finally:
        if transform:
            lcms.cmsDeleteTransform(transform)
        lcms.cmsCloseProfile(destination)
        lcms.cmsCloseProfile(source)


def _to_srgb(pixels, info, greyscale, cicp):
    if cicp is not None and cicp != b"\x01\x0d\x00\x01":
        raise ValueError("Only SDR sRGB cICP PNG is supported")
    profile = info.get("icc_profile")
    if profile and cicp is None:
        if not greyscale and _is_standard_icc(profile):
            return pixels.astype(np.float32) / 65535, "embedded_sRGB"
        return _icc_to_srgb(pixels, profile, greyscale), "converted_to_sRGB"
    rgb = pixels.astype(np.float32) / 65535
    if greyscale:
        rgb = np.repeat(rgb, 3, axis=2)
    if "srgb" in info or cicp is not None:
        return rgb, "embedded_sRGB"
    chromaticity = info.get("chromaticity")
    if chromaticity and not greyscale and not np.allclose(chromaticity, SRGB_CHROMATICITY, atol=.0001, rtol=0):
        raise ValueError("Non-sRGB PNG chromaticities need an embedded ICC profile")
    gamma = info.get("gamma")
    if gamma is None:
        return rgb, "no_profile_assumed_sRGB"
    if gamma <= 0:
        raise ValueError("Invalid PNG gamma")
    linear = rgb ** (1 / gamma)
    rgb = np.where(linear <= .0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - .055)
    return rgb, "converted_PNG_gamma_to_sRGB"


def read_png16(path, long_edge=0, max_pixels=0):
    """Return normalized float32 sRGB pixels and original/working dimensions."""
    if not is_png16(path):
        raise ValueError(f"Expected a 16-bit PNG: {path}")
    info, orientation, cicp = _metadata(path)
    png = _pypng()
    with open(path, "rb") as file:
        width, height, rows, metadata = png.Reader(file=file).read()
        planes = metadata["planes"]
        pixels = np.empty((height, width, planes), dtype=np.uint16)
        count = 0
        for row in rows:
            pixels[count] = np.asarray(row, dtype=np.uint16).reshape(width, planes)
            count += 1
        if count != height:
            raise ValueError("PNG row count does not match IHDR")
    pixels = _orient(pixels, orientation)
    greyscale = metadata["greyscale"]
    channels = pixels[..., :1 if greyscale else 3]
    rgb, icc = _to_srgb(channels, info, greyscale, cicp)
    has_alpha = metadata["alpha"] or "transparent" in metadata
    if metadata["alpha"]:
        alpha = pixels[..., -1:].astype(np.float32) / 65535
        rgb = 1 - (1 - rgb) * alpha
    elif "transparent" in metadata:
        transparent = np.all(channels == metadata["transparent"], axis=2)
        rgb[transparent] = 1
    source_size = [rgb.shape[1], rgb.shape[0]]
    scale = 1.0
    if long_edge:
        scale = min(scale, long_edge / max(source_size))
    if max_pixels:
        scale = min(scale, math.sqrt(max_pixels / math.prod(source_size)))
    working_size = source_size.copy()
    if scale < 1:
        working_size = [max(1, round(side * scale)) for side in source_size]
        rgb = np.stack([
            np.asarray(Image.fromarray(channel).resize(working_size, Image.Resampling.LANCZOS))
            for channel in rgb.transpose(2, 0, 1)
        ], axis=-1)
    np.clip(rgb, 0, 1, out=rgb)
    preparation = {
        "source_size": source_size,
        "working_size": working_size,
        "icc": icc,
        "alpha": "composited_on_white" if has_alpha else "none",
    }
    return rgb, preparation


def save_png16(path, normalized_rgb):
    """Save normalized sRGB channels as genuine 16-bit RGB PNG samples."""
    rgb = np.asarray(normalized_rgb)
    if rgb.ndim != 3 or rgb.shape[-1] != 3 or not np.isfinite(rgb).all():
        raise ValueError("16-bit PNG export expects a finite H×W×3 RGB image")
    path = Path(path)
    height, width, _ = rgb.shape
    if not height or not width:
        raise ValueError("PNG dimensions must be positive")
    png = _pypng()

    class SrgbWriter(png.Writer):
        def write_preamble(self, file):
            super().write_preamble(file)
            png.write_chunk(file, b"sRGB", b"\0")

    def rows():
        dtype = np.result_type(rgb.dtype, np.float32)
        for row in rgb:
            normalized = np.clip(row.astype(dtype, copy=False), 0, 1)
            yield np.rint(normalized * 65535).astype(np.uint16).ravel()

    with TemporaryDirectory(dir=path.parent) as temporary:
        pending = Path(temporary) / path.name
        with pending.open("wb") as file:
            SrgbWriter(width=width, height=height, bitdepth=16, greyscale=False).write(file, rows())
        pending.replace(path)
