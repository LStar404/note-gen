import sys, json
from marker.models import create_model_dict
from marker.converters.pdf import PdfConverter

models = create_model_dict()
conv = PdfConverter(config={"output_format": "json"}, artifact_dict=models)
doc = conv.build_document(sys.argv[1])

def call(obj, name):
    v = getattr(obj, name, None)
    if callable(v):
        try:
            return v(doc)
        except TypeError:
            return v()
    return v

pg = doc.pages[0]
out = {}
pp = getattr(pg, "polygon", None)
out["page_polygon"] = {"type": type(pp).__name__, "attrs": [a for a in dir(pp) if not a.startswith("_")][:25],
                       "dump": (pp.model_dump() if hasattr(pp, "model_dump") else str(pp))}
ch = list(call(pg, "contained_blocks") or [])
c = ch[0]
cp = getattr(c, "polygon", None)
out["block_polygon"] = {"type": type(cp).__name__,
                        "attrs": [a for a in dir(cp) if not a.startswith("_")][:25],
                        "dump": (cp.model_dump() if hasattr(cp, "model_dump") else str(cp)),
                        "bbox": (list(cp.bbox) if hasattr(cp, "bbox") and cp.bbox is not None else None)}
out["block_meta"] = {k: str(getattr(c, k, None))[:60] for k in ("id", "block_type", "source", "text_extraction_method")}
out["children_types"] = [type(x).__name__ for x in ch[:15]]
print("PROBE_JSON:" + json.dumps(out, ensure_ascii=False, default=str))
