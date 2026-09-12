import streamlit as st
import time
import threading
import queue
import re
import io
import zipfile
import random
from google import genai
from google.genai import types

# ==========================================
# CẤU HÌNH GIAO DIỆN & BỘ NHỚ
# ==========================================
st.set_page_config(page_title="Máy Dịch Đam Mỹ", page_icon="🏳️‍🌈", layout="wide")
st.title("🏳️‍🌈 Máy Dịch Đam Mỹ Siêu Tốc (Quản Lý Từng Chương)")

# Khởi tạo bộ nhớ để không bị mất dữ liệu khi bấm nút
if "chunks" not in st.session_state:
    st.session_state.chunks = []
if "results" not in st.session_state:
    st.session_state.results = {}
if "api_keys" not in st.session_state:
    st.session_state.api_keys = []

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
# CÁC HÀM TÁCH CHƯƠNG & DỊCH
# ==========================================
def split_by_chapter_title(text):
    regex = r'(?:^|\n)\s*(?:第[\d一二三四五六七八九十百千万零]+[章回节集卷部]|Chapter\s*\d+|Chương\s*\d+)[^\n]*'
    matches = list(re.finditer(regex, text, re.IGNORECASE))
    if not matches: return []
    chapters = []
    for i in range(len(matches)):
        start = matches[i].start()
        end = matches[i+1].start() if i + 1 < len(matches) else len(text)
        title = matches[i].group(0).strip()
        content = text[start:end].strip()
        chapters.append({"title": title, "content": content})
    return chapters

def split_by_word_count(text, max_words=2000):
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

def worker_translator(api_key, task_queue, results_dict):
    try:
        client = genai.Client(api_key=api_key.strip())
        safety_settings = [
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        ]
        config = types.GenerateContentConfig(system_instruction=DANMEI_PROMPT, safety_settings=safety_settings, temperature=0.3)
    except Exception: return

    while not task_queue.empty():
        try:
            idx, chapter_data = task_queue.get_nowait()
        except queue.Empty: break

        success = False
        retries = 3 
        while retries > 0 and not success:
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash", contents=chapter_data["content"], config=config
                )
                results_dict[idx] = {"title": chapter_data["title"], "translated": response.text}
                success = True
            except Exception as e:
                err_msg = str(e).lower()
                if "429" in err_msg or "503" in err_msg or "quota" in err_msg:
                    time.sleep(3)
                    retries -= 1
                else: break
        
        if not success:
            results_dict[idx] = {"title": chapter_data["title"], "translated": "❌ LỖI: API Key bị chặn hoặc lỗi. Vui lòng bấm nút 'Dịch lại' ở dưới."}
        task_queue.task_done()

def retry_single_chapter(idx):
    if not st.session_state.api_keys:
        return
    # Lấy ngẫu nhiên 1 key trong danh sách để thử lại
    key = random.choice(st.session_state.api_keys)
    chunk = st.session_state.chunks[idx]
    
    try:
        client = genai.Client(api_key=key.strip())
        safety_settings = [
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        ]
        config = types.GenerateContentConfig(system_instruction=DANMEI_PROMPT, safety_settings=safety_settings, temperature=0.3)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=chunk["content"], config=config)
        
        st.session_state.results[idx] = {"title": chunk["title"], "translated": response.text}
    except Exception as e:
        st.session_state.results[idx] = {"title": chunk["title"], "translated": f"❌ Vẫn bị lỗi: {e}"}

# ==========================================
# KHU VỰC NHẬP LIỆU
# ==========================================
col1, col2 = st.columns([1, 2])
with col1:
    st.subheader("🔑 1. Cấu hình")
    keys_input = st.text_area("Nhập API Keys (Mỗi dòng 1 key):", height=150)
    split_method = st.radio("Chọn cách chia:", ["Tự động nhận diện Chương (第一章...)", "Cắt đều theo số chữ"])
    word_count = 2000
    if split_method == "Cắt đều theo số chữ":
        word_count = st.number_input("Số chữ mỗi phần:", value=2000, step=500)

with col2:
    st.subheader("📥 2. Nguồn Truyện")
    uploaded_file = st.file_uploader("Tải file truyện (.txt)", type=['txt'])
    raw_text = st.text_area("Hoặc dán truyện vào đây:", height=200)

st.markdown("---")

