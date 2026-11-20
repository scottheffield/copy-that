"""Build the Windows setup folder, optionally with a verified offline model pack.

Build from main, then tag v<VERSION> (version.py) and publish a GitHub release
with a short changelog."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_paths import APP_NAME
from model_store import verify
from ui.icons import save_app_icon, save_welcome_bitmap
from version import COMMIT_FILE, VERSION, commit


def stage_models(source, release):
    manifests = {}
    for name in ("parakeet-tdt-openvino", "Qwen3-4B-int4-ov"):
        manifest = verify(source / name)
        dest = release / "models" / name
        for filename in [*manifest["files"], "model-manifest.json"]:
            relative = Path(filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid model file path")
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size != (source / relative).stat().st_size:
                shutil.copy2(source / relative, target)
        verify(dest)
        manifests[name] = {"repo": manifest["repo"], "revision": manifest["revision"]}
    (release / "models.json").write_text(json.dumps(manifests, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nsis", type=Path, required=True, help="Path to portable makensis.exe")
    parser.add_argument("--with-models", type=Path, nargs="?", const=ROOT / ".runtime/models",
                        help="Also stage an offline model pack (default source: .runtime/models)")
    parser.add_argument("--skip-app-build", action="store_true")
    args = parser.parse_args()
    dist = ROOT / ".runtime/packaged"
    release = ROOT / f".runtime/release/{APP_NAME}-{VERSION}"
    if release.exists():
        shutil.rmtree(release)
    release.mkdir(parents=True)
    icon = ROOT / ".runtime/package-build" / f"{APP_NAME}.ico"
    icon.parent.mkdir(parents=True, exist_ok=True)
    save_app_icon(icon)
    welcome = icon.parent / "welcome.bmp"
    save_welcome_bitmap(welcome)
    # The installed app has no git; it reads the commit from this file.
    (icon.parent / COMMIT_FILE).write_text(commit(), encoding="utf-8")
    if not args.skip_app_build:
        subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / f"packaging/{APP_NAME}.spec"),
                        "--distpath", str(dist), "--workpath", str(ROOT / ".runtime/package-build"),
                        "--noconfirm"], cwd=ROOT, check=True, env={**os.environ, "APP_ICON": str(icon)})
    if args.with_models:
        stage_models(args.with_models, release)
    subprocess.run([str(args.nsis.resolve()), "/V2", f"/DAPP_NAME={APP_NAME}", f"/DVERSION={VERSION}",
                    f"/DAPP_DIR={dist / APP_NAME}", f"/DRELEASE_DIR={release}", f"/DAPP_ICON={icon}",
                    f"/DWELCOME_BITMAP={welcome}", str(ROOT / "packaging/installer.nsi")], check=True)
    models_line = ("Keep the models folder beside the installer; setup copies those models in.\n"
                   if args.with_models else
                   "On first run, the welcome window downloads the speech and Polish models.\n")
    (release / "README.txt").write_text(
        f"Run {APP_NAME}-Setup.exe. Installs for the current user. Python and PowerShell are not required.\n"
        + models_line +
        "Start with Windows is optional and unchecked. It can also be changed in the tray menu.\n"
        f"Config, models and caches: %LOCALAPPDATA%\\{APP_NAME}\n"
        f"Application: %LOCALAPPDATA%\\Programs\\{APP_NAME}\n"
        "Updates and uninstall preserve your models and settings. Quit the tray app before either.\n"
        "This local preview is unsigned.\n",
        encoding="utf-8")
    print(f"Setup folder ready: {release}")


if __name__ == "__main__":
    main()
