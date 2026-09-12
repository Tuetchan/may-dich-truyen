import streamlit as st
import time
import re
import io
import zipfile
import random
import concurrent.futures
from google import genai
from google.genai import types

# ==========================================
# CẤU HÌNH GIAO DIỆN & BỘ NHỚ
# ==========================================
st.set_page_config(page_title="Máy Dịch Đam Mỹ", page_icon="🏳️‍🌈", layout="wide")
st.title("🏳️‍🌈 Máy Dịch Đam Mỹ (Giám Sát Thời Gian Thực)")

if "chunks" not in st.session_state:
    st.session_state.chunks = []
if "results" not in st.session_state:
    st.session_state.results = {}
if "api_keys" not in st.session_state:
    st.session_state.api_keys = []
if "show_preview" not in st.session_state:
    st.session_state.show_preview = False
if "is_translating" not in st.session_state:
    st.session_state.is_translating = False

# ==========================================
# CÂU LỆNH YÊU CẦU AI DỊCH
# ==========================================
DANMEI_PROMPT = """Bạn là một dịch giả chuyên nghiệp, chuyên dịch tiểu thuyết đam mỹ Trung Quốc sang tiếng Việt. Tôi sẽ cung cấp cho bạn một bộ truyện bằng tiếng Trung Quốc từ một truyện đam mỹ. Nhiệm vụ của bạn là dịch sang tiếng Việt, tuân thủ các nguyên tắc sau:
Giữ nguyên phong cách văn học mạng, xưng hô phù hợp thiết lập nhân vật (Anh-em; anh-tôi; tôi-cậu...). Giữ nguyên xưng hô xuyên suốt.
Ngữ pháp chuẩn xác, dễ hiểu. Câu nào Hán Việt tối nghĩa thì dịch thuần Việt.
Giữ nguyên danh từ riêng, võ công... Trừ TÊN NHÂN VẬT, ĐỊA DANH phải thuần Việt.
Dịch hết đoạn tôi đã gửi, không tự ý thêm bớt. Chỉ output kết quả tiếng Việt!"""

# ==========================================
# CÁC HÀM TÁCH CHƯƠNG THÔNG MINH
# ==========================================
def split_large_text(title_prefix, text, max_words=2000):
    paragraphs = text.split('\n')
    chapters = []
    current_content = ""
    part = 1
    for p in paragraphs:
        if len(current_content) + len(p) > max_words:
            title = f"{title_prefix} - Phần {part}" if part > 1 or len(text) > max_words else title_prefix
            chapters.append({"title": title, "content": current_content.strip()})
            current_content = p + "\n"
            part += 1
        else:
            current_content += p + "\n"
    if current_content.strip():
        title = f"{title_prefix} - Phần {part}" if part > 1 else title_prefix
        chapters.append({"title": title, "content": current_content.strip()})
    return chapters

def split_by_chapter_title(text, max_words=2000):
    regex = r'(?:^|\n)\s*(?:第[\d一二三四五六七八九十百千万零]+[章回节集卷部]|Chapter\s*\d+|Chương\s*\d+)[^\n]*'
    matches = list(re.finditer(regex, text, re.IGNORECASE))
    if not matches: 
        return split_large_text("Phần", text, max_words)
    
    chapters = []
    if matches[0].start() > 50:
        intro = text[0:matches[0].start()].strip()
        chapters.extend(split_large_text("Tiền truyện / Mở đầu", intro, max_words))
        
    for i in range(len(matches)):
        start = matches[i].start()
        end = matches[i+1].start() if i + 1 < len(matches) else len(text)
        title = matches[i].group(0).strip()
        content = text[start:end].strip()
        chapters.extend(split_large_text(title, content, max_words))
    return chapters

