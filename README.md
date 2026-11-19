# vn-content

Pipeline làm nội dung video ngắn từ A đến Z, ghép các repo sẵn có thay vì viết lại:

```
URL / file ──► ingest ──► dub ──► render ──► meta ──► publish
               yt-dlp     vndub    ffmpeg    meta.json  social-auto-upload (`sau`)
               tải video  lồng     dọc 9:16  tiêu đề,    YouTube · Facebook · Instagram
               + phụ đề   tiếng    cháy phụ  mô tả,      (hẹn giờ: YouTube)
                          Việt     đề, cắt   hashtag
                                   ≤ 59 s
```

| Khâu | Repo / công cụ | Cách ghép |
|---|---|---|
| Tải | [yt-dlp](https://github.com/yt-dlp/yt-dlp) | thư viện Python (`pyproject.toml`) |
| Lồng tiếng | **vndub** (repo cục bộ, `D:\vndub\vndub`) | gọi bằng Python riêng của nó (`[tools].vndub_python`) |
| Dựng dọc | ffmpeg | gọi trực tiếp |
| Đăng | [social-auto-upload](https://github.com/dreammis/social-auto-upload) | `vendor.toml` ghim commit → `scripts/vendor.py` clone → venv riêng theo `uv.lock` của họ |
| Tuỳ chọn | Postiz, OpenShorts, MoneyPrinterTurbo, KrillinAI | có sẵn trong `vendor.toml` (`enabled = false`), `python scripts/vendor.py --all` để clone |

Mỗi khâu là một tiến trình riêng với môi trường riêng (vndub dùng torch cu128, `sau` ghim patchright/opencv…) nên
không xung đột phụ thuộc, và đổi một khâu (ví dụ thay `sau` bằng Postiz) không đụng các khâu khác.

## Cài đặt

Cần: `git`, `uv`, `ffmpeg` trong PATH và vndub đã cài (xem `TIEP_TUC.md` của vndub).

```powershell
.\scripts\bootstrap.ps1             # venv + clone repo ghim commit + cài sau + tạo config.toml + vnc doctor
.\scripts\bootstrap.ps1 -Browsers   # thêm: tải Chromium cho sau (~150 MB), cần khi đăng nhập/đăng bài
```

Sau đó mở `config.toml` kiểm tra `[paths]` và `[tools]`, rồi `.venv\Scripts\vnc doctor` phải ra toàn dấu ✓.
Thêm `.venv\Scripts` vào PATH hoặc `.venv\Scripts\Activate.ps1` để gõ `vnc` cho gọn.

## Dùng

```powershell
# 1. Thêm video. URL BẮT BUỘC ghi chú quyền sử dụng (xem "Quyền nội dung" bên dưới)
vnc add "https://www.youtube.com/watch?v=..." --rights "video của kênh tôi" --series ten-phim
vnc add D:\clips\bai1.mp4 --srt bai1.en.srt --lang en            # file có sẵn + phụ đề gốc tiếng Anh

# 2. Chạy tới bước meta (mặc định dừng ở đây để bạn xem kết quả)
vnc run 1
vnc status 1                       # xem đường dẫn video lồng tiếng, các clip, meta.json

# 3. Xem/sửa tiêu đề, mô tả: <dữ liệu>\content\jobs\0001\meta.json

# 4. Đăng lần đầu: đăng nhập tài khoản (mở trình duyệt, TỰ bạn đăng nhập — pipeline không giữ mật khẩu)
vnc login youtube main

# 5. Xem kế hoạch đăng (chỉ in lệnh, chưa đăng gì) rồi đăng thật
vnc publish 1
vnc publish 1 --yes
vnc publish 1 --yes --start "2026-12-01 18:00"       # YouTube: hẹn giờ, các clip cách nhau [publish].interval_hours
```

Chạy hết một lèo: `vnc run 1 --to publish --yes`. Chạy lại một bước: `vnc run 1 --from render --to render`.
Lỗi giữa chừng thì sửa nguyên nhân rồi `vnc run <id>` — bước đã xong không chạy lại, clip đã đăng không đăng lại.

## Chế độ truyện audio

Cùng hàng đợi, cùng năm bước, nhưng đầu vào là **chương văn bản** thay vì video:

```
chương .txt ─► ingest ─► dub ─► render ─► meta ─► publish
               gộp chương  vndub   ảnh theo cảnh (zoom chậm) + giọng + nhạc nền hạ khi có thoại
               + hook       speak   ─► video dài 16:9  ─► bản dọc 9:16 kết ở cliffhanger
               + từ điển    giọng cố định cả bộ        ─► điểm đặt quảng cáo giữa video
```

```powershell
# 1. Một lần cho mỗi thể loại: bỏ ảnh nền vào <media_root>\content\assets\<thể loại>\images (và nhạc nền vào \bgm)
# 2. Mỗi tập: truyền các chương (file hoặc thư mục), hook 2 câu và tóm tắt tập trước (viết tay hoặc nhờ agent viết)
vnc story D:\truyen\van-co\tap03 --series van-co --series-title "Vạn Cổ" --ep 3 `
          --hook "Ba năm trước hắn đã chết một lần. Đêm nay kẻ giết hắn đang ngồi đợi." --recap @tap02_tomtat.txt
vnc run 1                      # tới meta; xem D:\...\content\jobs\0001\{render\long.mp4, render\short.mp4, meta.json}
vnc publish 1 --yes            # long.mp4 chỉ lên YouTube; short.mp4 lên các nền tảng trong --platforms / config
vnc clean 1                    # sau khi đăng xong: xoá WAV và cảnh dựng tạm (giữ clip + meta.json)
```

| Thứ cần | Ở đâu |
|---|---|
| Thể loại (giọng, tốc độ, từ điển phát âm, hashtag, cụm từ nhạy cảm) | `presets\genres\<tên>.toml` — thêm thể loại = sao chép một file. Có sẵn: `tien-hiep-hac-am`, `linh-di-dan-gian`, `tam-su-gia-dinh` |
| Giọng đọc | `vndub speak` theo `[voice].style` của preset; `--series` = một bảng phân vai chung cả bộ truyện nên mỗi người một giọng qua mọi tập. Kịch bản có dòng `Tên: lời` thì phân vai; `one_voice = true` thì một người kể đọc hết |
| Ảnh nền | `--images DIR` hoặc `content\assets\<thể loại>\images`. Mỗi ảnh hiện `scene_seconds` giây, xen kẽ zoom vào/ra. Thiếu ảnh thì dùng nền trơn **và cảnh báo** — video ảnh tĩnh đơn điệu dễ bị YouTube hạ tiếp cận |
| Nhạc nền | `content\assets\<thể loại>\bgm`; tự hạ khi có giọng đọc, chuẩn hoá `-16 LUFS` |
| Bản dọc | cắt từ video dài, kết ở cuối một câu gần 90% tập (đổi: `--short-end 12:30`), cháy phụ đề của đoạn đó |
| Quảng cáo giữa video | `render\midrolls.json` (`youtube`: các mốc dạng `10:03`), đặt tay trong YouTube Studio |
| Cụm từ nhạy cảm | báo ở `source\safety.txt`, **không tự sửa chữ** — bạn duyệt |
| Câu mở đầu / tóm tắt | `--hook` / `--recap` (hoặc `@file`). Gợi ý viết hook nằm ở `[meta].hook_prompt` của preset |

Việc **không** làm tự động (cố ý): sinh ảnh bằng ComfyUI, tạo tiêu đề/hook bằng LLM, thử A/B thumbnail. Sửa
`meta.json` tay trước khi đăng, hoặc nhờ agent viết hook/tóm tắt rồi truyền vào `vnc story`.

### Quyết định mặc định (để an toàn)

- **Không đăng thật nếu thiếu `--yes`.** `publish` không có `--yes` chỉ in lệnh `sau`.
- **YouTube đăng ở chế độ `private`** (`[publish].visibility`) — đổi sang `public` khi đã quen.
- **Phụ đề tự động của YouTube không dùng** (hay lặp chữ). Không có phụ đề người làm → dùng `vndub dub-video`
  (Gemini chép lời + dịch, cần `GEMINI_API_KEY` trong `.env` của vndub). Phụ đề tiếng Việt có sẵn → không dịch.
- **Series**: `--series TÊN` cho các video cùng bộ dùng chung `cast.json` → mỗi nhân vật giữ một giọng qua mọi tập.

## Dữ liệu nằm ở đâu

Quy ước máy này: C: chỉ cài đặt hệ thống/ứng dụng; dữ liệu nặng sang SSD ngoài D:.

```
<media_root>\content\            media_root = [paths].media_root > VNDUB_MEDIA > <repo>\data
  state.db                       hàng đợi (SQLite)
  jobs\0001\source\              video + phụ đề tải về
  jobs\0001\dub\                 voice.wav, voice.srt, voice_dub.mp4 (+ báo cáo của vndub)
  jobs\0001\render\              part00.mp4, part01.mp4…, thumb.jpg, subs.srt
  jobs\0001\meta.json            tiêu đề/mô tả/hashtag từng clip — sửa tay được
  jobs\0001\run.log              đầu ra mọi lệnh ngoài
  series\<tên>\cast.json         bảng phân vai dùng chung của series
  assets\<thể loại>\images|bgm   kho ảnh nền và nhạc nền của chế độ truyện (job truyện: source\script.txt,
                                 dub\voice.wav+srt, render\long.mp4, short.mp4, midrolls.json)
```

SSD chưa cắm thì lệnh dừng ngay với thông báo rõ, không ghi nhầm sang ổ C:. Repo (code) và cookie đăng nhập của `sau`
(`vendor\social-auto-upload\cookies\`, đã được `.gitignore`) nằm cạnh nhau trên D:.

## Quyền nội dung và điều khoản nền tảng

- Chỉ tải và đăng lại nội dung **bạn có quyền dùng**: của chính bạn, đã xin phép, hoặc giấy phép cho phép (CC-BY…).
  Lồng tiếng lại video của người khác không làm nó thành của bạn; nền tảng có thể gỡ, gắn bản quyền hoặc tắt kiếm tiền.
- `--rights` được ghi vào mô tả khi `[meta].credit = true` cùng dòng "Nguồn: kênh — URL".
- `sau` đăng bằng **trình duyệt tự động** (không dùng API chính thức) nên có thể trái điều khoản hoặc bị chặn trên một số
  nền tảng; bản thân tài khoản của bạn chịu rủi ro đó. Muốn đường chính thống hơn: thay khâu đăng bằng Postiz
  (API chính thức) — chỉ cần viết lại `stages.publish`.
- Nhân bản giọng nhân vật (`--clone-voices --consent` của vndub) KHÔNG được bật bởi pipeline này; chạy vndub trực tiếp
  nếu bạn có quyền với các giọng đó.

## Kiểm thử

```powershell
.venv\Scripts\python -m pytest -q        # 62 test; phần dựng video chạy ffmpeg thật, yt-dlp/vndub/sau được giả lập
```

## Mở rộng

- Thêm nền tảng: bổ sung vào `PLATFORMS` trong `vncontent/stages.py` (khả năng: visibility / thumbnail / schedule) và tài khoản trong `config.toml`.
  TikTok chưa có CLI trong `sau` (chỉ có script ví dụ) nên chưa được hỗ trợ.
- Hình cho truyện audio: thay bước chọn ảnh trong `story.render` bằng ComfyUI (gọi API, xong thì gọi `/free` xả VRAM
  trước khi `vndub speak` chạy lại — không để hai mô hình chung VRAM 8 GB).
- Cắt clip hay từ video dài bằng AI: OpenShorts (`vendor.toml`, `enabled = false`) có thể thay bước `render`.
- Tiêu đề/mô tả bằng Gemini: viết thêm vào `stages.meta` (hiện lấy từ video gốc + mẫu cố định).
