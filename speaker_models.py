"""Speaker-separation models shipped with the app (meeting mode, #44).

Both files are pinned by repository revision, size and sha256. The build script
fetches them into .runtime/speaker-models; the installer bundles that folder as
speaker_models/, and a dev checkout reads .runtime/speaker-models directly.
"""
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEV_DIR = ROOT / ".runtime" / "speaker-models"
BUNDLE_NAME = "speaker_models"

# name -> (repo, revision, file in repo, size in bytes, sha256)
MODELS = {
    # pyannote segmentation 3.0, exported for sherpa-onnx. MIT license.
    "segmentation.onnx": (
        "csukuangfj/sherpa-onnx-pyannote-segmentation-3-0",
        "9403a6902bb58e3d5ae8c7e77c3422de279db2e0",
        "model.onnx",
        5_992_913,
        "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
    ),
    # NVIDIA NeMo TitaNet small speaker embeddings. CC-BY-4.0 (attributed in README).
    "embedding.onnx": (
        "csukuangfj/speaker-embedding-models",
        "a8055d6daef723a6a4ecf4de233708575bf4994f",
        "nemo_en_titanet_small.onnx",
        40_257_283,
        "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e",
    ),
}


def models_dir():
    """Folder that holds the speaker models in this build."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / BUNDLE_NAME
    return DEV_DIR


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def problem(directory=None, check_hash=False):
    """None when the models are present (and match, with check_hash), else a short reason."""
    directory = Path(directory or models_dir())
    for name, (_, _, _, size, sha256) in MODELS.items():
        path = directory / name
        if not path.is_file():
            return f"{name} is missing"
        if path.stat().st_size != size:
            return f"{name} has the wrong size"
        if check_hash and _sha256(path) != sha256:
            return f"{name} does not match its checksum"
    return None


def model_paths(directory=None):
    directory = Path(directory or models_dir())
    return directory / "segmentation.onnx", directory / "embedding.onnx"


def fetch(directory=DEV_DIR):
    """Download any missing or wrong model into directory and verify every file."""
    from huggingface_hub import hf_hub_download

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, (repo, revision, filename, size, sha256) in MODELS.items():
        target = directory / name
        if target.is_file() and target.stat().st_size == size and _sha256(target) == sha256:
            continue
        cached = Path(hf_hub_download(repo, filename, revision=revision))
        if cached.stat().st_size != size or _sha256(cached) != sha256:
            raise ValueError(f"{repo}/{filename} at {revision[:8]} does not match its pinned checksum")
        partial = target.with_suffix(".partial")
        shutil.copyfile(cached, partial)
        partial.replace(target)
    reason = problem(directory, check_hash=True)
    if reason:
        raise ValueError(reason)
    return directory


if __name__ == "__main__":
    print(f"Speaker models ready: {fetch()}")
