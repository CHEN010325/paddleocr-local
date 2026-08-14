import importlib.metadata
import importlib.util
import io
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check-mlx-runtime.py"
SPEC = importlib.util.spec_from_file_location("check_mlx_runtime", SCRIPT_PATH)
CHECK_MLX_RUNTIME = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CHECK_MLX_RUNTIME)


class CheckMlxRuntimeTests(unittest.TestCase):
    def test_required_versions_reads_only_exact_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            requirements = Path(directory) / "requirements.txt"
            requirements.write_text(
                "mlx-lm==1.2.3\n"
                "mlx-vlm >= 2.0\n"
                "# comment\n"
                "transformers==4.5.6  # pinned\n",
                encoding="utf-8",
            )
            with patch.object(CHECK_MLX_RUNTIME, "REQUIREMENTS_FILE", requirements):
                self.assertEqual(
                    CHECK_MLX_RUNTIME.required_versions(),
                    [("mlx-lm", "1.2.3"), ("transformers", "4.5.6")],
                )

    def test_check_runtime_returns_verified_versions(self):
        with (
            patch.object(
                CHECK_MLX_RUNTIME,
                "required_versions",
                return_value=[("mlx-lm", "1.2.3"), ("mlx-vlm", "2.3.4")],
            ),
            patch.object(
                CHECK_MLX_RUNTIME.importlib.metadata,
                "version",
                side_effect=lambda package: {
                    "mlx-lm": "1.2.3",
                    "mlx-vlm": "2.3.4",
                }[package],
            ),
            patch.object(CHECK_MLX_RUNTIME.importlib, "import_module"),
        ):
            versions = CHECK_MLX_RUNTIME.check_runtime()

        self.assertEqual(versions, "mlx-lm=1.2.3, mlx-vlm=2.3.4")

    def test_check_runtime_fails_for_missing_or_mismatched_package(self):
        with (
            patch.object(
                CHECK_MLX_RUNTIME,
                "required_versions",
                return_value=[("mlx-lm", "1.2.3")],
            ),
            patch.object(
                CHECK_MLX_RUNTIME.importlib.metadata,
                "version",
                side_effect=importlib.metadata.PackageNotFoundError,
            ),
        ):
            with self.assertRaises(SystemExit):
                CHECK_MLX_RUNTIME.check_runtime()

    def test_python_version_import_failure_and_main(self):
        version = SimpleNamespace(major=3, minor=11, micro=9)
        version.__getitem__ = lambda *_: None

        class Version:
            major = 3
            minor = 11
            micro = 9

            def __getitem__(self, item):
                return (3, 11, 9)[item]

        with patch.object(CHECK_MLX_RUNTIME.sys, "version_info", Version()):
            with self.assertRaises(SystemExit):
                CHECK_MLX_RUNTIME.check_runtime()

        with patch.object(CHECK_MLX_RUNTIME, "required_versions", return_value=[]), patch.object(
            CHECK_MLX_RUNTIME.importlib, "import_module", side_effect=RuntimeError("broken")
        ):
            with self.assertRaises(SystemExit):
                CHECK_MLX_RUNTIME.check_runtime()

        output = io.StringIO()
        with patch.object(CHECK_MLX_RUNTIME, "check_runtime", return_value="mlx-lm=1"), redirect_stdout(output):
            CHECK_MLX_RUNTIME.main()
        self.assertIn("passed", output.getvalue())

    def test_script_main_guard(self):
        output = io.StringIO()
        module_globals = {"check_runtime": lambda: "unused"}
        with patch("importlib.metadata.version", return_value="0"), patch(
            "importlib.import_module"
        ), patch.object(Path, "read_text", return_value=""), redirect_stdout(output):
            runpy.run_path(str(SCRIPT_PATH), run_name="__main__", init_globals=module_globals)
        self.assertIn("MLX runtime check passed", output.getvalue())

        with (
            patch.object(
                CHECK_MLX_RUNTIME,
                "required_versions",
                return_value=[("mlx-lm", "1.2.3")],
            ),
            patch.object(
                CHECK_MLX_RUNTIME.importlib.metadata,
                "version",
                return_value="9.9.9",
            ),
        ):
            with self.assertRaises(SystemExit):
                CHECK_MLX_RUNTIME.check_runtime()


if __name__ == "__main__":
    unittest.main()
