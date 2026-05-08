import os

# Read key files by index
files_to_read = [7, 12, 5, 21, 22, 20, 18, 4, 0]
md_files = [f for f in os.listdir('docs') if f.endswith('.md')]

for idx in files_to_read:
    f = md_files[idx]
    path = os.path.join('docs', f)
    out_path = os.path.join('docs', f + '.decoded.txt')
    try:
        with open(path, 'r', encoding='utf-8') as file:
            content = file.read()
        with open(out_path, 'w', encoding='utf-8') as out:
            out.write(content)
        print('OK: ' + f + ' -> ' + out_path)
    except Exception as e:
        print('ERROR: ' + f + ' -> ' + str(e))
