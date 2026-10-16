"""Export only versionable project files, without local credentials or build caches."""
from pathlib import Path
import zipfile


def main():
    root = Path(__file__).resolve().parent
    names = [
        '.gitignore', 'README.md', 'requirements.txt', 'main.py',
        'build.ps1', 'collect_licenses.py', 'export_source.py',
        '使用说明.txt', '以管理员身份运行.cmd', 'THIRD_PARTY_NOTICES.txt',
    ]
    files = [root / name for name in names]
    for name in ('lanshare', 'tests'):
        files.extend(p for p in (root / name).rglob('*.py') if '__pycache__' not in p.parts)
    archive = root / 'LANShare-GitHub-Source.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(files):
            output.write(path, path.relative_to(root).as_posix())
    print(f'Source archive: {archive} ({len(files)} files)')


if __name__ == '__main__':
    main()
