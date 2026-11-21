"""Dashboard-Optik nach dem Vorbild von Splunk: flache Panels, dichte Tabellen, Schwellenfarben.

Farbe trägt Bedeutung (Schwellenwerte, Schweregrade), nicht Dekoration. Die beiden Navigationsleisten
bleiben in hellem und dunklem Design dunkel, wie bei Splunk Enterprise.
"""

from __future__ import annotations

from contextlib import contextmanager

from nicegui import ui

# Schwellen- und Schweregradfarben (Splunk-Bereichsfarben)
GREEN, BLUE, YELLOW, ORANGE, RED, GREY = '#53a051', '#1e93c6', '#f8be34', '#f1813f', '#dc4e41', '#8d96a0'
PRIMARY = '#3f8f3f'
SERIES = ['#1e93c6', '#f2b827', '#d6563c', '#6a5c9e', '#31a35f', '#ed8440', '#3863a0', '#a2cc3e']

SEVERITY_COLOR = {'kritisch': RED, 'hoch': ORANGE, 'mittel': YELLOW, 'niedrig': BLUE, 'info': GREY, 'unbekannt': GREY}
TRACKER_SEVERITY_COLOR = {'Critical': RED, 'High': ORANGE, 'Medium': YELLOW, 'Low': BLUE}

