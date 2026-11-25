# CopyThat

<img src="docs/icon.png" alt="CopyThat icon: a blue speech bubble with a yellow check" width="96">

A local Windows dictation app. Dictate into text fields in browsers, email, chat, documents, editors, and other desktop apps, with no destination-specific integration required. It also records meetings in any meeting app and transcribes who said what, all on your PC ([Meetings](#meetings)).

Speech recognition uses OpenVINO. Parakeet runs entirely on the Intel GPU, which measured faster than placing its encoder on the NPU; Whisper models still prefer the NPU. Optional Qwen3 cleanup also runs on the GPU in the same process. Runtime uses local model files; a model downloads only when you click Download.

On first launch, a welcome window offers the recommended pair: Parakeet for speech (2.5 GB) and Qwen3 4B for Polish (2.3 GB). One click downloads both from Hugging Face, Parakeet first. Dictation starts as soon as Parakeet finishes, and Polish turns on when Qwen finishes. Every model downloads a pinned revision, the build that was tested.

Developed and tested on Windows 11 with an Intel GPU and NPU. On a PC without an Intel GPU, CopyThat runs on the CPU. One contributor's tests on a fast desktop CPU found speech recognition quick, and Polish slower on long takes ([#13](https://github.com/scottheffield/copy-that/issues/13)). NVIDIA and AMD GPUs aren't used ([#26](https://github.com/scottheffield/copy-that/issues/26)). Other setups are untested.

## Install

1. Download `CopyThat-Setup.exe` from the [latest release](https://github.com/scottheffield/copy-that/releases/latest).
2. Run it. It installs for your user account, so it doesn't need administrator rights. The installer isn't code signed, so Windows may warn you first. Choose **More info**, then **Run anyway**.
3. Open CopyThat from the Start menu. The welcome window downloads the speech and Polish models, about 4.8 GB in total.
4. Click into any text field, hold Ctrl+Win, speak, and let go.

You don't need Python or PowerShell. Setup offers an unchecked **Start with Windows** option, and you can change it later from the tray menu.

If CopyThat is already running, launching it again exits quietly; use the existing tray icon.

Application files default to `%LOCALAPPDATA%\Programs\CopyThat`. Config, models, and compilation caches live in `%LOCALAPPDATA%\CopyThat`, independently of app files. Updates and uninstall preserve user data. Quit the tray app after finishing pending takes before updating or uninstalling.

## Use

The app lives in the system tray. The floating pill is hidden while idle and shows recording, processing, and brief results.

| Gesture | Action |
| --- | --- |
| Hold Ctrl+Win | Record into the current text field |
| Release | Finish, process, and paste when modifiers are up |
| Press Space while holding Ctrl+Win | Lock recording hands-free |
| Press Ctrl+Win again | Finish the locked recording |
| Escape | Cancel the recording, or the latest unfinished take |
| Shift+Alt+Z | Paste the latest result where the cursor is now |

Up to three unfinished takes can overlap. Processing and pasting remain ordered. Each pasted take ends with a space so the next take starts a new word. Existing trailing whitespace is preserved and auto-submit omits the added space. History keeps the transcript without the added separator. Recordings stop at 5 minutes by default, with a low double pluck 15 seconds before the limit. Audio cues: a pluck once the microphone is actually delivering audio, a lower pluck on release, and a rising two-note pluck when text is pasted. Silence detection rejects takes without enough detected speech before recognition.

## Meetings

Click **Record meeting** at the top of the Meetings page, or in the tray menu, to record a call, and **Stop** when it ends. CopyThat records two tracks: your microphone, labeled "Me", and whatever the laptop plays, labeled "Others". It works with any meeting app. Nothing joins the call, and nothing is uploaded. The output track records the Windows default speaker, so set your meeting app's speaker to Default.

Most of the meeting is transcribed during the call, so the transcript is written a few seconds after Stop. Dictation keeps working during a meeting and always goes first. Your dictated words stay out of the meeting transcript; turn that off with **Leave dictation out of meeting transcripts** in Settings. The mic button on the meeting chip (or **Mute my mic in the meeting** in the tray menu) mutes your side while the other side keeps recording. A mute longer than 5 seconds is marked in the transcript.

**Speakers.** After the transcript is written, CopyThat finds the different voices on the other side and labels them Speaker 1, Speaker 2, and so on, numbered by who speaks first. A voice with only a few words is merged into the speakers around it. When the laptop plays nothing for the whole recording, as in an in-person meeting, everyone is on the microphone track, labeled "Speaker", and the voices there are separated instead. On a long meeting this takes a few minutes, with progress on the meeting's page. Turn it off with **Label speakers in meeting transcripts** in Settings > General.

Click a line's speaker to fix the labels:

- Pick another speaker to move that line to them. The **›** next to a speaker also offers to move all of this speaker's lines, which merges the two.
- Type a name to add a speaker and move the line to them.
- The pencil renames this speaker on every line. A name another speaker already has merges the two, after a confirmation.

The name you give yourself is remembered for later meetings. **Re-transcribe** finds the speakers again, so it resets the other names and moved lines, after a warning.

**Files.** The Meetings page lists your meetings, newest first, with search. Each meeting has **Copy as Markdown** and **Re-transcribe**. Each meeting gets a folder under `CopyThat Meetings` in your user folder, named with its date, time, and title, such as `2026-11-24 2013 Weekly review`. It holds `mic.wav`, `others.wav`, `transcript.md`, and `meta.json`, plus the word timings and speaker names. The title in CopyThat is the meeting's name: renaming a meeting renames its folder, and a folder renamed by hand gets its title back the next time CopyThat starts. **Folder ▾** on the Meetings page opens the meetings folder, or uses **Change folder…** to save meetings somewhere else, and offers to move the meetings already saved. If the chosen folder is unavailable, for example on an unplugged drive, new meetings go to `CopyThat Meetings` until it returns.

If CopyThat quits or crashes mid-meeting, the audio is kept. Nothing restarts by itself at the next launch: the meeting says it didn't finish, and **Re-transcribe** finishes it.

Recording other people may require their consent, and the rules vary by place. Following them is your responsibility. Nothing on the call tells other people they are being recorded.

## Settings and recovery

Right-click the tray icon and choose **Settings**:

Settings is organized into **General**, **Speech**, **Dictionary**, and **Advanced**. In General, **Appearance** offers **System** (the default), **Light**, and **Dark**. Click **Apply changes** to save your choice. System follows the Windows app appearance while CopyThat is running; manual choices override it. Appearance changes do not reload the speech models. The recording pill keeps the same layout and controls in both themes. Routine status messages stay in the app instead of raising a Windows toast.

- **Microphone** chooses a particular input device or follows the Windows default. A selected microphone is remembered by its name and audio API. If it disappears, select another microphone.
- **Speech recognition** and **Polish** (on the Speech page) use the same model cards. Each shows Installed, a Download button with its size, or download progress with Cancel. Only installed models can be selected; click Apply to load one. The speech model and Polish model are independent. After you switch away from a model, CopyThat offers to delete it; installed models that aren't in use also have a Remove action.
- **Literal mode** returns the recognizer's raw transcript, bypassing the dictionary, rules, commands, and cleanup.
- **Dictionary** maps recognized phrases to preferred spellings using `phrase=replacement`, one per line.
- **Spoken commands** (experimental, off by default) interpret "new line", "new paragraph", and "scratch that" within this take. They never edit previously pasted text. Known issues: punctuation around a command can be left behind, ordinary phrases like "a new line of products" also trigger it, and Polish may remove line breaks.
- **Polish** (AI cleanup) fixes punctuation, stutters, and spoken corrections. It offers Off (dictionary and filler rules only), Standard (Qwen3 4B), Thorough (Qwen3 8B), or a custom local model. Qwen3 1.7B can be installed with `-CleanupModel 1.7b` as a lighter option; on the same takes it was about 0.1-0.2s faster than 4B but handled self-corrections less well. 8B is slower than 4B. Off avoids the model delay.
  Qwen cleanup uses prompt lookup to speed up copying the transcript. Meaning guards and deadline fallback still apply.
- **Add a space after dictation** can be turned off if you prefer exact insertion.
- **Press Enter after paste** is optional and submits text wherever Enter normally submits it. It is off by default.
- **Issue keys** are set in `config.json`, not Settings. With `"issue_keys": ["OPS"]`, saying "OPS twenty three" writes `OPS-23`. The list is empty by default.

**History** retains the latest 20 completed results in memory and provides **Copy final** and **Copy raw**. If the destination window changes, or the destination is one of this app's own windows, automatic paste is skipped and the result remains here; press Shift+Alt+Z in the intended field to paste it. History resets when the app exits or its engine reloads.

The destination check identifies a window and process. Moving between fields within the same window cannot be detected. Pasting into an app running as administrator is not supported. Clipboard preservation currently saves text only, so copying an image or file before dictating can lose that clipboard content. A newer user copy is left alone.

Take logging is off by default. When enabled, it saves audio and transcripts to the configured local directory outside the repository and Google Drive, keeping the newest 200 takes (`take_log_keep` in `config.json`; 0 keeps them all). Takes with extra files, such as a checked `reference.txt`, are never deleted. These files contain what you dictate. Ordinary runtime logs omit transcript content. Nothing is uploaded during dictation.

If recording fails, check Windows microphone permissions and choose the microphone in Settings. If a model fails, remove it and download it again in Settings, or select another installed model.

## What we learned

CopyThat has been used for daily dictation while it was built. The numbers below come from one laptop (Core Ultra 7 258V, Arc 140V, 32 GB) during development, so treat them as rough guides.

### The NPU plan did not survive measurement

The project started as an NPU app, which is why the fork began from `npu-whisper`. The idea was to use an otherwise idle chip for speech and leave the GPU for cleanup.

Parakeet changed that. Its encoder took about 50 ms on either the NPU or the GPU, but the all-GPU pipeline finished 1.7-1.9x sooner, because the GPU stays clocked up when all the work runs there. Parakeet now runs entirely on the GPU, and the NPU is only used by the Whisper models that prefer it. Larger Whisper models on the NPU can also trigger driver instability (`DEVICE_LOST`).

The lesson is to time the whole pipeline. A faster or more efficient chip for one stage can still lose end to end.

### Most of the speed came from outside the models

- **Finish the work before release.** Finished audio windows are recognized while you are still talking, so little remains when you let go.
- **Stop the mic first and close it later.** Closing the stream in the background saved about 0.7s per take.
- **Watch the Python scheduler.** UI threads starved the decoder loop. A shorter GIL switch interval took one decode under contention from 2.3s to 0.14s.
- **Keep the hardware awake.** Idle accelerators and audio devices clock down within seconds, so the app keeps them warm while recording.
- **Use prompt lookup for cleanup.** Cleanup mostly copies its input, so lookup helps a lot. Short takes dropped from 0.41-1.00s to 0.24-0.36s, and one long take from 5.17s to 1.13s.
- **Bigger was not better.** Qwen3 8B felt too slow for every take, and 1.7B handled self-corrections less well, so 4B is the default.
- **Test on a busy machine.** Timings on a quiet machine can hide problems that show up with a browser and editors open. Dictation raises its process priority from hotkey to paste for this reason.

Long takes needed their own fix. Parakeet's encoder has a fixed 16-second input, so audio runs in windows of at most 14.5 seconds with 2.5 seconds of overlap. Windows are joined only where the words and their timing agree, which removed duplicated and dropped words at the seams.

### Cleanup works best when the model does little

- **Rules go first.** Fillers, numbers, dates and spelled letters are handled by plain code, which is fast and predictable. Paths, URLs and code are masked so nothing can alter them.
- **The prompt asks mostly for deletion.** Polish is told to remove stutters, false starts and replaced words, and to leave everything else alone.
- **Model output is never trusted.** Each result is compared with the rules text and checked for lost negation, missing names, added text and low word overlap. If a check fails, or the 5-second deadline passes, you get the rules text.
- **Expect modest gains.** In one review Polish changed about 15% of takes, mostly punctuation. A better prompt cut leftover stutters from 14 to 11 in 118 takes and added 0.07s. It still misses some false starts.

### How context improves names

Speech models often misspell names, so the app collects likely terms and uses them to correct the transcript:

1. **Sources.** Terms come from the window title, earlier takes into the same window and text box, and text visible in the window. Visible text is read through UI Automation while you are still talking, which takes 63-312 ms but doesn't delay the result.
2. **Only terms travel.** Whole takes are never reused, and terms stay in memory and are never logged. `polish_context` and `screen_context` turn this off.
3. **Plain code does the reliable part.** Words whose letters match a term exactly are respelled without the model. Sound-alike names are respelled at 0.8 similarity or better when the first sound also matches. This beat the prompt, because Qwen3 4B mostly ignored name terms.
4. **Polish gets a short list.** It only sees terms the take seems to mention.
5. **Guards catch bad swaps.** Polish once appended the whole term list to a take, and once replaced a folder name with "Google Drive" because that term was available. A term now has to be spoken, and has to look at least 50% like the word it replaces.
6. **Number words can be names.** If "Seven" is a company name on screen, it stays a word instead of becoming 7.

Context can only correct spelling. If the recognizer hears a different word entirely, nothing downstream can recover it.

### Measure with real takes

Opt-in take logging and `scripts/replay_takes.py` re-run saved transcripts through the current rules and Polish, then compare the results with an earlier run. Most of the tuning above came from that loop. It also caught a context "win" that was actually wrong, the "Google Drive" swap above.

One reverted fix is worth repeating: a check for missed key releases stopped every take at once, because the hotkey's own keys are swallowed by the hook and `GetAsyncKeyState` reports them as up. After any keyboard hook change, try the real hotkey.

## Models

Models are not stored in this repository. Speech and Polish models download from Hugging Face when you choose them. The two small speaker models that label who spoke in a meeting (about 46 MB) are included in the installer. Each model keeps its own license.

| Model | Used for | Source | License |
| --- | --- | --- | --- |
| Parakeet TDT 0.6B v3 | Speech | [goodsmileduck/parakeet-tdt-0.6b-v3-onnx](https://huggingface.co/goodsmileduck/parakeet-tdt-0.6b-v3-onnx), converted from [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) | CC-BY-4.0 (NVIDIA original); the conversion is listed as Apache-2.0 |
| Whisper base, small, medium | Speech | [OpenVINO](https://huggingface.co/OpenVINO) conversions of OpenAI Whisper | Apache-2.0 |
| Whisper large v3 turbo | Speech | [FluidInference/whisper-large-v3-turbo-int4-ov-npu](https://huggingface.co/FluidInference/whisper-large-v3-turbo-int4-ov-npu), converted from [openai/whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | Apache-2.0 (conversion); MIT (OpenAI original) |
| Qwen3 1.7B, 4B, 8B | Polish | [OpenVINO](https://huggingface.co/OpenVINO) conversions of Qwen3 | Apache-2.0 |
| pyannote segmentation 3.0 | Meeting speakers | [csukuangfj/sherpa-onnx-pyannote-segmentation-3-0](https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0), converted from [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0) | MIT |
| TitaNet small | Meeting speakers | [csukuangfj/speaker-embedding-models](https://huggingface.co/csukuangfj/speaker-embedding-models) (`nemo_en_titanet_small.onnx`), converted from NVIDIA NeMo [TitaNet small](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/nemo/models/titanet_small) | CC-BY-4.0 |

## Build from source

This section is for developers. To use CopyThat, see [Install](#install).

For a new checkout, install Python 3.10 and run:

```powershell
.\Start-Dictation.ps1 -Setup
# Optional additional candidates:
.\Start-Dictation.ps1 -Setup -Model small -CleanupModel 4b
```

Setup installs `requirements.lock.txt` and downloads complete model snapshots at their pinned revisions. Cleanup remains opt-in. Other models can be downloaded from Settings. First compilation can take substantially longer than subsequent cached launches.

| Path | Contents |
| --- | --- |
| `.venv/` | Project Python environment |
| `.runtime/config.json` | Launcher configuration |
| `.runtime/models/` | Downloaded model snapshots and manifests |
| `.runtime/speaker-models/` | Meeting speaker models, fetched by setup and the installer build (`python speaker_models.py`) |
| `.runtime/ov-cache/` | Speech compilation cache |
| `.runtime/models/ov-cache/` | Cleanup compilation cache |
| `.runtime/dictation.log` | Timing and error logs without transcript text, capped at 10 MB across two files |
| `~/.copythat-takes/` | Personal recordings, only when take logging is enabled, newest 200 kept |

Use `-DataDir C:\path\to\data` to choose another local runtime directory. Keep it out of synced folders. Direct Python launches honor `COPYTHAT_DATA_DIR`; without it, they use `~/.copythat`.

### Build the installer

For a repeatable local build, install `requirements-build.txt`, obtain the [portable NSIS compiler](https://nsis.sourceforge.io/Download), and run:

```powershell
.\.venv\Scripts\python.exe scripts\build_windows.py --nsis .runtime\tools\nsis-3.13\makensis.exe
```

For an offline install, build with `--with-models` to stage Parakeet and Qwen3-4B in a `models/` folder beside the installer; setup copies them in. To move a development checkout's settings, dictionary and active models into the installed app, run `.\.venv\Scripts\python.exe scripts\migrate_dev_data.py` before first launch.

### Run from a checkout

```powershell
.\Start-Dictation.ps1
```

Optional console mode and Startup shortcut:

```powershell
.\Start-Dictation.ps1 -CLI
# Opt-in; not installed automatically:
.\scripts\Install-Startup.ps1
.\scripts\Install-Startup.ps1 -Remove
```

## Development and validation

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp=.test-temp
$env:COPYTHAT_DATA_DIR = Join-Path $PWD '.runtime'
.\.venv\Scripts\python.exe scripts\smoke_app.py
.\.venv\Scripts\python.exe scripts\smoke_paste.py
```

The paste smoke opens disposable test windows and exercises the real clipboard and Windows input. Do not type during that check. The app smoke loads both models with Python network calls blocked, then closes.

Replay opt-in take logs after adding checked `reference.txt` and, optionally, `expected.txt` alongside each `audio.wav`:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark.py "$HOME\.copythat-takes" --model small --device NPU --cleanup-path .runtime\models\Qwen3-4B-int4-ov --output .runtime\benchmark-personal.json
```

Synthetic smoke results validate execution, not accuracy on your voice.

## Contributing and security

CopyThat is a hobby project, shared as is. Pull requests may not get reviewed, issues may not get answered, and there's no schedule for fixes or new features. You're welcome to fork it and make it your own.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and tests, [SECURITY.md](SECURITY.md) for the local threat model and how to report a problem, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community standards.

## Credits and license

CopyThat began as a fork of [Goodsmileduck/npu-whisper](https://github.com/Goodsmileduck/npu-whisper), and that project's copyright notice is kept in [LICENSE](LICENSE).

CopyThat is released under the MIT license. Packaged builds also include third-party Python libraries under their own licenses, including pystray (LGPL-3.0).
