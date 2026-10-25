"""Verify source manifest, dashboard integration and Cognitive Engine unit tests."""
from pathlib import Path
import hashlib,json,subprocess,sys
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'RELEASE_MANIFEST.json').read_text(encoding='utf-8'))
assert not manifest['history_inherited']
assert not manifest['private_or_runtime_data_included']
registered={x['path'] for x in manifest['files']}
assert not any(
    any(part in ('__pycache__','.pytest_cache','.git','.venv') for part in Path(path).parts)
    or path.endswith(('.pyc','.pyo','.DS_Store'))
    for path in registered
), 'Runtime caches must never enter a release manifest'

for entry in manifest['files']:
    p=root/entry['path']
    assert p.is_file() and p.stat().st_size==entry['bytes'],entry['path']
    assert hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256'],entry['path']
for p in root.rglob('*'):
    if p.is_file() and not any(i in p.relative_to(root).parts for i in ('.git','__pycache__','.pytest_cache')) and p.name!='RELEASE_MANIFEST.json':
        assert str(p.relative_to(root)) in registered,p
for n in ('obsidian-vault','learning-hub'):
    assert not (root/n).exists()
assert not (root/'cognitive-engine/runtime/capture-ledger.sqlite3').exists()
for p in root.rglob('*.py'):
    compile(p.read_bytes(),str(p),'exec')
print('PASS: file integrity and no private source data, Python compile',flush=True)
def run(args,where):
    print('RUN',where.relative_to(root),*args[1:],flush=True)
    subprocess.run(args,cwd=where,check=True)
run([sys.executable,'-m','unittest','discover','-s','tests','-q'],root/'academic-dashboard')
run([sys.executable,'-m','pytest','tests','-q','-p','no:cacheprovider'],root/'cognitive-engine')
print('PASS: academic dashboard + cognitive engine test suite',flush=True)
