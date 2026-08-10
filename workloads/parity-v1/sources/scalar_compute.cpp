#include <cstdint>
#include <iostream>

int main() {
  std::int64_t iterations = 0;
  if (!(std::cin >> iterations) || iterations <= 0) {
    return 2;
  }
  std::int64_t accumulator = 17;
  constexpr std::int64_t modulus = 2147483647;
  for (std::int64_t index = 0; index < iterations; ++index) {
    accumulator = (accumulator * 1103515245 + index * 12345 + 1013904223) % modulus;
  }
  std::cout << accumulator << '\n';
  return 0;
}
