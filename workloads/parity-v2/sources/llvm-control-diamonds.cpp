#include <cstdint>
#include <iostream>
int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t n = 0;
  if (!(std::cin >> n) || n <= 0) return 2;
  std::int64_t score = 0;
  for (std::int64_t index = 0; index < n; ++index) {
    if (index % 2 == 0) score += index * 3 + 1;
    else if (index % 3 == 0) score -= index * 2 + 5;
    else score += index + 7;
    if (index % 11 == 0) score += index;
  }
  std::cout << score << '\n';
}
