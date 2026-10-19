// 极简只读视图：调用 /api/* 拉数据展示，不做重型编辑。

async function fetchJson(url, opts = {}) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  return r.json();
}

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  Object.assign(e, props);
  for (const c of children) {
    if (typeof c === "string") e.appendChild(document.createTextNode(c));
    else if (c) e.appendChild(c);
  }
  return e;
}

function setView(tab) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.getElementById(`view-${tab}`).classList.add("active");
  if (tab === "today") loadToday();
  if (tab === "week") loadWeek();
  if (tab === "courses") loadCourses();
  if (tab === "homework") loadHomework();
  if (tab === "affairs") loadAffairs();
  if (tab === "progress") loadProgress();
  if (tab === "outline") loadOutline();
  if (tab === "files") loadFiles();
  if (tab === "schedule") loadSchedule();
}

async function loadHealth() {
  try {
    const h = await fetchJson("/api/health");
    document.getElementById("vec-flag").textContent = h.vector_enabled ? "已启用" : "未启用";
  } catch {
    document.getElementById("vec-flag").textContent = "离线";
  }
}

async function loadToday() {
  const root = document.getElementById("today-list");
  root.innerHTML = "";
  try {
    const list = await fetchJson("/api/courses/today");
    if (!list.length) {
      root.appendChild(el("div", { className: "empty" }, "今天没有课"));
      return;
    }
    for (const c of list) {
      root.appendChild(
        el("div", { className: "card" },
          el("div", { className: "title" }, c.name,
            c.class_group ? el("span", { className: "badge" }, c.class_group) : null,
          ),
          el("div", { className: "meta" },
            `${c.start_time ?? ""}${c.end_time ? " - " + c.end_time : ""}`,
            c.location ? " · " + c.location : "",
            c.week_no ? " · 第 " + c.week_no + " 周" : "",
          ),
        )
      );
    }
  } catch (e) {
    root.appendChild(el("div", { className: "empty" }, "加载失败：" + e.message));
  }
}

async function loadWeek() {
  const root = document.getElementById("week-list");
  root.innerHTML = "";
  try {
    const list = await fetchJson("/api/courses/week");
    if (!list.length) {
      root.appendChild(el("div", { className: "empty" }, "本周暂无课程"));
      return;
    }
    let lastDate = "";
    for (const c of list) {
      if (c.lesson_date !== lastDate) {
        root.appendChild(el("h3", { style: { margin: "12px 0 4px" } }, c.lesson_date));
        lastDate = c.lesson_date;
      }
      root.appendChild(
        el("div", { className: "card" },
          el("div", { className: "title" }, c.name),
          el("div", { className: "meta" },
            `${c.start_time ?? ""}${c.end_time ? "-" + c.end_time : ""}`,
            c.location ? " · " + c.location : "",
            c.week_no ? " · 第 " + c.week_no + " 周" : "",
          ),
        )
      );
    }
  } catch (e) {
    root.appendChild(el("div", { className: "empty" }, "加载失败：" + e.message));
  }
}

async function loadCourses() {
  const root = document.getElementById("courses-list");
  root.innerHTML = "";
  try {
    const list = await fetchJson("/api/courses");
    if (!list.length) {
      root.appendChild(el("div", { className: "empty" }, "暂无课程"));
      return;
    }
    for (const c of list) {
      root.appendChild(
        el("div", { className: "card" },
          el("div", { className: "title" }, c.name,
            c.class_group ? el("span", { className: "badge" }, c.class_group) : null,
          ),
          el("div", { className: "meta" },
            c.weekday !== null && c.weekday !== undefined ? "周" + (c.weekday + 1) : "",
            c.start_time ? " · " + c.start_time : "",
            c.location ? " · " + c.location : "",
            c.semester_start && c.semester_end ? " · 学期 " + c.semester_start + " ~ " + c.semester_end : "",
          ),
          c.notes ? el("div", { className: "desc" }, c.notes) : null,
        )
      );
    }
  } catch (e) {
    root.appendChild(el("div", { className: "empty" }, "加载失败：" + e.message));
  }
}

