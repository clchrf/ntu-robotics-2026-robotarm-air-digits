"""一鍵啟動：鏡頭手勢、數字辨識、動作執行、PyQt6 介面。關閉介面視窗即結束全部節點。

  ros2 launch digit_arm digit_arm.launch.py                     # 模擬（預設）
  ros2 launch digit_arm digit_arm.launch.py mode:=hardware      # 實體（需先在設定檔填好動作）
  ros2 launch digit_arm digit_arm.launch.py camera_backend:=synthetic synthetic_text:=10

launch 參數會寫進設定檔中「各節點自己的區段」後再交給節點，
所以一定優先於 config/digit_arm.yaml 的同名設定。
"""

import os
import tempfile

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, OpaqueFunction, RegisterEventHandler
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# launch 參數 → (節點, 參數名稱, 型別)
OVERRIDES = {
    'mode': [('motion_executor_node', 'mode', str)],
    'port': [('motion_executor_node', 'port', str)],
    'max_count': [('digit_recognizer_node', 'max_count', int), ('motion_executor_node', 'max_count', int)],
    'camera_backend': [('hand_gesture_node', 'camera_backend', str)],
    'camera_index': [('hand_gesture_node', 'camera_index', int)],
    'synthetic_text': [('hand_gesture_node', 'synthetic_text', str)],
}


def _nodes(context):
    with open(LaunchConfiguration('config').perform(context), encoding='utf-8') as fh:
        cfg = yaml.safe_load(fh) or {}
    for arg, targets in OVERRIDES.items():
        value = LaunchConfiguration(arg).perform(context)
        if value == '':
            continue
        for node, name, typ in targets:
            cfg.setdefault(node, {}).setdefault('ros__parameters', {})[name] = typ(value)
    tmp = tempfile.NamedTemporaryFile('w', suffix='_digit_arm.yaml', delete=False, encoding='utf-8')
    yaml.safe_dump(cfg, tmp, allow_unicode=True)
    tmp.close()

    params = [tmp.name]
    ui = Node(package='digit_arm', executable='digit_arm_ui', output='screen',
              condition=IfCondition(LaunchConfiguration('ui')))
    return [
        Node(package='digit_arm', executable='hand_gesture_node', output='screen', parameters=params),
        Node(package='digit_arm', executable='digit_recognizer_node', output='screen', parameters=params),
        Node(package='digit_arm', executable='motion_executor_node', output='screen', parameters=params),
        ui,
        RegisterEventHandler(OnProcessExit(target_action=ui,
                                           on_exit=[EmitEvent(event=Shutdown(reason='介面已關閉'))])),
    ]


def generate_launch_description():
    default_cfg = os.path.join(get_package_share_directory('digit_arm'), 'config', 'digit_arm.yaml')
    return LaunchDescription([
        DeclareLaunchArgument('config', default_value=default_cfg, description='參數檔'),
        DeclareLaunchArgument('mode', default_value='', description='simulation / hardware（空白 = 用設定檔）'),
        DeclareLaunchArgument('port', default_value='', description='win:auto、win:COM5 或 /dev/ttyUSB0'),
        DeclareLaunchArgument('max_count', default_value='', description='最大動作次數（空白 = 用設定檔）'),
        DeclareLaunchArgument('camera_backend', default_value='', description='windows / local / synthetic'),
        DeclareLaunchArgument('camera_index', default_value=''),
        DeclareLaunchArgument('synthetic_text', default_value='', description='合成測試要寫的數字'),
        DeclareLaunchArgument('ui', default_value='true', description='是否開啟介面'),
        OpaqueFunction(function=_nodes),
    ])
