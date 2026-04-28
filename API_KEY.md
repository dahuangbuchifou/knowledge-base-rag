# 🔑 阿里云百炼 API Key

**应用：** 发动机排放标准
**创建时间：** 2026-04-26 22:16
**用途：** 网页版 + 飞书集成

---

## API Key

```
sk-c74fa97c63254dcd973513a760ba029c
```

---

## 应用信息

| 字段 | 值 |
|------|-----|
| **应用 ID** | `ff29dd075f354870b58251db9dea135d` |
| **应用名称** | 发动机排放标准 |
| **状态** | ✅ 已发布 |
| **选用模型** | qwen-plus-latest |

---

## 使用示例

### curl 调用
```bash
curl https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions \
  -H "Authorization: Bearer sk-c74fa97c63254dcd973513a760ba029c" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-plus-latest",
    "messages": [
      {"role": "user", "content": "国六阶段ⅥB 的 NOx 限值是多少？"}
    ]
  }'
```

---

_最后更新：2026-04-26 22:16_
