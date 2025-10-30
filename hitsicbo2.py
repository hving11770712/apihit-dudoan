import os
import json
import time
import logging
import threading
import requests
from collections import defaultdict, deque, Counter
from flask import Flask, jsonify
from flask_cors import CORS
import math


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

API_URL = "https://apihitsicbo.onrender.com/api/sicbo789"

POLL_INTERVAL = 5
MAX_HISTORY_LEN = 500

app = Flask(__name__)
CORS(app)

# ✅ Khởi tạo các thuộc tính tuỳ chỉnh cho app Flask
app.history = []
app.session_ids = []
app.lock = threading.Lock()
app.bot_state = {
    "mode": "random",
    "random_count": 0,
    "real_history": []
}
app.datvi_map = {}  # ✅ <== KHÔNG ĐƯỢC QUÊN DÒNG NÀY


def opposite(val):
    return "Xỉu" if val == "Tài" else "Tài"

def entropy(seq):
    if not seq: return 0
    c = Counter(seq)
    total = len(seq)
    return -sum((c[x]/total) * math.log2(c[x]/total) for x in c)

def detect_streak_and_break(history):
    if not history:
        return 0, None, 0.0
    streak = 1
    current_result = history[-1]
    for prev in reversed(history[:-1]):
        if prev == current_result:
            streak += 1
        else:
            break
    last_20 = history[-20:] if len(history) >= 20 else history
    switches = sum(a != b for a, b in zip(last_20, last_20[1:]))
    tai_count = last_20.count("Tài")
    xiu_count = last_20.count("Xỉu")
    imbalance = abs(tai_count - xiu_count) / len(last_20)
    if streak >= 8:
        break_prob = min(0.7 + (switches / 15) + imbalance * 0.2, 0.95)
    elif streak >= 5:
        break_prob = min(0.4 + (switches / 10) + imbalance * 0.3, 1.0)
    elif streak >= 3 and switches >= 6:
        break_prob = 0.35
    else:
        break_prob = 0
    return streak, current_result, break_prob

def call_trend_and_prob(history):
    try:
        streak_len, streak_result, break_prob = detect_streak_and_break(history)
        if streak_len >= 5:
            return opposite(streak_result) if break_prob > 0.7 else streak_result
        last_15 = history[-15:]
        weights = [1.3 ** i for i in range(len(last_15))]
        tai_weighted = sum(w for w, r in zip(weights, last_15) if r == "Tài")
        xiu_weighted = sum(w for w, r in zip(weights, last_15) if r == "Xỉu")
        total_weight = tai_weighted + xiu_weighted
        if total_weight == 0:
            return None
        if abs(tai_weighted - xiu_weighted) / total_weight >= 0.2:
            return "Tài" if tai_weighted > xiu_weighted else "Xỉu"
        return opposite(last_15[-1])
    except:
        return None

def call_short_pattern(history):
    try:
        last_8 = history[-8:]
        patterns = [tuple(last_8[i:i+3]) for i in range(len(last_8)-2)]
        counts = Counter(patterns)
        most_common = counts.most_common(1)
        if most_common and most_common[0][1] >= 2:
            pattern = most_common[0][0]
            return opposite(pattern[-1])
        return opposite(last_8[-1])
    except:
        return None

def call_mean_deviation(history):
    try:
        last_12 = history[-12:]
        tai = last_12.count("Tài")
        xiu = len(last_12) - tai
        deviation = abs(tai - xiu) / len(last_12)
        if deviation < 0.3:
            return opposite(last_12[-1])
        return "Tài" if tai > xiu else "Xỉu"
    except:
        return None

def call_recent_switch(history):
    try:
        last_10 = history[-10:]
        switches = sum(a != b for a, b in zip(last_10, last_10[1:]))
        return opposite(last_10[-1]) if switches >= 5 else opposite(last_10[-1])
    except:
        return None

def is_bad_pattern(history):
    if len(history) < 15:
        return False
    switches = sum(a != b for a, b in zip(history[-15:], history[-14:]))
    streak, _, _ = detect_streak_and_break(history)
    return switches >= 8 or streak >= 10

