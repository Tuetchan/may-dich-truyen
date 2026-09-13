import streamlit as st
import time
import re
import io
import os
import zipfile
import random
import concurrent.futures
from google import genai
from google.genai import types

# ==========================================
# CẤU HÌNH GIAO DIỆN & BỘ NHỚ
# ==========================================
st.set_page_config(page_title="AI Translator Pro", page_icon="⚡", layout="wide")

if "chunks" not in st.session_state:
    st.session_state.chunks = []
if "results" not in st.session_state:
    st.session_state.results = {}
if "api_keys" not in st.session_state:
    st.session_state.api_keys = []
if "is_translating" not in st.session_state:
    st.session_state.is_translating = False

# ==========================================
# PROMPT CHUẨN CỦA BẠN
# ==========================================
UNIVERSAL_PROMPT = """Bạn là một dịch giả chuyên nghiệp, chuyên dịch tiểu thuyết đam mỹ Trung Quốc sang tiếng Việt. Tôi sẽ cung cấp cho bạn một bộ truyện bằng tiếng Trung Quốc từ một truyện đam mỹ, bao gồm tựa đề, nội dung và thông tin chương. Nhiệm vụ của bạn là dịch các chương truyện này sang tiếng Việt, tuân thủ các nguyên tắc sau:

Giữ nguyên phong cách văn học mạng: Sử dụng ngôn ngữ và giọng văn phù hợp với thể loại đam mỹ,... các xưng hô cho phù hợp với thiết lập nhân vật và nhân xưng của tiếng việt (Anh-em; anh-tôi;tôi-cậu. Giữ nguyên xưng hô xuyên suốt đoạn dịch không tự ý thay đổi khi đã xuất bản dịch.

Ngữ pháp chuẩn xác: Đảm bảo bản dịch tuân thủ ngữ pháp tiếng Việt, dễ đọc, dễ hiểu.

Nếu 1 số câu của tác giả quá khô cứng và tối nghĩa nếu dịch theo Hán Việt, thì hãy chuyển qua văn phong thuần Việt sao cho dễ hiểu.

Xử lý danh từ riêng: Giữ nguyên tất cả các danh từ riêng như tên người, địa điểm, môn phái, võ công,... ở dạng Trung Quốc gốc.
Trừ các danh từ riêng TÊN NHÂN VẬT, ĐỊA DANH, ĐỊA ĐIỂM, phải dịch thuần việt, TUYỆT ĐỐI KHÔNG LẠM DỤNG HÁN VIỆT.

Dịch : (bản dịch đúng văn phong tác giả).

Hãy sau mỗi đoạn xuống dòng giữa các câu cho dễ đọc
Dịch hết đoạn tôi đã gửi. Tuyệt đối không được dừng giữa chừng và tự ý thêm tình tiết truyện. Hết văn bản tôi gửi là phải lập tức dừng lại.

Lưu ý: Chỉ output kết quả tiếng Việt, không cần lặp lại các hướng dẫn và ví dụ. Hãy sẵn sàng nhận nhiệm vụ!"""

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
    regex = r'(?:^|\n)\s*(?:[\dIVXLCDM]+\s*[-_.:]\s*)?(?:第\s*[\d一二三四五六七八九十百千万零]+\s*[章回节集卷部]|Chapter\s*[\dIVXLCDM]+|Chương\s*[\dIVXLCDM]+)[^\n]*'
    matches = list(re.finditer(regex, text, re.IGNORECASE))
    if not matches: return split_large_text("Phần", text, max_words)
    
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
# HÀM CÔNG NHÂN (WORKER) XỬ LÝ DỊCH
# ==========================================
def process_single_chapter(idx, chunk, active_keys_pool, status_dict):
    retries = 3
    last_error = ""
    
    while retries > 0:
        if not active_keys_pool:
            status_dict[idx] = "🔴 Thất bại: TOÀN BỘ KEY ĐÃ HẾT HẠN MỨC!"
            return idx, {"title": chunk["title"], "translated": "❌ LỖI: Toàn bộ API Keys đã chết.", "status": "error"}

        key = random.choice(active_keys_pool)
        safe_key = key[:8] + "..." if len(key) > 8 else key 
        status_dict[idx] = f"🔄 Đang dịch... [Key: {safe_key}]"
        
        try:
            client = genai.Client(api_key=key.strip())
            safety_settings = [
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            ]
            config = types.GenerateContentConfig(system_instruction=UNIVERSAL_PROMPT, safety_settings=safety_settings, temperature=0.3)
            response = client.models.generate_content(model="gemini-2.5-flash", contents=chunk["content"], config=config)
            
            status_dict[idx] = f"🟢 Hoàn thành [Key: {safe_key}]"
            return idx, {"title": chunk["title"], "translated": response.text, "status": "ok", "error": "", "key_used": safe_key}
            
        except Exception as e:
            last_error = str(e).lower()
            if "quota" in last_error or "exhausted" in last_error:
                status_dict[idx] = f"🗑️ Key {safe_key} hết Quota! Đang loại bỏ..."
                if key in active_keys_pool: active_keys_pool.remove(key)
            elif "429" in last_error or "too many" in last_error:
                status_dict[idx] = f"⏳ Google chặn do quá nhanh. Chờ 10s..."
                time.sleep(10)
                retries -= 1
            elif "safety" in last_error or "finish reason: 3" in last_error:
                status_dict[idx] = f"🔞 Cảnh báo: Google chặn nội dung 18+/Bạo lực."
                return idx, {"title": chunk["title"], "translated": "❌ CHƯƠNG NÀY BỊ CHẶN DO VI PHẠM CHÍNH SÁCH GOOGLE.", "status": "error", "error": "Bị chặn bởi Safety Policy", "key_used": safe_key}
            else:
                status_dict[idx] = f"⚠️ Lỗi mạng. Thử lại sau 3s... (Còn {retries-1} lần)"
                time.sleep(3)
                retries -= 1

    status_dict[idx] = f"🔴 Thất bại hoàn toàn phần này."
    return idx, {"title": chunk["title"], "translated": f"❌ LỖI HỆ THỐNG.\nChi tiết: {last_error}", "status": "error", "error": last_error, "key_used": "N/A"}

