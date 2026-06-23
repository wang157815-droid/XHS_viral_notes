import sys, ast, tokenize, io
sys.stdout.reconfigure(encoding='utf-8')

path = r'E:\redMuse\XHS_viral_notes\backend\app\application\comment_pipeline.py'
src = open(path, encoding='utf-8').read()

try:
    ast.parse(src)
    print("No errors")
except SyntaxError as e:
    print(f"SyntaxError line {e.lineno}: {e.msg}")
    lines = src.splitlines()
    start = max(0, e.lineno - 6)
    end = min(len(lines), e.lineno + 4)
    for i, line in enumerate(lines[start:end], start=start+1):
        marker = " <<<" if i == e.lineno else ""
        print(f"{i:5d}|{line}{marker}")
