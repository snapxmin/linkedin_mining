function showMessage(element, text, type = "success") {
  element.textContent = text;
  element.className = `message ${type}`;
  element.hidden = false;
}

async function parseJsonResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg || JSON.stringify(item)).join("; ")
      : detail || response.statusText;
    throw new Error(message);
  }
  return payload;
}

function renderCharts(container, distributions) {
  container.innerHTML = "";
  const labels = {
    university: "大学",
    role: "职位",
    location: "地点",
    degree: "学位",
  };
  Object.entries(labels).forEach(([key, title]) => {
    const items = distributions[key] || [];
    const max = items.length ? items[0].count : 1;
    const card = document.createElement("section");
    card.className = "chart-card";
    card.innerHTML = `<h3>${title}</h3>`;
    if (!items.length) {
      card.innerHTML += `<p class="muted">暂无数据</p>`;
    } else {
      items.slice(0, 10).forEach((item) => {
        const row = document.createElement("div");
        row.className = "bar-row";
        row.innerHTML = `
          <span>${item.label}</span>
          <strong>${item.count}</strong>
          <div class="bar-track"><div class="bar-fill" style="width:${(item.count / max) * 100}%"></div></div>
        `;
        card.appendChild(row);
      });
    }
    container.appendChild(card);
  });
}

async function loadAnalytics(projectId, scope) {
  const response = await fetch(`/api/projects/${projectId}/analytics?scope=${scope}`);
  const payload = await parseJsonResponse(response);
  document.getElementById("sample-size").textContent =
    `样本量：${payload.summary.sample_size}`;
  renderCharts(document.getElementById("analytics-charts"), payload.distributions);
  document.getElementById("export-link").href =
    `/api/projects/${projectId}/export.csv?scope=${scope}`;
}

function profileRow(profile) {
  const row = document.createElement("tr");
  row.dataset.profileId = profile.id;
  row.innerHTML = `
    <td><input value="${profile.name || ""}" data-field="name"></td>
    <td><input value="${profile.current_company || ""}" data-field="current_company"></td>
    <td><input value="${profile.university || ""}" data-field="university"></td>
    <td><input value="${profile.degree || ""}" data-field="degree"></td>
    <td><input value="${profile.location || ""}" data-field="location"></td>
    <td><input value="${profile.role || ""}" data-field="role"></td>
    <td><input type="number" min="0" step="0.5" value="${profile.years_experience ?? ""}" data-field="years_experience"></td>
    <td>
      <select data-field="review_status">
        <option value="pending" ${profile.review_status === "pending" ? "selected" : ""}>待复核</option>
        <option value="verified" ${profile.review_status === "verified" ? "selected" : ""}>已复核</option>
        <option value="rejected" ${profile.review_status === "rejected" ? "selected" : ""}>已拒绝</option>
      </select>
    </td>
    <td><button type="button" class="save-profile">保存</button></td>
  `;
  return row;
}

async function loadProfiles(projectId, page, reviewStatus) {
  const params = new URLSearchParams({ page: String(page), page_size: "20" });
  if (reviewStatus) {
    params.set("review_status", reviewStatus);
  }
  const response = await fetch(`/api/projects/${projectId}/profiles?${params}`);
  const payload = await parseJsonResponse(response);
  const tbody = document.querySelector("#profiles-table tbody");
  tbody.innerHTML = "";
  payload.items.forEach((profile) => tbody.appendChild(profileRow(profile)));
  document.getElementById("page-info").textContent =
    `第 ${payload.page} 页 / 共 ${Math.max(1, Math.ceil(payload.total / payload.page_size))} 页`;
  document.getElementById("prev-page").disabled = payload.page <= 1;
  document.getElementById("next-page").disabled =
    payload.page * payload.page_size >= payload.total;
  return payload;
}

document.addEventListener("DOMContentLoaded", () => {
  const createForm = document.getElementById("create-project-form");
  if (createForm) {
    const message = document.getElementById("create-project-message");
    createForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(createForm);
      try {
        const response = await fetch("/api/projects", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: formData.get("name"),
            company: formData.get("company"),
          }),
        });
        const project = await parseJsonResponse(response);
        window.location.href = `/projects/${project.id}`;
      } catch (error) {
        showMessage(message, error.message, "error");
      }
    });
  }

  if (typeof window.PROJECT_ID === "number") {
    let currentPage = 1;
    const projectId = window.PROJECT_ID;
    const searchForm = document.getElementById("search-form");
    const importForm = document.getElementById("import-form");
    const searchMessage = document.getElementById("search-message");
    const importMessage = document.getElementById("import-message");
    const scopeSelect = document.getElementById("analytics-scope");
    const filterSelect = document.getElementById("profile-filter");

    const refresh = async () => {
      await loadAnalytics(projectId, scopeSelect.value);
      await loadProfiles(projectId, currentPage, filterSelect.value);
    };

    searchForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const roles = searchForm.roles.value
        .split("\n")
        .map((role) => role.trim())
        .filter(Boolean);
      try {
        const response = await fetch(`/api/projects/${projectId}/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ roles }),
        });
        const payload = await parseJsonResponse(response);
        showMessage(
          searchMessage,
          `搜索完成：${payload.result_count} 条公开结果已入库。`,
          "success",
        );
        await refresh();
      } catch (error) {
        showMessage(searchMessage, error.message, "error");
      }
    });

    importForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = importForm.file.files[0];
      if (!file) {
        return;
      }
      const body = new FormData();
      body.append("file", file);
      try {
        const response = await fetch(`/api/projects/${projectId}/imports/csv`, {
          method: "POST",
          body,
        });
        const payload = await parseJsonResponse(response);
        showMessage(
          importMessage,
          `导入成功：${payload.imported_count} 条记录。`,
          "success",
        );
        await refresh();
      } catch (error) {
        showMessage(importMessage, error.message, "error");
      }
    });

    scopeSelect.addEventListener("change", () => loadAnalytics(projectId, scopeSelect.value));
    filterSelect.addEventListener("change", async () => {
      currentPage = 1;
      await loadProfiles(projectId, currentPage, filterSelect.value);
    });
    document.getElementById("prev-page").addEventListener("click", async () => {
      currentPage = Math.max(1, currentPage - 1);
      await loadProfiles(projectId, currentPage, filterSelect.value);
    });
    document.getElementById("next-page").addEventListener("click", async () => {
      currentPage += 1;
      await loadProfiles(projectId, currentPage, filterSelect.value);
    });
    document.querySelector("#profiles-table tbody").addEventListener("click", async (event) => {
      if (!event.target.classList.contains("save-profile")) {
        return;
      }
      const row = event.target.closest("tr");
      const payload = {};
      row.querySelectorAll("[data-field]").forEach((field) => {
        const value = field.value.trim();
        if (field.dataset.field === "years_experience") {
          if (value !== "") {
            payload.years_experience = Number(value);
          }
        } else if (value !== "") {
          payload[field.dataset.field] = value;
        } else if (field.dataset.field === "review_status") {
          payload.review_status = field.value;
        }
      });
      try {
        const response = await fetch(`/api/profiles/${row.dataset.profileId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        await parseJsonResponse(response);
        await refresh();
      } catch (error) {
        alert(error.message);
      }
    });

    refresh();
  }
});