def Scan(history):
    if not history or len(history) < 6:
        return "Tài", "[AI] Lịch sử quá ngắn"
    if is_bad_pattern(history):
        return None, "[AI] Cầu xấu, bỏ qua"
    votes = Counter()
    for func in [call_trend_and_prob, call_short_pattern, call_mean_deviation, call_recent_switch]:
        pred = func(history)
        if pred:
            votes[pred] += 1
    if not votes:
        return opposite(history[-1]), "[AI] Không có mô hình rõ ràng"
    prediction = votes.most_common(1)[0][0]
    return prediction, f"[AI] Tổng hợp theo {votes}"

def predict_anhbao(history, scores):
    recent_history = history[-5:]
    recent_scores = scores[-5:] if scores else []

    tai_count = recent_history.count("Tài")
    xiu_count = recent_history.count("Xỉu")

    if len(history) >= 3:
        last_3 = history[-3:]
        if last_3 == ["Tài", "Xỉu", "Tài"]:
            return "Xỉu", "[AI] Phát hiện mẫu 1T1X → tiếp theo nên đánh Xỉu", "anhbao"
        elif last_3 == ["Xỉu", "Tài", "Xỉu"]:
            return "Tài", "[AI] Phát hiện mẫu 1X1T → tiếp theo nên đánh Tài", "anhbao"

    if len(history) >= 4:
        last_4 = history[-4:]
        if last_4 == ["Tài", "Tài", "Xỉu", "Xỉu"]:
            return "Tài", "[AI] Phát hiện mẫu 2T2X → tiếp theo nên đánh Tài", "anhbao"
        elif last_4 == ["Xỉu", "Xỉu", "Tài", "Tài"]:
            return "Xỉu", "[AI] Phát hiện mẫu 2X2T → tiếp theo nên đánh Xỉu", "anhbao"

    if len(history) >= 9 and all(r == "Xỉu" for r in history[-9:]):
        return "Tài", "[AI] Chuỗi Xỉu quá dài (9 lần) → dự đoán Tài", "anhbao"

    if recent_scores:
        avg_score = sum(recent_scores) / len(recent_scores)
        if avg_score > 10:
            return "Tài", f"[AI] Điểm trung bình cao ({avg_score:.1f}) → dự đoán Tài", "anhbao"
        elif avg_score < 8:
            return "Xỉu", f"[AI] Điểm trung bình thấp ({avg_score:.1f}) → dự đoán Xỉu", "anhbao"

    if tai_count > xiu_count + 1:
        return "Tài", f"[AI] Tài chiếm đa số ({tai_count}/5) → dự đoán Tài", "anhbao"
    elif xiu_count > tai_count + 1:
        return "Xỉu", f"[AI] Xỉu chiếm đa số ({xiu_count}/5) → dự đoán Xỉu", "anhbao"

    overall_tai = history.count("Tài")
    overall_xiu = history.count("Xỉu")
    if overall_tai > overall_xiu:
        return "Xỉu", "[AI] Tổng thể Tài nhiều hơn → dự đoán Xỉu", "anhbao"
    else:
        return "Tài", "[AI] Tổng thể Xỉu nhiều hơn hoặc bằng → dự đoán Tài", "anhbao"

def predict_next(history):
    if len(history) < 6:
        return "Tài", "[AI] Dữ liệu quá ngắn"

    recent5 = history[-5:]
    recent10 = history[-10:] if len(history) >= 10 else history
    tai_5 = recent5.count("Tài")
    xiu_5 = recent5.count("Xỉu")
    tai_10 = recent10.count("Tài")
    xiu_10 = recent10.count("Xỉu")

    # 1. Phát hiện các mẫu đảo xen kẽ phổ biến
    if len(history) >= 3:
        last_3 = history[-3:]
        if last_3 == ["Tài", "Xỉu", "Tài"]:
            return "Xỉu", "[AI] Mẫu 1T1X → tiếp theo nên đánh Xỉu"
        elif last_3 == ["Xỉu", "Tài", "Xỉu"]:
            return "Tài", "[AI] Mẫu 1X1T → tiếp theo nên đánh Tài"

    # 2. Phát hiện mẫu 2T2X và 2X2T
    if len(history) >= 4:
        last_4 = history[-4:]
        if last_4 == ["Tài", "Tài", "Xỉu", "Xỉu"]:
            return "Tài", "[AI] Mẫu 2T2X → tiếp theo nên đánh Tài"
        elif last_4 == ["Xỉu", "Xỉu", "Tài", "Tài"]:
            return "Xỉu", "[AI] Mẫu 2X2T → tiếp theo nên đánh Xỉu"

    # 3. Đảo nếu phát hiện chuỗi quá dài 1 bên
    if len(history) >= 7:
        if all(r == "Tài" for r in history[-7:]):
            return "Xỉu", "[AI] Chuỗi Tài quá dài (7 lần) → dự đoán Xỉu"
        if all(r == "Xỉu" for r in history[-7:]):
            return "Tài", "[AI] Chuỗi Xỉu quá dài (7 lần) → dự đoán Tài"

    # 4. Dựa vào xu hướng gần nhất
    if abs(tai_5 - xiu_5) >= 3:
        pred = "Tài" if tai_5 > xiu_5 else "Xỉu"
        return pred, f"[AI] 5 phiên gần nhất nghiêng rõ: {tai_5}T/{xiu_5}X → dự đoán {pred}"

    # 5. Trung hòa bằng thống kê 10 phiên
    if abs(tai_10 - xiu_10) >= 4:
        pred = "Tài" if tai_10 > xiu_10 else "Xỉu"
        return pred, f"[AI] 10 phiên gần nhất lệch rõ: {tai_10}T/{xiu_10}X → dự đoán {pred}"

    # 6. Nếu không có mẫu rõ ràng, đánh theo xu hướng đảo
    return opposite(history[-1]), "[AI] Không có mẫu mạnh → chọn ngược phiên trước"
