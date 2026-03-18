# VPS Deployment Guide

Step-by-step instructions for running the Polymarket copy trading bot on a VPS with auto-restart on crash or reboot.

---

## 1. Choose a VPS

**Recommended: Hetzner CX22 (~€4/month)**
- 2 vCPU, 4 GB RAM, 40 GB SSD
- Choose **US-East (Ashburn)** datacenter for the lowest latency to Polymarket's infrastructure
- Sign up at https://www.hetzner.com/cloud

Other options: DigitalOcean Droplet (Basic, $6/mo), Vultr High Frequency ($6/mo).

---

## 2. SSH setup and create a non-root user

After provisioning, SSH in as root:

```bash
ssh root@YOUR_SERVER_IP
```

Create a dedicated non-root user:

```bash
adduser botuser
usermod -aG sudo botuser
```

Copy your SSH key to the new user:

```bash
# On your local machine
ssh-copy-id botuser@YOUR_SERVER_IP
```

Log in as `botuser` for all remaining steps:

```bash
ssh botuser@YOUR_SERVER_IP
```

---

## 3. Install Python 3, pip, and venv

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git
python3 --version   # should be 3.10+
```

---

## 4. Upload files via scp

From your local machine, upload the project:

```bash
scp -r ./polymarket_copybot botuser@YOUR_SERVER_IP:/home/botuser/
```

Or clone from your repo if you pushed it to GitHub:

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git /home/botuser/polymarket_copybot
```

---

## 5. Create and activate a virtualenv

```bash
cd /home/botuser/polymarket_copybot
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 6. Configure .env with secure permissions

```bash
cp .env.example .env
nano .env          # fill in all values
chmod 600 .env     # restrict to owner only
```

Verify permissions:

```bash
ls -la .env
# should show: -rw------- 1 botuser botuser ...
```

---

## 7. Test manually

With the venv active, run the bot once to confirm it starts cleanly:

```bash
source venv/bin/activate
python bot.py
```

Watch the logs for `🤖 Bot started`. Press `Ctrl+C` to stop.

---

## 8. Create a systemd service for auto-restart

Create the service file:

```bash
sudo nano /etc/systemd/system/polymarket-copybot.service
```

Paste the following:

```ini
[Unit]
Description=Polymarket CopyBot
After=network.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/polymarket_copybot
ExecStart=/home/botuser/polymarket_copybot/venv/bin/python bot.py
Restart=always
RestartSec=10
EnvironmentFile=/home/botuser/polymarket_copybot/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

## 9. Start, stop, and monitor

Reload systemd and enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-copybot
sudo systemctl start polymarket-copybot
```

Check status:

```bash
sudo systemctl status polymarket-copybot
```

View live logs:

```bash
sudo journalctl -u polymarket-copybot -f
```

View recent logs (last 100 lines):

```bash
sudo journalctl -u polymarket-copybot -n 100 --no-pager
```

Stop the bot:

```bash
sudo systemctl stop polymarket-copybot
```

Restart after config changes:

```bash
sudo systemctl restart polymarket-copybot
```

---

## 10. Security checklist

- [ ] **Firewall enabled** — allow only SSH (22), deny everything else inbound
  ```bash
  sudo ufw allow OpenSSH
  sudo ufw enable
  sudo ufw status
  ```
- [ ] **No root SSH** — disable root login in `/etc/ssh/sshd_config`:
  ```
  PermitRootLogin no
  ```
  Then restart SSH: `sudo systemctl restart sshd`
- [ ] **Dedicated wallet only** — never use your main wallet; fund the bot wallet with only what you can afford to lose
- [ ] **.env permissions** — confirm `chmod 600 .env`
- [ ] **Keep system updated** — run `sudo apt upgrade -y` regularly
- [ ] **Monitor spend** — set up Polymarket balance alerts or check periodically; the bot can only spend what's in the wallet
- [ ] **Paper-trade first** — run with `MAX_POSITION_USDC=0` or a separate testnet wallet before using real funds

---

## Quick reference

| Action | Command |
|---|---|
| Start bot | `sudo systemctl start polymarket-copybot` |
| Stop bot | `sudo systemctl stop polymarket-copybot` |
| Restart bot | `sudo systemctl restart polymarket-copybot` |
| View live logs | `sudo journalctl -u polymarket-copybot -f` |
| Check status | `sudo systemctl status polymarket-copybot` |
| Enable auto-start | `sudo systemctl enable polymarket-copybot` |
