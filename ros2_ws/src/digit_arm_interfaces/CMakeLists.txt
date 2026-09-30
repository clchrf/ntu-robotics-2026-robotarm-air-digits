cmake_minimum_required(VERSION 3.8)
project(digit_arm_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(action_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/Stroke.msg"
  "msg/Ink.msg"
  "msg/HandState.msg"
  "msg/CameraStatus.msg"
  "msg/DigitGuess.msg"
  "msg/RecognitionResult.msg"
  "msg/ArmStatus.msg"
  "action/RepeatMotion.action"
  DEPENDENCIES builtin_interfaces action_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