# ==========================================
# NÚT BẮT ĐẦU DỊCH ĐỒNG LOẠT
# ==========================================
if st.button("🚀 BẮT ĐẦU TÁCH CHƯƠNG & DỊCH (Xóa dữ liệu cũ)", type="primary", use_container_width=True):
    api_keys = [k.strip() for k in keys_input.split('\n') if k.strip()]
    final_raw_text = ""
    if uploaded_file is not None:
        final_raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore")
    elif raw_text.strip():
        final_raw_text = raw_text.strip()
        
    if not api_keys:
        st.error("❌ Vui lòng nhập API Key!")
    elif not final_raw_text:
        st.error("❌ Vui lòng tải file hoặc dán nội dung!")
    else:
        st.session_state.api_keys = api_keys
        
        if split_method == "Tự động nhận diện Chương (第一章...)":
            chunks = split_by_chapter_title(final_raw_text)
            if not chunks:
                chunks = split_by_word_count(final_raw_text, 2000)
        else:
            chunks = split_by_word_count(final_raw_text, word_count)
            
        st.session_state.chunks = chunks
        st.session_state.results = {}
        
        total_chunks = len(chunks)
        task_queue = queue.Queue()
        for i, chunk in enumerate(chunks):
            task_queue.put((i, chunk))
            
        temp_results = {}
        threads = []
        for key in api_keys:
            t = threading.Thread(target=worker_translator, args=(key, task_queue, temp_results))
            t.start()
            threads.append(t)
            
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        while len(temp_results) < total_chunks:
            done_count = len(temp_results)
            progress_bar.progress(done_count / total_chunks)
            status_text.markdown(f"**⏳ Đang dịch... Hoàn thành {done_count}/{total_chunks} chương.**")
            time.sleep(1) 
            
        for t in threads:
            t.join()
            
        progress_bar.progress(1.0)
        status_text.success("🎉 ĐÃ DỊCH XONG TOÀN BỘ!")
        st.session_state.results = temp_results

# ==========================================
# KHU VỰC HIỂN THỊ TỪNG CHƯƠNG & TẢI XUỐNG
# ==========================================
if st.session_state.results:
    st.markdown("---")
    
    # 1. KHU VỰC TẢI FILE
    st.subheader("💾 LƯU BẢN DỊCH VỀ MÁY")
    
    combined_text = ""
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for i in range(len(st.session_state.chunks)):
            item = st.session_state.results.get(i)
            if item:
                # Gộp txt
                combined_text += f"{item['title']}\n\n{item['translated']}\n\n{'='*40}\n\n"
                # Nén zip
                safe_title = re.sub(r'[\\/*?:"<>|]', "", item["title"]).strip()
                file_name = f"Chuong_{i+1:03d}_{safe_title}.txt"
                content = f"{item['title']}\n\n{item['translated']}"
                zip_file.writestr(file_name, content)
                
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button("📄 TẢI 1 FILE .TXT GỘP", data=combined_text.encode('utf-8'), file_name="Truyen_Dam_My_Gop.txt", mime="text/plain", use_container_width=True)
    with col_dl2:
        st.download_button("📦 TẢI FILE .ZIP (Gồm các chương lẻ)", data=zip_buffer.getvalue(), file_name="Truyen_Dam_My_Cac_Chuong.zip", mime="application/zip", use_container_width=True)

    # 2. KHU VỰC QUẢN LÝ TỪNG CHƯƠNG
    st.markdown("---")
    st.subheader("📖 KIỂM TRA & CHỈNH SỬA TỪNG CHƯƠNG")
    
    for i in range(len(st.session_state.chunks)):
        # Tạo khung có thể đóng/mở cho gọn
        with st.expander(f"📌 {st.session_state.chunks[i]['title']}", expanded=False):
            
            # Giao diện chia 2 cột: Trái là Raw, Phải là Bản Dịch
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Bản Raw (Tiếng Trung):**")
                st.info(st.session_state.chunks[i]['content'])
            with c2:
                st.markdown("**Bản Dịch (Tiếng Việt):**")
                # Cho phép sửa trực tiếp vào ô text nếu muốn
                current_trans = st.session_state.results.get(i, {}).get("translated", "")
                st.success(current_trans)
            
            # Nút bấm dịch lại riêng cho chương này
            if st.button(f"🔄 Dịch lại {st.session_state.chunks[i]['title']}", key=f"retry_btn_{i}"):
                with st.spinner("Đang gọi AI dịch lại chương này..."):
                    retry_single_chapter(i)
                st.rerun() # Tải lại giao diện để hiển thị bản dịch mới ngay lập tức
