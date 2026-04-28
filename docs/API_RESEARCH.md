# 🔬 阿里云百炼 API 能力调研报告

**调研人：** 户部尚书  
**调研时间：** 2026-04-28  
**调研对象：** 阿里云百炼（DashScope）应用 API  
**应用信息：**
- APP ID: `ff29dd075f354870b58251db9dea135d`
- 应用名称: 发动机排放标准
- 选用模型: qwen-plus-latest
- API Key: `sk-c74fa97c63254dcd973513a760ba029c`

---

## 一、API 端点总览

百炼提供 **两套 API 体系**：

| 体系 | 端点 | 协议 | 适用场景 |
|------|------|------|----------|
| **兼容模式** | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | OpenAI 兼容 | 直接调用模型，无需应用配置 |
| **应用模式** | `https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion` | DashScope 原生 | 调用已发布的应用（含知识库 RAG） |

### 测试结果

| API 端点 | 状态 | 响应时间 | 备注 |
|----------|------|----------|------|
| 兼容模式 | ✅ 正常 | ~0.7s | 5 次测试全部 HTTP 200 |
| 应用模式 | ❌ 超时 | >30s | 多次测试均超时，可能应用未正确发布或知识库未关联 |

---

## 二、搜索能力调研

### 2.1 兼容模式 API

**是否支持搜索：** ❌ 不直接支持

兼容模式是标准的 Chat Completions 接口，本质是直接调用大模型，**不包含知识库检索能力**。

```bash
# 兼容模式调用示例（无知识库检索）
curl -X POST "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-plus-latest",
    "messages": [{"role": "user", "content": "国六NOx限值"}]
  }'
```

**响应示例：**
```json
{
  "choices": [{
    "finish_reason": "stop",
    "index": 0,
    "message": {
      "content": "国六阶段ⅥB 的 NOx 限值为...",
      "role": "assistant"
    }
  }],
  "id": "chatcmpl-xxx",
  "model": "qwen-plus-latest",
  "usage": {
    "completion_tokens": 749,
    "prompt_tokens": 22,
    "total_tokens": 771
  }
}
```

### 2.2 应用模式 API（含知识库 RAG）

**是否支持搜索：** ✅ 支持（通过知识库配置）

应用模式调用的是百炼控制台中配置的**智能体应用**，如果应用关联了知识库，则自动启用 RAG 检索。

```bash
# 应用模式调用示例（含知识库检索）
curl -X POST "https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "prompt": "国六NOx限值"
    },
    "parameters": {},
    "debug": {}
  }'
```

**请求参数说明：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `input.prompt` | string | ✅ | 用户问题 |
| `input.session_id` | string | ❌ | 多轮对话会话 ID |
| `input.file_list` | array | ❌ | 文件问答（文档/图片/音视频 URL） |
| `input.image_list` | array | ❌ | 视觉理解（图片 URL 或 Base64） |
| `input.biz_params` | object | ❌ | 自定义业务参数 |
| `parameters.incremental_output` | bool | ❌ | 是否增量输出（流式时常用） |
| `debug` | object | ❌ | 调试信息开关 |

**⚠️ 当前问题：** 应用模式 API 持续超时（>30s），可能原因：
1. 应用未正确发布到 API 渠道
2. 知识库未正确关联到应用
3. 应用配置有问题

**建议：** 登录百炼控制台检查应用发布状态和知识库关联情况。

### 2.3 搜索方式对比

| 搜索方式 | 兼容模式 | 应用模式 |
|----------|----------|----------|
| 关键词搜索 | ❌ 不支持 | ✅ 知识库支持 |
| 语义搜索 | ❌ 不支持 | ✅ 知识库支持（向量检索） |
| 混合搜索 | ❌ 不支持 | ✅ 知识库支持 |
| 自定义检索 | ✅ 可自行实现 RAG | ✅ 可在应用中配置 |

---

## 三、引用数据（RAG 来源）调研

### 3.1 兼容模式

**是否返回引用数据：** ❌ 不返回

兼容模式只返回模型生成的文本，不包含来源文档信息。

### 3.2 应用模式

**是否返回引用数据：** ✅ 支持（通过 debug 参数）

