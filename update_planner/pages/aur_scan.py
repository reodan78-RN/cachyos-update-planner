from html import escape

from nicegui import ui

from .. import aur_scanner as sc
from .. import catalog as md
from .. import state as st
from ..i18n import _
from ..sources import aur as aur_src
from ..sysutil import NAME_RE
from ..theme import card

SEV_COLOR = sc.SEVERITY_COLOR


def code_html(text: str, marks: dict[int, str]) -> str:
    rows = []
    for no, line in enumerate(text.splitlines(), 1):
        sev = marks.get(no)
        cls = f' class="mark mark-{sev}"' if sev else ''
        rows.append(f'<div{cls}><span class="ln">{no}</span>{escape(line) or " "}</div>')
    return '<div class="code-view">' + ''.join(rows) + '</div>'


def diff_html(lines: list[tuple[str, str]]) -> str:
    if not lines:
        return '<div class="code-view"><div><span class="ln"></span>' + escape(_('Keine Änderungen gegenüber der installierten Version')) + '</div></div>'
    cls = {'+': 'add', '-': 'del', '@': 'hunk', ' ': ''}
    return '<div class="code-view">' + ''.join(
        f'<div class="{cls[k]}">{escape(l) or " "}</div>' for k, l in lines) + '</div>'


def finding_row(f: sc.Finding) -> None:
    with ui.row().classes('w-full items-start gap-3 no-wrap py-2').style('border-bottom:1px solid var(--line)'):
        ui.badge(_(f.severity), color=SEV_COLOR[f.severity]).props('rounded').classes('mt-1 min-w-[64px] justify-center')
        with ui.column().classes('gap-0 flex-1 min-w-0'):
            with ui.row().classes('items-center gap-2'):
                ui.label(_(f.title)).classes('font-medium')
                if f.new_in_update:
                    ui.badge(_('neu im Update'), color='accent').props('outline rounded')
            loc = f'{f.file}:{f.line}' if f.file else _('AUR-Metadaten')
            ui.label(f'{f.rule_id} · {_(f.category)} · {loc}').classes('muted text-xs')
            if f.code:
                ui.label(f.code).classes('code-inline mt-1')
            ui.label(_(f.advice)).classes('text-sm mt-1')


def render_findings(findings: list[sc.Finding]) -> None:
    if not findings:
        with ui.row().classes('items-center gap-2 py-4'):
            ui.icon('verified_user', color='positive', size='28px')
            ui.label(_('Keine verdächtigen Muster gefunden.'))
        return
    for f in findings:
        finding_row(f)


async def custom_scan_dialog() -> None:
    with ui.dialog() as dialog, ui.card().classes('glass p-6').style('width:860px;max-width:96vw').props('flat'):
        with ui.row().classes('items-center gap-3'):
            ui.icon('content_paste_search', size='28px').classes('text-primary')
            ui.label(_('Eigenes PKGBUILD prüfen')).classes('text-xl font-semibold')
        ui.label(_('PKGBUILD oder .install-Datei einfügen – die Prüfung läuft lokal mit denselben Regeln.')) \
            .classes('muted text-sm')
        text = ui.textarea(placeholder='pkgname=…').props('outlined autogrow input-style="min-height:180px;font-family:monospace;font-size:.8rem"') \
            .classes('w-full')
        result = ui.column().classes('w-full gap-0')

        def run() -> None:
            result.clear()
            findings = sorted(sc.scan_text(text.value or '', 'PKGBUILD'),
                              key=lambda f: (sc.SEVERITY_ORDER.index(f.severity), f.line))
            r = sc.ScanResult('eigenes', findings)
            verdict, color, icon = r.verdict
            with result:
                with ui.row().classes('items-center gap-2 my-2'):
                    ui.icon(icon, color=color, size='26px')
                    ui.label(f'{_(verdict)} · Score {r.score}').classes('font-semibold')
                render_findings(r.findings)

        with ui.row().classes('w-full justify-end gap-2'):
            ui.button(_('Schließen'), on_click=dialog.close).props('flat')
            ui.button(_('Prüfen'), icon='policy', on_click=run).props('unelevated')
    dialog.open()


