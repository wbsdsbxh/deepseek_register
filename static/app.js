/* DeepSeek 注册控制台 —— 前端逻辑 */

const $ = (id) => document.getElementById(id);
const state = {
  page: 1,
  pageSize: 8,
  keyword: "",
  logCursor: 0,
  pendingRendered: "",
  running: false,
};

/* ---------------- 通用 ---------------- */
function toast(msg, kind = "") {
  const box = $("toast");
  box.textContent = msg;
  box.className = "toast show " + kind;
  clearTimeout(box._t);
  box._t = setTimeout(() => (box.className = "toast " + kind), 2600);
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

const post = (path, body) =>
  api(path, { method: "POST", body: JSON.stringify(body || {}) });

/* ---------------- 状态轮询 ---------------- */
async function refreshStatus() {
  let data;
  try {
    data = await api("/api/status");
  } catch (e) {
    $("runState").textContent = "连接断开";
    $("led").className = "led";
    return;
  }

  state.running = data.running;
  $("runState").textContent = data.running ? "运行中" : "已停止";
  $("led").className = "led" + (data.running ? " on" : "");
  $("domainBadge").textContent = "@" + data.email_domain;

  $("mSent").textContent = data.counters.sent;
  $("mSuccess").textContent = data.counters.success;
  $("mFailed").textContent = data.counters.failed;
  $("mPending").textContent = data.pending.length;

  $("btnStart").disabled = data.running;
  $("btnStop").disabled = !data.running;

  renderPending(data.pending);
}

function renderPending(items) {
  const sig = JSON.stringify(items.map((i) => [i.account_id, i.waited]));
  if (sig === state.pendingRendered) return;
  state.pendingRendered = sig;

  const box = $("pendingList");
  if (!items.length) {
    box.innerHTML = '<p class="empty">当前没有等待中的任务</p>';
    return;
  }

  // 保留已输入但尚未提交的验证码
  const typed = {};
  box.querySelectorAll("input[data-acc]").forEach((el) => {
    if (el.value) typed[el.dataset.acc] = el.value;
  });

  box.innerHTML = "";
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "pending-item";
    row.innerHTML = `
      <div class="who">
        <b>${escapeHtml(item.email)}</b>
        <i>密码 ${escapeHtml(item.password)} · 已等 ${item.waited}s</i>
      </div>
      <input type="text" data-acc="${item.account_id}" maxlength="8" placeholder="验证码">
      <button class="btn primary mini" data-submit="${item.account_id}">提交</button>
      <button class="btn ghost mini" data-resend="${item.account_id}">重发</button>
    `;
    box.appendChild(row);
    const inp = row.querySelector("input");
    if (typed[item.account_id]) inp.value = typed[item.account_id];
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitCode(item.account_id, inp);
    });
    row.querySelector("[data-submit]").addEventListener("click", () =>
      submitCode(item.account_id, inp)
    );
    row.querySelector("[data-resend]").addEventListener("click", async (e) => {
      e.target.disabled = true;
      try {
        const r = await post("/api/resend_code", { account_id: item.account_id });
        toast(r.ok ? "已重新发送" : r.error, r.ok ? "ok" : "err");
      } catch (err) {
        toast(String(err), "err");
      } finally {
        e.target.disabled = false;
      }
    });
  });
}

async function submitCode(accountId, input) {
  const code = (input.value || "").trim();
  if (!code) {
    toast("先填验证码", "err");
    return;
  }
  input.disabled = true;
  try {
    const r = await post("/api/submit_code", { account_id: accountId, code });
    if (r.ok) {
      toast("注册成功", "ok");
      state.pendingRendered = "";
      await refreshStatus();
      await loadAccounts();
    } else {
      toast(r.error || "失败", "err");
    }
  } catch (e) {
    toast(String(e), "err");
  } finally {
    input.disabled = false;
  }
}

/* ---------------- 账号表 ---------------- */
const STATUS_TEXT = {
  success: "成功",
  failed: "失败",
  pending_code: "待填码",
};

