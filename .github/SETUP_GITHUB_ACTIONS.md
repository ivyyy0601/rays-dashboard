# GitHub Actions 设置 (一次性, 15 分钟)

让 GitHub 帮你周四自动抓 AAII + 工作日傍晚抓 Barchart, 推到 Hetzner 服务器.

## Step 1: 创建 GitHub repo (如果还没有)

```bash
cd /Users/chenjiexin/Desktop/rays
git init
git add .
git commit -m "Initial dashboard"

# 在 github.com 创建一个 PRIVATE repo (因为含 email_config.json + tushare_token.json)
# 名字: rays-dashboard

git remote add origin git@github.com:YOUR_USERNAME/rays-dashboard.git
git branch -M main
git push -u origin main
```

⚠️ **必须 PRIVATE repo** — 因为 `.github/workflows/` 之外的代码可能含敏感配置.
但 .gitignore 已经把 email_config.json 和 tushare_token.json 排除了, 所以即使 public 也不会推这些.

## Step 2: 在 GitHub 加 2 个 Secrets

1. 浏览器打开你 repo → Settings → Secrets and variables → Actions
2. 点 "New repository secret"
3. 加 2 个:

### Secret 1: `SERVER_IP`
   - Name: `SERVER_IP`
   - Value: `91.98.37.33`

### Secret 2: `SSH_PRIVATE_KEY`
本机终端跑:
```bash
cat ~/.ssh/id_ed25519
```
复制全部输出 (包括 `-----BEGIN OPENSSH PRIVATE KEY-----` 到 `-----END OPENSSH PRIVATE KEY-----`)
   - Name: `SSH_PRIVATE_KEY`
   - Value: 粘贴你的私钥

## Step 3: 立刻测试

在 GitHub repo 页面:
1. 点 "Actions" 标签
2. 选 "Sync AAII + Barchart to Hetzner"
3. 右边 "Run workflow" → Run

跑 1-2 分钟. 看日志:
- 如果 AAII fetch 显示 ~1MB xls → ✅ GitHub IP 能过 Imperva
- 如果显示 "tiny file" → ❌ GitHub IP 也被 block (那只能手动上传或付费代理)

## Step 4: 如果成功

- 每周四 11:30 AM ET 自动抓 AAII → 推 Hetzner
- 每个工作日 7:00 PM ET 自动抓 Barchart → 推 Hetzner
- 你完全不用管

## 流程对比

```
之前 (Hetzner cron 自己抓):
  AAII: 403 ❌
  Barchart: 403 ❌
  你: 每周手动上传 AAII

GitHub Actions 介入后:
  GitHub Actions (Microsoft Azure IP):
    周四 11:30 ET  →  抓 AAII  →  scp 推 Hetzner
    工作日 19:00 ET → 抓 Barchart → scp 推 Hetzner
  Hetzner:
    19:40 ET cron 跑 → 用最新 AAII + Barchart
    20:00 ET 邮件
  你: 完全不用管 ✅
```
