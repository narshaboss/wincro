/*
 * WinCro HID Controller for Arduino Leonardo
 *
 * 시리얼로 명령을 받아 마우스/키보드 HID 입력을 생성합니다.
 *
 * 명령 형식:
 * MM,x,y      - 마우스 상대 이동
 * MA,x,y      - 마우스 절대 이동 (여러번 상대이동으로 구현)
 * MC,L/R/M    - 마우스 클릭 (Left/Right/Middle)
 * MP,L/R/M    - 마우스 버튼 누르기
 * MR,L/R/M    - 마우스 버튼 떼기
 * MS,amount   - 마우스 스크롤
 * KP,keycode  - 키 누르기
 * KR,keycode  - 키 떼기
 * KT,text     - 텍스트 타이핑
 * KA          - 모든 키 떼기
 * PING        - 연결 확인 (PONG 응답)
 */

#include <Mouse.h>
#include <Keyboard.h>

// LED 핀 (Leonardo 내장 LED)
const int LED_PIN = 13;

void setup() {
  Serial.begin(115200);
  while (!Serial) {
    ; // 시리얼 연결 대기 (Leonardo 전용)
  }

  Mouse.begin();
  Keyboard.begin();

  pinMode(LED_PIN, OUTPUT);

  // 시작 신호
  blinkLED(3);
  Serial.println("WINCRO_HID_READY");
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() > 0) {
      processCommand(cmd);
      blinkLED(1);  // 명령 수신 시 LED 깜빡
    }
  }
}

void blinkLED(int times) {
  for (int i = 0; i < times; i++) {
    digitalWrite(LED_PIN, HIGH);
    delay(50);
    digitalWrite(LED_PIN, LOW);
    if (i < times - 1) delay(50);
  }
}

void processCommand(String cmd) {
  // PING 명령
  if (cmd == "PING") {
    Serial.println("PONG");
    return;
  }

  // 마우스 상대 이동: MM,x,y
  if (cmd.startsWith("MM,")) {
    int idx1 = 3;
    int idx2 = cmd.indexOf(',', idx1);
    int x = cmd.substring(idx1, idx2).toInt();
    int y = cmd.substring(idx2 + 1).toInt();

    // Mouse.move()는 -128~127 범위만 지원하므로 분할 이동
    while (x != 0 || y != 0) {
      int moveX = constrain(x, -127, 127);
      int moveY = constrain(y, -127, 127);
      Mouse.move(moveX, moveY, 0);
      x -= moveX;
      y -= moveY;
    }
    Serial.println("OK");
    return;
  }

  // 마우스 클릭: MC,L/R/M
  if (cmd.startsWith("MC,")) {
    char btn = cmd.charAt(3);
    int button = MOUSE_LEFT;
    if (btn == 'R') button = MOUSE_RIGHT;
    else if (btn == 'M') button = MOUSE_MIDDLE;
    Mouse.click(button);
    Serial.println("OK");
    return;
  }

  // 마우스 버튼 누르기: MP,L/R/M
  if (cmd.startsWith("MP,")) {
    char btn = cmd.charAt(3);
    int button = MOUSE_LEFT;
    if (btn == 'R') button = MOUSE_RIGHT;
    else if (btn == 'M') button = MOUSE_MIDDLE;
    Mouse.press(button);
    Serial.println("OK");
    return;
  }

  // 마우스 버튼 떼기: MR,L/R/M
  if (cmd.startsWith("MR,")) {
    char btn = cmd.charAt(3);
    int button = MOUSE_LEFT;
    if (btn == 'R') button = MOUSE_RIGHT;
    else if (btn == 'M') button = MOUSE_MIDDLE;
    Mouse.release(button);
    Serial.println("OK");
    return;
  }

  // 마우스 스크롤: MS,amount
  if (cmd.startsWith("MS,")) {
    int amount = cmd.substring(3).toInt();
    Mouse.move(0, 0, amount);
    Serial.println("OK");
    return;
  }

  // 키 누르기: KP,keycode
  if (cmd.startsWith("KP,")) {
    int keycode = cmd.substring(3).toInt();
    Keyboard.press(keycode);
    Serial.println("OK");
    return;
  }

  // 키 떼기: KR,keycode
  if (cmd.startsWith("KR,")) {
    int keycode = cmd.substring(3).toInt();
    Keyboard.release(keycode);
    Serial.println("OK");
    return;
  }

  // 텍스트 타이핑: KT,text
  if (cmd.startsWith("KT,")) {
    String text = cmd.substring(3);
    Keyboard.print(text);
    Serial.println("OK");
    return;
  }

  // 모든 키 떼기: KA
  if (cmd == "KA") {
    Keyboard.releaseAll();
    Mouse.release(MOUSE_LEFT);
    Mouse.release(MOUSE_RIGHT);
    Mouse.release(MOUSE_MIDDLE);
    Serial.println("OK");
    return;
  }

  // 알 수 없는 명령
  Serial.println("ERR:UNKNOWN_CMD");
}
