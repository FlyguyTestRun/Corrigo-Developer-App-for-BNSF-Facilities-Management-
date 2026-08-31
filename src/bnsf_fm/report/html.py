"""A self-contained HTML scorecard, generated locally.

One file, no network, no build step — written to disk on the machine that holds
the data and never uploaded anywhere. That property is the point: the page can
be opened, printed, or attached to an email without the underlying work order
data leaving the laptop.

Design notes, because charts get this wrong by default:

* The ranked bar chart is **one series with one member emphasized**, not two
  categories. Peers and you use two steps of a single blue ramp (validated as an
  ordinal pair in both light and dark against the real surfaces), and your bar
  additionally carries a direct "you" label — so identity never rests on color
  alone.
* Deltas pair a status color with an arrow glyph and words ("2.1 days faster
  than the department median"). A red or green swatch on its own never carries
  the meaning.
* Every figure is also present as text in a table, so the page survives being
  printed in greyscale or read by someone who cannot distinguish the hues.
"""

from __future__ import annotations

import html
import json
from datetime import datetime

from bnsf_fm.analytics.quality import QualityReport
from bnsf_fm.analytics.scorecard import Scorecard

# Ordinal blue ramp: peers, then you. Validated with the dataviz palette
# validator against both surfaces — monotone lightness, adjacent ΔL ≥ 0.06,
# light end clears the surface (2.06:1 light / 2.15:1 dark), single hue.
PALETTE = {
    "light": {
        "surface": "#fcfcfb",
        "plane": "#f9f9f7",
        "ink": "#0b0b0b",
        "ink_secondary": "#52514e",
        "ink_muted": "#898781",
        "grid": "#e1e0d9",
        "baseline": "#c3c2b7",
        "peer": "#86b6ef",
        "you": "#2a78d6",
        "good": "#0ca30c",
        "critical": "#d03b3b",
        "border": "rgba(11,11,11,0.10)",
    },
    "dark": {
        "surface": "#1a1a19",
        "plane": "#0d0d0d",
        "ink": "#ffffff",
        "ink_secondary": "#c3c2b7",
        "ink_muted": "#898781",
        "grid": "#2c2c2a",
        "baseline": "#383835",
        "peer": "#184f95",
        "you": "#3987e5",
        "good": "#0ca30c",
        "critical": "#d03b3b",
        "border": "rgba(255,255,255,0.10)",
    },
}


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _json_block(payload: dict[str, object]) -> str:
    """Serialize for embedding inside a <script> element.

    HTML parsers end a script element at the first literal "</script>", wherever
    it appears — including inside a JSON string. A work order title or a
    technician name containing that sequence would break out of the block and
    inject markup, so the characters that can start a tag are escaped as unicode
    literals. They stay valid JSON and parse back identically.
    """
    return (
        json.dumps(payload, indent=2)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _fmt(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value:.0%}"
    if unit == "days":
        return f"{value:.1f} d"
    return f"{value:g}"


def _delta_sentence(comparison) -> tuple[str, str, str]:  # noqa: ANN001
    """(glyph, wording, status role) for one comparison.

    Returns words, not just a color — a status hue never carries meaning alone.
    """
    delta = abs(comparison.delta)
    if delta < 1e-9:
        return "=", "level with the department median", "neutral"
    if comparison.metric == "Median days to completion":
        wording = "faster" if comparison.delta < 0 else "slower"
        return (
            ("▼" if comparison.delta < 0 else "▲"),
            f"{delta:.1f} days {wording} than the department median",
            "good" if comparison.favorable else "critical",
        )
    if comparison.unit == "%":
        # Rates are compared in percentage points, not as a percentage of a
        # percentage — and to one decimal, because 72% against 71% is a real
        # one-point gap that rounds to "0%" at zero decimals.
        wording = "above" if comparison.delta > 0 else "below"
        points = delta * 100
        return (
            ("▲" if comparison.delta > 0 else "▼"),
            f"{points:.1f} percentage points {wording} the department median",
            "good" if comparison.favorable else "critical",
        )
    wording = "more than" if comparison.delta > 0 else "fewer than"
    return (
        ("▲" if comparison.delta > 0 else "▼"),
        f"{delta:g} {wording} the department median",
        "good" if comparison.favorable else "critical",
    )


def _css() -> str:
    def block(mode: str) -> str:
        p = PALETTE[mode]
        return "\n".join(f"    --{k.replace('_', '-')}: {v};" for k, v in p.items())

    return f"""
:root {{
  color-scheme: light;
{block("light")}
  --radius: 10px;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
{block("dark")}
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
{block("dark")}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--plane); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}}
.wrap {{ max-width: 940px; margin: 0 auto; padding: 32px 24px 64px; }}
header {{ margin-bottom: 26px; }}
h1 {{ font-size: 22px; font-weight: 650; margin: 0 0 4px; letter-spacing: -0.01em; }}
.sub {{ color: var(--ink-secondary); font-size: 13.5px; }}
section {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 20px 22px; margin-bottom: 18px;
}}
h2 {{ font-size: 15px; font-weight: 620; margin: 0 0 3px; }}
.lede {{ color: var(--ink-secondary); font-size: 13px; margin: 0 0 18px; }}
.hero {{ display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }}
.hero .figure {{ font-size: 52px; font-weight: 620; letter-spacing: -0.03em; line-height: 1; }}
.hero .of {{ color: var(--ink-secondary); font-size: 14px; }}
.tiles {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }}
.tile {{ border: 1px solid var(--border); border-radius: 8px; padding: 13px 15px; }}
.tile .label {{
  font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.07em;
  color: var(--ink-muted); margin-bottom: 6px;
}}
.tile .value {{ font-size: 24px; font-weight: 620; letter-spacing: -0.02em; }}
.tile .note {{ font-size: 12px; color: var(--ink-secondary); margin-top: 3px; }}
.rows {{ display: grid; gap: 16px; }}
.row .head {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }}
.row .name {{ font-weight: 560; font-size: 13.5px; }}
.row .rank {{ color: var(--ink-muted); font-size: 12px; font-variant-numeric: tabular-nums; }}
.row .delta {{ font-size: 12.5px; margin-top: 5px; }}
.delta.good {{ color: var(--good); }}
.delta.critical {{ color: var(--critical); }}
.delta.neutral {{ color: var(--ink-secondary); }}
.chart {{ display: grid; gap: 7px; margin-top: 4px; }}
.bar-row {{ display: grid; grid-template-columns: 96px 1fr 58px; gap: 10px; align-items: center; }}
.bar-row .who {{ font-size: 12.5px; color: var(--ink-secondary); }}
.bar-row.is-you .who {{ color: var(--ink); font-weight: 600; }}
.bar-row .n {{
  font-size: 12.5px; text-align: right; font-variant-numeric: tabular-nums;
  color: var(--ink-secondary);
}}
.bar-row.is-you .n {{ color: var(--ink); font-weight: 600; }}
.track {{ position: relative; display: block; width: 100%; height: 18px; }}
.bar {{
  /* display:block matters: these are spans, and width/height do not apply to
     an inline box — without it the chart renders as labels with no bars. */
  display: block; height: 18px; background: var(--peer);
  border-radius: 0 4px 4px 0; min-width: 2px;
}}
.bar-row.is-you .bar {{ background: var(--you); }}
.you-tag {{
  position: absolute; top: 50%; transform: translateY(-50%);
  font-size: 11px; font-weight: 650;
  color: var(--ink); letter-spacing: 0.04em; text-transform: uppercase;
}}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 6px; }}
th, td {{
  text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--grid);
  white-space: nowrap;
}}
th {{
  font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--ink-muted); font-weight: 600;
}}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tr.is-you td {{ font-weight: 620; }}
.notes {{ font-size: 12.5px; color: var(--ink-secondary); }}
.notes li {{ margin-bottom: 7px; }}
.sev-blocking {{ color: var(--critical); font-weight: 600; }}
.sev-degraded {{ color: var(--ink); }}
.sev-minor, .sev-ok {{ color: var(--ink-muted); }}
footer {{ color: var(--ink-muted); font-size: 12px; margin-top: 26px; }}
@media print {{
  body {{ background: #fff; }}
  section {{ break-inside: avoid; }}
}}
"""


def _bars(scorecard: Scorecard) -> str:
    """Ranked completions by technician, with you emphasized."""
    if scorecard.team is None:
        return ""
    ranked = sorted(scorecard.team.technicians, key=lambda t: t.completed, reverse=True)
    top = max((t.completed for t in ranked), default=1) or 1
    rows = []
    for tech in ranked:
        width = tech.completed / top * 100
        # Anchor the tag just past the end of the bar rather than at its
        # static position, which is meaningless inside an absolute context.
        tag = (
            f'<span class="you-tag" style="left:calc({width:.1f}% + 8px)">you</span>'
            if tech.is_self
            else ""
        )
        rows.append(
            f'<div class="bar-row{" is-you" if tech.is_self else ""}">'
            f'<span class="who">{_e(tech.label)}</span>'
            f'<span class="track"><span class="bar" style="width:{width:.1f}%"></span>{tag}</span>'
            f'<span class="n">{tech.completed}</span>'
            f"</div>"
        )
    return "\n".join(rows)


def _comparisons(scorecard: Scorecard) -> str:
    out = []
    for c in scorecard.comparisons:
        glyph, wording, role = _delta_sentence(c)
        out.append(
            f'<div class="row">'
            f'<div class="head"><span class="name">{_e(c.metric)}</span>'
            f'<span class="rank">you {_fmt(c.mine, c.unit)} · '
            f"median {_fmt(c.department_median, c.unit)} · "
            f"rank {c.rank} of {c.of}</span></div>"
            f'<div class="delta {role}">{glyph} {_e(wording)}</div>'
            f"</div>"
        )
    return "\n".join(out)


def _team_table(scorecard: Scorecard) -> str:
    if scorecard.team is None:
        return ""
    rows = []
    for t in sorted(scorecard.team.technicians, key=lambda t: t.completed, reverse=True):
        rows.append(
            f'<tr class="{"is-you" if t.is_self else ""}">'
            f"<td>{_e(t.label)}</td>"
            f'<td class="num">{t.completed}</td>'
            f'<td class="num">{t.open_now}</td>'
            f'<td class="num">{t.median_cycle_days:.1f}</td>'
            f'<td class="num">{t.sla_met_rate:.0%}</td>'
            f'<td class="num">{t.hours_logged:.0f}</td>'
            f'<td class="num">{t.stalled_open}</td>'
            f"</tr>"
        )
    return (
        "<table><thead><tr><th>Technician</th>"
        '<th class="num">Completed</th><th class="num">Open</th>'
        '<th class="num">Median days</th><th class="num">SLA met</th>'
        '<th class="num">Hours</th><th class="num">Stalled</th>'
        "</tr></thead><tbody>" + "\n".join(rows) + "</tbody></table>"
    )


def _quality_section(quality: QualityReport | None) -> str:
    if quality is None:
        return ""
    rows = []
    for f in sorted(quality.findings, key=lambda f: f.share, reverse=True):
        if not f.missing:
            continue
        rows.append(
            f"<tr><td>{_e(f.field)}</td>"
            f'<td class="num">{f.missing} of {f.total}</td>'
            f'<td class="num">{f.share:.0%}</td>'
            f'<td class="sev-{f.severity}">{f.severity}</td>'
            f"<td style='white-space:normal'>{_e(f.costs)}</td></tr>"
        )
    if not rows:
        body = '<p class="notes">Every field the analytics need was present. Nothing to flag.</p>'
    else:
        body = (
            "<table><thead><tr><th>Missing field</th>"
            '<th class="num">Rows</th><th class="num">Share</th>'
            "<th>Impact</th><th>What it costs</th></tr></thead><tbody>"
            + "\n".join(rows)
            + "</tbody></table>"
        )
    return f"""
<section>
  <h2>What this export could not tell us</h2>
  <p class="lede">Each gap is a capability the current data does not support —
     and a concrete reason API access would help.</p>
  {body}
</section>"""


def render(
    scorecard: Scorecard,
    *,
    quality: QualityReport | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Build the complete page as a single HTML string."""
    when = (generated_at or scorecard.generated_at).strftime("%d %B %Y")
    share_pct = f"{scorecard.my_share:.0%}"
    par = scorecard.share_vs_even

    par_note = (
        f"{par:.1f}× an even split across {scorecard.department_size} technicians"
        if par
        else "no completed work in this window"
    )

    caveats = "\n".join(f"<li>{_e(c)}</li>" for c in scorecard.caveats)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Work order scorecard — {_e(scorecard.me)}</title>
<style>{_css()}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Work order scorecard — {_e(scorecard.me)}</h1>
  <div class="sub">BNSF Fort Worth facilities · trailing {scorecard.window_days} days ·
    generated {_e(when)}</div>
</header>

<section>
  <h2>Share of department output</h2>
  <p class="lede">Completed work orders attributed to you, as a share of everything
     the department closed in this window.</p>
  <div class="hero">
    <span class="figure">{share_pct}</span>
    <span class="of">{scorecard.my_completed} of {scorecard.department_completed}
      work orders · {_e(par_note)}</span>
  </div>
</section>

<section>
  <h2>Completed work orders by technician</h2>
  <p class="lede">Everyone who closed work in this window, ranked. Co-workers are
     identified only as Tech N — their names were discarded when the data was
     loaded and are not stored anywhere.</p>
  <div class="chart">
{_bars(scorecard)}
  </div>
</section>

<section>
  <h2>You against the department</h2>
  <p class="lede">Each metric compared with the median of everyone else.</p>
  <div class="rows">
{_comparisons(scorecard)}
  </div>
</section>

<section>
  <h2>Full department table</h2>
  <p class="lede">The same figures as text, so nothing here depends on reading a colour.</p>
  {_team_table(scorecard)}
  <div class="tiles" style="margin-top:18px">
    <div class="tile"><div class="label">Your open now</div>
      <div class="value">{scorecard.my_open_now}</div></div>
    <div class="tile"><div class="label">Your hours logged</div>
      <div class="value">{scorecard.my_hours:.0f}</div>
      <div class="note">in this window</div></div>
    <div class="tile"><div class="label">Department size</div>
      <div class="value">{scorecard.department_size}</div>
      <div class="note">completed ≥ 1 work order</div></div>
  </div>
</section>
{_quality_section(quality)}
<section>
  <h2>How to read this</h2>
  <ul class="notes">
{caveats}
  </ul>
</section>

<footer>
  Generated locally from a Corrigo export. No data left this machine to produce
  this page. Read-only — nothing here was written back to Corrigo.
</footer>
</div>
<script type="application/json" id="scorecard-data">
{_json_block(scorecard.to_dict())}
</script>
</body>
</html>
"""
