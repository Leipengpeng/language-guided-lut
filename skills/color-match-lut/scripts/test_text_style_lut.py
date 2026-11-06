"""Local numeric/file tests for the two-stage text-style workflow. No model calls."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import color_match_lut as c
import text_style_lut as t


class TextStylePipeline(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content = self.root / "original.png"
        rgb = (.2 + .6 * np.random.default_rng(51).random((240, 360, 3))) * 255
        Image.fromarray(rgb.astype(np.uint8)).save(self.content, icc_profile=c.srgb_profile())

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return t.prepare(self.content, "一种朦胧又清新的颜色感觉", self.root / "job", **kwargs)

    def finish(self, styled, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return t.finish(self.root / "job/job.json", styled, size=17, **kwargs)

    def test_upload_bound_colour_profile_no_exif_and_exact_analysis(self):
        original_hash = c.digest(self.content)
        job = self.prepare(edge=256)
        with Image.open(job["upload"]["path"]) as upload, Image.open(job["analysis_source"]["path"]) as analysis:
            self.assertEqual(max(upload.size), 256)
            self.assertFalse(upload.getexif())
            self.assertTrue(upload.info.get("icc_profile"))
            np.testing.assert_array_equal(np.asarray(upload), np.asarray(analysis))
        self.assertEqual(c.digest(self.content), original_hash)
        self.assertEqual(job["upload"]["bytes"], Path(job["upload"]["path"]).stat().st_size)
        self.assertEqual(job["content"]["oriented_size"], [360, 240])
        self.assertIn("一种朦胧又清新的颜色感觉", Path(job["prompt_file"]).read_text(encoding="utf-8"))

    def test_exif_orientation_applied_without_upscaling(self):
        image = Image.new("RGB", (24, 40), (50, 75, 100))
        exif = Image.Exif()
        exif[274] = 6
        path = self.root / "oriented.jpg"
        image.save(path, exif=exif)
        with contextlib.redirect_stdout(io.StringIO()):
            job = t.prepare(path, "清新", self.root/"job", edge=512)
        self.assertEqual(job["upload"]["size"], [40, 24])
        with Image.open(job["upload"]["path"]) as upload:
            self.assertFalse(upload.getexif())

    def test_default_upload_and_explicit_4k_preserve_aspect(self):
        path = self.root / "wide.png"
        Image.new("RGB", (4200, 840), (50, 75, 100)).save(path)
        with contextlib.redirect_stdout(io.StringIO()):
            default = t.prepare(path, "赛博朋克风格", self.root / "default")
            four_k = t.prepare(path, "赛博朋克风格", self.root / "four_k", edge=3840)
        self.assertEqual(default["upload"]["size"], [1080, 216])
        self.assertEqual(four_k["upload"]["size"], [3840, 768])

    def test_invalid_limits_and_empty_style(self):
        for value in (1, 4097):
            with self.assertRaises(ValueError):
                self.prepare(edge=value)
        with self.assertRaises(ValueError):
            t.prepare(self.content, " ", self.root/"job")
        with self.assertRaises(ValueError):
            self.prepare(quality=100)

    def test_natural_profile_default_preserves_original_request(self):
        request = "  强烈的赛博朋克，但仍像真实照片\n保留原来的光线  "
        with contextlib.redirect_stdout(io.StringIO()):
            # All existing positional arguments retain their original meaning.
            job = t.prepare(self.content, request, self.root/"job", 128, 85, None, False, False)
        saved = json.loads((self.root/"job/job.json").read_text(encoding="utf-8"))
        prompt = Path(job["prompt_file"]).read_text(encoding="utf-8")
        self.assertEqual(job["prompt_profile"], "natural")
        self.assertEqual(saved["prompt_profile"], "natural")
        self.assertEqual(saved["style_text"], request)
        self.assertIn(f"<style_request>\n{request}\n</style_request>", prompt)
        self.assertIn("Selected prompt profile: natural.", prompt)
        self.assertIn("explicit color directions and intensity", prompt)

    def test_explicit_prompt_profiles_are_saved_and_change_guidance(self):
        prompts = []
        for profile in ("natural", "balanced", "strong"):
            with self.subTest(profile=profile), contextlib.redirect_stdout(io.StringIO()):
                folder = self.root/profile
                job = t.prepare(self.content, "清新", folder, edge=128, prompt_profile=profile)
                saved = json.loads((folder/"job.json").read_text(encoding="utf-8"))
                prompt = Path(job["prompt_file"]).read_text(encoding="utf-8")
                self.assertEqual(saved["prompt_profile"], profile)
                self.assertIn(f"Selected prompt profile: {profile}", prompt)
                self.assertEqual(job["prompt_sha256"], c.digest(job["prompt_file"]))
                prompts.append(prompt)
        self.assertEqual(len(set(prompts)), 3)

    def test_invalid_prompt_profile_is_rejected_before_writing(self):
        with self.assertRaises(ValueError):
            self.prepare(prompt_profile="automatic")
        self.assertFalse((self.root/"job").exists())
        with self.assertRaises(ValueError):
            t.make_prompt("清新", prompt_profile="automatic")

    def test_cli_prompt_profile_defaults_and_explicit_selection(self):
        base = ["prepare", "--content", str(self.content), "--style-text", "强烈调色",
                "--output", str(self.root/"job")]
        for profile in (None, "balanced", "strong"):
            with self.subTest(profile=profile), patch.object(t, "prepare") as prepare:
                t.main(base + (["--prompt-profile", profile] if profile else []))
                self.assertEqual(prepare.call_args.args[-1], profile or "natural")

    def test_reviewed_prompt_retains_wording_and_adds_guardrails(self):
        prompt = "Edit the supplied photograph. Warm highlights and preserve the exposure."
        reviewed = self.root / "reviewed.txt"
        reviewed.write_text(prompt, encoding="utf-8-sig")
        job = self.prepare(edge=128, image_prompt_file=reviewed)
        self.assertEqual(Path(job["prompt_file"]).read_text(encoding="utf-8"),
                         prompt + "\n\n" + t.GRADE_GUARDRAILS)
        self.assertEqual(job["prompt_sha256"], c.digest(job["prompt_file"]))
        self.assertEqual(job["style_text"], "一种朦胧又清新的颜色感觉")
        self.assertEqual(job["prompt_origin"], "reviewed_image_prompt")
        self.assertIsNone(job["prompt_profile"])

    def test_reviewed_prompt_with_grade_guardrails_is_unchanged(self):
        prompt = f"冰田一鸣风格\n\n{t.GRADE_GUARDRAILS}"
        reviewed = self.root / "reviewed.txt"
        reviewed.write_text(prompt, encoding="utf-8")
        job = self.prepare(edge=128, image_prompt_file=reviewed)
        self.assertEqual(Path(job["prompt_file"]).read_text(encoding="utf-8"), prompt)

    def test_reviewed_prompt_upgrades_previous_guardrails_in_place(self):
        old_guard = t.GRADE_GUARDRAILS.replace(t._GLOBAL_LUT_GUARDRAILS, "", 1)
        wording = "Cyberpunk colors based on existing hue ranges."
        reviewed = self.root / "reviewed.txt"
        reviewed.write_text(f"{old_guard}\n\n{wording}", encoding="utf-8")
        job = self.prepare(edge=128, image_prompt_file=reviewed)
        prompt = Path(job["prompt_file"]).read_text(encoding="utf-8")
        self.assertEqual(prompt, f"{t.GRADE_GUARDRAILS}\n\n{wording}")
        self.assertIn("Identical input RGB values", prompt)
        self.assertIn("mirrored color effects", prompt)

    def test_reviewed_prompt_rejects_empty_or_conflicting_guidance_before_prepare(self):
        reviewed = self.root / "reviewed.txt"
        reviewed.write_text(" ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.prepare(image_prompt_file=reviewed)
        reviewed.write_text("Apply a slight warm grade.", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already includes"):
            self.prepare(image_prompt_file=reviewed, prompt_profile="strong")
        self.assertFalse((self.root / "job").exists())

    def test_existing_submitted_upload_is_not_silently_replaced(self):
        folder = self.root/"job"
        folder.mkdir()
        small, _ = c.read_srgb(self.content, long_edge=128)
        upload = folder/"upload.jpg"
        small.save(upload, quality=70, icc_profile=c.srgb_profile(), exif=b"")
        checksum = c.digest(upload)
        with self.assertRaises(ValueError):
            self.prepare(edge=128)
        self.assertEqual(c.digest(upload), checksum)
        job = self.prepare(edge=128, reuse_upload=True)
        self.assertEqual(job["upload"]["sha256"], checksum)
        self.assertTrue(job["upload"]["reused_existing_bytes"])

    def test_changed_original_is_rejected_before_fitting(self):
        job = self.prepare(edge=128)
        styled = self.root/"style.png"
        Image.open(job["upload"]["path"]).save(styled)
        Image.new("RGB", (360, 240), "red").save(self.content)
        with self.assertRaises(ValueError):
            self.finish(styled, apply=True)
        self.assertFalse((self.root/"job/render").exists())

    def test_changed_upload_is_rejected(self):
        job = self.prepare(edge=128)
        styled = self.root/"style.png"
        Image.open(job["upload"]["path"]).save(styled)
        Path(job["upload"]["path"]).write_bytes(b"changed bytes")
        with self.assertRaises(ValueError):
            self.finish(styled)

    def test_identity_guide_roundtrip_and_original_resolution(self):
        job = self.prepare(edge=128)
        styled = self.root/"generated.png"
        with Image.open(job["upload"]["path"]) as upload:
            upload.save(styled, icc_profile=c.srgb_profile())
        original_hash = c.digest(self.content)
        report = self.finish(styled, apply=True)
        self.assertEqual(report["render"]["output_size"], [360, 240])
        self.assertEqual(report["render"]["png_lut_verification_max_error_8bit"], 0)
        self.assertEqual(report["styled_input"]["sha256"], c.digest(styled))
        self.assertNotEqual(report["styled_input"]["path"], str(styled))
        self.assertEqual(c.digest(self.content), original_hash)
        table = c.read_cube(report["lut"]["path"])
        np.testing.assert_allclose(table, c.grid(17), atol=2e-6, rtol=0)
        with Image.open(report["render"]["png"]) as result, Image.open(self.content) as original:
            np.testing.assert_array_equal(np.asarray(result), np.asarray(original))

    def test_known_colour_grade_is_approximated_and_baked(self):
        job = self.prepare(edge=128)
        with Image.open(job["upload"]["path"]) as upload:
            rgb = np.asarray(upload, dtype=np.float32) / 255
        lab = c.to_oklab(rgb)
        lab[..., 0] = .95*lab[..., 0] + .02
        lab[..., 1:] *= .8
        guide = np.clip(c.from_oklab(lab), 0, 1)
        styled = self.root/"generated.png"
        Image.fromarray(np.rint(guide*255).astype(np.uint8)).save(styled)
        report = self.finish(styled, apply=True)
        self.assertLess(report["diagnostics"]["lut_vs_generated_guide_rmse_8bit"], 2)
        self.assertEqual(report["render"]["png_lut_verification_max_error_8bit"], 0)
        self.assertTrue(Path(report["lut"]["path"]).is_file())

    def test_export_only_and_aspect_diagnostic(self):
        self.prepare(edge=128)
        styled = self.root/"generated.png"
        Image.new("RGB", (80, 120), (80, 150, 170)).save(styled)
        report = self.finish(styled)
        self.assertIsNone(report["render"])
        self.assertTrue(report["diagnostics"]["aspect_ratio_changed_more_than_two_percent"])
        self.assertEqual(report["source_fit"]["size"], [120, 80])
        self.assertEqual(report["style_fit"]["size"], [80, 120])
        self.assertFalse(list((self.root/"job/render").glob("fit/**/renders/*.png")))

    def test_auto_monochrome_routes_to_neutral_mkl_without_colorfm(self):
        job = self.prepare(edge=128)
        styled = self.root / "generated.png"
        with Image.open(job["upload"]["path"]) as uploaded:
            uploaded.convert("L").save(styled, icc_profile=None)

        with patch("compare_benchmark.configure_assets") as configure:
            report = self.finish(styled, method="auto", apply=True)

        configure.assert_not_called()
        routing = report["routing"]
        self.assertEqual(routing["requested_method"], "auto")
        self.assertEqual(routing["selected_method"], "mkl_oklab")
        self.assertTrue(routing["reference_classification"]["is_monochrome"])
        self.assertTrue(routing["force_neutral_output"])
        table = c.read_cube(report["lut"]["path"])
        self.assertFalse(np.any(np.ptp(table, axis=-1)))
        with Image.open(report["render"]["png"]) as result:
            self.assertEqual(result.size, (360, 240))
            self.assertFalse(np.any(np.ptp(np.asarray(result), axis=-1)))
        self.assertEqual(report["render"]["png_lut_verification_max_error_8bit"], 0)

    def test_auto_muted_color_routes_to_colorfm_even_with_monochrome_request(self):
        with contextlib.redirect_stdout(io.StringIO()):
            t.prepare(self.content, "Use a black and white grade", self.root / "job", edge=128)
        styled = self.root / "generated.png"
        tint = (132, 128, 126)
        Image.new("RGB", (128, 85), tint).save(styled)
        backend_root = self.root / "local_models"
        fitted = {"values": c.grid(17), "metadata": {"test_backend": True}}

        with patch("compare_benchmark.configure_assets", return_value=backend_root) as configure:
            with patch("compare_benchmark.fit_method", return_value=fitted) as fit:
                report = self.finish(styled, method="auto", backend_root=backend_root, device="cpu")

        configure.assert_called_once_with(["colorfm"], backend_root)
        fit.assert_called_once()
        method, _, reference, size, device = fit.call_args.args
        self.assertEqual((method, size, device), ("colorfm", 17, "cpu"))
        np.testing.assert_allclose(reference[0, 0], np.asarray(tint) / 255)
        routing = report["routing"]
        self.assertEqual(routing["selected_method"], "colorfm")
        self.assertFalse(routing["reference_classification"]["is_monochrome"])
        self.assertFalse(routing["force_neutral_output"])
        self.assertIsNone(report["render"])
        np.testing.assert_allclose(c.read_cube(report["lut"]["path"]), fitted["values"], atol=1e-6)

    def test_auto_color_reports_missing_backend_before_writing_outputs(self):
        self.prepare(edge=128)
        styled = self.root / "generated.png"
        Image.new("RGB", (128, 85), "red").save(styled)
        with patch("compare_benchmark.configure_assets", side_effect=FileNotFoundError("missing ColorFM")):
            with self.assertRaisesRegex(FileNotFoundError, "missing ColorFM"):
                self.finish(styled, method="auto")

        self.assertFalse((self.root / "job/render").exists())

    def test_exact_style_catalogue_matching(self):
        catalogue = {"styles": [{"id": "fresh", "name": "清新", "aliases": ["fresh"],
                                  "grading_intent": "gentle contrast", "avoid": ["redrawing objects"]}]}
        (self.root/"style_catalog.json").write_text(json.dumps(catalogue), encoding="utf-8")
        with patch.object(t, "__file__", str(self.root/"text_style_lut.py")):
            self.assertEqual(t._style("清新")["id"], "fresh")
            self.assertIsNone(t._style("一些看起来有点清新的效果"))
            with self.assertRaises(ValueError):
                t._style("x", "nonexistent")
        with patch.object(t, "_style", return_value=catalogue["styles"][0]):
            job = self.prepare(edge=128, style_id="fresh")
        prompt = Path(job["prompt_file"]).read_text(encoding="utf-8")
        self.assertEqual(job["style_catalog_match"]["id"], "fresh")
        self.assertNotIn("gentle contrast", prompt)
        self.assertIn(t.GRADE_GUARDRAILS, prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
