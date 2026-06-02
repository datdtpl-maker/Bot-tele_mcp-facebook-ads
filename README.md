# Meta Ads MCP + Telegram Bot

Project này tạo một MCP server nội bộ để quản lý Meta/Facebook Ads qua Meta Marketing API, rồi cho Telegram bot gọi các MCP tools đó bằng lệnh chat.

## Trạng thái chức năng

Đã có trong code:

- **Thiết lập mục tiêu và tạo chiến dịch nhanh**: Hỗ trợ mục tiêu chiến dịch tin nhắn (messages), chuyển đổi (conversions), leads, và lưu lượng truy cập (traffic) bằng lệnh tạo theo goal.
- **Tích hợp nút bấm (Inline Keyboards)**: Hỗ trợ nút bấm bật/tắt (🟢 Kích hoạt / 🔴 Tạm dừng) trực quan khi xem trạng thái chiến dịch, cùng nút bấm **[ 🚀 Tạo thật (LIVE) ]** hoặc **[ 🚀 Xác nhận đổi ngân sách ]** trực tiếp dưới tin nhắn chat.
- **Tự động dịch Targeting bằng AI**: Sử dụng OpenAI trực tiếp phân tích các tin nhắn tự nhiên để lấy độ tuổi (`age_min`/`age_max`), giới tính, và địa điểm (Hà Nội, Hồ Chí Minh, Đà Nẵng,...) thay vì dùng cấu hình cứng.
- **Cập nhật ngân sách (Update Budget)**: Thay đổi ngân sách chiến dịch hoặc nhóm nhanh chóng bằng lệnh chat tự nhiên hoặc nút bấm xác nhận.
- **Định dạng hiển thị trực quan (UX)**: Xem danh sách tài khoản, chiến dịch, nhóm quảng cáo, quảng cáo và trạng thái chi tiết theo mẫu có sẵn được căn chỉnh đẹp mắt kèm emoji trạng thái thay vì hiển thị JSON thô.
- **Hỗ trợ link rút gọn và video**: Tự động nhận dạng và phân tích chuyển hướng cho link `fb.watch`, link Reels, Watch, link di động, link permalink phức tạp.
- **Tránh chặn nghẽn (Async Thread Pool)**: Đọc/ghi cơ sở dữ liệu Supabase và ghi log Google Sheets trong Thread Pool bất đồng bộ giúp Telegram bot không bị nghẽn Event Loop.
- **Chế độ test an toàn (`SAFE_MODE=true`)**: Bot chỉ preview/dry-run, không thay đổi thực tế trên Meta Ads.
- **Tạo chiến dịch thật (`SAFE_MODE=false`)**: Tạo campaign ở trạng thái `PAUSED` để đảm bảo an toàn vận hành, quản trị viên cần kiểm tra Ads Manager trước khi kích hoạt.
- **Phân tích hiệu suất**: So sánh hiệu quả chiến dịch (winner/loser) dựa trên các mục tiêu chuyển đổi, chi phí, ROAS và lượt tin nhắn.
- **Ghi log & Lưu trữ**: Tự động lưu audit log vào Google Sheets và lưu vết ngữ cảnh phiên chat của từng Admin vào Supabase.

Đã có flow full funnel an toàn: bot preview hoặc tạo `campaign + ad set + creative + ad` ở trạng thái `PAUSED`. Người quản trị vẫn cần kiểm tra Ads Manager trước khi bật chạy thật, nhất là targeting, ngân sách, pixel/conversion event và creative policy.

## Kiến trúc

```mermaid
flowchart LR
  U["Người dùng Telegram"] --> B["telegram_bot.py"]
  B --> C["MCP client stdio"]
  C --> S["mcp_server.py"]
  S --> M["Meta Marketing API"]
  B --> G["Google Sheets audit log"]
  B --> DB["Supabase session context"]
```

## Cài đặt

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Sửa `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=token_tu_BotFather
TELEGRAM_ALLOWED_USER_IDS=telegram_user_id_cua_ban

META_ACCESS_TOKEN=token_meta
META_GRAPH_API_VERSION=v22.0
DEFAULT_AD_ACCOUNT_ID=act_1234567890
BOT_TIMEZONE=Asia/Bangkok

OPENAI_API_KEY=sk-replace_me
OPENAI_MODEL=gpt-5.4-mini

SAFE_MODE=true
```

Lấy Telegram user id:

```powershell
Invoke-RestMethod "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates"
```

