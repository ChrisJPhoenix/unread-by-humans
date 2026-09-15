import sys
from libs.ast_query.ast_query import run_cli_with_status

if __name__ == "__main__":
    text, status = run_cli_with_status(sys.argv[1:])
    if text:
        print(text)
    sys.exit(status)
