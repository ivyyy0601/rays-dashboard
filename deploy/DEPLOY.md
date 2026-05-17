# Hetzner 部署手册

完整部署 = **3 阶段, 30 分钟**:
1. 注册 Hetzner + 开服务器 (10 分钟)
2. 服务器初始化 (10 分钟)
3. 上传代码 + 启动 (10 分钟)

---

## 阶段 1: 注册 Hetzner + 开服务器 (10 分钟)

### 1.1 注册账号
1. 打开 https://accounts.hetzner.com/signUp
2. 用邮箱注册 (需要信用卡, 但只在月底扣)

### 1.2 创建项目
1. 登录 Hetzner Cloud Console: https://console.hetzner.cloud/
2. 点击 "+ New project", 名字填 "Rays Dashboard"

### 1.3 创建服务器
1. 在项目里点 "+ Add Server"
2. **Location**: 选 **Ashburn, VA (USA)** (离美股交易所最近, 拉数据快)
3. **Image**: Ubuntu 22.04
4. **Type**: 选 **CX22** (€4.51/月, 2vCPU, 4GB RAM, 40GB SSD)
   - 不要选 CX11, 内存太小 Playwright 跑不动
5. **Networking**: 默认 IPv4 + IPv6
6. **SSH Keys**: 见下面的 1.4 步
7. **Name**: "rays-dashboard"
8. 点 "Create & Buy now"

### 1.4 添加 SSH Key (重要!)
**在你 Mac 终端**:
```bash
# 看是否已有 SSH key
ls ~/.ssh/id_*.pub

# 如果没有, 创建一个 (一直按回车跳过密码)
ssh-keygen -t ed25519

# 复制 public key
cat ~/.ssh/id_ed25519.pub
# 复制全部输出 (从 ssh-ed25519 开头)
```

回到 Hetzner 创建服务器页面:
- 点 "Add SSH Key"
- 粘贴你的 public key
- 名字写 "Mac"
- 保存

### 1.5 拿到服务器 IP
服务器创建后显示 IP, 比如 `5.78.xxx.xxx`. 记下来.

测试 SSH (在 Mac 终端):
```bash
ssh root@5.78.xxx.xxx
# 第一次会问 yes/no, 输 yes
# 应该直接进入服务器 (不用密码, 因为有 SSH key)
```

如果连接成功, 你会看到 Ubuntu 欢迎信息. 输 `exit` 退出.

---

## 阶段 2: 服务器初始化 (10 分钟)

**在 Mac 终端跑:**

### 2.1 上传 setup_server.sh 到服务器
```bash
cd /Users/chenjiexin/Desktop/rays
scp deploy/setup_server.sh root@YOUR_IP:/root/
```

### 2.2 SSH 进服务器跑初始化
```bash
ssh root@YOUR_IP
bash /root/setup_server.sh
```

这会自动:
- 装 Python 3.11, nginx, cron, Playwright 系统依赖
- 设时区为 America/New_York
- 创建 `rays` 用户和 `/opt/rays` 目录
- 配置防火墙 (开放 22/80/443)

跑完会显示 `✓ Server base setup complete!`. 不要关 SSH.

---

## 阶段 3: 上传代码 + 启动 (10 分钟)

### 3.1 在 Mac 终端 (开一个新终端窗口) 上传代码:
```bash
cd /Users/chenjiexin/Desktop/rays
bash deploy/upload_to_server.sh root@YOUR_IP
```

这会 rsync 上传所有代码 (包括你的 email_config.json + tushare_token.json).

### 3.2 在服务器 SSH 窗口跑 finalize:
```bash
bash /opt/rays/deploy/finalize.sh
```

这会:
- 装 Python 依赖 (yfinance, akshare, baostock, playwright, etc.)
- 下载 Chromium (Playwright 用)
- 创建 systemd 服务让 streamlit 后台跑
- 配置 nginx 反向代理 (80 端口转发到 Streamlit 8501)
- 安装每天 19:40 NY 时间的 cron

跑完显示 `✓ Deployment complete!`.

### 3.3 浏览器打开 dashboard
在你的浏览器打开:
```
http://YOUR_IP/
```

应该看到 Rays Dashboard! 🎉

### 3.4 立刻跑一次完整数据更新 (测试)
```bash
sudo -u rays /opt/rays/venv/bin/python /opt/rays/run_daily.py
```

跑 ~15 分钟, 完成后会发邮件给你.

---

## 验证 / 监控

### 看服务状态
```bash
systemctl status streamlit
# 应该看到 "active (running)"
```

### 看 streamlit 日志
```bash
tail -f /opt/rays/data/streamlit.log
```

### 看 cron 配置
```bash
sudo -u rays crontab -l
# 应该看到: 40 19 * * * /opt/rays/venv/bin/python /opt/rays/run_daily.py ...
```

### 看 cron 跑过的日志
```bash
tail -f /opt/rays/data/cron.log
```

### 重启 streamlit
```bash
systemctl restart streamlit
```

---

## 关闭你的 Mac

部署成功后, **你的 Mac 可以彻底关机**——服务器会自己跑.

Hetzner 服务器 24/7 在线, 每天 NY 时间 19:40 自动跑数据, 19:55 发邮件.

---

## 监控价钱

Hetzner CX22 是 **€4.51/月** = ~¥35/月.

按月底结算, 第一个月是按使用天数计费 (开 10 天就只扣 1/3).

---

## 加域名 (可选)

如果想用 `dashboard.your-domain.com` 访问, 不是 IP:
1. 买一个域名 (Cloudflare $9/年)
2. 在 DNS 加 A 记录指向服务器 IP
3. 改 nginx.conf 把 `server_name _;` 改成 `server_name dashboard.your-domain.com;`
4. 装 certbot 加 HTTPS:
```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d dashboard.your-domain.com
```
5. 完成. 现在 https://dashboard.your-domain.com 能访问.

---

## 故障排查

**streamlit 没起来:**
```bash
journalctl -u streamlit --no-pager -n 50
```

**数据没更新:**
```bash
cat /opt/rays/data/cron.log
```

**邮件没发:**
```bash
sudo -u rays /opt/rays/venv/bin/python /opt/rays/check_alerts.py
# 看输出里有没有 "Email sent" 或错误
```

**端口 80 不通:**
```bash
ufw status
nginx -t
systemctl status nginx
```