def retry_single_chapter(idx):
    if not st.session_state.api_keys: return
    active_pool = st.session_state.api_keys.copy()
    temp_status = {}
    _, result = process_single_chapter(idx, st.session_state.chunks[idx], active_pool, temp_status)
    st.session_state.results[idx] = result


# ==========================================
# CỘT SIDEBAR (CÀI ĐẶT BÊN TRÁI)
# ==========================================
with st.sidebar:
    st.markdown("## ⚙️ Cài Đặt Hệ Thống")
    keys_input = st.text_area("🔑 API Keys Gemini (Mỗi dòng 1 key):", height=150, help="Hệ thống sẽ tự động xoay vòng Key và loại bỏ Key chết.")
    
    st.markdown("---")
    st.markdown("### 📂 Liên Kết Thư Mục")
    input_dir = st.text_input("📁 Đường dẫn Input (Nguồn):", placeholder="Ví dụ: C:\\Truyen\\Raw", help="Hệ thống sẽ gom toàn bộ file .txt trong thư mục này để dịch.")
    output_dir = st.text_input("💾 Đường dẫn Output (Đích):", placeholder="Ví dụ: C:\\Truyen\\Dich", help="Dịch xong, hệ thống tự động lưu file .txt vào thư mục này.")

    st.markdown("---")
    st.markdown("### ✂️ Tùy chọn Tách Chương")
    split_method = st.radio("Chế độ:", ["Tự động nhận diện Chương", "Chỉ cắt đều theo số chữ"])
    word_count = st.number_input("Số chữ tối đa / phần (Khuyên dùng: 2000):", value=2000, step=500)
    
    st.markdown("---")
    st.success("Tích hợp Prompt Đam Mỹ đa năng.\nXưng hô đồng nhất. Tự động nhận diện nội dung.")

# ==========================================
# GIAO DIỆN CHÍNH (MAIN CONTENT)
# ==========================================
st.title("⚡ AI Translator Pro")

# BẢNG THỐNG KÊ (METRICS)
total_c = len(st.session_state.chunks)
done_c = sum(1 for v in st.session_state.results.values() if v.get("status") == "ok")
err_c = len(st.session_state.results) - done_c if st.session_state.results else 0

col_m1, col_m2, col_m3 = st.columns(3)
col_m1.metric("📚 Tổng số phần", f"{total_c}")
col_m2.metric("✅ Đã hoàn thành", f"{done_c}")
col_m3.metric("🔴 Bị Lỗi / Chặn", f"{err_c}")

st.markdown("---")

# TẠO 3 TABS GIAO DIỆN
tab1, tab2, tab3 = st.tabs(["📁 1. Nguồn Truyện & Phân Tích", "🚀 2. Bảng Dịch Thuật", "💾 3. Kết Quả & Chỉnh Sửa"])

