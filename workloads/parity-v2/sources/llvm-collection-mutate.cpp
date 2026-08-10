#include <cstdint>
#include <iostream>
int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t n = 0;
  if (!(std::cin >> n) || n <= 0) return 2;
  std::int64_t value = 0;
  std::int64_t total = 0;
  for (std::int64_t index = 0; index < n; ++index) {
    value = (value + index * 3 + 1) % 100003;
    total = (total + value) % 1000000007;
  }
  std::cout << total << '\n';
}
