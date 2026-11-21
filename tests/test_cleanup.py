from unittest.mock import Mock
from cleanup import Cleanup, apply_rules, mask_spans, restore_spans, drift_reason, dropped_text, words


def test_dictionary_uses_boundaries_without_cascading_or_touching_paths():
    assert apply_rules("claude code and claude coder", {"claude code": "Claude Code"}) == "Claude Code and claude coder"
    assert apply_rules("um use /tmp/um.py like this") == "Use /tmp/um.py like this"
    assert apply_rules("one", {"one": "two", "two": "three"}) == "two"


def test_spoken_commands_only_change_current_take_when_enabled():
    assert apply_rules("hello new paragraph world", commands=True) == "Hello\n\nWorld"
    assert apply_rules("First sentence. Wrong sentence. scratch that Correct sentence.", commands=True) == "First sentence. Correct sentence."
    assert apply_rules("wrong scratch that right", commands=True) == "Right"
    assert apply_rules("say scratch that") == "Say scratch that"


def test_spoken_commands_leave_no_stray_punctuation():
    assert apply_rules("Ask Alex. New line. Then go", commands=True) == "Ask Alex.\nThen go"
    assert apply_rules("hello, new paragraph, world", commands=True) == "Hello\n\nWorld"
    assert apply_rules("First sentence. Wrong sentence. Scratch that. Correct sentence.", commands=True) == "First sentence. Correct sentence."


def test_spoken_command_words_inside_ordinary_speech_stay_words():
    assert apply_rules("this is a new line of products today", commands=True) == "This is a new line of products today"
    assert apply_rules("we wrote the new paragraph of the report", commands=True) == "We wrote the new paragraph of the report"
    assert apply_rules("send a new line", commands=True) == "Send a new line"
    assert apply_rules("first part new line second part", commands=True) == "First part\nSecond part"


def test_literal_mode_bypasses_all_cleanup():
    model = Mock()
    config = {"cleanup_enabled": True, "spoken_commands": True, "dictionary": {"um": "oops"}}
    result = Cleanup(config, model).run("um scratch that", literal=True)
    assert result.text == "um scratch that"
    model.generate.assert_not_called()


def test_negation_numbers_and_expansion_cannot_pass_high_overlap():
    assert drift_reason("Please do not deploy this to production today", "Please do deploy this to production today") == "negation"
    assert drift_reason("Please send 15 copies today", "Please send 50 copies today") == "numbers"
    assert drift_reason("could you check this build please", "The build failed because the dependencies are missing and the configuration is wrong")


def test_placeholder_loss_triggers_fallback_and_valid_output_restores_identifiers():
    raw = "please update foo_bar in /tmp/main.py and keep the existing public interface"
    model = Mock()
    model.generate.side_effect = lambda text, *args: text + "."
    cleaner = Cleanup({"cleanup_enabled": True, "cleanup_gate": False}, model)
    assert cleaner.run(raw).text == "P" + raw[1:] + "."
    model.generate.return_value = "Please update the file and keep the existing public interface."
    model.generate.side_effect = None
    assert cleaner.run(raw).fallback == "protected_spans"


def test_rules_survive_timeout_and_short_input_skips_model():
    model = Mock()
    model.generate.side_effect = TimeoutError()
    cleaner = Cleanup({"cleanup_enabled": True, "cleanup_gate": False}, model)
    assert cleaner.run("um please check the current build and tell me why it failed").fallback == "timeout"
    result = cleaner.run("um hello there")
    assert result.text == "Hello there"
    assert model.generate.call_count == 1


def test_explicit_name_correction_permits_only_named_replacement():
    source = "send it on Tuesday sorry Wednesday but do not change the time"
    assert drift_reason(source, "Send it on Wednesday, but do not change the time.") is None
    assert drift_reason(source, "Send it on Thursday, but do not change the time.") == "names"
    assert drift_reason("Send it on Tuesday but do not change the time", "Send it on Wednesday but do not change the time") == "names"


def test_dropped_ending_falls_back_even_with_high_overlap():
    # Real take: the model silently omitted the final sentence.
    source = ("So there we might need to handle that somehow with some kind of a download "
              "dialogue or something. But you can think about that.")
    output = "So there we might need to handle that somehow with some kind of a download dialogue or something."
    assert drift_reason(source, output) == "dropped_ending"