## Chạy bot

```powershell
python telegram_bot.py
```

Bot dùng long polling, phù hợp chạy trên máy cá nhân hoặc VPS nhỏ.

## Lệnh Telegram

Bot hỗ trợ 2 kiểu thao tác:

- Nhắn tự nhiên như đang trao đổi với trợ lý.
- Dùng lệnh slash cố định như `/campaigns`, `/analyze`, `/activate`.

Nếu có `OPENAI_API_KEY`, bot dùng OpenAI Structured Outputs để hiểu ý định tiếng Việt linh hoạt hơn, ví dụ:

```text
Camp nào hôm nay đốt tiền mà không ra đơn?
Tắt giúp tôi camp lỗ nhất 7 ngày qua
So sánh ngân sách mấy camp đang chạy
Tạo camp inbox từ bài này, ngân sách 100000, chạy cho page này
```

OpenAI chỉ dùng để phân tích câu nói thành intent/tham số JSON. Bot vẫn tự kiểm quyền, safe mode, xác nhận và gọi MCP tool nội bộ để thao tác Meta Ads.

### Nhắn tự nhiên

Các câu mẫu:

```text
Hôm nay có bao nhiêu chiến dịch đang chạy?
So sánh camp nào tốt nhất 7 ngày qua
So sánh chiến dịch tin nhắn hôm nay
So sánh ngân sách chiến dịch
Trạng thái chiến dịch "Tên Camp"
Bật chiến dịch "Tên Camp"
Tắt chiến dịch "Tên Camp"
Tắt campaign 120000000000000
```

Khi hỏi chiến dịch đang chạy, bật/tắt hoặc xem trạng thái, bot sẽ trả ra tên campaign và ID để người dùng kiểm tra lại trong Ads Manager.

Khi `SAFE_MODE=true`, câu "Bật chiến dịch..." chỉ trả về dry-run và hướng dẫn dùng `/activate campaign_id CONFIRM` sau khi đã đổi `SAFE_MODE=false`.

### Tạo chiến dịch từ tin nhắn Telegram

Cú pháp nhắn tự nhiên:

```text
Tạo chiến dịch tin nhắn tên "Camp inbox A" act_123456789 page https://facebook.com/tenpage bài viết https://facebook.com/tenpage/posts/123
Tạo chiến dịch chuyển đổi tên "Camp sale A" act_123456789 page https://facebook.com/tenpage bài viết https://facebook.com/tenpage/posts/123
```

Bot sẽ dựng full funnel ở dạng bản nháp:

- Campaign `PAUSED`.
- Ad set `PAUSED`.
- Creative dùng bài viết Page qua `object_story_id`.
- Ad `PAUSED`.

Điều kiện bắt buộc:

- Tin nhắn phải có `act_...`.
- Phải có tên campaign trong dấu ngoặc kép sau chữ `tên`.
- Phải có link Page và link bài viết.
- Token Meta đang cấu hình phải truy cập được Page đó, và Page phải nằm trong danh sách `promote_pages` của tài khoản quảng cáo. Nếu tài khoản quảng cáo không có quyền quảng bá Page, bot sẽ báo lỗi và không tạo bản nháp.
- Mặc định bot chỉ tạo preview/dry-run, chưa gửi mutation lên Meta và chưa tiêu tiền.

Để tạo thật full funnel:

- `.env` phải có `SAFE_MODE=false`.
- Tin nhắn phải nói rõ muốn chạy/tạo live, ví dụ có chữ `live`, `chạy thật`, hoặc `CONFIRM_LIVE`.
- Bot vẫn tạo mọi object ở trạng thái `PAUSED`; sau đó người quản trị kiểm tra Ads Manager rồi mới bật campaign.

Cấu hình mặc định cho full funnel:

```dotenv
DEFAULT_DAILY_BUDGET=100000
DEFAULT_TARGETING={"geo_locations":{"countries":["VN"]},"age_min":18,"age_max":55}
DEFAULT_PIXEL_ID=
DEFAULT_CONVERSION_EVENT=PURCHASE
```

Với campaign chuyển đổi, bắt buộc có `DEFAULT_PIXEL_ID`.

### Lệnh slash

Xem dữ liệu:

```text
/start
/accounts
/campaigns act_123456789
/adsets campaign_id
/ads campaign_or_adset_id
/status campaign campaign_id
/status adset adset_id
/status ad ad_id
```

Xem và phân tích kết quả quảng cáo:

