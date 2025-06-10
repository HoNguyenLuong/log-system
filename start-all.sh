#!/bin/bash

set -e  # Dừng nếu có lỗi

modules=("ai-core" "data-ingestion" "analytics-dashboard" "database" "stream-processor")

for module in "${modules[@]}"; do
  echo "🟢 Starting module: $module"
  (cd "$module" && docker-compose up -d)
done

echo "✅ All services started"
