# simulation（模擬，手臂不會移動）或 hardware（實體序列埠）
string mode
bool simulated
bool connected
# 可以接受新的動作目標
bool ready
bool busy
# 位置是否可信；逾時、斷線或控制器重開後為 false，需擺回 HOME 重新連線
bool position_trusted
string port
string motion_description
int32 max_count
string message
