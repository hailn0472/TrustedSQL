import requests
import json
import csv
import time
import uuid
import sys
import os

# --- CẤU HÌNH ---
# Địa chỉ API của bạn (đảm bảo backend đang chạy)
API_URL = "http://127.0.0.1:5000/api/chat"
INPUT_FILE = "E:/Tai_lieu_hoc/1.TexttoSQL/chatbot-rag-t2sql/auto-test/questions.txt"
OUTPUT_FILE = "E:/Tai_lieu_hoc/1.TexttoSQL/chatbot-rag-t2sql/auto-test/report_ket_qua.csv"

def ask_chatbot(question, thread_id):
    """
    Hàm gửi câu hỏi lên API và nhận phản hồi dạng Stream (SSE)
    """
    headers = {'Content-Type': 'application/json'}
    payload = {
        "message": question,
        "thread_id": thread_id
    }

    full_answer = ""
    process_steps = [] # Lưu lại các bước bot đã đi qua (ví dụ: Searching Documents...)
    
    start_time = time.time()
    
    try:
        # Gọi API với stream=True để bắt từng event
        response = requests.post(API_URL, json=payload, headers=headers, stream=True)
        
        if response.status_code != 200:
            return f"Error {response.status_code}", [], 0

        # Đọc từng dòng dữ liệu trả về
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                
                # Xử lý sự kiện LOG (Quá trình suy nghĩ/xử lý)
                if decoded_line.startswith("event: log"):
                    # Dòng tiếp theo sẽ là data
                    continue
                
                # Xử lý sự kiện MESSAGE (Câu trả lời)
                if decoded_line.startswith("event: message"):
                    continue

                # Xử lý dữ liệu (dòng bắt đầu bằng data:)
                if decoded_line.startswith("data:"):
                    json_str = decoded_line.replace("data: ", "")
                    try:
                        data = json.loads(json_str)
                        
                        # Trường hợp 1: Log các bước xử lý
                        if "step" in data:
                            step_name = data.get("step")
                            # Chỉ lấy các bước quan trọng
                            if step_name in ["call_rag_agent", "call_sql_agent", "generate_response"]:
                                process_steps.append(step_name)
                        
                        # Trường hợp 2: Token câu trả lời
                        if "token" in data:
                            full_answer += data["token"]
                            
                        # Trường hợp 3: Báo lỗi
                        if "error" in data:
                            full_answer = f"LỖI TỪ BOT: {data['error']}"

                    except json.JSONDecodeError:
                        pass

    except Exception as e:
        return f"Lỗi kết nối: {str(e)}", [], 0

    duration = round(time.time() - start_time, 2)
    return full_answer.strip(), process_steps, duration
print("--- BẮT ĐẦU CHẠY AUTO TEST ---")

# Kiểm tra file câu hỏi
if not os.path.exists(INPUT_FILE):
    print(f"Lỗi: Không tìm thấy file {INPUT_FILE}")
# Mở file ghi kết quả (CSV)
with open(OUTPUT_FILE, mode='w', newline='', encoding='utf-8-sig') as csv_file:
    fieldnames = ['STT', 'Câu hỏi', 'Câu trả lời của Bot', 'Quy trình xử lý', 'Thời gian (s)']
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    writer.writeheader()

    # Đọc danh sách câu hỏi
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        questions = [line.strip() for line in f if line.strip()]

    total = len(questions)
    print(f"Đã tìm thấy {total} câu hỏi. Đang xử lý...\n")

    for idx, question in enumerate(questions, 1):
        # Tạo thread_id ngẫu nhiên cho mỗi câu hỏi để không bị lẫn context cũ
        thread_id = str(uuid.uuid4())
        
        print(f"[{idx}/{total}] Đang hỏi: {question} ...", end="\r")
        
        # Gửi câu hỏi
        answer, steps, duration = ask_chatbot(question, thread_id)
        
        # Format lại quy trình cho đẹp
        steps_str = " -> ".join(steps) if steps else "Direct/General"

        # Ghi vào file CSV
        writer.writerow({
            'STT': idx,
            'Câu hỏi': question,
            'Câu trả lời của Bot': answer,
            'Quy trình xử lý': steps_str,
            'Thời gian (s)': duration
        })
        
        print(f"[{idx}/{total}] Xong! ({duration}s) - {steps_str}")

print(f"\n--- HOÀN THÀNH! ---")
print(f"Kết quả đã được lưu tại: {OUTPUT_FILE}")