# ------------------------------------------
# TAB 1: NẠP VÀ TÁCH CHƯƠNG
# ------------------------------------------
with tab1:
    st.markdown("#### Nạp nội dung cần dịch")
    st.info("💡 **Mẹo:** Bạn có thể điền Đường dẫn Input ở Cột trái, tải file lên, hoặc dán chữ trực tiếp. Hệ thống sẽ ưu tiên lấy từ Thư mục Input trước.")
    
    uploaded_file = st.file_uploader("📥 Tải 1 file .txt lên", type=['txt'])
    raw_text = st.text_area("✍️ Hoặc dán trực tiếp truyện vào đây:", height=200)

    if st.button("🔍 PHÂN TÍCH & LÊN DANH SÁCH", use_container_width=True):
        api_keys = [k.strip() for k in keys_input.split('\n') if k.strip()]
        final_raw_text = ""
        
        # 1. Ưu tiên đọc từ Thư mục Nguồn (Input Directory)
        if input_dir and os.path.isdir(input_dir.strip()):
            for filename in sorted(os.listdir(input_dir.strip())):
                if filename.endswith(".txt"):
                    filepath = os.path.join(input_dir.strip(), filename)
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        final_raw_text += f"\n\n{f.read()}\n\n"
        # 2. Nếu không có thư mục thì lấy từ File tải lên
        elif uploaded_file is not None:
            final_raw_text = uploaded_file.getvalue().decode("utf-8", errors="ignore")
        # 3. Nếu không có thì lấy Text dán
        elif raw_text.strip():
            final_raw_text = raw_text.strip()
            
        if not api_keys: st.error("❌ Vui lòng nhập API Key ở menu Cài đặt bên trái!")
        elif not final_raw_text: st.error("❌ Vui lòng cung cấp nội dung truyện (Nhập thư mục, tải file hoặc dán text)!")
        else:
            st.session_state.api_keys = api_keys
            if split_method == "Tự động nhận diện Chương":
                chunks = split_by_chapter_title(final_raw_text, max_words=word_count)
            else:
                chunks = split_large_text("Phần", final_raw_text, word_count)
                
            st.session_state.chunks = chunks
            st.session_state.results = {} 
            st.session_state.is_translating = False
            st.success(f"Đã gom văn bản và chia thành {len(chunks)} phần. Hãy chuyển sang Tab **2. Bảng Dịch Thuật** để bắt đầu!")

    if st.session_state.chunks:
        with st.expander("👀 Xem trước danh sách các chương đã chia", expanded=False):
            for i, c in enumerate(st.session_state.chunks):
                st.write(f"**{i+1}. {c['title']}** *(~{len(c['content'])} chữ)*")

# ------------------------------------------
# TAB 2: QUÁ TRÌNH DỊCH THUẬT LIVE
# ------------------------------------------
with tab2:
    if not st.session_state.chunks:
        st.info("👈 Hãy phân tích truyện ở Tab 1 trước.")
    else:
        st.markdown("#### Tiến trình Dịch vụ AI")
        if st.button("🚀 XÁC NHẬN BẮT ĐẦU DỊCH", type="primary", use_container_width=True, disabled=st.session_state.is_translating):
            st.session_state.is_translating = True
            st.rerun()

        if st.session_state.is_translating:
            chunks = st.session_state.chunks
            total_chunks = len(chunks)
            active_keys_pool = st.session_state.api_keys.copy()
            
            progress_bar = st.progress(0)
            status_summary = st.empty()
            status_board = st.empty()
            
            status_dict = {i: "⏳ Đang xếp hàng..." for i in range(total_chunks)}
            final_results = {}
            num_workers = min(len(active_keys_pool), 10)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = {executor.submit(process_single_chapter, i, chunk, active_keys_pool, status_dict): i for i, chunk in enumerate(chunks)}
                
                while futures:
                    done, not_done = concurrent.futures.wait(futures, timeout=1.0, return_when=concurrent.futures.FIRST_COMPLETED)
                    for f in done:
                        idx, result = f.result()
                        final_results[idx] = result
                        del futures[f]
                    
                    done_count = len(final_results)
                    progress_bar.progress(done_count / total_chunks)
                    
                    key_status_msg = f"🟢 Đang chạy ({len(active_keys_pool)} Key còn sống)" if active_keys_pool else "🔴 TOÀN BỘ KEY ĐÃ CHẾT!"
                    status_summary.markdown(f"**Tiến độ:** Hoàn thành **{done_count}/{total_chunks}** chương. | Trạng thái Key: {key_status_msg}")
                    
                    board_html = "<div style='height:350px; overflow-y:auto; font-family:monospace; background-color:#1e1e1e; color:#d4d4d4; padding:15px; border-radius:8px;'>"
                    for i in range(total_chunks):
                        color = "#d4d4d4"
                        if "Đang dịch" in status_dict[i]: color = "#60a5fa"
                        elif "Hoàn thành" in status_dict[i]: color = "#4ade80"
                        elif "Thất bại" in status_dict[i] or "❌" in status_dict[i] or "🔞" in status_dict[i]: color = "#f87171"
                        elif "⚠️" in status_dict[i] or "🗑️" in status_dict[i] or "⏳" in status_dict[i]: color = "#fbbf24"
                        board_html += f"<div style='margin-bottom:6px;'><strong style='color:#fff;'>{chunks[i]['title']}:</strong> <span style='color:{color};'>{status_dict[i]}</span></div>"
                    board_html += "</div>"
                    status_board.markdown(board_html, unsafe_allow_html=True)
                    
            status_summary.success("🎉 ĐÃ DỊCH XONG! Hãy chuyển sang Tab **3. Kết quả & Chỉnh sửa**.")
            
            # --- TỰ ĐỘNG XUẤT RA THƯ MỤC NẾU CÓ ---
            if output_dir and os.path.isdir(output_dir.strip()):
                out_path = output_dir.strip()
                for i in range(total_chunks):
                    item = final_results.get(i)
                    if item and item.get("status") == "ok":
                        safe_title = re.sub(r'[\\/*?:"<>|]', "", item["title"]).strip()
                        file_path = os.path.join(out_path, f"Phan_{i+1:03d}_{safe_title}.txt")
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(f"{item['title']}\n\n{item['translated']}")
                st.toast(f"✅ Đã lưu file thành công vào thư mục: {out_path}", icon="💾")

            time.sleep(1)
            st.session_state.is_translating = False
            st.session_state.results = final_results
            st.rerun()

