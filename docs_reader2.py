import os
import shutil

decoded = [f for f in os.listdir('docs') if f.endswith('.decoded.txt')]

# Copy each to a simple numbered file
for i, f in enumerate(decoded):
    src = os.path.join('docs', f)
    dst = os.path.join('docs', f'doc_{i}.txt')
    shutil.copy2(src, dst)
    print(f"doc_{i}.txt <- {f}")
