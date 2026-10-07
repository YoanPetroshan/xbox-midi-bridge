"""Update check and self-update from GitHub Releases.

check:   GET the latest release from the GitHub API, compare its tag with APP_VERSION.
update:  download the release's .zip, unpack it, verify it is this app (bundle id and
         the expected version, intact ad-hoc signature), then hand over to a tiny shell
         script that waits for the app to quit, swaps the bundles (rolling back if the
         swap fails) and relaunches the new version.

Only the packaged .app can update itself; when running from source the release page
is opened instead.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from version import APP_VERSION

REPO = "YoanPetroshan/xbox-midi-bridge"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
BUNDLE_ID = "com.yoan.xboxmidibridge"
CACHE_DIR = Path(os.path.expanduser("~/Library/Caches/XboxMidiBridge/update"))
USER_AGENT = f"XboxMidiBridge/{APP_VERSION}"


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str  # "1.3.0"
    tag: str  # "v1.3.0"
    notes: str  # release notes (Markdown)
    zip_url: str | None  # the .app zip asset, if the release has one
    page: str  # release page in the browser


def parse_version(s: str) -> tuple[int, ...]:
    """'v1.2.10' → (1, 2, 10). Anything after the numbers (e.g. '-beta') is ignored."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", (s or "").strip())
    if not m:
        return (0,)
    parts = [int(p) for p in m.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()  # 1.2.0 == 1.2
    return tuple(parts)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return parse_version(latest) > parse_version(current)


def _ssl_context() -> ssl.SSLContext:
    # The python.org Python in the .app has no system certificates: use certifi's bundle.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                              "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def release_from_json(data: dict) -> Release:
    tag = data.get("tag_name") or ""
    zip_url = None
    for asset in data.get("assets") or []:
        name = asset.get("name", "")
        if name.lower().endswith(".zip") and "bridge" in name.lower():
            zip_url = asset.get("browser_download_url")
            break
    return Release(version=tag.strip().lstrip("vV"), tag=tag,
                   notes=data.get("body") or "", zip_url=zip_url,
                   page=data.get("html_url") or RELEASES_PAGE)


def fetch_latest(timeout: float = 10.0) -> Release:
    try:
        with _open(API_LATEST, timeout) as r:
            return release_from_json(json.loads(r.read().decode("utf-8")))
    except Exception as e:
        raise UpdateError(str(e)) from e


# ---------------------------------------------------------------- self-update

def running_bundle() -> Path | None:
    """The .app this process runs from (packaged app only)."""
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def can_self_update(bundle: Path | None = None) -> bool:
    bundle = bundle if bundle is not None else running_bundle()
    return bool(bundle and os.access(bundle.parent, os.W_OK) and os.access(bundle, os.W_OK))


def download(url: str, dest: Path, progress=None, cancelled=lambda: False, timeout: float = 30.0) -> Path:
    """Download to `dest`. progress(done_bytes, total_bytes_or_0) is called as data arrives."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with _open(url, timeout) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancelled():
                    raise UpdateError("cancelled")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if total and done != total:
            raise UpdateError(f"incomplete download ({done} of {total} bytes)")
        os.replace(tmp, dest)
        return dest
    except UpdateError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise UpdateError(str(e)) from e


def app_info(app: Path) -> dict:
    with open(app / "Contents" / "Info.plist", "rb") as f:
        return plistlib.load(f)


def prepare(zip_path: Path, expected_version: str, workdir: Path, check_signature: bool = True) -> Path:
    """Unpack the zip and make sure it holds this app in the expected version. Returns the .app."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    r = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zip_path), str(workdir)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise UpdateError(f"unzip failed: {r.stderr.strip()}")
    apps = [p for p in workdir.iterdir() if p.suffix == ".app"]
    if len(apps) != 1:
        raise UpdateError("the download does not contain exactly one .app")
    app = apps[0]
    try:
        info = app_info(app)
    except Exception as e:
        raise UpdateError(f"invalid app bundle: {e}") from e
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise UpdateError(f"unexpected app: {info.get('CFBundleIdentifier')}")
    if parse_version(info.get("CFBundleShortVersionString", "")) != parse_version(expected_version):
        raise UpdateError(f"version mismatch: {info.get('CFBundleShortVersionString')} ≠ {expected_version}")
    if check_signature:
        r = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise UpdateError(f"signature check failed: {r.stderr.strip()}")
    return app


def install_script(new_app: Path, bundle: Path, pid: int, relaunch: bool = True) -> str:
    """Shell script: wait for `pid` to exit, swap the bundles (with rollback), relaunch."""
    old, new = shlex.quote(str(bundle)), shlex.quote(str(new_app))
    backup = shlex.quote(str(bundle) + ".previous")
    return f"""#!/bin/sh
# Xbox MIDI Bridge updater: replaces {bundle.name} after the app has quit.
i=0
while kill -0 {pid} 2>/dev/null; do
  sleep 0.2; i=$((i+1))
  [ $i -gt 300 ] && exit 1   # the app didn't quit within a minute: leave everything as is
done
rm -rf {backup}
if mv {old} {backup}; then
  if mv {new} {old}; then
    rm -rf {backup}
  else
    mv {backup} {old}   # roll back to the old version
  fi
fi
xattr -dr com.apple.quarantine {old} 2>/dev/null
{"open " + old if relaunch else ":"}
"""


def start_install(new_app: Path, bundle: Path, pid: int | None = None, relaunch: bool = True) -> Path:
    """Write and launch the install script detached; the caller must then quit the app."""
    script = new_app.parent / "install.sh"
    script.write_text(install_script(new_app, bundle, pid or os.getpid(), relaunch))
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    return script


def open_release_page(url: str = RELEASES_PAGE) -> None:
    subprocess.Popen(["/usr/bin/open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def work_paths(version: str) -> tuple[Path, Path]:
    """(zip path, unpack dir) for a version, under ~/Library/Caches."""
    base = CACHE_DIR / version
    return base / "XboxMidiBridge.zip", base / "app"


def cleanup_old_downloads() -> None:
    shutil.rmtree(CACHE_DIR, ignore_errors=True)


def temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="xmb-update-"))
