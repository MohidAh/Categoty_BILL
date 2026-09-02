#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# Whole-system API field-contract audit (v8.18.9 review)
#
# Bug class this hunts: frontend reads r.<field> that the backend route
# NEVER returns (exactly what broke /reports/monthly-close for 8+ minor
# versions). Approach:
#   Backend : AST-parse app/**/*.py -> route path -> handler -> resolve
#             return dict keys (incl. cross-module helper calls, 1 level
#             of recursion, dict-literal assignments, dict(**kw) calls).
#   Frontend: regex-parse app/static/js/**/*.js -> api()/apiPost()/fetch()
#             call sites -> response variable -> property reads scoped to
#             the enclosing route()/function block.
#   Report  : fields read but never returned, and frontend URLs with no
#             backend route (potential 404s).
#
# Heuristic tool: every finding is manually triaged afterwards.
# ═══════════════════════════════════════════════════════════════════
import ast
import re
import sys
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parents[1]   # repo root (parent of scripts/)
APP = REPO / 'app'
JS_ROOT = REPO / 'app/static/js'

# ------------------------------------------------------------------ backend
# module_name -> {'funcs': {name: {'keys': set|None, 'dynamic': bool}}, 'routes': {path: handler}}
MODULES = {}


def mod_path_for(imported: str) -> Path | None:
    """Map '..reports' / '.insights' / 'reports' to app/<name>.py."""
    name = imported.lstrip('.').split('.')[0]
    for cand in (APP / f'{name}.py', APP / 'routers' / f'{name}.py'):
        if cand.exists():
            return cand
    return None


def dict_keys_from_expr(expr, acc, ctx, depth=0, seen=None):
    """Accumulate top-level keys from a return expression. Sets dynamic flags."""
    if expr is None or depth > 3:
        return
    if isinstance(expr, ast.Dict):
        for k, v in zip(expr.keys, expr.values):
            if k is None:  # **spread
                acc['dynamic'] = True
            elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                acc['keys'].add(k.value)
            else:
                acc['dynamic'] = True
        return
    if isinstance(expr, ast.List):
        for e in expr.elts:
            if isinstance(e, ast.Dict):
                for k, v in zip(e.keys, e.values):
                    if k is None:
                        acc['dynamic'] = True
                    elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                        acc['keys'].add(k.value)
        return
    if isinstance(expr, ast.Call):
        # dict(a=1, b=2) or JSONResponse(content={...})
        f = expr.func
        fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
        if fname == 'dict':
            for kw in expr.keywords:
                if kw.arg:
                    acc['keys'].add(kw.arg)
            if expr.keywords and any(kw.arg is None for kw in expr.keywords):
                acc['dynamic'] = True
            for a in expr.args:
                dict_keys_from_expr(a, acc, ctx, depth + 1)
            return
        # SomeJSONWrapper(content={...})
        for kw in expr.keywords:
            if kw.arg == 'content' or kw.arg == 'json_data':
                dict_keys_from_expr(kw.value, acc, ctx, depth + 1)
                return
        # plain helper call  -> resolve cross-module
        resolve_call(f, expr.args, acc, ctx, depth + 1)
        return
    if isinstance(expr, ast.Name):
        # returning a local var: look for `var = {...}` assignments in this func
        fn = ctx.get('fn')
        if fn is not None:
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id == expr.id:
                            dict_keys_from_expr(node.value, acc, ctx, depth + 1)
        return


def resolve_call(f, args, acc, ctx, depth):
    """f is helper function name (Attribute reports.monthly_summary or Name)."""
    if depth > 3:
        acc['dynamic'] = True
        return
    if isinstance(f, ast.Attribute):  # mod.func
        base = f.value
        if isinstance(base, ast.Name):
            target = ctx.get('imports', {}).get(base.id)
            if target:
                mpath, orig = target
                mname = mpath.stem if hasattr(mpath, 'stem') else None
                if mname and mname in MODULES and orig in MODULES[mname]['funcs']:
                    fn_acc = MODULES[mname]['funcs'][orig]
                    acc['keys'] |= fn_acc['keys']
                    if fn_acc['dynamic']:
                        acc['dynamic'] = True
                    return
    if isinstance(f, ast.Name):  # local func
        mod = MODULES.get(ctx.get('module', ''))
        if mod and f.id in mod['funcs']:
            fn_acc = mod['funcs'][f.id]
            acc['keys'] |= fn_acc['keys']
            if fn_acc['dynamic']:
                acc['dynamic'] = True
    return


