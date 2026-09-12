import streamlit as st
import time
import threading
import queue
from google import genai
from google.genai import types

# ==========================================
# CẤU HÌNH GIAO DIỆN
# ==========================================
st.set_page_config(page_title="Máy Dịch Đam Mỹ", page_icon="🏳️‍🌈", layout="wide")
st.title("🏳️‍🌈 Máy Dịch Đam Mỹ Siêu Tốc (Hệ Thống Hàng Đợi)")

# ==========================================
# CÂU LỆNH YÊU CẦU AI DỊCH (PROMPT CỦA BẠN)
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

Lưu ý: Chỉ output kết quả tiếng Việt, không cần lặp lại các hướng dẫn và ví dụ. Hãy sẵn sàng nhận nhiệm vụ!

Đây là đoạn văn, bộ truyện file cần dịch:
"""

# ==========================================
# 1. HÀM CHIA NHỎ VĂN BẢN
# ==========================================
def split_text(text, chunk_size=2000):
    """Cắt văn bản dài thành các phần để đưa vào Hàng đợi."""
    paragraphs = text.split('\n')
    chunks = []
    current_chunk = ""
    
    for p in paragraphs:
        if len(current_chunk) + len(p) > chunk_size:
            chunks.append(current_chunk.strip())
            current_chunk = p + "\n"
        else:
            current_chunk += p + "\n"
            
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    return chunks

# ==========================================
# 2. HÀM CÔNG NHÂN (MỖI API KEY LÀ 1 CÔNG NHÂN)
# ==========================================
def worker_translator(api_key, task_queue, results_dict):
    """
    Công nhân này sẽ liên tục rút các phần văn bản từ Hàng đợi (task_queue) ra để dịch.
    Dịch xong phần nào, cất kết quả vào tủ (results_dict) rồi rút phần tiếp theo.
    """
    try:
        client = genai.Client(api_key=api_key.strip())
        # Tắt bộ lọc an toàn để tránh bị lỗi do các từ ngữ trong truyện
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
    except Exception:
        # Nếu Key bị lỗi ngay từ lúc khởi tạo, công nhân này sẽ dừng làm việc
        return

    # Liên tục làm việc cho đến khi Hàng đợi trống không
    while not task_queue.empty():
        try:
            # Lấy 1 công việc ra làm (idx là số thứ tự đoạn, chunk là văn bản tiếng Trung)
            idx, chunk = task_queue.get_nowait()
        except queue.Empty:
            break

        success = False
        retries = 3 # Cho phép thử lại 3 lần nếu mạng nghẽn
        
        while retries > 0 and not success:
            try:
                response = client.models.generate_content(
                    model="gemini-1.5-flash", 
                    contents=chunk, 
                    config=config
                )
                # Dịch thành công -> Cất vào tủ đúng vị trí
                results_dict[idx] = response.text
                success = True
            except Exception as e:
                err_msg = str(e).lower()
                if "429" in err_msg or "503" in err_msg or "quota" in err_msg:
                    time.sleep(3) # Đợi 3 giây rồi thử lại
                    retries -= 1
                else:
                    break # Lỗi nặng khác thì bỏ qua vòng lặp
        
        # Nếu thử 3 lần vẫn thất bại
        if not success:
            results_dict[idx] = f"\n\n[❌ LỖI Ở ĐOẠN NÀY: API Key bị chặn hoặc lỗi mạng. Vui lòng tự dịch lại đoạn này.]\n\n"
        
        # Báo cáo đã làm xong nhiệm vụ này
        task_queue.task_done()

# ==========================================
# 3. GIAO DIỆN CHÍNH
# ==========================================
st.markdown("💡 **Cơ chế Hàng Đợi:** Bạn nhập 5 Key, hệ thống tạo ra 5 Công nhân. Truyện được cắt thành 20 đoạn xếp hàng. Ai dịch xong trước sẽ tự lấy đoạn tiếp theo để dịch. Cam kết 100% không bị lộn xộn nội dung!")

keys_input = st.text_area("🔑 Nhập các API Keys của bạn (Mỗi dòng 1 key):", height=100)
raw_text = st.text_area("📝 Dán toàn bộ nội dung tiếng Trung vào đây (Dài thoải mái):", height=200)

if st.button("🚀 KHỞI ĐỘNG CỖ MÁY DỊCH", type="primary", use_container_width=True):
    api_keys = [k.strip() for k in keys_input.split('\n') if k.strip()]
    
    if not api_keys:
        st.error("Vui lòng nhập ít nhất 1 API Key!")
    elif not raw_text.strip():
        st.warning("Vui lòng dán văn bản cần dịch!")
    else:
        # Cắt nhỏ văn bản thành các đoạn
        chunks = split_text(raw_text)
        total_chunks = len(chunks)
        
        st.info(f"📚 Hệ thống đã chia truyện thành **{total_chunks} phần** và đưa vào Hàng đợi.")
        
        # Tạo Hàng đợi và đưa toàn bộ công việc vào
        task_queue = queue.Queue()
        for i, chunk in enumerate(chunks):
            task_queue.put((i, chunk))
            
        # Tủ chứa kết quả (Sử dụng Dictionary để đánh số, đảm bảo không bao giờ bị lệch)
        results_dict = {}
        
        # Gọi công nhân (Tạo Threads)
        threads = []
        for key in api_keys:
            t = threading.Thread(target=worker_translator, args=(key, task_queue, results_dict))
            t.start()
            threads.append(t)
            
        # Giao diện theo dõi tiến độ
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Vòng lặp cập nhật tiến trình trên giao diện (Vẫn giữ cho Web mượt mà)
        while len(results_dict) < total_chunks:
            done_count = len(results_dict)
            progress_bar.progress(done_count / total_chunks)
            status_text.markdown(f"**Đang dịch... Hoàn thành {done_count}/{total_chunks} phần.**")
            time.sleep(1) # Kiểm tra tiến độ mỗi giây
            
        # Chờ tất cả công nhân nghỉ việc
        for t in threads:
            t.join()
            
        progress_bar.progress(1.0)
        status_text.success("🎉 ĐÃ DỊCH XONG TOÀN BỘ!")
        
        # Mở tủ lấy kết quả ghép lại theo đúng số thứ tự 0, 1, 2, 3...
        ordered_results = []
        for i in range(total_chunks):
            ordered_results.append(results_dict.get(i, ""))
            
        final_translation = "\n\n".join(ordered_results)
        
        # Hiển thị
        st.subheader("🇻🇳 BẢN DỊCH HOÀN CHỈNH:")
        st.text_area("Có thể copy từ đây:", value=final_translation, height=500)
        
        st.download_button(
            label="💾 Tải Bản Dịch Về Máy (.txt)",
            data=final_translation.encode('utf-8'),
            file_name="Ban_Dich_Dam_My.txt",
            mime="text/plain",
            use_container_width=True
        )
