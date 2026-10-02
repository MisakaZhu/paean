"""Audit the distribution allowlist, local references and obvious private data."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SKILL = 'skills/paean/'
EXPECTED = {'README.md', 'LICENSE', '.gitignore', 'tests/test_local_behaviour.py',
            'tests/test_synthetic_media.py', 'tools/audit_package.py'}
EXPECTED |= {SKILL + name for name in (
    'SKILL.md', 'agents/openai.yaml', 'config.example.json', 'requirements.txt',
    'scripts/render.py', 'scripts/identify_music.py', 'scripts/paean_config.py',
    'references/configuration.md', 'references/mixing.md', 'references/identification.md')}
PATTERNS = {
    'github_token': r'(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}',
    'private_key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'private_windows_path': r'[A-Za-z]:[\\/](?:Users[\\/]|Downloads[\\/]|WB[\\/]|一姐|像素蛋糕|Claude)',
    'phone_number': r'(?<![0-9])1[3-9][0-9]{9}(?![0-9])',
    'inherited_authorization': r'standing_user_preapproval_[0-9]{4}',
    'signed_result_url': r'https?://[^\s<>\"]+[?&](?:Signature|X-Amz-Signature|q-signature)=',
}


def audit(root: Path = ROOT) -> dict:
    errors, files = [], {}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if '.git' in relative.parts or '__pycache__' in relative.parts:
            continue
        name = relative.as_posix()
        if path.is_symlink():
            errors.append('symlink: ' + name)
        elif path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            if name not in EXPECTED:
                errors.append('unexpected_file: ' + name)
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except UnicodeError:
                errors.append('not_utf8: ' + name)
                continue
            for category, pattern in PATTERNS.items():
                if re.search(pattern, text, re.IGNORECASE):
                    errors.append(category + ': ' + name)
            if path.suffix == '.py':
                try:
                    ast.parse(text, filename=name)
                except SyntaxError:
                    errors.append('invalid_python: ' + name)
            if path.suffix == '.md':
                for href in re.findall(r'\[[^\]]+\]\(([^\s)]+)\)', text):
                    if re.match(r'^[a-z][a-z0-9+.-]*:', href, re.IGNORECASE) or href.startswith(('#', '<')):
                        continue
                    target = (path.parent / unquote(href.split('#', 1)[0])).resolve()
                    # README contains example user media links. Actual package references must exist.
                    if target.suffix.lower() == '.mp4' and href.startswith('./videos/'):
                        continue
                    if not target.is_relative_to(root.resolve()) or not target.exists():
                        errors.append('broken_or_external_local_reference: ' + name + ': ' + href)
    for missing in sorted(EXPECTED - set(files)):
        errors.append('missing_file: ' + missing)
    config = root / (SKILL + 'config.example.json')
    if config.is_file():
        try:
            values = json.loads(config.read_text(encoding='utf-8'))
            for field in ('source_root', 'destination_dir', 'cache_dir', 'drafts_dir'):
                if values.get(field) is not None:
                    errors.append('personal_default: ' + field)
            if any(value is not None for value in values.get('track_aliases', {}).values()):
                errors.append('bundled_personal_track_mapping')
        except (ValueError, AttributeError):
            errors.append('invalid_example_config')
    return {'status': 'passed' if not errors else 'failed', 'files': len(files),
            'errors': errors, 'sha256': files, 'scope': 'allowlist_references_and_obvious_patterns_only'}


if __name__ == '__main__':
    report = audit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report['status'] == 'passed' else 1)