```text
/insights act_123456789 2026-05-01 2026-05-28 campaign
/insights act_123456789 2026-05-01 2026-05-28 adset
/insights act_123456789 2026-05-01 2026-05-28 ad

/analyze act_123456789 2026-05-01 2026-05-28 conversions campaign
/analyze act_123456789 2026-05-01 2026-05-28 messages adset
/compare act_123456789 2026-05-01 2026-05-28 leads ad
```

Nếu đã đặt `DEFAULT_AD_ACCOUNT_ID`, có thể bỏ `act_...`:

```text
/campaigns
/insights 2026-05-01 2026-05-28 campaign
/analyze 2026-05-01 2026-05-28 conversions campaign
```

Tạo chiến dịch test/dry-run:

```text
/draft_campaign act_123456789 messages "Camp tin nhan test"
/draft_campaign act_123456789 conversions "Camp chuyen doi test"
```

Tạo campaign thật:

```text
/create_campaign act_123456789 messages "Camp tin nhan live" CONFIRM_LIVE
/create_campaign act_123456789 conversions "Camp chuyen doi live" CONFIRM_LIVE
```

Điều kiện để tạo thật:

- `.env` phải có `SAFE_MODE=false`.
- Lệnh phải có `CONFIRM_LIVE`.
- Campaign vẫn được tạo ở trạng thái `PAUSED`; muốn chạy phải dùng `/activate`.

Bật/tắt campaign:

```text
/pause campaign_id CONFIRM
/activate campaign_id CONFIRM
```

Khi `SAFE_MODE=true`, lệnh `/activate` chỉ trả dry-run và không bật thật.

## Google Sheets audit log

Tạo Google service account, cấp quyền edit vào spreadsheet, rồi cấu hình:

```dotenv
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SERVICE_ACCOUNT_FILE=C:\secure\service-account.json
GOOGLE_SHEET_ID=spreadsheet_id
GOOGLE_SHEET_TAB=bot_logs
```

Sheet nên có 6 cột:

```text
timestamp | user_id | chat_id | command | payload | result
```

Mỗi lệnh bot sẽ append một dòng log.

## Supabase session context

Tạo table:

```sql
create table if not exists bot_sessions (
  user_id text primary key,
  chat_id text,
  context jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
```

Cấu hình:

```dotenv
SUPABASE_ENABLED=true
SUPABASE_URL=https://project-ref.supabase.co
SUPABASE_SERVICE_ROLE_KEY=service_role_key
SUPABASE_SESSION_TABLE=bot_sessions
```

Bot lưu `last_command`, `last_payload`, `last_result` theo từng Telegram user.

## Logic winner/loser

File `analytics.py` chuẩn hóa insights thành các metric:

- Spend, impressions, clicks, CTR, CPC, CPM.
- Messages, leads, purchases.
- Revenue, ROAS.
- Cost per message, cost per lead, cost per purchase.

Scoring:

- `messages`: ưu tiên nhiều conversation trên mỗi đồng spend.
- `leads`: ưu tiên nhiều lead trên mỗi đồng spend.
- `conversions`: ưu tiên ROAS, purchase volume, CPC thấp.

Kết quả trả về gồm `summary`, `best`, `winners`, `losers`.

## Yêu cầu quyền Meta

Bạn cần Meta Developer App có Marketing API và access token hợp lệ:

- `ads_read` để đọc account/campaign/insights.
- `ads_management` để tạo/sửa campaign.
- Token phải có quyền trên ad account.
- Nếu dùng cho business/người dùng khác, app/token cần qua quy trình review của Meta.

Tham khảo: [Meta Marketing API Postman collection](https://www.postman.com/meta/facebook-marketing-api/overview), [Meta campaign structure](https://developers.facebook.com/docs/marketing-api/campaign-structure), [Telegram Bot API](https://core.telegram.org/bots/api/).

## An toàn vận hành

- Giữ `SAFE_MODE=true` khi demo hoặc test.
- Không commit `.env`, service account JSON hoặc Supabase service role key.
- Chỉ thêm Telegram user id đáng tin vào `TELEGRAM_ALLOWED_USER_IDS`.
- Chạy live theo 2 bước: tạo campaign `PAUSED`, kiểm tra trong Ads Manager, rồi mới `/activate`.
- Nên thêm duyệt 2 người trước khi cho bot thay đổi ngân sách hoặc bật campaign live.

## Tác giả

Người thực hiện: **Danny DT (Thành Đạt)**
