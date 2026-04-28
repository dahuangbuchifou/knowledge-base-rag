# 🔒 安全性修正记录 - 2026-04-28

## 基本信息
- **分类：** E - 项目文档 / E2 - 技术文档
- **标签：** 安全, HTTPS, Basic Auth, Nginx
- **负责人：** 大黄
- **更新时间：** 2026-04-28
- **版本：** v1.0

_本次修正解决传输层风险和访问控制风险_

---

## 📋 修正内容

### 1. HTTPS 加密

**问题：** 浏览器报"不安全"，HTTP 明文传输

**解决方案：**
- 生成自签名 SSL 证书
- Nginx 配置 HTTPS 反向代理
- HTTP → HTTPS 自动重定向

**配置文件：** `/etc/nginx/conf.d/knowledge-base.conf`

**证书位置：**
- 证书：`/etc/nginx/ssl/server.crt`
- 私钥：`/etc/nginx/ssl/server.key`

**访问地址：** https://dahuangbuchirou.xyz

---

### 2. Basic Auth 访问控制

**问题：** 无身份验证，任何人知道 IP 就能访问

**解决方案：**
- 安装 httpd-tools
- 创建 `.htpasswd` 文件
- Nginx 配置 Basic Auth

**访问凭证：**
- 用户名：`admin`
- 密码：`KbAdmin2026!`（请修改）

**配置文件位置：** `/etc/nginx/.htpasswd`

---

### 3. API Key 环境变量化

**问题：** API Key 硬编码在 server.js 中，存在泄露风险

**解决方案：**
- 创建 `.env` 文件存储敏感配置
- `.env` 加入 `.gitignore`
- server.js 从 `.env` 读取配置

**文件位置：** `web/.env`

**配置内容：**
```bash
API_KEY=sk-c74fa97c63254dcd973513a760ba029c
APP_ID=ff29dd075f354870b58251db9dea135d
PORT=3000
```

---

### 4. 安全头配置

**配置内容：**
```nginx
add_header X-Content-Type-Options nosniff;
add_header X-Frame-Options SAMEORIGIN;
add_header X-XSS-Protection "1; mode=block";
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

---

## ✅ 测试验证

```bash
# 测试 HTTPS + Basic Auth
curl -sk -u admin:KbAdmin2026! https://dahuangbuchirou.xyz/api/health
# 返回：{"status":"ok","app_id":"ff29dd075f354870b58251db9dea135d"}
```

---

## 📝 后续建议

1. **修改默认密码** - 当前密码为临时密码，建议修改
2. **考虑正式 SSL 证书** - 自签名证书浏览器会告警，建议用 Let's Encrypt 免费证书
3. **定期更新密码** - 建议每 3 个月更换一次
4. **访问日志监控** - 建议配置访问日志，监控异常访问

---

_执行时间：2026-04-28 09:30_
_执行人：婉儿 + 工部_
