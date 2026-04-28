# 🤖 汽车法规知识库 - OpenClaw 集成

在 OpenClaw 中直接调用汽车法规标准知识库

---

## 使用方法

### 方式 1：直接对话
```
/知识库 国六阶段ⅥB 的 NOx 限值是多少？
```

### 方式 2：使用脚本
```bash
python scripts/query-openclaw.py "WHTC+WHSC 测试循环怎么理解？"
```

---

## 配置

API Key 已配置在 `API_KEY.md`，无需额外设置。

---

## 集成到 OpenClaw 技能

将 `bailian_skill.py` 添加到 OpenClaw skills 目录，即可通过消息调用。

---

_集成时间：2026-04-26_
