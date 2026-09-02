#!/usr/bin/env python3
"""Enumerate every SPA route registered via route('/path', ...) across app/static/js."""
import re
from pathlib import Path

JS_ROOT = Path(__file__).resolve().parents[1] / 'app/static/js'
# route('/x', ...) / route(`/x`, ...) — capture the path literal
ROUTE_RE = re.compile(r"""\broute\(\s*(['"`])(/[^'"`]*?)\1""")

routes = {}  # path -> (file, lineno)
for jsf in sorted(JS_ROOT.rglob('*.js')):
    src = jsf.read_text(encoding='utf-8', errors='replace')
    for m in ROUTE_RE.finditer(src):
        path = m.group(2)
        lineno = src[:m.start()].count('\n') + 1
        rel = str(jsf.relative_to(JS_ROOT))
        # path params like '/bills/:id' or template literals
        routes.setdefault(path, []).append((rel, lineno))

print(f"TOTAL registered routes: {len(routes)}\n")
for path in sorted(routes):
    files = routes[path]
    loc = ', '.join(f"{f}:{ln}" for f, ln in files)
    print(f"  {path:42s} {loc}")

# Split literal routes (visitable) from param routes (need a sample id)
literal = [p for p in routes if '${' not in p and ':' not in p]
param = [p for p in routes if '${' in p or ':' in p]
print(f"\nDirectly visitable: {len(literal)}")
print(f"Need params (sample ids required): {len(param)}")
for p in sorted(param):
    print(f"  PARAM: {p}  -> {routes[p][0][0]}")