CSS = r"""
:root {
  --bar: #0e1013; --nav: #1b1f24; --nav-text: #c3cbd4; --nav-active: #ffffff;
  --page: #eceef0; --panel: #ffffff; --panel-head: #f7f8fa; --border: #d4d9de; --row-alt: #f5f6f8;
  --text: #2b3238; --muted: #6b7682; --link: #1a73b8; --line: #e3e7eb;
  --code-bg: #f1f3f5;
}
body.body--dark {
  --page: #171b1f; --panel: #21262c; --panel-head: #252b32; --border: #2e353d; --row-alt: #252a31;
  --text: #e3e7eb; --muted: #8d96a0; --link: #4fa3e0; --line: #2e353d;
  --code-bg: #171b1f;
}
html, body { background: var(--page) !important; }
body {
  color: var(--text);
  font-family: 'Fira Sans', 'Noto Sans', 'Open Sans', system-ui, sans-serif;
  font-size: 13px; font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased;
}
a, .q-link { color: var(--link); }
.muted { color: var(--muted); }

/* ---------- Kopf: Systemleiste + App-Navigation ---------- */
.q-header { background: transparent !important; box-shadow: none !important; }
.sysbar { background: var(--bar); color: #e9edf1; height: 40px; padding: 0 16px; }
.sysbar .q-btn { color: #c3cbd4; }
.sysbar .app-name { font-weight: 600; font-size: 14px; letter-spacing: .01em; }
.sysbar .meta { color: #8d96a0; font-size: 12px; }
.appnav { background: var(--nav); height: 38px; padding: 0 8px; border-bottom: 1px solid #000; }
.appnav { scrollbar-width: none; }
.appnav::-webkit-scrollbar { display: none; }
.appnav .nav-item { color: var(--nav-text); font-size: 13px; padding: 0 14px; height: 38px; line-height: 38px;
                    border-bottom: 3px solid transparent; cursor: pointer; user-select: none; white-space: nowrap; }
.appnav .nav-item:hover { color: var(--nav-active); }
.appnav .nav-item.active { color: var(--nav-active); border-bottom-color: #5cc05c; font-weight: 500; }
.appnav .nav-item:focus-visible { outline: 2px solid #4fa3e0; outline-offset: -2px; }
.src-select .q-field__control { min-height: 28px !important; height: 28px; background: #2a3037 !important;
                                border-radius: 2px !important; padding: 0 8px; }
.src-select .q-field__native, .src-select .q-field__marginal, .src-select .q-field__label { color: #e3e7eb !important;
                                min-height: 28px !important; font-size: 12px; }
.src-select .q-field__prefix { color: #8d96a0 !important; padding-right: 6px; font-size: 12px; }
.src-select .q-field__control:before, .src-select .q-field__control:after { border: none !important; }

/* ---------- Dashboard-Kopf ---------- */
.dash-title { font-size: 22px; font-weight: 400; line-height: 1.2; }
.dash-desc { color: var(--muted); font-size: 13px; max-width: 80ch; }

/* ---------- Panels ---------- */
.panel, .glass {
  background: var(--panel) !important; border: 1px solid var(--border); border-radius: 2px !important;
  box-shadow: none !important;
}
.panel { padding: 0 !important; gap: 0 !important; }
.panel-head { padding: 8px 12px 6px; display: flex; align-items: baseline; gap: 12px; width: 100%; }
.panel-title { font-size: 14px; font-weight: 600; }
.panel-meta { color: var(--muted); font-size: 12px; margin-left: auto; }
.panel-body { padding: 4px 12px 12px; width: 100%; }
.section-title { font-size: 14px; font-weight: 600; }

/* Single Value */
.sv { padding: 10px 12px 12px; width: 100%; }
.sv-label { font-size: 13px; color: var(--muted); }
.sv-value { font-family: 'Fira Sans Condensed', 'Fira Sans', sans-serif; font-weight: 600; font-size: 46px;
            line-height: 1.05; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.sv-caption { font-size: 12px; color: var(--muted); margin-top: 2px; }
.kpi { font-family: 'Fira Sans Condensed', 'Fira Sans', sans-serif; font-size: 34px; font-weight: 600; line-height: 1.1; }

/* ---------- Tabellen ---------- */
.q-table__container { background: transparent !important; box-shadow: none !important; border-radius: 0 !important; }
.q-table { color: var(--text); }
.q-table thead tr { background: var(--panel-head); }
.q-table thead th { font-size: 12px; font-weight: 600; color: var(--text); padding: 6px 10px !important;
                    height: 30px !important; border-bottom: 1px solid var(--border) !important; }
.q-table tbody td { font-size: 13px; padding: 4px 10px !important; height: 30px !important;
                    border-bottom: 1px solid var(--line) !important; }
.q-table tbody tr:nth-child(even) { background: var(--row-alt); }
.q-table tbody tr:hover { background: rgba(30,147,198,.10) !important; }
.q-table__bottom { font-size: 12px; color: var(--muted); border-top: 1px solid var(--line) !important; min-height: 36px; }
.cell-num { display: inline-block; min-width: 36px; padding: 1px 6px; text-align: right; font-weight: 600; color: #111; border-radius: 1px; }

/* ---------- Bedienelemente ---------- */
.q-btn { text-transform: none !important; border-radius: 2px !important; font-weight: 500; letter-spacing: 0; }
.q-btn--unelevated.bg-primary { background: var(--primary-btn, #3f8f3f) !important; }
.q-field--outlined .q-field__control { border-radius: 2px !important; }
.q-badge { border-radius: 2px !important; font-weight: 600; font-size: 11px; padding: 2px 6px; }
.q-chip { border-radius: 2px !important; }
.q-tab { text-transform: none !important; }
.q-tab__label { font-size: 13px; }
.q-dialog__inner > .q-card { border-radius: 2px !important; }
.q-notification { border-radius: 2px !important; }
.q-linear-progress { border-radius: 0 !important; }
:focus-visible { outline: 2px solid #4fa3e0; outline-offset: 1px; }

/* Hinweiszeile: farbiger linker Rand nach Bedeutung */
.notice { background: var(--panel-head); border: 1px solid var(--border); border-left: 3px solid var(--muted);
          border-radius: 2px; padding: 8px 12px; }

/* Felder im Splunk-Stil: Schlüssel und Wert */
.fields { display: flex; flex-wrap: wrap; gap: 4px 18px; font-size: 13px; }
.field-key { color: var(--muted); margin-right: 4px; }
.field-val { font-weight: 600; }

/* Sicherheits-Ereignisse */
.event { border-top: 1px solid var(--line); padding: 10px 12px 12px; width: 100%; }
.event:first-child { border-top: none; }
.event-pkg { font-size: 20px; font-weight: 600; line-height: 1.15; }
.event-title { font-size: 14px; }
.sev-stripe { width: 4px; align-self: stretch; border-radius: 1px; flex-shrink: 0; }
.desc { max-width: 110ch; line-height: 1.5; }

/* Log, Code, Diff */
.log-box { font-family: 'Hack', 'Noto Sans Mono', ui-monospace, monospace; font-size: .78rem; height: 190px;
           background: var(--code-bg); border: 1px solid var(--border); border-radius: 2px; padding: 6px 8px; }
.code-view { font-family: 'Hack', 'Noto Sans Mono', ui-monospace, monospace; font-size: .78rem; line-height: 1.55;
             background: var(--code-bg); border: 1px solid var(--border); border-radius: 2px; padding: 6px 0; overflow-x: auto; }
.code-view > div { white-space: pre; padding: 0 12px; }
.code-view .ln { display: inline-block; width: 2.6em; color: var(--muted); user-select: none; }
.code-view .mark { border-left: 3px solid; }
.code-view .mark-kritisch { background: rgba(220,78,65,.20); border-color: #dc4e41; }
.code-view .mark-hoch { background: rgba(241,129,63,.18); border-color: #f1813f; }
.code-view .mark-mittel { background: rgba(248,190,52,.16); border-color: #f8be34; }
.code-view .mark-niedrig { background: rgba(30,147,198,.14); border-color: #1e93c6; }
.code-view .add { background: rgba(83,160,81,.18); }
.code-view .del { background: rgba(220,78,65,.16); }
.code-view .hunk { color: #1e93c6; }
.code-inline { font-family: 'Hack', 'Noto Sans Mono', ui-monospace, monospace; font-size: .75rem; background: var(--code-bg);
               border: 1px solid var(--line); padding: 2px 8px; border-radius: 2px; max-width: 100%; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; display: block; }
.risk-bar { height: 6px; background: var(--line); overflow: hidden; min-width: 90px; }
.risk-bar > div { height: 100%; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
"""