# ====== PATTERN BASED MODEL DỮA TRÊN THỐNG KÊ CẦU ======
PATTERN_DATA = {
    # Các pattern cơ bản
    "tttt": {"tai": 73, "xiu": 27}, "xxxx": {"tai": 27, "xiu": 73},
    "tttttt": {"tai": 83, "xiu": 17}, "xxxxxx": {"tai": 17, "xiu": 83},
    "ttttx": {"tai": 40, "xiu": 60}, "xxxxt": {"tai": 60, "xiu": 40},
    "ttttttx": {"tai": 30, "xiu": 70}, "xxxxxxt": {"tai": 70, "xiu": 30},
    "ttxx": {"tai": 62, "xiu": 38}, "xxtt": {"tai": 38, "xiu": 62},
    "ttxxtt": {"tai": 32, "xiu": 68}, "xxttxx": {"tai": 68, "xiu": 32},
    "txx": {"tai": 60, "xiu": 40}, "xtt": {"tai": 40, "xiu": 60},
    "txxtx": {"tai": 63, "xiu": 37}, "xttxt": {"tai": 37, "xiu": 63},
    "tttxt": {"tai": 60, "xiu": 40}, "xxxtx": {"tai": 40, "xiu": 60},
    "tttxx": {"tai": 60, "xiu": 40}, "xxxtt": {"tai": 40, "xiu": 60},
    "txxt": {"tai": 60, "xiu": 40}, "xttx": {"tai": 40, "xiu": 60},
    "ttxxttx": {"tai": 30, "xiu": 70}, "xxttxxt": {"tai": 70, "xiu": 30},
    
    # Bổ sung pattern cầu lớn (chuỗi dài)
    "tttttttt": {"tai": 88, "xiu": 12}, "xxxxxxxx": {"tai": 12, "xiu": 88},
    "tttttttx": {"tai": 25, "xiu": 75}, "xxxxxxxxt": {"tai": 75, "xiu": 25},
    "tttttxxx": {"tai": 35, "xiu": 65}, "xxxxtttt": {"tai": 65, "xiu": 35},
    "ttttxxxx": {"tai": 30, "xiu": 70}, "xxxxtttx": {"tai": 70, "xiu": 30},
    
    # Pattern đặc biệt cho Sunwin
    "txtxtx": {"tai": 68, "xiu": 32}, "xtxtxt": {"tai": 32, "xiu": 68},
    "ttxtxt": {"tai": 55, "xiu": 45}, "xxtxtx": {"tai": 45, "xiu": 55},
    "txtxxt": {"tai": 60, "xiu": 40}, "xtxttx": {"tai": 40, "xiu": 60},
    
    # Thêm các pattern mới nâng cao
    "ttx": {"tai": 65, "xiu": 35}, "xxt": {"tai": 35, "xiu": 65},
    "txt": {"tai": 58, "xiu": 42}, "xtx": {"tai": 42, "xiu": 58},
    "tttx": {"tai": 70, "xiu": 30}, "xxxt": {"tai": 30, "xiu": 70},
    "ttxt": {"tai": 63, "xiu": 37}, "xxtx": {"tai": 37, "xiu": 63},
    "txxx": {"tai": 25, "xiu": 75}, "xttt": {"tai": 75, "xiu": 25},
    "tttxx": {"tai": 60, "xiu": 40}, "xxxtt": {"tai": 40, "xiu": 60},
    "ttxtx": {"tai": 62, "xiu": 38}, "xxtxt": {"tai": 38, "xiu": 62},
    "ttxxt": {"tai": 55, "xiu": 45}, "xxttx": {"tai": 45, "xiu": 55},
    "ttttx": {"tai": 40, "xiu": 60}, "xxxxt": {"tai": 60, "xiu": 40},
    "tttttx": {"tai": 30, "xiu": 70}, "xxxxxt": {"tai": 70, "xiu": 30},
    "ttttttx": {"tai": 25, "xiu": 75}, "xxxxxxt": {"tai": 75, "xiu": 25},
    "tttttttx": {"tai": 20, "xiu": 80}, "xxxxxxxt": {"tai": 80, "xiu": 20},
    "ttttttttx": {"tai": 15, "xiu": 85}, "xxxxxxxxt": {"tai": 85, "xiu": 15},
    
    # Pattern đặc biệt zigzag
    "txtx": {"tai": 52, "xiu": 48}, "xtxt": {"tai": 48, "xiu": 52},
    "txtxt": {"tai": 53, "xiu": 47}, "xtxtx": {"tai": 47, "xiu": 53},
    "txtxtx": {"tai": 55, "xiu": 45}, "xtxtxt": {"tai": 45, "xiu": 55},
    "txtxtxt": {"tai": 57, "xiu": 43}, "xtxtxtx": {"tai": 43, "xiu": 57},
    
    # Pattern đặc biệt kết hợp
    "ttxxttxx": {"tai": 38, "xiu": 62}, "xxttxxtt": {"tai": 62, "xiu": 38},
    "ttxxxttx": {"tai": 45, "xiu": 55}, "xxttxxxt": {"tai": 55, "xiu": 45},
    "ttxtxttx": {"tai": 50, "xiu": 50}, "xxtxtxxt": {"tai": 50, "xiu": 50},
    
    # Thêm các pattern mới cực ngon
    "ttxttx": {"tai": 60, "xiu": 40}, "xxtxxt": {"tai": 40, "xiu": 60},
    "ttxxtx": {"tai": 58, "xiu": 42}, "xxtxxt": {"tai": 42, "xiu": 58},
    "ttxtxtx": {"tai": 62, "xiu": 38}, "xxtxtxt": {"tai": 38, "xiu": 62},
    "ttxxtxt": {"tai": 55, "xiu": 45}, "xxtxttx": {"tai": 45, "xiu": 55},
    "ttxtxxt": {"tai": 65, "xiu": 35}, "xxtxttx": {"tai": 35, "xiu": 65},
    "ttxtxttx": {"tai": 70, "xiu": 30}, "xxtxtxxt": {"tai": 30, "xiu": 70},
    "ttxxtxtx": {"tai": 68, "xiu": 32}, "xxtxtxtx": {"tai": 32, "xiu": 68},
    "ttxtxxtx": {"tai": 72, "xiu": 28}, "xxtxtxxt": {"tai": 28, "xiu": 72},
    "ttxxtxxt": {"tai": 75, "xiu": 25}, "xxtxtxxt": {"tai": 25, "xiu": 75},
}
BIG_STREAK_DATA = {
    "tai": {
        "3": {"next_tai": 65, "next_xiu": 35},
        "4": {"next_tai": 70, "next_xiu": 30},
        "5": {"next_tai": 75, "next_xiu": 25},
        "6": {"next_tai": 80, "next_xiu": 20},
        "7": {"next_tai": 85, "next_xiu": 15},
        "8": {"next_tai": 88, "next_xiu": 12},
        "9": {"next_tai": 90, "next_xiu": 10},
        "10+": {"next_tai": 92, "next_xiu": 8}
    },
    "xiu": {
        "3": {"next_tai": 35, "next_xiu": 65},
        "4": {"next_tai": 30, "next_xiu": 70},
        "5": {"next_tai": 25, "next_xiu": 75},
        "6": {"next_tai": 20, "next_xiu": 80},
        "7": {"next_tai": 15, "next_xiu": 85},
        "8": {"next_tai": 12, "next_xiu": 88},
        "9": {"next_tai": 10, "next_xiu": 90},
        "10+": {"next_tai": 8, "next_xiu": 92}
    }
}
SUM_STATS = {
    "3-10": {"tai": 0, "xiu": 100},  # Xỉu 100%
    "11": {"tai": 15, "xiu": 85},
    "12": {"tai": 25, "xiu": 75},
    "13": {"tai": 40, "xiu": 60},
    "14": {"tai": 50, "xiu": 50},
    "15": {"tai": 60, "xiu": 40},
    "16": {"tai": 75, "xiu": 25},
    "17": {"tai": 85, "xiu": 15},
    "18": {"tai": 100, "xiu": 0}     # Tài 100%
}
import random

