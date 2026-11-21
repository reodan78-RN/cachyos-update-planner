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


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            if ch == quote:
                quote = None
        elif ch in '\'"':
            quote = ch
        elif ch == '#':
            break
        out.append(ch)
    return ''.join(out)


FUNC_RE = re.compile(r'^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{?')
SKIP_RE = re.compile(r'^\s*(sha\d+sums|b2sums|md5sums)(_\w+)?=\(')
VCS_RE = re.compile(r'(git|svn|hg|bzr|fossil)\+|\.git\b')


def scan_text(text: str, filename: str = 'PKGBUILD', old_text: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    old_lines = set(old_text.splitlines()) if old_text is not None else None
    func = ''
    depth = 0
    in_sums = False
    lines = text.splitlines()

    for no, raw in enumerate(lines, 1):
        line = _strip_comment(raw)
        m = FUNC_RE.match(line)
        if m and depth == 0:
            func = m.group(1)
        depth += line.count('{') - line.count('}')
        if depth <= 0:
            depth = 0
            if not m:
                func = ''

        is_new = old_lines is not None and raw not in old_lines
        for rule in RULES:
            if rule.only_in is not None and func not in rule.only_in:
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


def _meta_findings(name: str, meta: dict) -> list[Finding]:
    out: list[Finding] = []
    if meta.get('maintainer') is None:
        out.append(Finding('META01', 'mittel', _('Vertrauen'), _('Paket ist verwaist (kein Maintainer)'),
                           _('Jeder kann das Paket übernehmen – Änderungen genau prüfen.')))
    today = date.today()
    old, new = meta['files'].get('PKGBUILD', ('', ''))
    m_old, m_new = _header_maintainer(old), _header_maintainer(new)
    if old and m_old and m_new and m_old != m_new and not meta.get('maintainer_changed'):
        out.append(Finding('META02', 'hoch', _('Vertrauen'), _('Maintainer im PKGBUILD gewechselt ({old} → {new})', old=m_old, new=m_new),
                           _('Übernahmen kurz vor einem Update sind ein bekanntes Angriffsmuster.')))
    if meta.get('maintainer_changed'):
        days = (today - date.fromisoformat(meta['maintainer_changed'])).days
        if days <= 30:
            out.append(Finding('META02', 'hoch', _('Vertrauen'),
                               _('Maintainer vor {days} Tagen gewechselt ({old} → {new})', days=days,
                                 old=meta.get('previous_maintainer'), new=meta['maintainer']),
                               _('Übernahmen kurz vor einem Update sind ein bekanntes Angriffsmuster.')))
    if meta.get('votes', 0) < 10:
        out.append(Finding('META03', 'niedrig', _('Vertrauen'), _('Wenige Stimmen ({votes}), kaum genutzt', votes=meta.get('votes')),
                           _('Weniger Augen auf dem PKGBUILD.')))
    if meta.get('first_submitted') and (today - date.fromisoformat(meta['first_submitted'])).days < 14:
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


def review_hash(texts) -> str:
    """Fingerabdruck der geprüften Dateien (PKGBUILD und ggf. .install), unabhängig von Reihenfolge und Dateinamen.

    „Als geprüft markiert“ gilt nur für genau diesen Inhalt: ändert sich das PKGBUILD danach (neue Version,
    Maintainer-Übernahme), passt der Hash nicht mehr und der Scanner fragt wieder nach."""
    digests = sorted(hashlib.sha256(t.encode('utf-8', 'replace')).hexdigest() for t in texts)
    return hashlib.sha256('\n'.join(digests).encode()).hexdigest()
