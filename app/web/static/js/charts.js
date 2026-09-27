/* Token detail page charts (UI_REDESIGN.md §6.2/6.5/6.7, §8). Progressive enhancement only: every
   chart's data has a real HTML legend or table beside it, so the page stays fully readable if this
   script (or the vendored ECharts build) fails to load. */
(function () {
  "use strict";
  if (typeof echarts === "undefined") return;

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  var KIND_COLOR = {
    bundle: function () { return cssVar("--series-bundle"); },
    smart: function () { return cssVar("--series-smart"); },
    holders: function () { return cssVar("--series-holders"); },
    rest: function () { return cssVar("--series-rest"); },
  };

  function registerTheme() {
    echarts.registerTheme("lp-radar", {
      color: [cssVar("--series-bundle"), cssVar("--series-smart"), cssVar("--series-holders"), cssVar("--series-rest")],
      backgroundColor: "transparent",
      textStyle: { fontFamily: cssVar("--font-sans"), color: cssVar("--text") },
      tooltip: {
        backgroundColor: cssVar("--surface-raised"),
        borderColor: cssVar("--line"),
        textStyle: { color: cssVar("--text") },
      },
    });
  }

  var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var instances = {};
  var observers = {};

  function readData(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  function dispose(containerId) {
    if (instances[containerId]) {
      instances[containerId].dispose();
      delete instances[containerId];
    }
    if (observers[containerId]) {
      observers[containerId].disconnect();
      delete observers[containerId];
    }
  }

  function initChart(containerId, dataId, buildOption, wire) {
    var container = document.getElementById(containerId);
    var data = readData(dataId);
    dispose(containerId);
    if (!container || data === null) return null;

    var chart = echarts.init(container, "lp-radar");
    var option = buildOption(data);
    option.animation = !reducedMotion;
    chart.setOption(option);
    instances[containerId] = chart;

    var observer = new ResizeObserver(function () {
      chart.resize();
    });
    observer.observe(container);
    observers[containerId] = observer;

    if (wire) wire(chart, data);
    return chart;
  }

  // --- 6.2 Supply ring ---

  function buildRing(data) {
    return {
      tooltip: {
        formatter: function (p) {
          var wallets = p.data.wallets;
          var count = wallets === null || wallets === undefined ? "" : ", " + wallets + " wallet" + (wallets === 1 ? "" : "s");
          return p.name + ": " + p.value.toFixed(1) + "%" + count;
        },
      },
      series: [
        {
          type: "pie",
          radius: ["62%", "85%"],
          center: ["50%", "50%"],
          avoidLabelOverlap: false,
          label: { show: false },
          labelLine: { show: false },
          data: data.map(function (s) {
            return { name: s.name, value: s.pct, wallets: s.wallets, itemStyle: { color: (KIND_COLOR[s.kind] || KIND_COLOR.rest)() } };
          }),
        },
      ],
    };
  }

  function wireRing(chart) {
    chart.on("click", function (params) {
      if (params.data.name === "Bundle") {
        document.querySelectorAll(".cluster").forEach(function (details) {
          details.open = true;
        });
        scrollToSection("bundle-section");
      } else if (params.data.name === "Smart money") {
        scrollToSection("smart-money-section");
      }
    });
  }

  function scrollToSection(id) {
    var el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
  }

  // --- 6.5 Score history ---

  var VERDICT_VAR = { GREEN: "--green", WATCH: "--watch", AVOID: "--avoid" };

  function verdictColor(verdict) {
    return cssVar(VERDICT_VAR[verdict] || "--text-muted");
  }

  function buildScoreHistory(data) {
    var points = data.points;
    var bands = [
      { name: "Avoid", from: 0, to: data.watchMin, color: cssVar("--avoid") },
      { name: "Watch", from: data.watchMin, to: data.greenMin, color: cssVar("--watch") },
      { name: "Green", from: data.greenMin, to: 100, color: cssVar("--green") },
    ];
    return {
      grid: { left: 40, right: 16, top: 16, bottom: 28 },
      xAxis: { type: "time", axisLine: { lineStyle: { color: cssVar("--line") } }, axisLabel: { color: cssVar("--text-muted") } },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        axisLine: { show: false },
        splitLine: { lineStyle: { color: cssVar("--line") } },
        axisLabel: { color: cssVar("--text-muted") },
      },
      tooltip: {
        trigger: "axis",
        formatter: function (params) {
          var p = params[0];
          var d = new Date(p.value[0]);
          return d.toLocaleString() + "<br/>Score " + p.value[1].toFixed(0) + " (" + p.data.verdict + ")";
        },
      },
      series: [
        {
          type: "line",
          showSymbol: true,
          symbolSize: 8,
          lineStyle: { color: cssVar("--text-muted"), width: 1.5 },
          markArea: {
            silent: true,
            data: bands.map(function (b) {
              return [
                { yAxis: b.from, itemStyle: { color: b.color, opacity: 0.1 }, label: { show: true, position: "insideRight", color: b.color, formatter: b.name } },
                { yAxis: b.to },
              ];
            }),
          },
          data: points.map(function (p) {
            return { value: [p.t, p.score], verdict: p.verdict, itemStyle: { color: verdictColor(p.verdict) } };
          }),
        },
      ],
    };
  }

  // --- 6.7 Tier donut ---

  function buildTierDonut(data) {
    var palette = [cssVar("--series-smart"), cssVar("--accent"), cssVar("--series-holders"), cssVar("--watch"), cssVar("--series-bundle")];
    return {
      tooltip: { formatter: function (p) { return p.name + ": $" + Math.round(p.value).toLocaleString(); } },
      series: [
        {
          type: "pie",
          radius: ["50%", "75%"],
          label: { show: false },
          data: data.map(function (row, i) {
            return { name: row.tier, value: row.usd, itemStyle: { color: palette[i % palette.length] } };
          }),
        },
      ],
    };
  }

  var activeTier = null;

  function applyTierFilter(tier) {
    activeTier = tier;
    document.querySelectorAll("tr[data-tier]").forEach(function (row) {
      row.hidden = tier !== null && row.getAttribute("data-tier") !== tier;
    });
    var chip = document.getElementById("tier-filter-chip");
    var label = document.getElementById("tier-filter-label");
    if (!chip || !label) return;
    if (tier === null) {
      chip.hidden = true;
    } else {
      label.textContent = "Tier: " + tier;
      chip.hidden = false;
    }
  }

  function toggleTier(tier) {
    applyTierFilter(activeTier === tier ? null : tier);
  }

  function wireTierDonut(chart) {
    chart.on("click", function (params) {
      toggleTier(params.name);
    });
  }

  function wireTierLegend() {
    document.querySelectorAll("[data-tier-legend]").forEach(function (item) {
      item.addEventListener("click", function () {
        toggleTier(item.getAttribute("data-tier-legend"));
      });
    });
    var clear = document.getElementById("tier-filter-clear");
    if (clear) clear.addEventListener("click", function () { applyTierFilter(null); });
  }

  // --- 6.7 Buy/sell ---

  function buildBuySell(data) {
    return {
      grid: { left: 90, right: 16, top: 8, bottom: 24 },
      xAxis: {
        type: "value",
        axisLine: { lineStyle: { color: cssVar("--line") } },
        axisLabel: {
          color: cssVar("--text-muted"),
          formatter: function (v) {
            var a = Math.abs(v);
            return a >= 1000 ? "$" + (a / 1000).toFixed(0) + "K" : "$" + a;
          },
        },
        splitLine: { lineStyle: { color: cssVar("--line") } },
      },
      yAxis: {
        type: "category",
        data: data.map(function (w) { return w.label; }),
        axisLine: { lineStyle: { color: cssVar("--line") } },
        axisLabel: { color: cssVar("--text-muted") },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: function (params) {
          var bought = params.filter(function (p) { return p.seriesName === "Bought"; })[0];
          var sold = params.filter(function (p) { return p.seriesName === "Sold"; })[0];
          return (bought ? bought.name : "") + "<br/>Bought $" + Math.round(bought ? bought.value : 0).toLocaleString() +
            "<br/>Sold $" + Math.round(sold ? Math.abs(sold.value) : 0).toLocaleString();
        },
      },
      series: [
        {
          name: "Bought",
          type: "bar",
          stack: "flow",
          itemStyle: { color: cssVar("--green") },
          data: data.map(function (w) { return w.bought; }),
        },
        {
          name: "Sold",
          type: "bar",
          stack: "flow",
          itemStyle: { color: cssVar("--avoid") },
          data: data.map(function (w) { return -Math.abs(w.sold); }),
        },
      ],
    };
  }

  function wireBuySell(chart, data) {
    chart.on("mouseover", { componentType: "series" }, function (params) {
      highlightRow(data[params.dataIndex] && data[params.dataIndex].address);
    });
    chart.on("mouseout", { componentType: "series" }, function () {
      highlightRow(null);
    });
  }

  function highlightRow(address) {
    document.querySelectorAll("tr[data-wallet]").forEach(function (row) {
      row.classList.toggle("row-highlighted", address !== null && row.getAttribute("data-wallet") === address);
    });
  }

  function initAll() {
    registerTheme();
    initChart("ring-chart", "chart-ring", buildRing, wireRing);
    initChart("score-history-chart", "chart-score-history", buildScoreHistory);
    initChart("tier-donut-chart", "chart-tier-donut", buildTierDonut, wireTierDonut);
    initChart("buysell-chart", "chart-buysell", buildBuySell, wireBuySell);
    wireTierLegend();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
  document.body.addEventListener("htmx:afterSwap", initAll);
})();
