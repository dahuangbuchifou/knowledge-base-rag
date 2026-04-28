# 📚 汽车法规知识库技能

**技能名称：** bailian-kb
**功能：** 在 OpenClaw 中调用阿里云百炼知识库

---

## 安装

将本技能文件夹复制到 OpenClaw skills 目录：

```bash
cp -r bailian-kb ~/.openclaw/skills/
```

---

## 使用

### 方式 1：直接对话
```
/知识库 国六阶段ⅥB 的 NOx 限值是多少？
```

### 方式 2：调用脚本
```bash
python scripts/query-openclaw.py "WHTC+WHSC 测试循环怎么理解？"
```

---

## 配置

在 `config.json` 中配置 API Key：

```json
{
  "api_key": "sk-c74fa97c63254dcd973513a760ba029c",
  "model": "qwen-max",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
}
```

---

## 依赖

```bash
pip install openai
```

---

_创建时间：2026-04-26_
