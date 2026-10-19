// 极简只读视图：调用 /api/* 拉数据展示，不做重型编辑。

async function fetchJson(url) {
  const r = await fetch(url);
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

loadHealth();
loadToday();
