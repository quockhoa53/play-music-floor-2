# Floor 2 Studio: phòng nghe nhạc YouTube chung, có AI 🎵🎀

Floor 2 Studio là website cho một nhóm bạn **cùng nghe nhạc YouTube đồng bộ theo thời gian thực**: chung một hàng đợi, chung một bài đang phát, chat với nhau trong lúc nghe. Đi kèm là **Em bé floor 2**, một thành viên AI hiểu chuyện trong phòng. Em bé tìm và gợi ý bài, giải thích lời bài hát, phân xử tranh luận bằng nguồn Wikipedia, nhớ gu của từng người qua nhiều ngày, chào người mới vào phòng, nhìn được ảnh và GIF trong chat, và làm MC Radio dẫn chuyện giữa các bài.

- Backend: **FastAPI** (Python 3.11) với REST và WebSocket.
- Frontend: HTML, Tailwind (CDN) và JavaScript thuần, không cần bước build.
- AI: **Groq** (gói miễn phí). `openai/gpt-oss-120b` / `gpt-oss-20b` làm agent dùng công cụ, `qwen/qwen3.8-27b` lo trò chuyện, tóm tắt và xem ảnh, `llama-prompt-guard-2` phát hiện tiêm lệnh.
- Lưu trữ: **Supabase** (Postgres hoặc REST). Thiếu database thì app vẫn chạy, dữ liệu chỉ nằm trong bộ nhớ.
- Triển khai: **Render** (Blueprint `render.yaml`), có CI trên GitHub Actions.

---

## Mục lục

