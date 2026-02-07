"""마우스 위치 확인 스크립트 - 3초 후 현재 마우스 좌표 출력"""
import time
import pyautogui

print("3초 후 마우스 위치를 확인합니다...")
print("마우스를 '게임시작' 버튼 위에 올려놓으세요!")
time.sleep(3)

pos = pyautogui.position()
print(f"\n현재 마우스 위치: ({pos.x}, {pos.y})")
print(f"\n로그의 타겟 위치: (1685, 386)")
print(f"차이: ({pos.x - 1685}, {pos.y - 386})")
