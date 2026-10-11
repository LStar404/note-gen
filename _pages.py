
import json, glob, os, shutil, sys
sys.path.insert(0, ".")

doc = "doc-a016ba58d56d"
man_path = f"data/{doc}/manifest.json"
shutil.copy(man_path, man_path + ".keep")
print("manifest 已备份")

from src.ingest.parse_pdf import parse_pdf
from src.core.paths import Layout
layout = Layout(os.getcwd())
pdf = f"data/textbooks/{doc}.pdf"
parse_pdf(pdf, layout.ingest_root, "pypdf", layout=layout, doc_id=doc,
          title="史济怀·复变函数", profile="textbook")
print("pages 重新生成完成")

# 换回原 manifest（章节/绑定不变）
shutil.copy(man_path + ".keep", man_path)
os.remove(man_path + ".keep")
print("manifest 已还原（章节保持原样）")

m = json.load(open(man_path, encoding="utf-8"))
print("章节:", [(c["chapter_id"], c.get("title", "")[:12]) for c in m.get("chapters", [])][:4], "...")
print("pages 文件数:", len(glob.glob(f"ingest_out/{doc}/pages/*.json")))
rows = sum(1 for f in glob.glob(f"data/{doc}/blocks/*.jsonl") for _ in open(f, encoding="utf-8"))
print("块数（应为 103）:", rows)
