"""Meeting playback (#63): mixing, level matching, echo ducking, the feed, and export."""
import wave

import numpy as np
import pytest

import meetings
import playback
from meetings import RATE, WavTrack


def tone(seconds, freq=300, amp=0.3):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def meeting(tmp_path, mic, others, compress=False):
    folder = tmp_path / "2026-11-23 0900"
    folder.mkdir()
    for name, samples in (("mic", mic), ("others", others)):
        if samples is None:
            continue
        track = WavTrack(folder / f"{name}.wav")
        track.append(samples)
        track.close()
        if compress:
            assert meetings.compress_track(folder / f"{name}.wav")
    return folder


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def test_levels_are_matched_within_limits(tmp_path):
    folder = meeting(tmp_path, tone(3, amp=0.02), tone(3, amp=0.2))
    sources = playback.Sources(folder)
    g = playback.gains(sources)
    sources.close()
    quiet, loud = 0.02 / np.sqrt(2), 0.2 / np.sqrt(2)
    assert g["mic"] * quiet == pytest.approx(playback.TARGET_RMS, rel=0.05)
    assert g["others"] * loud == pytest.approx(playback.TARGET_RMS, rel=0.05)
    (tmp_path / "w").mkdir()
    sources = playback.Sources(meeting(tmp_path / "w", tone(3, amp=0.002), None))
    assert playback.gains(sources) == {"mic": playback.GAIN_RANGE[1], "others": 1.0}  # Capped; no track
    sources.close()


def test_echo_on_the_mic_is_ducked_but_talking_over_is_not():
    mixer = playback.Mixer({"mic": 1.0, "others": 1.0})
    others = tone(1, amp=0.3)
    echo = np.roll(tone(1, amp=0.05), 400)  # The other side, quieter and a little late
    mixed = mixer(echo, others)
    tail = slice(RATE // 2, RATE)  # Once the ducking has settled
    assert rms(mixed[tail] - others[tail]) == pytest.approx(rms(echo[tail]) * playback.DUCK, rel=0.1)
    mixer.reset()
    me = tone(1, freq=500, amp=0.3)
    mixed = mixer(me, others)
    assert rms(mixed[tail] - others[tail]) == pytest.approx(rms(me[tail]), rel=0.02)


def test_mic_alone_is_never_ducked():
    mixer = playback.Mixer({"mic": 1.0, "others": 1.0})
    me = tone(1, amp=0.05)
    mixed = mixer(me, np.zeros_like(me))
    np.testing.assert_allclose(mixed, me, atol=1e-6)


def test_tracks_of_different_lengths_and_formats_play_to_the_longer(tmp_path):
    folder = meeting(tmp_path, tone(1), tone(2), compress=True)
    sources = playback.Sources(folder)
    assert sources.frames == 2 * RATE
    assert not sources.read("mic", int(1.5 * RATE), 100).any()  # Silence past the mic's end
    sources.close()


class FakeStream:
    """Stands in for sounddevice's stream: tests pull audio through the callback."""

    def __init__(self, callback, **_options):
        self.callback = callback

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def no_speakers(monkeypatch):
    import sounddevice
    streams = []
    monkeypatch.setattr(sounddevice, "OutputStream",
                        lambda **options: streams.append(FakeStream(**options)) or streams[-1])
    return streams


def pull(player, stream, seconds):
    out = np.zeros((int(seconds * RATE), 1), dtype=np.float32)
    done = 0
    for _ in range(400):
        chunk = out[done:done + playback.BLOCK // 2]
        if not len(chunk):
            break
        before = player._position
        stream.callback(chunk, len(chunk), None, None)
        if player._position != before or player.ended:
            done += len(chunk)
        else:
            import time
            time.sleep(0.01)  # The feeder hasn't caught up yet
    return out[:done, 0]


def test_player_plays_seeks_and_releases_the_files(tmp_path, no_speakers):
    folder = meeting(tmp_path, np.zeros(2 * RATE, np.float32), tone(2))
    released = []
    player = playback.Player(folder, on_release=lambda: released.append(True))
    assert player.duration == 2.0 and not playback.holding(folder)
    assert player.play() and player.playing and playback.holding(folder)
    heard = pull(player, no_speakers[-1], 0.5)
    assert len(heard) == RATE // 2 and rms(heard) > 0.05
    assert player.position == pytest.approx(0.5, abs=0.05)
    player.seek(1.5)
    pull(player, no_speakers[-1], 0.25)
    assert player.position == pytest.approx(1.75, abs=0.05)
    player.pause()
    assert not player.playing and not playback.holding(folder) and released == [True]
    assert player.position == pytest.approx(1.75, abs=0.05)  # Paused where it was


def test_player_stops_at_the_end_and_starts_over_next_time(tmp_path, no_speakers):
    folder = meeting(tmp_path, tone(0.5), tone(0.5))
    player = playback.Player(folder)
    player.play()
    pull(player, no_speakers[-1], 1.0)
    assert player.ended
    player.tick()
    assert not player.playing and player.position == 0.5 and not playback.holding(folder)
    player.play()
    assert player.position == 0
    player.close()


def test_missing_audio_is_reported_not_raised(tmp_path, no_speakers):
    folder = tmp_path / "empty"
    folder.mkdir()
    player = playback.Player(folder)
    assert player.duration == 0
    (folder / "mic.wav").write_bytes(b"not audio")
    assert not player.play() and "Couldn't open" in player.error
    assert not playback.holding(folder)


@pytest.mark.parametrize("suffix", [".mp3", ".opus"])
def test_export_writes_one_mixed_file(tmp_path, suffix):
    import soundfile
    folder = meeting(tmp_path, tone(3, freq=500, amp=0.1), tone(3, amp=0.3), compress=True)
    target = tmp_path / f"call{suffix}"
    fractions = []
    assert playback.export(folder, target, report=fractions.append)
    assert fractions[-1] == 1.0 and not target.with_name(target.name + ".part").exists()
    info = soundfile.info(str(target))
    assert info.channels == 1 and info.duration == pytest.approx(3.0, abs=0.1)


def test_stopped_export_leaves_nothing(tmp_path):
    folder = meeting(tmp_path, tone(1), tone(1))
    target = tmp_path / "call.mp3"
    assert not playback.export(folder, target, stopped=lambda: True)
    assert not list(tmp_path.glob("call*"))


def test_export_needs_a_known_format(tmp_path):
    folder = meeting(tmp_path, tone(1), tone(1))
    with pytest.raises(ValueError):
        playback.export(folder, tmp_path / "call.wma")
