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
st.title("🏳️‍🌈 Máy Dịch Đam Mỹ (Giám Sát & UI Gọn Nhẹ)")

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
# CÁC HÀM TÁCH CHƯƠNG THÔNG MINH (NÂNG CẤP)
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
    # Regex Nâng Cấp: Bắt được định dạng "024 - 第23章", "Chương 12", "Chương XII", "第十一章"...
    regex = r'(?:^|\n)\s*(?:[\dIVXLCDM]+\s*[-_.:]\s*)?(?:第\s*[\d一二三四五六七八九十百千万零]+\s*[章回节集卷部]|Chapter\s*[\dIVXLCDM]+|Chương\s*[\dIVXLCDM]+)[^\n]*'
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
# HÀM CÔNG NHÂN (WORKER)
# ==========================================
def process_single_chapter(idx, chunk, api_keys, status_dict):
    key = random.choice(api_keys)
    safe_key = key[:8] + "..." if len(key) > 8 else key 
    
    status_dict[idx] = f"🔄 Đang dịch... [Key: {safe_key}]"
    
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
            
            status_dict[idx] = f"🟢 Hoàn thành [Key: {safe_key}]"
            return idx, {"title": chunk["title"], "translated": response.text, "status": "ok", "error": "", "key_used": safe_key}
            
        except Exception as e:
            last_error = str(e).strip()
            if "429" in last_error or "503" in last_error or "quota" in last_error.lower():
                status_dict[idx] = f"⚠️ Quá tải [Key: {safe_key}]. Đang tráo Key... (Còn {retries-1} lần)"
                time.sleep(3)
                key = random.choice(api_keys)
                safe_key = key[:8] + "..." if len(key) > 8 else key
                retries -= 1
            else:
                break 

    error_msg = f"Lý do: {last_error}"
    status_dict[idx] = f"🔴 Thất bại [Key: {safe_key}]"
    return idx, {"title": chunk["title"], "translated": f"❌ KHÔNG THỂ DỊCH PHẦN NÀY.\n\nChi tiết lỗi: {error_msg}", "status": "error", "error": error_msg, "key_used": safe_key}

def retry_single_chapter(idx):
    if not st.session_state.api_keys: return
    key = random.choice(st.session_state.api_keys)
    safe_key = key[:8] + "..." if len(key) > 8 else key
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
        st.session_state.results[idx] = {"title": chunk["title"], "translated": response.text, "status": "ok", "error": "", "key_used": safe_key}
    except Exception as e:
        st.session_state.results[idx] = {"title": chunk["title"], "translated": f"❌ Vẫn bị lỗi: {e}", "status": "error", "error": str(e), "key_used": safe_key}

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
# BƯỚC 2: DUYỆT & TIẾN HÀNH DỊCH
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

if st.session_state.is_translating:
    st.markdown("### 🔄 ĐANG TRONG QUÁ TRÌNH DỊCH...")
    
    chunks = st.session_state.chunks
    total_chunks = len(chunks)
    api_keys = st.session_state.api_keys
    
    progress_bar = st.progress(0)
    status_summary = st.empty()
    status_board = st.empty()
    
    status_dict = {i: "⏳ Đang chờ..." for i in range(total_chunks)}
    final_results = {}
    
    num_workers = min(len(api_keys), 10)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(process_single_chapter, i, chunk, api_keys, status_dict): i for i, chunk in enumerate(chunks)}
        
        while futures:
            done, not_done = concurrent.futures.wait(futures, timeout=1.0, return_when=concurrent.futures.FIRST_COMPLETED)
            
            for f in done:
                idx, result = f.result()
                final_results[idx] = result
                del futures[f]
            
            done_count = len(final_results)
            progress_bar.progress(done_count / total_chunks)
            status_summary.markdown(f"**Tiến độ:** Hoàn thành **{done_count}/{total_chunks}** chương.")
            
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
            
    status_summary.success("🎉 ĐÃ DỊCH XONG TOÀN BỘ!")
    time.sleep(1)
    st.session_state.is_translating = False
    st.session_state.results = final_results
    st.rerun()

# ==========================================
# BƯỚC 3: KIỂM TRA LỖI, SỬA & TẢI XUỐNG (UI RÚT GỌN)
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
        st.download_button("📄 TẢI 1 FILE .TXT GỘP (Chỉ phần Thành công)", data=combined_text.encode('utf-8'), file_name="Truyen_Gop.txt", mime="text/plain", use_container_width=True)
    with col_dl2:
        st.download_button("📦 TẢI FILE .ZIP (Chỉ phần Thành công)", data=zip_buffer.getvalue(), file_name="Truyen_Cac_Chuong.zip", mime="application/zip", use_container_width=True)

    st.markdown("---")
    st.markdown("#### DANH SÁCH BẢN DỊCH (Bấm vào để xem chi tiết)")
    
    for i in range(len(st.session_state.chunks)):
        result = st.session_state.results.get(i, {})
        status = result.get("status", "error")
        title = st.session_state.chunks[i]["title"]
        key_used = result.get("key_used", "Không rõ")
        translated_text = result.get("translated", "")
        
        # Tạo đoạn xem trước (Preview) khoảng 80-100 ký tự để hiện ra ngoài
        preview_text = translated_text.replace('\n', ' ')[:90] + "..." if translated_text else "Chưa có nội dung..."
        
        icon = "🟢" if status == "ok" else "🔴"
        
        # UI Rút gọn: Tiêu đề thẻ sẽ chứa Tên Chương + Đoạn Preview ngắn
        expander_title = f"{icon} {title} | {preview_text}"
        
        with st.expander(expander_title, expanded=(status == "error")):
            st.caption(f"Dịch bởi API Key: {key_used}")
            
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Bản Raw (Tiếng Trung):**")
                # Giới hạn chiều cao ô text để trang không bị dài lê thê
                st.text_area("raw", st.session_state.chunks[i]['content'], height=250, key=f"raw_{i}", label_visibility="collapsed")
            with c2:
                st.markdown("**Bản Dịch (Tiếng Việt):**")
                if status == "ok":
                    st.text_area("trans", translated_text, height=250, key=f"trans_{i}", label_visibility="collapsed")
                else:
                    st.error(f"LỖI HỆ THỐNG TRẢ VỀ: \n{result.get('error', 'Không xác định')}")
            
            if st.button(f"🔄 Thử dịch lại phần này", key=f"retry_btn_{i}"):
                with st.spinner(f"Đang tự động dùng API Key khác để dịch lại..."):
                    retry_single_chapter(i)
                st.rerun()
