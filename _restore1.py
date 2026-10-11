
import os, glob, json, shutil

target = "data/doc-a016ba58d56d"
trash = "data/.trash/doc-a016ba58d56d-20261011T050235Z"

print("=== 1) 冲突检查 ===")
print("   data/doc-a016ba58d56d 已存在?", os.path.exists(target), "（应为 False 才可恢复）")
print("   回收站源存在?", os.path.isdir(trash))
print("   ingest_out 源文本在?", os.path.isdir("ingest_out/doc-a016ba58d56d/pages"),
      "| 页数:", len(glob.glob("ingest_out/doc-a016ba58d56d/pages/*.json")))
print("   PDF 在?", os.path.exists("data/textbooks/doc-a016ba58d56d.pdf"))

print()
print("=== 2) 回收站内容清点 ===")
rows = sum(1 for f in glob.glob(os.path.join(trash, "blocks", "*.jsonl")) for _ in open(f, encoding="utf-8"))
anchors = sum(1 for f in glob.glob(os.path.join(trash, "anchors", "*.jsonl")) for _ in open(f, encoding="utf-8"))
m = json.load(open(os.path.join(trash, "manifest.json"), encoding="utf-8"))
print("   标题:", m.get("title"))
print("   章节:", [(c["chapter_id"], c.get("title", "")[:16]) for c in m.get("chapters", [])])
print("   块行:", rows, "| 锚点行:", anchors)
print("   parser:", (m.get("parser") or {}).get("name"))
