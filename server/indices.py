"""Hai chỉ số tách riêng, không trung bình hóa với nhau.

- Môi trường nhà nấm: nhiệt độ, độ ẩm KK, ánh sáng, độ ẩm giá thể -> phun sương, quạt, màn che, đèn.
- Chất lượng nước cấp: pH, độ mặn/TDS -> nước có an toàn để tưới/phun sương không.

Nước không đạt thì kết luận chung không bao giờ là "Tốt", và hiện rõ thiết bị nào đang bị khóa
(theo đúng firmware ESP32: pH ngoài khoảng -> khóa bơm + phun sương; mặn vượt ngưỡng -> khóa bơm).
"""
from sensors import fmt, num

ENV_SENSORS = ("t", "h", "ANHSANG", "GT_AMDAT")
WATER_SENSORS = ("GT_PH", "GT_DOMAN")

PENALTY = {"info": 3, "warning": 12, "critical": 25}
LEVEL_PENALTY = {"warning": 4, "critical": 8, "fault": 8}  # trạng thái tức thời, chưa kéo dài
WATER_ADVICE = "Kiểm tra nguồn nước, lọc nước hoặc thay nước."


def water_locks(data, th):
    """Thiết bị firmware đang khóa vì nước: [{'devices': [...], 'reason': ...}]."""
    locks = []
    ph, sal = num(data.get("GT_PH")), num(data.get("GT_DOMAN"))
    lo, hi = th["SET_PHLOW"], th["SET_PHHIGH"]
    if ph is not None:
        if lo is None or hi is None:
            # firmware so với 0 khi chưa cài -> pH luôn bị coi là ngoài khoảng
            locks.append({"devices": ["máy bơm tưới", "phun sương"],
                          "reason": "chưa cài đủ ngưỡng pH (SET_PHLOW/SET_PHHIGH) nên ESP32 coi pH là lệch"})
        elif ph < lo or ph > hi:
            locks.append({"devices": ["máy bơm tưới", "phun sương"],
                          "reason": f"pH {fmt('GT_PH', ph)} ngoài khoảng {lo:g}–{hi:g}"})
    s = th["SET_DOMAN"]
    if sal is not None and s is not None and sal > s:
        locks.append({"devices": ["máy bơm tưới"], "reason": f"độ mặn {fmt('GT_DOMAN', sal)} vượt {fmt('GT_DOMAN', s)}"})
    return locks


def lock_text(locks):
    devices = []
    for lock in locks:
        devices += [d for d in lock["devices"] if d not in devices]
    if not devices:
        return None
    verb = "đang bị khóa"
    return (f"Nước cấp không đạt — {' và '.join(devices)} {verb} "
            f"({'; '.join(lock['reason'] for lock in locks)}). {WATER_ADVICE}")


def summary_lines(idx):
    """Các dòng hiển thị 2 chỉ số (dùng cho Zalo, báo cáo)."""
    if not idx:
        return []
    env, water = idx["environment"], idx["water"]
    lines = [
        f"🌿 Môi trường: {env['score']}/100 ({env['label']})",
        f"💧 Nước cấp: {water['label']} ({water['score']}/100)",
    ]
    if water["lock_text"]:
        lines.append("🚫 " + water["lock_text"])
    return lines


def _group(sensor):
    return "water" if sensor in WATER_SENSORS else "env"


def compute(sensors, findings, data, th):
    """sensors: trạng thái tức thời từng cảm biến; findings: kết quả phân tích."""
    pen = {"env": 0, "water": 0}
    issues = {"env": [], "water": []}
    for f in findings:
        groups = ("env", "water") if f["category"] == "system" else (_group(f.get("sensor")),)
        for g in groups:
            pen[g] += PENALTY.get(f["severity"], 0)
            if f["severity"] in ("warning", "critical"):
                issues[g].append(f["title"])
    for key, s in sensors.items():
        pen[_group(key)] += LEVEL_PENALTY.get(s["level"], 0)

    env = max(0, 100 - pen["env"])
    env_label = "Tốt" if env >= 85 else "Khá" if env >= 65 else "Cần chú ý" if env >= 40 else "Nguy hiểm"

    locks = water_locks(data, th)
    water = max(0, 100 - pen["water"])
    if locks:
        water = min(water, 35)
    water_label = "Không đạt" if locks or water < 50 else "Cần theo dõi" if water < 85 else "Đạt"

    overall = min(env, water)
    if locks:
        overall_label = "Nước không đạt"
    else:
        overall_label = "Tốt" if overall >= 85 else "Khá" if overall >= 65 else "Cần chú ý" if overall >= 40 else "Nguy hiểm"
    return {
        "environment": {"score": env, "label": env_label, "issues": issues["env"]},
        "water": {"score": water, "label": water_label, "issues": issues["water"],
                  "locks": locks, "lock_text": lock_text(locks)},
        "overall": {"score": overall, "label": overall_label},
    }
