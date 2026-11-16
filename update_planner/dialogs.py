"""Popups: Kontextabfrage, Passwort, Rückfragen und Fortschritt des echten Update-Laufs."""

from __future__ import annotations


from nicegui import background_tasks, ui

from . import catalog as md
from . import config, executor
from .i18n import _
from .state import State


def _dialog_card(width: str = '520px', persistent: bool = False):
    dialog = ui.dialog().props('persistent' if persistent else '')
    with dialog:
        card = ui.card().classes('glass p-6').style(f'width:{width};max-width:94vw').props('flat')
    return dialog, card


def expand_holds(names: set[str]) -> tuple[set[str], set[str]]:
    """Zurückgehaltene Repo-Pakete um ihre „nur gemeinsam“-Gruppen erweitern. (alle, zusätzlich)"""
    out = set(names)
    for g in md.GROUPS:
        if out & set(g):
            out |= set(g)
    return out, out - set(names)


def build_plan(state: State, opts: dict) -> tuple[executor.Plan, list[str]]:
    sel = state.selected
    repo = [u for u in md.UPDATES if u['source'] == 'repo']
    aur = [u for u in md.UPDATES if u['source'] == 'aur']
    fp = [u for u in md.UPDATES if u['source'] == 'flatpak']
    notes = []
    hold = {u['name'] for u in repo if u['name'] not in sel or u['risk'] >= opts['hold']
            or 'repo' not in state.sources}
    hold, extra = expand_holds(hold)
    if extra:
        notes.append(_('Wegen gemeinsamer Abhängigkeiten zusätzlich zurückgehalten: {pkgs}', pkgs=', '.join(sorted(extra)[:8]))
                     + (' …' if len(extra) > 8 else ''))
    run_repo = len(hold) < len(repo) or (not repo and opts['sync'])
    plan = executor.Plan(
        repo=run_repo, hold=sorted(hold) if run_repo else [],
        aur=[(u['name'], u.get('base', u['name'])) for u in aur
             if opts['aur'] and u['name'] in sel and u['risk'] < opts['hold']],
        flatpak=[u['name'] for u in fp if opts['flatpak'] and u['name'] in sel and u['risk'] < opts['hold']],
        snapshot=opts['snapshot'], dry_run=opts['dry_run'], reviewed=set(state.reviewed),
    )
    return plan, notes


async def context_dialog(state: State) -> dict | None:
    updates = state.selected_updates()
    if not updates:
        ui.notify(_('Keine Updates ausgewählt'), type='warning')
        return None
    size = sum(u['size'] for u in updates)
    top = max(updates, key=lambda u: u['risk'])
    level, color = md.risk_level(top['risk'])
    reboot = any(config.matches(u['name'], config.REBOOT) for u in updates if u['source'] == 'repo')
    sysinfo = md.SYSTEM

    dialog, card = _dialog_card()
    with card:
        with ui.row().classes('items-center gap-3'):
            ui.icon('rocket_launch', size='28px').classes('text-primary')
            ui.label(_('Update planen')).classes('text-xl font-semibold')
        with ui.grid(columns=3).classes('w-full gap-3 my-3'):
            for value, label in [(len(updates), _('Pakete')), (f'{size:.0f} MiB', _('Download')), (top['risk'], _('max. Risiko'))]:
                with ui.column().classes('glass p-3 items-center gap-0').style('border-radius:14px'):
                    ui.label(str(value)).classes('text-lg font-bold')
                    ui.label(label).classes('muted text-xs')
        with ui.row().classes('items-center gap-2'):
            ui.badge(_('Höchstes Risiko: {pkg} ({level})', pkg=top['name'], level=_(level)), color=color)
            if reboot:
                ui.badge(_('Neustart nötig'), color='secondary')
        flagged = [u['name'] for u in updates
                   if (scan := md.SCANS.get(u['name'])) and scan.score >= 40 and u['name'] not in state.reviewed]
        manual = [n for n in md.NEWS if n['manual'] and n.get('new')]
        for icon, text, link in ([('gpp_bad', _('AUR-Scanner meldet verdächtige Pakete: {pkgs}', pkgs=', '.join(flagged)), '/aur-scan')] if flagged else []) + \
                [('front_hand', _('News mit manuellem Eingriff: {title}', title=n['title']), n['link']) for n in manual]:
            with ui.row().classes('w-full items-start gap-2 mt-3 p-3 no-wrap') \
                    .style('background:rgba(239,68,68,.10);border-radius:12px'):
                ui.icon(icon, color='negative')
                with ui.column().classes('gap-0'):
                    ui.label(text).classes('text-sm font-medium')
                    if link.startswith('/'):
                        ui.link(_('Ansehen'), link).classes('text-sm text-primary').on('click', lambda: dialog.submit(None))
                    elif link:
                        ui.link(_('Meldung öffnen'), link, new_tab=True).classes('text-sm text-primary')
        ui.separator().classes('my-3')
        snap_text = (_('Snapshot: snap-pac legt pre/post automatisch an') if sysinfo.get('snap_pac')
                     else _('Btrfs-Snapshot vorher anlegen (Snapper)'))
        snapshot = ui.switch(snap_text, value=bool(sysinfo.get('snapper') or sysinfo.get('snap_pac')))
        if sysinfo.get('snap_pac') or not sysinfo.get('snapper'):
            snapshot.disable()
        aur = ui.switch(_('AUR-Pakete einbeziehen'), value='aur' in state.sources)
        flatpak = ui.switch(_('Flatpak-Pakete einbeziehen'), value='flatpak' in state.sources)
        dry = ui.switch(_('Probelauf – nichts installieren'), value=False)
        with ui.row().classes('w-full items-center gap-3 mt-2'):
            ui.label(_('Zurückhalten ab Risiko')).classes('text-sm')
            hold = ui.slider(min=30, max=100, step=5, value=100).classes('flex-1').props('label-always')
        ui.label(_('Zurückgehaltene Repo-Pakete werden per --ignore ausgelassen. Teil-Updates unterstützt Arch nicht – '
                   'nur kurzfristig nutzen.')).classes('muted text-xs mt-1')
        with ui.row().classes('w-full justify-end gap-2 mt-4'):
            ui.button(_('Abbrechen'), on_click=lambda: dialog.submit(None)).props('flat')
            ui.button(_('Weiter'), icon='arrow_forward', on_click=lambda: dialog.submit({
                'snapshot': snapshot.value, 'aur': aur.value, 'flatpak': flatpak.value, 'hold': hold.value,
                'dry_run': dry.value, 'sync': False,
            })).props('unelevated')
    result = await dialog
    dialog.delete()
    return result


