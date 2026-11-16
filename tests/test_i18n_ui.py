"""Kein sichtbarer UI-Text darf als nacktes Literal an NiceGUI-Elemente gehen."""

import ast
from pathlib import Path

UI_CALLS = {'label', 'button', 'badge', 'chip', 'notify', 'link', 'switch', 'tooltip', 'tab', 'input', 'textarea',
            'notification', 'checkbox'}
TEXT_KW = {'placeholder', 'label', 'text', 'message'}
NEUTRAL = {'', '–', '—', 'Repo', 'AUR', 'Flatpak', 'CachyOS Update Planner', 'Repo · AUR · Flatpak', 'pkgname=…',
           'Maintainer', 'Version', 'Score', '· Score', 'System', '· Kernel', 'Arch / CachyOS News'}  # in DE und EN gleich
PKG = Path(__file__).parent.parent / 'update_planner'


def _literal_text(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        consts = ''.join(v.value for v in node.values if isinstance(v, ast.Constant))
        return consts if any(c.isalpha() for c in consts) else None
    return None


def test_no_untranslated_ui_literals():
    problems = []
    for path in [*PKG.glob('*.py'), *PKG.glob('pages/*.py')]:
        if path.name in ('mockdata.py', 'aur_samples.py'):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in UI_CALLS):
                continue
            positional = [] if node.func.attr == 'tab' else node.args[:1]   # ui.tab(name, label=…): name ist intern
            candidates = positional + [k.value for k in node.keywords if k.arg in TEXT_KW]
            for arg in candidates:
                text = _literal_text(arg)
                if text and text.strip() not in NEUTRAL and any(c.isalpha() for c in text):
                    problems.append(f'{path.name}:{node.lineno}: {text[:70]!r}')
    assert problems == []
