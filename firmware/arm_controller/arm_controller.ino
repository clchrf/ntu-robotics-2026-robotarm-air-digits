#include <Servo.h>

/*
  arm_controller（加減速版，2026-09-29）

  通訊協定與原版完全相同（movearm.py、robot_draw 工具、空中寫數字都不用改）：
    - 115200 baud，開機印出 "System Ready"
    - 每行 "BASE,ARM,FOREARM,WRIST\n"（角度；前三軸為步進，第四軸為 pin 9 伺服）
    - 步進馬達由「移動中」變成「停止」時印一次 "OK"
  差別只在「怎麼走到目標」：
    原版：收到目標就以固定約 94°/s 從靜止直接起跑、到點急停 → 有負載的軸會失步、抖動。
    本版：每一軸各自做梯形速度曲線（等加速 → 等速 → 等減速），起步從 0 慢慢加速；
          移動中收到新目標會平順地改變方向或減速，不會瞬間反轉。
  原版備份：arm_controller.ino.original-20260929
*/

const int stepPins[] = {2, 3, 4}; // Base, Arm, Forearm
const int dirPins[] = {5, 6, 7};
const int enablePin = 8;
const float MICROSTEPS = 16.0;
const float STEPS_PER_REV = 200.0 * MICROSTEPS;
const float STEPS_PER_DEGREE = STEPS_PER_REV / 360.0;

// ── 可調參數（越小越慢越穩；失步時先調低 MAX_SPEED 或 ACCEL）──
const float MAX_SPEED_DEG_S = 60.0;    // 最高速度（原版固定約 94°/s）；上限約 110（每 1 ms 最多走 1 步）
const float ACCEL_DEG_S2    = 120.0;   // 加速度：約 0.5 秒加速到最高速
const float MIN_SPEED_DEG_S = 3.0;     // 快到目標時的最低速度，確保最後幾步一定走完
const unsigned long CONTROL_PERIOD_US = 1000;   // 速度更新週期 1 ms
const unsigned int STEP_PULSE_US = 5;           // 步進脈衝寬度（A4988/DRV8825 需 >= 2 µs）

const float MAX_SPEED = MAX_SPEED_DEG_S * STEPS_PER_DEGREE;   // steps/s
const float ACCEL     = ACCEL_DEG_S2 * STEPS_PER_DEGREE;      // steps/s^2
const float MIN_SPEED = MIN_SPEED_DEG_S * STEPS_PER_DEGREE;   // steps/s

long currentSteps[] = {0, 0, 0};
long targetSteps[] = {0, 0, 0};
float velocity[] = {0, 0, 0};    // steps/s，有正負號
float stepAcc[] = {0, 0, 0};     // 累積的小數步
int lastDir[] = {0, 0, 0};
float targetGripper = 90.0;
Servo gripper;
String inputString = "";
bool isMoving = false;
unsigned long lastControlUs = 0;

void parseData(String data);

void setup() {
  Serial.begin(115200);
  pinMode(enablePin, OUTPUT);
  digitalWrite(enablePin, LOW);
  for (int i = 0; i < 3; i++) {
    pinMode(stepPins[i], OUTPUT);
    pinMode(dirPins[i], OUTPUT);
    digitalWrite(stepPins[i], LOW);
  }
  gripper.attach(9);
  gripper.write(90);
  lastControlUs = micros();
  Serial.println("System Ready");
}

// 走一步（dir = +1 / -1）
void stepAxis(int i, int dir) {
  if (dir != lastDir[i]) {
    digitalWrite(dirPins[i], dir > 0 ? HIGH : LOW);
    lastDir[i] = dir;
    delayMicroseconds(2);   // 方向腳位建立時間
  }
  digitalWrite(stepPins[i], HIGH);
  delayMicroseconds(STEP_PULSE_US);
  digitalWrite(stepPins[i], LOW);
  currentSteps[i] += dir;
}

