#!/usr/bin/env python3
"""Tải lịch sử VN-Index và NAV chứng chỉ quỹ, xuất ra JSON để nhập vào trang theo dõi.

Chỉ dùng thư viện chuẩn của Python 3.8+, không cần pip install.

Ví dụ:
    python cap_nhat_du_lieu.py --funds DCDS VESAF --days 365
    python cap_nhat_du_lieu.py --funds E1VFVN30 --days 730 --out du_lieu.json

Kết quả: file du_lieu.json (và tự sao chép vào clipboard nếu máy hỗ trợ).
Trên trang web, bấm "Nhập JSON" rồi dán nội dung vào.
"""
import argparse
import json
import platform
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

VN_TZ = timezone(timedelta(hours=7))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
VCI_URL = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
VCI_REF = "https://trading.vietcap.com.vn/"
FM_BASE = "https://api.fmarket.vn/res"
FM_REF = "https://fmarket.vn/"


def post_json(url, body, referer, retries=3):
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": referer,
        "Origin": referer.rstrip("/"),
        "User-Agent": UA,
    }
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # mạng lỗi, bị chặn, JSON hỏng...
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Gọi {url} thất bại: {last}")


def fetch_vci(symbol, days, scale_below):
    """Giá đóng cửa theo ngày từ VCI (chỉ số hoặc ETF niêm yết trên HOSE)."""
    res = post_json(
        VCI_URL,
        {"timeFrame": "ONE_DAY", "symbols": [symbol],
         "to": int(time.time()) + 86400, "countBack": days},
        VCI_REF,
    )
    block = res[0] if isinstance(res, list) else res
    by_date = {}
    for t, c in zip(block["t"], block["c"]):
        d = datetime.fromtimestamp(int(t), VN_TZ).strftime("%Y-%m-%d")
        by_date[d] = float(c)
    if not by_date:
        raise RuntimeError(f"VCI không trả dữ liệu cho mã {symbol}.")
    # Nguồn có thể trả giá theo đơn vị nghìn đồng (33,15 thay vì 33.150)
    if max(by_date.values()) < scale_below:
        by_date = {d: v * 1000 for d, v in by_date.items()}
    return [{"d": d, "v": round(v, 2)} for d, v in sorted(by_date.items())]


def fetch_vnindex(days):
    return fetch_vci("VNINDEX", days, 100)


def fetch_fmarket(symbol, days):
    """NAV chứng chỉ quỹ mở theo mã, nguồn Fmarket."""
    res = post_json(
        FM_BASE + "/products/filter",
        {"types": ["NEW_FUND", "TRADING_FUND"], "issuerIds": [], "sortOrder": "DESC",
         "sortField": "navTo6Months", "page": 1, "pageSize": 100, "isIpo": False,
         "fundAssetTypes": [], "bondRemainPeriods": [], "searchField": symbol,
         "isBuyByReward": False, "thirdAppIds": []},
        FM_REF,
    )
    rows = res.get("data", {}).get("rows", [])
    row = next((r for r in rows if str(r.get("shortName", "")).upper() == symbol.upper()), None)
    if not row:
        raise LookupError(symbol)
    end = datetime.now(VN_TZ)
    start = end - timedelta(days=days)
    nav = post_json(
        FM_BASE + "/product/get-nav-history",
        {"isAllData": 0, "productId": row["id"],
         "fromDate": start.strftime("%Y%m%d"), "toDate": end.strftime("%Y%m%d")},
        FM_REF,
    )
    by_date = {}
    for r in nav.get("data", []):
        if r.get("nav") is not None:
            by_date[str(r["navDate"])[:10]] = float(r["nav"])
    return [{"d": d, "v": round(v, 2)} for d, v in sorted(by_date.items())]


def fetch_fund(symbol, days):
    """Quỹ mở: lấy NAV từ Fmarket. Không có ở đó (ví dụ ETF như E1VFVN30, FUEVFVND)
    thì lấy giá đóng cửa trên HOSE từ VCI."""
    try:
        return fetch_fmarket(symbol, days)
    except LookupError:
        try:
            return fetch_vci(symbol.upper(), days, 1000)
        except Exception as e:
            raise RuntimeError(f"Không tìm thấy mã '{symbol}' trên Fmarket lẫn VCI (sai mã?): {e}")


def copy_to_clipboard(text):
    try:
        sysname = platform.system()
        if sysname == "Darwin":
            cmd = ["pbcopy"]
        elif sysname == "Windows":
            cmd = ["clip"]
        else:
            cmd = ["xclip", "-selection", "clipboard"]
        enc = "utf-16" if sysname == "Windows" else "utf-8"
        subprocess.run(cmd, input=text.encode(enc), check=True)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="Tải VN-Index và NAV quỹ thành JSON cho trang theo dõi.")
    ap.add_argument("--funds", nargs="*", default=[], help="Mã quỹ, ví dụ: DCDS VESAF E1VFVN30")
    ap.add_argument("--days", type=int, default=365, help="Số ngày lịch sử (mặc định 365)")
    ap.add_argument("--out", default="du_lieu.json", help="File JSON đầu ra")
    ap.add_argument("--no-index", action="store_true", help="Bỏ qua VN-Index")
    args = ap.parse_args()

    series, errors = [], []
    if not args.no_index:
        try:
            series.append({"id": "vni", "name": "VN-Index", "points": fetch_vnindex(args.days)})
        except Exception as e:
            errors.append(f"VN-Index: {e}")
    for code in args.funds:
        try:
            series.append({"id": "f_" + code.upper(), "name": code.upper(),
                           "points": fetch_fund(code, args.days)})
        except Exception as e:
            errors.append(f"{code}: {e}")
        time.sleep(0.5)  # nhẹ tay với máy chủ

    for s in series:
        n = len(s["points"])
        last = s["points"][-1] if n else None
        print(f"- {s['name']}: {n} điểm" + (f", mới nhất {last['d']} = {last['v']:,}" if last else ""))
    for e in errors:
        print("LỖI ->", e, file=sys.stderr)

    series = [s for s in series if s["points"]]
    if not series:
        print("Không có dữ liệu nào để lưu.", file=sys.stderr)
        sys.exit(1)

    text = json.dumps({"updated": datetime.now(VN_TZ).isoformat(timespec="minutes"), "series": series},
                      ensure_ascii=False)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Đã ghi {args.out}.")
    if copy_to_clipboard(text):
        print("Đã sao chép vào clipboard: mở trang web, bấm 'Nhập JSON' rồi dán (Ctrl/Cmd+V).")
    else:
        print("Mở file JSON, sao chép nội dung rồi dán vào nút 'Nhập JSON' trên trang web.")
    if errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
