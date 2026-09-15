// Fixture for libs/ast_query JS tests. Known structure:
//   top-level function helper
//   top-level function greet (calls helper)
//   class Greeter with method speak (calls greet)
function helper(x) {
  return x + 1;
}

function greet(name) {
  return helper(name);
}

class Greeter {
  speak(name) {
    return greet(name);
  }
}
