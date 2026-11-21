"""Popups: Kontextabfrage, Passwort, Rückfragen und Fortschritt des echten Update-Laufs."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from datetime import time as dtime

from nicegui import background_tasks, ui

from . import catalog as md
from . import config, executor, scheduler, theme
from . import state as st
from .i18n import _
from .sources import flatpak
from .sources import pacman as pacman_src
from .state import State

_REFRESH_POLL = 0.5          # s: so oft nachsehen, ob eine laufende Prüfung des Systemstands fertig ist


def _stable_dialog(persistent: bool = False) -> ui.dialog:
    """Dialog am Seitenlayout statt an der Unterseite: ein Neuaufbau der Seite (neuer Systemstand) schließt ihn nicht."""
    with ui.context.client.layout:
        return ui.dialog().props('persistent' if persistent else '')


def _dialog_card(width: str = '520px', persistent: bool = False):
    dialog = _stable_dialog(persistent)
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
        snapshot=opts['snapshot'], dry_run=opts['dry_run'], reviewed=dict(state.reviewed),
        remove_orphans=opts.get('orphans', False), clean_cache=opts.get('cache', False),
        cache_keep=int(opts.get('cache_keep', 2)),
    )
    return plan, notes


def _parse_day(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or '').strip())
    except ValueError:
        return None


def _parse_clock(text: str | None) -> dtime | None:
    try:
        return dtime.fromisoformat((text or '').strip())
    except ValueError:
        return None


async def context_dialog(state: State) -> dict | None:
    updates = state.selected_updates()
    sysinfo = md.SYSTEM
    orphans = sysinfo.get('orphans') or []
    cache = sysinfo.get('cache') or {}
    if not updates and not orphans and not cache.get('candidates'):
        ui.notify(_('Keine Updates ausgewählt'), type='warning')
        return None
    size = sum(u['size'] for u in updates)
    top = max(updates, key=lambda u: u['risk']) if updates else {'name': '–', 'risk': 0}
    level, color = md.risk_level(top['risk'])
    reboot = any(config.matches(u['name'], config.REBOOT) for u in updates if u['source'] == 'repo')

    dialog, card = _dialog_card()
    with card:
        with ui.row().classes('items-center gap-3'):
            ui.icon('rocket_launch', size='28px').classes('text-primary')
            ui.label(_('Update planen')).classes('text-xl font-semibold')
        with ui.grid(columns=3).classes('w-full gap-3 my-3'):
            for value, label in [(len(updates), _('Pakete')), (f'{size:.0f} MiB', _('Download')), (top['risk'], _('max. Risiko'))]:
                with ui.column().classes('glass p-3 items-center gap-0'):
                    ui.label(str(value)).classes('text-lg font-bold')
                    ui.label(label).classes('muted text-xs')
        with ui.row().classes('items-center gap-2'):
            if updates:
                ui.badge(_('Höchstes Risiko: {pkg} ({level})', pkg=top['name'], level=_(level)), color=color)
            if reboot:
                ui.badge(_('Neustart nötig'), color='secondary')
        flagged = [u['name'] for u in updates
                   if (scan := md.SCANS.get(u['name'])) and scan.score >= 40 and not state.is_reviewed(u['name'])]
        manual = [n for n in md.NEWS if n['manual'] and n.get('new')]
        for icon, text, link in ([('gpp_bad', _('AUR-Scanner meldet verdächtige Pakete: {pkgs}', pkgs=', '.join(flagged)), '/aur-scan')] if flagged else []) + \
                [('front_hand', _('News mit manuellem Eingriff: {title}', title=n['title']), n['link']) for n in manual]:
            with ui.row().classes('notice w-full items-start gap-2 mt-3 no-wrap').style('border-left-color:#dc4e41'):
                ui.icon(icon, color='negative')
                with ui.column().classes('gap-0'):
                    ui.label(text).classes('text-sm font-medium')
                    if link.startswith('/'):
                        ui.link(_('Ansehen'), link).classes('text-sm text-primary').on('click', lambda: dialog.submit(None))
                    elif link:
                        ui.link(_('Meldung öffnen'), link, new_tab=True).classes('text-sm text-primary')
        ui.separator().classes('my-3')
        ui.label(_('Zeitpunkt')).classes('panel-title')
        when = ui.toggle({'now': _('Jetzt'), '1h': _('In 1 Stunde'), '2h': _('In 2 Stunden'), 'time': _('Uhrzeit')},
                         value='now').props('dense no-caps unelevated toggle-color=primary')
        soon = datetime.now() + timedelta(hours=1)
        with ui.row().classes('items-center gap-3 no-wrap') as at_row:
            day = ui.input(_('Datum'), value=soon.date().isoformat()).props('type=date dense outlined').classes('w-40')
            clock = ui.input(_('Uhrzeit'), value=soon.strftime('%H:%M')).props('type=time dense outlined').classes('w-32')
        at_row.bind_visibility_from(when, 'value', lambda v: v == 'time')
        when_info = ui.label('').classes('muted text-xs')
        when_error = ui.label('').classes('text-negative text-sm')

        def describe_when() -> None:
            when_error.text = ''
            when_info.text = (_('Das Passwort wird erst zum geplanten Zeitpunkt abgefragt. Der Update Planner muss dann '
                                'laufen (das Fenster darf auch minimiert sein).') if when.value != 'now' else '')
        when.on_value_change(describe_when)
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
        ui.separator().classes('my-3')
        ui.label(_('Aufräumen nach dem Update')).classes('panel-title')
        orphan_sw = ui.switch(_('Verwaiste Pakete entfernen – vorher wird die Liste zur Bestätigung angezeigt'),
                              value=False)
        ui.label(_('Derzeit {n} verwaiste Pakete', n=len(orphans)) + (f': {", ".join(orphans[:6])}' + (' …' if len(orphans) > 6 else '')
                 if orphans else _('. Nach dem Update wird erneut geprüft.'))).classes('muted text-xs -mt-1 ml-10')
        with ui.row().classes('items-center gap-3 no-wrap'):
            cache_sw = ui.switch(_('Paket-Cache aufräumen'), value=False)
            keep = ui.select({1: _('1 Version behalten'), 2: _('2 Versionen behalten'), 3: _('3 Versionen behalten')},
                             value=2).props('dense outlined options-dense').classes('w-48')
            keep.bind_enabled_from(cache_sw, 'value')
        ui.label(_('Cache {size} GiB; mit 2 Versionen frei werden ca. {saved} GiB ({n} Dateien). '
                   'Pakete, die nicht mehr installiert sind, werden ganz entfernt.',
                   size=f'{cache.get("size_gib", 0):.1f}', saved=f'{cache.get("saved_gib", 0):.1f}',
                   n=cache.get('candidates', 0))).classes('muted text-xs -mt-1 ml-10')
        with ui.row().classes('w-full justify-end gap-2 mt-4'):
            ui.button(_('Abbrechen'), on_click=lambda: dialog.submit(None)).props('flat')
            def proceed() -> None:
                try:
                    at = scheduler.compute_at(when.value, day=_parse_day(day.value), clock=_parse_clock(clock.value))
                except ValueError as exc:
                    when_error.text = str(exc)
                    return
                dialog.submit({
                    'snapshot': snapshot.value, 'aur': aur.value, 'flatpak': flatpak.value, 'hold': hold.value,
                    'dry_run': dry.value, 'sync': False,
                    'orphans': orphan_sw.value, 'cache': cache_sw.value, 'cache_keep': keep.value, 'at': at,
                })
            ui.button(_('Weiter'), icon='arrow_forward', on_click=proceed).props('unelevated')
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
        tries = {'n': 0, 'checking': False}

        async def ok():
            if tries['checking']:                 # Enter + Klick dürfen nicht zwei sudo-Prüfungen starten
                return
            value = pw.value or ''
            if not value:
                error.text = _('Bitte Passwort eingeben')
                return
            tries['checking'] = True
            btn.props('loading')
            try:
                valid = await executor.check_password(value)
            finally:
                tries['checking'] = False
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
    dialog = _stable_dialog(persistent=True)
    with dialog, ui.card().classes('glass p-6').style('width:680px;max-width:94vw').props('flat'):
        with ui.row().classes('items-center gap-3'):
            spinner = ui.spinner(size='26px')
            titles = {'install': (_('Installation läuft …'), _('Installation abgeschlossen'), _('Installation abgebrochen')),
                      'remove': (_('Entfernen läuft …'), _('Entfernen abgeschlossen'), _('Entfernen abgebrochen')),
                      'update': (_('Update läuft …'), _('Update abgeschlossen'), _('Update abgebrochen'))}[plan.kind()]
            title = ui.label(_('Probelauf …') if plan.dry_run else titles[0]).classes('text-xl font-semibold')
        ui.label(_('Gesamt')).classes('muted text-xs mt-2')
        total = ui.linear_progress(0, size='14px', show_value=False).props('instant-feedback')
        with ui.row().classes('w-full justify-between'):
            phase_label = ui.label('').classes('text-sm font-medium')
            pct = ui.label('0 %').classes('text-sm muted')
        phase_bar = ui.linear_progress(0, size='8px', show_value=False, color='secondary')
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

    try:
        runner = executor.Runner(plan, executor.Callbacks(on_phase, on_progress, log.push, ask), password, md.SYSTEM)
    except Exception as exc:  # noqa: BLE001 – sonst bliebe der persistente Dialog für immer offen
        result = executor.Result(ok=False, errors=[str(exc)])
        log.push(_('!! Abbruch: {err}', err=exc))
    else:
        password = None
        result = await runner.run()

    spinner.set_visibility(False)
    if current['p']:
        chips[current['p']].props(f'icon={"check_circle" if result.ok else "cancel"} color={"positive" if result.ok else "negative"}')
    title.text = (_('Probelauf abgeschlossen') if plan.dry_run else titles[1]) if result.ok else titles[2]
    phase_label.text = (_('{ok} Schritte erfolgreich · {skipped} übersprungen', ok=len(result.updated), skipped=len(result.skipped))
                        + (_(' · Fehler: {err}', err=result.errors[0]) if result.errors else ''))
    with footer:
        with ui.column().classes('gap-0'):
            if result.pacnew:
                ui.label(_('{n} neue .pacnew-Dateien – mit pacdiff zusammenführen', n=len(result.pacnew))).classes('text-sm text-warning')
            if result.removed:
                ui.label(_('{n} verwaiste Pakete entfernt', n=len(result.removed))).classes('text-sm')
            if result.cache_cleaned:
                ui.label(_('Paket-Cache aufgeräumt')).classes('text-sm')
            if result.reboot:
                ui.label(_('Neustart erforderlich (Kernel ersetzt)')).classes('text-sm text-negative font-medium')
            ui.label(_('Protokoll: {path}', path=result.log_file)).classes('muted text-xs')
        def close() -> None:                 # am Layout angehängt: sonst bliebe je Lauf ein Dialog samt Protokoll liegen
            dialog.close()
            dialog.delete()
        ui.button(_('Schließen'), on_click=close).props('unelevated')
    ui.notify(title.text, type='positive' if result.ok else 'negative', position='top-right')
    return result


async def _execute(state: State, plan: executor.Plan, notes: list[str], on_start=None) -> bool:
    """Passwort abfragen (falls nötig), Lauf mit Fortschritt ausführen. False, wenn vorher abgebrochen wurde."""
    password = None
    needs_root = plan.repo or plan.install or plan.remove or plan.aur or plan.remove_orphans or plan.clean_cache \
        or (plan.snapshot and not md.SYSTEM.get('snap_pac'))
    if needs_root and not plan.dry_run:
        password = await password_dialog()
        if not password:
            ui.notify(_('Abgebrochen'), position='top-right')
            return False
    state.busy = True
    try:
        if on_start:
            on_start()                    # schlägt das fehl, darf busy nicht hängen bleiben
        await progress_dialog(plan, password, notes)
    finally:
        password = None
        state.busy = False
    if not plan.dry_run:
        # auch nach Abbruch/Teilerfolg neu laden – sonst zeigt die Oberfläche bereits installierte Updates weiter an
        background_tasks.create(md.refresh(), name='refresh-after-update')
    return True


def _nothing_to_do(plan: executor.Plan) -> bool:
    return not (plan.repo or plan.install or plan.aur or plan.flatpak or plan.flatpak_install
                or plan.remove or plan.flatpak_remove or plan.remove_orphans or plan.clean_cache)


async def start_update(state: State) -> None:
    if state.busy or executor.running():          # auch Läufe aus anderen Fenstern/Tabs
        ui.notify(_('Es läuft bereits ein Update'), type='warning')
        return
    if not md.ready():
        ui.notify(_('Systemstand wird noch geladen …'), type='warning')
        return
    opts = await context_dialog(state)
    if not opts:
        return
    at = opts.pop('at', None)
    if at:
        await _schedule(state, opts, at)
        return
    plan, notes = build_plan(state, opts)
    if _nothing_to_do(plan):
        ui.notify(_('Nach Filter und Risikogrenze bleibt nichts zu tun'), type='warning')
        return
    await _execute(state, plan, notes)


async def _schedule(state: State, opts: dict, at: float) -> None:
    if scheduler.current() and not await confirm(
            _('Geplantes Update ersetzen?'),
            _('Es ist bereits ein Update für {time} geplant. Es gibt nur einen Plan gleichzeitig.',
              time=scheduler.format_at(scheduler.current().at)), yes=_('Ersetzen'), icon='schedule'):
        return
    # Gespeichert werden die ABGEWÄHLTEN Updates (was bis dahin neu erscheint, ist per Standardauswahl dabei) und die
    # bewusst ZUSÄTZLICH gewählten (z. B. Risiko ≥ 80 – die Standardauswahl schließt sie sonst wieder aus)
    visible = {u['name'] for u in state.visible_updates()}
    deselected = visible - state.selected
    extra = (visible & state.selected) - st.default_selection()
    try:
        scheduler.schedule(at, opts, deselected, state.sources, state.reviewed, extra)
    except OSError as exc:
        ui.notify(_('Plan konnte nicht gespeichert werden: {err}', err=exc), type='negative', position='top-right')
        return
    ui.notify(_('Update geplant für {time}', time=scheduler.format_at(at)), type='positive', position='top-right')


async def due_dialog(sch: scheduler.Schedule) -> str | None:
    """'start' | 'later' | 'discard'; None, wenn das Fenster geschlossen wurde (Plan bleibt dann fällig)."""
    dialog, card = _dialog_card('480px', persistent=True)
    with card:
        with ui.row().classes('items-center gap-3 no-wrap'):
            ui.icon('schedule', size='28px').classes('text-primary')
            ui.label(_('Geplantes Update ist fällig')).classes('text-xl font-semibold')
        ui.label(_('Geplant für {time}. Beim Start wird der Systemstand neu geladen, danach fragt der Update Planner '
                   'das sudo-Passwort ab.', time=scheduler.format_at(sch.at))).classes('text-sm')
        with ui.row().classes('w-full justify-end gap-2 mt-3'):
            ui.button(_('Verwerfen'), on_click=lambda: dialog.submit('discard')).props('flat color=negative')
            ui.button(_('Später (15 min)'), on_click=lambda: dialog.submit('later')).props('flat')
            ui.button(_('Starten'), icon='play_arrow', on_click=lambda: dialog.submit('start')).props('unelevated')
    result = await dialog
    dialog.delete()
    return result


async def run_scheduled(state: State) -> None:
    """Fälligen Plan abarbeiten (das aufrufende Fenster hat ihn per scheduler.claim beansprucht)."""
    sch = scheduler.current()
    if not sch:
        return
    scheduler.notify_due()
    choice = await due_dialog(sch)
    if choice is None:
        return
    if choice == 'discard':
        scheduler.cancel()
        return
    if choice == 'later':
        scheduler.snooze()
        return
    ui.notify(_('Systemstand wird neu geladen …'), position='top-right')
    await md.refresh()                    # kehrt sofort zurück, wenn schon eine Prüfung läuft → dann abwarten
    while md.STATUS['state'] == 'loading':
        await asyncio.sleep(_REFRESH_POLL)
    if not md.ready():
        ui.notify(_('Systemstand konnte nicht geladen werden'), type='negative')
        scheduler.snooze()
        return
    # Auswahl des Plans in einem eigenen State: die Filter- und Auswahlanzeige des Fensters bleibt unverändert
    plan_state = State(sources=set(sch.sources) or set(state.sources),
                       selected=(st.default_selection() - set(sch.deselected)) | set(sch.selected_extra),
                       reviewed={**state.reviewed, **sch.reviewed})
    plan, notes = build_plan(plan_state, sch.opts)
    if _nothing_to_do(plan):
        ui.notify(_('Nach Filter und Risikogrenze bleibt nichts zu tun'), type='warning')
        scheduler.cancel()
        return
    if not await _execute(state, plan, notes, on_start=scheduler.cancel):   # Plan erst beim echten Start verbrauchen
        scheduler.snooze()


# ------------------------------------------------------------------ Neue Pakete installieren

async def install_dialog(items: list[dict]) -> dict | None:
    sysinfo = md.SYSTEM
    repo_items = [c for c in items if c['source'] == 'repo']
    flat_items = [c for c in items if c['source'] == 'flatpak']
    pending = sum(1 for u in md.UPDATES if u['source'] == 'repo')
    # In welchen Bereichen (Benutzer/System) ist Flathub eingerichtet? Ohne Remote im gewählten Bereich hielte
    # `flatpak install` „flathub“ für einen App-Namen und suchte danach.
    scopes = await asyncio.to_thread(flatpak.remote_scopes, 'flathub') if flat_items else set()
    dialog, card = _dialog_card('560px')
    with card:
        with ui.row().classes('items-center gap-3'):
            ui.icon('download', size='28px').classes('text-primary')
            ui.label(_('{n} Pakete installieren', n=len(items))).classes('text-xl font-semibold')
        with ui.column().classes('w-full gap-1 my-2').style('max-height:260px;overflow:auto'):
            for c in items:
                with ui.row().classes('w-full items-center gap-2 no-wrap'):
                    ui.label(c['name']).classes('font-medium')
                    ui.label(c.get('origin') or md.SOURCES[c['source']]['label']).classes('muted text-xs')
                    ui.space()
                    sec = c.get('sec') or {}
                    if sec.get('severity'):
                        theme.badge(_(sec['severity']) + (f' ×{sec["count"]}' if sec['count'] > 1 else ''),
                                    theme.SEVERITY_COLOR.get(sec['severity'], theme.GREY))
                    for flag in c.get('flags', []):
                        theme.badge(flag, theme.ORANGE)
        if repo_items:
            with ui.row().classes('notice w-full items-start gap-2 no-wrap'):
                ui.icon('info', color='primary')
                ui.label(_('Repo-Pakete werden mit pacman -Syu --needed installiert: {n} anstehende Updates werden '
                           'mitinstalliert (Arch kennt keine Teil-Updates).', n=pending)).classes('text-sm')
        if any(c['source'] == 'aur' for c in items):
            ui.label(_('AUR-Pakete werden als Benutzer gebaut; das PKGBUILD wird vorher gescannt. '
                       'AUR-Abhängigkeiten aus dem AUR werden nicht automatisch gebaut.')).classes('muted text-xs mt-2')
        ui.separator().classes('my-3')
        snap_text = (_('Snapshot: snap-pac legt pre/post automatisch an') if sysinfo.get('snap_pac')
                     else _('Btrfs-Snapshot vorher anlegen (Snapper)'))
        snapshot = ui.switch(snap_text, value=bool(sysinfo.get('snapper') or sysinfo.get('snap_pac')))
        if sysinfo.get('snap_pac') or not sysinfo.get('snapper'):
            snapshot.disable()
        user_only = None
        if flat_items and not scopes:
            with ui.row().classes('notice w-full items-start gap-2 no-wrap').style('border-left-color:#dc4e41'):
                ui.icon('error', color='negative')
                ui.label(_('Der Flatpak-Remote „flathub“ ist nicht eingerichtet – Flatpak-Pakete lassen sich '
                           'nicht installieren.')).classes('text-sm')
        elif flat_items:
            user_only = ui.switch(_('Flatpak nur für mich installieren'), value='user' in scopes)
            if len(scopes) == 1:                       # nur ein Bereich eingerichtet: keine Wahl
                user_only.disable()
                ui.label(_('Flathub ist nur für den Benutzer eingerichtet.') if 'user' in scopes
                         else _('Flathub ist nur systemweit eingerichtet – die Installation fragt über polkit '
                                'nach der Anmeldung.')).classes('muted text-xs -mt-1 ml-10')
        if flat_items and scopes:
            ui.label(_('Flatpak braucht kein sudo-Passwort: Die Benutzerinstallation benötigt keine Berechtigung, '
                       'systemweit fragt Flatpak bei Bedarf über Polkit (Gruppe wheel meist ohne Abfrage).')) \
                .classes('muted text-xs -mt-1 ml-10')
        dry = ui.switch(_('Probelauf – nichts installieren'), value=False)
        with ui.row().classes('w-full justify-end gap-2 mt-4'):
            ui.button(_('Abbrechen'), on_click=lambda: dialog.submit(None)).props('flat')
            go = ui.button(_('Weiter'), icon='arrow_forward', on_click=lambda: dialog.submit({
                'snapshot': snapshot.value, 'dry_run': dry.value,
                'flatpak_user': bool(user_only.value) if user_only else False,
            })).props('unelevated')
            go.set_enabled(not (flat_items and not scopes))
    result = await dialog
    dialog.delete()
    return result


def build_install_plan(state: State, items: list[dict], opts: dict) -> executor.Plan:
    aur = [c for c in items if c['source'] == 'aur']
    return executor.Plan(
        repo=False, install=[c['name'] for c in items if c['source'] == 'repo'],
        aur=[(c['name'], c.get('base') or c['name']) for c in aur], install_aur={c['name'] for c in aur},
        flatpak_install=[c['name'] for c in items if c['source'] == 'flatpak'],
        flatpak_user=opts.get('flatpak_user', False),
        snapshot=opts['snapshot'], dry_run=opts['dry_run'], reviewed=dict(state.reviewed))


async def start_install(state: State, items: list[dict]) -> bool:
    """Ausgewählte neue Pakete sofort installieren. True nur nach einem echten Lauf – nach Abbruch oder Probelauf
    bleibt die Auswahl bestehen, damit man gleich anschließend wirklich installieren kann."""
    if not items:
        return False
    if state.busy or executor.running():
        ui.notify(_('Es läuft bereits ein Update'), type='warning')
        return False
    if not md.ready():
        ui.notify(_('Systemstand wird noch geladen …'), type='warning')
        return False
    opts = await install_dialog(items)
    if not opts:
        return False
    plan = build_install_plan(state, items, opts)
    return await _execute(state, plan, []) and not plan.dry_run


# ------------------------------------------------------------------ Software entfernen

def is_protected(name: str) -> bool:
    """Systemkomponenten, die der Planner nie entfernt (Kernel, glibc, systemd, pacman, Treiber …)."""
    return config.matches(name, config.CRITICAL)


async def remove_dialog(pkg: dict) -> dict | None:
    """Vorschau (was pacman tatsächlich entfernen würde) und Optionen. None bei Abbruch."""
    name, flat = pkg['name'], pkg['source'] == 'flatpak'
    gone: list[str] = [name]
    blockers: list[str] = []
    error = ''
    if not flat and not md.STATUS.get('demo'):
        try:
            gone, blockers = await asyncio.to_thread(pacman_src.removal_preview, [name])
        except Exception as exc:  # noqa: BLE001 – ohne Vorschau wird nicht entfernt
            error = str(exc)[:300]
    protected = sorted(n for n in [name, *gone] if is_protected(n))
    blocked = bool(blockers or error or protected or (not flat and name not in gone and not md.STATUS.get('demo')))
    sysinfo = md.SYSTEM
    dialog, card = _dialog_card('560px')
    with card:
        with ui.row().classes('items-center gap-3 no-wrap'):
            ui.icon('delete', size='28px').classes('text-negative')
            ui.label(_('Soll „{name}“ wirklich entfernt werden?', name=name)).classes('text-xl font-semibold')
        if flat:
            ui.label(_('Die Flatpak-App wird {scope} entfernt.',
                       scope=_('für den Benutzer') if pkg.get('reason') == 'user' else _('systemweit'))).classes('text-sm')
        elif gone and not blockers:
            ui.label(_('pacman würde {n} Pakete entfernen (das Paket und Abhängigkeiten, die sonst niemand braucht):',
                       n=len(gone))).classes('text-sm')
            ui.label(', '.join(gone[:30]) + (' …' if len(gone) > 30 else '')).classes('text-sm font-medium')
        for text in ([_('Systemkomponente – wird nicht entfernt: {pkgs}', pkgs=', '.join(protected))] if protected else []) \
                + ([_('Entfernen nicht möglich – davon abhängig: {pkgs}', pkgs=', '.join(blockers[:10]))] if blockers else []) \
                + ([_('Vorschau fehlgeschlagen: {err}', err=error)] if error else []):
            with ui.row().classes('notice w-full items-start gap-2 no-wrap mt-2').style('border-left-color:#dc4e41'):
                ui.icon('block', color='negative')
                ui.label(text).classes('text-sm')
        ui.separator().classes('my-3')
        snapshot = None
        if not flat:
            snap_text = (_('Snapshot: snap-pac legt pre/post automatisch an') if sysinfo.get('snap_pac')
                         else _('Btrfs-Snapshot vorher anlegen (Snapper)'))
            snapshot = ui.switch(snap_text, value=bool(sysinfo.get('snapper') or sysinfo.get('snap_pac')))
            if sysinfo.get('snap_pac') or not sysinfo.get('snapper'):
                snapshot.disable()
            extra = ui.switch(_('Konfigurationsdateien ebenfalls löschen (sonst bleiben .pacsave-Dateien)'), value=False)
        else:
            extra = ui.switch(_('Anwendungsdaten ebenfalls löschen (~/.var/app)'), value=False)
        dry = ui.switch(_('Probelauf – nichts entfernen'), value=False)
        with ui.row().classes('w-full justify-end gap-2 mt-4'):
            ui.button(_('Abbrechen'), on_click=lambda: dialog.submit(None)).props('flat')
            go = ui.button(_('Entfernen'), icon='delete', on_click=lambda: dialog.submit({
                'snapshot': bool(snapshot.value) if snapshot else False, 'extra': extra.value, 'dry_run': dry.value,
            })).props('unelevated color=negative')
            go.set_enabled(not blocked)
    result = await dialog
    dialog.delete()
    return result


def build_remove_plan(pkg: dict, opts: dict) -> executor.Plan:
    if pkg['source'] == 'flatpak':
        return executor.Plan(repo=False, snapshot=False, dry_run=opts['dry_run'], flatpak_remove=[pkg['name']],
                             flatpak_remove_user=pkg.get('reason') == 'user', flatpak_remove_data=opts['extra'])
    return executor.Plan(repo=False, snapshot=opts['snapshot'], dry_run=opts['dry_run'], remove=[pkg['name']],
                         remove_configs=opts['extra'])


async def start_remove(state: State, pkg: dict) -> bool:
    """Software entfernen. True nur nach einem echten Lauf (dann ändert sich der Bestand)."""
    if state.busy or executor.running():
        ui.notify(_('Es läuft bereits ein Update'), type='warning')
        return False
    if not md.ready():
        ui.notify(_('Systemstand wird noch geladen …'), type='warning')
        return False
    if is_protected(pkg['name']):
        ui.notify(_('{pkg} ist eine Systemkomponente und wird nicht entfernt', pkg=pkg['name']), type='negative')
        return False
    opts = await remove_dialog(pkg)
    if not opts:
        return False
    plan = build_remove_plan(pkg, opts)
    return await _execute(state, plan, []) and not plan.dry_run
