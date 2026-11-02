// GL1 is opt-in. Legacy KQ/KP/KR timing and commands remain unchanged.
// No delay/readStringUntil while a GL1 key is held; millis subtraction wraps safely.
bool glActive = false;
unsigned long glStarted = 0, glHeartbeat = 0;
unsigned int glDuration = 0;
String glToken = "", glRequest = "", glState = "READY", glLine = "";
bool glOverflow = false;

void glFinish(const String &state) {
  Keyboard.releaseAll();
  glActive = false;
  glState = state;
}

void glTick() {
  if (!glActive) return;
  unsigned long now = millis();
  if ((unsigned long)(now - glHeartbeat) >= 300) glFinish("EXPIRED");
  else if ((unsigned long)(now - glStarted) >= glDuration) glFinish("DONE");
}

void glReply() {
  Serial.println("GL1:" + glToken + ":" + glState);
}

bool glNumber(const String &value, int low, int high, int &result) {
  if (value.length() == 0 || value.length() > 4) return false;
  for (unsigned int i = 0; i < value.length(); ++i)
    if (value[i] < '0' || value[i] > '9') return false;
  result = value.toInt();
  return result >= low && result <= high;
}

bool glCommand(const String &cmd) {
  glTick();
  if (cmd == "GL1") { Serial.println("GL1:READY"); return true; }
  if (cmd == "KA" && glActive) glFinish("CANCELLED");
  if (cmd.startsWith("GLP,") || cmd.startsWith("GLC,")) {
    if (cmd.substring(4) != glToken) Serial.println("ERR:GL_TOKEN");
    else {
      if (cmd.startsWith("GLC,") && glActive) glFinish("CANCELLED");
      else if (glActive) glHeartbeat = millis();
      glReply();
    }
    return true;
  }
  if (!cmd.startsWith("GLS,")) {
    if (glActive && cmd != "KA" && cmd != "PING") {
      Serial.println("ERR:GL_BUSY"); return true;
    }
    return false;
  }
  int a = cmd.indexOf(',', 4), b = cmd.indexOf(',', a + 1), c = cmd.indexOf(',', b + 1);
  String token = cmd.substring(4, a);
  int duration = 0, first = 0, second = 0;
  bool valid = a == 20 && b > a && token.length() == 16;
  for (unsigned int i = 0; i < token.length(); ++i)
    if (!((token[i] >= '0' && token[i] <= '9') || (token[i] >= 'a' && token[i] <= 'f'))) valid = false;
  valid = valid && glNumber(cmd.substring(a + 1, b), 50, 1000, duration)
      && glNumber(c < 0 ? cmd.substring(b + 1) : cmd.substring(b + 1, c), 1, 255, first);
  if (c >= 0) valid = valid && glNumber(cmd.substring(c + 1), 1, 255, second) && first != second;
  // Two-key gameplay chords cannot contain a modifier or OS key.
  if (second && ((first >= 128 && first <= 135) || (second >= 128 && second <= 135))) valid = false;
  if (!valid) { Serial.println("ERR:GL_FORMAT"); return true; }
  if (token == glToken) {
    if (cmd != glRequest) Serial.println("ERR:GL_REUSED"); else glReply();
    return true;
  }
  if (glActive) { Serial.println("ERR:GL_BUSY"); return true; }
  glToken = token; glRequest = cmd; glDuration = duration;
  Keyboard.releaseAll();
  if (Keyboard.press((uint8_t)first) != 1 || (second && Keyboard.press((uint8_t)second) != 1)) {
    glFinish("CANCELLED"); glReply(); return true;
  }
  glStarted = glHeartbeat = millis(); glState = "RUNNING"; glActive = true;
  glReply();
  return true;
}

bool glPoll() {
  glTick();
  if (!glActive && glLine.length() == 0 && !glOverflow) return false;
  // The bounded parser cannot starve the timer even under malformed input.
  for (int n = 0; n < 128 && Serial.available(); ++n) {
    char ch = Serial.read();
    if (ch == '\n') {
      if (glOverflow) Serial.println("ERR:GL_LENGTH");
      else {
        glLine.trim();
        if (glLine == "KA") { glFinish("CANCELLED"); Serial.println("OK"); }
        else if (glLine == "PING") Serial.println("PONG");
        else if (!glCommand(glLine)) Serial.println("ERR:GL_BUSY");
      }
      glLine = ""; glOverflow = false;
    } else if (!glOverflow) {
      if (glLine.length() >= 80) { glLine = ""; glOverflow = true; }
      else glLine += ch;
    }
    glTick();
  }
  return true;
}