async function loadHomework() {
  const root = document.getElementById("homework-list");
  root.innerHTML = "";
  const onlyPending = document.getElementById("hw-only-pending").checked;
  const url = "/api/homework" + (onlyPending ? "?only_pending=true" : "");
  try {
    const list = await fetchJson(url);
    if (!list.length) {
      root.appendChild(el("div", { className: "empty" }, "无作业"));
      return;
    }
    const today = new Date().toISOString().slice(0, 10);
    for (const h of list) {
      const overdue = h.due_at && h.due_at < today && h.status === "pending";
      root.appendChild(
        el("div", { className: "card" },
          el("div", { className: "title" }, h.title,
            h.status === "submitted" ? el("span", { className: "badge ok" }, "已提交") : null,
            overdue ? el("span", { className: "badge warn" }, "已过期") : null,
          ),
          el("div", { className: "meta" },
            "布置 " + h.published_at,
            h.due_at ? " · 截止 " + h.due_at : "",
            h.class_group ? " · " + h.class_group : "",
          ),
          h.description ? el("div", { className: "desc" }, h.description) : null,
          h.link ? el("div", { className: "meta" }, "🔗 ", el("a", { href: h.link, target: "_blank" }, h.link)) : null,
        )
      );
    }
  } catch (e) {
    root.appendChild(el("div", { className: "empty" }, "加载失败：" + e.message));
  }
}

async function loadAffairs() {
  const root = document.getElementById("affairs-list");
  root.innerHTML = "";
  try {
    const list = await fetchJson("/api/affairs");
    if (!list.length) {
      root.appendChild(el("div", { className: "empty" }, "无事务"));
      return;
    }
    for (const a of list) {
      root.appendChild(
        el("div", { className: "card" },
          el("div", { className: "title" }, a.title,
            a.kind && a.kind !== "other" ? el("span", { className: "badge" }, a.kind) : null,
          ),
          el("div", { className: "meta" },
            a.due_at ? "截止 " + a.due_at : "无截止",
            a.remind_before_minutes ? " · 提前 " + a.remind_before_minutes + " 分钟提醒" : "",
          ),
          a.description ? el("div", { className: "desc" }, a.description) : null,
        )
      );
    }
  } catch (e) {
    root.appendChild(el("div", { className: "empty" }, "加载失败：" + e.message));
  }
}

async function loadProgress() {
  const root = document.getElementById("progress-list");
  const sel = document.getElementById("progress-course-filter");
  root.innerHTML = "";

  if (sel.options.length <= 1) {
    try {
      const list = await fetchJson("/api/courses");
      for (const c of list) {
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = c.name;
        sel.appendChild(opt);
      }
    } catch {}
  }
  const cid = sel.value;
  const url = "/api/progress" + (cid ? "?course_id=" + cid : "");
  try {
    const list = await fetchJson(url);
    if (!list.length) {
      root.appendChild(el("div", { className: "empty" }, "无进度记录"));
      return;
    }
    for (const p of list) {
      root.appendChild(
        el("div", { className: "card" },
          el("div", { className: "title" }, p.topic,
            p.completed ? el("span", { className: "badge ok" }, "已完成") : el("span", { className: "badge warn" }, "未完成"),
          ),
          el("div", { className: "meta" }, "上课日 " + p.lesson_date),
          p.outline ? el("div", { className: "desc" }, "大纲：" + p.outline) : null,
          p.notes ? el("div", { className: "desc" }, p.notes) : null,
        )
      );
    }
  } catch (e) {
    root.appendChild(el("div", { className: "empty" }, "加载失败：" + e.message));
  }
}

document.querySelectorAll("nav button").forEach((b) => {
  b.addEventListener("click", () => setView(b.dataset.tab));
});
document.getElementById("hw-only-pending").addEventListener("change", loadHomework);
document.getElementById("progress-course-filter").addEventListener("change", loadProgress);

