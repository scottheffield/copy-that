"""Names and terms for Polish from earlier takes into the same text box
and from the text visible in the window.

People multitask, so "the last ten minutes" mixes topics; takes are grouped by
destination (window and focused box) instead. Only terms travel, never whole
takes: Polish is less likely to copy them in, and the prompt stays short.
Terms live in memory only and are never logged, except in a take's record when
Save takes for replay is on (off by default), so replay can reproduce Polish.
"""
import re
import time
from collections import deque

TOKEN = re.compile(r"[A-Za-z0-9][\w.'’-]*[A-Za-z0-9]|[A-Za-z0-9]")
# Capitalized words that say nothing about the topic.
COMMON = {"i", "i'm", "i'll", "i've", "i'd", "ok", "okay", "yes", "no", "so", "and", "but", "the", "a",
          "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "am", "pm"}
TITLE_SPLIT = re.compile(r"\s+[-–—|·]\s+")


def is_term(token, sentence_start=False):
    """Identifiers, acronyms, mixed case and interior capitalized words."""
    if token.lower().replace("’", "'") in COMMON or len(token) < 2:
        return False
    # Numbers, ordinals and short labels ("22nd", "2.5", "1A") are not spellings to keep.
    if re.fullmatch(r"[\d.,:/]+|\d+(?:st|nd|rd|th)|\d[A-Za-z]|[A-Za-z]\d", token):
        return False
    if re.search(r"[A-Za-z]\d|\d[A-Za-z]|[_]|\w\.\w", token):  # OPS-49, Qwen3, take_log, cleanup.py
        return True
    if re.search(r"[A-Z]", token[1:]):  # PR, OpenVINO, CopyThat
        return True
    return token[0].isupper() and not sentence_start


def terms_in(text):
    found = []
    for match in TOKEN.finditer(text):
        before = text[:match.start()].rstrip()
        start = not before or before[-1] in ".!?\n"
        token = match.group()
        if is_term(token, start) and token not in found:
            found.append(token)
    return found


def title_terms(title):
    """Terms from a window title, minus the app name that ends most titles."""
    parts = TITLE_SPLIT.split(title.strip())
    found = []
    for part in parts[:-1] if len(parts) > 1 else parts:
        for term in ([part] if " " not in part and len(part) > 1 else []) + terms_in(part):
            if term not in found:
                found.append(term)
    return found


# UI Automation control types whose names are content: file tree entries, tabs,
# links, list rows and text. Buttons, menus, groups and toolbars are app chrome.
CONTENT_CONTROLS = {50005, 50007, 50019, 50020, 50024, 50029}  # Hyperlink, ListItem, TabItem, Text, TreeItem, DataItem
# Web addresses and emails are read, not spoken.
WEB = re.compile(r"\S+@\S+|\b[\w.-]+\.(?:com|org|net|io|ai|dev|app|co|us)\b", re.I)
SHORTCUT = re.compile(r"\s*\((?:Ctrl|Alt|Shift|Win)\+[^)]*\)")
PRIVATE_USE = re.compile("[-]")  # Icon font glyphs


def screen_terms(items, selection="", limit=300):
    """Terms from a window walk: (control type, name) pairs, plus selected text first."""
    found = []
    def add(text, label=False):
        text = WEB.sub("", PRIVATE_USE.sub("", SHORTCUT.sub("", text))).strip()
        if label:
            text = re.sub(r"\s+\d+$", "", text)  # Obsidian's folder counts: "Recipes 2"
        # A one-word label is a name ("Recipes", a folder), not a sentence's first word.
        single = [text] if label and TOKEN.fullmatch(text) and is_term(text) else []
        for term in single + terms_in(text):
            term = re.sub(r"['’]s$", "", term)  # "Alex's" is Alex
            if term not in found:
                found.append(term)
    add(selection)
    for control, name in items:
        if control in CONTENT_CONTROLS and name:
            add(name, label=True)
        if len(found) >= limit:
            break
    return found[:limit]


SPELLING = {"dot", "dash", "underscore", "slash"}


def letters(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def sounds(run):
    """Letters with common sound-alike spellings merged: "Cobolt" reads like
    "Kobalt", "BrightSense" like "BrightCents" (a soft c is an s)."""
    run = re.sub(r"c(?=[eiy])", "s", run.replace("ph", "f").replace("ck", "k"))
    return run.replace("c", "k").replace("q", "k").replace("z", "s").replace("y", "i")


def pairs_of(run):
    return {run[i:i + 2] for i in range(len(run) - 1)}


def spoken_runs(text):
    """Each run of 1-4 words as bare letters, indexed by letter pair, so a
    term is compared only with runs that share some of its letter pairs."""
    spoken = TOKEN.findall(text)
    runs = {}
    for size in range(1, 5):
        for i in range(len(spoken) - size + 1):
            run = sounds(letters("".join(w for w in spoken[i:i + size] if w.lower() not in SPELLING)))
            if run and run not in runs:
                runs[run] = len(pairs_of(run))
    index = {}
    for run in runs:
        for pair in pairs_of(run):
            index.setdefault(pair, []).append(run)
    return runs, index


def near_miss(term, text, ratio=0.75, runs=None):
    """The text has a run of 1-4 words that reads like the term but is not
    written as it: "open veeno" for OpenVINO, "handoff md" for HANDOFF.md,
    "OBS 49" for OPS-49 (0.8)."""
    return match_score(term, text, runs) >= ratio


def match_score(term, text, runs=None, ratio=0.75):
    """How closely the best run of words reads like the term; 0 below ratio."""
    target = sounds(letters(term))
    if len(target) < 4 or re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text):
        return 0
    # "the explorer" is not a misspelled "Explorer"; "open vino" is OpenVINO.
    if term.istitle() and term.isalpha() and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I):
        return 0
    from collections import Counter
    from difflib import SequenceMatcher
    sizes, index = spoken_runs(text) if runs is None else runs
    pairs = pairs_of(target)
    shared = Counter(run for pair in pairs for run in index.get(pair, ()))
    match = SequenceMatcher(None, autojunk=False)
    match.set_seq2(target)
    best = 0
    for run, count in shared.items():
        if run + "s" == target or target + "s" == run:
            continue  # A plural is the same word: "call" and "Calls".
        # Shared letter pairs first: cheap, and rules out almost every run.
        if abs(len(run) - len(target)) > len(target) * 0.4 or 2 * count < 0.3 * (len(pairs) + sizes[run]):
            continue
        match.set_seq1(run)
        if match.quick_ratio() >= max(ratio, best):
            best = max(best, match.ratio())
    return best if best >= ratio else 0


