from PyInstaller.utils.hooks import collect_all
from pathlib import Path
import os
import sys

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from app_paths import APP_NAME
from version import COMMIT_FILE

datas, binaries, hiddenimports = [], [], ["pystray._win32", "_sounddevice_data"]
commit_file = root / ".runtime/package-build" / COMMIT_FILE
if commit_file.exists():
    datas.append((str(commit_file), "."))
for package in ("openvino", "openvino_genai", "openvino_tokenizers", "onnxruntime", "customtkinter", "_sounddevice_data", "winrt", "pyaudiowpatch", "sherpa_onnx"):
    data, binary, hidden = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += hidden
a = Analysis([str(root / "app.py")], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports,
             hookspath=[str(root / "packaging/hooks")],
             excludes=["pytest", "matplotlib", "IPython", "torch", "tensorflow"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME,
          console=False, upx=False, icon=os.environ.get("APP_ICON"))
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)
