// Runs the actual GL1 header against a fake clock/serial/keyboard, never hardware.
#include <algorithm>
#include <cassert>
#include <climits>
#include <cstdint>
#include <deque>
#include <iostream>
#include <set>
#include <string>
#include <vector>

class String : public std::string {
 public:
  using std::string::string;
  String(const std::string &v) : std::string(v) {}
  int indexOf(char ch, int start = 0) const {
    auto result = find(ch, std::max(0, start));
    return result == npos ? -1 : static_cast<int>(result);
  }
  String substring(unsigned int start) const {
    return start >= size() ? "" : String(substr(start));
  }
  String substring(unsigned int start, unsigned int end) const {
    if (start > end) std::swap(start, end);
    return start >= size() ? "" : String(substr(start, std::min<unsigned int>(end, size()) - start));
  }
  bool startsWith(const std::string &v) const { return rfind(v, 0) == 0; }
  long toInt() const { return std::strtol(c_str(), nullptr, 10); }
  void trim() {
    auto start = find_first_not_of(" \r\n\t"), end = find_last_not_of(" \r\n\t");
    *this = start == npos ? "" : substr(start, end - start + 1);
  }
};

unsigned long now = 0;
unsigned long millis() { return now; }
struct KeyboardMock {
  std::set<int> down;
  int presses = 0, releases = 0;
  bool fail = false;
  int press(uint8_t code) { ++presses; if (fail) return 0; down.insert(code); return 1; }
  void releaseAll() { down.clear(); ++releases; }
} Keyboard;
struct SerialMock {
  std::deque<char> input;
  std::vector<std::string> output;
  void println(const String &value) { output.push_back(value); }
  bool available() const { return !input.empty(); }
  char read() { char v = input.front(); input.pop_front(); return v; }
  void add(const std::string &v) { for (char c : v) input.push_back(c); }
} Serial;

#include "../arduino/wincro_hid/gpt_play.h"

int main() {
  glCommand("GL1"); assert(Serial.output.back() == "GL1:READY" && Keyboard.down.empty());
  glCommand("GLS,0000000000000001,100,218,122");
  assert(glActive && Keyboard.down.size() == 2);
  now = 99; glTick(); assert(glActive);
  now = 100; glTick(); assert(!glActive && glState == "DONE" && Keyboard.down.empty());
  int count = Keyboard.presses;
  glCommand("GLS,0000000000000001,100,218,122"); assert(Keyboard.presses == count);
  glCommand("GLS,0000000000000001,200,218,122"); assert(Serial.output.back() == "ERR:GL_REUSED");
  glCommand("GLS,0000000000000002,1000,218");
  now = 200; glCommand("GLP,0000000000000002");
  now = 499; glTick(); assert(glActive);
  now = 500; glTick(); assert(!glActive && glState == "EXPIRED" && Keyboard.down.empty());
  glCommand("GLS,0000000000000003,1000,218");
  Serial.add("GLP,0000000000000003");
  assert(glPoll()); assert(glActive); // incomplete command never blocks watchdog
  now = 800; glPoll(); assert(!glActive && Keyboard.down.empty());
  Serial.add("\n"); glPoll();
  glCommand("GLS,0000000000000004,1000,218");
  Serial.add("KA\n"); glPoll(); assert(!glActive && glState == "CANCELLED");
  glCommand("GLS,0000000000000005,1000,218");
  glCommand("KQ,122"); assert(Serial.output.back() == "ERR:GL_BUSY");
  glCommand("GLC,wrong"); assert(glActive);
  glCommand("GLC,0000000000000005"); assert(!glActive && Keyboard.down.empty());
  for (String bad : {"GLS,x,100,122", "GLS,0000000000000006,0,122", "GLS,0000000000000006,1001,122",
      "GLS,0000000000000006,100,128,122", "GLS,0000000000000006,100,122,122",
      "GLS,0000000000000006,100,122,218,219", "GLS,0000000000000006,100,0", "GLS,0000000000000006,1x,122"}) {
    count = Keyboard.presses; glCommand(bad); assert(Keyboard.presses == count && !glActive);
  }
  now = ULONG_MAX - 100;
  glCommand("GLS,0000000000000007,200,218");
  now = 99; glTick(); assert(!glActive && glState == "DONE");
  Keyboard.fail = true;
  glCommand("GLS,0000000000000008,100,218"); assert(!glActive && Keyboard.down.empty());
  Keyboard.fail = false;
  glCommand("GLS,0000000000000009,1000,218");
  Serial.add(std::string(200, 'X')); glPoll();
  now += 300; glPoll(); assert(!glActive && Keyboard.down.empty());
  Serial.add("\n"); glPoll(); assert(!glOverflow);
  std::cout << "GL1 actual-header simulation passed: duration, heartbeat, cancel, duplicate, malformed, overflow, rollover, press failure\n";
}
