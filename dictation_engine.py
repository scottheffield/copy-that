"""
CopyThat dictation engine: local voice-to-text with OpenVINO on Intel hardware.
Parakeet TDT (OpenVINO + onnxruntime) runs on the GPU by default; Whisper
models (openvino_genai) prefer the NPU.

Usage: python dictation_engine.py [--setup] [--device NPU|GPU|CPU] [--model base|small|medium|turbo|parakeet]
"""

import sys
import os
import time
import json
import re
import argparse
import threading
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
from enum import Enum
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
from app_paths import data_directory
CONFIG_DIR = data_directory()
CONFIG_FILE = CONFIG_DIR / "config.json"
MODEL_DIR = CONFIG_DIR / "models"
LOG_FILE = CONFIG_DIR / "dictation.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # Then it moves to dictation.log.1: 10 MB at most
_log_lock = threading.Lock()
CACHE_DIR = CONFIG_DIR / "ov-cache"

DEFAULT_CONFIG = {
    "theme": "system",
    "highlight": "blue",      # Highlight color: blue, violet, teal or amber
    "device": "NPU",           # NPU, GPU, CPU
    "device_auto": True,       # Use the model's preferred device at launch; false keeps "device"
    "decoder_device": "GPU",   # Parakeet decoder: GPU or CPU
    "cleanup_device": "GPU",   # Polish model: GPU or CPU
    "model_size": "base",      # base, small, medium, turbo, parakeet (see MODEL_REGISTRY)
    "language": "en",          # Language code or "auto"
    "hotkey": "ctrl+win",      # Hold to record; Space locks; Escape cancels
    "auto_enter": False,       # Optional submission in the destination app
    "append_space": True,      # Separate consecutive takes without reading destination text
    "beep_on_start": True,     # Audio feedback when recording starts/stops
    "max_record_seconds": 300, # Max recording length
    "limit_warning_seconds": 15, # Cue before the recording limit ends a take
    "live_recognition": True,  # Recognize finished windows while still recording
    "show_live_text": True,    # Show provisional text at the caret (or under the pill)
    "probe_text_field": True,  # Find the focused field's caret; warn when there is none
    "pause_media": True,       # Pause playing media while recording, resume it after
    "keep_warm_seconds": 2,   # Accelerator warm-up interval while recording; 0 disables
    "sample_rate": 16000,      # Whisper expects 16kHz
    "vad_enabled": True,       # Reject non-speech before recognition; keep full audio
    "show_balloon": True,      # Show text balloon under notch after transcription
    "max_pending_takes": 3,
    "literal_mode": False,
    "dictionary": {},
    "spoken_commands": False,
    "cleanup_enabled": False,  # Enable after local evaluation; rules work without it
    "cleanup_model_path": str(CONFIG_DIR / "models" / "Qwen3-4B-int4-ov"),
    "cleanup_min_words": 8,
    "cleanup_gate": True,      # Run the model only for stutters, corrections, fillers
    "cleanup_overlap": 0.7,
    "cleanup_timeout_seconds": 5,
    "polish_context": True,    # Names and terms from the window title and earlier takes
    "screen_context": True,    # Also names visible in the window, read while recording
    "issue_keys": [],          # e.g. ["OPS"]: "OPS twenty three" -> OPS-23
    "take_log_enabled": False,
    "take_log_keep": 200,      # Newest takes kept on disk; 0 keeps them all
    "take_log_dir": str(Path.home() / ".copythat-takes"),
    "paste_wait_seconds": 15,
    "input_device": None,
    "dictation_priority": "above_normal",  # normal, above_normal, high: only from hotkey to paste
    "bubble_position": "cursor",     # cursor: open takes at the caret when known; top: the pill's spot
    # Meeting recordings: one folder per meeting. Kept out of synced folders by default.
    "meetings_dir": str(Path.home() / "CopyThat Meetings"),
    "meeting_skip_dictation": True,  # Leave dictation takes out of the meeting's mic track
    "meeting_speakers": True,        # Label Speaker 1..N in meeting transcripts (#44)
    "meeting_compress_audio": True,  # Replace finished meetings' WAVs with Opus; off keeps the WAVs (#61)
    "meeting_my_name": "",           # The name last given to Me; new meetings use it (#44)
    "meeting_chip_position": None,   # [right, top] in screen px once the chip is dragged
    "meeting_consent_seen": False,   # The consent note shows under the chip until dismissed
    "main_window_page": "Home",      # The last page used; a plain open lands on Home, Settings reopens its last tab
    "usage_stats": True,             # Count words and speaking time for Home; numbers only, in stats.json
}

# Model registry: pre-exported models from Hugging Face, pinned to tested revisions.
MODEL_REGISTRY = {
    "base": {
        "name": "Whisper Base",
        "summary": "Many languages. Tiny and quick, but makes the most mistakes.",
        "repo": "openai/whisper-base",
        "ov_repo": "OpenVINO/whisper-base-int8-ov",
        "revision": "0606293f0511136ada21755a265492f623a934b8",
        "size_gb": 0.08,
        "description": "74M parameters. Initial lightweight multilingual model.",
        "preferred_device": "NPU",
        "backend": "whisper",
        "local_dir": "whisper-base-openvino",
        "languages": "all",
    },
    "small": {
        "name": "Whisper Small",
        "summary": "Many languages. More accurate than Base and still light.",
        "repo": "openai/whisper-small",
        "ov_repo": "OpenVINO/whisper-small-int8-ov",
        "revision": "5b831719e093f86e1970be663e524fe001489f9b",
        "size_gb": 0.26,
        "description": "244M parameters. Multilingual candidate for local evaluation.",
        "preferred_device": "NPU",
        "backend": "whisper",
        "local_dir": "whisper-small-openvino",
        "languages": "all",
    },
    "medium": {
        "name": "Whisper Medium",
        "summary": "Many languages. Accurate, but noticeably slower.",
        "repo": "openai/whisper-medium",
        "ov_repo": "OpenVINO/whisper-medium-int8-ov",
        "revision": "8d43cce846729381f56bd45a1c70925cee2222ff",
        "size_gb": 0.78,
        "description": "769M parameters. Larger multilingual candidate, GPU preferred.",
        "preferred_device": "GPU",
        "backend": "whisper",
        "local_dir": "whisper-medium-openvino",
        "languages": "all",
    },
    "turbo": {
        "name": "Whisper Turbo",
        "summary": "Many languages. The most accurate Whisper choice.",
        "repo": "openai/whisper-large-v3-turbo",
        "ov_repo": "FluidInference/whisper-large-v3-turbo-int4-ov-npu",
        "revision": "2e8fe5d17cf32c8b997941667285bfec0cc69d4a",
        "size_gb": 0.59,
        "description": "809M parameters. Multilingual turbo candidate, GPU preferred.",
        "preferred_device": "GPU",
        "backend": "whisper",
        "local_dir": "whisper-turbo-openvino",
        "languages": "all",
    },
    "parakeet": {
        "name": "Parakeet",
        "summary": "English only. Fastest and most accurate. Recommended.",
        "repo": "nvidia/parakeet-tdt-0.6b-v3",
        "ov_repo": "goodsmileduck/parakeet-tdt-0.6b-v3-onnx",
        "revision": "cd3de0d7a01b8981c51ce17a4667a2177f6e09d6",
        "size_gb": 2.55,
        "description": "600M parameters. English candidate with a hybrid local pipeline.",
        # Measured: the encoder takes ~50ms on NPU or GPU, but the
        # all-GPU pipeline finished 1.7-1.9x sooner because the GPU stays clocked up.
        "preferred_device": "GPU",
        "backend": "parakeet",
        "local_dir": "parakeet-tdt-openvino",
        "languages": ["en"],
    },
}

