import streamlit as st
import time
import threading
import queue
import re
import io
import zipfile
from google import genai
from google.genai import types

# ==========================================
# CẤU HÌNH GIAO DIỆN
# ==========================================
st.set_page_config(page_title="Máy Dịch Đam Mỹ", page_icon="🏳️‍🌈", layout="wide")
st.title("🏳️‍🌈 Máy Dịch Đam Mỹ Siêu Tốc (Có Tách Chương)")

# ==========================================
# CÂU LỆNH YÊU CẦU AI DỊCH
# ==========================================
DANMEI_PROMPT = """Bạn là một dịch giả chuyên nghiệp, chuyên dịch tiểu thuyết đam mỹ Trung Quốc sang tiếng Việt. Tôi sẽ cung cấp cho bạn một bộ truyện bằng tiếng Trung Quốc từ một truyện đam mỹ, bao gồm tựa đề, nội dung và thông tin chương. Nhiệm vụ của bạn là dịch các chương truyện này sang tiếng Việt, tuân thủ các nguyên tắc sau:

Giữ nguyên phong cách văn học mạng: Sử dụng ngôn ngữ và giọng văn phù hợp với thể loại đam mỹ,... các xưng hô cho phù hợp với thiết lập nhân vật và nhân xưng của tiếng việt (Anh-em; anh-tôi;tôi-cậu. Giữ nguyên xưng hô xuyên suốt đoạn dịch không tự ý thay đổi khi đã xuất bản dịch.

Ngữ pháp chuẩn xác: Đảm bảo bản dịch tuân thủ ngữ pháp tiếng Việt, dễ đọc, dễ hiểu.

Nếu 1 số câu của tác giả quá khô cứng và tối nghĩa nếu dịch theo Hán Việt, thì hãy chuyển qua văn phong thuần Việt sao cho dễ hiểu.

Xử lý danh từ riêng: Giữ nguyên tất cả các danh từ riêng như tên người, địa điểm, môn phái, võ công,... ở dạng Trung Quốc gốc.
Trừ các danh từ riêng TÊN NHÂN VẬT, ĐỊA DANH, ĐỊA ĐIỂM, phải dịch thuần việt, TUYỆT ĐỐI KHÔNG LẠM DỤNG HÁN VIỆT.

Dịch : (bản dịch đúng văn phong tác giả).

Hãy sau mỗi đoạn xuống dòng giữa các câu cho dễ đọc.
Dịch hết đoạn tôi đã gửi. Tuyệt đối không được dừng giữa chừng và tự ý thêm tình tiết truyện. Hết văn bản tôi gửi là phải lập tức dừng lại.

Lưu ý: Chỉ output kết quả tiếng Việt, không cần lặp lại các hướng dẫn và ví dụ. Hãy sẵn sàng nhận nhiệm vụ!"""

# ==========================================
# 1. CÁC HÀM TÁCH CHƯƠNG THÔNG MINH
# ==========================================
def split_by_chapter_title(text):
    """Tách truyện dựa vào tiêu đề (Chương 1, 第一章...)"""
    # Công thức nhận diện tiêu đề chương tiếng Trung/Việt/Anh
    regex = r'(?:^|\n)\s*(?:第[\d一二三四五六七八九十百千万零]+[章回节集卷部]|Chapter\s*\d+|Chương\s*\d+)[^\n]*'
    matches = list(re.finditer(regex, text, re.IGNORECASE))
    
    if not matches:
        return [] # Nếu không tìm thấy chương nào
        
    chapters = []
    for i in range(len(matches)):
        start = matches[i].start()
        end = matches[i+1].start() if i + 1 < len(matches) else len(text)
        
        title = matches[i].group(0).strip()
        # Lấy nội dung và nhét thêm title lên đầu cho AI hiểu bối cảnh
        content = text[start:end].strip()
        chapters.append({"title": title, "content": content})
        
    return chapters

def split_by_word_count(text, max_words=2000):
    """Tách truyện cắt đều theo số chữ"""
    paragraphs = text.split('\n')
    chapters = []
    current_content = ""
    chap_idx = 1
    
    for p in paragraphs:
        if len(current_content) + len(p) > max_words:
            chapters.append({"title": f"Phần {chap_idx}", "content": current_content.strip()})
            current_content = p + "\n"
            chap_idx += 1
        else:
            current_content += p + "\n"
            
    if current_content.strip():
        chapters.append({"title": f"Phần {chap_idx}", "content": current_content.strip()})
        
    return chapters

# ==========================================
# 2. HÀM CÔNG NHÂN DỊCH (HÀNG ĐỢI)
# ==========================================
def worker_translator(api_key, task_queue, results_dict):
    try:
        client = genai.Client(api_key=api_key.strip())
        safety_settings = [
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        ]
        config = types.GenerateContentConfig(
            system_instruction=DANMEI_PROMPT,
            safety_settings=safety_settings,
            temperature=0.3
        )
    except Exception: return

    while not task_queue.empty():
        try:
            idx, chapter_data = task_queue.get_nowait()
        except queue.Empty: break

        success = False
        retries = 3 
        
        while retries > 0 and not success:
            try:
                # Gửi cho AI nội dung cần dịch
                response = client.models.generate_content(
                    model="gemini-2.5-flash", 
                    contents=chapter_data["content"], 
                    config=config
                )
                # Lưu kết quả kèm theo tiêu đề chương
                results_dict[idx] = {"title": chapter_data["title"], "translated": response.text}
                success = True
            except Exception as e:
                err_msg = str(e).lower()
                if "429" in err_msg or "503" in err_msg or "quota" in err_msg:
                    time.sleep(3)
                    retries -= 1
                else: break
        
        if not success:
            results_dict[idx] = {"title": chapter_data["title"], "translated": f"\n\n[❌ LỖI Ở CHƯƠNG NÀY: API Key bị chặn hoặc lỗi. Vui lòng dịch lại.]\n\n"}
        
        task_queue.task_done()

