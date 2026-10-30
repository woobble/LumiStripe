from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).parents[1] / "docker" / "spotify-entrypoint.sh"


class SpotifyEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.log = self.root / "pactl.log"
        self.api_key = self.root / "api-key"
        self.api_key.write_text("test-key", encoding="utf-8")
        soloist = self.bin_dir / "soloist"
        soloist.write_text("#!/bin/sh\nexec /bin/sleep 30\n", encoding="utf-8")
        soloist.chmod(0o755)
        soloist_exit = self.bin_dir / "soloist-exit"
        soloist_exit.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        soloist_exit.chmod(0o755)
        soloist_hang = self.bin_dir / "soloist-hang"
        soloist_hang.write_text(
            "#!/bin/sh\ntrap '' TERM\nexec /bin/sleep 30\n", encoding="utf-8"
        )
        soloist_hang.chmod(0o755)

        pactl = self.bin_dir / "pactl"
        pactl.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$PACTL_LOG"\n'
            'case "$1" in\n'
            "  list) exit 0 ;;\n"
            '  load-module) printf "42\\n" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        pactl.chmod(0o755)

        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin_dir}:{self.env['PATH']}",
                "PACTL_LOG": str(self.log),
                "LUMI_SPOTIFY_API_KEY_FILE": str(self.api_key),
                "LUMI_SPOTIFY_CREATE_SINK": "1",
                "LUMI_SPOTIFY_BIN": str(soloist),
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unloads_created_sink_when_soloist_exits(self) -> None:
        self.env["LUMI_SPOTIFY_BIN"] = str(self.bin_dir / "soloist-exit")

        subprocess.run(["/bin/sh", str(ENTRYPOINT)], env=self.env, check=True)

        self.assertIn("unload-module 42", self.log.read_text(encoding="utf-8"))

    def test_unloads_created_sink_when_container_receives_sigterm(self) -> None:
        process = subprocess.Popen(["/bin/sh", str(ENTRYPOINT)], env=self.env)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.log.exists() and "load-module" in self.log.read_text(
                    encoding="utf-8"
                ):
                    break
                if process.poll() is not None:
                    self.fail("entrypoint exited before loading the temporary sink")
                time.sleep(0.01)
            else:
                self.fail("entrypoint did not load the temporary sink")

            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=8), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

        self.assertIn("unload-module 42", self.log.read_text(encoding="utf-8"))

    def test_forces_down_unresponsive_soloist_before_unloading_sink(self) -> None:
        self.env["LUMI_SPOTIFY_BIN"] = str(self.bin_dir / "soloist-hang")
        process = subprocess.Popen(["/bin/sh", str(ENTRYPOINT)], env=self.env)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.log.exists() and "load-module" in self.log.read_text(
                    encoding="utf-8"
                ):
                    break
                if process.poll() is not None:
                    self.fail("entrypoint exited before loading the temporary sink")
                time.sleep(0.01)
            else:
                self.fail("entrypoint did not load the temporary sink")

            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=8), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

        self.assertIn("unload-module 42", self.log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
