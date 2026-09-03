import ast, glob, os

ROOT = "/Users/shion/Documents/GitHub/dog-monitoring"

def fix_1space(path):
    with open(path) as f:
        content = f.read()
    lines = content.split("\n")
    changed = False
    for i, line in enumerate(lines):
        if not line or not line[0].isspace():
            continue
        spaces = len(line) - len(line.lstrip(" "))
        if spaces % 4 != 0:
            new_spaces = (spaces // 4) * 4
            lines[i] = " " * new_spaces + line[spaces:]
            changed = True
    if changed:
        with open(path, "w") as f:
            f.write("\n".join(lines))
    return changed

ok = 0
bad = 0
for f in sorted(glob.glob(os.path.join(ROOT, "dog_monitoring", "**", "*.py"), recursive=True)):
    rel = os.path.relpath(f, ROOT)
    fix_1space(f)
    try:
        ast.parse(open(f).read())
        print("OK   " + rel)
        ok += 1
    except SyntaxError as e:
        print(f"BAD  {rel}: line {e.lineno}: {e.msg}")
        bad += 1
print(f"\n{ok} OK, {bad} BAD")
