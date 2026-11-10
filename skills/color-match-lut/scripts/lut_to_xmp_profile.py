#!/usr/bin/env python3
"""Embed a full-range sRGB .cube in an Adobe Creative Profile (.xmp).

Adobe Big Table serialization is documented by the DNG SDK and independently
tested for 2–64 divisions by RAWmakase against Camera Raw 18.7.
Input tables above the selected grid limit are resampled with the LUT renderer.
The default 32-point limit matches older Adobe Creative Profiles.
https://android.googlesource.com/platform/external/dng_sdk/+/de700ad461e35af50b28b861943a0b0753b10929/source/dng_big_table.cpp
https://github.com/pch/rawmakase/blob/main/docs/lightroom-profiles.md#cube-luts
"""
import argparse
import hashlib
import struct
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import numpy as np

from color_match_lut import apply_cube, grid, read_cube


XMP = "adobe:ns:meta/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CRS = "http://ns.adobe.com/camera-raw-settings/1.0/"
DEFAULT_PROFILE_GRID = 32
MAX_PROFILE_GRID = 64
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
ADOBE_BASE85 = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-:+=^!/*?`'|()[]{}@%$#"


def encode_big_table(raw):
    packed = struct.pack("<I", len(raw)) + zlib.compress(raw)
    encoded = bytearray()
    for start in range(0, len(packed), 4):
        chunk = packed[start:start + 4]
        value = int.from_bytes(chunk, "little")
        for _ in range(len(chunk) + 1):
            value, digit = divmod(value, 85)
            encoded.append(ADOBE_BASE85[digit])
    return encoded.decode("ascii")


def profile_xml(lut, name, *, max_grid=DEFAULT_PROFILE_GRID):
    if not 2 <= max_grid <= MAX_PROFILE_GRID:
        raise ValueError("Profile grid limit must be between 2 and 64")
    size = lut.shape[0]
    if size > max_grid:
        # Sample the original .cube transform on the largest supported grid.
        size = max_grid
        lut = apply_cube(grid(size), lut)
    # read_cube returns [R, G, B, channel]. Adobe serializes blue fastest.
    axis = (np.arange(size, dtype=np.int32) * 65535 + (size >> 1)) // (size - 1)
    neutral = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
    samples = np.rint(lut.astype(np.float64) * 65535).astype(np.int32)
    deltas = (samples - neutral).astype("<u2").tobytes()
    header = struct.pack("<4I", 1, 1, 3, size)
    # sRGB primaries, sRGB transfer, clip out of gamut, Amount 0–200%.
    footer = struct.pack("<3I2d", 0, 1, 0, 0.0, 2.0)
    raw = header + deltas + footer
    digest = hashlib.md5(raw, usedforsecurity=False).hexdigest().upper()
    fields = {
        "PresetType": "Look",
        "Cluster": "",
        "UUID": uuid.uuid5(uuid.NAMESPACE_URL, f"language-guided-lut:{digest}:{name}").hex.upper(),
        "SupportsAmount": "True",
        "SupportsColor": "True",
        "SupportsMonochrome": "False",
        "SupportsHighDynamicRange": "False",
        "SupportsNormalDynamicRange": "True",
        "SupportsSceneReferred": "True",
        "SupportsOutputReferred": "True",
        "CameraModelRestriction": "",
        "Version": "10.3",
        "ProcessVersion": "10.0",
        "ConvertToGrayscale": "False",
        "HasSettings": "True",
        "RGBTable": digest,
        f"Table_{digest}": encode_big_table(raw),
    }
    for prefix, namespace in (("x", XMP), ("rdf", RDF), ("crs", CRS)):
        ET.register_namespace(prefix, namespace)
    root = ET.Element(f"{{{XMP}}}xmpmeta")
    rdf = ET.SubElement(root, f"{{{RDF}}}RDF")
    description = ET.SubElement(rdf, f"{{{RDF}}}Description", {f"{{{RDF}}}about": ""})
    description.attrib.update({f"{{{CRS}}}{key}": value for key, value in fields.items()})
    for key, label in (("Name", name), ("Group", "Language-Guided LUT")):
        field = ET.SubElement(description, f"{{{CRS}}}{key}")
        choices = ET.SubElement(field, f"{{{RDF}}}Alt")
        ET.SubElement(choices, f"{{{RDF}}}li", {XML_LANG: "x-default"}).text = label
    ET.indent(root, space=" ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def write_profile(lut_path, output, name=None, *, max_grid=DEFAULT_PROFILE_GRID):
    output = Path(output)
    if output.suffix.lower() != ".xmp":
        raise ValueError("Output filename must end in .xmp")
    name = name.strip() if name is not None else Path(lut_path).stem
    if not name:
        raise ValueError("Profile name cannot be empty")
    data = profile_xml(read_cube(lut_path), name, max_grid=max_grid)
    # Check metadata text is valid XML before replacing any existing file.
    ET.fromstring(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        pending = Path(temporary) / output.name
        pending.write_bytes(data)
        pending.replace(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lut", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--name")
    parser.add_argument("--max-grid", type=int, default=DEFAULT_PROFILE_GRID,
                        help="maximum grid size per axis, 2–64 (default 32)")
    args = parser.parse_args()
    try:
        result = write_profile(args.lut, args.output, args.name, max_grid=args.max_grid)
    except (OSError, ValueError, ET.ParseError) as error:
        parser.error(str(error))
    print(result.resolve())


if __name__ == "__main__":
    main()
