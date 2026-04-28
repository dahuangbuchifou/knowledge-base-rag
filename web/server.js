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

// ========== 热门搜索词库 ==========
const POPULAR_SEARCHES = [
    'NOx 排放限值', 'PM 颗粒物', 'WHSC 测试循环', 'WHTC 测试',
    '国六阶段', 'OBD 诊断', 'CO 一氧化碳', 'HC 碳氢化合物',
    'PEMS 便携式排放', '耐久性要求', '排放质保期', '污染控制装置'
];

// ========== 搜索接口（关键词搜索） ==========
// 通过百炼 API 的 has_thoughts 功能，提取引用来源作为搜索结果
app.post('/api/search', (req, res) => {
    const { keyword } = req.body;
    
    if (!keyword || !keyword.trim()) {
        return res.json({ error: '请输入搜索关键词' });
    }
    
    // 构造搜索提示：让 API 返回相关标准内容和引用
    const searchPrompt = `请搜索以下关键词相关的发动机排放标准内容，并列出关键信息：${keyword.trim()}`;
    
    const postData = JSON.stringify({
        input: { prompt: searchPrompt },
        parameters: { has_thoughts: true }
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
                    return res.json({ error: result.message || '搜索失败', code: result.code });
                }
                
                const answer = result.output?.text || '未找到相关内容';
                // 从 thoughts 中提取引用
                const thoughts = result.output?.thoughts || [];
                const citations = [];
                
                for (const thought of thoughts) {
                    if (thought.citations && Array.isArray(thought.citations)) {
                        for (const c of thought.citations) {
                            citations.push({
                                doc_name: c.doc_name || c.title || '未知文档',
                                page_num: c.page_num || c.page || null,
                                content: c.content || c.snippet || '',
                                score: c.score || c.confidence || null
                            });
                        }
                    }
                }
                
                res.json({
                    keyword: keyword.trim(),
                    answer: answer,
                    citations: citations,
                    timestamp: new Date().toISOString()
                });
            } catch (e) {
                res.json({ error: '解析失败：' + e.message });
            }
        });
    });
    
    clientReq.on('error', (e) => {
        res.json({ error: '搜索请求失败：' + e.message });
    });
    
    clientReq.setTimeout(60000, () => {
        clientReq.destroy();
        res.json({ error: '搜索超时（60 秒）' });
    });
    
    clientReq.write(postData);
    clientReq.end();
});

// ========== 热门搜索词列表 ==========
app.get('/api/search/suggestions', (req, res) => {
    res.json({ suggestions: POPULAR_SEARCHES });
});

// ========== SSE 流式查询接口 ==========
// 使用百炼应用模式 API 的流式输出，解决复杂查询超时问题
// 开启 has_thoughts 以获取引用来源数据
app.post('/api/query', (req, res) => {
    const { question } = req.body;
    
    if (!question) {
        return res.json({ error: '请输入问题' });
    }
    
    const postData = JSON.stringify({
        input: { prompt: question },
        parameters: { has_thoughts: true },  // 开启引用来源
        stream: true
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
            'X-DashScope-SSE': 'enable'
        }
    };
    
    // SSE 响应头
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no');
    
    const clientReq = https.request(options, (response) => {
        let buffer = '';
        
        response.on('data', (chunk) => {
            buffer += chunk.toString();
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            
            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed.startsWith('data:')) continue;
                
                try {
                    const data = JSON.parse(trimmed.slice(5));
                    
                    if (data.code) {
                        res.write(`data: ${JSON.stringify({ error: data.message || 'API 错误', code: data.code })}\n\n`);
                        res.end();
                        clientReq.destroy();
                        return;
                    }
                    
                    // 提取文本
                    if (data.output && data.output.text !== undefined) {
                        res.write(`data: ${JSON.stringify({
                            text: data.output.text,
                            finish_reason: data.output.finish_reason,
                            session_id: data.output.session_id
                        })}\n\n`);
                        
                        if (data.output.finish_reason === 'stop') {
                            // 提取引用
                            const thoughts = data.output.thoughts || [];
                            const citations = [];
                            for (const thought of thoughts) {
                                if (thought.citations) {
                                    for (const c of thought.citations) {
                                        citations.push({
                                            doc_name: c.doc_name || c.title || '未知文档',
                                            page_num: c.page_num || c.page || null,
                                            content: c.content || c.snippet || '',
                                            score: c.score || c.confidence || null
                                        });
                                    }
                                }
                            }
                            
                            // 发送最终消息（含引用）
                            res.write(`data: ${JSON.stringify({
                                text: data.output.text,
                                citations: citations,
                                finish_reason: 'stop'
                            })}\n\n`);
                            res.write('data: [DONE]\n\n');
                            res.end();
                        }
                    }
                } catch (e) {
                    // 忽略解析错误
                }
            }
        });
        
        response.on('end', () => {
            if (buffer.trim()) {
                try {
                    const data = JSON.parse(buffer.trim().replace(/^data:\s*/, ''));
                    if (data.output?.text !== undefined && !res.writableEnded) {
                        res.write(`data: ${JSON.stringify({ text: data.output.text })}\n\n`);
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
    
    clientReq.setTimeout(120000, () => {
        if (!res.writableEnded) {
            res.write(`data: ${JSON.stringify({ error: '请求超时（120 秒），请重试或简化问题' })}\n\n`);
            res.end();
        }
        clientReq.destroy();
    });
    
    clientReq.write(postData);
    clientReq.end();
});

// ========== 兼容旧版非流式查询（降级方案） ==========
app.post('/api/query-sync', (req, res) => {
    const { question } = req.body;
    
    if (!question) {
        return res.json({ error: '请输入问题' });
    }
    
    const postData = JSON.stringify({
        input: { prompt: question },
        parameters: { has_thoughts: true }
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
                    return res.json({ error: result.message || 'API 错误', code: result.code });
                }
                
                const thoughts = result.output?.thoughts || [];
                const citations = [];
                for (const thought of thoughts) {
                    if (thought.citations) {
                        for (const c of thought.citations) {
                            citations.push({
                                doc_name: c.doc_name || c.title || '未知文档',
                                page_num: c.page_num || c.page || null,
                                content: c.content || c.snippet || '',
                                score: c.score || c.confidence || null
                            });
                        }
                    }
                }
                
                if (result.output?.text) {
                    res.json({
                        answer: result.output.text,
                        session_id: result.output.session_id || '',
                        citations: citations
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
    
    if (!rating || !['👍', '👎', '💡'].includes(rating)) {
        return res.status(400).json({ error: '无效的评分，rating 必须是 👍 / 👎 / 💡' });
    }
    
    const record = {
        timestamp: new Date().toISOString(),
        question: question || '',
        answer: answer || '',
        rating: rating,
        comment: comment || ''
    };
    
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
    res.json({ status: 'ok', app_id: APP_ID, mode: 'streaming', has_thoughts: true });
});

app.listen(PORT, '0.0.0.0', () => {
    console.log(`🚀 发动机排放标准知识库已启动`);
    console.log(`📱 访问地址：http://localhost:${PORT}`);
    console.log(`🌐 外网访问：http://47.104.130.57:${PORT}`);
    console.log(`📚 知识库应用 ID: ${APP_ID}`);
    console.log(`⚡ 模式：SSE 流式输出 + 引用来源`);
    console.log(`按 Ctrl+C 停止服务`);
});