# ------------------------------------------
# TAB 3: KẾT QUẢ & CHỈNH SỬA
# ------------------------------------------
with tab3:
    if not st.session_state.results:
        st.info("👈 Hãy chờ quá trình dịch hoàn tất ở Tab 2.")
    else:
        st.markdown("#### Lưu Trữ Bản Dịch")
        
        # Báo cáo nếu đã lưu ra thư mục
        if output_dir and os.path.isdir(output_dir.strip()):
            st.success(f"💾 Hệ thống đã tự động lưu từng chương vào thư mục trên máy tính của bạn: **{output_dir.strip()}**")
            
        combined_text = ""
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for i in range(len(st.session_state.chunks)):
                item = st.session_state.results.get(i)
                if item and item.get("status") == "ok":
                    combined_text += f"{item['title']}\n\n{item['translated']}\n\n{'='*40}\n\n"
                    safe_title = re.sub(r'[\\/*?:"<>|]', "", item["title"]).strip()
                    zip_file.writestr(f"Phan_{i+1:03d}_{safe_title}.txt", f"{item['title']}\n\n{item['translated']}")
                    
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button("📄 TẢI 1 FILE .TXT GỘP TRÊN WEB", data=combined_text.encode('utf-8'), file_name="Truyen_Gop.txt", mime="text/plain", use_container_width=True)
        with col_dl2:
            st.download_button("📦 TẢI FILE .ZIP TRÊN WEB", data=zip_buffer.getvalue(), file_name="Truyen_Cac_Chuong.zip", mime="application/zip", use_container_width=True)

        st.markdown("---")
        st.markdown("#### Quản lý & Sửa Lỗi Từng Phần")
        
        for i in range(len(st.session_state.chunks)):
            result = st.session_state.results.get(i, {})
            status = result.get("status", "error")
            title = st.session_state.chunks[i]["title"]
            translated_text = result.get("translated", "")
            preview_text = translated_text.replace('\n', ' ')[:90] + "..." if translated_text else "Chưa có nội dung..."
            
            icon = "🟢" if status == "ok" else "🔴"
            
            with st.expander(f"{icon} {title} | {preview_text}", expanded=(status == "error")):
                st.caption(f"Dịch bởi API Key: {result.get('key_used', 'Không rõ')}")
                
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**Bản Raw (Tiếng Trung):**")
                    st.text_area("raw", st.session_state.chunks[i]['content'], height=250, key=f"raw_{i}", label_visibility="collapsed")
                with c2:
                    st.markdown("**Bản Dịch (Tiếng Việt):**")
                    if status == "ok":
                        st.text_area("trans", translated_text, height=250, key=f"trans_{i}", label_visibility="collapsed")
                    else:
                        st.error(translated_text)
                
                if st.button(f"🔄 Thử dịch lại {title}", key=f"retry_btn_{i}"):
                    with st.spinner("Đang tự động dùng API Key khác để dịch lại..."):
                        retry_single_chapter(i)
                        
                        # Tự động cập nhật lại file vào Output Directory nếu có
                        if status == "error" and output_dir and os.path.isdir(output_dir.strip()):
                            new_res = st.session_state.results.get(i)
                            if new_res and new_res.get("status") == "ok":
                                safe_t = re.sub(r'[\\/*?:"<>|]', "", new_res["title"]).strip()
                                path_out = os.path.join(output_dir.strip(), f"Phan_{i+1:03d}_{safe_t}.txt")
                                with open(path_out, "w", encoding="utf-8") as f:
                                    f.write(f"{new_res['title']}\n\n{new_res['translated']}")
                                st.toast(f"Đã cập nhật lại file: {safe_t}", icon="✅")
                    st.rerun()
