# Hệ thống Giám sát Log và Metric với AI Agent

## 🧱 Mô tả hệ thống

Hệ thống này được thiết kế để giám sát log và metric từ các máy chủ nội bộ (Ubuntu / CentOS / Rocky Linux), xử lý cảnh báo theo thời gian thực, enrich thông tin phục vụ AI Agent và hiển thị báo cáo phân tích trên dashboard BI.

---

## ⚙️ Công nghệ sử dụng

| Chức năng          | Công nghệ sử dụng                                            |
| ------------------ | ------------------------------------------------------------ |
| Thu thập Log       | Filebeat / Logstash / Fluentbit                              |
| Thu thập Metric    | Prometheus + Node Exporter                                   |
| Cảnh báo           | Alertmanager                                                 |
| Message Queue      | Kafka                                                        |
| Streaming          | Apache Flink                                                 |
| Batch Processing   | Apache Spark                                                 |
| Truy vấn & lưu trữ | Elasticsearch, PostgreSQL                                    |
| Dashboard          | Apache Superset                                              |
| Phân loại cảnh báo | LLM (Large Language Model) thông qua AI Agent (MCP Protocol) |

---

## 🔄 Luồng xử lý

### 1. **Luồng Metric**

```
Node Exporter
    ↓
Prometheus
    ↓
Alertmanager (Khi CPU/RAM/Disk > 90%)
    ↓
Kafka Topic: `alert`
    ↓
Flink (Streaming Enrichment)
    ↓
Kafka Topic: `alert-context`
    ↓
AI Agent (LLM phân loại, lưu vào DB - bảng `alert_history`)
    ↓
Superset Dashboard
```

### 2. **Luồng Log**

```
Filebeat
    ↓
Logstash
    ↓
Elasticsearch
    ↓
AI Agent (đọc log qua MCP protocol)
    ↓
Database (tổng hợp thông tin alert/log)
    ↓
Superset Dashboard
```

---

## 📅 Chi tiết xử lý cảnh báo

* Khi có log cấp `ERROR` / `WARN` **hoặc** các metric vượt ngưỡng (90%) → gửi cảnh báo vào Kafka topic `alert` với:

  * IP server
  * Nội dung cảnh báo
  * Loại lỗi hoặc loại metric vi phạm

* **Flink** thực hiện xử lý real-time:

  * Enrich thêm thông tin:

    * IP, OS, version OS
    * Người quản lý, email quản lý
    * Loại cảnh báo
    * Dữ liệu metric tại các thời điểm: T-10p, T-5p, T-2p, T-1p, T-5s
  * Gửi dữ liệu enrich vào Kafka topic `alert-context`
  * Gửi tới AI Agent để:

    * Phân loại cảnh báo (LLM)
    * Lưu thông tin vào bảng `alert_history`

---

## 📂 Batch Job

Apache Spark định kỳ xử lý bảng `alert_history` để tạo:

* Báo cáo theo IP server
* Thống kê loại lỗi
* Phân bổ lỗi theo thời gian
* Tỷ lệ cảnh báo đã xử lý / chưa xử lý

---

## 📊 Dashboard (Superset)

Dashboard hiển thị:

* Biểu đồ thời gian cảnh báo
* Heatmap lỗi theo giờ/ngày
* Bộ lọc theo IP, loại lỗi, trạng thái xử lý

---

## 🌐 Phạm vi hoạt động

* Hệ thống hoạt động trong mạng LAN
* Thu thập log & metric từ các VM hoặc server cục bộ
* Tối ưu cho hệ thống giám sát nội bộ, có thể mở rộng ra môi trường cloud nếu cần

---

## 📁 Cấu trúc thư mục hệ thống

```
Log_system/
├── ai-core/                 # AI Agent xử lý và phân loại cảnh báo
├── analytics-dashboard/     # Superset hoặc các công cụ BI khác
├── data-ingestion/          # Thành phần thu thập dữ liệu
│   ├── alertmanager/
│   ├── elastic/
│   ├── kafka/
│   ├── logstash/
│   ├── prometheus/
│   ├── __init__.py
│   └── docker-compose.yml
├── share-config/            # Các file config dùng chung
├── stream-processor/        # Flink xử lý streaming
├── requirement.txt          # Thư viện Python nếu có
└── start-all.sh             # Script khởi chạy hệ thống
```

---

## 📌 Ghi chú

* Các thành phần đều container hóa (Docker/Kubernetes khuyến khích)
* AI Agent giao tiếp qua giao thức MCP để phân loại & xử lý cảnh báo
* Các thông số cảnh báo và truy vấn đều có thể tuỳ chỉnh theo yêu cầu tổ chức
