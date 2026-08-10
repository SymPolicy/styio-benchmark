#include <cstdint>
#include <iostream>
int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t n = 0;
  if (!(std::cin >> n) || n <= 0) return 2;
  std::int64_t total = 0;
  for (std::int64_t row = 0; row < n; ++row)
    for (std::int64_t col = 0; col < n; ++col) {
      std::int64_t cell = 0;
      for (std::int64_t inner = 0; inner < n; ++inner)
        cell += ((row + inner + 1) * (inner + col + 2)) % 97;
      total = (total + cell) % 1000000007;
    }
  std::cout << total << '\n';
}