// ============== 大纲 Tab ==============

async function _populateCourseFilter(selId) {
  const sel = document.getElementById(selId);
  if (!sel || sel.options.length > 1) return;
  try {
    const list = await fetchJson("/api/courses");
    for (const c of list) {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.name + (c.class_group ? ` (${c.class_group})` : "");
      sel.appendChild(opt);
    }
  } catch (e) {
    console.error(e);
  }
}

async function loadOutline() {
  await _populateCourseFilter("outline-course-filter");
  const sel = document.getElementById("outline-course-filter");
  const cid = sel.value;
  const dateInput = document.getElementById("outline-target-date");
  if (!dateInput.value) dateInput.value = new Date().toISOString().slice(0, 10);

  document.getElementById("outline-status").textContent = "";
  document.getElementById("outline-next-topic").innerHTML = "";
  document.getElementById("outline-text").innerHTML = "";
  document.getElementById("outline-cache").innerHTML = "";

  if (!cid) {
    document.getElementById("outline-status").textContent = "请选择课程";
    return;
  }
  try {
    const o = await fetchJson(`/api/courses/${cid}/outline`);
    const status = document.getElementById("outline-status");
    status.classList.remove("warn", "ok");
    let statusMsg = "";
    if (!o.has_file) statusMsg += "📄 大纲文件不存在 · ";
    else statusMsg += `📄 文件 ${o.file_size}B · `;
    if (!o.cache_present) statusMsg += "❓ 尚未解析";
    else if (o.stale) {
      statusMsg += "⚠️ cache 已过期（文件 mtime 变化）";
      status.classList.add("warn");
    } else {
      statusMsg += "✓ cache 同步";
      status.classList.add("ok");
    }
    status.textContent = statusMsg;

    document.getElementById("outline-text").innerHTML =
      o.text
        ? `<pre class="outline-text">${escapeHtml(o.text)}</pre>`
        : `<div class="empty">无文本</div>`;
    document.getElementById("outline-cache").innerHTML =
      o.cached
        ? `<pre class="outline-cache">${escapeHtml(JSON.stringify(o.cached, null, 2))}</pre>`
        : `<div class="empty">尚未缓存（调 course.refresh_outline 回填）</div>`;

    await computeNextTopic();
  } catch (e) {
    document.getElementById("outline-status").textContent = "加载失败：" + e.message;
  }
}

async function computeNextTopic() {
  const cid = document.getElementById("outline-course-filter").value;
  const td = document.getElementById("outline-target-date").value;
  const box = document.getElementById("outline-next-topic");
  if (!cid) { box.innerHTML = ""; return; }
  try {
    const url = `/api/courses/${cid}/next-topic` + (td ? `?target_date=${td}` : "");
    const r = await fetchJson(url);
    box.innerHTML = `
      <div class="nt-row"><span class="nt-label">目标日期</span><span class="nt-value">${r.target_date}${r.lesson_date ? " （当周有课：" + r.lesson_date + "）" : " （本节无课程）"}</span></div>
      <div class="nt-row"><span class="nt-label">大纲周次</span><span class="nt-value">第 ${r.target_week ?? "?"} 周</span></div>
      <div class="nt-row"><span class="nt-label">已讲到</span><span class="nt-value">第 ${r.current_week ?? "未开讲"} 周${r.recent_lesson_date ? "（"+r.recent_lesson_date+"）" : ""}</span></div>
      <div class="nt-row"><span class="nt-label">最近主题</span><span class="nt-value">${r.recent_topic ?? "—"}</span></div>
      <div class="nt-row"><span class="nt-label">大纲主题</span><span class="nt-value">${escapeHtml(r.outline_target ?? "（大纲无对应周次）")}</span></div>
      <div class="nt-row"><span class="nt-label">差距（周）</span><span class="nt-value">${r.lag_weeks}</span></div>
      <div class="nt-hint ${r.lag_weeks > 0 ? "warn" : ""}">${escapeHtml(r.hint)}</div>
    `;
  } catch (e) {
    box.innerHTML = `<div class="empty">算失败：${e.message}</div>`;
  }
}

