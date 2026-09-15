import sys
from libs.dev_test.dev_test import app_main

if __name__ == "__main__":
    raise SystemExit(app_main(sys.argv[1:], prog="dev", web=True))