def apply() -> None:
    ui.colors(primary=PRIMARY, secondary=BLUE, accent=ORANGE, positive=GREEN, negative=RED, warning=YELLOW,
              info=BLUE, dark='#21262c', dark_page='#171b1f')
    ui.add_css(CSS)  # Systemschriften – das Tool lädt nichts aus dem Netz


def threshold(score: float, warn: float = 30, crit: float = 60) -> str:
    """Farbe für einen Wert nach Schwellen (Splunk-Bereichsfarben)."""
    return RED if score >= crit else ORANGE if score >= (warn + crit) / 2 else YELLOW if score >= warn else GREEN


@contextmanager
def panel(title: str | None = None, meta: str | None = None, classes: str = ''):
    """Flaches Panel mit Titelzeile. Inhalt landet im Körper."""
    with ui.column().classes(f'panel {classes}') as box:
        if title or meta:
            with ui.element('div').classes('panel-head'):
                if title:
                    ui.label(title).classes('panel-title')
                if meta:
                    ui.label(meta).classes('panel-meta')
        with ui.column().classes('panel-body gap-2'):
            yield box


def card(classes: str = ''):
    """Panel ohne Titelzeile (für Seiten, die ihren Inhalt selbst gliedern)."""
    return ui.column().classes(f'panel p-3 gap-2 {classes}').style('padding:12px !important')


def single_value(label: str, value, color: str | None = None, caption: str | None = None,
                 caption2: str | None = None, caption2_color: str | None = None) -> None:
    with ui.column().classes('panel'):
        with ui.column().classes('sv gap-0'):
            ui.label(label).classes('sv-label')
            ui.label(str(value)).classes('sv-value').style(f'color:{color}' if color else '')
            if caption:
                ui.label(caption).classes('sv-caption')
            if caption2:
                ui.label(caption2).classes('sv-caption').style(f'color:{caption2_color}' if caption2_color else '')


def badge(text: str, color: str) -> None:
    dark_text = color in (YELLOW, GREEN, ORANGE)
    ui.label(text).classes('q-badge').style(f'background:{color};color:{"#111" if dark_text else "#fff"};display:inline-block')


def chart_base(dark: bool | None = None) -> dict:
    """Gemeinsame ECharts-Optionen: Splunk-Palette, dezente Achsen, transparent."""
    axis = '#8d96a0'
    grid = 'rgba(141,150,160,.18)'
    return {
        'backgroundColor': 'transparent', 'color': SERIES, 'animation': False,
        'textStyle': {'fontFamily': 'Fira Sans, Noto Sans, sans-serif', 'color': axis, 'fontSize': 11},
        'grid': {'left': 36, 'right': 12, 'top': 16, 'bottom': 28},
        'tooltip': {'trigger': 'axis', 'backgroundColor': '#0e1013', 'borderWidth': 0,
                    'textStyle': {'color': '#e3e7eb', 'fontSize': 12}},
        'xAxis': {'axisLine': {'lineStyle': {'color': grid}}, 'axisTick': {'show': False},
                  'axisLabel': {'color': axis}},
        'yAxis': {'splitLine': {'lineStyle': {'color': grid}}, 'axisLabel': {'color': axis}},
    }


def risk_bar(score: int) -> None:
    ui.html(f'<div class="risk-bar"><div style="width:{score}%;background:{threshold(score)}"></div></div>',
            sanitize=False)


def risk_cell(score: int) -> None:
    ui.html(f'<span class="cell-num" style="background:{threshold(score)}">{int(score)}</span>', sanitize=False)