应用模式在开启 `debug` 后，响应中会包含知识库检索的详细信息。

**预期响应结构（含 debug）：**
```json
{
  "output": {
    "text": "根据 GB17691-2018 表1，国六ⅥB 的 NOx 限值为...",
    "finish_reason": "stop",
    "session_id": "xxx",
    "reject_status": false
  },
  "usage": {
    "models": [{
      "input_tokens": 103,
      "model_id": "qwen-plus-latest",
      "output_tokens": 304
    }]
  },
  "request_id": "xxx",
  "debug": {
    "reference": [
      {
        "doc_id": "xxx",
        "doc_name": "GB17691-2018.pdf",
        "content": "根据 GB17691-2018 表1...",
        "score": 0.95
      }
    ]
  }
}
```

### 3.3 引用数据格式

根据官方文档，应用模式的 debug 响应中可能包含：

| 字段 | 说明 |
|------|------|
| `debug.reference` | 引用来源列表 |
| `debug.reference[].doc_id` | 文档 ID |
| `debug.reference[].doc_name` | 文档名称 |
| `debug.reference[].content` | 命中的原文片段 |
| `debug.reference[].score` | 检索相似度分数 |
| `debug.reference[].page_number` | 页码（如有） |

> ⚠️ 由于应用模式 API 超时，未能实际验证引用数据格式。建议在应用恢复正常后进一步测试。

---

## 四、多轮对话能力

### 4.1 兼容模式

**是否支持多轮对话：** ✅ 支持（通过 messages 数组）

```bash
# 多轮对话示例
curl -X POST "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-plus-latest",
    "messages": [
      {"role": "user", "content": "我是一名汽车工程师"},
      {"role": "assistant", "content": "很高兴认识您！..."},
      {"role": "user", "content": "国六NOx限值是多少？"}
    ]
  }'
```

**测试结果：** ✅ 正常，响应正常返回

### 4.2 应用模式

**是否支持多轮对话：** ✅ 支持（通过 session_id）

```bash
# 第一轮对话（无需 session_id）
curl -X POST "https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{"input": {"prompt": "你是谁？"}, "parameters": {}}'

# 响应中包含 session_id
# {"output": {"text": "...", "session_id": "4f8ef7233dc641aba496cb201fa59f8c"}, ...}

# 第二轮对话（携带 session_id）
curl -X POST "https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "prompt": "你有什么技能？",
      "session_id": "4f8ef7233dc641aba496cb201fa59f8c"
    },
    "parameters": {}
  }'
```

**会话管理规则：**

| 规则 | 说明 |
|------|------|
| 首次请求 | 不传 session_id，响应自动生成 |
| 后续请求 | 携带上一次响应的 session_id |
| 有效期 | 最后一次请求后 **1 小时**内有效 |
| 过期处理 | 过期后需重新发起对话（不传 session_id） |

### 4.3 多轮对话对比

| 特性 | 兼容模式 | 应用模式 |
|------|----------|----------|
| 实现方式 | messages 数组 | session_id |
| 上下文管理 | 客户端维护 | 服务端维护 |
| 上下文长度 | 受模型最大 token 限制 | 服务端自动管理 |
| 有效期 | 无限制（只要不超 token） | 1 小时 |
| 推荐场景 | 简单对话、自定义上下文 | 复杂应用、知识库问答 |

---

## 五、限流策略

### 5.1 官方文档说明

根据阿里云百炼官方文档，限流策略如下：

| 限制类型 | 说明 | 默认值 |
|----------|------|--------|
| QPS（每秒请求数） | 按 API Key 维度限制 | 因模型而异，通常 5-50 QPS |
| TPM（每分钟 Token 数） | 按 API Key + 模型维度限制 | 因模型而异 |
| 并发连接数 | 单 API Key 最大并发 | 通常 10-100 |

> ⚠️ 具体数值因模型和账号等级不同而异，建议在百炼控制台查看当前账号的配额。

### 5.2 实测结果

| 测试项 | 结果 |
|--------|------|
| 连续 5 次请求 | ✅ 全部成功，无限流 |
| 平均响应时间 | ~0.7s |
| 响应大小 | ~329 bytes |