def identifier(term):
    """File names, codes and mixed case: worth running Polish for. Plain words
    ("Pages", "Search") near-miss ordinary speech too often."""
    return not (term.isalpha() and term[:1].isupper() and term[1:].islower()) \
        and not re.fullmatch(r"[A-Za-z]+['’]s", term)


def strong_name(term, text):
    """A plain name close enough to run Polish for ("Cobolt" for Kobalt), but
    not a short one ("Jane" in "june")."""
    return len(letters(term)) >= 6 and match_score(term, text, ratio=0.8) > 0


def relevant(terms, text, limit=10):
    """Screen terms worth sending to Polish: only those the take seems to say,
    so a walk's hundreds of names cost a handful of prompt tokens."""
    runs = spoken_runs(text)
    # Closest first, short terms last: "Jane" reads like "june" too easily to
    # crowd out a real name.
    scored = [(match_score(term, text, runs) * min(1, len(letters(term)) / 6), i, term)
              for i, term in enumerate(terms)]
    return [term for score, i, term in sorted(scored, key=lambda x: (-x[0], x[1])) if score][:limit]


def respell(text, terms):
    """Write a run of words as the term when its letters match exactly:
    "Quick Notes bot" -> "QuickNotes bot", "the hubspot import" -> "HubSpot".
    No model needed. Terms under six letters ("How-to") are too easily ordinary words."""
    for term in terms:
        target = letters(term)
        if len(target) < 6:
            continue
        spoken = list(TOKEN.finditer(text))
        for size in range(4, 0, -1):
            for i in range(len(spoken) - size, -1, -1):
                run = spoken[i:i + size]
                if run[0].group().lower() in SPELLING or run[-1].group().lower() in SPELLING:
                    continue
                if letters("".join(m.group() for m in run if m.group().lower() not in SPELLING)) != target:
                    continue
                start, end = run[0].start(), run[-1].end()
                if text[start:end] != term:
                    text = text[:start] + term + text[end:]
            spoken = list(TOKEN.finditer(text))
    return respell_names(text, terms)


def respell_names(text, terms, ratio=0.8):
    """A capitalized word Parakeet heard as a name, written as the screen's
    spelling when it sounds nearly the same and starts with the same sound:
    "Cobolt" -> "Kobalt" (0.83). The first
    sound keeps "Grokbot" from becoming a "Brockbot" on screen (0.86)."""
    from difflib import SequenceMatcher
    known = {letters(term) for term in terms}
    candidates = [term for term in terms if len(letters(term)) >= 6]
    def replace(match):
        word = match.group()
        if not word[0].isupper() or len(letters(word)) < 5 or letters(word) in known:
            return word
        heard = sounds(letters(word))
        scores = sorted(((SequenceMatcher(None, heard, sounds(letters(term))).ratio(), term)
                         for term in candidates if sounds(letters(term))[:1] == heard[:1]), reverse=True)
        # Only a clear winner: two screen names alike is a guess.
        if scores and scores[0][0] >= ratio and (len(scores) == 1 or scores[1][0] < scores[0][0]):
            return scores[0][1]
        return word
    return TOKEN.sub(replace, text)


def number_names(terms, text):
    """Names on screen that are number words ("Seven", a company), said in the take."""
    from spoken_numbers import SMALL, TENS
    return [term for term in terms if term.istitle() and term.lower() in {*SMALL, *TENS}
            and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I)]


def merge_terms(recent, screen):
    """Screen terms first; an earlier take's term that reads like one of them
    is the old misspelling ("Cobolt" beside the screen's "Kobalt") and goes."""
    from difflib import SequenceMatcher
    alike = lambda a, b: SequenceMatcher(None, letters(a), letters(b)).ratio() >= 0.6
    return screen + [term for term in recent if term not in screen and not any(alike(term, s) for s in screen)]


def destination_key(hwnd, field, process):
    return (hwnd, field) if hwnd else (process,)


class RecentTerms:
    def __init__(self, minutes=30, limit=20):
        self.seconds, self.limit = minutes * 60, limit
        self._takes = deque(maxlen=200)

    def add(self, key, text, when=None):
        found = terms_in(text)
        if found:
            self._takes.append((time.monotonic() if when is None else when, key, found))

    def terms(self, key, title="", when=None):
        """Newest first: what the title shows, then what was said here lately."""
        now = time.monotonic() if when is None else when
        found = title_terms(title)
        for added, other, terms in reversed(self._takes):
            if other == key and now - added <= self.seconds:
                found += [term for term in terms if term not in found]
        return found[:self.limit]
