import ast
import json
import zipfile
import build_distribution


def test_source_distribution_has_complete_local_imports_and_clean_assets(tmp_path):
    dest=tmp_path/'sources'
    archive=build_distribution.package(dest)
    assert json.loads((dest/'packaging/streamers.json').read_text()) == {'streamers':[]}
    assert 'queries: []' in (dest/'config.yaml').read_text()
    root=build_distribution.ROOT
    for p in dest.rglob('*.py'):
        for node in ast.walk(ast.parse(p.read_text(encoding='utf-8'))):
            names=[]
            if isinstance(node,ast.Import):names=[a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom) and node.module:names=[node.module]
            for name in names:
                local=root/(name.replace('.','/')+'.py')
                if local.exists():assert (dest/local.relative_to(root)).exists(), f'{p.name} missing {local.name}'
    with zipfile.ZipFile(archive) as z:
        paths=z.namelist()
        assert all('/data/' not in name and '/.venv/' not in name and not name.endswith('.db') for name in paths)
        assert not any(name.endswith('com.zevent.archive.json') for name in paths)
        assert z.getinfo('sources/installer.command').external_attr >> 16 & 0o111