# Optional AI cleanup models ("Polish" in the UI), also pinned. Rules-only cleanup needs no model.
CLEANUP_REGISTRY = {
    "4b": {
        "name": "Standard",
        "model": "Qwen3 4B",
        "summary": "Recommended. Adds a moment on longer takes. Uses Qwen3 4B.",
        "repo": "OpenVINO/Qwen3-4B-int4-ov",
        "revision": "b467368d16b75df14055562fe927ae8e1f15f7ef",
        "size_gb": 2.29,
        "local_dir": "Qwen3-4B-int4-ov",
    },
    "8b": {
        "name": "Thorough",
        "model": "Qwen3 8B",
        "summary": "Catches a little more, but slower than Standard. Uses Qwen3 8B.",
        "repo": "OpenVINO/Qwen3-8B-int4-ov",
        "revision": "5c47abf4b8e12ebe8e99745bb0c1ec17e0c0abcc",
        "size_gb": 4.88,
        "local_dir": "Qwen3-8B-int4-ov",
    },
    "1.7b": {
        "name": "Light",
        "model": "Qwen3 1.7B",
        "summary": "Smallest and quickest, but misses more. Uses Qwen3 1.7B.",
        "repo": "OpenVINO/Qwen3-1.7B-int4-ov",
        "revision": "6f32d81e9deebeca30bb5490a7176cf4fa8c79e3",
        "size_gb": 1.21,
        "local_dir": "Qwen3-1.7B-int4-ov",
    },
}

# Supported languages (Whisper's top languages + display names)
LANGUAGES = {
    "en": "English",
    "ru": "Russian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "pt": "Portuguese",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
    "ar": "Arabic",
    "uk": "Ukrainian",
}


def get_models_for_language(lang: str) -> dict:
    """Return subset of MODEL_REGISTRY compatible with the given language."""
    return {k: v for k, v in MODEL_REGISTRY.items()
            if v["languages"] == "all" or lang in v["languages"]}


def is_model_downloaded(model_key: str) -> bool:
    """A speech model is installed once its download manifest exists."""
    from model_store import installed
    return installed(MODEL_DIR / MODEL_REGISTRY[model_key]["local_dir"])


def is_cleanup_downloaded(cleanup_key: str) -> bool:
    from model_store import installed
    return installed(MODEL_DIR / CLEANUP_REGISTRY[cleanup_key]["local_dir"])


# ---------------------------------------------------------------------------
# Application state machine
# ---------------------------------------------------------------------------
class AppState(Enum):
    LOADING = "loading"        # Model loading in progress
    READY = "ready"            # Idle, waiting for hotkey
    RECORDING = "recording"    # Microphone active
    PROCESSING = "processing"  # Transcribing audio
    ERROR = "error"            # Device lost or load failed


def _rotate_log():
    """Keep one older file, so the log can't grow without limit."""
    try:
        if LOG_FILE.stat().st_size >= LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE.with_name(LOG_FILE.name + ".1"))
    except OSError:
        pass  # No log yet, or Windows has it open: still write the line.


