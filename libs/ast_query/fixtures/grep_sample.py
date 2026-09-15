# Fixture for libs/ast_query grep tests. Known 1-based positions of the marker token:
#   comment:  line 8, col 3  (inside the # comment, after "# ")
#   string:   line 10, col 15 (inside the string literal)
#   code:     line 12, col 1  (bare NAME identifier tokenizing as NAME)

# Lines 5-6 are padding so token lines are easy to count.

# TODO in a comment

msg = "string TODO here"

TODO_count = 0
