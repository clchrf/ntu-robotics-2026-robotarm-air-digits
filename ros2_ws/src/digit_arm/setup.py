from glob import glob

from setuptools import find_packages, setup

package_name = 'digit_arm'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
        ('share/' + package_name + '/config', glob('config/*.yaml')),
        ('share/' + package_name + '/models', glob('models/*.task')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='digit_arm',
    maintainer_email='clchrf@users.noreply.github.com',
    description='Air-written digit recognition that drives a custom arm (ROS 2 Jazzy).',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'hand_gesture_node = digit_arm.hand_gesture_node:main',
            'digit_recognizer_node = digit_arm.digit_recognizer_node:main',
            'motion_executor_node = digit_arm.motion_executor_node:main',
            'digit_arm_ui = digit_arm.ui.app:main',
        ],
    },
)
