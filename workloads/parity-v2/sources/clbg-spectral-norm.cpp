#include <cstdint>
#include <iostream>

int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t n = 0;
  if (!(std::cin >> n) || n <= 0) return 2;
  std::int64_t value = 0;
  for (std::int64_t index = 0; index < n; ++index) {
    const auto term = (index * 17 + 11) % 1009;
    value = (value + term * term) % 1000000007;
  }
  std::cout << value << '\n';
}
