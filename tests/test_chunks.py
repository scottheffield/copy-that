import numpy as np
from audio_chunks import windows, stitch, stitch_timed, stitch_overlap, TimedWord


def test_continuous_minute_has_no_gaps_and_all_windows_fit():
    audio = np.arange(60 * 16000, dtype=np.float32)
    chunks = list(windows(audio))
    assert chunks[0][0] == 0
    assert chunks[-1][-1] == len(audio) - 1
    for chunk in chunks:
        assert len(chunk) <= int(14.5 * 16000)
    for left, right in zip(chunks, chunks[1:]):
        assert right[0] <= left[-1]


def test_stitch_removes_shared_words_and_preserves_unaligned_text():
    assert stitch("Please check the build.", "the build and fix it") == "Please check the build. and fix it"
    assert stitch("keep this content", "different wording continues") == "keep this content different wording continues"


def test_parakeet_long_audio_uses_multiple_bounded_inferences():
    from dictation_engine import ParakeetModel
    from unittest.mock import Mock
    model = ParakeetModel.__new__(ParakeetModel)
    model._transcribe_window = Mock(side_effect=[
        [TimedWord("first", 0), TimedWord("shared", 12.5), TimedWord("words", 13)],
        [TimedWord("shared", .5), TimedWord("words", 1), TimedWord("second", 3)]])
    text = model.transcribe(np.full(20 * 16000, 0.05, dtype=np.float32))
    assert text == "first shared words second"
    assert model._transcribe_window.call_count == 2


def test_repeated_sentences_are_not_mistaken_for_audio_overlap():
    phrase = "Please check the current build and tell me why it failed. Do not deploy it to production until I approve it."
    left = f"{phrase} {phrase}"
    right = f"it. {phrase} {phrase}"
    assert stitch(left, right) == " ".join([phrase] * 4)


def test_timed_overlap_preserves_a_new_repeated_sentence():
    phrase = "Do not deploy it."
    left = [TimedWord(w, i * .3) for i, w in enumerate(phrase.split())]
    repeated = [TimedWord(w, 3 + i * .3) for i, w in enumerate(phrase.split())]
    assert stitch_timed(left, repeated) == left + repeated
    shared = [TimedWord(w.text, w.time + .1) for w in left]
    assert stitch_timed(left, shared + repeated) == left + repeated


def test_uncertain_timed_overlap_keeps_content():
    left = [TimedWord("not", 1), TimedWord("approved", 1.2)]
    right = [TimedWord("not", 2.4), TimedWord("approved", 2.6)]
    assert stitch_timed(left, right) == left + right


def test_quick_word_repetition_is_preserved():
    left, right = [TimedWord("no", 1)], [TimedWord("no", 1.3)]
    assert stitch_timed(left, right) == left + right


def timed(text, start, step=0.3):
    return [TimedWord(word, start + i * step) for i, word in enumerate(text.split())]


def test_overlap_drops_a_misheard_clipped_word_at_the_right_edge():
    # Real take: the next window began inside "text box" and heard "Xbox".
    left = timed("into that text box and retype something it pasted.", 41.4)
    right = timed("Xbox and retype something it pasted. Sometimes it", 42.3)
    text = " ".join(w.text for w in stitch_overlap(left, right, 42.2, 44.7))
    assert text == "into that text box and retype something it pasted. Sometimes it"


def test_overlap_keeps_words_the_next_window_missed():
    # Real take: the next window never recognized "algorithm" near its start.
    left = timed("that smart correction algorithm.", 53.6, 0.4)
    right = [TimedWord("I", 55.3), TimedWord("will", 55.5), TimedWord("say", 55.7)]
    text = " ".join(w.text for w in stitch_overlap(left, right, 53.3, 55.8))
    assert text == "that smart correction algorithm. I will say"


def test_overlap_drops_a_clipped_word_at_the_left_edge():
    left = timed("please deploy the stag", 54.3, 0.5)  # "stag" clipped at 55.8
    right = timed("deploy the staging build", 54.8, 0.5)
    text = " ".join(w.text for w in stitch_overlap(left, right, 53.3, 55.8))
    assert text == "please deploy the staging build"


def test_overlap_takes_punctuation_from_the_window_that_heard_what_follows():
    left = timed("it's not obvious.", 21.0, 0.4)
    right = timed("it's not obvious what that means", 21.05, 0.4)
    text = " ".join(w.text for w in stitch_overlap(left, right, 20.2, 22.7))
    assert text == "it's not obvious what that means"
