#include <cstdint>
#include <iostream>
int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t n = 0;
  if (!(std::cin >> n) || n <= 0) return 2;
  std::int64_t total = 0;
  for (std::int64_t index = 0; index < n; ++index) {
    const std::int64_t first = index % 97;
    const std::int64_t second = (index * 5 + 1) % 101;
    total = (total + first * 3 + second) % 1000000007;
  }
  std::cout << total << '\n';
}
