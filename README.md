# 🔧 淘宝自动比价助手 (taobao-picker)

在聊天窗口（或企业微信）发购物清单 → 自动逛淘宝逐店比价 → 返回**券后实付**排序表（含 App 跳转链接）。

## 核心原理

**券后价 = 确认订单页的"实付"金额**，不猜页面标签：
选规格 → 设数量 → 点"立即购买" → 进确认订单页 → 读"合计/立即支付"金额
（淘宝服务端按登录账号算好的最终价，含店铺券/满减等）。

**安全红线（写死在代码里）**：
- **绝不点击支付/提交订单**，只读价格
- 不走购物车（避免污染用户购物车）
- 操作有人性化随机延迟，比价任务串行执行（防并发操作账号）

## 功能

- **清单比价**：输入"品名 + 规格 + 个数"（或"品名 规格，N个/包，M包"），自动：
  搜索 → 匹配规格 SKU → 进结算页读实付 → 按"买齐总需求"折算 → 排序输出
- **数量自动向上凑整**：选 SKU 包装凑够总需求，宁多勿少、绝不少买
- **网页聊天 UI**：手机浏览器打开即聊天，表格填写（品名/规格/个数），结果含 App/网页链接
- **企业微信接入**：自建应用收发消息（回调需企业备案域名，可选）

## 安装

```bash
# Python 3.11+
pip install playwright flask pycryptodome pillow
python -m playwright install chromium   # 或直接用本机 Edge（channel="msedge"）
```

## 配置

复制 `config.example.json` 为 `config.json` 并填写（企业微信接入需要；只用网页 UI 可留空）：

```json
{
  "corpid": "...", "agentid": "...", "secret": "...",
  "token": "...", "encodingaeskey": "...", "touser": "@all", "top": 5
}
```

⚠️ `config.json`、`state/`（淘宝登录 cookie）已在 `.gitignore` 中，**切勿提交到 GitHub**。

## 使用

```bash
# 1. 首次登录（手机淘宝 App 扫码，cookie 永久保存）
python taobao_bot.py session

# 2. 检查登录态
python taobao_bot.py check

# 3. 单商品验证券后价（不提交订单）
python taobao_bot.py verify <nid> "M8*30" --qty 10

# 4. 清单批量比价（JSON 清单）
python taobao_bot.py compare data/list.json --top 5

# 5. 启动网页聊天 UI（手机浏览器访问，需内网穿透如 cpolar）
python wecom_gateway.py
```

清单 JSON 格式：
```json
[
  {"name": "304不锈钢外六角螺栓", "spec": "M8*30", "pack": "100个", "packs": 3},
  {"name": "304不锈钢平垫圈", "spec": "M8", "total": 200}
]
```

## 内网穿透：手机远程访问

网页 UI 默认只监听本机 `127.0.0.1:8899`，想用手机（家里 WiFi 或在外用流量）访问，需要内网穿透。推荐 [cpolar](https://www.cpolar.com)（国内服务，免费版够用）：

1. **安装**：官网下载 Windows 版 zip，解压得到 `cpolar.exe`（单文件，无需安装，放到项目 `tools/` 下即可）
2. **注册**：https://dashboard.cpolar.com/signup 注册账号（免费）
3. **绑定令牌**：登录后到 https://dashboard.cpolar.com/auth 复制 authtoken，执行：
   ```bash
   cpolar authtoken <你的authtoken>
   ```
4. **开隧道**（先启动网关 `python wecom_gateway.py`，再开隧道）：
   ```bash
   cpolar http 8899 -log=stdout
   ```
5. **拿公网地址**：日志出现 `Tunnel established at https://xxxxxx.cpolar.top`，手机浏览器打开该网址即可用（建议"添加到主屏幕"当 App 用）

⚠️ 注意：
- **免费版网址每次重启会变**（`xxxxxx` 随机生成），重启后重新看日志取新网址；需要固定网址可升级 cpolar 付费版
- 微信内置浏览器打开可能交互受限，建议手机自带浏览器 / Chrome
- Windows 一键启动：`启动采购助手.bat`（自动拉起网关 + 隧道两个窗口，隧道窗口里看当天网址）

## 命令

| 命令 | 作用 |
|---|---|
| `session` | 登录并保存 cookie |
| `check` | 检查登录态 |
| `search "关键词"` | 搜索并解析商品（标题/价格/销量/店铺/链接） |
| `detail <nid>` | 打开详情页 dump HTML |
| `verify <nid> [规格] [--qty N]` | 选规格→立即购买→读实付 |
| `compare <list.json> [--top N] [--out 文件]` | 清单批量比价+排序 |

## 目录结构

```
taobao-picker/
├── taobao_bot.py        # 核心比价脚本（搜索/规格匹配/读实付/折算排序）
├── wecom_gateway.py     # 网关：网页聊天 UI + 企业微信回调
├── make_icon.py         # 生成桌面图标
├── config.example.json  # 配置模板（复制为 config.json 填写）
├── assets/              # 图标资源
└── data/                # 运行时数据（已 gitignore）
```

## 免责声明

- 本项目仅用于个人比价参考，**只读、不自动下单**；下单前请到淘宝人工确认
- 自动化操作可能触发平台风控（滑块验证/登录过期），请控制使用频率，风险自担
- 数据仅供个人研究使用，请遵守平台条款与相关法规
