#!/usr/bin/env node
/**
 * 飞书 Bot - 发动机排放标准知识库
 * 调用百炼 API 回答问题
 */

const https = require('https');
const http = require('http');

const API_KEY = 'sk-c74fa97c63254dcd973513a760ba029c';
const FEISHU_APP_ID = 'cli_a924cb8cc4381bc3';
const FEISHU_APP_SECRET = 'wMPlOa9NmBE6rUGh0H48Rf8GhTnmkHPC';

// 调用百炼 API
function queryBailian(question) {
    return new Promise((resolve, reject) => {
        const postData = JSON.stringify({
            model: 'qwen-plus-latest',
            messages: [
                { role: 'user', content: question }
            ],
            temperature: 0.7,
            max_tokens: 2000
        });
        
        const options = {
            hostname: 'dashscope.aliyuncs.com',
            port: 443,
            path: '/compatible-mode/v1/chat/completions',
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${API_KEY}`,
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(postData)
            }
        };
        
        const req = https.request(options, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    const result = JSON.parse(data);
                    if (result.error) {
                        reject(new Error(result.error.message || 'API 错误'));
                    } else {
                        resolve(result.choices?.[0]?.message?.content || '无回答');
                    }
                } catch (e) {
                    reject(new Error('解析失败：' + e.message));
                }
            });
        });
        
        req.on('error', reject);
        req.write(postData);
        req.end();
    });
}

// 获取飞书 Access Token
function getFeishuToken() {
    return new Promise((resolve, reject) => {
        const postData = JSON.stringify({
            app_id: FEISHU_APP_ID,
            app_secret: FEISHU_APP_SECRET
        });
        
        const options = {
            hostname: 'open.feishu.cn',
            port: 443,
            path: '/open-apis/auth/v3/tenant_access_token/internal',
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(postData)
            }
        };
        
        const req = https.request(options, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    const result = JSON.parse(data);
                    if (result.code !== 0) {
                        reject(new Error(result.msg || '获取 Token 失败'));
                    } else {
                        resolve(result.tenant_access_token);
                    }
                } catch (e) {
                    reject(new Error('解析失败：' + e.message));
                }
            });
        });
        
        req.on('error', reject);
        req.write(postData);
        req.end();
    });
}

// 发送飞书消息
function sendFeishuMessage(token, receiveId, content) {
    return new Promise((resolve, reject) => {
        const postData = JSON.stringify({
            receive_id: receiveId,
            msg_type: 'text',
            content: JSON.stringify({ text: content })
        });
        
        const options = {
            hostname: 'open.feishu.cn',
            port: 443,
            path: '/open-apis/im/v1/messages?receive_id_type=open_id',
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(postData)
            }
        };
        
        const req = https.request(options, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    const result = JSON.parse(data);
                    if (result.code !== 0) {
                        reject(new Error(result.msg || '发送消息失败'));
                    } else {
                        resolve(result.data);
                    }
                } catch (e) {
                    reject(new Error('解析失败：' + e.message));
                }
            });
        });
        
        req.on('error', reject);
        req.write(postData);
        req.end();
    });
}

// 处理消息
async function handleMessage(event) {
    const { message, sender } = event;
    
    if (!message || message.message_type !== 'text') {
        return;
    }
    
    const question = message.content.text;
    console.log(`收到问题：${question}`);
    
    try {
        // 调用百炼 API
        const answer = await queryBailian(question);
        console.log(`回答：${answer.substring(0, 100)}...`);
        
        // 发送回复
        const token = await getFeishuToken();
        await sendFeishuMessage(token, sender.sender_id.open_id, answer);
        console.log('回复已发送');
    } catch (error) {
        console.error('处理消息失败：', error.message);
    }
}

// 启动 HTTP 服务器接收飞书事件
const server = http.createServer(async (req, res) => {
    if (req.method === 'POST' && req.url === '/webhook') {
        let body = '';
        req.on('data', chunk => body += chunk);
        req.on('end', async () => {
            try {
                const data = JSON.parse(body);
                
                // 处理 challenge
                if (data.type === 'url_verification') {
                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ challenge: data.challenge }));
                    return;
                }
                
                // 处理消息事件
                if (data.header?.event_type === 'im.message.receive_v1') {
                    await handleMessage(data.event);
                }
                
                res.writeHead(200);
                res.end('OK');
            } catch (error) {
                console.error('处理请求失败：', error);
                res.writeHead(500);
                res.end('Error');
            }
        });
    } else {
        res.writeHead(404);
        res.end('Not Found');
    }
});

const PORT = process.env.PORT || 3001;
server.listen(PORT, () => {
    console.log(`🚀 飞书 Bot 已启动`);
    console.log(`📱 监听端口：${PORT}`);
    console.log(`📡 Webhook URL：http://localhost:${PORT}/webhook`);
});
