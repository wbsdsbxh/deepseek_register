# deepseek_register

DeepSeek 账号注册控制台，纯 Web 界面，纯 HTTP 发包，不启动浏览器。

原仓库（XxxXTeam/deepseek_register）依赖 Playwright + tempmail-sdk，邮箱渠道全部失效、脚本跑不起来。这个版本整体重写：

- 去掉 Playwright / tempmail-sdk / rich / dotenv
- 去掉所有 TUI，改成 Flask 单页控制台
- 邮箱改用固定域名 `wbsdsb.lol` 随机前缀
- 验证码改成人工从邮箱读取后填入
- hCaptcha 走 yescaptcha 打码
- PoW 走 wasm 求解（`AmanCode22/deepseek_pow_solver`）

## 一眼看懂流程

```
GET  /sign_up                          → 拿 ds_session_id cookie、region
POST /api/v0/users/create_guest_challenge
                                       → 返回 DeepSeekHashV1 挑战
   本地 WASM 求 nonce（0.0x 秒）
POST /api/v0/users/create_email_verification_code
   Header: X-DS-Guest-PoW-Response      → biz_code=0 表示验证码已发出
   Body:   email / scenario="register" / hcaptcha_token / device_id / locale
                                       ↓
   你从邮箱复制 6 位码，贴进网页
                                       ↓
POST /api/v0/users/register
   Body: {locale, region, payload:{email, email_verification_code, password}, device_id, os:"web"}
                                       → biz_code=0 注册成功
```

## 安装

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

`wasmtime` 用于跑 PoW 的 wasm，Python 3.10+ 都有轮子，不用编译。

## 启动

```bash
./venv/bin/python main.py
```

打开 `http://127.0.0.1:8080/`。

换端口：

```bash
PORT=9000 ./venv/bin/python main.py
```

## 界面里都有什么

### 一键控制

右上角绿点亮起就是运行中。

- **注册数量**：填 `0` 一直跑，填 `5` 就跑 5 条
- **间隔（秒）**：两条之间等多久
- **开启 / 停止**：停止是软停，当前这条收尾后退出，卡在 hCaptcha 解析阶段也能立刻中断

### 手动注册单个账号

填你想要的邮箱和密码，点「发送验证码」。这个邮箱必须是 `@wbsdsb.lol` 下真实存在的收件地址，或者任何你能收到信的邮箱。

### 等待填入验证码

每条跑到「验证码已发出」就在这里挂起一条，显示邮箱、密码、已等待秒数。

1. 去邮箱取 6 位码
2. 贴进输入框
3. 点「提交」或按 Enter

码填错了会提示，条目保留，可以重填。「重发」会重新打一次 hCaptcha 再发一封。

### 账号列表

每页 8 条，支持按邮箱/状态搜索。

- **验登**：拿这个账号密码走一次 `/users/login`，确认能登进去
- **删除**：删单条
- **导出 TXT**：`email:password` 行格式，只导成功的
- **导出 CSV**：全字段
- **清空**：删掉全部记录

状态三种：`成功` / `失败` / `待填码`。

### 运行日志

滚动输出每一步，包括 hCaptcha taskId、PoW 难度和答案、接口 biz_code。

## 文件结构

```
main.py            Flask 路由 + 启动入口
config.py          全部可调参数（环境变量覆盖）
deepseek_api.py    DeepSeek 接口封装 + yescaptcha 封装
pow_solver.py      DeepSeekHashV1 的 wasm 封装
runner.py          后台调度线程、挂起任务管理
store.py           accounts.json 读写、分页、导出
templates/index.html
static/style.css
static/app.js
assets/deepseek_pow_solver.wasm
```

## 配置

全部走环境变量，有默认值：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8080` | 监听端口 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `EMAIL_DOMAIN` | `wbsdsb.lol` | 自动生成邮箱的后缀 |
| `EMAIL_LOCAL_LENGTH` | `10` | 邮箱前缀长度 |
| `YESCAPTCHA_CLIENT_KEY` | 内置 | yescaptcha 客户密钥 |
| `YESCAPTCHA_API` | `https://api.yescaptcha.com` | 打码平台地址 |
| `HCAPTCHA_SITEKEY` | `352e5376-f2cc-43fe-a744-e51640449610` | DeepSeek 的 hCaptcha sitekey |
| `HCAPTCHA_PAGEURL` | `https://chat.deepseek.com/sign_up` | 打码时上报的页面 |
| `MAX_RETRIES` | `3` | 保留项 |
| `RETRY_DELAY` | `3` | 重试间隔 |
| `USER_AGENT` | Chrome 126 | 请求头 UA，会被 yescaptcha 返回的 UA 覆盖 |

替换打码密钥：

```bash
YESCAPTCHA_CLIENT_KEY=你的key ./venv/bin/python main.py
```

## 接口一览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 控制台页面 |
| GET | `/api/status` | 运行状态、计数、挂起列表 |
| GET | `/api/logs?since=N` | 增量拉日志 |
| GET | `/api/balance` | yescaptcha 余额 |
| POST | `/api/start` | `{count, interval}` |
| POST | `/api/stop` | 停止 |
| POST | `/api/register_one` | `{email, password}` |
| POST | `/api/submit_code` | `{account_id, code}` |
| POST | `/api/resend_code` | `{account_id}` |
| GET | `/api/accounts?page=&page_size=&keyword=` | 分页列表 |
| DELETE | `/api/accounts/<id>` | 删单条 |
| POST | `/api/accounts/clear` | 清空 |
| GET | `/api/export?format=txt\|csv` | 导出 |
| POST | `/api/verify/<id>` | 登录校验 |

## 几个容易踩的坑

**`create_guest_challenge` 返回 `INVALID_TARGET_PATH`**

`target_path` 必须写成 `/api/v0/users/create_email_verification_code`，带 `/api/v0` 前缀，不能只写 `/users/...`。

**发码返回 `HTTP 422 {"loc": "body.scenario"}`**

`scenario` 是必填项，注册场景固定传 `"register"`。

**PoW 求解返回无效**

wasm 的 `difficulty` 参数是**迭代次数上限**，不是难度位数。DeepSeek 给的是 20，20 次以内就能命中；如果换成别的值导致 20 次不够，`pow_solver.py` 会自动放大到 `difficulty * 50` 重试一次。

**提交验证码返回 `biz_code=6 REGISTER_FROM_MAINLAND`**

DeepSeek 对注册环节做了地区判定：中国大陆出口 IP 一律拒绝，跟验证码对不对无关。发码阶段不拦，只有最后 `/users/register` 会拒。

排查方法：发码成功、提交任意码都返回 6，就是出口 IP 问题。挂海外代理（`HTTPS_PROXY` 环境变量或给 `DeepSeekClient(proxy=...)` 传地址）再试。

代码里 6 和 3（邮箱已注册）被当作不可重试错误，直接标 `失败` 并从待填列表移除；其它错误码保留在待填列表可以重填。

**hCaptcha token 过期**

token 一次性、约 120 秒有效。代码里拿到就立刻发请求，中间不插入任何等待。

**服务重启后挂起任务消失**

挂起任务存在内存里（`runner._tasks`），重启进程就没了。`accounts.json` 里的记录还在，状态停在 `待填码`，但那个 session 已经不可用了，只能重新发一次码。

## 依赖说明

`assets/deepseek_pow_solver.wasm` 来自 [AmanCode22/deepseek_pow_solver](https://github.com/AmanCode22/deepseek_pow_solver) v1.0.0 release。算法是 DeepSeekHashV1 的独立重实现，求解 `{salt}_{expire_at}_{nonce}` 的哈希前缀碰撞。