def log(msg: str):
    """Simple logging to file and console."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with _log_lock:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _rotate_log()
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Config management
# ---------------------------------------------------------------------------
def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        config = {**DEFAULT_CONFIG, **saved}
        if saved.get("hotkey") in ("ctrl+space", "ctrl+alt+d"):
            config["hotkey"] = "ctrl+win"
    else:
        config = DEFAULT_CONFIG.copy()
    validate_config(config)
    return config


def save_config(config: dict):
    validate_config(config)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    log(f"Config saved to {CONFIG_FILE}")


def validate_settings(config: dict):
    """Checks for applying Settings. Startup skips them, so one bad path can't
    stop dictation: Polish falls back to rules, and the take log shows a notice."""
    validate_config(config)
    if len(config.get("dictionary", {})) > 500:
        raise ValueError("dictionary cannot exceed 500 entries")
    if config.get("cleanup_enabled"):
        from model_store import polish_path_allowed
        polish_path_allowed(config["cleanup_model_path"], MODEL_DIR)
    if config.get("take_log_enabled"):
        from take_log import private_directory
        private_directory(config.get("take_log_dir", DEFAULT_CONFIG["take_log_dir"]))


def validate_config(config: dict):
    """Validate config values. Raises ValueError on invalid values."""
    if config.get("theme", "system") not in ("system", "light", "dark"):
        raise ValueError("theme must be system, light or dark")
    if config.get("highlight", "blue") not in ("blue", "violet", "teal", "amber"):
        raise ValueError("highlight must be blue, violet, teal or amber")
    valid_devices = {"NPU", "GPU", "CPU"}
    if config.get("device") not in valid_devices:
        raise ValueError(f"device must be one of {valid_devices}, got '{config.get('device')}'")
    for key in ("decoder_device", "cleanup_device"):
        if config.get(key, "GPU") not in {"GPU", "CPU"}:
            raise ValueError(f"{key} must be GPU or CPU, got '{config.get(key)}'")

    valid_models = set(MODEL_REGISTRY.keys())
    if config.get("model_size") not in valid_models:
        raise ValueError(f"model_size must be one of {valid_models}, got '{config.get('model_size')}'")

    sr = config.get("sample_rate")
    if sr != 16000:
        raise ValueError("sample_rate must be 16000 for the speech models")

    max_rec = config.get("max_record_seconds")
    if max_rec is not None and (not isinstance(max_rec, (int, float)) or max_rec <= 0):
        raise ValueError(f"max_record_seconds must be a positive number or null, got '{max_rec}'")
    if config.get("hotkey", "ctrl+win").lower().replace(" ", "") != "ctrl+win":
        raise ValueError("hotkey must be ctrl+win for the hold-to-talk gesture")
    pending = config.get("max_pending_takes", 3)
    if not isinstance(pending, int) or isinstance(pending, bool) or not 1 <= pending <= 3:
        raise ValueError("max_pending_takes must be between 1 and 3")
    keep = config.get("take_log_keep", 200)
    if not isinstance(keep, int) or isinstance(keep, bool) or keep < 0:
        raise ValueError("take_log_keep must be 0 or more")
    dictionary = config.get("dictionary", {})
    if not isinstance(dictionary, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in dictionary.items()):
        raise ValueError("dictionary must map nonempty phrases to text")
    if not 0 <= config.get("cleanup_overlap", 0.7) <= 1:
        raise ValueError("cleanup_overlap must be between 0 and 1")
    if config.get("cleanup_min_words", 8) < 0 or config.get("cleanup_timeout_seconds", 5) <= 0:
        raise ValueError("cleanup thresholds must be positive")
    if config.get("dictation_priority", "above_normal") not in ("normal", "above_normal", "high"):
        raise ValueError("dictation_priority must be normal, above_normal or high")
    if config.get("bubble_position", "cursor") not in ("cursor", "top"):
        raise ValueError("bubble_position must be cursor or top")


# ---------------------------------------------------------------------------
# Legacy model discovery and explicit model setup
# ---------------------------------------------------------------------------
def setup_model(config: dict, **_):
    """Return the installed speech model folder. Downloads happen only from the UI or setup."""
    if not is_model_downloaded(config["model_size"]):
        raise FileNotFoundError("Speech model is not downloaded. Download it in Settings.")
    return MODEL_DIR / MODEL_REGISTRY[config["model_size"]]["local_dir"]


# ---------------------------------------------------------------------------
# Whisper pipeline using OpenVINO GenAI
# ---------------------------------------------------------------------------
class WhisperModel:
    """Whisper speech-to-text using OpenVINO on NPU/GPU/CPU."""

    def __init__(self, model_path: Path, device: str = "NPU"):
        self.model_path = model_path
        self.device = device
        self.pipeline = None
        self._load_pipeline()

    def _load_pipeline(self):
        """Load the OpenVINO Whisper pipeline."""
        # Warn about large models on NPU — they may trigger driver instability
        model_name = self.model_path.name.lower()
        if self.device == "NPU" and ("turbo" in model_name or "large" in model_name or "medium" in model_name):
            log(f"NOTE: Large models on NPU may cause driver instability (DEVICE_LOST).")
            log(f"  If inference fails, try rebooting to reset the NPU, or use --device GPU")

        from ov_devices import resolve_device
        self.device = resolve_device(self.device, log=log)
        log(f"Loading Whisper pipeline on {self.device}...")
        start = time.time()

        try:
            import openvino_genai as ov_genai
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self.pipeline = ov_genai.WhisperPipeline(
                str(self.model_path), self.device,
                CACHE_DIR=str(CACHE_DIR)
            )
            log(f"Loaded via openvino_genai in {time.time() - start:.1f}s")
        except Exception as e:
            log(f"Failed to load model: {e}")
            log("Falling back to CPU...")
            if self.device != "CPU":
                self.device = "CPU"
                self._load_pipeline()
            else:
                raise

    def transcribe(self, audio_data, sample_rate: int = 16000, language: str = "en") -> str:
        """Transcribe audio numpy array to text."""
        import numpy as np
        start = time.time()

        # Ensure float32 normalized to [-1, 1]
        if audio_data.dtype == np.int16:
            audio_data = audio_data.astype(np.float32) / 32768.0
        elif audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)

        config = self.pipeline.get_generation_config()
        config.max_new_tokens = 448
        if language and language != "auto":
            config.language = f"<|{language}|>"
        config.task = "transcribe"
        config.return_timestamps = False

        try:
            result = self.pipeline.generate(audio_data, config)
        except RuntimeError as e:
            if "DEVICE_LOST" in str(e) or "device hung" in str(e):
                log(f"NPU DEVICE_LOST — the NPU driver crashed or is in a bad state.")
                log(f"  A reboot will reset the NPU. Or use --device GPU to bypass it.")
                raise RuntimeError(
                    f"NPU device lost. Reboot to reset the NPU, "
                    f"or use --device GPU."
                ) from e
            raise
        text = str(result).strip()

        elapsed = time.time() - start
        audio_duration = len(audio_data) / sample_rate
        rtf = elapsed / audio_duration if audio_duration > 0 else 0
        log(f"Transcribed {audio_duration:.1f}s audio in {elapsed:.1f}s (RTF: {rtf:.2f}) on {self.device}")

        return text

    def keep_warm(self):
        """No-op: only the Parakeet pipeline has been measured to benefit."""


# ---------------------------------------------------------------------------
# Parakeet TDT pipeline using OpenVINO (all on the GPU by default)
# ---------------------------------------------------------------------------
class ParakeetModel:
    """Parakeet TDT 0.6B speech-to-text using OpenVINO.

    Architecture:
        nemo128.onnx (onnxruntime CPU) → mel spectrogram
        encoder-model.onnx (OpenVINO; GPU by default, NPU or CPU allowed) → encoder features
        decoder_joint-model.onnx (OpenVINO GPU, or CPU) → TDT greedy decode
    """

    supports_streaming = True
    # TDT constants
    # Compiled models share infer requests; warm-up must not overlap a take.
    _infer_lock = threading.Lock()
    BLANK_IDX = 8192
    VOCAB_SIZE = 8193  # 0-8192 are vocab tokens, 8193+ are duration tokens
    MAX_TOKENS_PER_STEP = 10
    MEL_FRAMES = 1600  # static shape for encoder — covers ~16s audio
    ENC_DIM = 1024
    LSTM_DIM = 640
    DECODE_SPACE = re.compile(r"\A\s|\s\B|(\s)\b")

    def __init__(self, model_path: Path, device: str = "NPU", decoder_device: str = "GPU"):
        self.model_path = model_path
        self.device = device
        self.vocab: dict[int, str] = {}
        self.preproc = None  # onnxruntime session
        self.enc_compiled = None  # OpenVINO compiled encoder
        self.dec_compiled = None  # OpenVINO compiled decoder
        self.decoder_device = decoder_device
        self.enc_time_dim = 0  # encoder output time dimension (computed at load)
        self._load_pipeline()

    def _load_vocab(self):
        """Load vocab.txt from model directory."""
        vocab_path = self.model_path / "vocab.txt"
        if not vocab_path.exists():
            raise FileNotFoundError(f"vocab.txt not found at {vocab_path}")
        with open(vocab_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip("\n").split(" ")
                if len(parts) == 2:
                    token, idx = parts[0], int(parts[1])
                    self.vocab[idx] = token.replace("\u2581", " ")
        log(f"Parakeet vocab loaded: {len(self.vocab)} tokens")

    def _load_pipeline(self):
        """Load preprocessor, encoder, and decoder."""
        log(f"Loading Parakeet TDT pipeline (encoder on {self.device}, decoder on {self.decoder_device})...")
        start = time.time()

        # 1. Load vocabulary
        self._load_vocab()

        # 2. Load mel preprocessor (onnxruntime)
        preproc_path = self.model_path / "nemo128.onnx"
        if not preproc_path.exists():
            raise FileNotFoundError(
                f"nemo128.onnx not found at {preproc_path}. "
                f"Re-run setup: python dictation_engine.py --model parakeet --setup"
            )
        try:
            import onnxruntime as ort
            self.preproc = ort.InferenceSession(
                str(preproc_path), providers=["CPUExecutionProvider"]
            )
        except ImportError:
            raise ImportError(
                "onnxruntime is required for Parakeet models. "
                "Install with: pip install onnxruntime"
            )
        log(f"  Preprocessor loaded from {preproc_path}")

        # 3. Load encoder on NPU/GPU via OpenVINO
        try:
            import openvino as ov
            from ov_devices import resolve_device
            core = ov.Core()
            self.device = resolve_device(self.device, core, log)
            self.decoder_device = resolve_device(self.decoder_device, core, log)

            encoder_path = self.model_path / "encoder-model.onnx"
            encoder_model = core.read_model(str(encoder_path))
            encoder_model.reshape({
                "audio_signal": [1, 128, self.MEL_FRAMES],
                "length": [1],
            })

            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                self.enc_compiled = core.compile_model(
                    encoder_model, self.device, {"CACHE_DIR": str(CACHE_DIR)}
                )
                log(f"  Encoder compiled on {self.device}")
            except Exception as e:
                if self.device != "CPU":
                    fallback = resolve_device("GPU", core, log) if self.device == "NPU" else "CPU"
                    log(f"  Encoder failed on {self.device}: {e}")
                    log(f"  Falling back to {fallback}...")
                    try:
                        self.enc_compiled = core.compile_model(
                            encoder_model, fallback, {"CACHE_DIR": str(CACHE_DIR)}
                        )
                        self.device = fallback
                        log(f"  Encoder compiled on {fallback}")
                    except Exception:
                        log(f"  Falling back to CPU...")
                        self.enc_compiled = core.compile_model(encoder_model, "CPU")
                        self.device = "CPU"
                else:
                    raise

            # Determine encoder output time dimension via dummy inference
            import numpy as np
            dummy_mel = np.zeros((1, 128, self.MEL_FRAMES), dtype=np.float32)
            dummy_len = np.array([self.MEL_FRAMES], dtype=np.int64)
            dummy_out = self.enc_compiled({"audio_signal": dummy_mel, "length": dummy_len})
            self.enc_time_dim = dummy_out["outputs"].shape[2]
            log(f"  Encoder output: [1, {self.ENC_DIM}, {self.enc_time_dim}]")

            # 4. Load decoder on the decoder device (GPU is 1.8x faster than CPU for the sequential loop)
            decoder_path = self.model_path / "decoder_joint-model.onnx"
            decoder_model = core.read_model(str(decoder_path))
            decoder_model.reshape({
                "encoder_outputs": [1, self.ENC_DIM, 1],
                "targets": [1, 1],
                "target_length": [1],
                "input_states_1": [2, 1, self.LSTM_DIM],
                "input_states_2": [2, 1, self.LSTM_DIM],
            })
            try:
                self.dec_compiled = core.compile_model(
                    decoder_model, self.decoder_device, {"CACHE_DIR": str(CACHE_DIR)}
                )
                log(f"  Decoder compiled on {self.decoder_device}")
            except Exception as e:
                log(f"  Decoder failed on {self.decoder_device}: {e}, falling back to CPU")
                self.dec_compiled = core.compile_model(decoder_model, "CPU")
                self.decoder_device = "CPU"
                log(f"  Decoder compiled on CPU")

        except Exception as e:
            log(f"Failed to load Parakeet pipeline: {e}")
            raise

        log(f"Parakeet pipeline loaded in {time.time() - start:.1f}s")

    def _preprocess(self, audio_data) -> tuple:
        """Convert raw audio to mel features using nemo128.onnx."""
        import numpy as np
        waveform = audio_data.reshape(1, -1).astype(np.float32)
        waveform_lens = np.array([waveform.shape[1]], dtype=np.int64)
        features, features_lens = self.preproc.run(
            ["features", "features_lens"],
            {"waveforms": waveform, "waveforms_lens": waveform_lens},
        )
        return features, features_lens

    def _tdt_greedy_decode(self, enc_out, enc_len: int, timed=False, cancelled=None):
        """TDT greedy decoding loop."""
        import numpy as np
        from audio_chunks import Cancelled

        states_1 = np.zeros((2, 1, self.LSTM_DIM), dtype=np.float32)
        states_2 = np.zeros((2, 1, self.LSTM_DIM), dtype=np.float32)
        tokens = []
        token_times = []
        t = 0
        emitted_at_frame = 0

        while t < enc_len:
            # A live preview yields the devices as soon as the take is released.
            if cancelled is not None and cancelled():
                raise Cancelled()
            frame = enc_out[:, :, t:t+1].astype(np.float32)
            prev_token = tokens[-1] if tokens else self.BLANK_IDX
            target = np.array([[prev_token]], dtype=np.int32)
            target_len = np.array([1], dtype=np.int32)

            result = self.dec_compiled({
                "encoder_outputs": frame,
                "targets": target,
                "target_length": target_len,
                "input_states_1": states_1,
                "input_states_2": states_2,
            })

            output = result["outputs"].squeeze()
            new_states_1 = result["output_states_1"]
            new_states_2 = result["output_states_2"]

            vocab_logits = output[:self.VOCAB_SIZE]
            duration_logits = output[self.VOCAB_SIZE:]
            token_id = int(vocab_logits.argmax())
            duration = int(duration_logits.argmax())

            if token_id != self.BLANK_IDX:
                states_1 = new_states_1
                states_2 = new_states_2
                tokens.append(token_id)
                token_times.append(t * 0.08)  # 10ms mel frames, 8x encoder subsampling
                emitted_at_frame += 1

            if duration > 0:
                t += duration
                emitted_at_frame = 0
            elif token_id == self.BLANK_IDX or emitted_at_frame >= self.MAX_TOKENS_PER_STEP:
                t += 1
                emitted_at_frame = 0

        pieces = [self.vocab.get(token, "?") for token in tokens]
        text = "".join(pieces)
        char_times = [timestamp for piece, timestamp in zip(pieces, token_times) for _ in piece]
        # Keep the same detokenization while retaining the character-to-audio map.
        parts, times, last = [], [], 0
        for match in self.DECODE_SPACE.finditer(text):
            parts.append(text[last:match.start()])
            times.extend(char_times[last:match.start()])
            if match.group(1):
                parts.append(" ")
                times.append(char_times[match.start()])
            last = match.end()
        parts.append(text[last:])
        times.extend(char_times[last:])
        text = "".join(parts)
        if timed:
            from audio_chunks import TimedWord
            return [TimedWord(match.group(), times[match.start()])
                    for match in re.finditer(r"\S+", text)]
        return text.strip()

    def keep_warm(self):
        """Short encoder and decoder pass on silence.

        Idle accelerators clock down within seconds; in testing, a
        pass every 2s while recording cut the first window from ~0.71s to
        ~0.42s. Skipped whenever a real transcription holds the devices.
        """
        import numpy as np
        if not self._infer_lock.acquire(blocking=False):
            return
        try:
            mel, mel_lens = self._preprocess(np.zeros(16000, dtype=np.float32))
            padded = np.zeros((1, 128, self.MEL_FRAMES), dtype=np.float32)
            padded[:, :, :mel.shape[2]] = mel
            result = self.enc_compiled({"audio_signal": padded, "length": mel_lens.astype(np.int64)})
            self._tdt_greedy_decode(result["outputs"], min(5, int(result["encoded_lengths"][0])))
        finally:
            self._infer_lock.release()

    def transcribe(self, audio_data, sample_rate: int = 16000, language: str = "en") -> str:
        with self._infer_lock:
            return self._transcribe_locked(audio_data, sample_rate, language)

    def _transcribe_locked(self, audio_data, sample_rate, language):
        transcript = self.windowed_transcript(sample_rate, language)
        transcript.advance(audio_data, final=True)
        return transcript.text

    @property
    def infer_lock(self):
        return self._infer_lock

    def windowed_transcript(self, sample_rate=16000, language="en", cancelled=None):
        """Incremental recognizer for a growing recording; callers hold infer_lock."""
        from audio_chunks import WindowedTranscript, TimedWord
        def window(samples, offset):
            words = self._transcribe_window(samples, sample_rate, language, timed=True, cancelled=cancelled)
            return [TimedWord(word.text, word.time + offset) for word in words]
        return WindowedTranscript(window, sample_rate)

    def _transcribe_window(self, audio_data, sample_rate: int = 16000, language: str = "en", timed=False,
                           cancelled=None):
        """Transcribe audio numpy array to text.

        Same interface as WhisperModel.transcribe() for drop-in compatibility.
        Note: Parakeet is English-only; language parameter is accepted but ignored.
        """
        import numpy as np
        start = time.time()

        # Ensure float32 normalized to [-1, 1]
        if audio_data.dtype == np.int16:
            audio_data = audio_data.astype(np.float32) / 32768.0
        elif audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)

        # 1. Mel spectrogram
        mel, mel_lens = self._preprocess(audio_data)
        mel_done = time.time()
        actual_frames = mel.shape[2]

        # Windows are bounded before preprocessing; never silently truncate.
        if actual_frames < self.MEL_FRAMES:
            mel_padded = np.zeros((1, 128, self.MEL_FRAMES), dtype=np.float32)
            mel_padded[:, :, :actual_frames] = mel
            mel = mel_padded
        elif actual_frames > self.MEL_FRAMES:
            raise ValueError("Parakeet window exceeds its static encoder shape")

        # 2. Encoder (NPU/GPU)
        enc_result = self.enc_compiled({
            "audio_signal": mel,
            "length": mel_lens.astype(np.int64),
        })
        enc_out = enc_result["outputs"]
        enc_len = int(enc_result["encoded_lengths"][0])
        enc_done = time.time()

        # 3. TDT decoder on its actual compiled device.
        text = self._tdt_greedy_decode(enc_out, enc_len, timed=timed, cancelled=cancelled)

        end = time.time()
        elapsed = end - start
        audio_duration = len(audio_data) / sample_rate
        rtf = elapsed / audio_duration if audio_duration > 0 else 0
        # Stage split shows where live passes lose time against the release pass.
        log(f"Transcribed {audio_duration:.1f}s audio in {elapsed:.1f}s "
            f"(RTF: {rtf:.2f}) on {self.device}+{self.decoder_device} [Parakeet] "
            f"mel={mel_done - start:.3f}s enc={enc_done - mel_done:.3f}s dec={end - enc_done:.3f}s")

        return text


def launch_device(config: dict) -> str:
    """The device for this launch: the model's preferred device while
    device_auto is on, otherwise the device the user saved."""
    if config.get("device_auto", True):
        return MODEL_REGISTRY[config["model_size"]]["preferred_device"]
    return config["device"]


def create_model(model_path: Path, device: str, backend: str, **parakeet_options):
    """Factory function to create the right model class based on backend.

    Args:
        model_path: Path to model directory.
        device: Device string (NPU, GPU, CPU).
        backend: "whisper" or "parakeet" from MODEL_REGISTRY.
        parakeet_options: Extra Parakeet settings, such as decoder_device. Whisper ignores them.

    Returns:
        WhisperModel or ParakeetModel instance.
    """
    if backend == "parakeet":
        return ParakeetModel(model_path, device=device, **parakeet_options)
    return WhisperModel(model_path, device=device)


# ---------------------------------------------------------------------------
# Audio recording
# ---------------------------------------------------------------------------
_MAPPERS = {}


def sound_mapper(kind: str):
    """Index of the MME "Microsoft Sound Mapper - Input/Output" device.

    PortAudio fixes its default devices when it initializes, so a long-running
    app kept using whatever Windows default existed at launch. The mapper
    follows the current Windows default instead. None when unavailable.
    """
    if kind not in _MAPPERS:
        try:
            import sounddevice as sd
            name = f"Microsoft Sound Mapper - {kind}"
            _MAPPERS[kind] = next((i for i, d in enumerate(sd.query_devices()) if d["name"] == name), None)
        except Exception:
            _MAPPERS[kind] = None
    return _MAPPERS[kind]


def _wasapi_convert(sd, device):
    """WASAPI shared mode only opens at the mic's native rate unless PortAudio converts."""
    try:
        if device is not None and sd.query_hostapis(sd.query_devices(device)["hostapi"])["name"] == "Windows WASAPI":
            return sd.WasapiSettings(auto_convert=True)
    except Exception:
        pass
    return None


