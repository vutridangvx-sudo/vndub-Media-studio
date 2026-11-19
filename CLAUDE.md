# vn-content

Trả lời người dùng bằng tiếng Việt. Đọc `README.md` trước (kiến trúc, lệnh, dữ liệu nằm đâu).

## Quy tắc

- **Không đăng thật khi chưa được người dùng cho phép trong chat.** `vnc publish`/`run --to publish` luôn thử KHÔNG có
  `--yes` trước; chỉ thêm `--yes` khi người dùng đã đồng ý đăng đúng video đó lên đúng nền tảng/tài khoản đó.
- Không nhập mật khẩu/cookie hộ người dùng: `vnc login` mở trình duyệt để họ tự đăng nhập. Không commit `config.toml`,
  `.env`, `vendor/`, cookie.
- Không tải URL thay người dùng nếu họ chưa nói rõ có quyền với nội dung đó (`--rights` là bắt buộc, đừng tự bịa).
- Dữ liệu nặng (video, cache, state.db) đi vào `[paths].media_root` / `VNDUB_MEDIA` trên SSD ngoài D:, không vào C:.
  Model Hugging Face và venv của vndub ở C: là chủ ý.
- Không sửa code trong repo `vndub` từ đây; nếu cần đổi hành vi lồng tiếng, truyền cờ qua `[dub].extra_args`.
- Repo bên thứ ba ghim commit trong `vendor.toml`; muốn nâng cấp thì đổi `ref`, chạy `scripts/vendor.py`, chạy lại `vnc doctor`.

## Cấu trúc

- `vncontent/stages.py`: 5 bước (`ingest dub render meta publish`) + hàm thuần có test (`video_filter`, `sau_command`, `build_meta`, `pick_sub`).
- `vncontent/story.py`: chế độ truyện audio (job `kind = story`), cùng 5 tên bước; `stages.py` chuyển sang đây. `srt.py`: SRT,
  điểm quảng cáo giữa video, đoạn cắt cliffhanger. `presets/genres/*.toml`: thể loại.
- `vncontent/pipeline.py`: chạy/tiếp tục/chạy lại job, ghi trạng thái. `store.py`: SQLite. `runner.py`: mọi lệnh ngoài đi qua đây.
- Test: `.venv\Scripts\python -m pytest -q` (cần ffmpeg+ffprobe cho `test_integration.py`).

## Bài học đã gặp

- Phụ đề ffmpeg `subtitles=`: SRT→ASS dùng lưới 288 px cao nên `FontSize`/`MarginV` phải quy đổi (`video_filter`); `original_size` làm chữ biến mất.
- `subtitles=` nhận tên file tương đối và chạy ở thư mục render (tránh escape `C\:` của đường dẫn Windows).
- vndub ghi `cast.json` cạnh phụ đề vào → pipeline chép phụ đề vào thư mục job trước khi gọi vndub.
- Truyện audio: không tự sửa chữ trong truyện của người dùng (chỉ báo cụm nhạy cảm ở `safety.txt`); không dựng nhiều tài khoản/
  proxy xoay IP để né nền tảng; thiếu ảnh nền thì cảnh báo chứ đừng im lặng; hook/tóm tắt do người dùng hoặc agent viết, không gọi LLM từ pipeline.
- zoompan: nạp MỘT khung ảnh vào (không `-loop 1`), `d=<số khung>` ra; phóng ảnh 2× trước để không giật.
- `sau` cần `conf.py` (sao chép từ `conf.example.py`), chạy với cwd = `vendor/social-auto-upload`.
