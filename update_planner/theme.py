"""Leichtes, schwebendes Glas-Design für hell und dunkel."""

from nicegui import ui

ACCENT = '#14b8a6'  # CachyOS-Türkis

CSS = r"""
:root {
  --bg-1: #eef6f6; --bg-2: #f6f3ff;
  --glass: rgba(255,255,255,.62); --glass-strong: rgba(255,255,255,.85);
  --glass-border: rgba(255,255,255,.9);
  --text: #0f172a; --muted: #64748b; --line: rgba(15,23,42,.08);
  --shadow: 0 10px 30px -12px rgba(15,23,42,.18), 0 2px 6px -2px rgba(15,23,42,.06);
}
body.body--dark {
  --bg-1: #0b1214; --bg-2: #141225;
  --glass: rgba(30,36,48,.55); --glass-strong: rgba(30,36,48,.85);
  --glass-border: rgba(255,255,255,.07);
  --text: #e2e8f0; --muted: #94a3b8; --line: rgba(255,255,255,.07);
  --shadow: 0 12px 34px -12px rgba(0,0,0,.6), 0 2px 6px -2px rgba(0,0,0,.4);
}
body {
  color: var(--text);
  background:
    radial-gradient(1200px 600px at 10% -10%, rgba(20,184,166,.18), transparent 60%),
    radial-gradient(900px 600px at 110% 10%, rgba(99,102,241,.16), transparent 60%),
    linear-gradient(160deg, var(--bg-1), var(--bg-2)) !important;
  background-attachment: fixed !important;
  font-family: 'Inter', system-ui, sans-serif;
}
.glass {
  background: var(--glass) !important;
  backdrop-filter: blur(18px) saturate(140%);
  -webkit-backdrop-filter: blur(18px) saturate(140%);
  border: 1px solid var(--glass-border);
  border-radius: 18px !important;
  box-shadow: var(--shadow) !important;
}
.lift { transition: transform .18s ease, box-shadow .18s ease; }
.lift:hover { transform: translateY(-3px); }
.muted { color: var(--muted); }
.kpi { font-size: 2rem; font-weight: 700; line-height: 1.1; }
.section-title { font-size: 1.05rem; font-weight: 600; }
.q-header { background: transparent !important; }
.q-drawer { background: transparent !important; }
.nav-btn { border-radius: 14px !important; width: 100%; justify-content: flex-start; }
.nav-active { background: rgba(20,184,166,.16) !important; color: #0d9488 !important; }
body.body--dark .nav-active { color: #5eead4 !important; }
.q-table__container { background: transparent !important; box-shadow: none !important; }
.q-table thead tr, .q-table tbody td { border-color: var(--line) !important; }
.risk-bar { height: 8px; border-radius: 99px; background: var(--line); overflow: hidden; min-width: 90px; }
.risk-bar > div { height: 100%; border-radius: 99px; }
.q-dialog__inner > .q-card { border-radius: 22px !important; }
.log-box { font-family: 'JetBrains Mono', monospace; font-size: .78rem; height: 170px;
           background: rgba(15,23,42,.06); border-radius: 12px; padding: 8px 10px; }
body.body--dark .log-box { background: rgba(0,0,0,.3); }
.code-view { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: .78rem; line-height: 1.55;
             background: rgba(15,23,42,.05); border-radius: 12px; padding: 8px 0; overflow-x: auto; }
body.body--dark .code-view { background: rgba(0,0,0,.28); }
.code-view > div { white-space: pre; padding: 0 12px; }
.code-view .ln { display: inline-block; width: 2.4em; color: var(--muted); user-select: none; }
.code-view .mark { border-left: 3px solid; }
.code-view .mark-kritisch { background: rgba(168,85,247,.16); border-color: #a855f7; }
.code-view .mark-hoch { background: rgba(239,68,68,.14); border-color: #ef4444; }
.code-view .mark-mittel { background: rgba(245,158,11,.14); border-color: #f59e0b; }
.code-view .mark-niedrig { background: rgba(56,189,248,.12); border-color: #38bdf8; }
.code-view .add { background: rgba(34,197,94,.14); color: #15803d; }
.code-view .del { background: rgba(239,68,68,.12); color: #b91c1c; }
body.body--dark .code-view .add { color: #86efac; }
body.body--dark .code-view .del { color: #fca5a5; }
.code-view .hunk { color: #6366f1; }
.code-inline { font-family: ui-monospace, monospace; font-size: .75rem; background: rgba(15,23,42,.06);
               padding: 2px 8px; border-radius: 6px; max-width: 100%; overflow: hidden; text-overflow: ellipsis;
               white-space: nowrap; display: block; }
body.body--dark .code-inline { background: rgba(0,0,0,.3); }
.chip-src { border-radius: 99px !important; transition: opacity .15s; }
.chip-src:not(.q-chip--selected) { opacity: .4; background: transparent !important; }
"""


def apply() -> None:
    ui.colors(primary=ACCENT, secondary='#6366f1', accent='#f59e0b',
              dark='#1e2430', dark_page='#0b1214')
    ui.add_css(CSS)  # Systemschriften – das Tool lädt nichts aus dem Netz


def card(classes: str = ''):
    return ui.card().classes(f'glass lift p-5 {classes}').props('flat')


def risk_bar(score: int) -> None:
    color = '#ef4444' if score >= 60 else '#f59e0b' if score >= 30 else '#22c55e'
    ui.html(f'<div class="risk-bar"><div style="width:{score}%;background:{color}"></div></div>',
            sanitize=False)
