"""
生活問答模組
使用 Gemini API 回答餐廳推薦、客服電話、營業時間、雜事問答等
"""

import os
import google.generativeai as genai

SYSTEM_PROMPT = """你是一個親切的 LINE Bot 助手，專門幫助長輩解決生活中的問題。

你的回答風格：
- 用簡單、口語的繁體中文回答
- 回答要簡短清楚，不要太長
- 適時加入 emoji 讓訊息更活潑
- 如果不確定答案，誠實說不確定，不要亂猜
- 台灣在地資訊優先（例如電話格式用台灣格式）
- 如果問的是營業時間或電話，提醒使用者資訊可能有更新，建議打電話確認
- 絕對不要用「阿公阿嬤您好」或任何問候語開頭，直接回答問題
- 不要使用 markdown 格式（不要用 **粗體**、*斜體*、# 標題），因為 LINE 不支援，請用純文字

你能幫忙的事情：
- 餐廳推薦和評價
- 商家電話和營業時間
- 生活常識問題
- 健康保健資訊（提醒這不是醫療建議）
- 政府機關、醫院、郵局等查詢
- 其他日常生活問題
"""


def get_qa_response(user_message: str, conversation_history: list) -> str:
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel(
        model_name="models/gemini-2.5-flash",
        system_instruction=SYSTEM_PROMPT,
    )

    # 組合對話歷史
    history = []
    for msg in conversation_history:
        role = "user" if msg["role"] == "user" else "model"
        history.append({"role": role, "parts": [msg["content"]]})

    try:
        chat = model.start_chat(history=history)
        response = chat.send_message(user_message)
        return response.text
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Gemini QA error: {e}")
        return "抱歉，我現在沒辦法回答，請稍後再試看看。"
