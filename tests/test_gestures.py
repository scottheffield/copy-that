import pytest
from hotkeys import Gesture, SPACE, ESCAPE


@pytest.mark.parametrize("first,second", [(0xA2, 0x5B), (0x5B, 0xA2)])
@pytest.mark.parametrize("release_first", [True, False])
def test_hold_balances_forwarded_modifiers_and_finalizes_once(first, second, release_first):
    g = Gesture()
    assert g.event(first, True) == (False, [])
    assert g.event(second, True) == (True, ["mask", "start"])
    assert g.event(second, True)[1] == []  # repeat
    order = (first, second) if release_first else (second, first)
    actions = [g.event(vk, False) for vk in order]
    assert sum(a.count("stop") for _, a in actions) == 1
    assert not g.chord
    assert not g.down
    assert not g.swallowed
    # Only the modifier whose down was swallowed has its up swallowed.
    assert dict(zip(order, [s for s, _ in actions])) == {first: False, second: True}


def test_hands_free_stop_does_not_restart_on_same_chord():
    g = Gesture()
    g.event(0xA2, True)
    g.event(0x5B, True)
    assert g.event(SPACE, True) == (True, ["lock"])
    g.event(SPACE, False)
    assert g.event(0xA2, False)[1] == []
    g.event(0x5B, False)
    g.event(0x5B, True)
    assert g.event(0xA2, True)[1] == ["mask", "stop"]
    assert g.event(0xA2, True)[1] == []
    assert g.event(0xA2, False)[1] == []
    assert g.event(0x5B, False)[1] == []


def test_timeout_does_not_restart_until_modifiers_are_up():
    g = Gesture()
    g.event(0xA2, True)
    g.event(0x5B, True)
    g.finish()
    assert g.event(0x5B, True)[1] == []
    assert g.event(0xA2, False)[1] == []
    g.event(0x5B, False)
    g.event(0xA2, True)
    assert g.event(0x5B, True)[1] == ["mask", "start"]


def test_space_is_normal_outside_chord_and_escape_repeat_cancels_once():
    g = Gesture()
    assert g.event(SPACE, True) == (False, [])
    g.event(SPACE, False)
    assert g.event(ESCAPE, True, True) == (True, ["cancel"])
    assert g.event(ESCAPE, True, False) == (True, [])
    assert g.event(ESCAPE, False, False) == (True, [])
    assert g.event(ESCAPE, True, False) == (False, [])


def test_shortcuts_during_hold_are_swallowed_with_balanced_releases():
    g = Gesture()
    g.event(0x5B, True)
    g.event(0xA2, True)
    assert g.event(0x44, True)[0]
    g.event(0xA2, False)
    g.event(0x5B, False)
    assert g.event(0x44, False)[0]


def test_key_forwarded_before_chord_keeps_its_release_after_suppressed_repeat():
    g = Gesture()
    assert not g.event(0x44, True)[0]
    g.event(0xA2, True)
    g.event(0x5B, True)
    assert g.event(0x44, True)[0]
    assert not g.event(0x44, False)[0]


def test_shift_alt_z_pastes_last_and_swallows_only_z():
    g = Gesture()
    assert g.event(0xA0, True) == (False, [])
    assert g.event(0xA4, True) == (False, [])
    assert g.event(0x5A, True) == (True, ["mask", "paste_last"])
    assert g.event(0x5A, True) == (True, [])  # auto-repeat pastes once
    assert g.event(0x5A, False) == (True, [])
    assert g.event(0xA4, False) == (False, [])
    assert g.event(0xA0, False) == (False, [])


@pytest.mark.parametrize("modifiers", [(), (0xA0,), (0xA4,), (0xA2, 0xA4), (0xA0, 0xA4, 0x5B)])
def test_z_without_exact_shift_alt_is_ordinary_typing(modifiers):
    g = Gesture()
    for vk in modifiers:
        g.event(vk, True)
    swallow, actions = g.event(0x5A, True)
    assert "paste_last" not in actions


def test_late_chord_press_lets_its_release_through():
    # Windows passed the Win press on while the hook stalled; hiding its
    # release would leave Win held down for every app.
    g = Gesture()
    g.event(0xA2, True)
    assert g.event(0x5B, True, late=True) == (True, ["mask", "start"])
    assert 0x5B not in g.swallowed
    swallow, actions = g.event(0x5B, False)
    assert not swallow and actions == ["stop"]


def test_swallowed_release_windows_still_holds_is_reported_once():
    g = Gesture()
    g.event(0xA2, True)
    g.event(0x5B, True)
    g.event(0xA2, False)
    assert g.event(0x5B, False)[0]  # Its press was swallowed, so the release is too.
    assert g.hidden_releases == {0x5B}
    assert g.stuck(lambda vk: vk == 0x5B) == {0x5B}
    assert g.stuck(lambda vk: vk == 0x5B) == set()


