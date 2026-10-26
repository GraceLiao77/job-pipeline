import re

IGNORE = [r"\blead\b", r"\bSenior\b", r"\bSr\b", r"\bstaff\b", r"\bhead of\b", r"\bPrincipal\b", r"\bmanager\b", r"\bdirector\b", r"\bsingapore\b"]

def quick_reject(page_title: str) -> str | None:
    """看标题就能判断不合适的岗位 → 返回原因；不确定的 → 返回 None（交给 Claude 判断）。"""
    for item in IGNORE:
        case = re.search(item, page_title, re.IGNORECASE)
        if case:
            return f"标题含「{case.group(0)}」"   # 记下是哪个词命中的，方便以后查
    return None   # 所有词都检查完都没命中，才交给 Claude