# ==========================================
# HÀM CÔNG NHÂN (LÀM VIỆC ĐỘC LẬP & BÁO CÁO)
# ==========================================
def process_single_chapter(idx, chunk, api_keys, status_dict):
    """Hàm này xử lý 1 chương, tự động cập nhật trạng thái ra UI và trả về kết quả"""
    key = random.choice(api_keys) # Chọn ngẫu nhiên 1 key để phân tải
    status_dict[idx] = "🔄 Đang dịch..."
    
    retries = 2
    last_error = ""
    
    while retries > 0:
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
            
            status_dict[idx] = "🟢 Hoàn thành"
            return idx, {"title": chunk["title"], "translated": response.text, "status": "ok", "error": ""}
            
        except Exception as e:
            last_error = str(e).strip()
            # Bắt lỗi phổ biến
            if "429" in last_error or "503" in last_error or "quota" in last_error.lower():
                status_dict[idx] = f"⚠️ Nghẽn mạng/Quá tải. Thử lại sau 3s... (Còn {retries-1} lần)"
                time.sleep(3)
                key = random.choice(api_keys) # Đổi key khác
                retries -= 1
            else:
                break # Lỗi do nội dung hoặc lỗi lạ -> Dừng luôn

    # Nếu thất bại hoàn toàn
    error_msg = f"Lý do: {last_error}"
    status_dict[idx] = f"🔴 Thất bại ({error_msg})"
    return idx, {"title": chunk["title"], "translated": f"❌ KHÔNG THỂ DỊCH PHẦN NÀY.\n\nChi tiết lỗi: {error_msg}", "status": "error", "error": error_msg}

# ==========================================
# GIAO DIỆN CHÍNH
# ==========================================
st.markdown("### BƯỚC 1: CẤU HÌNH & NẠP TRUYỆN")
col1, col2 = st.columns([1, 2])
with col1:
    keys_input = st.text_area("🔑 Nhập API Keys (Mỗi dòng 1 key):", height=150)
    split_method = st.radio("✂️ Chọn cách chia chương:", ["Tự động nhận diện Chương (Kèm băm nhỏ)", "Chỉ cắt đều theo số chữ"])
    word_count = st.number_input("Số chữ tối đa mỗi phần:", value=2000, step=500)

with col2:
    uploaded_file = st.file_uploader("📥 Tải file truyện (.txt)", type=['txt'])
    raw_text = st.text_area("Hoặc dán truyện vào đây:", height=100)

if st.button("🔍 PHÂN TÍCH & XEM TRƯỚC DANH SÁCH CHƯƠNG", use_container_width=True):
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
        
        if split_method == "Tự động nhận diện Chương (Kèm băm nhỏ)":
            chunks = split_by_chapter_title(final_raw_text, max_words=word_count)
        else:
            chunks = split_large_text("Phần", final_raw_text, word_count)
            
        st.session_state.chunks = chunks
        st.session_state.results = {} 
        st.session_state.show_preview = True
        st.session_state.is_translating = False

st.markdown("---")

# ==========================================
# BƯỚC 2: DUYỆT & TIẾN HÀNH DỊCH (THỜI GIAN THỰC)
# ==========================================
if st.session_state.show_preview and not st.session_state.results and not st.session_state.is_translating:
    st.markdown("### BƯỚC 2: DUYỆT DANH SÁCH CHƯƠNG")
    st.success(f"✅ Hệ thống đã phân tích và chia truyện thành **{len(st.session_state.chunks)} phần**.")
    
    with st.container(height=250):
        for i, c in enumerate(st.session_state.chunks):
            st.markdown(f"**{i+1}. {c['title']}** *(Khoảng {len(c['content'])} chữ)*")
    
    st.warning("☝️ Hãy kiểm tra kỹ. Nếu thấy các phần chia đã đều và an toàn, hãy bấm nút dưới để dịch.")
    
    if st.button("🚀 XÁC NHẬN BẮT ĐẦU DỊCH", type="primary", use_container_width=True):
        st.session_state.is_translating = True
        st.rerun()

