# 情绪看板：部署与运维

线上地址：**http://91.98.37.33/sentiment/**

| 项 | 值 |
|---|---|
| 服务器目录 | `/opt/rays`（属主 `rays` 用户） |
| systemd 服务 | `streamlit`，监听 `127.0.0.1:8501`，路径前缀 `/sentiment` |
| 反向代理 | nginx `/sentiment/`，配置在 `final/server/nginx.conf`（和 ETF 看板共用） |
| 定时任务 | systemd `rays-daily.timer`，每天**香港时间 08:00** 运行 `run_daily.py`（固定按香港时间，不受美国夏令时影响） |
| 外部数据同步 | GitHub Actions `.github/workflows/sync_aaii_barchart.yml` |
| Python | `/opt/rays/venv` |

## 本文件夹里的文件

| 文件 | 对应服务器上的位置 | 用途 |
|---|---|---|
| `streamlit.service` | `/etc/systemd/system/streamlit.service` | 常驻运行 Streamlit |
| `rays-daily.service` | `/etc/systemd/system/` | 每日数据更新 + 提醒邮件（跑一次就结束） |
| `rays-daily.timer` | `/etc/systemd/system/` | 每天 08:00 HKT 触发上面的服务 |
| `setup_server.sh` | — | **全新服务器**第一次初始化（装系统包、建 `rays` 用户、开防火墙） |
| `finalize.sh` | — | 代码上传后：装依赖、装 systemd 服务和每日定时器 |

---

## 日常：修改代码后上线

在 `final/` 目录下运行。先预览会上传哪些文件：

```bash
bash server/deploy.sh sentiment --dry-run
```

确认后正式部署（会自动备份并重启服务）：

```bash
bash server/deploy.sh sentiment
```

只会上传代码。服务器上的 `data/` 目录和三个密钥文件（`anthropic_key.json`、`email_config.json`、`tushare_token.json`）不会被覆盖。

## 每日数据流程

```
08:00 HKT  rays-daily.timer ─► run_daily.py
                   ├─ update_cache.py   指数快照：一次下载所有成分股，同时算出
                   │                      Tab 2 收盘价 / RSI / 成交量 / 成交额 → data/index_snapshot.json
                   │                      Tab 3 均线 Breadth / 涨跌家数        → data/breadth_cache.json
                   │                      Tab 1 VIX / GLD 日线                → data/market_series.json
                   │                    每个指数都用「最近一个完成的交易日」；
                   │                    08:00 HKT 正在交易的市场（东京、首尔）用前一个交易日
                   │                    记录 Put/Call 历史               → data/putcall_history.csv
                   └─ check_alerts.py   检查提醒条件 + Claude 生成每日简报 → 发邮件

GitHub Actions（服务器 IP 会被 AAII、Barchart 屏蔽，所以在 GitHub 上抓再推过来）
  周四 15:30 UTC        AAII 情绪 .xls     → /opt/rays/data/aaii_sentiment.xls
  周一到周五 23:00 UTC   Barchart $SPX P/C → /opt/rays/data/barchart_pc.json
  任何一步失败都会发邮件通知
```

GitHub Actions 需要在仓库 Settings → Secrets 里配置 `SSH_PRIVATE_KEY` 和 `SERVER_IP`。

页面**只读这些文件，不会在打开时去外部抓数据**（没有 Refresh 按钮），所以打开很快、所有人看到的数字一样。
需要手动重跑时，在服务器上运行 `systemctl start rays-daily`（约 15 分钟，跑完会发邮件）。

---

## 常用命令（在服务器上运行）

```bash
systemctl status streamlit                    # 服务状态
systemctl restart streamlit                   # 重启
tail -f /opt/rays/data/streamlit.log          # Streamlit 日志
tail -f /opt/rays/data/cron.log               # 每日任务日志
systemctl list-timers rays-daily.timer        # 下一次什么时候跑

# 立刻手动跑一次每日流程（约 15 分钟，跑完会发邮件）
systemctl start rays-daily

# 只测试提醒邮件
sudo -u rays /opt/rays/venv/bin/python /opt/rays/check_alerts.py
```

## 故障排查

| 现象 | 检查 |
|---|---|
| 页面打不开 / 502 | `systemctl status streamlit`，然后 `journalctl -u streamlit -n 50` |
| 页面能开但资源 404、一直转圈 | `streamlit.service` 里有没有 `--server.baseUrlPath sentiment`；nginx 的 `location /sentiment/` 有没有配 WebSocket 头 |
| 数据不更新 | `tail /opt/rays/data/cron.log`；`ls -l /opt/rays/data/` 看文件修改时间 |
| AAII / Put-Call 过期 | 去 GitHub 仓库的 Actions 页面看最近一次运行结果 |
| RSI & Volume 表出现 ⚠️ | 展开表下方「Data notes & sources」看原因；手动重建：`sudo -u rays /opt/rays/venv/bin/python /opt/rays/index_snapshot.py` |
| AI 侧栏不能用 | `/opt/rays/anthropic_key.json` 是否存在、key 是否有效 |

---

## 从零搭建（新服务器）

只有换服务器时才需要。

1. 在 Hetzner 开一台 Ubuntu 机器，添加你 Mac 的 SSH 公钥（`~/.ssh/id_ed25519.pub`）。
2. 上传并运行初始化脚本：
   ```bash
   scp deploy/setup_server.sh root@<IP>:/root/
   ```
   ```bash
   ssh root@<IP> 'bash /root/setup_server.sh'
   ```
3. 把 `final/server/common.sh` 里的 `SERVER` 改成新 IP（或者运行前设置 `SERVER=root@<IP>`），然后在 `final/` 下上传代码：
   ```bash
   bash server/deploy.sh sentiment
   ```
   第一次部署时服务还不存在，脚本最后一步重启会报错，这是正常的。
4. 手动上传密钥文件（deploy 脚本不会传密钥）：
   ```bash
   scp anthropic_key.json email_config.json tushare_token.json root@<IP>:/opt/rays/
   ```
5. 装依赖、systemd 服务和每日定时器：
   ```bash
   ssh root@<IP> 'bash /opt/rays/deploy/finalize.sh'
   ```
6. 安装 nginx 配置（见 `final/README.md` 的「修改 nginx 配置」一节）。
7. 生成第一份缓存：
   ```bash
   ssh root@<IP> 'sudo -u rays /opt/rays/venv/bin/python /opt/rays/update_cache.py'
   ```
8. 更新 GitHub 仓库 Secrets 里的 `SERVER_IP`。