class AudioRecorder:
    """Record audio from microphone using sounddevice."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1,
                 max_record_seconds: float = None, input_device=None, on_timeout=None,
                 on_warning=None, warning_seconds: float = 15, on_silence=None,
                 silence_seconds: float = 4.0):
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_record_seconds = max_record_seconds
        self.recording = False
        self._frames = []
        self._stream = None
        self._lock = threading.Lock()
        self._timer = None
        self._warning_timer = None
        self._closing = None
        # Set when the device delivers non-zero audio; USB speakerphones send
        # ~0.3s of exact zeros while waking, during which speech is lost.
        self.live = threading.Event()
        self.input_device = input_device
        self.on_timeout = on_timeout
        self.on_warning = on_warning
        self.warning_seconds = warning_seconds
        # on_silence(True) once the mic, after waking, sends digital silence (peaks of
        # SILENT_PEAK or less) for silence_seconds mid-take; on_silence(False) when sound
        # returns. A dropped device sends exact zeros; the EMEET's mute button sends
        # 1-LSB dither, and Windows isn't told it is muted. Runs on the audio thread.
        self.on_silence = on_silence
        self.silence_seconds = silence_seconds
        self._zero_run = 0
        self._silent = False
        self.silent_at = None  # Seconds into the take when the mic went silent, or None

    def start(self):
        """Start recording."""
        import sounddevice as sd

        # PortAudio must not open a stream while the previous one is closing.
        if self._closing:
            self._closing.join()
            self._closing = None

        with self._lock:
            self._frames = []
            self.recording = True
        self.live.clear()
        self._zero_run, self._silent, self.silent_at = 0, False, None
        recorded = [0]

        def callback(indata, frames, time_info, status):
            with self._lock:
                if self.recording:
                    self._frames.append(indata.copy())
            recorded[0] += frames
            if not self.live.is_set():
                if indata.any():
                    self.live.set()
                return  # Zeros while the device wakes are not a dropout
            self._watch_silence(float(abs(indata).max()) > self.SILENT_PEAK, frames, recorded[0])

        device = self.input_device if self.input_device is not None else sound_mapper("Input")
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                blocksize=1024,
                callback=callback,
                device=device,
                extra_settings=_wasapi_convert(sd, device),
            )
            self._stream.start()
        except Exception:
            self.recording = False
            if self._stream:
                self._stream.close()
            self._stream = None
            raise
        log("Recording started...")

        # Enforce max recording duration
        if self.max_record_seconds:
            self._timer = threading.Timer(self.max_record_seconds, self.on_timeout or self._timeout_stop)
            self._timer.daemon = True
            self._timer.start()
            lead = self.max_record_seconds - self.warning_seconds
            if self.on_warning and lead > 0:
                self._warning_timer = threading.Timer(lead, self.on_warning)
                self._warning_timer.daemon = True
                self._warning_timer.start()

    SILENT_PEAK = 2 / 32768  # At most 2 LSB: digital silence, not a quiet room

    def _watch_silence(self, sound, frames, recorded):
        if sound:
            self._zero_run = 0
            if self._silent:
                self._silent = False
                self._notify_silence(False)
            return
        self._zero_run += frames
        if not self._silent and self._zero_run >= self.silence_seconds * self.sample_rate:
            self._silent = True
            self.silent_at = round((recorded - self._zero_run) / self.sample_rate, 1)
            self._notify_silence(True)

    def _notify_silence(self, silent):
        if self.on_silence:
            try:
                self.on_silence(silent)
            except Exception:
                pass

    def _timeout_stop(self):
        """Called when max_record_seconds is reached."""
        if self.recording:
            log(f"Max recording time ({self.max_record_seconds}s) reached, stopping.")
            # Standalone recorder preserves frames for the caller to retrieve.
            with self._lock:
                self.recording = False
            if self._stream:
                self._stream.stop()

    def stop(self):
        """Stop recording and return audio as numpy array."""
        import numpy as np

        for timer in (self._timer, self._warning_timer):
            if timer:
                timer.cancel()
        self._timer = self._warning_timer = None

        stream, self._stream = self._stream, None
        if stream:
            # Stopping first keeps the final buffered block. Stopping is fast;
            # closing the device takes ~0.7s, so it happens off this path.
            stream.stop()

        with self._lock:
            self.recording = False
            frames = list(self._frames)
            self._frames = []

        if stream:
            self._closing = threading.Thread(target=stream.close, name="mic-close", daemon=True)
            self._closing.start()

        if not frames:
            return np.array([], dtype=np.float32)

        audio = np.concatenate(frames, axis=0).flatten()
        duration = len(audio) / self.sample_rate
        log(f"Recording stopped. Duration: {duration:.1f}s")
        return audio

    def snapshot(self):
        """Audio recorded so far in this take, without stopping."""
        import numpy as np
        with self._lock:
            frames = list(self._frames) if self.recording else []
        if not frames:
            return np.array([], dtype=np.float32)
        return np.concatenate(frames, axis=0).flatten()

    @property
    def audio_level(self) -> float:
        """Return current RMS audio level normalized to 0.0-1.0."""
        import numpy as np
        with self._lock:
            if not self._frames or not self.recording:
                return 0.0
            last_frame = self._frames[-1].copy()
        rms = float(np.sqrt(np.mean(last_frame ** 2)))
        # Scale aggressively — typical speech RMS is 0.01-0.05
        return min(rms * 25.0, 1.0)


# ---------------------------------------------------------------------------
# Text output (type into active window)
# ---------------------------------------------------------------------------
def type_text(text: str, auto_enter: bool = False, guard=lambda: True, commit=lambda: True):
    """Paste at the checked destination without writing dictated text to logs."""
    from windows_io import paste_text
    return paste_text(text, auto_enter, guard, commit)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Audio feedback (chimes)
# ---------------------------------------------------------------------------
def _generate_tone(frequencies: list[float], duration_ms: int = 120,
                   sample_rate: int = 44100, volume: float = 0.3) -> bytes:
    """Generate a smooth WAV tone in memory.

    Args:
        frequencies: List of Hz values. Multiple = chord; sequential list of
            single-element lists for arpeggiated notes.
        duration_ms: Total duration in milliseconds.
        sample_rate: Audio sample rate.
        volume: Peak amplitude 0.0-1.0.
    """
    import struct, io, wave
    import numpy as np

    n_samples = int(sample_rate * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, n_samples, endpoint=False)

    # Sum sine waves for chord
    signal = np.zeros(n_samples, dtype=np.float64)
    for freq in frequencies:
        signal += np.sin(2 * np.pi * freq * t)
    if frequencies:
        signal /= len(frequencies)

    # Smooth envelope: 10ms attack, 40ms decay at end
    attack = min(int(0.010 * sample_rate), n_samples)
    decay = min(int(0.040 * sample_rate), n_samples)
    envelope = np.ones(n_samples)
    envelope[:attack] = np.linspace(0, 1, attack)
    envelope[-decay:] = np.linspace(1, 0, decay)

    signal = (signal * envelope * volume * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(signal.tobytes())
    return buf.getvalue()


def _pluck_samples(frequency: float, duration_ms: int = 150, decay_ms: float = 30,
                   sample_rate: int = 44100, volume: float = 0.15):
    """Soft pluck as float32 samples: instant attack, exponential decay.

    Most energy lands in the first ~1.15 * decay_ms, so the cue reads as a
    tick rather than a held note.
    """
    import numpy as np

    n_samples = int(sample_rate * duration_ms / 1000)
    t = np.arange(n_samples) / sample_rate
    # A quiet second harmonic gives a rounder, less beep-like timbre.
    signal = np.sin(2 * np.pi * frequency * t) + 0.25 * np.sin(4 * np.pi * frequency * t)
    signal /= 1.25
    envelope = np.exp(-t / (decay_ms / 1000))
    # 8ms attack: sharper onsets crackled on a USB speakerphone's output DSP.
    attack = int(0.008 * sample_rate)
    envelope[:attack] *= np.linspace(0, 1, attack)
    fade = int(0.010 * sample_rate)
    envelope[-fade:] *= np.linspace(1, 0, fade)
    return (signal * envelope * volume).astype(np.float32)


def _generate_pluck(frequency: float, duration_ms: int = 150, decay_ms: float = 30,
                    sample_rate: int = 44100, volume: float = 0.15) -> bytes:
    """The same pluck as an in-memory WAV for winsound."""
    import io, wave
    import numpy as np

    samples = (_pluck_samples(frequency, duration_ms, decay_ms, sample_rate, volume) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


class CuePlayer:
    """One output stream for every cue.

    Idle audio hardware sleeps and took ~0.36s to wake in testing, and a
    second stream on a speakerphone distorted. Recording holds the stream
    open; once released it closes after IDLE_SECONDS without cues.
    """
    SAMPLE_RATE = 44100
    IDLE_SECONDS = 3.0

    def __init__(self):
        self._lock = threading.Lock()
        self._open_lock = threading.Lock()  # Concurrent opens share one stream.
        self._stream = None
        self._pending = []
        self._held = False
        self._last_used = 0.0
        self._last_fill = 0.0
        self._closer = None

    def _fill(self, out, frames, time_info, status):
        out.fill(0)
        with self._lock:
            self._last_fill = time.monotonic()
            position = 0
            while self._pending and position < frames:
                cue = self._pending[0]
                count = min(len(cue), frames - position)
                out[position:position + count, 0] = cue[:count]
                position += count
                if count < len(cue):
                    self._pending[0] = cue[count:]
                else:
                    self._pending.pop(0)

    def open(self) -> bool:
        with self._open_lock:
            with self._lock:
                if self._stream:
                    self._last_used = time.monotonic()
                    return True
            try:
                import sounddevice as sd
                # A larger buffer rides out brief GIL stalls (e.g. the result
                # balloon render) that crackled cues with the low-latency buffer.
                stream = sd.OutputStream(samplerate=self.SAMPLE_RATE, channels=1, dtype="float32",
                                         latency="high", callback=self._fill,
                                         device=sound_mapper("Output"))
                stream.start()
            except Exception:
                return False
            with self._lock:
                self._stream = stream
                self._last_used = self._last_fill = time.monotonic()
        self._schedule_close()
        return True

    def _detach_if_stalled(self):
        """Caller holds the lock. A live stream requests audio every few ms;
        one that has not for 0.5s died (a speakerphone dropped it) and would
        otherwise swallow every later cue."""
        if self._stream is None or time.monotonic() - self._last_fill < 0.5:
            return None
        stream, self._stream = self._stream, None
        self._pending.clear()
        return stream

    def hold(self):
        """Keep the output awake for a recording."""
        with self._lock:
            self._held = True
        return self.open()

    def release(self):
        with self._lock:
            self._held = False
            self._last_used = time.monotonic()
        self._schedule_close()

    def play(self, samples, fallback_wav=None):
        """Queue a cue without blocking; wakes the output first if it slept."""
        with self._lock:
            stalled = self._detach_if_stalled()
            if self._stream is not None:
                self._pending.append(samples)
                self._last_used = time.monotonic()
                return
        if stalled:
            log("Cue output stalled; reopening")
            threading.Thread(target=self._close_stream, args=(stalled,), daemon=True).start()
        def wake_and_play():
            if not self.open():
                if fallback_wav:
                    _play_wav(fallback_wav)
                return
            with self._lock:
                self._pending.append(samples)
                self._last_used = time.monotonic()
        threading.Thread(target=wake_and_play, name="cue-wake", daemon=True).start()

    def _schedule_close(self):
        with self._lock:
            if self._closer and self._closer.is_alive():
                return
            self._closer = threading.Thread(target=self._close_when_idle, name="cue-idle", daemon=True)
            self._closer.start()

    def _close_when_idle(self):
        while True:
            time.sleep(0.5)
            with self._lock:
                stream = self._detach_if_stalled()
                if stream is None:
                    if self._stream is None:
                        return
                    if self._held or self._pending or time.monotonic() - self._last_used < self.IDLE_SECONDS:
                        continue
                    stream, self._stream = self._stream, None
            self._close_stream(stream)
            return

    @staticmethod
    def _close_stream(stream):
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


_cues = CuePlayer()


def prepare_cues():
    """Start waking the audio output in parallel with opening the mic."""
    threading.Thread(target=_cues.hold, name="cue-output", daemon=True).start()


def release_cues():
    _cues.release()


def _play_wav(wav: bytes):
    try:
        import winsound
        winsound.PlaySound(wav, winsound.SND_MEMORY)
    except Exception:
        pass


def chime_start():
    """Subtle single pluck once the mic is delivering audio."""
    _cues.hold()
    _cues.play(_pluck_samples(440.0), _generate_pluck(440.0))


def chime_stop():
    """Lower pluck at release; immediate while the output is held awake."""
    _cues.play(_pluck_samples(290.0), _generate_pluck(290.0))
    _cues.release()


def chime_paste():
    """Quiet rising two-note pluck when text is delivered to the destination."""
    import numpy as np
    _cues.play(np.concatenate([_pluck_samples(523.25, duration_ms=70, volume=0.1),
                               _pluck_samples(783.99, duration_ms=140, volume=0.1)]))


def chime_warning():
    """Low double pluck, e.g. shortly before the recording limit."""
    import numpy as np
    tone = _pluck_samples(330.0, duration_ms=110)
    _cues.play(np.concatenate([tone, tone]))


# ---------------------------------------------------------------------------
# Main application loop
# ---------------------------------------------------------------------------
from dictation_runtime import DictationRuntime


class DictationApp(DictationRuntime):
    def __init__(self, config):
        super().__init__(config, AppState, AudioRecorder, log,
                         lambda text, auto_enter, guard, commit: type_text(text, auto_enter, guard, commit))

    def ensure_model(self):
        if self.whisper is None:
            model_path = setup_model(self.config, allow_download=False)
            from model_store import verify
            verify(model_path)
            info = MODEL_REGISTRY[self.config["model_size"]]
            options = {}
            if self.config.get("decoder_device", "GPU") != "GPU":
                options["decoder_device"] = self.config["decoder_device"]
            self.whisper = create_model(model_path, device=self.config["device"], backend=info["backend"], **options)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Setup / Install dependencies
# ---------------------------------------------------------------------------
def run_setup():
    """Explicit setup using the frozen environment and revision-tracked snapshots."""
    import subprocess
    from model_store import download
    requirements_file = Path(__file__).parent / "requirements.lock.txt"
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements_file)], check=True)
    config = load_config()
    info = MODEL_REGISTRY[config["model_size"]]
    download(info["ov_repo"], MODEL_DIR / info["local_dir"], revision=info["revision"])
    cleanup = CLEANUP_REGISTRY["4b"]
    cleanup_path = MODEL_DIR / cleanup["local_dir"]
    download(cleanup["repo"], cleanup_path, revision=cleanup["revision"])
    config["cleanup_model_path"] = str(cleanup_path)
    save_config(config)
    log("Setup complete. Hold Ctrl+Win to dictate after launch.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    sys.setswitchinterval(0.0002)  # See app.main: keeps the decoder loop responsive.
    parser = argparse.ArgumentParser(description="CopyThat dictation engine")
    parser.add_argument("--setup", action="store_true", help="Run first-time setup")
    parser.add_argument("--device", choices=["NPU", "GPU", "CPU"], help="Override device")
    parser.add_argument("--model", choices=list(MODEL_REGISTRY.keys()), help="Model size")
    parser.add_argument("--language", type=str, help="Language code (e.g., en, ru, id)")
    parser.add_argument("--auto-enter", action="store_true", help="Press Enter after typing")
    parser.add_argument("--hotkey", type=str, help="Hold-to-talk hotkey (ctrl+win)")
    args = parser.parse_args()

    if args.setup:
        run_setup()
        return

    config = load_config()

    # Apply CLI overrides
    if args.device:
        config["device"] = args.device
    if args.model:
        config["model_size"] = args.model
    if args.language:
        config["language"] = args.language
    if args.auto_enter:
        config["auto_enter"] = True
    if args.hotkey:
        config["hotkey"] = args.hotkey

    # Auto-select device based on model when --device not explicitly set
    if not args.device:
        preferred = launch_device(config)
        if config["device"] != preferred:
            log(f"Auto-selecting {preferred} for {config['model_size']} "
                f"(override with --device {config['device']})")
            config["device"] = preferred

    validate_config(config)

    from windows_io import single_instance
    with single_instance() as acquired:
        if not acquired:
            log("CopyThat is already running")
            return
        app = DictationApp(config)
        app.run()


if __name__ == "__main__":
    main()