### 5.3 限流错误码

当触发限流时，API 返回 HTTP 429：

```json
{
  "error": {
    "message": "Rate limit exceeded",
    "type": "rate_limit_exceeded",
    "code": "rate_limit_exceeded"
  }
}
```

### 5.4 建议

1. **实现重试机制**：遇到 429 时使用指数退避重试
2. **监控配额**：在百炼控制台查看当前配额使用情况
3. **申请提额**：如需要更高 QPS，可联系阿里云申请提额
4. **缓存结果**：对相同问题缓存响应，减少重复请求

---

## 六、错误码说明

### 6.1 常见错误码

| HTTP 状态码 | 错误码 | 说明 | 解决方案 |
|-------------|--------|------|----------|
| 400 | `invalid_api_key` | API Key 无效 | 检查 API Key 是否正确 |
| 400 | `Arrearage` | 账号欠费 | 充值后重试 |
| 400 | `data_inspection_failed` | 内容安全拦截 | 修改输入内容后重试 |
| 400 | `InvalidParameter` | 参数错误 | 检查请求参数格式 |
| 429 | `rate_limit_exceeded` | 限流 | 降低请求频率，使用退避重试 |
| 500 | `InternalError` | 服务端错误 | 稍后重试 |
| 503 | `ServiceUnavailable` | 服务不可用 | 稍后重试 |

### 6.2 错误响应格式

**兼容模式：**
```json
{
  "error": {
    "message": "Incorrect API key provided...",
    "type": "invalid_request_error",
    "param": null,
    "code": "invalid_api_key"
  },
  "request_id": "82516e9f-4bb5-9679-9600-780acefd424f"
}
```

**应用模式：**
```json
{
  "request_id": "xxx",
  "code": 400,
  "message": "Incorrect API key provided..."
}
```

### 6.3 测试验证

| 测试场景 | 预期结果 | 实际结果 |
|----------|----------|----------|
| 正确 API Key | HTTP 200 | ✅ HTTP 200 |
| 错误 API Key | HTTP 400 + invalid_api_key | ✅ HTTP 400 + invalid_api_key |
| 超时（应用模式） | HTTP 504 或超时 | ❌ 持续超时 >30s |

---

## 七、其他能力

### 7.1 流式输出（SSE）

**支持情况：** ✅ 两套 API 均支持

**应用模式流式输出：**
```bash
curl -X POST "https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -H "X-DashScope-SSE: enable" \
  -d '{
    "input": {"prompt": "你是谁？"},
    "parameters": {"incremental_output": true}
  }'
```

**兼容模式流式输出：**
```bash
curl -X POST "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-plus-latest",
    "messages": [{"role": "user", "content": "你是谁？"}],
    "stream": true
  }'
```

### 7.2 文件问答

**支持情况：** ✅ 仅应用模式支持

通过 `input.file_list` 传入文件 URL，支持文档、图片、音视频。

```bash
curl -X POST "https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "prompt": "一句话总结文件内容",
      "file_list": ["https://example.com/document.pdf"]
    }
  }'
```

### 7.3 视觉理解

**支持情况：** ✅ 仅应用模式支持（需配置视觉模型）

通过 `input.image_list` 传入图片 URL 或 Base64 Data URL。

```bash
curl -X POST "https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion" \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "prompt": "这张图片里有什么？",
      "image_list": ["https://example.com/image.jpg"]
    }
  }'
```

### 7.4 自定义参数（biz_params）

**支持情况：** ✅ 仅应用模式支持

通过 `input.biz_params` 传递自定义业务参数，用于插件调用等场景。

```json
{
  "input": {
    "prompt": "查询排放标准",
    "biz_params": {
      "user_defined_params": {
        "PLUGIN_ID": {
          "param1": "value1"
        }
      }
    }
  }
}
```

---

## 八、能力对比总结

