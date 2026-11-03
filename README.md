# MyIPTV-dist

MyIPTV Android 直播播放器的 **公开分发仓库** (仅存放构建产物, 不含源码)。

- 源码仓库: <https://github.com/suwei8/MyIPTV>
- 本仓库 Release 中的资源:
  - `MyIPTV-<版本>.apk` —— 安装包
  - `version.json` —— 版本元数据 (`versionCode` / `versionName` / `apk` 下载地址 / 签名类型)

> 所有 Release 均使用**同一把 release keystore** 签名, 因此 App 内可直接**覆盖安装**,
> 无需先在电视上卸载旧版本(即"热更新")。

## 构建

构建由本仓库的 GitHub Actions 完成, 源码从 `suwei8/MyIPTV` 拉取:

- 手动触发: `Actions` → `Build & Release APK` → `Run workflow`, 填入版本号(如 `v1.1.15`)
- 自动触发: 在源码仓库 `MyIPTV` 打 `v*` tag 时, 通过 `repository_dispatch` 通知本仓库构建

> 需要在 `MyIPTV-dist` 仓库配置 secrets: `KEYSTORE_BASE64` / `KEYSTORE_PASSWORD` / `KEY_ALIAS` / `KEY_PASSWORD`,
> 否则退化为 debug 签名(无法覆盖安装)。

## 应用内更新(App 侧)

App 启动时(或「设置 → 检查更新」)请求:

```
GET https://api.github.com/repos/suwei8/MyIPTV-dist/releases/latest
```

读取 `version.json` 与 APK 资源, 若 `versionCode` 大于本机版本则提示下载并调起系统安装器覆盖安装。

## 发版注意

1. 每次发版必须**递增** `app/build.gradle` 中的 `versionCode`;
2. tag 名与 `versionName` 保持一致(如 `v1.1.15` ↔ `versionName "1.1.15"`);
3. 始终使用同一把 release keystore, 切勿更换, 否则老版本无法覆盖安装。
