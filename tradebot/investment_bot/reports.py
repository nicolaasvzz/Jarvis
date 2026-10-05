"""Every run leaves a report behind, in ``reports/`` — not just the last one.

    reports/2026-10-05_213015_backtest.html
    reports/2026-10-06_063002_learn.html
    reports/2026-10-06_070110_champ.html
    reports/2026-10-06_071544_check.html
    reports/2026-10-06_160012_trading.html

The name says when and what kind of run made it, so the folder sorts by date.
Each page carries its own label in its head — the ``<title>``, a one-line
``<meta name="description">`` ("+3.2% return, 41 trades") and a
``<meta name="tone">`` (good / bad / neutral) — which is what Jarvis's
TradeBot page lists, newest first. A report is never rewritten afterwards.

Writing a report must never break the run that made it: every function here
returns ``None`` instead of raising.
"""
from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

REPORTS_DIR = "reports"

KINDS = {
    "backtest": "Backtest",
    "learn": "Learning session",
    "champ": "Champ-set builder",
    "check": "Test mode",
    "trading": "Paper trading",
}

Tone = str  # "good", "bad" or "neutral"


def report_path(kind: str, folder: str | Path = REPORTS_DIR, when: datetime | None = None) -> Path:
    """A new, unused file name for a report of this kind."""
    when = when or datetime.now()
    folder = Path(folder)
    stem = f"{when:%Y-%m-%d_%H%M%S}_{kind}"
    path = folder / f"{stem}.html"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}.html"
        n += 1
    return path


def _head(title: str, summary: str, tone: Tone) -> str:
    return (f'<meta name="description" content="{html.escape(summary)}">\n'
            f'<meta name="tone" content="{html.escape(tone)}">\n'
            f"<title>{html.escape(title)}</title>")


def archive(source: str | Path, kind: str, summary: str, tone: Tone = "neutral",
            title: str | None = None, folder: str | Path = REPORTS_DIR,
            when: datetime | None = None) -> Path | None:
    """Keep a copy of a report another part of the bot wrote (the backtest's
    report.html), with this label put in its head."""
    try:
        text = Path(source).read_text(encoding="utf-8")
        head = _head(title or KINDS.get(kind, kind.title()), summary, tone)
        text = re.sub(r"<title>.*?</title>", "", text, count=1, flags=re.S)
        text = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + "\n" + head, text, count=1)
        path = report_path(kind, folder, when)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path
    except (OSError, ValueError):
        return None


