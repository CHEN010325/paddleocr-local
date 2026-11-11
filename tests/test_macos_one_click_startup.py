import os
import secrets
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_VENVS = (
    ("paddleocr-vl-1.6", "PANDOCR_MACOS_VENV", ".venv-macos"),
    ("pp-ocrv6", "PANDOCR_MACOS_VENV", ".venv-macos"),
    ("unlimited-ocr", "UNLIMITED_OCR_MACOS_VENV", ".venv-unlimited-ocr-macos"),
    ("ovisocr3", "OVISOCR2_MACOS_VENV", ".venv-ovisocr3-macos"),
    ("hpd-parsing", "HPD_PARSING_MACOS_VENV", ".venv-hpd-parsing-macos"),
)


class MacosOneClickStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        if os.name == "nt":
            git = shutil.which("git")
            git_bash = Path(git).parents[1] / "bin/bash.exe" if git else None
            if git_bash and git_bash.is_file():
                cls.bash = str(git_bash)
        if not cls.bash:
            raise unittest.SkipTest("Bash is not available")
        cls.shell_environment = {"PATH": os.environ.get("PATH", os.defpath)}
        try:
            with tempfile.TemporaryDirectory() as directory:
                probe_script = Path(directory) / "bash-probe"
                probe_script.write_bytes(
                    b"#!/usr/bin/env bash\n"
                    b"(( BASH_VERSINFO[0] > 3 || "
                    b"(BASH_VERSINFO[0] == 3 && BASH_VERSINFO[1] >= 2) ))\n"
                )
                probe_script.chmod(0o755)
                probe = subprocess.run(
                    [cls.bash, "-c", "./bash-probe"],
                    cwd=directory,
                    env=cls.shell_environment,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
        except (OSError, subprocess.SubprocessError):
            raise unittest.SkipTest("A compatible Bash runtime is not available")
        if probe.returncode != 0:
            raise unittest.SkipTest("A compatible Bash runtime is not available")

    def check_startup(
        self,
        existing_directories=False,
        model="paddleocr-vl-1.6",
        venv=".venv-macos",
        overrides=None,
        health="ready",
        controller_state="alive",
        bootstrap_usable=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            launcher = (ROOT / "scripts/macos-one-click.sh").read_text()
            (scripts / "macos-one-click.sh").write_bytes(
                launcher.split("trap fail_with_logs ERR", 1)[0].encode("utf-8")
            )
            (scripts / "macos-python.sh").write_bytes(
                (ROOT / "scripts/macos-python.sh").read_text().encode("utf-8")
            )
            if existing_directories:
                for name in ("logs", "run"):
                    (root / name).mkdir()
                    (root / name / "keep").write_text("existing data")
                (root / "logs/macos-controller.log").write_text("previous log")

            runtime_python = root / venv / "bin/python"
            runtime_python.parent.mkdir(parents=True)
            runtime_python.write_bytes(r"""#!/usr/bin/env bash
[[ -f "$3" && ! -s "$3" ]] || exit 1
cat >/dev/null
printf 'controller-launched\n' >> "$3"
if [[ "$TEST_CONTROLLER_STATE" == dead ]]; then
  printf 'No module named uvicorn\n' >> "$3"
fi
printf '%s\n' "$0" > run/controller-python
printf '999999\n'
""".encode("utf-8"))
            runtime_python.chmod(0o755)
            bootstrap_python = runtime_python
            if not bootstrap_usable:
                bootstrap_python = root / (
                    ".venv-macos/bin/python" if model == "ovisocr3"
                    else "bootstrap/bin/python"
                )
                bootstrap_python.parent.mkdir(parents=True, exist_ok=True)
                bootstrap_python.write_bytes(r"""#!/usr/bin/env bash
printf 'bootstrap Python has no controller dependencies\n' >&2
exit 1
""".encode("utf-8"))
                bootstrap_python.chmod(0o755)

            token = secrets.token_hex(32)
            result = subprocess.run(
                [self.bash, "-c", r"""
source scripts/macos-one-click.sh --model "$TEST_MODEL"
kill() {
  [[ "$1" == -0 && "$2" == 999999 && "$TEST_CONTROLLER_STATE" == alive ]]
}
curl() {
  local header=0 url=0
  local argument
  for argument in "$@"; do
    [[ "$argument" != "X-Pandocr-Controller-Token:$PANDOCR_MODEL_CONTROLLER_TOKEN" ]] || header=1
    [[ "$argument" != "http://127.0.0.1:8001/health" ]] || url=1
  done
  [[ "$header" == 1 && "$url" == 1 ]] || return 2
  printf 'health-request\n' >> run/startup-events
  HEALTH_ATTEMPTS=$((HEALTH_ATTEMPTS + 1))
  case "$TEST_HEALTH" in
    ready) return 0 ;;
    delayed) (( HEALTH_ATTEMPTS >= 3 )) ;;
    failed) return 1 ;;
  esac
}
sleep() { SECONDS=$((SECONDS + 1)); }
bash() {
  [[ "$1" == scripts/start-macos.sh ]] || return 1
  printf 'downstream-started\n' >> run/startup-events
  printf 'downstream-started\n'
}
HEALTH_ATTEMPTS=0
start_services
"""],
                cwd=root,
                env={
                    **self.shell_environment,
                    "PANDOCR_MODEL_CONTROLLER_TOKEN": token,
                    "PYTHON_BIN": bootstrap_python.relative_to(root).as_posix(),
                    "TEST_MODEL": model,
                    "TEST_HEALTH": health,
                    "TEST_CONTROLLER_STATE": controller_state,
                    **(overrides or {}),
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            if health == "failed":
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("downstream-started", result.stdout)
                self.assertIn("logs/macos-controller.log", result.stdout)
                self.assertIn("controller-launched", result.stdout)
                if controller_state == "dead":
                    self.assertIn("No module named uvicorn", result.stdout)
                    self.assertIn("exited", result.stdout)
                    expected_attempts = 1
                else:
                    self.assertIn("30s", result.stdout)
                    expected_attempts = None
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("downstream-started", result.stdout)
                expected_attempts = 3 if health == "delayed" else 1
            events = (root / "run/startup-events").read_text().splitlines()
            if expected_attempts is not None:
                expected_events = ["health-request"] * expected_attempts
                if health != "failed":
                    expected_events.append("downstream-started")
                self.assertEqual(events, expected_events)
            else:
                self.assertTrue(events)
                self.assertTrue(all(event == "health-request" for event in events))
            self.assertEqual(
                (root / (root / "run/controller-python").read_text().strip()).resolve(),
                runtime_python.resolve(),
            )
            self.assertEqual(
                (root / "logs/macos-controller.log").read_text(),
                "controller-launched\n"
                + ("No module named uvicorn\n" if controller_state == "dead" else ""),
            )
            self.assertEqual((root / "run/macos-controller.pid").read_text(), "999999\n")
            self.assertEqual((root / "run/macos-controller.token").read_text(), token)
            if existing_directories:
                for name in ("logs", "run"):
                    self.assertEqual((root / name / "keep").read_text(), "existing data")

    def test_startup_creates_missing_directories(self):
        self.check_startup(existing_directories=False, bootstrap_usable=True)

    def test_startup_preserves_existing_directories(self):
        self.check_startup(existing_directories=True, bootstrap_usable=True)

    def test_controller_uses_selected_model_runtime(self):
        for model, _, venv in MODEL_VENVS:
            with self.subTest(model=model):
                self.check_startup(model=model, venv=venv)

    def test_controller_preserves_venv_overrides(self):
        for model, setting, _ in MODEL_VENVS:
            with self.subTest(model=model):
                venv = "custom runtime/" + model
                self.check_startup(model=model, venv=venv, overrides={setting: venv})

    def test_controller_health_failure_stops_downstream_after_exit(self):
        self.check_startup(
            health="failed", controller_state="dead", bootstrap_usable=True
        )

    def test_controller_health_timeout_stops_downstream(self):
        self.check_startup(health="failed", bootstrap_usable=True)

    def test_controller_waits_for_authenticated_health_before_downstream(self):
        self.check_startup(health="delayed", bootstrap_usable=True)