def test_dropped_middle_span_falls_back_unless_it_is_a_correction():
    source = "Please review the plan and then check the deployment logs before you ship it."
    assert drift_reason(source, "Please review the plan before you ship it.") == "dropped_span"
    corrected = "Send it to the London office, sorry, I mean the Paris office today."
    assert dropped_text(words(corrected), words("Send it to the Paris office today.")) is None


def test_trailing_filler_and_stutter_removal_are_not_dropped_text():
    assert dropped_text(words("It works fine you know"), words("It works fine.")) is None
    assert dropped_text(words("these are very very very very useful"), words("These are very useful.")) is None


def test_gate_skips_model_unless_rules_text_has_stutter_correction_or_filler():
    model = Mock()
    model.generate.side_effect = lambda text, *args: text
    cleaner = Cleanup({"cleanup_enabled": True}, model)
    clean = "I guess we have an ending tone after the recording finishes."
    assert cleaner.run(clean).text == clean
    model.generate.assert_not_called()
    for raw in ("Send it on Tuesday, sorry, Wednesday before the meeting starts.",
                "I think the the problem is in the release path somewhere.",
                "It works fine most of the time, you know, but not always."):
        cleaner.run(raw)
    assert model.generate.call_count == 3


def test_screen_terms_are_respelled_without_running_the_model():
    model = Mock()
    model.generate.side_effect = lambda text, *args: text
    cleaner = Cleanup({"cleanup_enabled": True}, model)
    spell = ["QuickNotes", "Kobalt", "BrightCents", "OpenVINO"]
    result = cleaner.run("Ask Alex Cobalts about the Quick Notes bot and BrightSense today.", terms=spell, spell=spell)
    assert result.text == "Ask Alex Kobalt about the QuickNotes bot and BrightCents today."
    assert cleaner.run("I was talking about the email from Alex Cobolt and Grokbot.",
                       terms=spell + ["Brockbot"], spell=spell + ["Brockbot"]).text == \
        "I was talking about the email from Alex Kobalt and Grokbot."
    # A fuzzy match the rules can't settle is left for Polish runs that happen anyway.
    assert cleaner.run("Please update the open veeno runtime before the release.", terms=spell, spell=spell).text == \
        "Please update the open veeno runtime before the release."
    model.generate.assert_not_called()


def test_rules_capitalize_sentence_starts_but_not_abbreviations_or_paths():
    assert apply_rules("It failed. so we retried. it worked") == "It failed. So we retried. It worked"
    assert apply_rules("Use tools, e.g. git and npm.") == "Use tools, e.g. git and npm."
    assert apply_rules("Done. git status shows nothing") == "Done. git status shows nothing"


def test_rules_capitalize_the_first_word_of_a_take_but_not_a_leading_command():
    assert apply_rules("is it ready") == "Is it ready"
    assert apply_rules("git status shows nothing") == "git status shows nothing"
    assert apply_rules("iPhone sync failed") == "iPhone sync failed"


def test_rules_end_question_shaped_sentences_with_a_question_mark():
    # Real take: the model was skipped and the question kept its period.
    assert apply_rules("is raising the priority a good idea.") == "Is raising the priority a good idea?"
    assert apply_rules("Should be fine. can you check it.") == "Should be fine. Can you check it?"
    assert apply_rules("Do it now. do you see it.") == "Do it now. Do you see it?"
    assert apply_rules("What I mean is this. what is the plan.") == "What I mean is this. What is the plan?"
    assert apply_rules("Is it 3.5 or 4.") == "Is it 3.5 or 4?"
    assert apply_rules("Does /tmp/a.py exist.") == "Does /tmp/a.py exist?"
    assert apply_rules("Are we done! I wonder if it is done.") == "Are we done! I wonder if it is done."
    assert apply_rules("Is it, e.g. this one.") == "Is it, e.g. this one."


def test_removing_a_stuttered_negation_is_not_a_negation_change():
    assert drift_reason("Please don't don't deploy that until I say so, okay?",
                        "Please don't deploy that until I say so, okay?") is None
    assert drift_reason("Please don't deploy that until I say so.", "Please deploy that until I say so.") == "negation"