1. [Tính năng](#1-tính-năng)
2. [Kiến trúc & cấu trúc thư mục](#2-kiến-trúc--cấu-trúc-thư-mục)
3. [Chạy ở máy cá nhân](#3-chạy-ở-máy-cá-nhân)
4. [Biến môi trường](#4-biến-môi-trường)
5. [Database (Supabase)](#5-database-supabase)
6. [Triển khai lên Render & CI](#6-triển-khai-lên-render--ci)
7. [Em bé floor 2: hệ thống AI](#7-em-bé-floor-2-hệ-thống-ai)
8. [Hạn mức Groq miễn phí & cách app xoay xở](#8-hạn-mức-groq-miễn-phí--cách-app-xoay-xở)
9. [Bảo mật](#9-bảo-mật)
10. [Kiểm thử](#10-kiểm-thử)
11. [Theo dõi & đo đạc](#11-theo-dõi--đo-đạc)
12. [API & giao thức WebSocket](#12-api--giao-thức-websocket)
13. [Giới hạn hệ thống](#13-giới-hạn-hệ-thống)
14. [Xử lý sự cố](#14-xử-lý-sự-cố)

---

## 1. Tính năng

### Nghe nhạc chung
- **Đồng bộ thời gian thực:** mọi người trong phòng nghe cùng bài, cùng vị trí. Host điều khiển phát, dừng, tua cho cả phòng. Thành viên có thể tua riêng trên máy mình rồi bấm "Về trực tiếp".
- **Hàng đợi FIFO:** bài đầu phát ngay, các bài sau xếp hàng, phát xong tự chuyển. Có tab lịch sử và nút kéo bài lên đầu cho host.
- **Bỏ phiếu qua bài:** cần đa số (≥ 50%) thành viên online đồng ý, chống tạo nhiều tài khoản ảo để gian lận phiếu. Host cũng không tự ý bỏ qua bài được.
- **Ưu tiên bài:** thành viên đề xuất đẩy một bài lên phát kế tiếp, đủ phiếu thì bài được ưu tiên.
- **DJ Mode:** chỉ host được thêm bài.
- **Auto-DJ:** hàng đợi trống thì AI tự chọn bài nối tiếp theo gu của phòng.
- **Ba chế độ hiển thị:** 🎬 Video, 📻 Radio (đĩa vinyl xoay, sóng nhạc) và 🎤 Lời bài hát. Nếu video bị chặn nhúng thì tự chuyển sang phát **chỉ âm thanh** (`/api/youtube/audio/{id}`).
- **Tìm kiếm khi đang gõ:** gõ tên bài hoặc dán link YouTube, có phím tắt `/`, `Ctrl+K`, `↑↓`, `Enter`, `Esc`.
- **Gợi ý nhanh:**
  - Thanh gợi ý 1 chạm hiện các bài liên quan đến bài đang phát.
  - Ô tìm kiếm khi trống hiện gợi ý theo **gu của từng thành viên đang online**.
  - Phòng mới chưa có lịch sử thì hiện **bảng xếp hạng thịnh hành YouTube Music Việt Nam**.

### Lời bài hát đồng bộ
- Lấy lời từ **LRCLIB**, chọn đúng phiên bản theo **độ dài thật của video**.
- **Canh từng dòng** theo phụ đề cùng ngôn ngữ của video. MV có đoạn intro dài hay chèn thêm đoạn giữa bài vẫn khớp.
- Video có phụ đề lời bài hát do kênh đăng thì dùng thẳng phụ đề đó.
- Mỗi kết quả có **độ tin cậy**. Bài không tự canh được thì hiện nhãn "Có thể lệch".
- **Host hiệu chỉnh độ lệch một lần**: cả phòng thấy ngay, và giá trị được lưu theo video cho mọi lần phát sau. Thành viên chỉnh riêng cho máy mình, ví dụ để bù độ trễ tai nghe Bluetooth.

### Chat
- Chat realtime, @tag thành viên, GIF và sticker (Giphy cùng **bộ sticker Bé Floor 2** riêng), gửi ảnh (dán bằng `Ctrl+V` cũng được).
- Thả biểu cảm bay khắp màn hình, có âm báo.
- Câu trả lời của AI được định dạng: đoạn văn, danh sách, chữ in đậm, và chữ hiện dần khi đang trả lời.

### Em bé floor 2 (AI): xem chi tiết ở [mục 7](#7-em-bé-floor-2-hệ-thống-ai)
- Gọi bằng cách tag `@Em bé floor 2` trong chat.
- Gợi ý nhạc đúng tâm trạng và thể loại, chỉ từ bài có thật trên YouTube, kèm thẻ **"+ Thêm"** để thêm một chạm.
- Tự thêm bài vào hàng đợi khi được nhờ, theo đúng quyền của người yêu cầu.
- Giải thích lời bài hát, phân xử tranh luận, kể drama nghệ sĩ, tất cả đều có nguồn.
- Trí nhớ dài hạn, chế độ chủ động, xem ảnh và GIF.
- **MC Radio** giọng nữ tiếng Việt (Microsoft HoaiMy) dẫn chuyện mỗi khi chuyển bài, với 7 phong cách khác nhau.
- **Thẻ "Vibe phòng"** phân tích gu nhạc chung của cả phòng.

### Phòng & quyền
- Phòng công khai hoặc **riêng tư**: có mật khẩu (băm bằng PBKDF2) hoặc link mời.
- Host là người tạo phòng. Khi host rời đi, quyền host tự chuyển cho thành viên vào phòng sớm nhất.
- **Chuyển tab hay rớt mạng không bị tính là rời phòng:** thành viên được giữ trong phòng thêm 5 phút. Chỉ bấm "Rời phòng" hoặc đóng tab mới rời ngay.
- Có giao diện Sáng / Tối, và dùng được trên điện thoại.

---

## 2. Kiến trúc & cấu trúc thư mục

```
Trình duyệt (HTML + Tailwind + JS)
   │  REST (fetch)            WebSocket /ws/{room_id}
   ▼                          ▼
FastAPI (main.py) ── phòng, hàng đợi, đồng bộ, quyền, chat, AI ── bộ nhớ trong (phòng, phiên)
   │
   ├── services/youtube.py   tìm kiếm, bài liên quan, bảng xếp hạng, luồng âm thanh (Innertube, yt-dlp)
   ├── services/lyrics.py    LRCLIB + phụ đề YouTube, canh từng dòng
   ├── services/agent.py     agent gpt-oss gọi công cụ, chữ hiện dần
   ├── services/ai.py        Qwen: trò chuyện dự phòng, tóm tắt, MC, Vibe, DJ, lời chủ động
   ├── services/memory.py    trí nhớ dài hạn (hồ sơ thành viên, trí nhớ phòng, lịch nhắc)
   ├── services/vision.py    mô tả ảnh / GIF (Qwen vision)
   ├── services/knowledge.py Wikipedia: dữ kiện & drama nghệ sĩ
   ├── services/safety.py    chống tiêm lệnh, cổng kiểm tra hành động
   ├── services/ratelimit.py giới hạn token theo (key, model), thời gian chờ khi bị 429
   ├── services/telemetry.py bộ đếm, độ trễ, sự kiện, log [ai-metric]
   └── services/database.py  Supabase Postgres / REST (tùy chọn)
```

| Đường dẫn | Vai trò |
|---|---|
| `main.py` | Ứng dụng FastAPI: model `Room` / `Track` / `User`, toàn bộ REST endpoint, WebSocket, đồng bộ phát nhạc, gợi ý, điều phối AI, chế độ chủ động, Auto-DJ, giữ chỗ khi rớt kết nối |
| `services/agent.py` | Agent Em bé: 12 công cụ, chọn công cụ theo ý định, vòng gọi tối đa 3–4 bước, thẻ bài chỉ từ kết quả công cụ, yêu cầu viết lại khi model bịa bài, chuyển sang model dự phòng |
| `services/ai.py` | Gọi Groq dùng chung (`groq_chat`), pipeline chat Qwen dự phòng, tóm tắt hội thoại chạy nền, kho hit theo tâm trạng, MC Radio và giọng đọc, Vibe, AI DJ |
| `services/memory.py` | `MemoryStore`: hồ sơ thành viên (gu nhạc học từ hành vi, điều đã kể), trí nhớ phòng, lịch nhắc, ghi DB có gom nhóm |
| `services/safety.py` | Mẫu câu tiêm lệnh tiếng Việt và Anh, Prompt Guard, vô hiệu hoá chữ không tin cậy, cổng kiểm tra ý định trước khi hành động |
| `services/ratelimit.py` | Theo dõi token trong 60 giây theo từng (key, model), đọc `retry-after`, cho nghỉ đúng (key, model) bị chặn |
| `services/telemetry.py` | Bộ đếm, phân vị độ trễ (p50 / p95), sự kiện gần nhất |
| `services/youtube.py` | Tìm kiếm, bài liên quan (Mix), bảng xếp hạng YouTube Music VN, thông tin video, luồng âm thanh |
| `services/lyrics.py` | Lời đồng bộ, canh lời theo phụ đề, đánh giá độ tin cậy |
| `services/gifs.py` | Giphy GIF / sticker cùng bộ sticker Bé Floor 2 cục bộ (`images/stickers`) |
| `services/security.py` | Phiên đăng nhập, làm sạch dữ liệu đầu vào, mật khẩu phòng, giới hạn tần suất request |
| `services/ddos.py` | Chống DDoS tầng ứng dụng (cấm IP, giới hạn kết nối WebSocket) |
| `services/keep_alive.py` | Tự ping để Render free không ngủ |
| `static/` | `index.html`, `css/style.css`, `js/app.js` (logic chính), `js/chat.js`, `js/player.js`, `js/socket.js`, `img/em-be-floor-2.svg` (mascot) |
| `images/stickers/` | 30 sticker GIF Bé Floor 2 |
| `scripts/supabase_schema.sql` | Lược đồ database |
| `tests/smoke_test.py` | Smoke test offline (REST, WebSocket, AI giả lập) |
| `tests/ai_eval/` | Bộ test chất lượng AI dùng model thật |

---

## 3. Chạy ở máy cá nhân

Yêu cầu: **Python 3.10+** (Render dùng 3.11) và **Node** (chỉ cần để kiểm tra cú pháp JS trong CI).

```bash
git clone https://github.com/quockhoa53/play-music-floor-2.git
cd play-music-floor-2
pip install -r requirements.txt

# Tạo file .env (xem mục 4). Tối thiểu cần key Groq để có AI:
#   GROQ_API_KEYS=gsk_xxx,gsk_yyy

python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Trên Windows có thể nhấp đúp `run.bat`. Sau đó mở **http://localhost:8000**.

> Không cấu hình Supabase thì app chạy hoàn toàn trong bộ nhớ: phòng, chat, trí nhớ AI mất khi khởi động lại.
> Không có key Groq thì các tính năng AI tắt, nhưng phần nghe nhạc vẫn hoạt động bình thường.
>
> ⚠️ Nếu `.env` trỏ tới **database Supabase thật**, khi chạy local bạn sẽ đọc và ghi vào chính database đó.

---

## 4. Biến môi trường

Đặt trong file `.env` ở thư mục gốc khi chạy local (file này đã nằm trong `.gitignore`), hoặc trong **Render → Environment** khi triển khai.

### AI (Groq)
| Biến | Bắt buộc | Mặc định | Mô tả |
|---|---|---|---|
| `GROQ_API_KEYS` | Có (để có AI) | — | Một hoặc nhiều key `gsk_…`, cách nhau bằng dấu phẩy, khoảng trắng hoặc `;`. Code cũng nhận các tên `GROD`, `GROQ_API_KEY`, `GROQ_API_KEY_1`, `GROQ_API_KEY_2`. **Nên dùng key từ 2 tổ chức Groq khác nhau**, vì hạn mức tính theo tổ chức |
| `AI_AGENT_ENABLED` | Không | `1` | `0` = tắt agent gpt-oss, chỉ dùng pipeline Qwen |
| `AI_PROACTIVE_DEFAULT` | Không | `1` | `0` = phòng mới mặc định tắt chế độ chủ động |
| `PROFILE_ID_PEPPER` | Nên đặt | giá trị cố định trong code | Chuỗi bí mật dùng khi băm định danh thiết bị thành profile trí nhớ. **Chỉ đặt một lần:** đổi về sau thì mọi người mất trí nhớ AI |
| `ADMIN_TOKEN` | Không | trống (tắt) | Token để xem `/api/admin/ai-metrics`, gửi qua header `X-Admin-Token` |

### Database (Supabase): chọn một trong hai cách
| Biến | Mô tả |
|---|---|
| `DATABASE_URL` | **Cách 1 (khuyên dùng):** chuỗi kết nối Postgres (pooler) của Supabase |
| `SUPABASE_URL` + `SUPABASE_SECRET_KEY` (hoặc `SUPABASE_SERVICE_ROLE_KEY`) | **Cách 2:** dùng Supabase REST API |
| `SUPABASE_PUBLISHABLE_KEY` | Đang khai báo trong `render.yaml`; backend dùng secret key |

### Vận hành
| Biến | Mặc định | Mô tả |
|---|---|---|
| `LEAVE_GRACE_SEC` | `300` | Số giây giữ thành viên trong phòng khi rớt kết nối (chuyển tab, mạng chập chờn) |
| `KEEP_ALIVE_ENABLED` | `true` | Tự ping để Render free không ngủ |
| `APP_SERVER_PUBLIC_URL` | — | URL công khai của app; code cũng nhận `KEEP_ALIVE_URL` hoặc `RENDER_EXTERNAL_URL` |
| `KEEP_ALIVE_EVERY` / `KEEP_ALIVE_DELAY` | `480` / `60` | Chu kỳ ping và thời gian chờ sau khi khởi động, tính bằng giây |
| `KEEP_ALIVE_EXTRA_URLS` | — | Các service Render khác cần giữ thức, cách nhau bằng dấu phẩy |
| `DDOS_GUARD_ENABLED` | `true` | Bật chống DDoS tầng ứng dụng |
| `YT_PROXY` | — | Proxy dân dụng nếu YouTube chặn IP datacenter (dùng cho luồng chỉ âm thanh) |

Ví dụ `.env` tối thiểu:

```env
GROQ_API_KEYS=gsk_key_to_chuc_1,gsk_key_to_chuc_2
PROFILE_ID_PEPPER=<chuỗi-ngẫu-nhiên-dài>
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SECRET_KEY=<secret key>
ADMIN_TOKEN=<chuỗi-ngẫu-nhiên-dài>
```

Tạo chuỗi ngẫu nhiên: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

---

## 5. Database (Supabase)

Mở **Supabase → SQL Editor**, chạy toàn bộ `scripts/supabase_schema.sql`. Script dùng `IF NOT EXISTS` nên chạy lại nhiều lần vẫn an toàn.

| Bảng | Lưu gì |
|---|---|
| `rooms`, `room_queue`, `room_history`, `room_messages` | Phòng, hàng đợi, lịch sử phát, tin nhắn (để khôi phục sau khi khởi động lại) |
| `chat_media` | Ảnh gửi trong chat (base64, giữ tối đa 80 ảnh gần nhất) |
| `lyrics_offsets` | Độ lệch lời bài hát host đã hiệu chỉnh cho từng video |
| `member_profiles` | Trí nhớ AI về từng thành viên (gu nhạc, điều đã kể, lần chào gần nhất), dạng JSONB |
| `room_memories` | Trí nhớ của phòng (biệt danh, chuyện đùa riêng, lịch nhắc, tóm tắt các buổi), dạng JSONB |

- Mọi bảng đều bật **Row Level Security**: trình duyệt dùng publishable key không đọc được dữ liệu; backend dùng `DATABASE_URL` hoặc secret key nên không bị chặn.
- Các bảng AI chỉ được truy vấn theo khoá chính, nên **không cần thêm index**.
- Nếu một bảng còn thiếu, app tự nhận ra và lưu dữ liệu của bảng đó trong bộ nhớ thay vì báo lỗi.

---

## 6. Triển khai lên Render & CI

1. Push lên nhánh `main`. **GitHub Actions** (`.github/workflows/ci.yml`) sẽ:
   - kiểm tra cú pháp Python,
   - kiểm tra cú pháp các file JS frontend,
   - chạy `tests/smoke_test.py`.
2. Lần đầu: **Render Dashboard → New + → Blueprint →** chọn repo → **Apply**. Render đọc `render.yaml`: vùng Singapore, `healthCheckPath: /api/health`, và `autoDeployTrigger: checksPass` để chỉ tự deploy khi CI pass.
3. Vào **Environment** điền các biến `sync: false` (key Groq, Supabase, `PROFILE_ID_PEPPER`, `ADMIN_TOKEN`…).
4. Chạy `scripts/supabase_schema.sql` trên Supabase (mục 5).

Lưu ý với gói **Render Free**:
- Server ngủ sau 15 phút không có request. Tính năng keep-alive tự ping mỗi 8 phút.
- Mỗi lần khởi động lại, phiên đăng nhập trong bộ nhớ bị mất. Trình duyệt tự tạo phiên mới; trí nhớ AI vẫn còn nhờ định danh thiết bị và database.
- IP datacenter đôi khi bị YouTube làm chậm. Trong log sẽ có dòng `[youtube related fetch error] … ReadTimeout`; app tự dùng gợi ý theo gu thành viên hoặc bài trending thay thế.

---

## 7. Em bé floor 2: hệ thống AI

### 7.1 Luồng xử lý một tin nhắn
```
Tin nhắn có @Em bé floor 2
  ├─ Kiểm tra tiêm lệnh (mẫu câu tiếng Việt + Prompt Guard)      ┐ chạy song song
  ├─ Chờ mô tả ảnh vừa gửi (nếu có) + tóm tắt hội thoại          ┘
  ├─ Nhận diện ý định: music / add / facts / drama / lyrics / memory / remind
  │     ├─ Không có ý định nào (chuyện phiếm, chào hỏi, hỏi lại chuyện cũ, xem ảnh) → Qwen
  │     └─ Có ý định → agent gpt-oss-120b, chỉ kèm công cụ và quy tắc của ý định đó
  │            ├─ 120b bận hoặc hết hạn mức → gpt-oss-20b
  │            └─ cả hai không dùng được → pipeline Qwen dự phòng (trung thực: không giả vờ đã làm việc nó không làm được)
  └─ Chữ hiện dần qua WebSocket → tin nhắn cuối cùng (kèm thẻ bài) thay bong bóng tạm
```

### 7.2 Công cụ của agent
| Công cụ | Việc làm | Ý định |
|---|---|---|
| `search_songs` | Tìm bài thật trên YouTube (tìm kèm biến thể "official mv", lọc remix, cover, video tổng hợp, ít view) | music, add, facts, lyrics |
| `get_trending_songs` | Bảng xếp hạng YouTube Music Việt Nam | music |
| `get_related_songs` | Bài cùng gu với một bài (YouTube Mix) | music |
| `get_song_lyrics` | Lời bài hát (`current` = bài đang phát) | lyrics |
| `lookup_music_facts` | Tra Wikipedia (tiếng Việt trước, sau đó tiếng Anh) | facts, drama |
| `lookup_artist_drama` | Các mục Tranh cãi / Scandal / Đời tư trên Wikipedia, có nguồn | drama |
| `add_song_to_queue` | Thêm bài dưới tên người yêu cầu, áp đúng quyền của phòng | add |
| `schedule_reminder` | Hẹn giờ để Em bé nhắc cả phòng | remind |
| `remember_fact` / `forget_my_memory` | Ghi nhớ / xoá trí nhớ của chính người gửi | memory |

Các quy tắc chính:
- Thẻ bài **chỉ chứa video có thật trong kết quả công cụ**. Nếu model liệt kê bài không có trong kết quả, nó bị yêu cầu viết lại.
- Năm và con số chỉ được nêu khi có nguyên văn trong nguồn.
- Kể drama phải nêu nguồn, dùng từ trung lập; chuyện tình dục, nội dung riêng tư bị phát tán, sức khoẻ, con cái chỉ được nhắc chung.

### 7.3 Hiểu ngữ cảnh hội thoại
- **Nguyên văn các tin gần nhất:** tối đa 45 phút, 40 tin hoặc khoảng 3.500 ký tự; dừng ở khoảng im lặng hơn 20 phút.
- **Bản tóm tắt cả buổi** cập nhật nền: chủ đề, mạch chuyện, ai nói gì, câu hỏi bỏ ngỏ, điều đã thống nhất, không khí, chuyện đùa riêng.
- Ảnh và GIF hiện trong bản ghi dưới dạng `[gửi ảnh: mô tả]`.
- Câu có từ chỉ định ("bài đó", "nó") được kèm thêm 3 tin gần nhất ngay cạnh câu hỏi.

### 7.4 Trí nhớ dài hạn
- **Định danh:** trình duyệt tự tạo khoá ngẫu nhiên `floor2_device_key`; server chỉ lưu bản băm (SHA-256 kèm `PROFILE_ID_PEPPER`) làm `profile_id`. Trùng tên hiển thị cũng không đọc được trí nhớ của nhau.
- **Học gu nhạc từ hành vi:**

  | Hành vi | Điểm |
  |---|---|
  | Thêm bài | +2 |
  | Nghe hết bài | +0,6 |
  | Thả reaction tích cực | +0,8 |
  | Vote qua bài trong 60 giây đầu | −2 (bài được đánh dấu "không thích") |
  | Vote qua bài sau đó | −0,8 |

  Điểm giảm một nửa sau mỗi 30 ngày.
- **Dữ kiện:** người dùng nhờ "nhớ giùm…", hoặc bản tóm tắt nền tự rút ra (bỏ thông tin nhạy cảm). Dữ kiện tự động không ghi đè điều người dùng đã chủ động kể.
- **Trí nhớ phòng:** biệt danh, chuyện đùa, lịch nhắc, tóm tắt mỗi ngày (bot hiểu được "hôm qua phòng hẹn gì").
- **Được dùng ở:** ngữ cảnh của agent và Qwen, gợi ý nhanh (loại bài hay bị bỏ qua), Auto-DJ, lời chào người mới.
- **"Quên mình đi"** xoá toàn bộ hồ sơ của chính người gửi, không xoá giùm người khác được.

### 7.5 Chế độ chủ động (host bật / tắt bằng nút "🤖 Em bé chủ động")
| Tình huống | Bot làm gì |
|---|---|
| Có người vào phòng | Chào kèm 1 bài hợp gu (hoặc bài trending); mỗi người tối đa 1 lần / 6 giờ, lưu trong hồ sơ |
| Câu hỏi gửi cả phòng, 2 phút chưa ai đáp | Trả lời giúp; không được hành động thay ai |
| Đến giờ hẹn | Nhắc cả phòng (chỉ khi có người online; quá giờ 30 phút thì bỏ qua) |
| 3 bài bị vote qua trong 10 phút | Bình luận vui, gợi ý 1 bài đổi gió |

Mỗi phòng có giới hạn chung: hai lần bot tự lên tiếng cách nhau tối thiểu 90 giây, tối đa 6 lần mỗi giờ.

### 7.6 MC Radio, Vibe, Auto-DJ
- **MC Radio** (host bật): lời dẫn đúng bài đang phát, 7 phong cách (hài hước, chuyên nghiệp, cà khịa, nhẹ nhàng, sôi động, kể chuyện, tâm tình). Giọng đọc là Microsoft HoaiMy (edge-tts); nếu Edge chậm quá 4,5 giây thì dùng Google TTS tiếng Việt. Lời dẫn cho bài kế tiếp được chuẩn bị sẵn.
- **Vibe phòng:** thẻ phân tích gu chung, lưu tạm 5 phút.
- **Auto-DJ:** hàng đợi trống thì tự nối bài theo gu phòng, không chọn bài thành viên đang online hay bỏ qua.

---

## 8. Hạn mức Groq miễn phí & cách app xoay xở

| Model | Request/phút | Request/ngày | Token/phút | Token/ngày |
|---|---|---|---|---|
| `openai/gpt-oss-120b` | 30 | 1.000 | 8.000 | **200.000** |
| `openai/gpt-oss-20b` | 30 | 1.000 | 8.000 | 200.000 |
| `qwen/qwen3.8-27b` | 30 | 1.000 | 8.000 | 200.000 |
| `meta-llama/llama-prompt-guard-2-86m` | 30 | 14.400 | 15.000 | 500.000 |

Hạn mức tính **riêng cho từng model** và theo từng tổ chức Groq. Hai giới hạn hay gặp nhất là **8K token/phút** và **200K token/ngày**.

Cách app xoay xở:
1. **Phân luồng:** chỉ câu cần công cụ mới vào agent; chuyện phiếm dùng Qwen.
2. **Prompt gọn:** chỉ gửi công cụ và quy tắc của ý định hiện tại, khoảng 500–950 token cố định mỗi lượt (trước là khoảng 3.300).
3. **Giới hạn phía client** (`services/ratelimit.py`): ước lượng token theo từng (key, model) trong 60 giây và giữ dưới khoảng 7.000. Request không vừa thì chuyển key hoặc model khác thay vì gửi rồi bị 429.
4. **Thời gian chờ chính xác:** đọc `retry-after` hoặc câu "try again in 24m17s", rồi chỉ cho nghỉ đúng cặp (key, model) bị chặn.
5. **Chuyển model:** `gpt-oss-120b` → `gpt-oss-20b` → Qwen. Khi agent hết hạn mức ngày, app bỏ qua agent cho đến lúc hồi.

> ⚠️ **Một lượt chạy đủ bộ test chất lượng (38 ca) tốn rất nhiều hạn mức ngày.** Nên chạy từng nhóm và tránh giờ phòng đông người. Cần ổn định hơn thì nâng Groq lên gói Developer.

---

## 9. Bảo mật

- **Phiên đăng nhập do server cấp** (token không đoán được), chặn mạo danh và cướp quyền host. WebSocket bắt buộc phải có phiên và kiểm tra Origin.
- **Phòng riêng tư:** mật khẩu băm PBKDF2 (120.000 vòng), link mời, chặn truy cập trái phép vào phòng hay lịch sử của người khác.
- **Làm sạch đầu vào** (chữ, emoji, màu, ID Giphy, ID media, ID sticker cục bộ); ảnh bìa chỉ dựng từ video ID đã kiểm tra.
- **Giới hạn tần suất** cho từng request REST và từng lệnh WebSocket; chống DDoS tầng ứng dụng; giới hạn số kết nối trên mỗi IP.
- **Chống tiêm lệnh vào AI** (`services/safety.py`):
  - Phát hiện bằng mẫu câu tiếng Việt và Anh, kèm Llama Prompt Guard 2 (Prompt Guard bắt tốt tiếng Anh nhưng bỏ lọt tiếng Việt, nên cần thêm mẫu câu).
  - Mọi chữ không tin cậy (chat, mô tả ảnh, tiêu đề YouTube, Wikipedia, trí nhớ) bị đổi `<` `>` và nhãn giả `[Hệ thống]`, nên không giả được cấu trúc prompt.
  - **Cổng hành động trên server:** thêm bài, xoá trí nhớ, đặt lịch, ghi nhớ chỉ chạy khi chính tin nhắn hiện tại có ý định đó, bất kể model quyết định gì. Lượt tự trả lời câu hỏi bỏ ngỏ và tin bị đánh dấu nghi vấn thì không được hành động.

---

## 10. Kiểm thử

### Smoke test (offline, chạy trong CI)
```bash
python tests/smoke_test.py
```
Bao phủ:
- REST, WebSocket, FIFO và khoá chống tranh chấp, phòng riêng tư, chống DDoS, bỏ phiếu qua bài, ưu tiên bài.
- Lời đồng bộ và thuật toán canh từng dòng, gợi ý (trending và gu thành viên).
- AI giả lập: vòng gọi công cụ, thẻ bài, trí nhớ, chế độ chủ động, xem ảnh, chống tiêm lệnh, phân luồng, giới hạn tốc độ và chuyển model dự phòng.
- Giữ chỗ khi rớt kết nối.

Test chạy hoàn toàn offline (không database, không Groq thật) và đặt `AI_PROACTIVE_DEFAULT=0` để kết quả ổn định.

### Bộ test chất lượng AI (dùng model thật)
```bash
python -m tests.ai_eval.run_eval --category music        # theo nhóm
python -m tests.ai_eval.run_eval --only fact_year,inj_fake_system
python -m tests.ai_eval.run_eval --judge -v               # thêm giám khảo AI (Qwen) cho tiêu chí khó viết thành quy tắc
python -m tests.ai_eval.run_eval --judge --save-baseline  # lưu làm mốc chuẩn
```
- 38 tình huống trong `tests/ai_eval/cases.json`, chia nhóm: music, actions, facts, context, memory, tools, vision, offtopic, injection.
- Mỗi ca chạy qua **toàn bộ** luồng trả lời thật trên một phòng giả lập (không database), rồi chấm tự động: công cụ đã gọi, nội dung câu trả lời, số thẻ bài, bài có vào hàng đợi không, trí nhớ có bị xoá không.
- Báo cáo lưu vào `tests/ai_eval/reports/` (đã gitignore). Nếu có `baseline.json`, trình chạy liệt kê các ca **bị tệ đi** và trả mã lỗi.
- Chạy lại sau mỗi lần sửa prompt hoặc đổi model. Nhớ lưu ý về hạn mức ở mục 8.

---

## 11. Theo dõi & đo đạc

- **`GET /api/health`**: trạng thái server, database, keep-alive, cache, và `ai_metrics`:
  - tỉ lệ câu trả lời thành công qua agent, tỉ lệ phải dùng dự phòng, tỉ lệ JSON lỗi,
  - số câu chuyển vào agent / sang Qwen, số lần agent tạm nghỉ vì hết hạn mức, số lần bị 429,
  - số tin bị đánh dấu tiêm lệnh, số hành động bị chặn,
  - độ trễ trả lời p50 / p95 và thời gian đến chữ đầu tiên.
- **`GET /api/admin/ai-metrics`** (header `X-Admin-Token: $ADMIN_TOKEN`): toàn bộ bộ đếm, độ trễ theo từng model và công cụ, 40 sự kiện gần nhất, token đã dùng trong phút và thời gian nghỉ của từng (key, model), thời gian agent còn tạm nghỉ.
- **Log trên Render:** mỗi sự kiện in ra một dòng `[ai-metric] {...}` (kết quả trả lời, lỗi 429, chuyển model, hành động bị chặn, tin chủ động…) để tìm nhanh.

---

## 12. API & giao thức WebSocket

### REST
| Method & đường dẫn | Mô tả |
|---|---|
| `POST /api/session` | Tạo hoặc tiếp tục phiên (`X-Session-Token`, `X-Device-Key`) |
| `GET /api/rooms` · `POST /api/rooms` | Danh sách phòng công khai · tạo phòng |
| `GET /api/rooms/{id}` · `POST /api/rooms/{id}/access` | Thông tin phòng · vào phòng riêng tư (mật khẩu hoặc link mời) |
| `GET /api/youtube/search?q=` | Tìm kiếm YouTube |
| `GET /api/youtube/info?url=` | Thông tin video từ link |
| `GET /api/youtube/related/{video_id}` | Bài liên quan |
| `GET /api/youtube/audio/{video_id}` | Luồng chỉ âm thanh (có HTTP Range) |
| `GET /api/suggestions` | Bài thịnh hành YouTube Music Việt Nam |
| `GET /api/rooms/{id}/suggestions` | Gợi ý theo gu thành viên online (hoặc trending) |
| `GET /api/lyrics?title=&author=&duration=&video_id=` | Lời đồng bộ kèm độ tin cậy |
| `GET /api/gifs/search` · `GET /api/stickers/search` | GIF / sticker |
| `POST /api/chat/upload` · `GET /api/chat/media/{id}` | Gửi / xem ảnh chat |
| `POST /api/rooms/{id}/ai/dj` | AI DJ tạo danh sách bài theo vibe |
| `GET /api/rooms/{id}/ai/vibe` | Thẻ Vibe phòng |
| `GET /api/rooms/{id}/ai/mc-intro?track_uid=` · `GET /api/ai/mc-audio/{id}` | Lời dẫn MC · file âm thanh MP3 |
| `GET /api/health` · `GET /api/admin/ai-metrics` | Theo dõi (mục 11) |

### WebSocket `/ws/{room_id}?token=…&invite=…`
Client gửi `{"action": "...", ...}`.
- **Vào / ra:** `join` (bắt buộc đầu tiên), `ping` (giữ kết nối mỗi 25 giây), `leave` (rời phòng ngay).
- **Chat:** `chat`, `reaction`.
- **Hàng đợi:** `add_track`, `remove_track`, `reorder_queue`, `request_priority`, `cancel_priority`, `vote_skip`.
- **Phát nhạc:** `control` (host: play / pause / seek; muốn qua bài thì phải vote), `track_ended`.
- **Chỉ host:** `toggle_dj_mode`, `toggle_auto_dj`, `toggle_mc_mode`, `toggle_ai_proactive`, `lyrics_offset`.

Server gửi các sự kiện như:
- trạng thái và phát nhạc: `init_state`, `playback_changed`, `queue_updated`, `skip_vote_updated`…
- thành viên: `user_joined`, `user_left`.
- chat: `chat`, `reaction`.
- AI: `ai_typing` (kèm trạng thái công cụ), `ai_stream` (chữ hiện dần).
- cài đặt và lỗi: `lyrics_offset`, `mc_mode_changed`, `ai_proactive_changed`, `error`.

---

## 13. Giới hạn hệ thống

| Giới hạn | Giá trị |
|---|---|
| Số phòng tối đa | 300 |
| Hàng đợi mỗi phòng | 50 bài; mỗi thành viên (không phải host) tối đa 10 bài |
| Kết nối mỗi IP trong một phòng | 5 |
| Kích thước một khung WebSocket | 4 KB |
| Ảnh chat | 2,5 MB / ảnh; cache 120 ảnh hoặc 60 MB |
| Tin nhắn lưu mỗi phòng | 100 gần nhất |
| Giữ chỗ khi rớt kết nối | 300 giây (`LEAVE_GRACE_SEC`) |
| Bot tự lên tiếng | cách nhau ≥ 90 giây, tối đa 6 lần / giờ / phòng |
| Gọi @Em bé | 8 lần / phút / người |

---

## 14. Xử lý sự cố

| Hiện tượng | Nguyên nhân & cách xử lý |
|---|---|
| Em bé trả lời "kém thông minh", không tìm bài hay tra cứu được | Agent đang dùng Qwen dự phòng vì gpt-oss hết hạn mức ngày. Xem `/api/health` → `ai_metrics.agent_quota_cooldowns`, hoặc log `agent_429 … "daily": true`. Chờ hạn mức hồi, thêm key ở tổ chức Groq khác, hoặc nâng gói |
| Log có nhiều `[youtube related fetch error] … ReadTimeout` | YouTube trả chậm cho IP datacenter của Render. App tự dùng gợi ý theo gu hoặc trending; có thể đặt `YT_PROXY` |
| Lời bài hát lệch | Nhãn "Có thể lệch" nghĩa là video không có phụ đề cùng ngôn ngữ và không có bản lời cùng độ dài. Host chỉnh bằng nút −/+ một lần; giá trị được lưu (cần bảng `lyrics_offsets`) |
| Trí nhớ AI / lịch nhắc mất sau khi deploy | Chưa tạo bảng `member_profiles` / `room_memories`, hoặc đã đổi `PROFILE_ID_PEPPER` |
| MC đọc bằng giọng Google (hơi máy) | Edge TTS đang lỗi hoặc chậm nên app tạm dùng Google; tự quay lại giọng HoaiMy sau 2 phút |
| Chuyển tab xong bị "đã rời phòng" | Rớt kết nối quá `LEAVE_GRACE_SEC`. Quay lại tab là tự kết nối lại; tăng giá trị này nếu cần |
| Bot chào hoặc bình luận quá nhiều | Host tắt nút "🤖 Em bé chủ động", hoặc đặt `AI_PROACTIVE_DEFAULT=0` |
| Smoke test treo ở bước WebSocket | TestClient chạy mỗi kết nối trên một event loop riêng; tác vụ nền gắn với kết nối đã đóng sẽ không chạy. Gọi trực tiếp hàm cần kiểm tra như trong `test_tab_switch_grace_period_and_explicit_leave` |

---

Made with 🎧 cho những buổi nghe nhạc chung ở Floor 2.