# --- KHU VỰC CHẠY DỊCH & THEO DÕI TRỰC TIẾP ---
if st.session_state.is_translating:
    st.markdown("### 🔄 ĐANG TRONG QUÁ TRÌNH DỊCH...")
    
    chunks = st.session_state.chunks
    total_chunks = len(chunks)
    api_keys = st.session_state.api_keys
    
    # Chuẩn bị giao diện giám sát
    progress_bar = st.progress(0)
    status_summary = st.empty()
    status_board = st.empty()
    
    # Khởi tạo bảng trạng thái
    status_dict = {i: "⏳ Đang chờ..." for i in range(total_chunks)}
    final_results = {}
    
    # BẮT ĐẦU CÁC LUỒNG DỊCH (Giới hạn bằng số Key)
    num_workers = min(len(api_keys), 10)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        # Nạp toàn bộ việc cho công nhân
        futures = {executor.submit(process_single_chapter, i, chunk, api_keys, status_dict): i for i, chunk in enumerate(chunks)}
        
        # Vòng lặp liên tục quét tiến độ mỗi 1 giây
        while futures:
            # Lấy ra các task đã xong trong 1 giây qua
            done, not_done = concurrent.futures.wait(futures, timeout=1.0, return_when=concurrent.futures.FIRST_COMPLETED)
            
            for f in done:
                idx, result = f.result()
                final_results[idx] = result
                del futures[f]
            
            # --- CẬP NHẬT GIAO DIỆN MỖI GIÂY ---
            done_count = len(final_results)
            progress_bar.progress(done_count / total_chunks)
            status_summary.markdown(f"**Tiến độ:** Hoàn thành **{done_count}/{total_chunks}** chương.")
            
            # Vẽ bảng trạng thái của tất cả các chương
            board_html = "<div style='height:300px; overflow-y:auto; font-family:monospace; background-color:#f8f9fa; padding:10px; border-radius:5px; border:1px solid #ddd;'>"
            for i in range(total_chunks):
                color = "black"
                if "Đang dịch" in status_dict[i]: color = "blue"
                elif "Hoàn thành" in status_dict[i]: color = "green"
                elif "Thất bại" in status_dict[i] or "Lỗi" in status_dict[i]: color = "red"
                elif "⚠️" in status_dict[i]: color = "orange"
                
                board_html += f"<div style='margin-bottom:4px;'><strong style='color:#333;'>{chunks[i]['title']}:</strong> <span style='color:{color};'>{status_dict[i]}</span></div>"
            board_html += "</div>"
            
            status_board.markdown(board_html, unsafe_allow_html=True)
            
    # Chạy xong toàn bộ
    status_summary.success("🎉 ĐÃ DỊCH XONG TOÀN BỘ!")
    time.sleep(1)
    st.session_state.is_translating = False
    st.session_state.results = final_results
    st.rerun()

# ==========================================
# BƯỚC 3: KIỂM TRA LỖI, SỬA & TẢI XUỐNG
# ==========================================
if st.session_state.results and not st.session_state.is_translating:
    st.markdown("### BƯỚC 3: KẾT QUẢ & XỬ LÝ LỖI")
    
    combined_text = ""
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for i in range(len(st.session_state.chunks)):
            item = st.session_state.results.get(i)
            if item and item.get("status") == "ok":
                combined_text += f"{item['title']}\n\n{item['translated']}\n\n{'='*40}\n\n"
                safe_title = re.sub(r'[\\/*?:"<>|]', "", item["title"]).strip()
                file_name = f"Phan_{i+1:03d}_{safe_title}.txt"
                content = f"{item['title']}\n\n{item['translated']}"
                zip_file.writestr(file_name, content)
                
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button("📄 TẢI 1 FILE .TXT GỘP (Chỉ gồm phần Thành công)", data=combined_text.encode('utf-8'), file_name="Truyen_Gop.txt", mime="text/plain", use_container_width=True)
    with col_dl2:
        st.download_button("📦 TẢI FILE .ZIP (Chỉ gồm phần Thành công)", data=zip_buffer.getvalue(), file_name="Truyen_Cac_Chuong.zip", mime="application/zip", use_container_width=True)

    st.markdown("---")
    st.markdown("#### BẢNG CHI TIẾT TỪNG PHẦN")
    
    # Hiển thị list chương với màu sắc rõ ràng
    for i in range(len(st.session_state.chunks)):
        result = st.session_state.results.get(i, {})
        status = result.get("status", "error")
        title = st.session_state.chunks[i]["title"]
        icon = "🟢" if status == "ok" else "🔴"
        
        # Nếu lỗi thì thẻ tự động mở tung ra
        with st.expander(f"{icon} {title}", expanded=(status == "error")):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Bản Raw (Tiếng Trung):**")
                st.info(st.session_state.chunks[i]['content'])
            with c2:
                st.markdown("**Bản Dịch (Tiếng Việt):**")
                if status == "ok":
                    st.success(result.get("translated", ""))
                else:
                    st.error(f"LỖI HỆ THỐNG TRẢ VỀ: \n{result.get('error', 'Không xác định')}")
            
            # Nút dịch lại riêng cho các phần bị lỗi
            if st.button(f"🔄 Thử dịch lại phần này", key=f"retry_btn_{i}"):
                with st.spinner(f"Đang dùng API Key để dịch lại {title}..."):
                    status_dict_temp = {}
                    _, new_result = process_single_chapter(i, st.session_state.chunks[i], st.session_state.api_keys, status_dict_temp)
                    st.session_state.results[i] = new_result
                st.rerun()
