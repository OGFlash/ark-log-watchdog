import re
from typing import List, Dict, Tuple

def build_regexes(cfg: Dict) -> List[re.Pattern]:
    regs = []
    for pat in cfg.get("regex", []):
        try:
            regs.append(re.compile(pat))
        except re.error as e:
            print(f"[WARN] Bad regex '{pat}': {e}")
    return regs

def match_line(text: str, keywords: List[str], regs: List[re.Pattern]) -> Tuple[bool, str]:
    t = text.strip()
    for kw in keywords:
        if kw.lower() in t.lower():
            return True, kw
    for rg in regs:
        if rg.search(t):
            return True, rg.pattern
    return False, ""