def page() -> None:
    s = st.get()
    current = {'name': ''}

    @ui.refreshable
    def content() -> None:
        visible = 'aur' in s.sources
        results = sorted((r for r in md.SCANS.values() if r), key=lambda r: -r.score)
        if results and not md.SCANS.get(current['name']):  # auch None-Einträge (Scan ohne Metadaten)
            current['name'] = results[0].package

        with ui.row().classes('w-full items-end justify-between'):
            with ui.column().classes('gap-0'):
                ui.label(_('AUR-Sicherheitsscanner')).classes('text-2xl font-bold')
                ui.label(_('{n} Regeln · PKGBUILD, .install und AUR-Metadaten · statische Analyse, kein Code wird ausgeführt',
                           n=len(sc.RULES) + 7)).classes('muted text-sm')
            with ui.row().classes('gap-2 items-center'):
                lookup = ui.input(placeholder=_('AUR-Paket prüfen, z. B. yay-bin')).props('dense outlined rounded') \
                    .classes('w-64')
                lookup_btn = ui.button(icon='travel_explore').props('flat round').tooltip(_('Aus dem AUR laden und scannen'))
                ui.button(_('Eigenes PKGBUILD'), icon='content_paste_search', on_click=custom_scan_dialog) \
                    .props('flat rounded no-caps')
                rescan_btn = ui.button(_('Alle neu scannen'), icon='radar').props('unelevated rounded no-caps')

        if not visible:
            with card():
                ui.label(_('AUR ist im Quellenfilter ausgeblendet.')).classes('muted')
            return

        bar = ui.linear_progress(0, show_value=False, size='4px').props('rounded').classes('w-full')
        bar.set_visibility(False)

        async def rescan() -> None:
            if not md.AUR_META:
                ui.notify(_('Keine AUR-Pakete zum Scannen'), position='top-right')
                return
            rescan_btn.props('loading')
            bar.set_visibility(True)
            bar.value = 0.3
            try:
                if not md.STATUS.get('demo'):
                    fresh = await aur_src.scan_inputs(list(md.AUR_META))
                    for name, meta in fresh.items():   # Markierung „nur geprüft“ erhalten
                        if md.AUR_META.get(name, {}).get('lookup'):
                            meta['lookup'] = True
                    md.AUR_META.update(fresh)
                md.SCANS.update(sc.scan_all(md.AUR_META))
                ui.notify(_('{n} AUR-Pakete geprüft', n=len(md.AUR_META)), type='positive', position='top-right')
            except Exception as exc:  # noqa: BLE001
                ui.notify(_('Scan fehlgeschlagen: {err}', err=exc), type='negative', position='top-right')
            finally:
                rescan_btn.props(remove='loading')
            content.refresh()
        rescan_btn.on_click(rescan)

        async def lookup_pkg() -> None:
            name = (lookup.value or '').strip()
            if not NAME_RE.match(name):
                ui.notify(_('Ungültiger Paketname'), type='warning', position='top-right')
                return
            lookup_btn.props('loading')
            try:
                metas = await aur_src.scan_inputs([name])
            except Exception as exc:  # noqa: BLE001
                metas = {}
                ui.notify(_('AUR nicht erreichbar: {err}', err=exc), type='negative', position='top-right')
            lookup_btn.props(remove='loading')
            if name not in metas:
                ui.notify(_('{pkg} nicht im AUR gefunden', pkg=name), type='warning', position='top-right')
                return
            md.AUR_META[name] = metas[name] | {'lookup': True}
            md.SCANS[name] = sc.scan_package(name, md.AUR_META[name])
            current['name'] = name
            content.refresh()
        lookup_btn.on_click(lookup_pkg)
        lookup.on('keydown.enter', lookup_pkg)

        if not results:
            with card('w-full items-center py-8'):
                ui.icon('verified_user', color='positive', size='40px')
                ui.label(_('Keine AUR-Pakete mit Updates installiert')).classes('text-lg font-semibold mt-2')
                ui.label(_('Pakete vor der Installation prüfen: Namen oben eingeben oder ein PKGBUILD einfügen.')) \
                    .classes('muted text-sm')
            return

        with ui.grid().classes('w-full gap-4 grid-cols-2 md:grid-cols-4'):
            for verdict, color, icon, lo, hi in [('Gefährlich', 'negative', 'gpp_bad', 70, 101),
                                                 ('Manuell prüfen', 'warning', 'gpp_maybe', 40, 70),
                                                 ('Auffällig', 'info', 'policy', 15, 40),
                                                 ('Unbedenklich', 'positive', 'verified_user', 0, 15)]:
                with card('items-center'):
                    ui.icon(icon, color=color, size='26px')
                    ui.label(str(sum(1 for r in results if lo <= r.score < hi))).classes('kpi')
                    ui.label(_(verdict)).classes('muted text-sm')

        with ui.grid().classes('w-full gap-4 grid-cols-1 xl:grid-cols-7'):
            with ui.column().classes('xl:col-span-2 gap-3'):
                for r in results:
                    verdict, color, icon = r.verdict
                    upd = md.update(r.package)
                    active = r.package == current['name']
                    with card('w-full cursor-pointer p-4' + (' ring-2 ring-teal-400' if active else '')) \
                            .on('click', lambda n=r.package: select(n)):
                        with ui.row().classes('w-full items-center no-wrap gap-3'):
                            ui.circular_progress(r.score, max=100, size='46px', show_value=True,
                                                 color={'negative': 'red-5', 'warning': 'amber-6',
                                                        'info': 'light-blue-5', 'positive': 'green-5'}[color]) \
                                .props('thickness=0.18 track-color=grey-4 font-size=13px')
                            with ui.column().classes('gap-0 flex-1 min-w-0'):
                                ui.label(r.package).classes('font-semibold truncate')
                                ui.label((f'{upd["old"]} → {upd["new"]}' if upd else (_('nur geprüft – nicht installiert') if md.AUR_META.get(r.package, {}).get('lookup') else _('kein Update')))).classes('muted text-xs')
                                with ui.row().classes('gap-1 mt-1 items-center'):
                                    ui.icon(icon, color=color, size='16px')
                                    ui.label(_(verdict)).classes('text-xs')
                            with ui.column().classes('gap-1 items-end'):
                                for sev in ('kritisch', 'hoch', 'mittel'):
                                    if r.count(sev):
                                        ui.badge(f'{r.count(sev)} {_(sev)}', color=SEV_COLOR[sev]).props('rounded')

            detail = ui.column().classes('xl:col-span-5 gap-4')

        def select(name: str) -> None:
            current['name'] = name
            content.refresh()

        r = md.SCANS[current['name']]
        meta = md.AUR_META.get(r.package, {})
        verdict, color, icon = r.verdict
        with detail:
            with card('w-full'):
                with ui.row().classes('w-full items-center gap-4 no-wrap'):
                    ui.icon(icon, color=color, size='44px')
                    with ui.column().classes('gap-0 flex-1'):
                        ui.label(r.package).classes('text-xl font-bold')
                        ui.label(_('{verdict} · Scan-Score {score}/100 · {n} Befunde', verdict=_(verdict), score=r.score, n=len(r.findings))).classes('text-sm')
                    ui.link(_('AUR-Seite'), f'https://aur.archlinux.org/packages/{r.package}', new_tab=True) \
                        .classes('text-primary text-sm')
                with ui.grid().classes('w-full gap-2 mt-3 grid-cols-2 md:grid-cols-5 text-sm'):
                    for label, value in [
                        (_('Maintainer'), meta.get('maintainer') or _('— verwaist —')),
                        (_('Stimmen'), meta.get('votes', '–')),
                        (_('Popularität'), f'{meta.get("popularity", 0):.2f}'),
                        (_('Zuletzt geändert'), meta.get('last_modified') or '–'),
                        (_('Maintainer-Wechsel'), meta.get('maintainer_changed') or '—'),
                    ]:
                        with ui.column().classes('gap-0'):
                            ui.label(label).classes('muted text-xs')
                            ui.label(str(value)).classes('font-medium')

                with ui.row().classes('w-full justify-end gap-2 mt-3'):
                    def hold():
                        s.selected.discard(r.package)
                        ui.notify(_('{pkg} vom Update ausgeschlossen', pkg=r.package), position='top-right')

                    def reviewed():
                        s.reviewed.add(r.package)
                        ui.notify(_('{pkg} als manuell geprüft markiert', pkg=r.package), type='positive', position='top-right')
                        content.refresh()
                    if r.package in s.reviewed:
                        ui.chip(_('manuell geprüft'), icon='task_alt', color='positive').props('outline')
                    ui.button(_('Vom Update ausschließen'), icon='block', on_click=hold).props('flat rounded no-caps')
                    ui.button(_('Als geprüft markieren'), icon='task_alt', on_click=reviewed).props('flat rounded no-caps')

            with card('w-full p-2'):
                with ui.tabs().props('dense align=left no-caps active-color=primary indicator-color=primary') as tabs:
                    t_find = ui.tab('findings', label=_('Befunde'), icon='report')
                    t_code = ui.tab('files', label=_('Dateien'), icon='description')
                    t_diff = ui.tab('diff', label=_('Änderungen im Update'), icon='difference')
                with ui.tab_panels(tabs, value=t_find).classes('w-full bg-transparent'):
                    with ui.tab_panel(t_find):
                        render_findings(r.findings)
                    with ui.tab_panel(t_code):
                        for fname, (_old, new) in r.files.items():
                            marks = {}
                            for f in r.findings:
                                if f.file == fname and f.line and (
                                        sc.SEVERITY_ORDER.index(f.severity)
                                        < sc.SEVERITY_ORDER.index(marks.get(f.line, 'info'))):
                                    marks[f.line] = f.severity
                            ui.label(fname).classes('section-title mt-2 mb-1')
                            ui.html(code_html(new, marks), sanitize=False).classes('w-full')
                    with ui.tab_panel(t_diff):
                        for fname, (old, new) in r.files.items():
                            ui.label(fname + (_(' (neu hinzugekommen)') if not old else '')).classes('section-title mt-2 mb-1')
                            ui.html(diff_html(sc.diff(old, new, fname)), sanitize=False).classes('w-full')

    content()
    s.on_change = content.refresh
