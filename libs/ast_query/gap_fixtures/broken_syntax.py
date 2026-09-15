# INTENTIONALLY UNPARSEABLE.
# This file exists only to pin LG-17 (design/look.md §8.1): `_parse_python`
# swallows SyntaxError -> None, so `outline` on this file returns "" with
# exit 0 instead of reporting the syntax error. Do NOT "fix" the syntax
# below — that would silently defeat the pin in libs/ast_query/test_gaps.txt.

def helper(x)
    return x + 1
