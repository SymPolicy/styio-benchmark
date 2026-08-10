#include <iostream>
#include <string>

int main() {
  std::string line;
  while (std::getline(std::cin, line)) {
    std::cout << line << '\n';
  }
  return std::cin.eof() ? 0 : 2;
}