# ==========================================
# 3. GIAO DIỆN CHÍNH
# ==========================================
col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("🔑 1. API Keys & Cấu hình")
    keys_input = st.text_area("Nhập API Keys (Mỗi dòng 1 key):", height=150)
    
    st.markdown("---")
    st.subheader("✂️ 2. Phương pháp chia chương")
    split_method = st.radio("Chọn cách chia:", ["Tự động nhận diện Chương (第一章...)", "Cắt đều theo số chữ"])
    if split_method == "Cắt đều theo số chữ":
        word_count = st.number_input("Số chữ mỗi phần:", value=2000, step=500)

with col2:
    st.subheader("📥 3. Nguồn Truyện Raw")
    
    # Tính năng Upload file .txt (Thay thế cho HTML Upload)
    uploaded_file = st.file_uploader("Tải lên file truyện (.txt)", type=['txt'])
    
    # Khung dán text
    raw_text = st.text_area("Hoặc dán trực tiếp truyện raw vào đây:", height=200)

st.markdown("---")

if st.button("🚀 BẮT ĐẦU TÁCH CHƯƠNG & DỊCH", type="primary", use_container_width=True):
    api_keys = [k.strip() for k in keys_input.split('\n') if k.strip()]
    
    # Ưu tiên lấy text từ file tải lên, nếu không có thì lấy text ở khung dán
    final_raw_text = ""
    if uploaded_file is not None:
        final_raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore")
    elif raw_text.strip():
        final_raw_text = raw_text.strip()
        
    if not api_keys:
        st.error("❌ Vui lòng nhập ít nhất 1 API Key!")
    elif not final_raw_text:
        st.error("❌ Vui lòng tải file .txt lên hoặc dán nội dung vào!")
    else:
        # BƯỚC 1: TÁCH CHƯƠNG
        if split_method == "Tự động nhận diện Chương (第一章...)":
            chunks = split_by_chapter_title(final_raw_text)
            if not chunks:
                st.warning("⚠️ Không tìm thấy dấu hiệu Chương nào! Hệ thống tự động chuyển sang cắt theo số chữ (2000 chữ/phần).")
                chunks = split_by_word_count(final_raw_text, 2000)
        else:
            chunks = split_by_word_count(final_raw_text, word_count)
            
        total_chunks = len(chunks)
        st.info(f"✅ Đã tách truyện thành **{total_chunks} phần/chương**. Bắt đầu huy động {len(api_keys)} API Key để dịch song song...")
        
        # BƯỚC 2: TIẾN HÀNH DỊCH
        task_queue = queue.Queue()
        for i, chunk in enumerate(chunks):
            task_queue.put((i, chunk))
            
        results_dict = {}
        
        threads = []
        for key in api_keys:
            t = threading.Thread(target=worker_translator, args=(key, task_queue, results_dict))
            t.start()
            threads.append(t)
            
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        while len(results_dict) < total_chunks:
            done_count = len(results_dict)
            progress_bar.progress(done_count / total_chunks)
            status_text.markdown(f"**⏳ Đang dịch... Hoàn thành {done_count}/{total_chunks} chương.**")
            time.sleep(1) 
            
        for t in threads:
            t.join()
            
        progress_bar.progress(1.0)
        status_text.success("🎉 ĐÃ DỊCH XONG TOÀN BỘ!")
        
        # BƯỚC 3: GOM KẾT QUẢ VÀ TẠO FILE TẢI VỀ
        ordered_results = []
        for i in range(total_chunks):
            ordered_results.append(results_dict.get(i))
            
        # 3.1: File TXT gộp chung
        combined_text = ""
        for item in ordered_results:
            if item:
                combined_text += f"{item['title']}\n\n{item['translated']}\n\n{'='*40}\n\n"
                
        # 3.2: File ZIP chứa từng file lẻ (Thay thế cho JSZip HTML)
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for i, item in enumerate(ordered_results):
                if item:
                    # Lọc ký tự đặc biệt để làm tên file
                    safe_title = re.sub(r'[\\/*?:"<>|]', "", item["title"]).strip()
                    file_name = f"Chuong_{i+1:03d}_{safe_title}.txt"
                    content = f"{item['title']}\n\n{item['translated']}"
                    zip_file.writestr(file_name, content)
        
        # Hiển thị nút tải
        st.subheader("💾 LƯU BẢN DỊCH VỀ MÁY")
        col_dl1, col_dl2 = st.columns(2)
        
        with col_dl1:
            st.download_button(
                label="📄 TẢI 1 FILE .TXT GỘP",
                data=combined_text.encode('utf-8'),
                file_name="Truyen_Dam_My_Gop.txt",
                mime="text/plain",
                use_container_width=True
            )
            
        with col_dl2:
            st.download_button(
                label="📦 TẢI FILE .ZIP (Gồm các chương lẻ)",
                data=zip_buffer.getvalue(),
                file_name="Truyen_Dam_My_Cac_Chuong.zip",
                mime="application/zip",
                use_container_width=True
            )
            
        st.markdown("---")
        st.text_area("Xem trước bản dịch tại đây:", value=combined_text, height=400)
