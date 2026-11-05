"""Parse/compile the copied snapshot without importing application modules."""
from pathlib import Path
import ast
import hashlib
import json
import sys
import tomllib
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_support import protect

root = Path(__file__).resolve().parent
source = root / 'source'
blocked = protect(source)
manifest = json.loads((root / 'source-manifest.json').read_text(encoding='utf-8'))
rows = []
for item in manifest['files']:
    path = source / item['path']
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == item['sha256'], item['path']
    if path.suffix == '.py':
        tree = ast.parse(raw, filename=item['path'])
        compile(tree, item['path'], 'exec')
        rows.append(item['path'])
with (source / 'config.example.toml').open('rb') as f:
    cfg = tomllib.load(f)
result = {'copied_files_verified': len(manifest['files']), 'python_compiled': len(rows),
          'python_files': rows, 'toml_parsed': True, 'python': sys.version,
          'platform': str(sys.getwindowsversion()), 'blocked_attempts': blocked,
          'scope': 'Syntax/bytecode compilation only; no app imports, service startup or provider'}
(root / 'static-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k != 'python_files'}, ensure_ascii=False))
