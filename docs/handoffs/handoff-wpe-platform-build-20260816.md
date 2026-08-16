# Handoff: Build WPE WebKit 2.52+ (WPEPlatform headless) artifact

日期：2026-08-16
來源：WPE 最低 RAM 量測任務（天風指定）

## 任務

用 GitHub Actions（或其他 8GB+ RAM 機器）build **WPE WebKit main（2.52+）**，啟用 **WPEPlatform headless**（`ENABLE_WPE_PLATFORM=ON`），打包成 artifact 傳回 hermes VM，讓她在本地跑「真·最瘦架構」的 PSS 量測。

背景：apt 只有 WPE 2.38（bookworm）/ 2.40（sid），WPEPlatform 是 2.52 才有的新架構，沒有任何 distro 打包。hermes 是 e2-micro（969MB RAM），WebKit link 需要 4-6GB RAM，本地編不動。

## Build 參數（source 驗證過：WebKit/WebKit main `Source/cmake/OptionsWPE.cmake`）

- Source：`https://github.com/WebKit/WebKit`（main branch；注意：**不是** WebPlatformForEmbedded/WPEWebKit downstream，那個 tags 只到 2.42.4、沒有 wpe-2.52 分支）
- CMake 參數：
  - `-DPORT=WPE`
  - `-DENABLE_WPE_PLATFORM=ON`（預設跟著 ENABLE_DEVELOPER_MODE，必須顯式開）
  - `-DENABLE_WPE_1_1_API=OFF`（main 已預設 OFF；**與 ENABLE_WPE_PLATFORM 互斥**，確保維持 OFF）
  - `-DENABLE_WPE_PLATFORM_HEADLESS=ON`（預設 ON，確認即可）
  - `-DCMAKE_BUILD_TYPE=RelWithDebInfo`
  - `-GNinja`
- 官方 dev-build 流程（README）：`Tools/wpe/install-dependencies` → `Tools/Scripts/update-webkitwpe-libs` → `Tools/Scripts/build-webkit --wpe --release`；production build 用 `cmake -DPORT=WPE ... && ninja && ninja install`

## 建議：GitHub Actions workflow（container: debian:bookworm，glibc 相容）

```yaml
name: build-wpe-platform
on: workflow_dispatch
jobs:
  build:
    runs-on: ubuntu-latest
    container: debian:bookworm
    steps:
      - uses: actions/checkout@v4
        with:
          repository: WebKit/WebKit
          fetch-depth: 1
      - name: Install deps
        run: |
          apt-get update && apt-get install -y cmake ninja-build python3 ruby \
            libglib2.0-dev libsoup-3.0-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
            libdrm-dev libgbm-dev libegl1-mesa-dev libepoxy-dev libtasn1-6-dev \
            libwebp-dev libxml2-dev libxslt1-dev libopenjp2-7-dev libjpeg62-turbo-dev \
            libpng-dev libsqlite3-dev libwpe-1.0-dev libharfbuzz-dev libicu-dev \
            libhyphen-dev liblcms2-dev libmanette-0.2-dev libsecret-1-dev libnotify-dev \
            gperf bison flex gettext libffi-dev
      - name: Configure
        run: |
          cmake -DPORT=WPE -DENABLE_WPE_PLATFORM=ON -DENABLE_WPE_1_1_API=OFF \
                -DENABLE_WPE_PLATFORM_HEADLESS=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo \
                -GNinja -B BuildWPE
      - name: Build
        run: ninja -C BuildWPE
      - name: Package
        run: |
          mkdir -p /tmp/wpe-out && cd BuildWPE && ninja install DESTDIR=/tmp/wpe-out
          tar czf /tmp/wpe-webkit-2.52-platform.tar.gz -C /tmp/wpe-out .
      - uses: actions/upload-artifact@v4
        with:
          name: wpe-webkit-platform
          path: /tmp/wpe-webkit-2.52-platform.tar.gz
```

⚠️ 依賴清單是「起點」，WebKit build 常缺套件——接手 agent 用 `Tools/wpe/install-dependencies`（apt 包裝腳本）補齊比手動列更可靠。debian:bookworm 的 GCC 12 應可編 main；若 compiler 太舊，改用 `container: debian:trixie`（GCC 13/14）但 glibc 2.38+ 需 hermes 端 Debian 13+ 才能跑——**優先保持 bookworm 容器**以確保 artifact 能在 hermes (Debian 12, glibc 2.36) 上跑。

## 交付

- GitHub Actions artifact（`wpe-webkit-platform`）→ hermes 下載：
  ```bash
  cd /tmp/wpe-bench && gh run download <run-id> --repo Skywind5487/hermes-agent
  tar xzf wpe-webkit-2.52-platform.tar.gz -C /tmp/wpe-root
  ```
- 或 gh release / scp 傳檔（接手 agent 自選）

## 驗證（接收端要做）

1. 確認 artifact 可執行：`/tmp/wpe-root/usr/bin/MiniBrowser --version`（WPE build 有 MiniBrowser）
2. 或寫 tiny WPEPlatform headless launcher（`wpe_display_headless_new()`）——官方 2.52 的 WPEPlatform 範例
3. 跑 PSS 量測（沿用現有腳本 `/tmp/wpe-bench/pss_measure.py`，改 launcher path）：idle / loaded / peak × launcher / WebProcess / NetworkProcess，全部用 smaps_rollup Pss
4. 對照基準（現有 2.38 + Cog 已測）：idle 138MB / loaded 208MB / peak 212MB（sum PSS）
5. **判定標準：peak PSS 若落在 160-190MB 區間 → <200MB 門檻達標，WPE 候選復活**

## 環境資訊

- 接收端：hermes VM（Debian 12 bookworm，glibc 2.36，e2-micro 969MB RAM）
- 量測工具：`/tmp/wpe-bench/pss_measure.py`（現成）、`/tmp/wpe-bench/bench.html`（benchmark 頁）
- PSS 讀法：`/proc/<pid>/smaps_rollup` 的 `Pss:` 欄位（已驗證可用）

## 邊界

- ❌ 不要用 WebPlatformForEmbedded/WPEWebKit downstream（無 2.52）
- ❌ 不要試圖在 e2-micro 上編譯（OOM）
- ✅ 只有 build + 傳 artifact + 跑 PSS 量測

## Suggested skills

- `workthrough-report`（接手 agent 完成後產報告）
- `searxng-search`（若需要搜尋 build 錯誤解法，走 SearXNG 方法論）
