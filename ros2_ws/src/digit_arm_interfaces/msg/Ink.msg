# 目前的整份筆跡。每次清除後 ink_id 會加一。
builtin_interfaces/Time stamp
uint32 ink_id
# true = 已握拳送出，筆跡凍結，等待辨識／清除
bool locked
# true = 最後一筆仍在書寫中
bool pen_down
# true = 有筆畫因為手離開畫面（追蹤中斷）而被迫結束，筆跡可能不完整，不可據此動作
bool interrupted
Stroke[] strokes
