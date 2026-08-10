#include <cstdint>
#include <iostream>

int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);
  std::int64_t steps = 0;
  if (!(std::cin >> steps) || steps <= 0) return 2;
  std::int64_t energy = 17;
  std::int64_t momentum = 31;
  for (std::int64_t index = 0; index < steps; ++index) {
    energy = (energy * 1000003 + index * 97 + 13) % 1000000007;
    momentum = (momentum + energy + index * 7) % 1000000007;
  }
  std::cout << energy << '\n' << momentum << '\n';
}