def suggest_vitri_by_prediction(prediction, session_id, top_n=3):
    """
    Trả về 3 vị trí ngẫu nhiên từ 4 đến 17 theo dự đoán tài/xỉu, cố định cho mỗi session_id
    """
    if session_id in app.datvi_map:
        return app.datvi_map[session_id]  # dùng lại nếu đã có

    if prediction == "Tài":
        pool = list(range(11, 18))  # 11 → 17
    elif prediction == "Xỉu":
        pool = list(range(4, 11))   # 4 → 10
    else:
        pool = list(range(4, 18))   # 4 → 17

    selected = random.sample(pool, min(top_n, len(pool)))
    result = ", ".join(map(str, sorted(selected)))
    app.datvi_map[session_id] = result
    return result


def find_closest_pattern(input_pattern_oldest_first):
    if not input_pattern_oldest_first:
        return None
    for key in sorted(PATTERN_DATA.keys(), key=len, reverse=True):
        if input_pattern_oldest_first.endswith(key):
            return key
    return None

def analyze_big_streak(history):
    if len(history) < 10:
        return None, 0
    current_streak = 1
    current_result = history[0]["result"]
    for i in range(1, len(history)):
        if history[i]["result"] == current_result:
            current_streak += 1
        else:
            break
    if current_streak >= 10:
        streak_key = str(current_streak) if current_streak <= 9 else "10+"
        stats = BIG_STREAK_DATA[current_result.lower()].get(streak_key, None)
        if stats:
            return ("Tài", stats["next_tai"]) if stats["next_tai"] > stats["next_xiu"] else ("Xỉu", stats["next_xiu"])
    return None, 0

