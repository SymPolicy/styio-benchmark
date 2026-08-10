#include <cstdint>
#include <iostream>

int main() {
  char delimiter = 0;
  if (!(std::cin >> delimiter) || delimiter != '[') {
    return 2;
  }
  std::int64_t accumulator = 0;
  std::int64_t index = 0;
  constexpr std::int64_t modulus = 1000000007;
  while (true) {
    std::int64_t value = 0;
    if (!(std::cin >> value)) {
      return 2;
    }
    accumulator = (accumulator + value * ((index % 17) + 1)) % modulus;
    ++index;
    if (!(std::cin >> delimiter)) {
      return 2;
    }
    if (delimiter == ']') {
      break;
    }
    if (delimiter != ',') {
      return 2;
    }
  }
  std::cout << accumulator << '\n';
  return 0;
}