document.getElementById("outline-course-filter").addEventListener("change", loadOutline);
document.getElementById("outline-recompute").addEventListener("click", computeNextTopic);
document.getElementById("outline-target-date").addEventListener("change", computeNextTopic);

// ============== 文件 Tab ==============

async function loadFiles() {
  await _populateCourseFilter("files-course-filter");
  const cid = document.getElementById("files-course-filter").value;
  const role = document.getElementById("files-role-filter").value;
  const root = document.getElementById("files-list");
  root.innerHTML = "";
  if (!cid) {
    root.appendChild(el("div", { className: "empty" }, "请选择课程"));
    return;
  }
  try {
    // 先扫一遍（保证最新）
    await fetchJson("/api/fs/scan-course", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ course_id: cid }) }).catch(() => {});
    const url = `/api/courses/${cid}/files` + (role ? `?role=${role}` : "");
    const list = await fetchJson(url);
    if (!list.length) {
      root.appendChild(el("div", { className: "empty" }, "此课程暂无文件（教师将文件放入绑定的文件夹后，点"重新扫描"）"));
      return;
    }
    // 按 role 分组
    const byRole = {};
    for (const f of list) {
      const r = f.role || "(无角色)";
      if (!byRole[r]) byRole[r] = [];
      byRole[r].push(f);
    }
    const tree = el("div", { className: "files-tree" });
    for (const r of Object.keys(byRole).sort()) {
      tree.appendChild(el("div", { className: "role-header" }, r));
      for (const f of byRole[r]) {
        tree.appendChild(el("div", { className: "file" },
          el("span", { className: "name" }, f.filename),
          el("span", { className: "meta" }, `${f.size}B · sha ${(f.sha256 || "").slice(0, 10)}…`),
        ));
      }
    }
    root.appendChild(tree);
  } catch (e) {
    root.appendChild(el("div", { className: "empty" }, "加载失败：" + e.message));
  }
}

document.getElementById("files-course-filter").addEventListener("change", loadFiles);
document.getElementById("files-role-filter").addEventListener("change", loadFiles);
document.getElementById("files-refresh").addEventListener("click", async () => {
  const cid = document.getElementById("files-course-filter").value;
  if (!cid) return;
  try {
    await fetchJson("/api/fs/scan-course", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ course_id: cid }) });
    loadFiles();
  } catch (e) {
    alert("扫描失败：" + e.message);
  }
});

// ============== 时间表 Tab ==============

async function loadSchedule() {
  const root = document.getElementById("schedule-grid");
  root.innerHTML = "";
  try {
    const sch = await fetchJson("/api/school/schedule");
    root.appendChild(el("div", { className: "head" }, ""));
    root.appendChild(el("div", { className: "head" }, "早晨"));
    root.appendChild(el("div", { className: "head" }, "下午"));
    root.appendChild(el("div", { className: "head" }, "晚上"));
    const buckets = ["morning", "afternoon", "evening"];
    // 按 max(morning/afternoon/evening length) 决定行数
    const maxLen = Math.max(...buckets.map((b) => (sch[b] || []).length));
    for (let i = 0; i < maxLen; i++) {
      root.appendChild(el("div", { className: "row-label" }, `第 ${i + 1} 节`));
      for (const b of buckets) {
        const items = sch[b] || [];
        const it = items[i];
        if (it) {
          root.appendChild(el("div", { className: "slot" },
            el("span", { className: "no" }, "第 " + it.no + " 节"),
            el("span", { className: "time" }, it.start + " ~ " + it.end),
          ));
        } else {
          root.appendChild(el("div", { className: "slot no" }, "—"));
        }
      }
    }
  } catch (e) {
    root.appendChild(el("div", { className: "empty" }, "加载失败：" + e.message));
  }
}

// ============== 工具 ==============

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

loadHealth();
loadToday();
