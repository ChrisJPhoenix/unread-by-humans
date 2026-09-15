# Fixture for libs/ast_query tests. Known 1-based line numbers:
# helper=5, greet=9, Greeter=13, __init__=14, speak=17


def helper(x):
    return x + 1


def greet(name):
    return helper(name)


class Greeter:
    def __init__(self, name):
        self.name = name

    def speak(self):
        return greet(self.name)
