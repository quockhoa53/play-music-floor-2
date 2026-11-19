# Floor 2 Studio — Phòng Nhạc YouTube Đồng Bộ, Radio & Chat Realtime 🎵📻💬

Website cho phép mọi người cùng vào phòng nghe nhạc YouTube đồng bộ thời gian thực, thêm nhạc theo thứ tự (FIFO Queue), chuyển **Chế độ Radio không cần xem video**, tìm kiếm YouTube **realtime as-you-type**, hỗ trợ **2 theme Sáng/Tối** và trò chuyện trực tiếp trong phòng.

---

## 🌟 Tính năng nổi bật

- **🌓 2 Theme Sáng & Tối**: Chuyển mượt mà giữa **Porcelain Daylight (Sáng)** và **Midnight Studio (Tối)**, tự lưu lựa chọn & phủ ánh sáng nền biến đổi theo ảnh bìa bài hát.
- **⚡ Tìm kiếm YouTube Realtime**: Gõ tên bài hát hoặc dán link YouTube vào thanh tìm kiếm trung tâm — kết quả hiện tức thì không cần bấm nút Search (hỗ trợ phím tắt `/`, `Ctrl+K`, `↑↓`, `Enter`, `Esc`).
- **📻 Chế độ Radio (Không cần xem video)**: Chuyển 1 chạm `🎬 Video ↔ 📻 Radio` không ngắt âm thanh, giao diện đĩa Vinyl xoay + 24 cột sóng visualizer + huy hiệu `ON AIR` + tự động fallback sang luồng **Pure Audio** (`/api/youtube/audio/{id}`) nếu video bị chặn nhúng.
- **📋 Hàng đợi phát theo thứ tự (FIFO)**: Bài đầu phát ngay, các bài sau xếp hàng tuần tự, tự động chuyển bài khi kết thúc, có tab lịch sử phát lại và nút đưa lên đầu hàng đợi (Host).
- **💬 Chat Realtime & Cảm xúc bay**: Nhắn tin tức thì qua WebSocket, thả biểu cảm bay khắp màn hình (❤️ 🔥 🎵 👏 🕺 🍻 🚀 😂), chuông thông báo Web Audio API, phân quyền Host / DJ Mode và bỏ phiếu Skip.

---

## 🚀 Chạy ở máy cá nhân (Local)

```bash
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Hoặc nhấp đúp file `run.bat` trên Windows, sau đó mở trình duyệt tại **http://localhost:8000**.

---

## ☁️ CI/CD & Deploy lên Render

Dự án đã tích hợp sẵn **GitHub Actions CI** (`.github/workflows/ci.yml`) và **Render Blueprint** (`render.yaml`):

1. Mỗi khi push code lên nhánh `main`, GitHub Actions tự động kiểm tra cú pháp Python, cú pháp JavaScript frontend và chạy bộ smoke test (`tests/smoke_test.py`).
2. Truy cập [Render Dashboard](https://dashboard.render.com) → **New +** → **Blueprint** → chọn repo `quockhoa53/play-music-floor-2` → bấm **Apply**.
3. Render sẽ tự đọc `render.yaml` (`healthCheckPath: /api/health`, `autoDeployTrigger: checksPass`) và chỉ tự động deploy sau khi CI trên nhánh `main` chạy pass.