def test_hidden_release_is_forgotten_when_windows_agrees_or_key_is_pressed_again():
    g = Gesture()
    g.event(0xA2, True)
    g.event(0x5B, True)
    g.event(0xA2, False)
    g.event(0x5B, False)
    assert g.stuck(lambda vk: False) == set()
    assert not g.hidden_releases
    g.hidden_releases = {0x5B}
    g.event(0x5B, True)  # Held again on purpose: not stuck.
    assert g.stuck(lambda vk: True) == set()
    assert g.hidden_releases == {0x5B}


class Windows:
    """Key state as Windows sees it: only keys the hook passes on change it."""
    def __init__(self):
        self.down = set()

    def key(self, g, vk, pressed):
        # The hook's order: forget missed releases, then decide.
        stale = g.forget_released(lambda k: k in self.down, vk)
        swallow, actions = g.event(vk, pressed)
        if not swallow:
            (self.down.add if pressed else self.down.discard)(vk)
        return swallow, actions, stale

    def released_out_of_sight(self, vk):
        self.down.discard(vk)  # On the lock or sign-in screen.


WIN_L, CTRL_L, A, C = 0x5B, 0xA2, 0x41, 0x43


def test_win_released_on_the_lock_screen_does_not_start_a_take_or_lock_typing():
    g, windows = Gesture(), Windows()
    windows.key(g, WIN_L, True)
    windows.key(g, 0x4C, True)  # Win+L
    windows.released_out_of_sight(0x4C)
    windows.released_out_of_sight(WIN_L)
    swallow, actions, stale = windows.key(g, CTRL_L, True)  # Ctrl+C after signing in
    assert stale == {WIN_L}
    assert (swallow, actions) == (False, [])
    windows.key(g, C, True)
    windows.key(g, C, False)
    windows.key(g, CTRL_L, False)
    assert windows.key(g, A, True)[:2] == (False, [])
    assert not g.chord


def test_ctrl_released_out_of_sight_does_not_start_on_win():
    g, windows = Gesture(), Windows()
    windows.key(g, CTRL_L, True)
    windows.released_out_of_sight(CTRL_L)
    assert windows.key(g, WIN_L, True)[:2] == (False, [])


@pytest.mark.parametrize("first,second", [(CTRL_L, WIN_L), (WIN_L, CTRL_L)])
def test_a_real_hold_survives_the_check(first, second):
    g, windows = Gesture(), Windows()
    windows.key(g, first, True)
    assert windows.key(g, second, True)[1] == ["mask", "start"]
    for _ in range(3):  # Repeats and a hands-free lock while both are held.
        assert windows.key(g, second, True)[2] == set()
    assert windows.key(g, SPACE, True)[1] == ["lock"]
    windows.key(g, SPACE, False)
    assert g.chord and g.locked
    windows.key(g, second, False)
    windows.key(g, first, False)
    assert not g.chord and not g.down and not windows.down


def test_swallowed_presses_are_never_forgotten():
    g, windows = Gesture(), Windows()
    windows.key(g, CTRL_L, True)
    windows.key(g, WIN_L, True)  # Swallowed: Windows never saw it.
    assert WIN_L not in windows.down
    assert g.forget_released(lambda k: k in windows.down) == set()
    assert WIN_L in g.down


from hotkeys import HookWatch


def test_watch_reinstalls_when_windows_sees_the_chord_and_the_hook_hears_nothing():
    w = HookWatch(hold=0.5, cooldown=10)
    assert not w.check(100.0, True, last_event=99.0)
    assert not w.check(100.25, True, last_event=99.0)
    assert w.check(100.5, True, last_event=99.0)


def test_watch_leaves_a_live_hook_alone():
    w = HookWatch(hold=0.5)
    # Windows sees both held (a late press), but the hook keeps hearing repeats.
    for i in range(20):
        now = 100 + i * 0.25
        assert not w.check(now, True, last_event=now - 0.03)
    # Nothing held: never a reinstall, however quiet the hook.
    assert not any(w.check(200 + i, False, last_event=0) for i in range(5))


def test_watch_needs_the_chord_held_without_a_break():
    w = HookWatch(hold=0.5)
    w.check(100.0, True, last_event=0)
    w.check(100.25, False, last_event=0)
    assert not w.check(100.5, True, last_event=0)
    assert w.check(101.0, True, last_event=0)


def test_watch_waits_out_the_cooldown():
    w = HookWatch(hold=0.5, cooldown=10)
    w.check(100.0, True, last_event=0)
    assert w.check(100.5, True, last_event=0)
    assert not any(w.check(101 + i * 0.25, True, last_event=0) for i in range(20))
    assert w.check(110.5, True, last_event=0)  # Held right through: fires once the cooldown ends.
