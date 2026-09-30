"""Identities for the agents in the pool.

An agent is not a separate mind — it is one concurrent worker running one
step of a plan. But giving each worker a stable name, colour and appearance
turns an opaque thread pool into something you can watch and reason about:
"Brooke is stuck on the download" is a far more useful sentence than
"worker 3 is blocked".

The roster is fixed and ordered, so agent ``agent-01`` is always Hale with
the same colour across restarts. That stability is what lets the dashboard
animate a character rather than redraw a stranger every time.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Short surnames — they have to fit above a 16-pixel-wide character without
#: the nameplate swamping the sprite.
_NAMES = (
    "Hale",
    "Brooke",
    "Ives",
    "Carrow",
    "Ashby",
    "Vance",
    "Lowry",
    "Marsh",
    "Thorne",
    "Reeves",
    "Quill",
    "Frost",
    "Bram",
    "Pike",
    "Weld",
    "Nash",
    "Croft",
    "Dunn",
    "Wren",
    "Gage",
)

#: How many visually distinct character designs the office renderer draws.
#: Agents beyond this count reuse a design but never a colour, so they stay
#: tellable apart.
SPRITE_VARIANTS = 6

#: Largest pool the roster can name. Matches ``AgentConfig.pool_size``'s cap.
MAX_AGENTS = len(_NAMES)


@dataclass(frozen=True)
class AgentProfile:
    """The stable identity of one worker in the pool."""

    id: str
    name: str
    #: Hue in degrees (0-360) for the agent's colour. The dashboard tints the
    #: sprite and its trail with this, so 20 agents stay distinguishable.
    hue: int
    #: Which character design to draw, in ``range(SPRITE_VARIANTS)``.
    sprite: int
    #: Position in the roster, 0-based.
    index: int

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "hue": self.hue,
            "sprite": self.sprite,
            "index": self.index,
        }


def _hue_for(index: int) -> int:
    """Spread hues around the wheel so neighbours never look alike.

    Stepping by the golden angle (~137.5°) keeps consecutive agents far
    apart in colour no matter how many there are, which a fixed palette of
    N colours cannot do when N changes.
    """
    return int((index * 137.508) % 360)


def build_roster(size: int) -> list[AgentProfile]:
    """Create ``size`` agent identities, capped at :data:`MAX_AGENTS`."""
    count = max(1, min(size, MAX_AGENTS))
    return [
        AgentProfile(
            id=f"agent-{index + 1:02d}",
            name=_NAMES[index],
            hue=_hue_for(index),
            sprite=index % SPRITE_VARIANTS,
            index=index,
        )
        for index in range(count)
    ]
