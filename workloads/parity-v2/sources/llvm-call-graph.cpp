#include <cstdint>
#include <iostream>
namespace {
inline std::int64_t leaf(std::int64_t value) { return (value * 17 + 5) % 1000003; }
inline std::int64_t branch(std::int64_t value) { return (leaf(value) + leaf(value + 1)) % 1000003; }
}
int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t n = 0;
  if (!(std::cin >> n) || n <= 0) return 2;
  std::int64_t value = 11;
  for (std::int64_t index = 0; index < n; ++index) value = branch(value + index);
  std::cout << value << '\n';
}
