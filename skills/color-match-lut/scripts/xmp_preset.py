"""Read and write editable Adobe Camera Raw and Lightroom develop presets."""

from collections.abc import Mapping, Sequence
from operator import index
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5
from xml.etree import ElementTree as ET


XMP = "adobe:ns:meta/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CRS = "http://ns.adobe.com/camera-raw-settings/1.0/"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

CURVE_FIELDS = {
    "master": "ToneCurvePV2012",
    "red": "ToneCurvePV2012Red",
    "green": "ToneCurvePV2012Green",
    "blue": "ToneCurvePV2012Blue",
}
BANDS = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")
MIXER_AXES = ("HueAdjustment", "SaturationAdjustment", "LuminanceAdjustment")


def _integer(value: int, low: int, high: int, label: str) -> int:
    try:
        result = index(value)
    except TypeError as error:
        raise ValueError(f"{label} must be an integer") from error
    if isinstance(value, bool) or not low <= result <= high:
        raise ValueError(f"{label} must be between {low} and {high}")
    return result


def _curve_points(points: Sequence[Sequence[int]], label: str) -> list[list[int]]:
    result = [
        [_integer(x, 0, 255, label), _integer(y, 0, 255, label)]
        for x, y in points
    ]
    if len(result) < 2 or any(a[0] >= b[0] for a, b in zip(result, result[1:])):
        raise ValueError(f"{label} needs at least two points with increasing x")
    return result


def write_preset(
    path: str | Path,
    name: str,
    *,
    curves: Mapping[str, Sequence[Sequence[int]]],
    mixer: Mapping[str, Sequence[int]] | None = None,
    gray_mixer: Mapping[str, int] | None = None,
) -> Path:
    """Write a Normal preset with point curves and one optional color mixer."""
    if not name.strip():
        raise ValueError("Preset name cannot be empty")
    if not curves or curves.keys() - CURVE_FIELDS.keys():
        raise ValueError("Provide point curves named master, red, green, or blue")
    if mixer is not None and gray_mixer is not None:
        raise ValueError("Choose a color mixer or a B&W mixer")
    if mixer is not None and (not mixer or mixer.keys() - set(BANDS)):
        raise ValueError("Provide color mixer values for Adobe color bands")
    if gray_mixer is not None and (not gray_mixer or gray_mixer.keys() - set(BANDS)):
        raise ValueError("Provide B&W mixer values for Adobe color bands")

    # Match the RDF shape and Process Version used by Adobe's bundled presets.
    ET.register_namespace("x", XMP)
    ET.register_namespace("rdf", RDF)
    ET.register_namespace("crs", CRS)
    xmp = ET.Element(f"{{{XMP}}}xmpmeta")
    rdf = ET.SubElement(xmp, f"{{{RDF}}}RDF")
    settings = {
        "PresetType": "Normal",
        "SupportsAmount": "False",
        "SupportsColor": "True",
        "SupportsMonochrome": "True",
        "SupportsHighDynamicRange": "False",
        "SupportsNormalDynamicRange": "True",
        "SupportsSceneReferred": "True",
        "SupportsOutputReferred": "True",
        "CameraModelRestriction": "",
        "Version": "14.4",
        "ProcessVersion": "11.0",
        "ConvertToGrayscale": "True" if gray_mixer is not None else "False",
        "ToneCurveName2012": "Custom",
        "HasSettings": "True",
    }

    if mixer is not None:
        for band, values in mixer.items():
            for axis, value in zip(MIXER_AXES, values, strict=True):
                key = axis + band.title()
                settings[key] = str(_integer(value, -100, 100, key))
    if gray_mixer is not None:
        for band, value in gray_mixer.items():
            key = "GrayMixer" + band.title()
            settings[key] = str(_integer(value, -100, 100, key))

    description = ET.SubElement(rdf, f"{{{RDF}}}Description", {f"{{{RDF}}}about": ""})
    for field, value in settings.items():
        description.set(f"{{{CRS}}}{field}", value)

    for field, value in (("Name", name), ("Group", "Language Guided LUT")):
        text = ET.SubElement(description, f"{{{CRS}}}{field}")
        alternate = ET.SubElement(text, f"{{{RDF}}}Alt")
        ET.SubElement(alternate, f"{{{RDF}}}li", {XML_LANG: "x-default"}).text = value

    for curve, field in CURVE_FIELDS.items():
        if curve in curves:
            points = _curve_points(curves[curve], curve)
            element = ET.SubElement(description, f"{{{CRS}}}{field}")
            sequence = ET.SubElement(element, f"{{{RDF}}}Seq")
            for x, y in points:
                ET.SubElement(sequence, f"{{{RDF}}}li").text = f"{x}, {y}"

    fingerprint = ET.tostring(description, encoding="unicode")
    preset_uuid = uuid5(NAMESPACE_URL, "language-guided-lut:preset:" + fingerprint)
    description.set(f"{{{CRS}}}UUID", preset_uuid.hex.upper())
    tree = ET.ElementTree(xmp)
    ET.indent(tree, space=" ")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        pending = Path(temporary) / output.name
        tree.write(pending, encoding="utf-8", xml_declaration=True)
        ET.parse(pending)
        pending.replace(output)
    return output


def read_preset(path: str | Path) -> dict:
    """Read point curves and the active mixer from a Normal develop preset."""
    root = ET.parse(path).getroot()
    description = next(
        (
            element
            for element in root.iter(f"{{{RDF}}}Description")
            if element.get(f"{{{CRS}}}PresetType") == "Normal"
        ),
        None,
    )
    if description is None:
        raise ValueError("Expected an Adobe Normal develop preset")
    fields = {key.removeprefix(f"{{{CRS}}}"): value for key, value in description.attrib.items()}

    curves = {}
    for curve, field in CURVE_FIELDS.items():
        sequence = description.find(f"{{{CRS}}}{field}/{{{RDF}}}Seq")
        if sequence is not None:
            points = [
                [int(component) for component in (item.text or "").split(",")]
                for item in sequence.findall(f"{{{RDF}}}li")
            ]
            curves[curve] = _curve_points(points, curve)

    mixer = {}
    gray_mixer = {}
    if fields.get("ConvertToGrayscale", "False").lower() == "true":
        for band in BANDS:
            key = "GrayMixer" + band.title()
            if key in fields:
                gray_mixer[band] = _integer(int(fields[key]), -100, 100, key)
    else:
        for band in BANDS:
            keys = [axis + band.title() for axis in MIXER_AXES]
            if any(key in fields for key in keys):
                mixer[band] = [_integer(int(fields.get(key, "0")), -100, 100, key) for key in keys]

    return {"curves": curves, "mixer": mixer or None, "gray_mixer": gray_mixer or None}