async def password_dialog() -> str | None:
    """Fragt das sudo-Passwort ab und prüft es sofort (sudo -v). Liefert es nur an den Aufrufer zurück."""
    dialog, card = _dialog_card('420px')
    with card:
        with ui.row().classes('items-center gap-3'):
            ui.icon('lock', size='28px').classes('text-primary')
            ui.label(_('Administrator-Rechte')).classes('text-xl font-semibold')
        ui.label(_('Für pacman wird Ihr sudo-Passwort benötigt. Es wird nur für diesen Lauf im Arbeitsspeicher '
                   'gehalten und nicht gespeichert.')).classes('muted text-sm')
        pw = ui.input(_('Passwort'), password=True, password_toggle_button=True).classes('w-full') \
            .props('outlined autofocus autocomplete=off')
        error = ui.label('').classes('text-negative text-sm')
        tries = {'n': 0}

        async def ok():
            value = pw.value or ''
            if not value:
                error.text = _('Bitte Passwort eingeben')
                return
            btn.props('loading')
            valid = await executor.check_password(value)
            btn.props(remove='loading')
            if valid:
                pw.value = ''
                dialog.submit(value)
                return
            tries['n'] += 1
            pw.value = ''
            error.text = _('Falsches Passwort') if tries['n'] < 3 else _('Drei Fehlversuche – abgebrochen')
            if tries['n'] >= 3:
                dialog.submit(None)

        pw.on('keydown.enter', ok)
        with ui.row().classes('w-full justify-end gap-2 mt-3'):
            ui.button(_('Abbrechen'), on_click=lambda: dialog.submit(None)).props('flat')
            btn = ui.button(_('Authentifizieren'), icon='key', on_click=ok).props('unelevated')
    result = await dialog
    dialog.delete()
    return result


async def confirm(title: str, text: str, yes: str | None = None, no: str | None = None, icon: str = 'help') -> bool:
    yes, no = yes or _('Ja'), no or _('Abbrechen')
    dialog, card = _dialog_card('460px', persistent=True)
    with card:
        with ui.row().classes('items-center gap-3 no-wrap'):
            ui.icon(icon, size='26px').classes('text-accent')
            ui.label(title).classes('text-lg font-semibold')
        ui.label(text).classes('text-sm')
        with ui.row().classes('w-full justify-end gap-2 mt-3'):
            ui.button(no, on_click=lambda: dialog.submit(False)).props('flat')
            ui.button(yes, on_click=lambda: dialog.submit(True)).props('unelevated')
    result = await dialog
    dialog.delete()
    return bool(result)


