# 手部追蹤與手勢判斷結果（每個影格一則）
builtin_interfaces/Time stamp
bool hand_visible
# none / point / open / fist / other
string gesture
# down（下筆）/ up（抬筆）/ locked（已送出，等待清除）
string pen
# 握拳送出的累積進度 0..1
float32 fist_progress
# 食指指尖（鏡像後正規化座標），手不在畫面時為 -1
float32 tip_x
float32 tip_y
# 21 個 MediaPipe 手部關鍵點（鏡像後正規化座標）
float32[] landmarks_x
float32[] landmarks_y
# 給使用者看的簡短提示（繁體中文）
string message