def analyze_module(path: Path):
    src = path.read_text(encoding='utf-8', errors='replace')
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    mname = path.stem
    imports = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            mpath = mod_path_for(node.module) or mod_path_for(node.module + '.'.join(['']))
            for a in node.names:
                imports[a.asname or a.name] = (mpath or node.module, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                top = (a.asname or a.name).split('.')[0]
                mpath = mod_path_for(top)
                if mpath:
                    imports[top] = (mpath, None)
    info = {'funcs': {}, 'routes': {}, 'module': mname}
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    for fn in funcs:
        acc = {'keys': set(), 'dynamic': False}
        for node in ast.walk(fn):
            if isinstance(node, ast.Return):
                dict_keys_from_expr(node.value, acc, {'imports': imports, 'module': mname, 'fn': fn})
        # catch `out = {}` + `out["k"] = v` patterns
        has_dict_build = False
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Dict, ast.Call)):
                dict_keys_from_expr(node.value, acc, {'imports': imports, 'module': mname, 'fn': fn})
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        for sub in ast.walk(fn):
                            if isinstance(sub, ast.Assign):
                                for st in sub.targets:
                                    if (isinstance(st, ast.Subscript) and isinstance(st.value, ast.Name)
                                            and st.value.id == t.id
                                            and isinstance(sub.value, ast.Constant) is False
                                            and isinstance(st.slice, ast.Constant)):
                                        acc['keys'].add(st.slice.value)
                                        has_dict_build = True
        info['funcs'][fn.name] = acc

        for dec in fn.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr in ('get', 'post', 'put', 'delete', 'patch') and dec.args:
                    p = dec.args[0]
                    if isinstance(p, ast.Constant) and isinstance(p.value, str):
                        info['routes'][(dec.func.attr.upper(), p.value)] = fn.name
    return info


# load every module first (two passes so cross-module resolution sees all)
py_files = sorted(set(list(APP.glob('*.py')) + list((APP / 'routers').glob('*.py'))))
for p in py_files:
    info = analyze_module(p)
    if info:
        MODULES[info['module']] = info

# second pass to re-resolve with full registry
for p in py_files:
    info = analyze_module(p)
    if info:
        MODULES[info['module']] = info

# (method, path) -> key set
ROUTE_KEYS = {}
for mname, mod in MODULES.items():
    for (method, path), handler in mod['routes'].items():
        acc = mod['funcs'].get(handler)
        if acc:
            key = (method, path)
            ROUTE_KEYS.setdefault(key, {'keys': set(), 'dynamic': False})
            ROUTE_KEYS[key]['keys'] |= acc['keys']
            if acc['dynamic']:
                ROUTE_KEYS[key]['dynamic'] = True

# normalize: {param} -> *
def norm_backend(path):
    path = re.sub(r'\{[^}]+\}', '*', path)
    path = re.sub(r':\w+', '*', path)
    return path.rstrip('/')

ROUTE_NORM = {(method, norm_backend(p)): ((method, p), v) for (method, p), v in ROUTE_KEYS.items()}

HELPER_METHOD = {'api': 'GET', 'apiPost': 'POST', 'apiPut': 'PUT',
                 'apiDelete': 'DELETE', 'apiUpload': 'POST', 'fetch': 'GET'}

# ------------------------------------------------------------------ frontend
JS_FILES = sorted(JS_ROOT.rglob('*.js'))
FRONT_API_CALLS = re.compile(
    r'(?:const|let|var)\s+(\w+)\s*=\s*await\s+(api|apiPost|apiPut|apiDelete|apiUpload|fetch)\s*\(\s*([`\'"])(.*?)\3',
    re.S)
INLINE_AWAIT = re.compile(r'\(\s*await\s+(?:api|apiPost|apiPut|apiDelete|fetch)\s*\(\s*[`\'"](.*?)[`\'"]\s*\)\s*\)\s*\.\s*(\w+)')

JS_NOISE = {
    'then', 'catch', 'finally', 'message', 'length', 'status', 'ok', 'statusText',
    'headers', 'json', 'text', 'arrayBuffer', 'blob', 'map', 'filter', 'forEach',
    'slice', 'join', 'push', 'includes', 'some', 'every', 'find', 'findIndex',
    'reduce', 'sort', 'concat', 'keys', 'values', 'entries', 'toString',
    'hasOwnProperty', 'at', 'flat', 'pop', 'shift', 'unshift', 'splice',
    'indexOf', 'lastIndexOf', 'split', 'trim', 'toLowerCase', 'toUpperCase',
    'padStart', 'padEnd', 'replace', 'replaceAll', 'toFixed', 'toLocaleString',
    'substring', 'substr', 'charAt', 'charCodeAt', 'startsWith', 'endsWith',
    'open', 'close', 'log', 'warn', 'constructor', 'prototype', 'call', 'apply',
    'bind', 'valueOf', 'name', 'stack', 'code', 'type', 'target',
}

findings = []          # (jsfile, lineno, url, field)
missing_routes = []    # (jsfile, lineno, url)
verified = 0
dynamic_unverified = 0

def norm_front(url):
    url = url.replace('\n', '')
    url = re.sub(r'\$\{[^}]*\}', '*', url)
    url = url.split('?')[0]
    return url.rstrip('/')

