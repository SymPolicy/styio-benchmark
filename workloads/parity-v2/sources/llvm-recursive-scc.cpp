#include <cstdint>
#include <iostream>
int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t n = 0;
  if (!(std::cin >> n) || n <= 0) return 2;
  std::int64_t value = 19;
  for (std::int64_t index = 0; index < n; ++index) {
    value = (value + index + 9) % 1000003;
    for (int depth = 0; depth < 9; ++depth) value = (value * 3 + depth) % 1000003;
  }
  std::cout << value << '\n';
}