def analyze_sum_trend(history):
    if not history:
        return None, 0
    last_sum = history[0]["total"]
    sum_stats = SUM_STATS.get(str(last_sum), None)
    if sum_stats:
        if sum_stats["tai"] == 100:
            return "Tài", 95
        elif sum_stats["xiu"] == 100:
            return "Xỉu", 95
        return ("Tài", sum_stats["tai"]) if sum_stats["tai"] > sum_stats["xiu"] else ("Xỉu", sum_stats["xiu"])
    return None, 0

def pattern_predict(history_sessions):
    if not history_sessions:
        return "Tài", 50

    # 1. Phân tích cầu lớn
    streak_prediction, streak_confidence = analyze_big_streak(history_sessions)
    if streak_prediction and streak_confidence > 75:
        return streak_prediction, streak_confidence

    # 2. Phân tích tổng điểm
    sum_prediction, sum_confidence = analyze_sum_trend(history_sessions)
    if sum_prediction and sum_confidence > 80:
        return sum_prediction, sum_confidence

    # 3. Pattern theo chuỗi gần đây
    elements = [("t" if s["result"] == "Tài" else "x") for s in history_sessions[:15]]
    current_pattern_str = "".join(reversed(elements))
    closest_pattern_key = find_closest_pattern(current_pattern_str)

    if closest_pattern_key:
        data = PATTERN_DATA[closest_pattern_key]
        if data["tai"] == data["xiu"]:
            last_total = history_sessions[0]["total"]
            return ("Tài", 55) if last_total >= 11 else ("Xỉu", 55)
        return ("Tài", data["tai"]) if data["tai"] > data["xiu"] else ("Xỉu", data["xiu"])

    # fallback
    last_total = history_sessions[0]["total"]
    return ("Tài", 55) if last_total >= 11 else ("Xỉu", 55)

