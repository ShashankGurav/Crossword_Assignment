"""
app.py
Minimal Flask web interface for the Aster & Row support agent.
Run with: python app.py
"""

from flask import Flask, request, jsonify, render_template_string
from agent import chat, new_session

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aster & Row Support</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #f5f5f0; color: #1a1a1a; }
  header { background: #1a1a1a; color: white; padding: 16px 24px; }
  header h1 { font-size: 18px; font-weight: 600; }
  header p { font-size: 12px; color: #aaa; margin-top: 2px; }
  #chat-container { max-width: 720px; margin: 24px auto; padding: 0 16px; }
  #messages { display: flex; flex-direction: column; gap: 12px; margin-bottom: 90px; }
  .msg { padding: 14px 16px; border-radius: 12px; max-width: 85%; line-height: 1.6; font-size: 14px; }
  .msg.user { background: #1a1a1a; color: white; align-self: flex-end; }
  .msg.agent { background: white; border: 1px solid #e5e5e5; align-self: flex-start; width: 85%; }
  .msg.agent p { margin-bottom: 8px; }
  .msg.agent p:last-of-type { margin-bottom: 0; }
  .msg.agent strong { font-weight: 700; color: #111; }
  .msg.agent em { font-style: italic; color: #555; }
  .msg.agent ul { margin: 6px 0 10px 20px; }
  .msg.agent ul li { margin-bottom: 4px; }
  .msg.agent .sources { margin-top: 12px; font-size: 11px; color: #999; border-top: 1px solid #eee; padding-top: 8px; }
  .msg.agent .handoff { margin-top: 10px; font-size: 12px; background: #fff8e1; color: #7a5c00; padding: 8px 10px; border-radius: 6px; border-left: 3px solid #f0c000; }
  .msg.agent .conflict { margin-top: 10px; font-size: 12px; background: #fff0f0; color: #a00; padding: 8px 10px; border-radius: 6px; border-left: 3px solid #e00; }
  .typing { color: #aaa; font-size: 13px; font-style: italic; }
  .input-wrapper { position: fixed; bottom: 0; left: 0; right: 0; background: white; border-top: 1px solid #e5e5e5; padding: 14px 16px; }
  .input-inner { max-width: 720px; margin: 0 auto; display: flex; gap: 8px; }
  .input-inner input { flex: 1; padding: 10px 14px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; outline: none; transition: border-color 0.2s; }
  .input-inner input:focus { border-color: #1a1a1a; }
  .input-inner button { padding: 10px 22px; background: #1a1a1a; color: white; border: none; border-radius: 8px; font-size: 14px; cursor: pointer; transition: background 0.2s; }
  .input-inner button:hover { background: #333; }
  .input-inner button:disabled { background: #bbb; cursor: not-allowed; }
</style>
</head>
<body>
<header>
  <h1>Aster &amp; Row Support</h1>
  <p>Ask about returns, shipping, orders, or products</p>
</header>
<div id="chat-container">
  <div id="messages">
    <div class="msg agent">
      <p>Hi! I'm the Aster &amp; Row support assistant. I can help with return policies, shipping, order status, product care, and more. How can I help you today?</p>
    </div>
  </div>
</div>
<div class="input-wrapper">
  <div class="input-inner">
    <input type="text" id="user-input" placeholder="Ask a question..." autofocus />
    <button id="send-btn" onclick="sendMessage()">Send</button>
  </div>
</div>

<script>
  let sessionId = null;

  function formatResponse(text) {
    // Strip [HANDOFF RECOMMENDED] tag from visible text
    text = text.replace(/\\[HANDOFF RECOMMENDED\\]/g, '').trim();

    // Escape HTML entities
    text = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Bold: **text**
    text = text.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');

    // Italic: *text*
    text = text.replace(/\\*([^*\\n]+?)\\*/g, '<em>$1</em>');

    // Convert bullet lines (* item or - item) to <li>
    text = text.replace(/^[\\*\\-] (.+)$/gm, '<li>$1</li>');

    // Wrap consecutive <li> blocks in <ul>
    text = text.replace(/(<li>[\s\S]*?<\/li>)(\s*<li>[\s\S]*?<\/li>)*/g, '<ul>$&</ul>');

    // Split into paragraphs on double newline
    const paragraphs = text.split(/\\n\\n+/);
    text = paragraphs.map(para => {
      para = para.trim();
      if (!para) return '';
      if (para.startsWith('<ul>')) return para;
      if (para.startsWith('<li>')) return '<ul>' + para + '</ul>';
      return '<p>' + para.replace(/\\n/g, '<br>') + '</p>';
    }).filter(Boolean).join('');

    return text;
  }

  async function sendMessage() {
    const input = document.getElementById('user-input');
    const msg = input.value.trim();
    if (!msg) return;

    input.value = '';
    document.getElementById('send-btn').disabled = true;

    // Show user message (escaped)
    appendMessage('user', msg.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'));
    const typingEl = appendMessage('agent', '<span class="typing">Thinking...</span>');

    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, session_id: sessionId })
      });
      const data = await res.json();
      sessionId = data.session_id;

      let html = formatResponse(data.response);

      if (data.sources && data.sources.length > 0) {
        html += `<div class="sources">📄 Sources: ${data.sources.join(', ')}</div>`;
      }
      if (data.conflict) {
        html += `<div class="conflict">⚠️ Our sources conflict on this topic — please contact support for a definitive answer</div>`;
      }
      if (data.needs_handoff) {
        html += `<div class="handoff">👤 A human support agent can best help with this — please reach out to our team directly</div>`;
      }

      typingEl.innerHTML = html;
    } catch (e) {
      typingEl.innerHTML = '<p>Something went wrong. Please try again.</p>';
    }

    document.getElementById('send-btn').disabled = false;
    input.focus();
  }

  function appendMessage(role, content) {
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.innerHTML = content;
    document.getElementById('messages').appendChild(div);
    div.scrollIntoView({ behavior: 'smooth' });
    return div;
  }

  document.getElementById('user-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') sendMessage();
  });
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/chat", methods=["POST"])
def chat_endpoint():
    data = request.get_json()
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id")

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    try:
        result = chat(user_message, session_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("Starting Aster & Row Support Agent...")
    print("Open http://localhost:5000 in your browser\n")
    app.run(debug=False, port=5000)