async function loadAccounts() {
  const q = new URLSearchParams({
    page: state.page,
    page_size: state.pageSize,
    keyword: state.keyword,
  });
  let data;
  try {
    data = await api("/api/accounts?" + q.toString());
  } catch (e) {
    return;
  }

  const tbody = $("accountTable").querySelector("tbody");
  tbody.innerHTML = "";

  if (!data.items.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="6" style="text-align:center;color:#8595ad">暂无账号</td>';
    tbody.appendChild(tr);
  }

  data.items.forEach((it) => {
    const tr = document.createElement("tr");
    const status = STATUS_TEXT[it.status] || it.status;
    tr.innerHTML = `
      <td class="mono">${escapeHtml(it.email)}</td>
      <td class="mono">${escapeHtml(it.password)}</td>
      <td><span class="tag ${it.status}">${escapeHtml(status)}</span></td>
      <td>${escapeHtml(it.step || "")}</td>
      <td class="mono">${escapeHtml(it.updated_at || "")}</td>
      <td>
        <button class="btn ghost mini" data-verify="${it.id}">验登</button>
        <button class="btn ghost mini" data-del="${it.id}">删除</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("确定删除这条记录？")) return;
      await api("/api/accounts/" + btn.dataset.del, { method: "DELETE" });
      await loadAccounts();
      await refreshStatus();
    });
  });

  tbody.querySelectorAll("[data-verify]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "验证中";
      try {
        const r = await post("/api/verify/" + btn.dataset.verify);
        toast(r.ok ? "登录校验通过" : "校验失败：" + (r.biz_msg || r.error || ""), r.ok ? "ok" : "err");
      } catch (e) {
        toast(String(e), "err");
      } finally {
        btn.disabled = false;
        btn.textContent = "验登";
      }
    });
  });

  $("pageInfo").textContent = `${data.page} / ${data.pages}`;
  $("totalInfo").textContent = `共 ${data.total} 条`;
  $("prevPage").disabled = data.page <= 1;
  $("nextPage").disabled = data.page >= data.pages;
}

/* ---------------- 日志 ---------------- */
async function loadLogs() {
  let data;
  try {
    data = await api("/api/logs?since=" + state.logCursor);
  } catch (e) {
    return;
  }
  if (!data.items.length) return;
  const box = $("logBox");
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  box.textContent += data.items.join("\n") + "\n";
  state.logCursor = data.total;
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function escapeHtml(str) {
  const s = str === null || str === undefined ? String.fromCharCode() : String(str);
  return s
    .split('&').join('&amp;')
    .split('<').join('&lt;')
    .split('>').join('&gt;')
    .split(String.fromCharCode(34)).join('&quot;')
    .split(String.fromCharCode(39)).join('&#39;');
}

/* ---------------- 余额 ---------------- */
async function loadBalance() {
  try {
    const data = await api("/api/balance");
    $("balanceBadge").textContent = data.ok ? `余额 ${data.balance}` : "余额读取失败";
  } catch (e) {
    $("balanceBadge").textContent = "余额读取失败";
  }
}

/* ---------------- 事件绑定 ---------------- */
$("btnStart").addEventListener("click", async () => {
  const count = parseInt($("count").value, 10) || 0;
  const interval = parseInt($("interval").value, 10) || 5;
  try {
    const r = await post("/api/start", { count, interval });
    toast(r.ok ? "已开启" : r.error, r.ok ? "ok" : "err");
    await refreshStatus();
  } catch (e) {
    toast(String(e), "err");
  }
});

$("btnStop").addEventListener("click", async () => {
  try {
    await post("/api/stop");
    toast("已发出停止指令", "ok");
    setTimeout(refreshStatus, 600);
  } catch (e) {
    toast(String(e), "err");
  }
});

$("btnManual").addEventListener("click", async () => {
  const email = $("mEmail").value.trim();
  const password = $("mPassword").value.trim();
  if (!email || !password) {
    toast("邮箱和密码都要填", "err");
    return;
  }
  const btn = $("btnManual");
  btn.disabled = true;
  btn.textContent = "发送中…";
  try {
    const r = await post("/api/register_one", { email, password });
    toast(r.ok ? "验证码已发出，去邮箱取" : r.error, r.ok ? "ok" : "err");
    if (r.ok) {
      $("mEmail").value = "";
      $("mPassword").value = "";
      state.pendingRendered = "";
    }
    await refreshStatus();
    await loadAccounts();
  } catch (e) {
    toast(String(e), "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "发送验证码";
  }
});

$("prevPage").addEventListener("click", () => {
  if (state.page > 1) {
    state.page--;
    loadAccounts();
  }
});

$("nextPage").addEventListener("click", () => {
  state.page++;
  loadAccounts();
});

let kwTimer = null;
$("keyword").addEventListener("input", (e) => {
  clearTimeout(kwTimer);
  kwTimer = setTimeout(() => {
    state.keyword = e.target.value.trim();
    state.page = 1;
    loadAccounts();
  }, 300);
});

$("btnClear").addEventListener("click", async () => {
  if (!confirm("清空全部账号记录？不可恢复")) return;
  await post("/api/accounts/clear");
  state.page = 1;
  await loadAccounts();
  await refreshStatus();
});

$("btnClearLog").addEventListener("click", () => {
  $("logBox").textContent = "";
});

/* ---------------- 启动 ---------------- */
(async function init() {
  await refreshStatus();
  await loadAccounts();
  await loadBalance();

  setInterval(refreshStatus, 1500);
  setInterval(loadLogs, 1500);
  setInterval(loadAccounts, 6000);
  setInterval(loadBalance, 30000);
})();