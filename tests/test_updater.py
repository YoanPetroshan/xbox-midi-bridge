"""Update logic tests (no network): python -m unittest discover tests"""
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import updater  # noqa: E402


def fake_app(root: Path, name="Xbox MIDI Bridge.app", bundle_id=updater.BUNDLE_ID, version="9.9.0",
             payload="new") -> Path:
    app = root / name
    (app / "Contents" / "MacOS").mkdir(parents=True)
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version}, f)
    (app / "Contents" / "MacOS" / "marker").write_text(payload)
    return app


def zip_app(app: Path, dest: Path) -> Path:
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(app), str(dest)], check=True)
    return dest


class VersionTest(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(updater.is_newer("v1.3.0", "1.2.0"))
        self.assertTrue(updater.is_newer("1.10.0", "1.9.9"))
        self.assertFalse(updater.is_newer("v1.2.0", "1.2.0"))
        self.assertFalse(updater.is_newer("v1.2", "1.2.0"))
        self.assertFalse(updater.is_newer("v1.1.9", "1.2.0"))
        self.assertEqual(updater.parse_version("v2.0.1-beta"), (2, 0, 1))

    def test_release_from_github_json(self):
        rel = updater.release_from_json({
            "tag_name": "v1.4.0", "body": "notes", "html_url": "https://example/rel",
            "assets": [{"name": "checksums.txt", "browser_download_url": "x"},
                       {"name": "Xbox.MIDI.Bridge.zip", "browser_download_url": "https://dl/zip"}],
        })
        self.assertEqual((rel.version, rel.tag, rel.zip_url), ("1.4.0", "v1.4.0", "https://dl/zip"))

    def test_running_from_source_cannot_self_update(self):
        self.assertIsNone(updater.running_bundle())
        self.assertFalse(updater.can_self_update())


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_download_and_prepare(self):
        z = zip_app(fake_app(self.tmp / "src", version="1.4.0"), self.tmp / "release.zip")
        got = updater.download(z.as_uri(), self.tmp / "dl" / "x.zip", progress=lambda d, t: None)
        app = updater.prepare(got, "1.4.0", self.tmp / "unpacked", check_signature=False)
        self.assertEqual(app.name, "Xbox MIDI Bridge.app")

    def test_rejects_other_app(self):
        z = zip_app(fake_app(self.tmp / "src", bundle_id="com.evil.app", version="1.4.0"), self.tmp / "r.zip")
        with self.assertRaises(updater.UpdateError):
            updater.prepare(z, "1.4.0", self.tmp / "u", check_signature=False)

    def test_rejects_wrong_version(self):
        z = zip_app(fake_app(self.tmp / "src", version="1.3.9"), self.tmp / "r.zip")
        with self.assertRaises(updater.UpdateError):
            updater.prepare(z, "1.4.0", self.tmp / "u", check_signature=False)

    def test_cancelled_download_leaves_nothing(self):
        z = zip_app(fake_app(self.tmp / "src"), self.tmp / "r.zip")
        dest = self.tmp / "dl" / "x.zip"
        with self.assertRaises(updater.UpdateError):
            updater.download(z.as_uri(), dest, cancelled=lambda: True)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_suffix(".zip.part").exists())


class InstallScriptTest(unittest.TestCase):
    """Runs the real install script on temporary folders."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.installed = fake_app(self.tmp / "Applications", payload="old")
        self.new = fake_app(self.tmp / "cache", payload="new")

    def run_script(self, new_app):
        app_proc = subprocess.Popen(["/bin/sleep", "0.5"])  # stands in for the running app
        script = updater.start_install(new_app, self.installed, app_proc.pid, relaunch=False)
        self.assertTrue(script.exists())
        app_proc.wait()
        marker = self.installed / "Contents" / "MacOS" / "marker"
        for _ in range(50):  # the script polls every 0.2 s
            time.sleep(0.1)
            if marker.exists() and marker.read_text() == "new":
                break
        return marker

    def test_swaps_after_app_quits(self):
        marker = self.run_script(self.new)
        self.assertEqual(marker.read_text(), "new")
        self.assertFalse(Path(str(self.installed) + ".previous").exists())

    def test_rolls_back_if_new_app_is_missing(self):
        missing = self.tmp / "cache" / "gone" / "Xbox MIDI Bridge.app"
        missing.parent.mkdir(parents=True)
        marker = self.run_script(missing)
        time.sleep(0.6)
        self.assertEqual(marker.read_text(), "old", "old version must be back in place")


if __name__ == "__main__":
    unittest.main()


class ChangelogTest(unittest.TestCase):
    def test_since_previous_lists_every_newer_version(self):
        import changelog
        md = changelog.markdown_since("1.1.0", "1.3.0")
        self.assertIn("### 1.3.0", md)
        self.assertIn("### 1.2.0", md)
        self.assertNotIn("### 1.1.0", md)
        self.assertLess(md.index("### 1.3.0"), md.index("### 1.2.0"), "newest first")

    def test_without_previous_only_current(self):
        import changelog
        md = changelog.markdown_since(None, "1.3.0")
        self.assertIn("### 1.3.0", md)
        self.assertNotIn("### 1.2.0", md)

    def test_current_version_has_notes(self):
        import changelog
        from version import APP_VERSION
        self.assertTrue(changelog.items(APP_VERSION), "add a changelog entry for every release")


class SdlEnvTest(unittest.TestCase):
    def test_playstation_full_report_mode_requested(self):
        """Without these hints a Bluetooth DualSense never reports Mic, touchpad position or gyro."""
        import os
        from input_reader import _setup_sdl_env
        _setup_sdl_env()
        self.assertEqual(os.environ.get("SDL_JOYSTICK_HIDAPI_PS5_RUMBLE"), "1")
        self.assertEqual(os.environ.get("SDL_JOYSTICK_HIDAPI_PS4_RUMBLE"), "1")
        self.assertEqual(os.environ.get("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"), "1")
