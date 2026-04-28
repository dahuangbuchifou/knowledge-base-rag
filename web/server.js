const express = require('express');
const https = require('https');
const fs = require('fs');
const path = require('path');

// 加载 .env 文件
const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) {
    fs.readFileSync(envPath).toString().split('\n').forEach(line => {
        const match = line.match(/^([^=]+)=(.*)$/);
        if (match && !line.startsWith('#')) {
            process.env[match[1].trim()] = match[2].trim();
        }
    });
}

// 百炼知识库配置（从 .env 读取）
const API_KEY = process.env.API_KEY;
const APP_ID = process.env.APP_ID; // 发动机排放标准应用 ID
const PORT = process.env.PORT || 3000;

const app = express();
app.use(express.json());
app.use(express.static('public'));

// ========== SSE 流式查询接口 ==========
// 使用百炼应用模式 API 的流式输出，解决复杂查询超时问题
app.post('/api/query', (req, res) => {
    const { question } = req.body;
    
    if (!question) {
        return res.json({ error: '请输入问题' });
    }
    
    const postData = JSON.stringify({
        input: {
            prompt: question
        },
        parameters: {
            has_thoughts: false
        },
        stream: true  // 启用流式输出
    });
    
    const options = {
        hostname: 'dashscope.aliyuncs.com',
        port: 443,
        path: `/api/v1/apps/${APP_ID}/completion`,
        method: 'POST',
        headers: {
            'Authorization': `Bearer ${API_KEY}`,
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(postData),
            'X-DashScope-SSE': 'enable'  // 启用 SSE 流式输出
        }
    };
    
    // 设置 SSE 响应头
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no');
    
    const clientReq = https.request(options, (response) => {
        let buffer = '';
        
        response.on('data', (chunk) => {
            buffer += chunk.toString();
            
            // 处理 SSE 格式：逐行解析 data: 开头的行
            const lines = buffer.split('\n');
            buffer = lines.pop() || ''; // 最后一行可能不完整，保留到下次
            
            for (const line of lines) {
                const trimmed = line.trim();
                if (trimmed.startsWith('data:')) {
                    try {
                        const data = JSON.parse(trimmed.slice(5));
                        
                        // 检查是否有错误
                        if (data.code) {
                            res.write(`data: ${JSON.stringify({ error: data.message || 'API 错误', code: data.code })}\n\n`);
                            res.end();
                            clientReq.destroy();
                            return;
                        }
                        
                        // 流式输出：逐字发送文本片段
                        if (data.output && data.output.text !== undefined) {
                            res.write(`data: ${JSON.stringify({ text: data.output.text, finish_reason: data.output.finish_reason, session_id: data.output.session_id })}\n\n`);
                            
                            // 如果是最后一条消息，关闭连接
                            if (data.output.finish_reason === 'stop') {
                                res.write('data: [DONE]\n\n');
                                res.end();
                            }
                        }
                    } catch (e) {
                        // 忽略解析错误
                    }
                }
            }
        });
        
        response.on('end', () => {
            // 处理剩余的 buffer
            if (buffer.trim()) {
                try {
                    const data = JSON.parse(buffer.trim().replace(/^data:\s*/, ''));
                    if (data.output && data.output.text !== undefined) {
                        res.write(`data: ${JSON.stringify({ text: data.output.text, finish_reason: data.output.finish_reason, session_id: data.output.session_id })}\n\n`);
                    }
                } catch (e) {}
            }
            if (!res.writableEnded) {
                res.write('data: [DONE]\n\n');
                res.end();
            }
        });
    });
    
    clientReq.on('error', (e) => {
        if (!res.writableEnded) {
            res.write(`data: ${JSON.stringify({ error: '请求失败：' + e.message })}\n\n`);
            res.end();
        }
    });
    
    // 超时设置：120 秒（流式输出一般不会超时，但作为安全边界）
    clientReq.setTimeout(120000, () => {
        if (!res.writableEnded) {
            res.write('data: ' + JSON.stringify({ error: '请求超时（120 秒），请重试或简化问题' }) + '\n\n');
            res.end();
        }
        clientReq.destroy();
    });
    
    clientReq.write(postData);
    clientReq.end();
});

// ========== 兼容旧版非流式查询（降级方案） ==========
// 保留原接口用于不支持 SSE 的客户端
app.post('/api/query-sync', (req, res) => {
    const { question } = req.body;
    
    if (!question) {
        return res.json({ error: '请输入问题' });
    }
    
    const postData = JSON.stringify({
        input: {
            prompt: question
        },
        parameters: {
            has_thoughts: false
        }
    });
    
    const options = {
        hostname: 'dashscope.aliyuncs.com',
        port: 443,
        path: `/api/v1/apps/${APP_ID}/completion`,
        method: 'POST',
        headers: {
            'Authorization': `Bearer ${API_KEY}`,
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(postData)
        }
    };
    
    const clientReq = https.request(options, (response) => {
        let data = '';
        response.on('data', chunk => data += chunk);
        response.on('end', () => {
            try {
                const result = JSON.parse(data);
                if (result.code) {
                    res.json({ error: result.message || 'API 错误', code: result.code });
                } else if (result.output && result.output.text) {
                    res.json({ 
                        answer: result.output.text,
                        session_id: result.output.session_id || ''
                    });
                } else {
                    res.json({ error: '无回答', raw: result });
                }
            } catch (e) {
                res.json({ error: '解析失败：' + e.message, raw: data });
            }
        });
    });
    
    clientReq.on('error', (e) => {
        res.json({ error: '请求失败：' + e.message });
    });
    
    // 超时设置：90 秒（比原来 60 秒更长）
    clientReq.setTimeout(90000, () => {
        clientReq.destroy();
        res.json({ error: '请求超时（90 秒），请重试或简化问题' });
    });
    
    clientReq.write(postData);
    clientReq.end();
});

// ========== 用户反馈接口 ==========
const FEEDBACK_LOG = path.join(__dirname, 'feedback.log');

app.post('/api/feedback', (req, res) => {
    const { question, answer, rating, comment } = req.body;
    
    // 基本校验：rating 必填
    if (!rating || !['👍', '👎', '💡'].includes(rating)) {
        return res.status(400).json({ error: '无效的评分，rating 必须是 👍 / 👎 / 💡' });
    }
    
    // 构造反馈记录
    const record = {
        timestamp: new Date().toISOString(),
        question: question || '',
        answer: answer || '',
        rating: rating,
        comment: comment || ''
    };
    
    // 追加写入日志文件（JSON Lines 格式，每行一条 JSON）
    try {
        fs.appendFileSync(FEEDBACK_LOG, JSON.stringify(record) + '\n', 'utf8');
        res.json({ success: true });
    } catch (e) {
        console.error('反馈写入失败：', e.message);
        res.status(500).json({ error: '反馈保存失败：' + e.message });
    }
});

// 健康检查
app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', app_id: APP_ID, mode: 'streaming' });
});

app.listen(PORT, '0.0.0.0', () => {
    console.log(`🚀 发动机排放标准知识库已启动`);
    console.log(`📱 访问地址：http://localhost:${PORT}`);
    console.log(`🌐 外网访问：http://47.104.130.57:${PORT}`);
    console.log(`📚 知识库应用 ID: ${APP_ID}`);
    console.log(`⚡ 模式：SSE 流式输出`);
    console.log(`按 Ctrl+C 停止服务`);
});
