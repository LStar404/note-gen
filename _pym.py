
import pymupdf, glob
pdf = glob.glob(r"data/textbooks/doc-a016ba58d56d.pdf")[0]
doc = pymupdf.open(pdf)
page = doc[3]   # 第 4 页
txt = page.get_text()
print("=== pymupdf 第 4 页（前 900 字符）===")
print(repr(txt[:900]))
