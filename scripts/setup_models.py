"""Explicit online setup. Runtime loads these local files without hub calls."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dictation_engine import CLEANUP_REGISTRY, MODEL_DIR, MODEL_REGISTRY, load_config, save_config
from model_store import download
import speaker_models

CLEANUP_MODELS = {key: info["repo"] for key, info in CLEANUP_REGISTRY.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech", choices=MODEL_REGISTRY, default=None)
    parser.add_argument("--cleanup", choices=CLEANUP_REGISTRY, default=None)
    parser.add_argument("--configure", action="store_true", help="Save the chosen paths; cleanup remains opt-in")
    args = parser.parse_args()
    config = load_config()
    speech = args.speech or config["model_size"]
    info = MODEL_REGISTRY[speech]
    manifest = download(info["ov_repo"], MODEL_DIR / info["local_dir"], revision=info["revision"])
    print(f"Speech snapshot ready: {speech} @ {manifest['revision']}")
    print(f"Speaker models ready: {speaker_models.fetch()}")
    if args.cleanup:
        cleanup = CLEANUP_REGISTRY[args.cleanup]
        path = MODEL_DIR / cleanup["local_dir"]
        manifest = download(cleanup["repo"], path, revision=cleanup["revision"])
        print(f"Cleanup snapshot ready: {args.cleanup} @ {manifest['revision']}")
        config["cleanup_model_path"] = str(path)
    if args.configure:
        config["model_size"] = speech
        save_config(config)


if __name__ == "__main__":
    main()
