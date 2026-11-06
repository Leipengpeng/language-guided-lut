"""Numerical and end-to-end checks; no image display or network access."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import color_match_lut as c


class Numerics(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(1947)

    def gaussian(self, mean=None):
        q = self.rng.normal(size=(3, 3)) * .03
        return {"mean": self.rng.random(3) * .1 if mean is None else np.asarray(mean),
                "covariance": q @ q.T + np.eye(3) * c.REG}

    def test_oklab_roundtrip(self):
        rgb = np.concatenate([self.rng.random((10000, 3)), np.repeat(np.linspace(0, 1, 256)[:, None], 3, 1)])
        np.testing.assert_allclose(c.from_oklab(c.to_oklab(rgb)), rgb, atol=2e-6, rtol=0)

    def test_transport_covariance_noncommuting_and_tiny(self):
        for scale in (1, 1e-3, 1e-6):
            for _ in range(12):
                s, t = self.gaussian(), self.gaussian()
                s["covariance"] *= scale
                t["covariance"] *= scale
                a, b, error = c.transport(s, t)
                self.assertLess(error, 1e-7)
                np.testing.assert_allclose(a @ s["mean"] + b, t["mean"], atol=1e-12)
                self.assertGreater(np.linalg.eigvalsh(a).min(), 0)

    def test_barycenter_diagonal_closed_form(self):
        stds = np.array([[.04, .03, .01], [.15, .02, .04], [.09, .12, .02]])
        w = np.array([.2, .3, .5])
        covariance, meta = c.covariance_barycenter([np.diag(s ** 2) for s in stds], w)
        np.testing.assert_allclose(covariance, np.diag((w @ stds) ** 2), atol=1e-12)
        self.assertLess(meta["relative_residual"], 1e-7)

    def test_two_gaussian_barycenter_matches_geodesic(self):
        s, t = self.gaussian(), self.gaussian()
        transform, _, _ = c.transport(s, t)
        for fraction in (.1, .4, .8):
            u = (1 - fraction) * np.eye(3) + fraction * transform
            expected = u @ s["covariance"] @ u.T
            actual, _ = c.covariance_barycenter([s["covariance"], t["covariance"]], [1-fraction, fraction])
            np.testing.assert_allclose(actual, expected, rtol=2e-7, atol=1e-11)

    def test_mixture_includes_between_image_variance(self):
        cov = np.eye(3) * .002
        records = [{"mean": np.array([v, 0., 0.]), "covariance": cov} for v in (.2, .8)]
        pooled, _ = c.aggregate(records, np.array([.5, .5]), "pooled")
        average, _ = c.aggregate(records, np.array([.5, .5]), "mean-cov")
        np.testing.assert_allclose(pooled["covariance"] - average["covariance"], np.diag([.09, 0, 0]), atol=1e-14)

    def test_singletons_all_reduce_to_the_same_mkl(self):
        s, t = self.gaussian(), self.gaussian()
        a, b, _ = c.transport(s, t)
        for method in c.METHODS:
            model = c.fit_model([s], [t], method)
            np.testing.assert_allclose(model["matrix"], a, atol=1e-12)
            np.testing.assert_allclose(model["bias"], b, atol=1e-12)

    def test_order_invariance_and_zero_weight(self):
        source = [self.gaussian() for _ in range(3)]
        target = [self.gaussian() for _ in range(4)]
        for method in c.METHODS[:-1]:
            a = c.fit_model(source, target, method, [1, 2, 3], [1, 2, 3, 0])
            b = c.fit_model(source[::-1], target[2::-1], method, [3, 2, 1], [3, 2, 1])
            np.testing.assert_allclose(a["matrix"], b["matrix"], rtol=1e-7, atol=1e-9)
            np.testing.assert_allclose(a["bias"], b["bias"], atol=1e-9)

    def test_same_bank_identity_for_distribution_aggregations(self):
        bank = [self.gaussian() for _ in range(4)]
        for method in c.METHODS[:4]:
            model = c.fit_model(bank, bank, method)
            np.testing.assert_allclose(model["matrix"], np.eye(3), atol=1e-9)
            np.testing.assert_allclose(model["bias"], 0, atol=1e-9)

    def test_robust_outlier_downweight(self):
        covariance = np.eye(3) * .001
        records = [{"mean": np.array([v, 0, 0]), "covariance": covariance} for v in (.50, .501, .499, .95)]
        w, meta = c.robust_weights(records, c.weights(None, 4))
        self.assertLess(w[-1], .02)
        self.assertAlmostEqual(w.sum(), 1)
        self.assertGreater(meta["cutoff"], 0)

    def test_weighted_pairwise_map_is_mean_of_maps(self):
        source, target = [self.gaussian() for _ in range(2)], [self.gaussian() for _ in range(3)]
        model = c.fit_model(source, target, "pairwise", [1, 3], [1, 2, 1])
        a, b = np.zeros((3, 3)), np.zeros(3)
        for i, ws in enumerate([.25, .75]):
            for j, wt in enumerate([.25, .5, .25]):
                aa, bb, _ = c.transport(source[i], target[j])
                a += ws * wt * aa
                b += ws * wt * bb
        np.testing.assert_allclose(model["matrix"], a, atol=1e-12)
        np.testing.assert_allclose(model["bias"], b, atol=1e-12)

    def test_zero_strength_and_constant_images(self):
        source = c.gaussian_stats(c.to_oklab(np.ones((1, 3)) * .2))
        target = c.gaussian_stats(c.to_oklab(np.ones((50, 3)) * .7))
        rgb = self.rng.random((256, 3))
        for method in c.METHODS:
            model = c.fit_model([source], [target], method, strength=0)
            np.testing.assert_allclose(c.mapping(rgb, model), rgb, atol=2e-6, rtol=0)
            model["strength"] = 1
            self.assertTrue(np.isfinite(c.mapping(rgb, model)).all())

    def test_invalid_weights_and_strength(self):
        for values in ([0, 0], [-1, 2], [float("nan"), 1], [1]):
            with self.assertRaises(ValueError):
                c.weights(values, 2)
        with self.assertRaises(ValueError):
            c.fit_model([self.gaussian()], [self.gaussian()], strength=2)


class Files(unittest.TestCase):
    def test_cube_axis_order_and_independent_interpolation(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "asymmetric.cube"
            g = c.grid(17)
            table = np.stack([.1 + .7*g[..., 2], .1 + .4*g[..., 0] + .3*g[..., 1], .8 - .6*g[..., 1]], -1)
            c.write_cube(p, table)
            loaded = c.read_cube(p)
            np.testing.assert_allclose(table, loaded, atol=1e-8)
            points = np.random.default_rng(7).random((128, 3), dtype=np.float32)
            actual = c.apply_cube(points, loaded)
            expected = np.stack([.1 + .7*points[:, 2], .1 + .4*points[:, 0] + .3*points[:, 1], .8 - .6*points[:, 1]], -1)
            np.testing.assert_allclose(actual, expected, atol=1e-7)
            rows = [line for line in p.read_text().splitlines() if line and line[0].isdigit()]
            # Red-fastest storage: the second row changes red, so only output green changes here.
            np.testing.assert_allclose(np.array(rows[1].split(), float) - np.array(rows[0].split(), float), [0, .4/16, 0], atol=1e-8)

    def test_cli_export_only_apply_and_exif_icc(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            root = Path(folder)
            a, b = root / "content.jpg", root / "style.png"
            rgb = (np.random.default_rng(12).random((30, 50, 3)) * 255).astype(np.uint8)
            exif = Image.Exif()
            exif[274] = 6
            Image.fromarray(rgb).save(a, exif=exif, icc_profile=c.srgb_profile())
            Image.fromarray(rgb[:, ::-1]).save(b)
            c.main(["fit", "--content", str(a), "--style", str(b), "--output", str(root/"fit")])
            self.assertFalse(list((root/"fit").rglob("*.png")))
            lut = root/"fit/bures/look.cube"
            self.assertTrue(lut.exists())
            c.main(["apply", "--lut", str(lut), "--content", str(a), "--output", str(root/"apply")])
            report = json.loads((root/"apply/apply.json").read_text(encoding="utf-8"))
            render = report["renders"][0]
            self.assertEqual(render["output_size"], [30, 50])
            self.assertEqual(render["png_lut_verification_max_error_8bit"], 0)
            with Image.open(render["png"]) as saved:
                self.assertTrue(saved.info.get("icc_profile"))
            with self.assertRaises(FileExistsError):
                c.main(["apply", "--lut", str(lut), "--content", str(a), "--output", str(root/"apply")])

    def test_invalid_cube_and_duplicate_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            p = root/"bad.cube"
            p.write_text("LUT_3D_SIZE 2\nDOMAIN_MIN -1 0 0\n")
            with self.assertRaises(ValueError):
                c.read_cube(p)
            p.write_text("LUT_3D_SIZE 2\n0 0 0\n")
            with self.assertRaises(ValueError):
                c.read_cube(p)
            image = root/"image.png"
            Image.new("RGB", (1, 1)).save(image)
            with self.assertRaises(ValueError):
                c.expand_paths([image, image])

    def test_multi_cli_all_methods_and_local_review(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            root = Path(folder)
            paths = []
            for i in range(3):
                path = root/f"input{i}.png"
                rgb = (np.random.default_rng(i).random((32, 48, 3)) * 255).astype(np.uint8)
                Image.fromarray(rgb).save(path)
                paths.append(str(path))
            output = root/"comparison"
            c.main(["fit", "--content", *paths[:2], "--style", *paths, "--methods", "all", "--size", "17",
                    "--style-weights", "3", "1", "1", "--apply", "--output", str(output)])
            report = json.loads((output/"run.json").read_text(encoding="utf-8"))
            self.assertEqual(len(report["methods"]), 6)
            np.testing.assert_allclose(report["style_weights"], [.6, .2, .2])
            for info in report["methods"].values():
                self.assertEqual(len(info["renders"]), 2)
                self.assertTrue(all(r["png_lut_verification_max_error_8bit"] == 0 for r in info["renders"]))
            html = (output/"review.html").read_text(encoding="utf-8")
            self.assertNotIn("__DATA__", html)
            # Inspect all generated asset paths, rather than testing text labels.
            data = json.loads(html.split("const D=", 1)[1].split(";\nconst $=", 1)[0])
            self.assertEqual(len(data["pages"]), 2)
            from urllib.parse import unquote
            for page in data["pages"]:
                for item in [page["content"], *page["references"], *page["results"].values()]:
                    for key in ("url", "preview", "cube"):
                        if key in item:
                            self.assertTrue((output/unquote(item[key])).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
