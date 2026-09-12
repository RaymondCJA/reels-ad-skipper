#include <Arduino.h>
#include <HijelHID_BLEMouse.h>

// BLE mouse controlled by newline-delimited commands over the ESP32 USB serial
// connection. The Mac sends SWIPE only after its detector confirms an ad.
HijelBLEMouse mouse("Reels Skipper", "DIY", 100);

static const uint32_t SERIAL_BAUD = 115200;
static const int SETTLE_DELAY_MS = 50;

String inputLine;

void swipeUp() {
  if (!mouse.isPaired()) {
    Serial.println("ERR BLE_NOT_CONNECTED");
    return;
  }

  // Normalize against the top-left edges, then move to the middle of the
  // video. Never use the bottom edge: if iOS drops a positioning report, a
  // pressed drag near the Home indicator could leave Instagram.
  mouse.moveTo(-800, 0, 80);
  mouse.moveTo(0, -1200, 120);
  mouse.moveTo(180, 0, 60);
  mouse.moveTo(0, 440, 110);
  delay(SETTLE_DELAY_MS);

  // moveTo buffers every delta. Starting it immediately after button-down
  // prevents the gesture becoming a stationary tap on an advertiser link.
  mouse.press(MouseButton::Left);
  mouse.moveTo(0, -350, 180);
  mouse.release(MouseButton::Left);
  delay(20);
  Serial.println("OK SWIPE");
}

void wheelDown() {
  if (!mouse.isPaired()) {
    Serial.println("ERR BLE_NOT_CONNECTED");
    return;
  }
  mouse.scroll(-6);
  Serial.println("OK WHEEL");
}

void handleCommand(String command) {
  command.trim();
  command.toUpperCase();

  if (command == "PING") {
    Serial.println("PONG");
  } else if (command == "STATUS") {
    Serial.print("STATUS BLE=");
    Serial.println(mouse.isPaired() ? "CONNECTED" : "WAITING");
  } else if (command == "SWIPE") {
    swipeUp();
  } else if (command == "WHEEL") {
    wheelDown();
  } else if (command.length() > 0) {
    Serial.print("ERR UNKNOWN_COMMAND ");
    Serial.println(command);
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  inputLine.reserve(64);
  delay(400);
  Serial.println("BOOT Reels Skipper");
  mouse.setUpdateRate(HIDRate::Hz100);
  mouse.begin();
  Serial.println("READY Pair 'Reels Skipper' in iPhone Bluetooth settings");
}

void loop() {
  while (Serial.available() > 0) {
    const char incoming = static_cast<char>(Serial.read());
    if (incoming == '\n' || incoming == '\r') {
      if (inputLine.length() > 0) {
        handleCommand(inputLine);
        inputLine = "";
      }
    } else if (inputLine.length() < 63) {
      inputLine += incoming;
    }
  }
  delay(2);
}