async def progress_dialog(plan: executor.Plan, password: str | None, notes: list[str]) -> executor.Result:
    phases = plan.phases()
    dialog = ui.dialog().props('persistent')
    with dialog, ui.card().classes('glass p-6').style('width:680px;max-width:94vw').props('flat'):
        with ui.row().classes('items-center gap-3'):
            spinner = ui.spinner(size='26px')
            title = ui.label(_('Probelauf …') if plan.dry_run else _('Update läuft …')).classes('text-xl font-semibold')
        ui.label(_('Gesamt')).classes('muted text-xs mt-2')
        total = ui.linear_progress(0, size='14px', show_value=False).props('rounded instant-feedback')
        with ui.row().classes('w-full justify-between'):
            phase_label = ui.label('').classes('text-sm font-medium')
            pct = ui.label('0 %').classes('text-sm muted')
        phase_bar = ui.linear_progress(0, size='8px', show_value=False, color='secondary').props('rounded')
        with ui.row().classes('gap-2 mt-2'):
            chips = {p: ui.chip(_(p), icon='radio_button_unchecked', color='grey-5').props('dense outline') for p in phases}
        log = ui.log(max_lines=2000).classes('log-box w-full mt-3')
        footer = ui.row().classes('w-full items-center justify-between mt-3')
    dialog.open()
    for n in notes:
        log.push(':: ' + n)

    current = {'p': None}

    def on_phase(name: str) -> None:
        base = name.split(' · ')[0]
        if base in chips and base != current['p']:
            if current['p']:
                chips[current['p']].props('icon=check_circle color=positive')
            chips[base].props('icon=pending color=primary')
            current['p'] = base
        phase_label.text = ' · '.join(_(x) for x in name.split(' · '))

    def on_progress(t: float, p: float) -> None:
        total.value, phase_bar.value = t, p
        pct.text = f'{t * 100:.0f} %'

    async def ask(t: str, text: str, yes: str, no: str) -> bool:
        return await confirm(t, text, yes=yes, no=no, icon='gpp_bad')

    runner = executor.Runner(plan, executor.Callbacks(on_phase, on_progress, log.push, ask), password, md.SYSTEM)
    password = None
    result = await runner.run()

    spinner.set_visibility(False)
    if current['p']:
        chips[current['p']].props(f'icon={"check_circle" if result.ok else "cancel"} color={"positive" if result.ok else "negative"}')
    title.text = (_('Probelauf abgeschlossen') if plan.dry_run else _('Update abgeschlossen')) if result.ok else _('Update abgebrochen')
    phase_label.text = (_('{ok} Schritte erfolgreich · {skipped} übersprungen', ok=len(result.updated), skipped=len(result.skipped))
                        + (_(' · Fehler: {err}', err=result.errors[0]) if result.errors else ''))
    with footer:
        with ui.column().classes('gap-0'):
            if result.pacnew:
                ui.label(_('{n} neue .pacnew-Dateien – mit pacdiff zusammenführen', n=len(result.pacnew))).classes('text-sm text-warning')
            if result.reboot:
                ui.label(_('Neustart erforderlich (Kernel ersetzt)')).classes('text-sm text-negative font-medium')
            ui.label(_('Protokoll: {path}', path=result.log_file)).classes('muted text-xs')
        ui.button(_('Schließen'), on_click=dialog.close).props('unelevated')
    ui.notify(title.text, type='positive' if result.ok else 'negative', position='top-right')
    return result


async def start_update(state: State) -> None:
    if state.busy:
        ui.notify(_('Es läuft bereits ein Update'), type='warning')
        return
    if not md.ready():
        ui.notify(_('Systemstand wird noch geladen …'), type='warning')
        return
    opts = await context_dialog(state)
    if not opts:
        return
    plan, notes = build_plan(state, opts)
    if not (plan.repo or plan.aur or plan.flatpak):
        ui.notify(_('Nach Filter und Risikogrenze bleibt nichts zu tun'), type='warning')
        return
    password = None
    needs_root = plan.repo or plan.aur or (plan.snapshot and not md.SYSTEM.get('snap_pac'))
    if needs_root and not plan.dry_run:
        password = await password_dialog()
        if not password:
            ui.notify(_('Abgebrochen'), position='top-right')
            return
    state.busy = True
    try:
        result = await progress_dialog(plan, password, notes)
    finally:
        password = None
        state.busy = False
    if not plan.dry_run and result.ok:
        background_tasks.create(md.refresh(), name='refresh-after-update')