// 更新一軸的速度（梯形曲線），並在需要時走一步。回傳這軸是否仍在移動。
bool updateAxis(int i, float dt) {
  long togo = targetSteps[i] - currentSteps[i];
  float v = velocity[i];

  if (togo == 0 && fabs(v) <= ACCEL * dt * 2.0) {
    velocity[i] = 0;
    stepAcc[i] = 0;
    return false;
  }

  int want = (togo > 0) ? 1 : (togo < 0 ? -1 : 0);   // 目標方向
  float speed = fabs(v);
  int moving = (v > 0) ? 1 : (v < 0 ? -1 : 0);
  float stopDist = speed * speed / (2.0 * ACCEL);    // 以目前速度煞停需要的步數

  if (moving != 0 && (want != moving || fabs((float)togo) <= stopDist)) {
    // 方向不對（目標改到後面）或快到了 → 減速
    speed -= ACCEL * dt;
    if (want == moving && speed < MIN_SPEED) speed = MIN_SPEED;  // 同方向快到時保持最低速走完
    if (speed <= 0) { speed = 0; moving = 0; }
  } else if (want != 0) {
    // 還遠：加速到最高速度
    moving = want;
    speed += ACCEL * dt;
    if (speed < MIN_SPEED) speed = MIN_SPEED;
    if (speed > MAX_SPEED) speed = MAX_SPEED;
  }
  v = speed * moving;
  velocity[i] = v;

  // 依速度累積步數
  stepAcc[i] += v * dt;
  if (stepAcc[i] >= 1.0) {
    stepAcc[i] -= 1.0;
    if (currentSteps[i] < targetSteps[i]) stepAxis(i, +1);
    else { velocity[i] = 0; stepAcc[i] = 0; }            // 已到目標，不越過
  } else if (stepAcc[i] <= -1.0) {
    stepAcc[i] += 1.0;
    if (currentSteps[i] > targetSteps[i]) stepAxis(i, -1);
    else { velocity[i] = 0; stepAcc[i] = 0; }
  }
  return true;
}

void loop() {
  while (Serial.available() > 0) {
    char inChar = (char)Serial.read();
    if (inChar == '\n') {
      parseData(inputString);
      inputString = "";
    } else {
      inputString += inChar;
    }
  }

  unsigned long now = micros();
  unsigned long elapsed = now - lastControlUs;
  if (elapsed < CONTROL_PERIOD_US) return;
  lastControlUs = now;
  float dt = elapsed * 1e-6;
  if (dt > 0.01) dt = 0.01;   // 解析序列資料較久時避免速度一次跳太多

  bool stillMoving = false;
  for (int i = 0; i < 3; i++) {
    if (updateAxis(i, dt)) stillMoving = true;
  }

  if (stillMoving) {
    isMoving = true;
  } else if (isMoving) {
    // 與原版相同：由「移動中」變成「停止」時回傳 OK
    Serial.println("OK");
    isMoving = false;
  }
}

// 解析 "BASE,ARM,FOREARM,WRIST" 封包（必須要有 4 個數值）——與原版相同
void parseData(String data) {
  int first = data.indexOf(',');
  int second = data.indexOf(',', first + 1);
  int third = data.indexOf(',', second + 1);

  if (first > 0 && second > 0 && third > 0) {
    float angles[4];
    angles[0] = data.substring(0, first).toFloat();
    angles[1] = data.substring(first + 1, second).toFloat();
    angles[2] = data.substring(second + 1, third).toFloat();
    angles[3] = data.substring(third + 1).toFloat();

    // 防呆機制
    if (isnan(angles[0]) || isnan(angles[1]) || isnan(angles[2])) return;

    targetSteps[0] = round(angles[0] * STEPS_PER_DEGREE);
    targetSteps[1] = round(angles[1] * STEPS_PER_DEGREE);
    targetSteps[2] = round(angles[2] * STEPS_PER_DEGREE);

    if (abs(angles[3] - targetGripper) > 1.0) {
       targetGripper = angles[3];
       gripper.write(targetGripper);
    }
  }
}
