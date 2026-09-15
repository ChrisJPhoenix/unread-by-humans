"""Fixture for libs/ast_query LG-6 (constants are not indexed) tests."""
# Known 1-based positions: module const _GLOB_METACHARS=3:0; class const DEFAULT_TIMEOUT=7:4.
_GLOB_METACHARS: str = "*?["


class Config:
    DEFAULT_TIMEOUT: int = 30

    def get_timeout(self):
        return self.DEFAULT_TIMEOUT
