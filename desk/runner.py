"""Runner score (LOG-ONLY until research/runner-model.json says validated). Uses the same features as research/analyze.py."""
import importlib.util, json, math, os
_R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("analyze", os.path.join(_R, "research", "analyze.py")); A = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(A)
def score(fs, ev=None, ch=None):
    try: m = json.load(open(os.path.join(_R, "research", "runner-model.json")))
    except FileNotFoundError: return {"score": None, "note": "no model yet (needs 7-day labels)"}
    x, _ = A.feats({"fs": fs, "ev": ev or {}, "ch": ch})
    X, _ = A.matrix([(x, None)], m["names"], [tuple(s) for s in m["stats"]])
    p = A.pred_lr((m["w"], m["b"]), X)[0]
    return {"score": round(p, 4), "target": m["target"], "validated": m["validated"], "auc": m["pooled_auc"]}
