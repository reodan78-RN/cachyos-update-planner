"""AUR-Sicherheitsscanner: statische Prüfung von PKGBUILD/.install auf verdächtige Muster.

Die Regeln sind echt und arbeiten auf beliebigem Text – im Mockup werden sie auf Demo-PKGBUILDs
angewendet. Ein Scan ersetzt kein Lesen des PKGBUILD, er priorisiert nur, wo man hinschauen sollte.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from .i18n import _

SEVERITY_POINTS = {'kritisch': 45, 'hoch': 25, 'mittel': 12, 'niedrig': 4, 'info': 0}
SEVERITY_ORDER = ['kritisch', 'hoch', 'mittel', 'niedrig', 'info']
SEVERITY_COLOR = {'kritisch': 'purple', 'hoch': 'negative', 'mittel': 'warning', 'niedrig': 'info', 'info': 'grey'}

INSTALL_FUNCS = {'pre_install', 'post_install', 'pre_upgrade', 'post_upgrade', 'pre_remove', 'post_remove'}


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    category: str
    title: str
    pattern: str
    advice: str
    only_in: frozenset[str] | None = None  # nur in diesen Funktionen ('' = global)
    flags: int = re.IGNORECASE

    def match(self, line: str) -> bool:
        return re.search(self.pattern, line, self.flags) is not None


RULES: list[Rule] = [
    Rule('AUR001', 'kritisch', 'Remote-Code', 'Download direkt in eine Shell ausgeführt',
         r'(curl|wget|fetch)\b[^|#]*\|\s*(sudo\s+)?(ba|z|da)?sh\b',
         'Externer Code wird ungeprüft ausgeführt – nicht durch Prüfsummen abgedeckt.'),
    Rule('AUR002', 'kritisch', 'Verschleierung', 'Base64-dekodierter Inhalt wird ausgeführt',
         r'base64\s+(-d|--decode)[^#]*\|\s*(ba|z)?sh\b|eval\s*["\$(]*\s*\$\(.*base64',
         'Verschleierter Code ist ein starkes Malware-Indiz. Inhalt dekodieren und prüfen.'),
    Rule('AUR003', 'kritisch', 'Netzwerk', 'Reverse-Shell-Muster',
         r'/dev/tcp/|\bnc(at)?\b.*\s-e\s|socat\b.*exec:|bash\s+-i\s*>&',
         'Öffnet eine Verbindung, über die ein Angreifer Befehle ausführen kann.'),
    Rule('AUR004', 'hoch', 'Netzwerk', 'Netzwerkzugriff im Install-Skript',
         r'\b(curl|wget|nc|ssh|scp|rsync)\b', 'Install-Skripte laufen als root und sollten nichts nachladen.',
         only_in=frozenset(INSTALL_FUNCS)),
    Rule('AUR005', 'hoch', 'Netzwerk', 'Netzwerkzugriff außerhalb von source=()',
         r'\b(curl|wget)\b\s', 'Downloads gehören in source=() mit Prüfsumme, nicht in prepare/build/package.',
         only_in=frozenset({'prepare', 'build', 'check', 'package', 'pkgver'})),
    Rule('AUR006', 'hoch', 'Persistenz', 'Cronjob oder Autostart wird angelegt',
         r'\bcrontab\b|/etc/cron|\.config/autostart|/etc/xdg/autostart|@reboot',
         'Dauerhafte Ausführung außerhalb der Paketverwaltung.'),
    Rule('AUR007', 'hoch', 'Persistenz', 'Benutzer-Startdateien oder Home-Verzeichnisse verändert',
         r'(/home/|\$HOME|~/)[^\s"]*|\.bashrc|\.zshrc|\.profile|config\.fish',
         'Pakete dürfen Home-Verzeichnisse nicht verändern.'),
    Rule('AUR008', 'hoch', 'Rechte', 'SUID/SGID-Bit wird gesetzt',
         r'chmod\s+([ug]\+s|[2467][0-7]{3})\b', 'SUID-Binärdateien laufen mit erhöhten Rechten – nur mit gutem Grund.'),
    Rule('AUR009', 'hoch', 'Rechte', 'sudo im PKGBUILD',
         r'(^|[\s;&|])sudo\s', 'makepkg läuft ohne root; sudo im PKGBUILD ist nie nötig.'),
    Rule('AUR010', 'hoch', 'Quelle', 'Quelle über rohe IP-Adresse',
         r'https?://\d{1,3}(\.\d{1,3}){3}', 'Keine nachvollziehbare Herkunft – Upstream prüfen.'),
    Rule('AUR011', 'hoch', 'Quelle', 'Quelle von Paste-/Kurz-URL-Dienst',
         r'(pastebin\.com|paste\.ee|hastebin|transfer\.sh|bit\.ly|tinyurl|t\.ly|0x0\.st|discord(app)?\.com/attachments)',
         'Solche Dienste werden typischerweise zum Ausliefern von Schadcode genutzt.'),
    Rule('AUR012', 'mittel', 'Quelle', 'Skript direkt von raw.githubusercontent geladen',
         r'raw\.githubusercontent\.com|gist\.githubusercontent\.com',
         'Inhalt kann jederzeit geändert werden – nicht versioniert, nicht geprüft.'),
    Rule('AUR013', 'mittel', 'Integrität', 'Unverschlüsselte Quelle (http://)',
         r'^\s*(source(_\w+)?=|["\'])[^#]*http://', 'Ohne TLS manipulierbar – nur in Ordnung mit GPG-Signaturprüfung.',
         only_in=frozenset({''})),
    Rule('AUR014', 'mittel', 'System', 'Schreibt direkt ins laufende System statt nach $pkgdir',
         r'(>>?|\btee\b|\bcp\b|\binstall\b|\bmv\b)\s+(-\S+\s+)*["\']?/(etc|usr|opt|var|boot)/',
         'Dateien gehören nach "$pkgdir", sonst kennt pacman sie nicht.'),
    Rule('AUR015', 'mittel', 'System', 'Dienst wird automatisch aktiviert',
         r'systemctl\s+(--\w+\s+)*(enable|start|daemon-reload)', 'Pakete sollen Dienste nicht selbst aktivieren (Arch-Richtlinie).'),
    Rule('AUR016', 'mittel', 'Verschleierung', 'Lange kodierte Zeichenkette',
         r'[A-Za-z0-9+/]{60,}={0,2}', 'Kann verschleierten Code enthalten.', flags=0),
    Rule('AUR017', 'mittel', 'Verschleierung', 'Inline-Interpreter mit Code',
         r'\b(python3?|perl|ruby|node)\s+-(c|e)\s', 'Eingebetteter Code ist schwer zu prüfen.'),
    Rule('AUR018', 'niedrig', 'System', 'Rekursives Löschen außerhalb von $srcdir/$pkgdir',
         r'rm\s+-[a-z]*r[a-z]*f?\s+["\']?/(?!\$)', 'Löschen im System gehört nicht in ein PKGBUILD.'),
    Rule('AUR019', 'niedrig', 'Rechte', 'Weltweit beschreibbare Rechte',
         r'chmod\s+(-R\s+)?(777|666|a\+w|o\+w)', 'Erlaubt jedem Benutzer Schreibzugriff.'),
]


@dataclass
class Finding:
    rule_id: str
    severity: str
    category: str
    title: str
    advice: str
    file: str = ''
    line: int = 0
    code: str = ''
    new_in_update: bool = False


@dataclass
class ScanResult:
    package: str
    findings: list[Finding] = field(default_factory=list)
    files: dict[str, tuple[str, str]] = field(default_factory=dict)  # file -> (alt, neu)

    @property
    def score(self) -> int:
        worst_per_rule: dict[str, int] = {}  # jede Regel zählt nur einmal
        for f in self.findings:
            worst_per_rule[f.rule_id] = max(worst_per_rule.get(f.rule_id, 0), SEVERITY_POINTS[f.severity])
        return min(100, sum(worst_per_rule.values()))

    @property
    def verdict(self) -> tuple[str, str, str]:
        """(Text, Farbe, Icon)"""
        s = self.score
        if s >= 70:
            return 'Gefährlich – nicht installieren', 'negative', 'gpp_bad'
        if s >= 40:
            return 'Manuell prüfen', 'warning', 'gpp_maybe'
        if s >= 15:
            return 'Auffällig', 'info', 'policy'
        return 'Unbedenklich', 'positive', 'verified_user'

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    @property
    def worst(self) -> str:
        return next((s for s in SEVERITY_ORDER if self.count(s)), 'info')


BUILD_FUNCS = frozenset({'prepare', 'build', 'check', 'package', 'pkgver'})
ANY = None                     # Kontext unbekannt: funktionsgebundene Regeln gelten (konservativ) überall

FUNC_RE = re.compile(r'^\s*(?:function\s+([A-Za-z_][\w:.-]*)\s*(?:\(\s*\))?|([A-Za-z_][\w:.-]*)\s*\(\s*\))\s*')
ASSIGN_RE = re.compile(r'^\s*(?:(?:local|declare|typeset|export|readonly)\s+(?:-\w+\s+)*)?[A-Za-z_]\w*(\[[^\]]*\])?\+?=')
HEREDOC_RE = re.compile(r'(?<!<)<<(-?)\s*([\'"]?)([A-Za-z_][\w-]*)\2')
INSTALL_RE = re.compile(r'^\s*install=["\']?([^"\'\s;)]+)', re.MULTILINE)
SKIP_RE = re.compile(r'^\s*(sha\d+sums|b2sums|md5sums|cksums)(_\w+)?=\(')
VCS_RE = re.compile(r'(git|svn|hg|bzr|fossil)\+|\.git\b')


def _lex(raw: str, quote: str | None) -> tuple[str, str, str | None]:
    """Eine Zeile zerlegen: (Text ohne Kommentar, Struktur ohne Zeichenketten und ${…}, offenes Anführungszeichen).

    Der Text wird gegen die Regeln geprüft, die Struktur nur für Klammern und Funktionsgrenzen genutzt –
    so verschieben `"{"` oder `${pkgdir}` die Tiefe nicht. Ein `#` beginnt nur am Wortanfang einen Kommentar
    (wie in bash), und ein über mehrere Zeilen offenes Anführungszeichen wird mitgeführt."""
    text: list[str] = []
    code: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if quote:
            text.append(ch)
            if ch == '\\' and quote == '"' and i + 1 < n:
                text.append(raw[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
                code.append(ch)
            i += 1
            continue
        if ch == '\\' and i + 1 < n:              # \{ ist kein Strukturzeichen
            text.append(raw[i:i + 2])
            code.append('__')
            i += 2
            continue
        if ch in '\'"':
            quote = ch
            text.append(ch)
            code.append(ch)
            i += 1
            continue
        if ch == '#' and (i == 0 or raw[i - 1] in ' \t;&|()'):
            break
        if raw.startswith('${', i):               # Parameter-Erweiterung samt Verschachtelung überspringen
            j, d = i + 2, 1
            while j < n and d:
                d += {'{': 1, '}': -1}.get(raw[j], 0)
                j += 1
            text.append(raw[i:j])
            code.append('$_')
            i = j
            continue
        text.append(ch)
        code.append(ch)
        i += 1
    return ''.join(text), ''.join(code), quote


@dataclass
class _Line:
    text: str                  # ohne Kommentar
    func: str | None           # '' = oberste Ebene, Name der Funktion, ANY = unbekannt
    toplevel_exec: bool        # Befehl auf oberster Ebene – läuft schon beim Einlesen (makepkg/pacman „sourcen“ die Datei)


def _contexts(lines: list[str]) -> tuple[list[_Line], bool]:
    """Funktionskontext jeder Zeile. Zweiter Wert False, wenn die Struktur nicht sicher erkannt wurde
    (unausgeglichene Klammern, offene Zeichenkette, Funktionsrumpf ohne { … }) – dann gilt ANY."""
    out: list[_Line] = []
    stack: list[tuple[str, int]] = []         # (Funktion, Tiefe ihrer öffnenden Klammer)
    depth, array = 0, 0
    quote: str | None = None
    pending: str | None = None                # Funktionskopf gesehen, { steht noch aus
    heredoc: tuple[str, bool] | None = None   # (Endmarke, führende Tabs erlaubt)
    certain = True

    for raw in lines:
        if heredoc:                           # Inhalt gehört zur umgebenden Funktion, ist aber keine Struktur
            out.append(_Line(raw, stack[-1][0] if stack else '', False))
            end = raw.lstrip('\t') if heredoc[1] else raw
            if end.rstrip() == heredoc[0]:
                heredoc = None
            continue
        was_quoted = quote is not None
        text, code, quote = _lex(raw, quote)
        ctx = stack[-1][0] if stack else ''
        fm = None if was_quoted else FUNC_RE.match(code)
        rest = code
        if fm:
            pending, rest = fm.group(1) or fm.group(2), code[fm.end():]
        if pending and rest.strip() and not rest.lstrip().startswith('{'):
            certain, pending = False, None    # z. B. Funktionsrumpf als Subshell: name() ( … )
        array_before = array
        for ch in rest:
            if ch == '{':
                depth += 1
                if pending:
                    stack.append((pending, depth))
                    ctx, pending = pending, None
            elif ch == '}':
                if stack and stack[-1][1] == depth:
                    stack.pop()
                depth -= 1
                if depth < 0:
                    certain, depth = False, 0
            elif not stack and not fm and ch in '()':
                array = array + 1 if ch == '(' else max(0, array - 1)
        plain = (ASSIGN_RE.match(code) is not None or array_before > 0
                 or code.strip() in ('', '{', '}', '(', ')'))
        cmd_subst = '$(' in text or '`' in text
        toplevel = ctx == '' and not stack and not fm and pending is None
        out.append(_Line(text, ctx, toplevel and (cmd_subst or not plain)))
        m = HEREDOC_RE.search(text)
        if m:
            heredoc = (m.group(3), m.group(1) == '-')

    if stack or depth or quote or pending or heredoc:
        certain = False
    return out, certain


def _applies(rule: Rule, ln: _Line, install_file: bool) -> bool:
    if rule.only_in is None or ln.func is ANY:
        return True
    if install_file and rule.only_in & INSTALL_FUNCS:
        return True                           # .install wird als root eingelesen – jede Zeile zählt
    func = 'package' if ln.func.startswith('package_') else ln.func   # Split-Pakete: package_foo()
    if func in rule.only_in:
        return True
    return ln.toplevel_exec and bool(rule.only_in & BUILD_FUNCS)


def scan_text(text: str, filename: str = 'PKGBUILD', old_text: str | None = None) -> list[Finding]:
    """Regeln zeilenweise anwenden. Funktionsgebundene Regeln (z. B. Netzwerk in build()) gelten auch in
    Split-Funktionen (package_*), für Befehle auf oberster Ebene, in .install-Dateien überall – und in der
    ganzen Datei, sobald die Struktur nicht sicher erkannt wird (lieber ein Befund zu viel als einer zu wenig)."""
    findings: list[Finding] = []
    old_lines = set(old_text.splitlines()) if old_text is not None else None
    lines = text.splitlines()
    ctxs, certain = _contexts(lines)
    if not certain:
        ctxs = [_Line(c.text, ANY, c.toplevel_exec) for c in ctxs]
        findings.append(Finding('AUR000', 'info', _('Analyse'), _('Struktur nicht eindeutig erkannt'),
                                _('Funktionsgebundene Regeln wurden auf die ganze Datei angewendet.'), filename))
    install_file = not filename.endswith('PKGBUILD')
    in_sums = False

    for no, (raw, ln) in enumerate(zip(lines, ctxs), 1):
        line = ln.text
        is_new = old_lines is not None and raw not in old_lines
        for rule in RULES:
            if not _applies(rule, ln, install_file):
                continue
            if rule.id == 'AUR016' and (SKIP_RE.match(line) or in_sums or re.search(r"'[0-9a-f]{32,}'", line)):
                continue
            if rule.match(line):
                findings.append(Finding(rule.id, rule.severity, _(rule.category), _(rule.title), _(rule.advice),
                                        filename, no, raw.strip(), is_new))
        if SKIP_RE.match(line):
            in_sums = not line.rstrip().endswith(')')
        elif in_sums and ')' in line:
            in_sums = False

    has_sig = re.search(r'validpgpkeys=\(', text)
    if has_sig:  # http:// mit GPG-Signaturprüfung ist vertretbar
        for f in findings:
            if f.rule_id == 'AUR013':
                f.severity, f.title = 'niedrig', f.title + _(' (GPG-signiert)')

    # SKIP-Prüfsummen ohne VCS-Quelle
    if re.search(r"sums(_\w+)?=\([^)]*'SKIP'", text) and not VCS_RE.search(text):
        no = next((i for i, l in enumerate(lines, 1) if 'SKIP' in l), 0)
        findings.append(Finding('AUR020', 'niedrig' if has_sig else 'hoch', _('Integrität'),
                                _('Prüfsumme SKIP für Nicht-VCS-Quelle') + (_(' (GPG-signiert)') if has_sig else ''),
                                _('Ohne Prüfsumme kann der Download unbemerkt ausgetauscht werden.'),
                                filename, no, lines[no - 1].strip() if no else '',
                                bool(old_lines is not None and no and lines[no - 1] not in old_lines)))
    return findings


def install_names(pkgbuild: str, pkgbase: str, pkgname: str) -> list[str]:
    """Alle per install= referenzierten Dateien, $pkgname/$pkgbase aufgelöst (auch in package_*()).

    Oberfläche und Executor nutzen dieselbe Auflösung, damit beide dieselben Dateien prüfen und denselben
    Freigabe-Hash bilden. Nicht auflösbare Namen (z. B. mit $pkgver) werden ausgelassen."""
    out: list[str] = []
    for m in INSTALL_RE.finditer(pkgbuild):
        name = m.group(1)
        for var, val in (('pkgbase', pkgbase), ('pkgname', pkgname)):
            name = name.replace('${' + var + '}', val).replace('$' + var, val)
        if re.fullmatch(r'[\w.+-]+', name) and not name.startswith('.') and name not in out:
            out.append(name)
    return out


def _meta_findings(name: str, meta: dict) -> list[Finding]:
    out: list[Finding] = []
    if meta.get('maintainer') is None:
        out.append(Finding('META01', 'mittel', _('Vertrauen'), _('Paket ist verwaist (kein Maintainer)'),
                           _('Jeder kann das Paket übernehmen – Änderungen genau prüfen.')))
    today = date.today()
    old, new = (meta.get('files') or {}).get('PKGBUILD', ('', ''))
    m_old, m_new = _header_maintainer(old), _header_maintainer(new)
    if old and m_old and m_new and m_old != m_new and not meta.get('maintainer_changed'):
        out.append(Finding('META02', 'hoch', _('Vertrauen'), _('Maintainer im PKGBUILD gewechselt ({old} → {new})', old=m_old, new=m_new),
                           _('Übernahmen kurz vor einem Update sind ein bekanntes Angriffsmuster.')))
    changed = _iso_date(meta.get('maintainer_changed'))
    if changed:
        days = (today - changed).days
        if days <= 30:
            out.append(Finding('META02', 'hoch', _('Vertrauen'),
                               _('Maintainer vor {days} Tagen gewechselt ({old} → {new})', days=days,
                                 old=meta.get('previous_maintainer'), new=meta.get('maintainer')),
                               _('Übernahmen kurz vor einem Update sind ein bekanntes Angriffsmuster.')))
    votes = meta.get('votes', 0)
    if not isinstance(votes, int | float) or votes < 10:
        out.append(Finding('META03', 'niedrig', _('Vertrauen'), _('Wenige Stimmen ({votes}), kaum genutzt', votes=meta.get('votes')),
                           _('Weniger Augen auf dem PKGBUILD.')))
    first = _iso_date(meta.get('first_submitted'))
    if first and (today - first).days < 14:
        out.append(Finding('META04', 'mittel', _('Vertrauen'), _('Paket erst vor kurzem eingereicht'),
                           _('Neue Pakete haben noch keine Prüfhistorie.')))
    if meta.get('out_of_date'):
        out.append(Finding('META05', 'info', _('Pflege'), _('Als veraltet markiert'), _('Upstream ist neuer als das Paket.')))
    host = lambda t: set(re.findall(r'https?://([^/"\'$\s]+)', t))  # noqa: E731
    added = host(new) - host(old)
    if old and added:
        out.append(Finding('META06', 'hoch', _('Quelle'), _('Neue Download-Domain im Update: {hosts}', hosts=', '.join(sorted(added))),
                           _('Quellwechsel ohne Ankündigung upstream prüfen.')))
    return out


def _iso_date(value) -> date | None:
    """Datum aus Fremddaten (AUR-RPC); Unlesbares wird ignoriert statt den ganzen Scan abzubrechen."""
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _header_maintainer(text: str) -> str | None:
    m = re.search(r'^#\s*Maintainer:\s*([^<\n]+)', text, re.M)
    return m.group(1).strip() if m else None


def scan_package(name: str, meta: dict | None) -> ScanResult | None:
    if not meta:
        return None
    result = ScanResult(name, files=meta['files'])
    for fname, (old, new) in meta['files'].items():
        result.findings += scan_text(new, fname, old)
    result.findings += _meta_findings(name, meta)
    result.findings.sort(key=lambda f: (SEVERITY_ORDER.index(f.severity), f.file, f.line))
    return result


def scan_all(metas: dict[str, dict]) -> dict[str, ScanResult]:
    return {name: scan_package(name, meta) for name, meta in metas.items()}


def diff(old: str, new: str, filename: str) -> list[tuple[str, str]]:
    """Unified Diff als Liste (Art, Zeile) mit Art in {'+', '-', ' ', '@'}."""
    out = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), f'a/{filename}', f'b/{filename}',
                                     lineterm='', n=3):
        if line.startswith(('---', '+++')):
            continue
        out.append((line[0] if line[0] in '+-@' else ' ', line))
    return out


def review_hash(files: dict[str, str], package: str = '') -> str:
    """Fingerabdruck einer Prüf-Freigabe: Paketname plus jedes Paar (Dateiname, Inhalt) – PKGBUILD und .install.

    „Als geprüft markiert“ gilt nur für genau diese Dateien mit genau diesem Inhalt. Ändert sich ein Inhalt,
    kommt eine Datei hinzu, fällt eine weg oder wird sie umbenannt, passt der Hash nicht mehr und der Scanner
    fragt wieder nach. Die Reihenfolge spielt keine Rolle; jedem Teil wird seine Länge vorangestellt, damit sich
    die Grenze zwischen Name und Inhalt nicht verschieben lässt."""
    h = hashlib.sha256(b'update-planner-review-v2\0')
    for part in (package, *(x for name in sorted(files) for x in (name, files[name]))):
        b = part.encode('utf-8', 'replace')
        h.update(len(b).to_bytes(8, 'big') + b)
    return h.hexdigest()
