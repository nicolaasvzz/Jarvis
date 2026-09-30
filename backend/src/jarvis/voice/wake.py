"""Deciding whether an utterance was addressed to Jarvis.

With an always-open microphone, most of what arrives is not a command: it is
half of a phone call, the television, someone in the next room. The wake word
is the filter, and it has to be forgiving in one direction and strict in the
other — missing a real "Jarvis" is a bad experience, but acting on a
half-heard word is worse.

So matching is fuzzy but anchored: the wake word must appear at the *start*
of the utterance, and only close variants count. Speech recognisers mishear
a name in predictable ways ("Travis", "Jervis", "Java's"), and a similarity
threshold catches those without letting an unrelated word through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# Overall similarity alone cannot separate a mishearing from a different
# name: measured against "jarvis", the common mishearing "travis" scores
# 0.667 — exactly the same as "marvin" and "harris", which must not trigger
# anything. What distinguishes them is the ending. Recognisers garble the
# leading consonant of a name far more often than its stressed tail, so
# "-vis" survives in travis/jervis/javis/darvis while unrelated names keep
# only the middle. Hence two ways to match:
#
#   * close enough outright (jervis 0.83, javis 0.91), or
#   * plausible AND sharing the wake word's ending (travis 0.67 + "vis").
#
# Measured decoys stay out under both: marvin/harris 0.667 without the tail,
# harvest 0.615, java 0.600, service 0.462.
_SIMILARITY_THRESHOLD = 0.78
_TAIL_SIMILARITY_THRESHOLD = 0.62
_TAIL_LENGTH = 3

#: The wake word may be preceded by a form of address without breaking the
#: match — people say "hey Jarvis" as readily as "Jarvis".
_PREFIXES = frozenset({"hey", "ok", "okay", "yo", "hi", "hello", "um", "uh", "so"})

#: Filler that commonly follows the name and is not part of the command.
_LEADING_FILLER = frozenset({"please", "can", "could", "would", "you"})

_WORD = re.compile(r"[a-z0-9']+")

#: Possessives and stray apostrophes ("jarvis'", "jarvis's") are the same
#: word for matching purposes.
_TRAILING_APOSTROPHE = re.compile(r"'s?$")


@dataclass(frozen=True)
class WakeMatch:
    """Whether an utterance was addressed to Jarvis, and what it asked for."""

    addressed: bool
    command: str
    #: Similarity of the best candidate word to the wake word, for tuning.
    score: float = 0.0


def _similarity(candidate: str, wake_word: str) -> float:
    return SequenceMatcher(None, candidate, wake_word).ratio()


def _is_wake_word(candidate: str, wake_word: str) -> tuple[bool, float]:
    """Decide whether one spoken word is the wake word, and how close it was."""
    word = _TRAILING_APOSTROPHE.sub("", candidate)
    score = _similarity(word, wake_word)
    if score >= _SIMILARITY_THRESHOLD:
        return True, score
    tail = wake_word[-_TAIL_LENGTH:]
    if score >= _TAIL_SIMILARITY_THRESHOLD and word.endswith(tail):
        return True, score
    return False, score


def match_wake_word(
    text: str,
    wake_word: str,
    *,
    required: bool = True,
) -> WakeMatch:
    """Split ``text`` into "was this for me" and "what was asked".

    When ``required`` is false every utterance counts as addressed, but a
    leading wake word is still stripped so "Jarvis, open the notes" and
    "open the notes" produce the same command.
    """
    stripped = text.strip()
    if not stripped:
        return WakeMatch(addressed=False, command="")

    words = _WORD.findall(stripped.lower())
    if not words:
        return WakeMatch(addressed=not required, command=stripped if not required else "")

    target = wake_word.strip().lower()
    # Allow one optional greeting before the name: "hey jarvis", "ok jarvis".
    start = 1 if len(words) > 1 and words[0] in _PREFIXES else 0

    matched, score = (
        _is_wake_word(words[start], target) if start < len(words) else (False, 0.0)
    )
    if not matched:
        # Not addressed. Without a wake word requirement the whole utterance
        # is the command; with one, it is ambient noise.
        return WakeMatch(
            addressed=not required,
            command=stripped if not required else "",
            score=score,
        )

    return WakeMatch(
        addressed=True,
        command=_command_after(stripped, words, start),
        score=score,
    )


def _command_after(original: str, words: list[str], name_index: int) -> str:
    """Return the original text following the wake word, tidily.

    Works from the original string rather than the normalised words so that
    punctuation, casing and numbers in the command survive intact.
    """
    consumed = words[: name_index + 1]
    remainder = original
    for word in consumed:
        # Cut just past each consumed word, case-insensitively.
        position = remainder.lower().find(word)
        if position == -1:
            continue
        remainder = remainder[position + len(word) :]
    command = remainder.lstrip(" ,.;:!?-—…").strip()

    # "jarvis please open X" and "jarvis can you open X" both mean "open X".
    lowered = _WORD.findall(command.lower())
    trimmed = 0
    for word in lowered:
        if word not in _LEADING_FILLER:
            break
        position = command.lower().find(word, trimmed)
        if position == -1:
            break
        trimmed = position + len(word)
    if trimmed:
        candidate = command[trimmed:].lstrip(" ,.;:!?-—…").strip()
        # Only accept the trim if something is actually left to act on.
        if candidate:
            command = candidate
    return command
