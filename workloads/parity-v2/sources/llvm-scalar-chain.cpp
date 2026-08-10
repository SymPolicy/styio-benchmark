#include <cstdint>
#include <iostream>
int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t n = 0;
  if (!(std::cin >> n) || n <= 0) return 2;
  std::int64_t value = 3;
  for (std::int64_t index = 0; index < n; ++index)
    value = (value * 33 + index + 7) % 1000000007;
  std::cout << value << '\n';
}