def poll_api():
    while True:
        try:
            res = requests.get(API_URL, timeout=10)
            if res.status_code != 200:
                logging.warning(f"⚠️ API trả về mã {res.status_code}")
                time.sleep(POLL_INTERVAL)
                continue

            data = res.json()

            # ✅ Đọc các trường đúng định dạng mới
            phien_raw = data.get("Phien")  # dạng "#2285143"
            sid = int(str(phien_raw).lstrip("#")) if phien_raw else None
            d1 = data.get("xuc_xac_1")
            d2 = data.get("xuc_xac_2")
            d3 = data.get("xuc_xac_3")
            total = data.get("tong")
            result = data.get("ket_qua")  # "Tài" hoặc "Xỉu"

            if not all([sid, d1, d2, d3, total, result]):
                logging.warning("⚠️ Thiếu dữ liệu từ API")
                time.sleep(POLL_INTERVAL)
                continue

            with app.lock:
                if not app.session_ids or sid > app.session_ids[-1]:
                    app.session_ids.append(sid)
                    app.history.append({
                        "result": result.strip().capitalize(),  # "tài" -> "Tài"
                        "total": total,
                        "dice": [d1, d2, d3]
                    })

                    if len(app.history) > MAX_HISTORY_LEN:
                        app.history.pop(0)
                        app.session_ids.pop(0)
                        app.lock = threading.Lock()
                        app.datvi_map = {}

                    logging.info(f"✅ Phiên mới #{sid}: {result.upper()} (Xúc xắc: {d1},{d2},{d3} | Tổng: {total})")

        except Exception as e:
            logging.error(f"❌ Lỗi khi gọi API: {e}")
        time.sleep(POLL_INTERVAL)



def smart_vote_prediction(history, scores=None, session_data=None):
    votes = Counter()
    reasons = []

    # Cầu thống kê đơn giản
    pred1, reason1 = Scan(history)
    if pred1:
        votes[pred1] += 1
        reasons.append(f"[Scan] {reason1}")

    # Cầu Anh Bảo
    if scores is not None:
        pred2, reason2, _ = predict_anhbao(history, scores)
        if pred2:
            votes[pred2] += 1
            reasons.append(f"[AnhBao] {reason2}")

    # Cầu pattern thống kê
    if session_data is not None:
        pred3, conf3 = pattern_predict(session_data)
        if pred3:
            weight = 2 if conf3 >= 70 else 1
            votes[pred3] += weight
            reasons.append(f"[Pattern] {pred3} ({conf3}%)")

    # Ưu tiên theo số phiếu
    if not votes:
        return opposite(history[-1]), "[Fallback] Không có phiếu, đánh ngược phiên cuối"
    best = votes.most_common(1)[0][0]
    detail = " | ".join(reasons)
    return best, f"[SmartVote] Chốt theo phiếu: {votes} → {best}. {detail}"

@app.route("/api/hitclub", methods=["GET"])
def get_prediction():
    try:
        with app.lock:
            if not app.history or not app.session_ids:
                return jsonify({"error": "Chưa có dữ liệu"}), 500

            current_result = app.history[-1]["result"]
            current_sid = app.session_ids[-1]

            history_simple = [h["result"] for h in app.history]
            scores = [h["total"] for h in app.history]
            session_data = list(reversed(app.history))  
            prediction, reason = smart_vote_prediction(history_simple, scores, session_data)

            # ✅ Random datvi (vị trí)
            datvi_text = suggest_vitri_by_prediction(prediction, current_sid + 1)

            # ✅ Lấy 8 phiên gần nhất
            last_8_history = history_simple[-8:]

            return jsonify({
                "next_session": current_sid + 1,
                "prediction": prediction,
                "datvi": datvi_text,
                "history": last_8_history
            })
    except Exception as e:
        logging.error(f"❌ Lỗi trong get_prediction: {e}")
        return jsonify({"error": f"Lỗi máy chủ nội bộ: {str(e)}"}), 500



@app.route("/api/history", methods=["GET"])
def get_history():
    with app.lock:
        return jsonify({
            "history": app.history,
            "session_ids": app.session_ids,
            "length": len(app.history)
        })

if __name__ == "__main__":
    threading.Thread(target=poll_api, daemon=True).start()
    port = int(os.getenv("PORT", 8880))

    app.run(host="0.0.0.0", port=port)




