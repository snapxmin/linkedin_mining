const demoData = {
  summary: {
    sample_size: 1247,
    verified: 1180,
    pending: 52,
    rejected: 15,
  },
  university: [
    { label: "香港科技大学", count: 125 },
    { label: "NUS", count: 98 },
    { label: "NTU", count: 83 },
    { label: "港大", count: 74 },
    { label: "浙江大学", count: 62 },
    { label: "北京大学", count: 55 },
    { label: "复旦大学", count: 51 },
    { label: "清华大学", count: 47 },
    { label: "上海交通大学", count: 43 },
    { label: "CUHK", count: 38 },
  ],
  role: [
    { label: "Software Engineer", count: 286 },
    { label: "Product Manager", count: 142 },
    { label: "Data Scientist", count: 118 },
    { label: "Backend Engineer", count: 97 },
    { label: "Operations Lead", count: 64 },
  ],
  location: [
    { label: "香港", count: 312 },
    { label: "Singapore", count: 245 },
    { label: "上海", count: 178 },
    { label: "北京", count: 156 },
    { label: "深圳", count: 121 },
  ],
};

function renderBars(container, title, items) {
  const card = document.createElement("section");
  card.className = "chart-card";
  card.innerHTML = `<h3>${title}</h3>`;
  const max = items[0]?.count || 1;
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <span>${item.label}</span>
      <strong>${item.count}</strong>
      <div class="bar-track">
        <div class="bar-fill" style="width:${(item.count / max) * 100}%"></div>
      </div>
    `;
    card.appendChild(row);
  });
  container.appendChild(card);
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("sample-size").textContent =
    `样本量：${demoData.summary.sample_size}（已复核 ${demoData.summary.verified}）`;

  const summary = document.getElementById("summary-stats");
  [
    ["总样本", demoData.summary.sample_size],
    ["已复核", demoData.summary.verified],
    ["待复核", demoData.summary.pending],
    ["已拒绝", demoData.summary.rejected],
  ].forEach(([label, value]) => {
    const item = document.createElement("li");
    item.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
    summary.appendChild(item);
  });

  renderBars(
    document.getElementById("university-chart"),
    "Top 10 大学",
    demoData.university,
  );

  const secondary = document.getElementById("secondary-charts");
  renderBars(secondary, "职位分布", demoData.role);
  renderBars(secondary, "地点分布", demoData.location);
});
