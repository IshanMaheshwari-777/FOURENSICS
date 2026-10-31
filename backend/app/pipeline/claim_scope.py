"""Conservative scope checks for claims that need actual test outcomes."""
import re
from ..schemas import Claim


def test_outcome_claim(claim: Claim) -> bool:
    return claim.type == "pr_test" and bool(re.search(
        r"\b(?:pass(?:es|ed|ing)?|fail(?:s|ed|ing|ure|ures)?|green|no regression|without regressions?|"
        r"successful(?:ly)?|execut(?:e|es|ed|ing))\b", claim.text, re.I))


def bare_test_change(claim: Claim) -> bool:
    """A diff's test-change flag establishes presence, not counts, coverage or success."""
    return claim.type == "pr_test" and not test_outcome_claim(claim) and not re.search(
        r"\d|`|\b(?:no|not|never|without|covers?|covering)\b", claim.text, re.I) and bool(re.search(
        r"\b(?:add(?:s|ed|ing)?|chang(?:e|es|ed|ing)|updat(?:e|es|ed|ing))\b.*\btests?\b", claim.text, re.I))
