import streamlit as st
import time
from google import genai
from google.genai import types

# CẤU HÌNH GIAO DIỆN
st.set_page_config(page_title="Máy Dịch Truyện Cá Nhân", page_icon="⚡", layout="wide")
st.title("⚡ Máy Dịch Truyện Siêu Tốc (Xử lý truyện dài)")

# 1. HÀM CHIA NHỎ VĂN BẢN (TRÁNH QUÁ TẢI)
def split_text(text, chunk_size=2500):
    """Cắt văn bản dài thành các đoạn ngắn hơn, không cắt ngang câu."""
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

# 2. HÀM GỌI API GEMINI (CÓ ĐẢO KEY KHI LỖI)
def translate_chunk(text_chunk, api_keys_list):
    system_prompt = "Bạn là một dịch giả tiểu thuyết chuyên nghiệp. Dịch mượt mà, thuần Việt, không tự ý thêm bớt tình tiết. Chỉ trả về kết quả dịch, không giải thích gì thêm."
    
    for key in api_keys_list:
        try:
            client = genai.Client(api_key=key.strip())
            # Tắt các bộ lọc an toàn để tránh bị chặn khi truyện có yếu tố đánh nhau
            safety_settings = [
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            ]
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                safety_settings=safety_settings,
                temperature=0.3
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash", # Dùng model mặc định ổn định nhất
                contents=text_chunk, 
                config=config
            )
            return True, response.text
        except Exception as e:
            err_msg = str(e).lower()
            if "429" in err_msg or "quota" in err_msg:
                continue # Bị giới hạn -> Nhảy sang Key tiếp theo
            elif "503" in err_msg or "unavailable" in err_msg:
                time.sleep(2) # Kẹt mạng -> Chờ 2s rồi thử lại
                continue
            else:
                continue # Các lỗi khác cũng thử nhảy key
                
    return False, "❌ Lỗi: Tất cả API Key đều đã hết hạn mức hoặc bị lỗi."

# 3. GIAO DIỆN NGƯỜI DÙNG
# Nhập danh sách API Key
keys_input = st.text_area("🔑 Nhập API Keys (Mỗi dòng 1 key, tool tự động đổi key khi nghẽn):", height=100)

# Nhập nội dung cần dịch
raw_text = st.text_area("📝 Dán nội dung truyện cần dịch (Dài mấy cũng được):", height=250)

# Nút Dịch
if st.button("🚀 BẮT ĐẦU DỊCH", type="primary", use_container_width=True):
    api_keys = [k.strip() for k in keys_input.split('\n') if k.strip()]
    
    if not api_keys:
        st.error("Vui lòng nhập ít nhất 1 API Key!")
    elif not raw_text.strip():
        st.warning("Vui lòng dán văn bản cần dịch!")
    else:
        # Cắt nhỏ văn bản
        chunks = split_text(raw_text)
        st.info(f"Văn bản quá dài, hệ thống đã tự động chia thành {len(chunks)} phần nhỏ để AI dịch không bị lỗi.")
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        final_translation = ""
        
        # Dịch từng phần
        for i, chunk in enumerate(chunks):
            status_text.text(f"⏳ Đang dịch phần {i+1}/{len(chunks)}...")
            
            success, result = translate_chunk(chunk, api_keys)
            
            if success:
                final_translation += result + "\n\n"
            else:
                final_translation += f"\n\n[LỖI Ở PHẦN NÀY: {result}]\n\n"
                st.error("Quá trình dịch bị gián đoạn do lỗi API Key.")
                break
                
            # Cập nhật thanh tiến trình
            progress_bar.progress((i + 1) / len(chunks))
            
            # Nghỉ 1 giây giữa các phần để tránh spam máy chủ Google
            time.sleep(1)
            
        status_text.success("🎉 ĐÃ DỊCH XONG!")
        
        # Hiển thị kết quả
        st.subheader("🇻🇳 KẾT QUẢ DỊCH:")
        st.text_area("Bản dịch:", value=final_translation, height=400)
        
        # Nút tải file về
        st.download_button(
            label="💾 Tải bản dịch về máy (.txt)",
            data=final_translation.encode('utf-8'),
            file_name="Ban_Dich.txt",
            mime="text/plain",
            use_container_width=True
        )
