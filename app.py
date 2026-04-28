# -*- coding: utf-8 -*-
"""
汽车法规标准知识库 - Flask 网页版
轻量级替代 Streamlit，兼容 Python 3.6
"""

from flask import Flask, render_template_string, request, jsonify
from openai import OpenAI
import os

# ==================== 读取 API Key ====================
def get_api_key():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    api_key_file = os.path.join(script_dir, "API_KEY.md")
    
    try:
        with open(api_key_file, "r", encoding="utf-8") as f:
            content = f.read()
            for line in content.split("\n"):
                if line.strip().startswith("sk-"):
                    return line.strip()
    except Exception as e:
        print(f"读取 API Key 失败：{e}")
        return None
    return None

API_KEY = get_api_key()

if not API_KEY:
    print("❌ 未找到 API Key，请在 API_KEY.md 中配置")
    exit(1)

# 初始化客户端
client = OpenAI(
    api_key=API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# ==================== Flask 应用 ====================
app = Flask(__name__)

# HTML 模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📚 汽车法规标准知识库</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header h1 { font-size: 28px; margin-bottom: 10px; }
        .header p { opacity: 0.9; font-size: 14px; }
        .chat-box {
            padding: 30px;
            max-height: 500px;
            overflow-y: auto;
        }
        .message {
            margin-bottom: 20px;
            display: flex;
            align-items: flex-start;
        }
        .message.user { flex-direction: row-reverse; }
        .avatar {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            flex-shrink: 0;
        }
        .message.assistant .avatar { background: #667eea; margin-right: 15px; }
        .message.user .avatar { background: #4CAF50; margin-left: 15px; }
        .content {
            max-width: 70%;
            padding: 15px 20px;
            border-radius: 15px;
            line-height: 1.6;
        }
        .message.assistant .content {
            background: #f0f2f5;
            border-bottom-right-radius: 5px;
        }
        .message.user .content {
            background: #667eea;
            color: white;
            border-bottom-left-radius: 5px;
        }
        .input-box {
            padding: 20px 30px;
            border-top: 1px solid #eee;
            display: flex;
            gap: 15px;
        }
        .input-box input {
            flex: 1;
            padding: 15px 20px;
            border: 2px solid #e0e0e0;
            border-radius: 25px;
            font-size: 14px;
            outline: none;
            transition: border-color 0.3s;
        }
        .input-box input:focus { border-color: #667eea; }
        .input-box button {
            padding: 15px 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 25px;
            font-size: 14px;
            cursor: pointer;
            transition: transform 0.2s;
        }
        .input-box button:hover { transform: scale(1.05); }
        .input-box button:disabled { opacity: 0.6; cursor: not-allowed; }
        .loading { color: #999; font-style: italic; }
        .guide {
            padding: 15px 30px;
            background: #f8f9fa;
            border-top: 1px solid #eee;
            font-size: 13px;
            color: #666;
        }
        .guide strong { color: #667eea; }
        @media (max-width: 600px) {
            .content { max-width: 85%; }
            .header h1 { font-size: 22px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📚 汽车法规标准知识库</h1>
            <p>基于 GB17691-2018 和 GB/T17692-2024 标准</p>
        </div>
        
        <div class="chat-box" id="chatBox">
            <div class="message assistant">
                <div class="avatar">🤖</div>
                <div class="content">
                    你好！我是汽车法规标准专家助手，可以帮你查询：<br><br>
                    • 排放限值（如 NOx、PM 等）<br>
                    • 测试流程（WHTC+WHSC 循环）<br>
                    • 公式计算（净功率计算）<br>
                    • 合规判定规则<br>
                    • 国五/国六对比<br><br>
                    请问有什么可以帮你？
                </div>
            </div>
        </div>
        
        <div class="guide">
            <strong>💡 示例问题：</strong>国六阶段ⅥB 的 NOx 限值是多少？ | WHTC+WHSC 测试循环怎么理解？ | 净功率怎么计算？
        </div>
        
        <div class="input-box">
            <input type="text" id="questionInput" placeholder="请输入问题..." onkeypress="if(event.keyCode===13) sendQuestion()">
            <button onclick="sendQuestion()" id="sendBtn">发送</button>
        </div>
    </div>
    
    <script>
        const chatBox = document.getElementById('chatBox');
        const questionInput = document.getElementById('questionInput');
        const sendBtn = document.getElementById('sendBtn');
        
        function appendMessage(role, content) {
            const div = document.createElement('div');
            div.className = 'message ' + role;
            div.innerHTML = `
                <div class="avatar">${role === 'assistant' ? '🤖' : '👤'}</div>
                <div class="content">${content}</div>
            `;
            chatBox.appendChild(div);
            chatBox.scrollTop = chatBox.scrollHeight;
        }
        
        function sendQuestion() {
            const question = questionInput.value.trim();
            if (!question) return;
            
            appendMessage('user', question);
            questionInput.value = '';
            questionInput.disabled = true;
            sendBtn.disabled = true;
            
            appendMessage('assistant', '<span class="loading">正在查询知识库...</span>');
            
            fetch('/api/query', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({question: question})
            })
            .then(res => res.json())
            .then(data => {
                chatBox.lastElementChild.remove();
                if (data.error) {
                    appendMessage('assistant', '❌ ' + data.error);
                } else {
                    appendMessage('assistant', data.answer.replace(/\\n/g, '<br>'));
                }
                questionInput.disabled = false;
                sendBtn.disabled = false;
                questionInput.focus();
            })
            .catch(err => {
                chatBox.lastElementChild.remove();
                appendMessage('assistant', '❌ 请求失败：' + err);
                questionInput.disabled = false;
                sendBtn.disabled = false;
            });
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/query', methods=['POST'])
def query():
    data = request.get_json()
    question = data.get('question', '')
    
    if not question:
        return jsonify({'error': '请输入问题'})
    
    try:
        response = client.chat.completions.create(
            model="qwen-max",
            messages=[
                {"role": "system", "content": """你是汽车法规标准专家助手，基于 GB17691-2018 和 GB/T17692-2024 标准提供专业解答。

## 回答规范
1. 引用来源：回答时必须标注标准条款号（如"根据 GB17691-2018 表 1"）
2. 数据准确：限值、公式、参数必须精确，不得估算
3. 结构清晰：使用表格、列表、公式等格式，便于阅读
4. 实际应用：在理论解读后，提供实际应用建议
5. 只回答与汽车法规标准相关的问题
"""},
                {"role": "user", "content": question}
            ],
            temperature=0.7,
            max_tokens=2000
        )
        
        answer = response.choices[0].message.content
        return jsonify({'answer': answer})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("🚀 启动汽车法规知识库网页版...")
    print("📱 访问地址：http://localhost:5000")
    print("按 Ctrl+C 停止服务")
    app.run(host='0.0.0.0', port=5000, debug=False)
