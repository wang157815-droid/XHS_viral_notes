import base64
import pathlib

paths = [
    pathlib.Path(r"e:/redMuse/XHS_viral_notes/_mempalace_staging/inv_frontend.json"),
    pathlib.Path(r"e:/redMuse/XHS_viral_notes/_mempalace_staging/inv_rbac_p1.json"),
    pathlib.Path(r"e:/redMuse/XHS_viral_notes/_mempalace_staging/inv_rbac_p2.json"),
]
for p in paths:
    print("---")
    print(base64.b64encode(p.read_bytes()).decode("ascii"))