def fields_read(var, block):
    """Fields read on `var` inside a JS block (top-level only)."""
    flds = set()
    for m in re.finditer(rf'\b{re.escape(var)}\s*\.\s*(\w+)', block):
        flds.add(m.group(1))
    for m in re.finditer(rf'\b{re.escape(var)}\s*\?\.\s*(\w+)', block):
        flds.add(m.group(1))
    for m in re.finditer(rf'\b{re.escape(var)}\s*\[\s*[\'"](\w+)[\'"]\s*\]', block):
        flds.add(m.group(1))
    for m in re.finditer(rf'\{{([^}}]*)\}}\s*=\s*{re.escape(var)}\b', block):
        for part in m.group(1).split(','):
            part = part.strip().split(':')[0].strip()
            if re.fullmatch(r'\w+', part):
                flds.add(part)
    return flds - JS_NOISE

for jsf in JS_FILES:
    src = jsf.read_text(encoding='utf-8', errors='replace')
    lines = src.split('\n')

    # scope blocks: split at `route(` boundaries (page files) or top-level funcs
    blocks = []
    cur = {'start': 0, 'header': '(file)'}
    for i, ln in enumerate(lines):
        if re.search(r'\broute\s*\(\s*[\'"`]/', ln):
            if cur['start'] != i:
                blocks.append({**cur, 'end': i})
            cur = {'start': i, 'header': ln.strip()[:90]}
        elif re.match(r'\s*(async\s+)?function\s+\w+', ln) and i - cur['start'] > 0:
            blocks.append({**cur, 'end': i})
            cur = {'start': i, 'header': ln.strip()[:90]}
    blocks.append({**cur, 'end': len(lines)})

    for b in blocks:
        block = '\n'.join(lines[b['start']:b['end']])
        # find api call sites with assigned vars
        for m in FRONT_API_CALLS.finditer(block):
            var, helper, q, url = m.group(1), m.group(2), m.group(3), m.group(4)
            lineno = b['start'] + block[:m.start()].count('\n') + 1
            # HTTP method: helper name, overridden by explicit options method
            method = HELPER_METHOD.get(helper, 'GET')
            tail = block[m.end():m.end() + 120]
            mm = re.search(r"method\s*:\s*['\"](\w+)['\"]", tail)
            if mm:
                method = mm.group(1).upper()
            # scope for reads: rest of this block
            rest = block[m.end():]
            # but stop if var is reassigned
            reass = re.search(rf'\b{re.escape(var)}\s*=(?!=)', rest)
            scope = rest[:reass.start()] if reass else rest
            flds = fields_read(var, scope)
            if not flds:
                continue
            u = norm_front(url)
            if not u.startswith('/'):
                continue
            hit = ROUTE_NORM.get((method, u))
            if hit is None:
                # try wildcard match on path segments (same method)
                segs = u.split('/')
                for (bm, bp), (orig, v) in ROUTE_NORM.items():
                    if bm != method:
                        continue
                    bsegs = bp.split('/')
                    if len(bsegs) == len(segs):
                        if all(a == b or b == '*' or a == '*' for a, b in zip(segs, bsegs)):
                            hit = (orig, v)
                            break
                # last resort: same path on ANY method (route may be defined
                # via a different verb alias) — flagged separately if mismatch
                if hit is None:
                    for (bm, bp), (orig, v) in ROUTE_NORM.items():
                        segs2 = u.split('/')
                        bsegs2 = bp.split('/')
                        if len(bsegs2) == len(segs2):
                            if all(a == b or b == '*' or a == '*' for a, b in zip(segs2, bsegs2)):
                                hit = (orig, v)
                                break
            if hit is None:
                missing_routes.append((str(jsf.relative_to(REPO)), lineno, u, sorted(flds)[:8], b['header']))
                continue
            orig, v = hit
            if v['dynamic'] or not v['keys']:
                dynamic_unverified += 1
                continue
            unknown = {f for f in flds if f not in v['keys']}
            if unknown:
                findings.append((str(jsf.relative_to(REPO)), lineno, method, u, sorted(unknown), sorted(v['keys'])[:40], b['header']))
            else:
                verified += 1

# ------------------------------------------------------------------ report
print(f'Scanned {len(py_files)} backend modules, {len(ROUTE_KEYS)} routes, {len(JS_FILES)} JS files')
print(f'Frontend call sites: {verified} fully-verified, {dynamic_unverified} dynamic (skipped), '
      f'{len(findings)} MISMATCHES, {len(missing_routes)} unmatched URLs\n')

if findings:
    print('=' * 78)
    print('FIELD MISMATCHES — UI reads fields the backend route never returns:')
    print('=' * 78)
    for f, ln, method, u, unknown, known, hdr in findings:
        print(f'\n{f}:{ln}  [{method}]  {hdr}')
        print(f'  URL    : {u}')
        print(f'  READ   : {", ".join(unknown)}')
        print(f'  KNOWN  : {", ".join(known)}')

if missing_routes:
    print()
    print('=' * 78)
    print('FRONTEND URLS WITH NO MATCHING BACKEND ROUTE:')
    print('=' * 78)
    for f, ln, u, flds, hdr in missing_routes:
        print(f'{f}:{ln}  {u}   reads={flds}')
        print(f'    block: {hdr}')

sys.exit(0)
