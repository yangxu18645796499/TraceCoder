"""ClassEval-only selection policy for the new 8192 cohort.

Select bytes, do not repair, merge blocks or reject by AST validity.
"""
import re


def extract_class_code(response, entry_point, fallback):
    blocks = re.findall(r'```(?:python|python3|py)\s*\n([\s\S]*?)```', response, re.I)
    wanted = re.compile(r'^\s*class\s+' + re.escape(entry_point) + r'\b', re.M)
    candidates = [block for block in blocks if wanted.search(block)]
    return candidates[-1].strip() if candidates else fallback(response)


def configure(module, public):
    """Override an isolated method worker only, never a shared host module."""
    original = module.extract
    def extract(response, task):
        return extract_class_code(response, public['entry_point'], lambda text: original(text, task))
    module.extract = extract