class Report:
    """A simple report page: headline numbers, then sections of text, lists
    and tables. Build it up, then ``save()``."""

    def __init__(self, kind: str, summary: str, tone: Tone = "neutral",
                 title: str | None = None, subtitle: str = ""):
        self.kind, self.summary, self.tone = kind, summary, tone
        self.title = title or KINDS.get(kind, kind.title())
        self.subtitle = subtitle
        self.stats: list[tuple[str, str, Tone]] = []
        self.sections: list[tuple[str, str]] = []

    def stat(self, label: str, value: Any, tone: Tone = "neutral") -> Report:
        self.stats.append((label, str(value), tone))
        return self

    def text(self, heading: str, paragraph: str) -> Report:
        self.sections.append((heading, f"<p>{html.escape(paragraph)}</p>"))
        return self

    def bullets(self, heading: str, lines: Iterable[str], empty: str = "None.") -> Report:
        items = "".join(f"<li>{html.escape(str(line))}</li>" for line in lines)
        self.sections.append((heading, f"<ul>{items}</ul>" if items else
                              f'<p class="muted">{html.escape(empty)}</p>'))
        return self

    def table(self, heading: str, rows: Sequence[dict[str, Any]], empty: str = "None.") -> Report:
        if not rows:
            self.sections.append((heading, f'<p class="muted">{html.escape(empty)}</p>'))
            return self
        columns = list(dict.fromkeys(k for row in rows for k in row))
        head = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
        body = "".join(
            "<tr>" + "".join(_cell(row.get(c)) for c in columns) + "</tr>" for row in rows)
        self.sections.append((heading, f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
                                       f"<tbody>{body}</tbody></table></div>"))
        return self

    def html(self, when: datetime | None = None) -> str:
        when = when or datetime.now()
        tiles = "".join(
            f'<div class="tile"><span>{html.escape(label)}</span><b class="{tone}">'
            f"{html.escape(value)}</b></div>" for label, value, tone in self.stats)
        sections = "".join(f"<h2>{html.escape(h)}</h2><div class=\"card\">{body}</div>"
                           for h, body in self.sections)
        subtitle = f"{when:%A %d %B %Y, %H:%M}" + (f" · {self.subtitle}" if self.subtitle else "")
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{_head(self.title, self.summary, self.tone)}
<style>{STYLE}</style></head><body><div class="wrap">
<h1>{html.escape(self.title)}</h1>
<p class="sub">{html.escape(subtitle)}</p>
<p class="summary {self.tone}">{html.escape(self.summary)}</p>
{f'<div class="tiles">{tiles}</div>' if tiles else ""}
{sections}
<p class="foot">Not financial advice. Backtests overstate live results.</p>
</div></body></html>"""

    def save(self, folder: str | Path = REPORTS_DIR) -> Path | None:
        try:
            path = report_path(self.kind, folder)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.html(), encoding="utf-8")
            return path
        except (OSError, ValueError):
            return None


def _cell(value: Any) -> str:
    if isinstance(value, float):
        cls = ' class="num neg"' if value < 0 else ' class="num"'
        return f"<td{cls}>{value:,.2f}</td>"
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<td class="num">{value:,}</td>'
    return f"<td>{html.escape('' if value is None else str(value))}</td>"


def tone_of(value: float) -> Tone:
    return "good" if value > 0 else "bad" if value < 0 else "neutral"


STYLE = """
 :root { --bg:#070b10; --card:#0e151d; --edge:rgba(148,196,214,.14); --text:#e6f1f5;
   --muted:rgba(230,241,245,.6); --green:#34d399; --red:#f43f5e; --accent:#22d3ee; }
 * { box-sizing:border-box; }
 body { margin:0; background:var(--bg); color:var(--text); padding:24px 16px 48px;
   font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
 .wrap { max-width:1000px; margin:0 auto; }
 h1 { margin:0; font-size:24px; letter-spacing:-.01em; }
 .sub { margin:4px 0 0; color:var(--muted); font-size:13px; }
 .summary { margin:16px 0 0; font-size:17px; font-weight:600; }
 h2 { margin:28px 0 10px; font-size:11px; letter-spacing:.14em; text-transform:uppercase;
   color:var(--muted); }
 .card { background:var(--card); border:1px solid var(--edge); border-radius:14px;
   padding:14px 16px; }
 .tiles { display:grid; gap:10px; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
   margin-top:18px; }
 .tile { background:var(--card); border:1px solid var(--edge); border-radius:12px;
   padding:12px 14px; }
 .tile span { display:block; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
   color:var(--muted); }
 .tile b { display:block; margin-top:4px; font-size:20px; font-variant-numeric:tabular-nums;
   overflow-wrap:anywhere; }
 .good { color:var(--green); } .bad { color:var(--red); }
 ul { margin:0; padding-left:20px; } li { margin:3px 0; }
 p { margin:0; } .muted { color:var(--muted); }
 .scroll { overflow-x:auto; }
 table { border-collapse:collapse; width:100%; font-size:13px; }
 th { text-align:left; font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
   color:var(--muted); padding:6px 8px; white-space:nowrap; }
 td { padding:6px 8px; border-top:1px solid var(--edge); }
 td.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
 td.neg { color:var(--red); }
 .foot { margin-top:28px; color:var(--muted); font-size:12px; }
"""
