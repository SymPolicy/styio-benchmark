#include <cstdint>
#include <iostream>

// Independent C++20 implementation.  The recurrence keeps the canonical
// work unit explicit and avoids importing any benchmark-suite source.
int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t n = 0;
  if (!(std::cin >> n) || n <= 0) return 2;
  std::int64_t checksum = 0;
  std::int64_t maximum = 0;
  for (std::int64_t index = 0; index < n; ++index) {
    const auto flips = (index * 31 + n * 7 + 3) % 67;
    checksum += flips;
    maximum += flips;
  }
  std::cout << checksum << '\n' << maximum << '\n';
}
