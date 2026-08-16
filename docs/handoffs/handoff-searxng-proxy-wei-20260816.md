# Handoff: SearXNG 查詢流量改走 wei 家用 IP（方案 A：修 wei SSH → ssh -D tunnel）

日期：2026-08-16
來源：Discord 討論（WPE 測試 session 內插話）

## 任務

讓 SearXNG 的查詢流量改從 wei（天風家用 Windows）的家用 IP 出站，**只限搜尋流量**，不要整台 VM 設 tailscale exit node（會拖累 gateway 等其他服務）。

## 現況（2026-08-16 實測 Fact）

- **microsocks** 在 hermes 上跑：`/usr/bin/microsocks -i 0.0.0.0 -p 1080`（systemd service `microsocks.service`，root，8/15 啟動），聽 `0.0.0.0:1080`。
- **出站 IP 實測**：hermes 直連出站 = `34.168.202.65`（GCP）；經 `curl --socks5 127.0.0.1:1080` 出站 = 同樣 `34.168.202.65`。**架構斷在「microsocks → wei」這一段**，SearXNG 查詢實際從 GCP IP 出去。
- **hermes tailscale**：`ExitNodeID` / `ExitNodeIP` 都是空（沒設 exit node）。wei 在線（`100.99.4.99`，offers exit node），`tailscale ping wei` 130ms 通。
- **wei SSH 不通**：`100.99.4.99:22` 全部 timeout。試過 `id_windows` / `id_vm2` / `id_ed25519` 三把 key × `skywind` / `Wei` / `skywind5487` 三個 user，全部 `Connection timed out`。原因未知（sshd 沒起？防火牆擋？）。
- **SearXNG 主機 SSH 也不通**：free-web-service（35.212.135.197 / tailscale 100.102.204.108）public IP timeout、tailscale SSH 卡網頁認證；vm2（35.212.140.47）timeout。所以 SearXNG 容器內 `settings.yml` 的 outgoing proxy 目前設定**無法確認**。
- **skill 記錄**：searxng-search skill 的 proxy-setup.md 記載目標架構 = `SearXNG → SOCKS5 → hermes(100.90.232.17:1080) → tailscale exit node → wei(220.134.216.2) → internet`，但 skill 主文 7/12 實測標註「尚未走 Tailscale exit node」。skill 的解法（hermes 整台走 exit node）**已被天風否決**。

## 解法（天風已確認：方案 A）

1. **修 wei SSH**：檢查 Windows OpenSSH server（sshd）是否啟動、Windows 防火牆是否擋 tailscale 網段進來的 22 port。參考 skill `windows-ssh`（administrators_authorized_keys 陷阱、key rotation 存 vaultwarden）。
2. **hermes 開 ssh -D tunnel**：`ssh -D 1080 -N wei`（SOCKS5 dynamic forwarding，出站 = wei 家用 IP），取代或並存 microsocks。只影響走 1080 的流量，其他流量不動。
3. **確認 SearXNG settings.yml**：`outgoing.proxies.all://` 指向 `socks5://100.90.232.17:1080`（hermes tailscale IP）——需要能進 free-web-service 才能確認/改。
4. **驗證**：`docker exec searxng python3 -c "import urllib.request; print(urllib.request.urlopen('http://ifconfig.me').read())"` 應顯示 wei 家用 IP（220.134.216.x 區段）。

## 環境資訊（安全可寫）

- hermes tailscale IP：`100.90.232.17`（microsocks 所在地）
- wei tailscale IP：`100.99.4.99`（目標出口，家用 IP 220.134.216.2）
- free-web-service tailscale IP：`100.102.204.108`（SearXNG Docker 所在地）
- SSH key：`~/.ssh/id_windows`（wei）、`~/.ssh/id_vm2`（free-web-service/vm2）
- 注意：所有密碼 / API key / vaultwarden session 不可寫入此文件

## 邊界

- ❌ 不要設 hermes 整台 tailscale exit node（天風明確否決）
- ❌ 不要動 gateway / cloudflared / 其他服務流量
- ✅ 只需要搜尋查詢流量繞 wei 出站

## Suggested skills

- `windows-ssh`（wei OpenSSH 設定、administrators_authorized_keys、key rotation）
- `searxng-search`（SearXNG API、proxy 設定、references/proxy-setup.md）
- `vaultwarden-cli`（wei SSH key 若需 rotation）
- `wsl-ssh`（若 wei-wsl 100.79.130.25 比 Windows 本體好接）
