"""Check a release checkout without printing sensitive values."""
import argparse
import ast
import re
import sqlite3
import subprocess
from pathlib import Path
from contextlib import closing


def release_files(root):
    result = subprocess.run(
        ['git', '-c', 'core.quotepath=false', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=root, check=True, capture_output=True, text=True, encoding='utf-8')
    return [name for name in result.stdout.split('\0') if name]


def check_files(root, files, private_names=()):
    findings = []
    forbidden = {'.env', 'config.ini', 'ai_credentials.dat', 'supplier_data.db'}
    secrets = re.compile(r'(?:sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})')
    for relative in files:
        path = root / relative
        parts = Path(relative).parts
        if path.name in forbidden or path.suffix.lower() in ('.db','.sqlite','.sqlite3') or any(part in ('qa','backups','attachments','.local') for part in parts):
            findings.append((relative, 'private-artifact'))
            continue
        if path.name.startswith('.tmp_'):
            findings.append((relative, 'temporary-script'))
            continue
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            continue
        if secrets.search(content):
            findings.append((relative, 'possible-secret'))
        if any(name and name in content for name in private_names):
            findings.append((relative, 'private-entity-name'))
        if relative.replace('\\','/') == 'db/migrations/operating_entities.py':
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != 'executemany' or len(node.args) != 2:
                    continue
                if not isinstance(node.args[0], ast.Constant) or 'INSERT INTO operating_entities' not in str(node.args[0].value):
                    continue
                try:
                    seeds = ast.literal_eval(node.args[1])
                except (ValueError, TypeError):
                    findings.append((relative, 'unreviewed-entity-seed'))
                    continue
                if any(not str(row[0]).startswith('示例') for row in seeds):
                    findings.append((relative, 'nonanonymous-entity-seed'))
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--private-ledger', type=Path, help='Optional readonly local comparison; never printed')
    args = parser.parse_args()
    root = args.root.resolve()
    files = release_files(root)
    private_names = []
    if args.private_ledger:
        with closing(sqlite3.connect(args.private_ledger.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            conn.execute('PRAGMA query_only=ON')
            private_names = [row[0] for row in conn.execute('SELECT name FROM operating_entities') if not row[0].startswith('示例')]
    findings = check_files(root, files, private_names)
    for file, rule in findings:
        print(f'{file}: {rule}')
    if findings:
        raise SystemExit(1)
    print(f'PASS: release privacy; {len(files)} files, anonymous entity seeds, no private artifacts or detected credentials.')


if __name__ == '__main__':
    main()