| 能力 | 兼容模式 | 应用模式 |
|------|----------|----------|
| 直接调用模型 | ✅ | ✅ |
| 知识库 RAG | ❌ | ✅ |
| 关键词搜索 | ❌ | ✅ |
| 语义搜索 | ❌ | ✅ |
| 引用数据 | ❌ | ✅（debug 模式） |
| 多轮对话 | ✅（messages） | ✅（session_id） |
| 流式输出 | ✅ | ✅ |
| 文件问答 | ❌ | ✅ |
| 视觉理解 | ❌ | ✅ |
| 自定义参数 | ❌ | ✅ |
| 响应速度 | ~0.7s | 取决于知识库检索 |
| 协议 | OpenAI 兼容 | DashScope 原生 |

---

## 九、当前问题与风险

### 9.1 应用模式 API 超时

**现象：** `/api/v1/apps/{APP_ID}/completion` 持续超时（>30s）

**可能原因：**
1. 应用未正确发布到 API 渠道
2. 知识库未正确关联到应用
3. 应用配置中的模型不可用
4. 百炼服务端问题

**建议排查步骤：**
1. 登录百炼控制台 → 应用管理 → 检查应用发布状态
2. 检查知识库是否已创建并关联到应用
3. 尝试在控制台「在线测试」中验证应用是否正常
4. 检查应用的模型配置是否为 qwen-plus-latest

### 9.2 兼容模式无知识库能力

**现象：** 当前 `app.py` 使用的是兼容模式，无法利用知识库 RAG

**影响：**
- 回答依赖模型训练数据，无法引用标准原文
- 无法保证回答的准确性和可追溯性
- 不符合项目「引用来源」的要求

**建议：** 修复应用模式 API 后，切换到应用模式调用

---

## 十、使用建议

### 10.1 短期方案（兼容模式可用）

当前兼容模式 API 正常工作，可以继续使用，但需注意：

1. **增强 Prompt**：在 system prompt 中注入关键标准数据
2. **手动 RAG**：在应用层实现文档检索，将检索结果注入 prompt
3. **缓存机制**：对常见问题缓存响应，提高响应速度

### 10.2 中期方案（修复应用模式）

1. **排查应用配置**：登录百炼控制台检查应用发布状态
2. **验证知识库**：确认知识库已创建并包含标准文档
3. **切换 API**：将 `app.py` 从兼容模式切换到应用模式
4. **解析引用**：在响应中解析 debug.reference 字段，展示引用来源

### 10.3 长期方案（完整 RAG 能力）

1. **知识库优化**：
   - 对标准文档进行分块处理
   - 配置合适的检索参数（top_k、阈值等）
   - 定期更新知识库内容

2. **应用增强**：
   - 配置插件实现自定义功能
   - 启用流式输出提升用户体验
   - 实现多轮对话上下文管理

3. **监控与优化**：
   - 监控 API 调用量和响应时间
   - 分析用户查询日志，优化知识库内容
   - 定期评估回答质量

---

## 十一、官方文档参考

| 文档 | URL |
|------|-----|
| 应用调用 - DashScope API | https://help.aliyun.com/zh/model-studio/application-call/ |
| 新版智能体应用 API | https://help.aliyun.com/zh/model-studio/new-agent-application-api-reference |
| 工作流与旧版智能体 API | https://help.aliyun.com/zh/model-studio/agent-and-workflow-application-api-reference |
| 错误码参考 | https://help.aliyun.com/zh/model-studio/error-code |
| 百炼控制台 | https://bailian.console.aliyun.com/ |

---

## 十二、调研结论

1. **搜索能力：** 应用模式支持知识库 RAG（关键词+语义搜索），兼容模式不支持
2. **引用数据：** 应用模式支持（通过 debug 参数），兼容模式不支持
3. **多轮对话：** 两套 API 均支持，实现方式不同
4. **限流策略：** 按 API Key 维度限制，具体 QPS 因模型而异
5. **错误码：** 标准化错误响应，兼容模式为 OpenAI 格式，应用模式为 DashScope 格式
6. **当前风险：** 应用模式 API 超时，需尽快排查修复

**下一步行动：**
- [ ] 排查应用模式 API 超时问题
- [ ] 验证知识库是否已正确配置
- [ ] 测试引用数据格式
- [ ] 根据测试结果更新本报告

---

_报告生成时间：2026-04-28 10:30 GMT+8_  
_户部尚书 敬上_
